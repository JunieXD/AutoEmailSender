from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base,
    EmailDirection,
    EmailLog,
    EmailTask,
    EmailTaskStatus,
    IdentityProfile,
    ImapMailboxHistoricalScanStatus,
    ImapMailboxSyncState,
    ImapProfessorHistoricalScanStatus,
    ImapProfessorSyncState,
    LLMProfile,
    Professor,
)
from app.services import imap_sync_state
from app.services.imap_message_fetcher import ImapFetchedMessage
from app.services.imap_sync_state import clear_identity_sent_folder_discovery_cache
from app.services.imap_sync_state import ensure_professor_scan_states
from app.services.imap_sync_state import ensure_professor_scan_states_if_needed
from app.services.imap_sync_state import ensure_recent_history_professor_scan_states
from app.services.mail_runtime import ImapHistoryHeaderFetchResult, ImapMailboxHistoryHeaderFetchResult
from app.services.task_runtime import _sync_identity_imap_once_unlocked
from app.services.task_runtime import is_imap_incremental_paused
from app.services.task_runtime import log_imap_history_progress
from app.services.task_runtime import mark_imap_throttled
from app.services.task_runtime import poll_imap_history_once
from app.services.task_runtime import poll_for_replies_once
from app.services.task_runtime import repair_identity_replies
from app.services.task_runtime import process_imap_fetched_messages
from app.services.task_runtime import sync_identity_history_once
from app.services.task_runtime import sync_identity_imap_once
from app.services.task_runtime import sync_identity_incremental_once


class ImapSyncRuntimeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self._run_async(self._create_schema())

    def tearDown(self) -> None:
        self._run_async(self.engine.dispose())

    def _run_async(self, awaitable):
        return asyncio.run(awaitable)

    async def _create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    def test_ensure_professor_scan_states_tracks_all_existing_professors_for_inbox_and_sent(self) -> None:
        async def scenario() -> tuple[int, int, list[tuple[str, str, str]]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                active = Professor(name="Active", email="Active@Example.edu")
                untouched = Professor(name="Untouched", email="untouched@example.edu")
                archived = Professor(
                    name="Archived",
                    email="archived@example.edu",
                    archived_at=datetime(2026, 5, 1, tzinfo=UTC),
                )
                no_email = Professor(name="No Email", email=None)
                session.add_all([identity, active, untouched, archived, no_email])
                await session.commit()

            first_created = await ensure_professor_scan_states(
                self.session_factory,
                sent_folder="Sent",
            )
            second_created = await ensure_professor_scan_states(
                self.session_factory,
                sent_folder="Sent",
            )

            async with self.session_factory() as session:
                rows = list(
                    (
                        await session.execute(
                            select(ImapProfessorSyncState).order_by(
                                ImapProfessorSyncState.professor_email,
                                ImapProfessorSyncState.folder_role,
                            ),
                        )
                    ).scalars(),
                )
                return (
                    first_created,
                    second_created,
                    [(row.professor_email, row.folder_role, row.folder) for row in rows],
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                4,
                0,
                [
                    ("active@example.edu", "inbox", "INBOX"),
                    ("active@example.edu", "sent", "Sent"),
                    ("untouched@example.edu", "inbox", "INBOX"),
                    ("untouched@example.edu", "sent", "Sent"),
                ],
            ),
        )

    def test_ensure_professor_scan_states_tracks_email_log_professors(self) -> None:
        async def scenario() -> list[str]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                llm = self._build_llm()
                professor = Professor(name="Logged", email="logged@example.edu")
                session.add_all([identity, llm, professor])
                await session.flush()
                session.add(
                    EmailLog(
                        identity_id=identity.id,
                        llm_profile_id=llm.id,
                        professor_id=professor.id,
                        direction=EmailDirection.SENT.value,
                        subject="Hello",
                        content="Hi",
                    ),
                )
                await session.commit()

            await ensure_professor_scan_states(self.session_factory)

            async with self.session_factory() as session:
                rows = list((await session.execute(select(ImapProfessorSyncState))).scalars())
                return [row.professor_email for row in rows]

        self.assertEqual(self._run_async(scenario()), ["logged@example.edu"])

    def test_ensure_professor_scan_states_skips_incomplete_imap_identities(self) -> None:
        async def scenario() -> int:
            async with self.session_factory() as session:
                identity = self._build_identity()
                identity.imap_port = None
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, professor])
                await session.commit()

            await ensure_professor_scan_states(self.session_factory, sent_folder="Sent")

            async with self.session_factory() as session:
                return len(list((await session.execute(select(ImapProfessorSyncState))).scalars()))

        self.assertEqual(self._run_async(scenario()), 0)

    def test_ensure_professor_scan_states_skips_blank_imap_identities(self) -> None:
        async def scenario() -> int:
            async with self.session_factory() as session:
                identity = self._build_identity()
                identity.imap_host = " "
                identity.imap_username = ""
                identity.imap_password = "  "
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, professor])
                await session.commit()

            await ensure_professor_scan_states(self.session_factory, sent_folder="Sent")

            async with self.session_factory() as session:
                return len(list((await session.execute(select(ImapProfessorSyncState))).scalars()))

        self.assertEqual(self._run_async(scenario()), 0)

    def test_ensure_professor_scan_states_batches_existing_state_lookup(self) -> None:
        async def scenario() -> tuple[int, int, int]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professors = [
                    Professor(name=f"Professor {index}", email=f"prof{index}@example.edu")
                    for index in range(5)
                ]
                session.add(identity)
                session.add_all(professors)
                await session.commit()

            await ensure_professor_scan_states(self.session_factory, sent_folder="Sent")

            select_count = 0

            def count_professor_state_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
                nonlocal select_count
                normalized = " ".join(statement.lower().split())
                if (
                    normalized.startswith("select")
                    and "from imap_professor_sync_states" in normalized
                ):
                    select_count += 1

            event.listen(self.engine.sync_engine, "before_cursor_execute", count_professor_state_selects)
            try:
                second_created = await ensure_professor_scan_states(self.session_factory, sent_folder="Sent")
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", count_professor_state_selects)

            async with self.session_factory() as session:
                total_states = await session.scalar(select(func.count(ImapProfessorSyncState.id)))
            return second_created, total_states, select_count

        self.assertEqual(self._run_async(scenario()), (0, 10, 1))

    def test_ensure_professor_scan_states_chunks_large_existing_state_lookup(self) -> None:
        async def scenario() -> tuple[int, int, int]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professors = [
                    Professor(name=f"Professor {index}", email=f"prof{index}@example.edu")
                    for index in range(405)
                ]
                session.add(identity)
                session.add_all(professors)
                await session.commit()

            await ensure_professor_scan_states(self.session_factory, sent_folder="Sent")

            select_count = 0

            def count_professor_state_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
                nonlocal select_count
                normalized = " ".join(statement.lower().split())
                if (
                    normalized.startswith("select")
                    and "from imap_professor_sync_states" in normalized
                ):
                    select_count += 1

            event.listen(self.engine.sync_engine, "before_cursor_execute", count_professor_state_selects)
            try:
                second_created = await ensure_professor_scan_states(self.session_factory, sent_folder="Sent")
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", count_professor_state_selects)

            async with self.session_factory() as session:
                total_states = await session.scalar(select(func.count(ImapProfessorSyncState.id)))
            return second_created, total_states, select_count

        self.assertEqual(self._run_async(scenario()), (0, 810, 3))

    def test_recent_history_candidate_states_reset_when_strategy_changes(self) -> None:
        async def scenario() -> tuple[
            int,
            str,
            int | None,
            datetime | None,
            datetime | None,
            str | None,
            str,
        ]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Known", email="Known@Example.edu")
                session.add_all([identity, professor])
                await session.flush()
                old_started_at = datetime(2026, 6, 29, 9, 0, tzinfo=UTC)
                old_completed_at = datetime(2026, 6, 29, 9, 30, tzinfo=UTC)
                state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="known@example.edu",
                    folder_role="inbox",
                    folder="INBOX",
                    historical_scan_status=ImapProfessorHistoricalScanStatus.COMPLETED.value,
                    last_scanned_uid=900,
                    historical_scan_started_at=old_started_at,
                    historical_scan_completed_at=old_completed_at,
                    last_error="old error",
                    history_strategy_version="recent-v1-2024",
                )
                session.add(state)
                await session.commit()
                identity_id = identity.id
                professor_id = professor.id

            created = await ensure_recent_history_professor_scan_states(
                self.session_factory,
                identity_id=identity_id,
                candidates={(professor_id, "known@example.edu")},
                strategy_version="recent-v1-2025",
                folder="INBOX",
            )

            async with self.session_factory() as session:
                saved = await session.scalar(select(ImapProfessorSyncState))
                return (
                    created,
                    saved.historical_scan_status,
                    saved.last_scanned_uid,
                    saved.historical_scan_started_at,
                    saved.historical_scan_completed_at,
                    saved.last_error,
                    saved.history_strategy_version,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                0,
                ImapProfessorHistoricalScanStatus.PENDING.value,
                None,
                None,
                None,
                None,
                "recent-v1-2025",
            ),
        )

    def test_recent_history_candidate_email_chunks_are_bounded(self) -> None:
        chunks = list(
            imap_sync_state._chunked_values(
                ["a", "b", "c", "d", "e"],
                2,
            ),
        )

        self.assertEqual(chunks, [["a", "b"], ["c", "d"], ["e"]])

    def test_recent_history_candidate_states_handles_large_candidate_batches(self) -> None:
        async def scenario() -> tuple[int, int]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professors = [
                    Professor(name=f"Bulk {index}", email=f"bulk{index}@example.edu")
                    for index in range(5)
                ]
                session.add(identity)
                session.add_all(professors)
                await session.flush()
                for professor in professors:
                    session.add(
                        ImapProfessorSyncState(
                            identity_id=identity.id,
                            professor_id=professor.id,
                            professor_email=professor.email,
                            folder_role="inbox",
                            folder="INBOX",
                            historical_scan_status=ImapProfessorHistoricalScanStatus.COMPLETED.value,
                            last_scanned_uid=900,
                            history_strategy_version="recent-v1-2024",
                        ),
                    )
                await session.commit()
                identity_id = identity.id
                candidates = {(professor.id, professor.email) for professor in professors}

            with patch.object(imap_sync_state, "SCAN_STATE_KEY_LOOKUP_CHUNK_SIZE", 2):
                created = await ensure_recent_history_professor_scan_states(
                    self.session_factory,
                    identity_id=identity_id,
                    candidates=candidates,
                    strategy_version="recent-v1-2025",
                    folder="INBOX",
                )

            async with self.session_factory() as session:
                recent_count = await session.scalar(
                    select(func.count(ImapProfessorSyncState.id)).where(
                        ImapProfessorSyncState.history_strategy_version == "recent-v1-2025",
                    ),
                )
                assert recent_count is not None
                return created, recent_count

        self.assertEqual(self._run_async(scenario()), (0, 5))

    def test_recent_history_candidate_states_create_only_candidates(self) -> None:
        async def scenario() -> list[tuple[str, str]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                chosen = Professor(name="Chosen", email="chosen@example.edu")
                skipped = Professor(name="Skipped", email="skipped@example.edu")
                session.add_all([identity, chosen, skipped])
                await session.commit()
                identity_id = identity.id
                chosen_id = chosen.id

            await ensure_recent_history_professor_scan_states(
                self.session_factory,
                identity_id=identity_id,
                candidates={(chosen_id, "Chosen@Example.edu")},
                strategy_version="recent-v1-2025",
                folder="INBOX",
            )

            async with self.session_factory() as session:
                rows = list(
                    (
                        await session.execute(
                            select(ImapProfessorSyncState).order_by(
                                ImapProfessorSyncState.professor_email,
                            ),
                        )
                    ).scalars(),
                )
                return [(row.professor_email, row.history_strategy_version) for row in rows]

        self.assertEqual(self._run_async(scenario()), [("chosen@example.edu", "recent-v1-2025")])

    def test_recent_history_candidate_states_do_not_reset_non_candidate_same_email(self) -> None:
        async def scenario() -> tuple[
            tuple[str, int | None, str],
            tuple[str, int | None, str],
        ]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                candidate = Professor(name="Candidate", email="candidate@example.edu")
                non_candidate = Professor(name="Non Candidate", email="non-candidate@example.edu")
                session.add_all([identity, candidate, non_candidate])
                await session.flush()
                session.add_all(
                    [
                        ImapProfessorSyncState(
                            identity_id=identity.id,
                            professor_id=candidate.id,
                            professor_email="shared@example.edu",
                            folder_role="inbox",
                            folder="INBOX",
                            historical_scan_status=ImapProfessorHistoricalScanStatus.COMPLETED.value,
                            last_scanned_uid=900,
                            history_strategy_version="recent-v1-2024",
                        ),
                        ImapProfessorSyncState(
                            identity_id=identity.id,
                            professor_id=non_candidate.id,
                            professor_email="shared@example.edu",
                            folder_role="inbox",
                            folder="INBOX",
                            historical_scan_status=ImapProfessorHistoricalScanStatus.COMPLETED.value,
                            last_scanned_uid=901,
                            history_strategy_version="recent-v1-2024",
                        ),
                    ],
                )
                await session.commit()
                identity_id = identity.id
                candidate_id = candidate.id
                non_candidate_id = non_candidate.id

            await ensure_recent_history_professor_scan_states(
                self.session_factory,
                identity_id=identity_id,
                candidates={(candidate_id, "shared@example.edu")},
                strategy_version="recent-v1-2025",
                folder="INBOX",
            )

            async with self.session_factory() as session:
                rows = list((await session.execute(select(ImapProfessorSyncState))).scalars())
                by_professor_id = {row.professor_id: row for row in rows}
                candidate_state = by_professor_id[candidate_id]
                non_candidate_state = by_professor_id[non_candidate_id]
                return (
                    (
                        candidate_state.historical_scan_status,
                        candidate_state.last_scanned_uid,
                        candidate_state.history_strategy_version,
                    ),
                    (
                        non_candidate_state.historical_scan_status,
                        non_candidate_state.last_scanned_uid,
                        non_candidate_state.history_strategy_version,
                    ),
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                (ImapProfessorHistoricalScanStatus.PENDING.value, None, "recent-v1-2025"),
                (ImapProfessorHistoricalScanStatus.COMPLETED.value, 901, "recent-v1-2024"),
            ),
        )

    def test_recent_history_candidate_states_skip_invalid_professor_ids(self) -> None:
        async def scenario() -> list[tuple[int, str]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Valid", email="valid@example.edu")
                session.add_all([identity, professor])
                await session.commit()
                identity_id = identity.id
                professor_id = professor.id

            await ensure_recent_history_professor_scan_states(
                self.session_factory,
                identity_id=identity_id,
                candidates={
                    (-1, "bad@example.edu"),
                    (0, "zero@example.edu"),
                    (professor_id, "valid@example.edu"),
                },
                strategy_version="recent-v1-2025",
                folder="INBOX",
            )

            async with self.session_factory() as session:
                rows = list(
                    (
                        await session.execute(
                            select(ImapProfessorSyncState).order_by(
                                ImapProfessorSyncState.professor_id,
                            ),
                        )
                    ).scalars(),
                )
                return [(row.professor_id, row.professor_email) for row in rows]

        self.assertEqual(self._run_async(scenario()), [(1, "valid@example.edu")])

    def test_ensure_professor_scan_states_if_needed_skips_unchanged_fingerprint(self) -> None:
        async def scenario() -> tuple[int, int, int]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, professor])
                await session.commit()

            await self._mark_mailbox_history_completed(1)
            await self._mark_mailbox_history_completed(1, folder_role="sent", folder="Sent")

            first_created = await ensure_professor_scan_states_if_needed(
                self.session_factory,
                identity_id=1,
                sent_folder="Sent",
            )
            second_created = await ensure_professor_scan_states_if_needed(
                self.session_factory,
                identity_id=1,
                sent_folder="Sent",
            )
            async with self.session_factory() as session:
                total_states = await session.scalar(select(func.count(ImapProfessorSyncState.id)))
            return first_created, second_created, total_states

        self.assertEqual(self._run_async(scenario()), (2, 0, 2))

    def test_ensure_professor_scan_states_if_needed_runs_when_professor_changes(self) -> None:
        async def scenario() -> tuple[int, int, int, int]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, professor])
                await session.commit()

            await self._mark_mailbox_history_completed(1)
            await self._mark_mailbox_history_completed(1, folder_role="sent", folder="Sent")

            await ensure_professor_scan_states_if_needed(
                self.session_factory,
                identity_id=1,
                sent_folder="Sent",
            )

            async with self.session_factory() as session:
                session.add(Professor(name="New", email="new@example.edu"))
                await session.commit()

            second_created = await ensure_professor_scan_states_if_needed(
                self.session_factory,
                identity_id=1,
                sent_folder="Sent",
            )
            async with self.session_factory() as session:
                total_states = await session.scalar(select(func.count(ImapProfessorSyncState.id)))
                pending_states = await session.scalar(
                    select(func.count(ImapProfessorSyncState.id)).where(
                        ImapProfessorSyncState.historical_scan_status
                        == ImapProfessorHistoricalScanStatus.PENDING.value,
                    ),
                )
                completed_states = await session.scalar(
                    select(func.count(ImapProfessorSyncState.id)).where(
                        ImapProfessorSyncState.historical_scan_status
                        == ImapProfessorHistoricalScanStatus.COMPLETED.value,
                    ),
                )
            return second_created, total_states, pending_states, completed_states

        self.assertEqual(self._run_async(scenario()), (2, 4, 2, 2))

    def test_ensure_professor_scan_states_if_needed_marks_changed_email_pending(self) -> None:
        async def scenario() -> tuple[int, list[tuple[str, str, str]]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, professor])
                await session.commit()
                professor_id = professor.id

            await self._mark_mailbox_history_completed(1)
            await self._mark_mailbox_history_completed(1, folder_role="sent", folder="Sent")
            await ensure_professor_scan_states_if_needed(
                self.session_factory,
                identity_id=1,
                sent_folder="Sent",
            )

            async with self.session_factory() as session:
                professor = await session.get(Professor, professor_id)
                professor.email = "new-known@example.edu"
                await session.commit()

            created = await ensure_professor_scan_states_if_needed(
                self.session_factory,
                identity_id=1,
                sent_folder="Sent",
            )

            async with self.session_factory() as session:
                states = list(
                    (
                        await session.execute(
                            select(ImapProfessorSyncState).order_by(
                                ImapProfessorSyncState.professor_email,
                                ImapProfessorSyncState.folder_role,
                            ),
                        )
                    ).scalars(),
                )
                return created, [
                    (state.professor_email, state.folder_role, state.historical_scan_status)
                    for state in states
                ]

        self.assertEqual(
            self._run_async(scenario()),
            (
                2,
                [
                    ("known@example.edu", "inbox", ImapProfessorHistoricalScanStatus.COMPLETED.value),
                    ("known@example.edu", "sent", ImapProfessorHistoricalScanStatus.COMPLETED.value),
                    ("new-known@example.edu", "inbox", ImapProfessorHistoricalScanStatus.PENDING.value),
                    ("new-known@example.edu", "sent", ImapProfessorHistoricalScanStatus.PENDING.value),
                ],
            ),
        )

    def test_ensure_professor_scan_states_if_needed_baselines_later_sent_folder(self) -> None:
        async def scenario() -> tuple[int, int, int, int]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, professor])
                await session.commit()

            await self._mark_mailbox_history_completed(1)
            await ensure_professor_scan_states_if_needed(
                self.session_factory,
                identity_id=1,
                sent_folder=None,
            )

            await self._mark_mailbox_history_completed(1, folder_role="sent", folder="Sent")
            created = await ensure_professor_scan_states_if_needed(
                self.session_factory,
                identity_id=1,
                sent_folder="Sent",
            )

            async with self.session_factory() as session:
                total_states = await session.scalar(select(func.count(ImapProfessorSyncState.id)))
                pending_states = await session.scalar(
                    select(func.count(ImapProfessorSyncState.id)).where(
                        ImapProfessorSyncState.historical_scan_status
                        == ImapProfessorHistoricalScanStatus.PENDING.value,
                    ),
                )
                completed_states = await session.scalar(
                    select(func.count(ImapProfessorSyncState.id)).where(
                        ImapProfessorSyncState.historical_scan_status
                        == ImapProfessorHistoricalScanStatus.COMPLETED.value,
                    ),
                )
            return created, total_states, pending_states, completed_states

        self.assertEqual(self._run_async(scenario()), (1, 2, 0, 2))

    def test_ensure_professor_scan_states_if_needed_waits_for_mailbox_history_completion(self) -> None:
        async def scenario() -> tuple[int, int, str | None]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, professor])
                await session.commit()

            created = await ensure_professor_scan_states_if_needed(
                self.session_factory,
                identity_id=1,
                sent_folder="Sent",
            )

            async with self.session_factory() as session:
                total_states = await session.scalar(select(func.count(ImapProfessorSyncState.id)))
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == 1,
                        ImapMailboxSyncState.folder_role == "inbox",
                    ),
                )
                if state is None:
                    return created, total_states, None
                return created, total_states, state.professor_state_fingerprint

        self.assertEqual(self._run_async(scenario()), (0, 0, None))

    def test_history_scan_baselines_legacy_targeted_queue_after_mailbox_history_completion(self) -> None:
        async def scenario() -> tuple[int, str, int]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                legacy_state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="known@example.edu",
                    folder_role="inbox",
                    folder="INBOX",
                    historical_scan_status=ImapProfessorHistoricalScanStatus.PENDING.value,
                )
                mailbox_state = ImapMailboxSyncState(
                    identity_id=identity.id,
                    folder_role="inbox",
                    folder="INBOX",
                    history_scan_status=ImapMailboxHistoricalScanStatus.COMPLETED.value,
                    history_next_before_uid=0,
                )
                session.add_all([legacy_state, mailbox_state])
                await session.commit()
                identity_id = identity.id
                legacy_state_id = legacy_state.id

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.discover_sent_folder",
                    new=AsyncMock(return_value=None),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(side_effect=AssertionError("legacy targeted queue should be baselined")),
                ) as targeted_fetch_mock,
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.get(ImapProfessorSyncState, legacy_state_id)
                return detected, state.historical_scan_status, targeted_fetch_mock.await_count

        self.assertEqual(
            self._run_async(scenario()),
            (0, ImapProfessorHistoricalScanStatus.COMPLETED.value, 0),
        )

    def test_history_scan_baselines_legacy_targeted_queue_when_fingerprint_already_exists(self) -> None:
        async def scenario() -> tuple[int, str, int]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                legacy_state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="known@example.edu",
                    folder_role="inbox",
                    folder="INBOX",
                    historical_scan_status=ImapProfessorHistoricalScanStatus.PENDING.value,
                )
                mailbox_state = ImapMailboxSyncState(
                    identity_id=identity.id,
                    folder_role="inbox",
                    folder="INBOX",
                    history_scan_status=ImapMailboxHistoricalScanStatus.COMPLETED.value,
                    history_next_before_uid=0,
                    professor_state_fingerprint="legacy-fingerprint",
                    last_professor_state_ensure_at=datetime(2026, 6, 30, tzinfo=UTC),
                )
                session.add_all([legacy_state, mailbox_state])
                await session.commit()
                identity_id = identity.id
                legacy_state_id = legacy_state.id

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.discover_sent_folder",
                    new=AsyncMock(return_value=None),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(side_effect=AssertionError("legacy targeted queue should be baselined")),
                ) as targeted_fetch_mock,
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.get(ImapProfessorSyncState, legacy_state_id)
                return detected, state.historical_scan_status, targeted_fetch_mock.await_count

        self.assertEqual(
            self._run_async(scenario()),
            (0, ImapProfessorHistoricalScanStatus.COMPLETED.value, 0),
        )

    def test_ensure_professor_scan_states_if_needed_records_fingerprint_after_success(self) -> None:
        async def scenario() -> tuple[str | None, bool]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, professor])
                await session.commit()

            await self._mark_mailbox_history_completed(1)
            await self._mark_mailbox_history_completed(1, folder_role="sent", folder="Sent")

            with patch(
                "app.services.imap_sync_state.ensure_professor_scan_states",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    await ensure_professor_scan_states_if_needed(
                        self.session_factory,
                        identity_id=1,
                        sent_folder="Sent",
                    )

            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == 1,
                        ImapMailboxSyncState.folder_role == "inbox",
                    ),
                )
                if state is None:
                    return None, True
                return state.professor_state_fingerprint, state.last_professor_state_ensure_at is None

        self.assertEqual(self._run_async(scenario()), (None, True))

    def test_existing_reply_is_not_overwritten_when_content_is_present(self) -> None:
        async def scenario() -> str:
            identity_id, professor_id, task_id = await self._create_reply_task(
                status=EmailTaskStatus.SENT.value,
            )
            async with self.session_factory() as session:
                session.add(
                    EmailLog(
                        email_task_id=task_id,
                        identity_id=identity_id,
                        llm_profile_id=1,
                        professor_id=professor_id,
                        direction=EmailDirection.RECEIVED.value,
                        subject="old subject",
                        content="old content",
                        rfc_message_id="<reply@example.edu>",
                    ),
                )
                await session.commit()

            await process_imap_fetched_messages(
                self.session_factory,
                identity_id,
                [self._build_fetched_message(message_id="<reply@example.edu>", content="new content")],
            )

            async with self.session_factory() as session:
                log = await session.scalar(
                    select(EmailLog).where(EmailLog.rfc_message_id == "<reply@example.edu>"),
                )
                return log.content

        self.assertEqual(self._run_async(scenario()), "old content")

    def test_existing_received_log_still_marks_task_as_replied(self) -> None:
        async def scenario() -> tuple[int, str, bool]:
            identity_id, professor_id, task_id = await self._create_reply_task(
                status=EmailTaskStatus.SENT.value,
            )
            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_id)
                task.is_replied = False
                session.add(
                    EmailLog(
                        email_task_id=task_id,
                        identity_id=identity_id,
                        llm_profile_id=1,
                        professor_id=professor_id,
                        direction=EmailDirection.RECEIVED.value,
                        subject="old subject",
                        content="old content",
                        rfc_message_id="<already-logged-reply@example.edu>",
                        normalized_message_id="<already-logged-reply@example.edu>",
                    ),
                )
                await session.commit()

            detected = await process_imap_fetched_messages(
                self.session_factory,
                identity_id,
                [self._build_fetched_message(message_id="<already-logged-reply@example.edu>")],
            )

            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_id)
                return detected, task.status, task.is_replied

        self.assertEqual(
            self._run_async(scenario()),
            (1, EmailTaskStatus.REPLY_DETECTED.value, True),
        )

    def test_existing_reply_empty_content_is_backfilled(self) -> None:
        async def scenario() -> str:
            identity_id, professor_id, task_id = await self._create_reply_task(
                status=EmailTaskStatus.SENT.value,
            )
            async with self.session_factory() as session:
                session.add(
                    EmailLog(
                        email_task_id=task_id,
                        identity_id=identity_id,
                        llm_profile_id=1,
                        professor_id=professor_id,
                        direction=EmailDirection.RECEIVED.value,
                        subject="old subject",
                        content="",
                        rfc_message_id="<reply@example.edu>",
                    ),
                )
                await session.commit()

            await process_imap_fetched_messages(
                self.session_factory,
                identity_id,
                [self._build_fetched_message(message_id="<reply@example.edu>", content="new content")],
            )

            async with self.session_factory() as session:
                log = await session.scalar(
                    select(EmailLog).where(EmailLog.rfc_message_id == "<reply@example.edu>"),
                )
                return log.content

        self.assertEqual(self._run_async(scenario()), "new content")

    def test_existing_reply_raw_mime_content_is_replaced(self) -> None:
        async def scenario() -> str:
            identity_id, professor_id, task_id = await self._create_reply_task(
                status=EmailTaskStatus.SENT.value,
            )
            async with self.session_factory() as session:
                session.add(
                    EmailLog(
                        email_task_id=task_id,
                        identity_id=identity_id,
                        llm_profile_id=1,
                        professor_id=professor_id,
                        direction=EmailDirection.RECEIVED.value,
                        subject="old subject",
                        content=(
                            "---=_Part_1\r\n"
                            "Content-Type: text/plain; charset=utf-8\r\n"
                            "Content-Transfer-Encoding: base64\r\n\r\n"
                            "5L2g5aW9"
                        ),
                        rfc_message_id="<reply@example.edu>",
                    ),
                )
                await session.commit()

            await process_imap_fetched_messages(
                self.session_factory,
                identity_id,
                [self._build_fetched_message(message_id="<reply@example.edu>", content="你好")],
            )

            async with self.session_factory() as session:
                log = await session.scalar(
                    select(EmailLog).where(EmailLog.rfc_message_id == "<reply@example.edu>"),
                )
                return log.content

        self.assertEqual(self._run_async(scenario()), "你好")

    def test_canceled_task_is_marked_replied_when_reply_is_found(self) -> None:
        async def scenario() -> tuple[str, bool]:
            identity_id, _, task_id = await self._create_reply_task(
                status=EmailTaskStatus.CANCELED.value,
            )

            await process_imap_fetched_messages(
                self.session_factory,
                identity_id,
                [self._build_fetched_message(message_id="<new-reply@example.edu>")],
            )

            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_id)
                return task.status, task.is_replied

        self.assertEqual(
            self._run_async(scenario()),
            (EmailTaskStatus.REPLY_DETECTED.value, True),
        )

    def test_claim_next_professor_scan_claims_one_pending_state(self) -> None:
        async def scenario() -> list[str]:
            identity_id, _, _ = await self._create_reply_task(status=EmailTaskStatus.SENT.value)
            await self._create_professor_task(identity_id, "other@example.edu")
            await ensure_professor_scan_states(self.session_factory)

            from app.services.imap_sync_state import claim_next_professor_scan

            claimed = await claim_next_professor_scan(self.session_factory, identity_id)
            self.assertIsNotNone(claimed)

            async with self.session_factory() as session:
                states = list((await session.execute(select(ImapProfessorSyncState))).scalars())
                return [state.historical_scan_status for state in states]

        statuses = self._run_async(scenario())
        self.assertEqual(statuses.count("running"), 1)

    def test_stale_running_professor_scans_are_reclaimed(self) -> None:
        async def scenario() -> tuple[int, str]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Stale", email="stale@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="stale@example.edu",
                    folder_role="inbox",
                    folder="INBOX",
                    historical_scan_status=ImapProfessorHistoricalScanStatus.RUNNING.value,
                    historical_scan_started_at=datetime(2026, 6, 29, tzinfo=UTC),
                )
                session.add(state)
                await session.commit()
                identity_id = identity.id
                state_id = state.id

            await self._mark_mailbox_history_completed(identity_id)
            await self._mark_targeted_history_baseline_ready(identity_id)

            full_message = self._build_fetched_message(
                uid=101,
                uidvalidity=999,
                message_id="<stale-recovered@example.edu>",
                from_email="stale@example.edu",
            )
            with (
                patch("app.services.imap_sync_state.utc_now", return_value=datetime(2026, 6, 30, tzinfo=UTC)),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(return_value=ImapHistoryHeaderFetchResult([full_message], 1)),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(return_value=[full_message]),
                ),
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.get(ImapProfessorSyncState, state_id)
                return detected, state.historical_scan_status

        self.assertEqual(
            self._run_async(scenario()),
            (1, ImapProfessorHistoricalScanStatus.COMPLETED.value),
        )

    def test_poll_for_replies_uses_incremental_entrypoint_only(self) -> None:
        async def scenario() -> int:
            identity_id = await self._create_identity_with_imap()
            with (
                patch(
                    "app.services.task_runtime.sync_identity_incremental_poll_once",
                    new=AsyncMock(return_value=2),
                ) as incremental_mock,
                patch(
                    "app.services.task_runtime.sync_identity_history_poll_once",
                    new=AsyncMock(return_value=9),
                ) as history_mock,
            ):
                result = await poll_for_replies_once(self.session_factory)
            incremental_mock.assert_awaited_once_with(self.session_factory, identity_id)
            history_mock.assert_not_awaited()
            return result

        self.assertEqual(self._run_async(scenario()), 2)

    def test_poll_imap_history_uses_history_entrypoint_only(self) -> None:
        async def scenario() -> int:
            identity_id = await self._create_identity_with_imap()
            with (
                patch(
                    "app.services.task_runtime.sync_identity_incremental_poll_once",
                    new=AsyncMock(return_value=2),
                ) as incremental_mock,
                patch(
                    "app.services.task_runtime.sync_identity_history_poll_once",
                    new=AsyncMock(return_value=3),
                ) as history_mock,
            ):
                result = await poll_imap_history_once(self.session_factory)
            history_mock.assert_awaited_once_with(self.session_factory, identity_id)
            incremental_mock.assert_not_awaited()
            return result

        self.assertEqual(self._run_async(scenario()), 3)

    def test_poll_for_replies_skips_incomplete_and_blank_imap_identities(self) -> None:
        async def scenario() -> tuple[int, bool, int]:
            valid_id = await self._create_identity_with_imap()
            async with self.session_factory() as session:
                missing_port = self._build_identity()
                missing_port.name = "缺少端口"
                missing_port.profile_name = "缺少端口"
                missing_port.email_address = "missing-port@example.com"
                missing_port.smtp_username = "missing-port@example.com"
                missing_port.imap_username = "missing-port@example.com"
                missing_port.imap_port = None

                blank_host = self._build_identity()
                blank_host.name = "空白主机"
                blank_host.profile_name = "空白主机"
                blank_host.email_address = "blank-host@example.com"
                blank_host.smtp_username = "blank-host@example.com"
                blank_host.imap_username = "blank-host@example.com"
                blank_host.imap_host = "  "

                blank_username = self._build_identity()
                blank_username.name = "空白用户名"
                blank_username.profile_name = "空白用户名"
                blank_username.email_address = "blank-username@example.com"
                blank_username.smtp_username = "blank-username@example.com"
                blank_username.imap_username = "  "

                blank_password = self._build_identity()
                blank_password.name = "空白密码"
                blank_password.profile_name = "空白密码"
                blank_password.email_address = "blank-password@example.com"
                blank_password.smtp_username = "blank-password@example.com"
                blank_password.imap_username = "blank-password@example.com"
                blank_password.imap_password = "  "

                session.add_all([missing_port, blank_host, blank_username, blank_password])
                await session.commit()

            seen_identity_ids: list[int] = []

            async def fake_sync(_session_factory, identity_id: int) -> int:
                seen_identity_ids.append(identity_id)
                return 1

            with patch(
                "app.services.task_runtime.sync_identity_incremental_poll_once",
                new=AsyncMock(side_effect=fake_sync),
            ):
                result = await poll_for_replies_once(self.session_factory)
            return result, seen_identity_ids == [valid_id], len(seen_identity_ids)

        self.assertEqual(self._run_async(scenario()), (1, True, 1))

    def test_incremental_sync_keeps_cursor_and_records_error_when_fetch_fails(self) -> None:
        async def scenario() -> tuple[int | None, str | None]:
            identity_id = await self._create_identity_with_imap()
            async with self.session_factory() as session:
                session.add(ImapMailboxSyncState(identity_id=identity_id, last_seen_uid=10))
                await session.commit()

            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                new=AsyncMock(side_effect=RuntimeError("fetch failed")),
            ):
                result = await sync_identity_incremental_once(self.session_factory, identity_id)

            self.assertEqual(result, 0)
            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                    ),
                )
                return state.last_seen_uid, state.last_error

        self.assertEqual(self._run_async(scenario()), (10, "fetch failed"))

    def test_incremental_sync_does_not_clear_or_rewind_cursor(self) -> None:
        async def scenario() -> tuple[int | None, int | None]:
            identity_id = await self._create_identity_with_imap()
            async with self.session_factory() as session:
                session.add(ImapMailboxSyncState(identity_id=identity_id, last_seen_uid=10))
                await session.commit()

            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                new=AsyncMock(return_value=(None, [], None)),
            ):
                await sync_identity_incremental_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                    ),
                )
                after_none = state.last_seen_uid

            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                new=AsyncMock(return_value=(7, [], None)),
            ):
                await sync_identity_incremental_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                    ),
                )
                return after_none, state.last_seen_uid

        self.assertEqual(self._run_async(scenario()), (10, 10))

    def test_incremental_sync_resets_cursor_when_uidvalidity_changes(self) -> None:
        async def scenario() -> tuple[int | None, int | None, int | None]:
            identity_id = await self._create_identity_with_imap()
            async with self.session_factory() as session:
                session.add(
                    ImapMailboxSyncState(
                        identity_id=identity_id,
                        folder_role="inbox",
                        folder="INBOX",
                        uidvalidity=111,
                        last_seen_uid=99,
                    ),
                )
                await session.commit()

            message = self._build_fetched_message(
                uid=1,
                uidvalidity=222,
                message_id="<uidvalidity-reset@example.edu>",
                from_email="Prof <prof@example.edu>",
                to_emails=["student@example.com"],
            )
            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                new=AsyncMock(return_value=(1, [message], 222)),
            ) as mocked:
                await sync_identity_incremental_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "inbox",
                    ),
                )
                return mocked.await_args.kwargs["expected_uidvalidity"], state.uidvalidity, state.last_seen_uid

        self.assertEqual(self._run_async(scenario()), (111, 222, 1))

    def test_incremental_sync_resets_legacy_cursor_when_uidvalidity_becomes_known(self) -> None:
        async def scenario() -> tuple[int | None, int | None, int | None]:
            identity_id = await self._create_identity_with_imap()
            async with self.session_factory() as session:
                session.add(
                    ImapMailboxSyncState(
                        identity_id=identity_id,
                        folder_role="inbox",
                        folder="INBOX",
                        uidvalidity=None,
                        last_seen_uid=99,
                    ),
                )
                await session.commit()

            message = self._build_fetched_message(
                uid=1,
                uidvalidity=222,
                message_id="<legacy-uidvalidity-reset@example.edu>",
                from_email="Prof <prof@example.edu>",
                to_emails=["student@example.com"],
            )
            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                new=AsyncMock(return_value=(1, [message], 222)),
            ) as mocked:
                await sync_identity_incremental_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "inbox",
                    ),
                )
                return mocked.await_args.kwargs["expected_uidvalidity"], state.uidvalidity, state.last_seen_uid

        self.assertEqual(self._run_async(scenario()), (None, 222, 1))

    def test_incremental_sync_fallback_uidvalidity_mismatch_does_not_advance_old_cursor(self) -> None:
        async def scenario() -> tuple[int | None, int | None]:
            identity_id = await self._create_identity_with_imap()
            async with self.session_factory() as session:
                session.add(
                    ImapMailboxSyncState(
                        identity_id=identity_id,
                        folder_role="inbox",
                        folder="INBOX",
                        uidvalidity=111,
                        last_seen_uid=99,
                    ),
                )
                await session.commit()

            message = self._build_fetched_message(
                uid=100,
                uidvalidity=222,
                message_id="<fallback-uidvalidity-mismatch@example.edu>",
                from_email="Prof <prof@example.edu>",
                to_emails=["student@example.com"],
            )
            with (
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                    new=None,
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages",
                    new=AsyncMock(return_value=(100, [message])),
                ),
            ):
                await sync_identity_incremental_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "inbox",
                    ),
                )
                return state.uidvalidity, state.last_seen_uid

        self.assertEqual(self._run_async(scenario()), (222, None))

    def test_incremental_sync_rejects_unknown_folder_role_without_creating_state(self) -> None:
        async def scenario() -> int:
            identity_id = await self._create_identity_with_imap()

            with self.assertRaisesRegex(ValueError, "folder_role"):
                await sync_identity_incremental_once(
                    self.session_factory,
                    identity_id,
                    folder_role="archive",
                    folder="Archive",
                )

            async with self.session_factory() as session:
                return len(list((await session.execute(select(ImapMailboxSyncState))).scalars()))

        self.assertEqual(self._run_async(scenario()), 0)

    def test_sent_incremental_sync_advances_sent_cursor_and_records_existing_professor_mail(self) -> None:
        async def scenario() -> tuple[
            int,
            int | None,
            str | None,
            list[tuple[str, str, str, int | None, int | None]],
            int,
            str,
            int | None,
        ]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Known", email="Known@Example.edu")
                session.add_all([identity, professor])
                await session.commit()
                identity_id = identity.id

            await self._mark_mailbox_history_completed(identity_id, folder_role="sent", folder="Sent")

            sent_message = self._build_fetched_message(
                uid=21,
                message_id="<sent-known@example.com>",
                from_email="student@example.com",
                to_emails=["Known <known@example.edu>"],
                cc_emails=["outsider@example.edu"],
                subject="Hello known",
                content="sent content",
            )
            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                new=AsyncMock(return_value=(21, [sent_message], None)),
            ) as mocked:
                detected = await sync_identity_incremental_once(
                    self.session_factory,
                    identity_id,
                    folder_role="sent",
                    folder="Sent",
                )

            fetched_identity, fetched_folder, fetched_cursor = mocked.await_args.args
            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "sent",
                        ImapMailboxSyncState.folder == "Sent",
                    ),
                )
                logs = list((await session.execute(select(EmailLog))).scalars())
                return (
                    detected,
                    state.last_seen_uid,
                    state.last_error,
                    [
                        (log.direction, log.folder_role, log.folder, log.llm_profile_id, log.imap_uid)
                        for log in logs
                    ],
                    fetched_identity.id,
                    fetched_folder,
                    fetched_cursor,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                1,
                21,
                None,
                [(EmailDirection.SENT.value, "sent", "Sent", None, 21)],
                1,
                "Sent",
                None,
            ),
        )

    def test_sent_ingestion_records_log_for_each_active_professor_sharing_email(self) -> None:
        async def scenario() -> tuple[int, list[tuple[int, str, str, int | None]]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor_a = Professor(name="A", email="shared@example.edu")
                professor_b = Professor(name="B", email="Shared@Example.edu")
                session.add_all([identity, professor_a, professor_b])
                await session.commit()
                identity_id = identity.id

            message = self._build_fetched_message(
                uid=61,
                uidvalidity=777,
                message_id="<sent-shared-professors@example.com>",
                from_email="student@example.com",
                to_emails=["shared@example.edu"],
                subject="Shared address",
                content="sent body",
            )
            detected = await process_imap_fetched_messages(
                self.session_factory,
                identity_id,
                [message],
                folder_role="sent",
                folder="Sent",
            )

            async with self.session_factory() as session:
                logs = list(
                    (
                        await session.execute(
                            select(EmailLog).order_by(EmailLog.professor_id),
                        )
                    ).scalars(),
                )
                return detected, [
                    (log.professor_id, log.direction, log.rfc_message_id, log.imap_uid)
                    for log in logs
                ]

        self.assertEqual(
            self._run_async(scenario()),
            (
                2,
                [
                    (1, EmailDirection.SENT.value, "<sent-shared-professors@example.com>", 61),
                    (2, EmailDirection.SENT.value, "<sent-shared-professors@example.com>", 61),
                ],
            ),
        )

    def test_sent_incremental_sync_ignores_non_system_professors(self) -> None:
        async def scenario() -> int:
            identity_id = await self._create_identity_with_imap()
            await self._mark_mailbox_history_completed(identity_id, folder_role="sent", folder="Sent")
            sent_message = self._build_fetched_message(
                uid=22,
                message_id="<sent-outsider@example.com>",
                from_email="student@example.com",
                to_emails=["outsider@example.edu"],
            )
            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                new=AsyncMock(return_value=(22, [sent_message], None)),
            ):
                detected = await sync_identity_incremental_once(
                    self.session_factory,
                    identity_id,
                    folder_role="sent",
                    folder="Sent",
                )

            async with self.session_factory() as session:
                count = len(list((await session.execute(select(EmailLog))).scalars()))
                return detected + count

        self.assertEqual(self._run_async(scenario()), 0)

    def test_sent_incremental_sync_does_not_mutate_unmatched_latest_task(self) -> None:
        async def scenario() -> tuple[int, tuple[str, str | None], tuple[int | None, str, str | None]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                llm = self._build_llm()
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, llm, professor])
                await session.flush()
                pending_task = EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm.id,
                    professor_id=professor.id,
                    status=EmailTaskStatus.REVIEW_REQUIRED.value,
                    last_rfc_message_id="<current-draft@example.com>",
                )
                session.add(pending_task)
                await session.commit()
                identity_id = identity.id
                pending_task_id = pending_task.id

            await self._mark_mailbox_history_completed(identity_id, folder_role="sent", folder="Sent")

            old_message = self._build_fetched_message(
                uid=24,
                message_id="<old-sent@example.com>",
                from_email="student@example.com",
                to_emails=["known@example.edu"],
                subject="Old sent",
                content="old body",
            )
            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                new=AsyncMock(return_value=(24, [old_message], None)),
            ):
                detected = await sync_identity_incremental_once(
                    self.session_factory,
                    identity_id,
                    folder_role="sent",
                    folder="Sent",
                )

            async with self.session_factory() as session:
                pending_task = await session.get(EmailTask, pending_task_id)
                log = await session.scalar(
                    select(EmailLog).where(EmailLog.rfc_message_id == "<old-sent@example.com>"),
                )
                return (
                    detected,
                    (pending_task.status, pending_task.last_rfc_message_id),
                    (log.email_task_id, log.direction, log.rfc_message_id),
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                1,
                (EmailTaskStatus.REVIEW_REQUIRED.value, "<current-draft@example.com>"),
                (None, EmailDirection.SENT.value, "<old-sent@example.com>"),
            ),
        )

    def test_sent_incremental_sync_does_not_mutate_unmatched_canceled_task(self) -> None:
        async def scenario() -> tuple[int, str, str | None, int | None]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                llm = self._build_llm()
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, llm, professor])
                await session.flush()
                task = EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm.id,
                    professor_id=professor.id,
                    status=EmailTaskStatus.CANCELED.value,
                    last_rfc_message_id="<canceled@example.com>",
                )
                session.add(task)
                await session.commit()
                identity_id = identity.id
                task_id = task.id

            await self._mark_mailbox_history_completed(identity_id, folder_role="sent", folder="Sent")

            old_message = self._build_fetched_message(
                uid=25,
                message_id="<old-canceled-sent@example.com>",
                from_email="student@example.com",
                to_emails=["known@example.edu"],
            )
            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                new=AsyncMock(return_value=(25, [old_message], None)),
            ):
                detected = await sync_identity_incremental_once(
                    self.session_factory,
                    identity_id,
                    folder_role="sent",
                    folder="Sent",
                )

            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_id)
                log = await session.scalar(
                    select(EmailLog).where(EmailLog.rfc_message_id == "<old-canceled-sent@example.com>"),
                )
                return detected, task.status, task.last_rfc_message_id, log.email_task_id

        self.assertEqual(
            self._run_async(scenario()),
            (1, EmailTaskStatus.CANCELED.value, "<canceled@example.com>", None),
        )

    def test_sent_incremental_sync_backfills_sent_metadata_without_downgrading_replied_task(self) -> None:
        async def scenario() -> tuple[int, str, bool, datetime | None, str | None, tuple[str, str, int | None]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                llm = self._build_llm()
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, llm, professor])
                await session.flush()
                task = EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm.id,
                    professor_id=professor.id,
                    status=EmailTaskStatus.REPLY_DETECTED.value,
                    is_replied=True,
                    sent_at=None,
                    last_rfc_message_id="<sent-replied@example.com>",
                )
                session.add(task)
                session.add(
                    EmailLog(
                        email_task=task,
                        identity=identity,
                        llm_profile=llm,
                        professor=professor,
                        direction=EmailDirection.SENT.value,
                        subject="existing sent",
                        content="existing",
                        rfc_message_id="<sent-replied@example.com>",
                    ),
                )
                await session.commit()
                identity_id = identity.id
                task_id = task.id

            await self._mark_mailbox_history_completed(identity_id, folder_role="sent", folder="Sent")

            sent_at = datetime(2026, 5, 3, 9, 30, tzinfo=UTC)
            sent_message = self._build_fetched_message(
                uid=23,
                message_id="<sent-replied@example.com>",
                from_email="student@example.com",
                to_emails=["known@example.edu"],
                subject="Hello after reply",
                content="sent body",
            )
            sent_message.sent_at = sent_at

            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                new=AsyncMock(return_value=(23, [sent_message], None)),
            ):
                detected = await sync_identity_incremental_once(
                    self.session_factory,
                    identity_id,
                    folder_role="sent",
                    folder="Sent",
                )

            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_id)
                log = await session.scalar(
                    select(EmailLog).where(EmailLog.rfc_message_id == "<sent-replied@example.com>"),
                )
                return (
                    detected,
                    task.status,
                    task.is_replied,
                    task.sent_at,
                    task.last_rfc_message_id,
                    (log.direction, log.folder_role, log.imap_uid),
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                1,
                EmailTaskStatus.REPLY_DETECTED.value,
                True,
                datetime(2026, 5, 3, 9, 30, tzinfo=UTC),
                "<sent-replied@example.com>",
                (EmailDirection.SENT.value, "sent", 23),
            ),
        )

    def test_inbox_and_sent_logs_record_uidvalidity(self) -> None:
        async def scenario() -> tuple[int | None, int | None]:
            identity_id, _, _ = await self._create_reply_task(status=EmailTaskStatus.SENT.value)
            await self._mark_mailbox_history_completed(identity_id)
            inbox_message = self._build_fetched_message(
                uid=32,
                uidvalidity=777,
                message_id="<reply-uidvalidity@example.edu>",
                from_email="Prof <prof@example.edu>",
                to_emails=["student@example.com"],
            )
            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                new=AsyncMock(return_value=(32, [inbox_message], 777)),
            ):
                await sync_identity_incremental_once(self.session_factory, identity_id)

            sent_message = self._build_fetched_message(
                uid=33,
                uidvalidity=888,
                message_id="<sent-uidvalidity@example.com>",
                from_email="student@example.com",
                to_emails=["prof@example.edu"],
            )
            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                new=AsyncMock(return_value=(33, [sent_message], 888)),
            ):
                await self._mark_mailbox_history_completed(identity_id, folder_role="sent", folder="Sent")
                await sync_identity_incremental_once(
                    self.session_factory,
                    identity_id,
                    folder_role="sent",
                    folder="Sent",
                )

            async with self.session_factory() as session:
                inbox_log = await session.scalar(
                    select(EmailLog).where(EmailLog.rfc_message_id == "<reply-uidvalidity@example.edu>"),
                )
                sent_log = await session.scalar(
                    select(EmailLog).where(EmailLog.rfc_message_id == "<sent-uidvalidity@example.com>"),
                )
                return inbox_log.uidvalidity, sent_log.uidvalidity

        self.assertEqual(self._run_async(scenario()), (777, 888))

    def test_process_imap_fetched_messages_rejects_unknown_folder_role_without_writing_log(self) -> None:
        async def scenario() -> int:
            identity_id, _, _ = await self._create_reply_task(status=EmailTaskStatus.SENT.value)

            with self.assertRaisesRegex(ValueError, "folder_role"):
                await process_imap_fetched_messages(
                    self.session_factory,
                    identity_id,
                    [self._build_fetched_message(message_id="<archive-message@example.edu>")],
                    folder_role="archive",
                    folder="Archive",
                )

            async with self.session_factory() as session:
                log = await session.scalar(
                    select(EmailLog).where(EmailLog.rfc_message_id == "<archive-message@example.edu>"),
                )
                return 0 if log is None else 1

        self.assertEqual(self._run_async(scenario()), 0)

    def test_inbox_incremental_detects_reply_and_records_imap_metadata(self) -> None:
        async def scenario() -> tuple[int, str, bool, tuple[str, str, int | None, str | None, list[str] | None]]:
            identity_id, _, task_id = await self._create_reply_task(status=EmailTaskStatus.SENT.value)
            await self._mark_mailbox_history_completed(identity_id)
            message = self._build_fetched_message(
                uid=31,
                message_id="<reply-with-metadata@example.edu>",
                from_email="Prof <prof@example.edu>",
                to_emails=["student@example.com"],
            )
            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                new=AsyncMock(return_value=(31, [message], None)),
            ):
                detected = await sync_identity_incremental_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_id)
                log = await session.scalar(
                    select(EmailLog).where(EmailLog.rfc_message_id == "<reply-with-metadata@example.edu>"),
                )
                return (
                    detected,
                    task.status,
                    task.is_replied,
                    (log.folder_role, log.folder, log.imap_uid, log.from_email, log.to_emails),
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                1,
                EmailTaskStatus.REPLY_DETECTED.value,
                True,
                ("inbox", "INBOX", 31, "prof@example.edu", ["student@example.com"]),
            ),
        )

    def test_reply_reference_matches_sent_log_scoped_to_sender_professor(self) -> None:
        async def scenario() -> tuple[int, str, bool, str, bool]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                llm = self._build_llm()
                professor_a = Professor(name="Professor A", email="prof-a@example.edu")
                professor_b = Professor(name="Professor B", email="prof-b@example.edu")
                session.add_all([identity, llm, professor_a, professor_b])
                await session.flush()
                task_a = EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm.id,
                    professor_id=professor_a.id,
                    status=EmailTaskStatus.SENT.value,
                    sent_at=datetime(2026, 5, 1, tzinfo=UTC),
                    approved_subject="Group hello",
                    last_rfc_message_id="<group-sent@example.com>",
                )
                task_b = EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm.id,
                    professor_id=professor_b.id,
                    status=EmailTaskStatus.SENT.value,
                    sent_at=datetime(2026, 5, 1, tzinfo=UTC),
                    approved_subject="Group hello",
                    last_rfc_message_id="<group-sent@example.com>",
                )
                session.add_all([task_a, task_b])
                await session.flush()
                session.add_all(
                    [
                        EmailLog(
                            email_task_id=task_a.id,
                            identity_id=identity.id,
                            llm_profile_id=llm.id,
                            professor_id=professor_a.id,
                            direction=EmailDirection.SENT.value,
                            subject="Group hello",
                            content="sent",
                            rfc_message_id="<group-sent@example.com>",
                            normalized_message_id="<group-sent@example.com>",
                            created_at=datetime(2026, 5, 1, tzinfo=UTC),
                        ),
                        EmailLog(
                            email_task_id=task_b.id,
                            identity_id=identity.id,
                            llm_profile_id=llm.id,
                            professor_id=professor_b.id,
                            direction=EmailDirection.SENT.value,
                            subject="Group hello",
                            content="sent",
                            rfc_message_id="<group-sent@example.com>",
                            normalized_message_id="<group-sent@example.com>",
                            created_at=datetime(2026, 5, 1, tzinfo=UTC),
                        ),
                    ],
                )
                await session.commit()
                identity_id = identity.id
                task_a_id = task_a.id
                task_b_id = task_b.id

            message = self._build_fetched_message(
                uid=41,
                message_id="<reply-from-b@example.edu>",
                from_email="Professor B <prof-b@example.edu>",
                to_emails=["student@example.com"],
                subject="Re: Group hello",
            )
            message.in_reply_to = "<group-sent@example.com>"
            detected = await process_imap_fetched_messages(self.session_factory, identity_id, [message])

            async with self.session_factory() as session:
                task_a = await session.get(EmailTask, task_a_id)
                task_b = await session.get(EmailTask, task_b_id)
                return detected, task_a.status, task_a.is_replied, task_b.status, task_b.is_replied

        self.assertEqual(
            self._run_async(scenario()),
            (1, EmailTaskStatus.SENT.value, False, EmailTaskStatus.REPLY_DETECTED.value, True),
        )

    def test_inbox_message_from_existing_professor_without_task_is_logged_unbound(self) -> None:
        async def scenario() -> tuple[int, tuple[int | None, int | None, str, str, int | None, int | None]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="主动来信老师", email="incoming@example.edu")
                session.add_all([identity, professor])
                await session.commit()
                identity_id = identity.id

            message = self._build_fetched_message(
                uid=42,
                uidvalidity=777,
                message_id="<incoming-without-task@example.edu>",
                from_email="Incoming Professor <incoming@example.edu>",
                to_emails=["student@example.com"],
                subject="主动来信",
                content="你好，我想了解一下你的背景。",
            )
            detected = await process_imap_fetched_messages(self.session_factory, identity_id, [message])

            async with self.session_factory() as session:
                log = await session.scalar(
                    select(EmailLog).where(
                        EmailLog.rfc_message_id == "<incoming-without-task@example.edu>",
                    ),
                )
                return detected, (
                    log.email_task_id,
                    log.llm_profile_id,
                    log.direction,
                    log.folder_role,
                    log.uidvalidity,
                    log.imap_uid,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (1, (None, None, EmailDirection.RECEIVED.value, "inbox", 777, 42)),
        )

    def test_reply_fallback_matches_professor_email_case_insensitively(self) -> None:
        async def scenario() -> tuple[int, str, bool]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                llm = self._build_llm()
                professor = Professor(name="Mixed Case", email="MixedCase@Example.edu")
                session.add_all([identity, llm, professor])
                await session.flush()
                task = EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm.id,
                    professor_id=professor.id,
                    status=EmailTaskStatus.SENT.value,
                    sent_at=datetime(2026, 5, 1, tzinfo=UTC),
                    approved_subject="Case Hello",
                    last_rfc_message_id="<case-sent@example.com>",
                )
                session.add(task)
                await session.flush()
                session.add(
                    EmailLog(
                        email_task_id=task.id,
                        identity_id=identity.id,
                        llm_profile_id=llm.id,
                        professor_id=professor.id,
                        direction=EmailDirection.SENT.value,
                        subject="Case Hello",
                        content="sent",
                        rfc_message_id="<case-sent@example.com>",
                    ),
                )
                await session.commit()
                identity_id = identity.id
                task_id = task.id

            message = self._build_fetched_message(
                uid=43,
                message_id="<case-reply@example.edu>",
                from_email="mixedcase@example.edu",
                to_emails=["student@example.com"],
                subject="Re: Case Hello",
            )
            message.in_reply_to = None
            message.references = None
            detected = await process_imap_fetched_messages(self.session_factory, identity_id, [message])

            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_id)
                return detected, task.status, task.is_replied

        self.assertEqual(
            self._run_async(scenario()),
            (1, EmailTaskStatus.REPLY_DETECTED.value, True),
        )

    def test_recent_history_window_uses_current_and_previous_calendar_year(self) -> None:
        from app.services.task_runtime import build_recent_history_window

        self.assertEqual(
            build_recent_history_window(datetime(2026, 7, 7, 12, 0, tzinfo=UTC)).start_date.isoformat(),
            "2025-01-01",
        )
        self.assertEqual(
            build_recent_history_window(datetime(2027, 1, 1, 0, 0, tzinfo=UTC)).strategy_version,
            "recent-v1-2026",
        )

    def test_history_sync_discovers_sent_recent_messages_by_real_uid_search(self) -> None:
        async def scenario() -> tuple[int, list[dict[str, object]], list[tuple[int, str, str]]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor_a = Professor(name="A", email="a@example.edu")
                professor_b = Professor(name="B", email="b@example.edu")
                professor_c = Professor(name="C", email="c@example.edu")
                session.add_all([identity, professor_a, professor_b, professor_c])
                await session.commit()
                identity_id = identity.id

            header = self._build_fetched_message(
                uid=51,
                uidvalidity=777,
                message_id="<multi-teacher@example.com>",
                from_email="student@example.com",
                to_emails=["A <a@example.edu>", "b@example.edu", "stranger@example.edu"],
                subject="Hello",
                content="",
            )
            body = self._build_fetched_message(
                uid=51,
                uidvalidity=777,
                message_id="<multi-teacher@example.com>",
                from_email="student@example.com",
                to_emails=["A <a@example.edu>", "b@example.edu", "stranger@example.edu"],
                subject="Hello",
                content="sent body",
            )
            header_calls: list[dict[str, object]] = []

            async def fake_recent_headers(_identity, _folder, since_date, *, min_uid, max_fetch_batches):
                header_calls.append(
                    {
                        "folder": _folder,
                        "since_date": since_date.isoformat(),
                        "min_uid": min_uid,
                        "max_fetch_batches": max_fetch_batches,
                    },
                )
                return ImapHistoryHeaderFetchResult(messages=[header], command_count=2, exhausted=False)

            with (
                patch("app.services.task_runtime.get_cached_or_discover_sent_folder", new=AsyncMock(return_value="Sent")),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_recent_mailbox_message_headers_since",
                    new=AsyncMock(side_effect=fake_recent_headers),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(return_value=[body]),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_history_mailbox_message_headers_before_uid",
                    new=AsyncMock(side_effect=AssertionError("legacy uid range scan must not run")),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(return_value=ImapHistoryHeaderFetchResult(messages=[], command_count=1)),
                ),
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                logs = list(
                    (
                        await session.execute(
                            select(EmailLog).order_by(EmailLog.professor_id),
                        )
                    ).scalars(),
                )
                return detected, header_calls, [
                    (log.professor_id, log.direction, log.content) for log in logs
                ]

        detected, header_calls, logs = self._run_async(scenario())
        self.assertEqual(detected, 2)
        self.assertEqual(header_calls[0]["folder"], "Sent")
        self.assertEqual(header_calls[0]["since_date"], "2025-01-01")
        self.assertEqual(logs, [(1, "sent", "sent body"), (2, "sent", "sent body")])

    def test_recent_sent_history_keeps_high_water_on_partial_body_budget(self) -> None:
        async def scenario() -> tuple[int, list[int], int | None, str, int, int]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor_a = Professor(name="A", email="a@example.edu")
                professor_b = Professor(name="B", email="b@example.edu")
                session.add_all([identity, professor_a, professor_b])
                await session.commit()
                identity_id = identity.id

            headers = [
                self._build_fetched_message(
                    uid=11,
                    uidvalidity=777,
                    message_id="<recent-sent-11@example.com>",
                    from_email="student@example.com",
                    to_emails=["a@example.edu"],
                    content="",
                ),
                self._build_fetched_message(
                    uid=12,
                    uidvalidity=777,
                    message_id="<recent-sent-12@example.com>",
                    from_email="student@example.com",
                    to_emails=["b@example.edu"],
                    content="",
                ),
            ]
            body = self._build_fetched_message(
                uid=11,
                uidvalidity=777,
                message_id="<recent-sent-11@example.com>",
                from_email="student@example.com",
                to_emails=["a@example.edu"],
                content="sent body 11",
            )
            fetched_uids: list[int] = []

            async def fake_body_fetch(_identity, _folder, uids: list[int]):
                fetched_uids.extend(uids)
                self.assertEqual(uids, [11])
                return [body]

            with (
                patch("app.services.task_runtime.get_settings") as settings_mock,
                patch("app.services.task_runtime.get_cached_or_discover_sent_folder", new=AsyncMock(return_value="Sent")),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_recent_mailbox_message_headers_since",
                    new=AsyncMock(
                        return_value=ImapHistoryHeaderFetchResult(
                            messages=headers,
                            command_count=1,
                            exhausted=False,
                        ),
                    ),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(side_effect=fake_body_fetch),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_history_mailbox_message_headers_before_uid",
                    new=AsyncMock(side_effect=AssertionError("legacy uid range scan must not run")),
                ),
            ):
                settings_mock.return_value.imap_history_batch_size = 200
                settings_mock.return_value.imap_history_command_budget_per_minute = 8
                settings_mock.return_value.imap_fetch_batch_size = 20
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "sent",
                        ImapMailboxSyncState.folder == "Sent",
                    ),
                )
                return (
                    detected,
                    fetched_uids,
                    state.history_high_water_uid,
                    state.history_scan_status,
                    state.history_scanned_count,
                    state.history_matched_count,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (1, [11], 11, "sent_recent_discovery_running", 1, 1),
        )

    def test_recent_sent_history_advances_exhausted_page_with_only_non_professor_headers(self) -> None:
        async def scenario() -> tuple[
            int,
            tuple[int | None, str, int, int],
            int,
            tuple[int | None, str, int, int],
            list[int | None],
        ]:
            identity_id = await self._create_identity_with_imap()
            header_calls: list[int | None] = []
            first_page = [
                self._build_fetched_message(
                    uid=31,
                    uidvalidity=777,
                    message_id="<recent-sent-outsider-31@example.com>",
                    from_email="student@example.com",
                    to_emails=["outsider-a@example.edu"],
                    content="",
                ),
                self._build_fetched_message(
                    uid=33,
                    uidvalidity=777,
                    message_id="<recent-sent-outsider-33@example.com>",
                    from_email="student@example.com",
                    to_emails=["outsider-b@example.edu"],
                    content="",
                ),
            ]

            async def fake_recent_headers(_identity, _folder, _since_date, *, min_uid, max_fetch_batches):
                header_calls.append(min_uid)
                if len(header_calls) == 1:
                    return ImapHistoryHeaderFetchResult(
                        messages=first_page,
                        command_count=1,
                        exhausted=True,
                    )
                return ImapHistoryHeaderFetchResult(
                    messages=[],
                    command_count=1,
                    exhausted=False,
                )

            with (
                patch("app.services.task_runtime.get_cached_or_discover_sent_folder", new=AsyncMock(return_value="Sent")),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_recent_mailbox_message_headers_since",
                    new=AsyncMock(side_effect=fake_recent_headers),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(side_effect=AssertionError("non-professor headers do not need bodies")),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_history_mailbox_message_headers_before_uid",
                    new=AsyncMock(side_effect=AssertionError("legacy uid range scan must not run")),
                ),
            ):
                first_detected = await sync_identity_history_once(self.session_factory, identity_id)
                async with self.session_factory() as session:
                    first_state = await session.scalar(
                        select(ImapMailboxSyncState).where(
                            ImapMailboxSyncState.identity_id == identity_id,
                            ImapMailboxSyncState.folder_role == "sent",
                            ImapMailboxSyncState.folder == "Sent",
                        ),
                    )
                    first_snapshot = (
                        first_state.history_high_water_uid,
                        first_state.history_scan_status,
                        first_state.history_scanned_count,
                        first_state.history_matched_count,
                    )
                second_detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "sent",
                        ImapMailboxSyncState.folder == "Sent",
                    ),
                )
                return (
                    first_detected,
                    first_snapshot,
                    second_detected,
                    (
                        state.history_high_water_uid,
                        state.history_scan_status,
                        state.history_scanned_count,
                        state.history_matched_count,
                    ),
                    header_calls,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                0,
                (33, "sent_recent_discovery_running", 2, 0),
                0,
                (33, "inbox_recent_replies_pending", 2, 0),
                [None, 33],
            ),
        )

    def test_recent_sent_history_waits_when_body_budget_unavailable(self) -> None:
        async def scenario() -> tuple[int, int, int | None, str, str | None]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="A", email="a@example.edu")
                session.add_all([identity, professor])
                await session.commit()
                identity_id = identity.id

            header = self._build_fetched_message(
                uid=21,
                uidvalidity=777,
                message_id="<recent-sent-wait@example.com>",
                from_email="student@example.com",
                to_emails=["a@example.edu"],
                content="",
            )
            with (
                patch("app.services.task_runtime.get_settings") as settings_mock,
                patch("app.services.task_runtime.get_cached_or_discover_sent_folder", new=AsyncMock(return_value="Sent")),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_recent_mailbox_message_headers_since",
                    new=AsyncMock(
                        return_value=ImapHistoryHeaderFetchResult(
                            messages=[header],
                            command_count=1,
                            exhausted=False,
                        ),
                    ),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(side_effect=AssertionError("body fetch must not run")),
                ) as body_fetch_mock,
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_history_mailbox_message_headers_before_uid",
                    new=AsyncMock(side_effect=AssertionError("legacy uid range scan must not run")),
                ),
            ):
                settings_mock.return_value.imap_history_batch_size = 200
                settings_mock.return_value.imap_history_command_budget_per_minute = 1
                settings_mock.return_value.imap_fetch_batch_size = 20
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "sent",
                        ImapMailboxSyncState.folder == "Sent",
                    ),
                )
                return (
                    detected,
                    body_fetch_mock.await_count,
                    state.history_high_water_uid,
                    state.history_scan_status,
                    state.history_last_error,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (0, 0, None, "sent_recent_discovery_running", None),
        )

    def test_recent_sent_history_records_failure_state(self) -> None:
        async def scenario() -> tuple[int, str, str | None]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                session.add(identity)
                await session.commit()
                identity_id = identity.id

            with (
                patch("app.services.task_runtime.get_cached_or_discover_sent_folder", new=AsyncMock(return_value="Sent")),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_recent_mailbox_message_headers_since",
                    new=AsyncMock(side_effect=RuntimeError("boom")),
                ),
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "sent",
                        ImapMailboxSyncState.folder == "Sent",
                    ),
                )
                return detected, state.history_scan_status, state.history_last_error

        detected, status, last_error = self._run_async(scenario())
        self.assertEqual(detected, 0)
        self.assertEqual(status, "sent_recent_discovery_failed")
        self.assertIn("boom", last_error or "")

    def test_recent_history_does_not_process_legacy_sent_targeted_state(self) -> None:
        async def scenario() -> tuple[int, str, str | None, bool, str, int]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Legacy", email="legacy@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="legacy@example.edu",
                    folder_role="sent",
                    folder="Sent",
                    historical_scan_status=ImapProfessorHistoricalScanStatus.PENDING.value,
                    history_strategy_version="legacy",
                    last_error="keep me",
                    historical_scan_started_at=None,
                )
                session.add(state)
                await session.commit()
                identity_id = identity.id
                state_id = state.id

            with (
                patch("app.services.task_runtime.get_cached_or_discover_sent_folder", new=AsyncMock(return_value="Sent")),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_recent_mailbox_message_headers_since",
                    new=AsyncMock(
                        return_value=ImapHistoryHeaderFetchResult(
                            messages=[],
                            command_count=1,
                            exhausted=False,
                        ),
                    ),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(side_effect=AssertionError("legacy sent targeted must not run")),
                ) as legacy_fetch_mock,
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.get(ImapProfessorSyncState, state_id)
                return (
                    detected,
                    state.historical_scan_status,
                    state.last_error,
                    state.historical_scan_started_at is None,
                    state.history_strategy_version,
                    legacy_fetch_mock.await_count,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (0, ImapProfessorHistoricalScanStatus.PENDING.value, "keep me", True, "legacy", 0),
        )

    def test_inbox_reply_detection_is_not_blocked_by_same_message_id_in_other_scope(self) -> None:
        async def scenario() -> tuple[int, str, bool, int]:
            identity_id, _, task_id = await self._create_reply_task(status=EmailTaskStatus.SENT.value)
            async with self.session_factory() as session:
                other_identity = self._build_identity()
                other_identity.email_address = "other-student@example.com"
                other_identity.smtp_username = "other-student@example.com"
                other_identity.imap_username = "other-student@example.com"
                other_llm = LLMProfile(
                    name="其他模型",
                    provider="openai",
                    api_key="key",
                    model_name="gpt-test",
                )
                other_professor = Professor(name="Other Professor", email="other-prof@example.edu")
                session.add_all([other_identity, other_llm, other_professor])
                await session.flush()
                session.add(
                    EmailLog(
                        identity_id=other_identity.id,
                        llm_profile_id=other_llm.id,
                        professor_id=other_professor.id,
                        direction=EmailDirection.RECEIVED.value,
                        subject="other reply",
                        content="other content",
                        rfc_message_id="<shared-reply@example.edu>",
                        normalized_message_id="<shared-reply@example.edu>",
                    ),
                )
                await session.commit()

            detected = await process_imap_fetched_messages(
                self.session_factory,
                identity_id,
                [self._build_fetched_message(message_id="<shared-reply@example.edu>")],
            )

            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_id)
                logs = list(
                    (
                        await session.execute(
                            select(EmailLog).where(
                                EmailLog.rfc_message_id == "<shared-reply@example.edu>",
                            ),
                        )
                    ).scalars(),
                )
                return detected, task.status, task.is_replied, len(logs)

        self.assertEqual(
            self._run_async(scenario()),
            (1, EmailTaskStatus.REPLY_DETECTED.value, True, 2),
        )

    def test_history_scan_matches_inbox_headers_locally_and_fetches_only_matched_bodies(self) -> None:
        async def scenario() -> tuple[int, list[int], int | None, int | None, int, int, str, str]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                known = Professor(name="Known", email="known@example.edu")
                ignored = Professor(name="Ignored", email="ignored@example.edu")
                session.add_all([identity, known, ignored])
                await session.flush()
                state = ImapMailboxSyncState(
                    identity_id=identity.id,
                    folder_role="inbox",
                    folder="INBOX",
                    history_high_water_uid=100,
                    history_next_before_uid=101,
                )
                session.add(state)
                await session.commit()
                identity_id = identity.id
                state_id = state.id

            headers = [
                self._build_fetched_message(
                    uid=98,
                    uidvalidity=777,
                    message_id="<known-history@example.edu>",
                    from_email="known@example.edu",
                    content="",
                ),
                self._build_fetched_message(
                    uid=99,
                    uidvalidity=777,
                    message_id="<stranger-history@example.edu>",
                    from_email="stranger@example.edu",
                    content="",
                ),
            ]
            full_message = self._build_fetched_message(
                uid=98,
                uidvalidity=777,
                message_id="<known-history@example.edu>",
                from_email="known@example.edu",
                content="matched body",
            )
            fetched_uids: list[int] = []

            async def fake_body_fetch(_identity, _folder, uids: list[int]):
                fetched_uids.extend(uids)
                return [full_message]

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_history_mailbox_message_headers_before_uid",
                    new=AsyncMock(
                        return_value=ImapMailboxHistoryHeaderFetchResult(
                            messages=headers,
                            command_count=1,
                            uidvalidity=777,
                            high_water_uid=100,
                            next_before_uid=91,
                            scanned_count=10,
                        ),
                    ),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(side_effect=fake_body_fetch),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(side_effect=AssertionError("per-professor search should not run")),
                ),
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.get(ImapMailboxSyncState, state_id)
                log = await session.scalar(
                    select(EmailLog).where(EmailLog.rfc_message_id == "<known-history@example.edu>"),
                )
                return (
                    detected,
                    fetched_uids,
                    state.history_high_water_uid,
                    state.history_next_before_uid,
                    state.history_scanned_count,
                    state.history_matched_count,
                    state.history_scan_status,
                    log.content,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                1,
                [98],
                100,
                91,
                10,
                1,
                ImapMailboxHistoricalScanStatus.PENDING.value,
                "matched body",
            ),
        )

    def test_history_scan_matches_sent_headers_by_recipient_without_per_professor_search(self) -> None:
        async def scenario() -> tuple[int, list[int], str, str]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Sent Known", email="sent-known@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                state = ImapMailboxSyncState(
                    identity_id=identity.id,
                    folder_role="sent",
                    folder="Sent",
                    history_high_water_uid=40,
                    history_next_before_uid=41,
                )
                session.add(state)
                await session.commit()
                identity_id = identity.id
                state_id = state.id

            await self._mark_mailbox_history_completed(identity_id)

            header = self._build_fetched_message(
                uid=37,
                uidvalidity=888,
                message_id="<sent-local-match@example.com>",
                from_email="student@example.com",
                to_emails=["other@example.edu"],
                cc_emails=["sent-known@example.edu"],
                content="",
            )
            full_message = self._build_fetched_message(
                uid=37,
                uidvalidity=888,
                message_id="<sent-local-match@example.com>",
                from_email="student@example.com",
                to_emails=["other@example.edu"],
                cc_emails=["sent-known@example.edu"],
                content="sent matched body",
            )
            fetched_uids: list[int] = []

            async def fake_body_fetch(_identity, _folder, uids: list[int]):
                fetched_uids.extend(uids)
                return [full_message]

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.discover_sent_folder",
                    new=AsyncMock(return_value="Sent"),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_history_mailbox_message_headers_before_uid",
                    new=AsyncMock(
                        return_value=ImapMailboxHistoryHeaderFetchResult(
                            messages=[header],
                            command_count=1,
                            uidvalidity=888,
                            high_water_uid=40,
                            next_before_uid=0,
                            scanned_count=40,
                        ),
                    ),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(side_effect=fake_body_fetch),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(side_effect=AssertionError("per-professor search should not run")),
                ),
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.get(ImapMailboxSyncState, state_id)
                log = await session.scalar(select(EmailLog))
                return detected, fetched_uids, state.history_scan_status, log.content

        self.assertEqual(
            self._run_async(scenario()),
            (
                1,
                [37],
                ImapMailboxHistoricalScanStatus.COMPLETED.value,
                "sent matched body",
            ),
        )

    def test_history_scan_empty_folder_page_completes_without_body_fetch(self) -> None:
        async def scenario() -> tuple[int, int, str, int, int | None]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                session.add(identity)
                await session.flush()
                state = ImapMailboxSyncState(
                    identity_id=identity.id,
                    folder_role="inbox",
                    folder="INBOX",
                    history_high_water_uid=10,
                    history_next_before_uid=11,
                )
                session.add(state)
                await session.commit()
                identity_id = identity.id
                state_id = state.id

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_history_mailbox_message_headers_before_uid",
                    new=AsyncMock(
                        return_value=ImapMailboxHistoryHeaderFetchResult(
                            messages=[],
                            command_count=1,
                            uidvalidity=777,
                            high_water_uid=10,
                            next_before_uid=0,
                            scanned_count=10,
                        ),
                    ),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(return_value=[]),
                ) as body_fetch_mock,
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.get(ImapMailboxSyncState, state_id)
                return (
                    detected,
                    body_fetch_mock.await_count,
                    state.history_scan_status,
                    state.history_scanned_count,
                    state.history_next_before_uid,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                0,
                0,
                ImapMailboxHistoricalScanStatus.COMPLETED.value,
                10,
                0,
            ),
        )

    def test_history_scan_resets_state_when_uidvalidity_changes(self) -> None:
        async def scenario() -> tuple[int, list[dict[str, object]], int | None, int | None, int | None, int, int, str]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                state = ImapMailboxSyncState(
                    identity_id=identity.id,
                    folder_role="inbox",
                    folder="INBOX",
                    uidvalidity=111,
                    last_seen_uid=1000,
                    history_high_water_uid=1000,
                    history_next_before_uid=801,
                    history_scanned_count=200,
                    history_matched_count=5,
                )
                session.add(state)
                await session.commit()
                identity_id = identity.id
                state_id = state.id

            header = self._build_fetched_message(
                uid=49,
                uidvalidity=222,
                message_id="<uidvalidity-history@example.edu>",
                from_email="known@example.edu",
                content="",
            )
            full_message = self._build_fetched_message(
                uid=49,
                uidvalidity=222,
                message_id="<uidvalidity-history@example.edu>",
                from_email="known@example.edu",
                content="new uid space body",
            )
            header_calls: list[dict[str, object]] = []

            async def fake_header_fetch(_identity, _folder, **kwargs):
                header_calls.append(dict(kwargs))
                return ImapMailboxHistoryHeaderFetchResult(
                    messages=[header],
                    command_count=1,
                    uidvalidity=222,
                    high_water_uid=50,
                    next_before_uid=41,
                    scanned_count=10,
                    uidvalidity_changed=True,
                )

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_history_mailbox_message_headers_before_uid",
                    new=AsyncMock(side_effect=fake_header_fetch),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(return_value=[full_message]),
                ),
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.get(ImapMailboxSyncState, state_id)
                return (
                    detected,
                    header_calls,
                    state.uidvalidity,
                    state.last_seen_uid,
                    state.history_high_water_uid,
                    state.history_scanned_count,
                    state.history_matched_count,
                    state.history_scan_status,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                1,
                [
                    {
                        "before_uid": 801,
                        "limit": 200,
                        "max_fetch_batches": 1,
                        "expected_uidvalidity": 111,
                    },
                ],
                222,
                50,
                50,
                10,
                1,
                ImapMailboxHistoricalScanStatus.PENDING.value,
            ),
        )

    def test_history_scan_skips_uninitialized_mailbox_when_budget_cannot_cover_high_water_and_page(self) -> None:
        async def scenario() -> tuple[int, int, str, int | None]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                session.add(identity)
                await session.flush()
                state = ImapMailboxSyncState(
                    identity_id=identity.id,
                    folder_role="inbox",
                    folder="INBOX",
                )
                session.add(state)
                await session.commit()
                identity_id = identity.id
                state_id = state.id

            with (
                patch("app.services.task_runtime.get_settings") as settings_mock,
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_history_mailbox_message_headers_before_uid",
                    new=AsyncMock(side_effect=AssertionError("budget should skip IMAP fetch")),
                ) as header_fetch_mock,
            ):
                settings_mock.return_value.imap_history_batch_size = 200
                settings_mock.return_value.imap_history_command_budget_per_minute = 1
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.get(ImapMailboxSyncState, state_id)
                return detected, header_fetch_mock.await_count, state.history_scan_status, state.history_next_before_uid

        self.assertEqual(
            self._run_async(scenario()),
            (0, 0, ImapMailboxHistoricalScanStatus.PENDING.value, None),
        )

    def test_history_scan_without_imap_config_skips_state_creation(self) -> None:
        async def scenario() -> tuple[int, int, int, int]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                identity.imap_host = " "
                identity.imap_port = None
                identity.imap_username = ""
                identity.imap_password = " "
                session.add(identity)
                await session.commit()
                identity_id = identity.id

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.discover_sent_folder",
                    new=AsyncMock(side_effect=AssertionError("sent discovery should not run")),
                ) as discover_mock,
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_history_mailbox_message_headers_before_uid",
                    new=AsyncMock(side_effect=AssertionError("history fetch should not run")),
                ) as header_fetch_mock,
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state_count = await session.scalar(select(func.count(ImapMailboxSyncState.id)))
                return detected, discover_mock.await_count, header_fetch_mock.await_count, state_count

        self.assertEqual(self._run_async(scenario()), (0, 0, 0, 0))

    def test_history_progress_log_counts_only_active_mailbox_folders(self) -> None:
        async def scenario() -> str:
            async with self.session_factory() as session:
                identity = self._build_identity()
                session.add(identity)
                await session.flush()
                session.add_all(
                    [
                        ImapMailboxSyncState(
                            identity_id=identity.id,
                            folder_role="inbox",
                            folder="INBOX",
                            history_scan_status=ImapMailboxHistoricalScanStatus.COMPLETED.value,
                            history_scanned_count=10,
                            history_matched_count=2,
                        ),
                        ImapMailboxSyncState(
                            identity_id=identity.id,
                            folder_role="sent",
                            folder="Sent",
                            history_scan_status=ImapMailboxHistoricalScanStatus.PENDING.value,
                            history_scanned_count=100,
                            history_matched_count=20,
                        ),
                        ImapMailboxSyncState(
                            identity_id=identity.id,
                            folder_role="sent",
                            folder="Sent Items",
                            history_scan_status=ImapMailboxHistoricalScanStatus.PENDING.value,
                            history_scanned_count=5,
                            history_matched_count=1,
                        ),
                    ],
                )
                await session.commit()
                identity_id = identity.id

            with self.assertLogs("app.services.task_runtime", level="INFO") as logs:
                await log_imap_history_progress(
                    self.session_factory,
                    identity_id,
                    folders=[("inbox", "INBOX"), ("sent", "Sent Items")],
                )
            return logs.output[0]

        line = self._run_async(scenario())
        self.assertIn("mailbox_pending=1 mailbox_completed=1", line)
        self.assertIn("scanned=15 matched=3", line)
        self.assertNotIn("mailbox_pending=2", line)

    def test_history_scan_uses_state_folder_and_sent_ingestion_path(self) -> None:
        async def scenario() -> tuple[int, tuple[str, str, str], tuple[str, int | None]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="known@example.edu",
                    folder_role="sent",
                    folder="Sent",
                )
                session.add(state)
                await session.commit()
                identity_id = identity.id
                state_id = state.id

            await self._mark_mailbox_history_completed(identity_id)
            await self._mark_mailbox_history_completed(identity_id, folder_role="sent", folder="Sent")
            await self._mark_targeted_history_baseline_ready(identity_id)

            message = self._build_fetched_message(
                uid=41,
                message_id="<sent-history@example.com>",
                from_email="student@example.com",
                to_emails=["known@example.edu"],
            )
            with (
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(return_value=ImapHistoryHeaderFetchResult([message], 1)),
                ) as mocked,
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(return_value=[message]),
                ),
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            fetched_identity, fetched_folder, fetched_email = mocked.await_args.args
            fetched_folder_role = mocked.await_args.kwargs["folder_role"]
            async with self.session_factory() as session:
                state = await session.get(ImapProfessorSyncState, state_id)
                log = await session.scalar(select(EmailLog))
                return (
                    detected,
                    (fetched_folder, fetched_email, fetched_folder_role),
                    (log.folder_role, state.last_scanned_uid),
                )

        self.assertEqual(
            self._run_async(scenario()),
            (1, ("Sent", "known@example.edu", "sent"), ("sent", 41)),
        )

    def test_history_scan_skips_body_fetch_for_existing_message_id(self) -> None:
        async def scenario() -> tuple[int, int, str]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                llm = self._build_llm()
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, llm, professor])
                await session.flush()
                session.add(
                    EmailLog(
                        identity_id=identity.id,
                        llm_profile_id=llm.id,
                        professor_id=professor.id,
                        direction=EmailDirection.RECEIVED.value,
                        subject="existing",
                        content="existing body",
                        rfc_message_id="<existing-history@example.edu>",
                        normalized_message_id="<existing-history@example.edu>",
                        folder_role="inbox",
                        folder="INBOX",
                        uidvalidity=777,
                        imap_uid=90,
                    ),
                )
                session.add(
                    ImapProfessorSyncState(
                        identity_id=identity.id,
                        professor_id=professor.id,
                        professor_email="known@example.edu",
                        folder_role="inbox",
                        folder="INBOX",
                    ),
                )
                await session.commit()
                identity_id = identity.id

            await self._mark_mailbox_history_completed(identity_id)
            await self._mark_targeted_history_baseline_ready(identity_id)

            header = self._build_fetched_message(
                uid=90,
                uidvalidity=777,
                message_id="<existing-history@example.edu>",
                from_email="known@example.edu",
                content="",
            )

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(return_value=ImapHistoryHeaderFetchResult([header], 1)),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(return_value=[]),
                ) as body_fetch_mock,
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.scalar(select(ImapProfessorSyncState))
                return detected, body_fetch_mock.await_count, state.historical_scan_status

        self.assertEqual(
            self._run_async(scenario()),
            (0, 0, ImapProfessorHistoricalScanStatus.COMPLETED.value),
        )

    def test_history_scan_advances_existing_headers_without_body_budget(self) -> None:
        async def scenario() -> tuple[int, int, int | None, str]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                llm = self._build_llm()
                professor = Professor(name="Known", email="known@example.edu")
                session.add_all([identity, llm, professor])
                await session.flush()
                session.add(
                    EmailLog(
                        identity_id=identity.id,
                        llm_profile_id=llm.id,
                        professor_id=professor.id,
                        direction=EmailDirection.RECEIVED.value,
                        subject="existing",
                        content="existing body",
                        rfc_message_id="<existing-no-body-budget@example.edu>",
                        normalized_message_id="<existing-no-body-budget@example.edu>",
                        folder_role="inbox",
                        folder="INBOX",
                        uidvalidity=777,
                        imap_uid=94,
                    ),
                )
                state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="known@example.edu",
                    folder_role="inbox",
                    folder="INBOX",
                )
                session.add(state)
                await session.commit()
                identity_id = identity.id
                state_id = state.id

            await self._mark_mailbox_history_completed(identity_id)
            await self._mark_targeted_history_baseline_ready(identity_id)

            header = self._build_fetched_message(
                uid=94,
                uidvalidity=777,
                message_id="<existing-no-body-budget@example.edu>",
                from_email="known@example.edu",
                content="",
            )

            with (
                patch("app.services.task_runtime.get_settings") as settings_mock,
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(return_value=ImapHistoryHeaderFetchResult([header], 1)),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(return_value=[]),
                ) as body_fetch_mock,
            ):
                settings_mock.return_value.imap_history_batch_size = 50
                settings_mock.return_value.imap_history_command_budget_per_minute = 1
                settings_mock.return_value.imap_fetch_batch_size = 20
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.get(ImapProfessorSyncState, state_id)
                return detected, body_fetch_mock.await_count, state.last_scanned_uid, state.historical_scan_status

        self.assertEqual(
            self._run_async(scenario()),
            (0, 0, 94, ImapProfessorHistoricalScanStatus.COMPLETED.value),
        )

    def test_history_scan_fetches_body_for_new_header_before_completing_state(self) -> None:
        async def scenario() -> tuple[int, int, str, str]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Incoming", email="incoming@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                session.add(
                    ImapProfessorSyncState(
                        identity_id=identity.id,
                        professor_id=professor.id,
                        professor_email="incoming@example.edu",
                        folder_role="inbox",
                        folder="INBOX",
                    ),
                )
                await session.commit()
                identity_id = identity.id

            await self._mark_mailbox_history_completed(identity_id)
            await self._mark_targeted_history_baseline_ready(identity_id)

            header = self._build_fetched_message(
                uid=91,
                uidvalidity=777,
                message_id="<new-history@example.edu>",
                from_email="incoming@example.edu",
                content="",
            )
            full_message = self._build_fetched_message(
                uid=91,
                uidvalidity=777,
                message_id="<new-history@example.edu>",
                from_email="incoming@example.edu",
                content="new body",
            )

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(return_value=ImapHistoryHeaderFetchResult([header], 1)),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(return_value=[full_message]),
                ) as body_fetch_mock,
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                log = await session.scalar(
                    select(EmailLog).where(EmailLog.rfc_message_id == "<new-history@example.edu>"),
                )
                state = await session.scalar(select(ImapProfessorSyncState))
                return detected, body_fetch_mock.await_count, log.content, state.historical_scan_status

        self.assertEqual(
            self._run_async(scenario()),
            (1, 1, "new body", ImapProfessorHistoricalScanStatus.COMPLETED.value),
        )

    def test_history_scan_does_not_complete_when_required_body_fetch_is_missing(self) -> None:
        async def scenario() -> tuple[int, str, str | None]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Incoming", email="incoming@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="incoming@example.edu",
                    folder_role="inbox",
                    folder="INBOX",
                )
                session.add(state)
                await session.commit()
                identity_id = identity.id
                state_id = state.id

            await self._mark_mailbox_history_completed(identity_id)
            await self._mark_targeted_history_baseline_ready(identity_id)

            header = self._build_fetched_message(
                uid=92,
                uidvalidity=777,
                message_id="<missing-body@example.edu>",
                from_email="incoming@example.edu",
                content="",
            )

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(return_value=ImapHistoryHeaderFetchResult([header], 1)),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(return_value=[]),
                ),
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.get(ImapProfessorSyncState, state_id)
                return detected, state.historical_scan_status, state.last_error

        detected, status, last_error = self._run_async(scenario())
        self.assertEqual(detected, 0)
        self.assertEqual(status, ImapProfessorHistoricalScanStatus.FAILED.value)
        self.assertIn("body fetch incomplete", last_error or "")

    def test_history_scan_stops_before_body_fetch_when_command_budget_is_exhausted(self) -> None:
        async def scenario() -> tuple[int, int, str]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Incoming", email="incoming@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="incoming@example.edu",
                    folder_role="inbox",
                    folder="INBOX",
                )
                session.add(state)
                await session.commit()
                identity_id = identity.id
                state_id = state.id

            header = self._build_fetched_message(
                uid=93,
                uidvalidity=777,
                message_id="<budget-new@example.edu>",
                from_email="incoming@example.edu",
                content="",
            )

            with (
                patch("app.services.task_runtime.get_settings") as settings_mock,
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(return_value=ImapHistoryHeaderFetchResult([header], 1)),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(return_value=[]),
                ) as body_fetch_mock,
            ):
                settings_mock.return_value.imap_history_batch_size = 50
                settings_mock.return_value.imap_history_command_budget_per_minute = 1
                settings_mock.return_value.imap_fetch_batch_size = 20
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.get(ImapProfessorSyncState, state_id)
                return detected, body_fetch_mock.await_count, state.historical_scan_status

        self.assertEqual(
            self._run_async(scenario()),
            (0, 0, ImapProfessorHistoricalScanStatus.PENDING.value),
        )

    def test_history_scan_counts_header_fetch_batches_against_command_budget(self) -> None:
        async def scenario() -> tuple[int, int, str]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Budget Header", email="budget-header@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="budget-header@example.edu",
                    folder_role="inbox",
                    folder="INBOX",
                )
                session.add(state)
                await session.commit()
                identity_id = identity.id
                state_id = state.id

            await self._mark_mailbox_history_completed(identity_id)
            await self._mark_targeted_history_baseline_ready(identity_id)

            headers = [
                self._build_fetched_message(
                    uid=301,
                    uidvalidity=777,
                    message_id="<header-budget-301@example.edu>",
                    from_email="budget-header@example.edu",
                    content="",
                )
            ]
            with (
                patch("app.services.task_runtime.get_settings") as settings_mock,
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(return_value=ImapHistoryHeaderFetchResult(headers, 2)),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(return_value=[]),
                ) as body_fetch_mock,
            ):
                settings_mock.return_value.imap_history_batch_size = 50
                settings_mock.return_value.imap_history_command_budget_per_minute = 2
                settings_mock.return_value.imap_fetch_batch_size = 1
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.get(ImapProfessorSyncState, state_id)
                return detected, body_fetch_mock.await_count, state.historical_scan_status

        self.assertEqual(
            self._run_async(scenario()),
            (0, 0, ImapProfessorHistoricalScanStatus.PENDING.value),
        )

    def test_history_scan_stops_current_batch_when_provider_throttles(self) -> None:
        async def scenario() -> tuple[int, int, str, str]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                first = Professor(name="First", email="first@example.edu")
                second = Professor(name="Second", email="second@example.edu")
                session.add_all([identity, first, second])
                await session.flush()
                first_state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=first.id,
                    professor_email="first@example.edu",
                    folder_role="inbox",
                    folder="INBOX",
                )
                second_state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=second.id,
                    professor_email="second@example.edu",
                    folder_role="inbox",
                    folder="INBOX",
                )
                session.add_all([first_state, second_state])
                await session.commit()
                identity_id = identity.id
                first_state_id = first_state.id
                second_state_id = second_state.id

            await self._mark_mailbox_history_completed(identity_id)
            await self._mark_targeted_history_baseline_ready(identity_id)

            with (
                patch("app.services.task_runtime.get_settings") as settings_mock,
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(side_effect=RuntimeError("Too many requests")),
                ) as header_fetch_mock,
            ):
                settings_mock.return_value.imap_history_batch_size = 50
                settings_mock.return_value.imap_history_command_budget_per_minute = 20
                settings_mock.return_value.imap_throttle_backoff_seconds = 86400
                settings_mock.return_value.imap_fetch_batch_size = 20
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                first_state = await session.get(ImapProfessorSyncState, first_state_id)
                second_state = await session.get(ImapProfessorSyncState, second_state_id)
                return (
                    detected,
                    header_fetch_mock.await_count,
                    first_state.historical_scan_status,
                    second_state.historical_scan_status,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                0,
                1,
                ImapProfessorHistoricalScanStatus.FAILED.value,
                ImapProfessorHistoricalScanStatus.PENDING.value,
            ),
        )

    def test_history_scan_does_not_start_body_fetch_when_budget_cannot_cover_missing_uids(self) -> None:
        async def scenario() -> tuple[int, int, str]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Budget", email="budget@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="budget@example.edu",
                    folder_role="inbox",
                    folder="INBOX",
                )
                session.add(state)
                await session.commit()
                identity_id = identity.id
                state_id = state.id

            await self._mark_mailbox_history_completed(identity_id)
            await self._mark_targeted_history_baseline_ready(identity_id)

            headers = [
                self._build_fetched_message(
                    uid=uid,
                    uidvalidity=777,
                    message_id=f"<budget-{uid}@example.edu>",
                    from_email="budget@example.edu",
                    content="",
                )
                for uid in (201, 202)
            ]
            with (
                patch("app.services.task_runtime.get_settings") as settings_mock,
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(return_value=ImapHistoryHeaderFetchResult(headers, 1)),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(return_value=[]),
                ) as body_fetch_mock,
            ):
                settings_mock.return_value.imap_history_batch_size = 50
                settings_mock.return_value.imap_history_command_budget_per_minute = 2
                settings_mock.return_value.imap_fetch_batch_size = 1
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.get(ImapProfessorSyncState, state_id)
                return detected, body_fetch_mock.await_count, state.historical_scan_status

        self.assertEqual(
            self._run_async(scenario()),
            (0, 0, ImapProfessorHistoricalScanStatus.PENDING.value),
        )

    def test_history_scan_fetches_budgeted_body_subset_and_advances_cursor(self) -> None:
        async def scenario() -> tuple[int, list[int], int | None, str, list[str]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(name="Budget", email="budget@example.edu")
                session.add_all([identity, professor])
                await session.flush()
                state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="budget@example.edu",
                    folder_role="inbox",
                    folder="INBOX",
                )
                session.add(state)
                await session.commit()
                identity_id = identity.id
                state_id = state.id

            await self._mark_mailbox_history_completed(identity_id)
            await self._mark_targeted_history_baseline_ready(identity_id)

            headers = [
                self._build_fetched_message(
                    uid=uid,
                    uidvalidity=777,
                    message_id=f"<budget-subset-{uid}@example.edu>",
                    from_email="budget@example.edu",
                    content="",
                )
                for uid in (301, 302, 303)
            ]
            fetched_uids: list[int] = []

            async def fake_body_fetch(_identity, _folder, uids: list[int]):
                fetched_uids.extend(uids)
                return [
                    self._build_fetched_message(
                        uid=uid,
                        uidvalidity=777,
                        message_id=f"<budget-subset-{uid}@example.edu>",
                        from_email="budget@example.edu",
                        content=f"body {uid}",
                    )
                    for uid in uids
                ]

            with (
                patch("app.services.task_runtime.get_settings") as settings_mock,
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(return_value=ImapHistoryHeaderFetchResult(headers, 1, exhausted=True)),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(side_effect=fake_body_fetch),
                ),
            ):
                settings_mock.return_value.imap_history_batch_size = 50
                settings_mock.return_value.imap_history_command_budget_per_minute = 8
                settings_mock.return_value.imap_fetch_batch_size = 20
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.get(ImapProfessorSyncState, state_id)
                logs = list(
                    (
                        await session.execute(
                            select(EmailLog).order_by(EmailLog.imap_uid),
                        )
                    ).scalars(),
                )
                return (
                    detected,
                    fetched_uids,
                    state.last_scanned_uid,
                    state.historical_scan_status,
                    [log.content for log in logs],
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                1,
                [301],
                301,
                ImapProfessorHistoricalScanStatus.PENDING.value,
                ["body 301"],
            ),
        )

    def test_incremental_fetch_error_is_scoped_to_folder_state(self) -> None:
        async def scenario() -> tuple[str | None, str | None, int | None]:
            identity_id = await self._create_identity_with_imap()
            async with self.session_factory() as session:
                session.add_all(
                    [
                        ImapMailboxSyncState(
                            identity_id=identity_id,
                            folder_role="inbox",
                            folder="INBOX",
                            last_seen_uid=10,
                        ),
                        ImapMailboxSyncState(
                            identity_id=identity_id,
                            folder_role="sent",
                            folder="Sent",
                            last_seen_uid=20,
                        ),
                    ],
                )
                await session.commit()

            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                new=AsyncMock(side_effect=RuntimeError("sent failed")),
            ):
                await sync_identity_incremental_once(
                    self.session_factory,
                    identity_id,
                    folder_role="sent",
                    folder="Sent",
                )

            async with self.session_factory() as session:
                inbox = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "inbox",
                    ),
                )
                sent = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "sent",
                    ),
                )
                return inbox.last_error, sent.last_error, sent.last_seen_uid

        self.assertEqual(self._run_async(scenario()), (None, "sent failed", 20))

    def test_incremental_bootstraps_empty_cursor_from_history_high_water_without_fetching_old_mail(self) -> None:
        async def scenario() -> tuple[int, int, int | None, int | None, int | None, int | None]:
            identity_id = await self._create_identity_with_imap()

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_history_mailbox_message_headers_before_uid",
                    new=AsyncMock(
                        return_value=ImapMailboxHistoryHeaderFetchResult(
                            messages=[],
                            command_count=0,
                            uidvalidity=777,
                            high_water_uid=500,
                            next_before_uid=501,
                            scanned_count=0,
                            exhausted=True,
                        ),
                    ),
                ) as bootstrap_mock,
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                    new=AsyncMock(return_value=(None, [], None)),
                ) as incremental_mock,
            ):
                detected = await sync_identity_incremental_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "inbox",
                        ImapMailboxSyncState.folder == "INBOX",
                    ),
                )
                return (
                    detected,
                    bootstrap_mock.await_count + incremental_mock.await_count,
                    state.last_seen_uid,
                    state.history_high_water_uid,
                    state.history_next_before_uid,
                    state.uidvalidity,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (0, 1, 500, 500, 501, 777),
        )

    def test_incremental_bootstrap_high_water_failure_skips_full_mailbox_fetch(self) -> None:
        async def scenario() -> tuple[int, int, int | None, int | None, str | None]:
            identity_id = await self._create_identity_with_imap()

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_history_mailbox_message_headers_before_uid",
                    new=AsyncMock(
                        return_value=ImapMailboxHistoryHeaderFetchResult(
                            messages=[],
                            command_count=1,
                            uidvalidity=777,
                            high_water_uid=None,
                            next_before_uid=None,
                            scanned_count=0,
                            exhausted=True,
                        ),
                    ),
                ),
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                    new=AsyncMock(side_effect=AssertionError("incremental fetch should not run")),
                ) as incremental_mock,
            ):
                detected = await sync_identity_incremental_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "inbox",
                        ImapMailboxSyncState.folder == "INBOX",
                    ),
                )
                return (
                    detected,
                    incremental_mock.await_count,
                    state.last_seen_uid,
                    state.uidvalidity,
                    state.last_error,
                )

        detected, incremental_calls, last_seen_uid, uidvalidity, last_error = self._run_async(scenario())
        self.assertEqual((detected, incremental_calls, last_seen_uid, uidvalidity), (0, 0, None, 777))
        self.assertIsNotNone(last_error)
        self.assertIn("high-water", last_error)

    def test_incremental_sync_without_imap_config_skips_bootstrap(self) -> None:
        async def scenario() -> tuple[int, int, int, str | None]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                identity.imap_host = " "
                identity.imap_port = None
                identity.imap_username = ""
                identity.imap_password = " "
                session.add(identity)
                await session.commit()
                identity_id = identity.id

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_history_mailbox_message_headers_before_uid",
                    new=AsyncMock(side_effect=AssertionError("history bootstrap should not run")),
                ) as bootstrap_mock,
                patch(
                    "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                    new=AsyncMock(side_effect=AssertionError("incremental fetch should not run")),
                ) as incremental_mock,
            ):
                detected = await sync_identity_incremental_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "inbox",
                        ImapMailboxSyncState.folder == "INBOX",
                    ),
                )
                return detected, bootstrap_mock.await_count, incremental_mock.await_count, state.last_error

        self.assertEqual(self._run_async(scenario()), (0, 0, 0, None))

    def test_incremental_provider_throttle_pauses_account(self) -> None:
        async def scenario() -> tuple[int, bool, str | None]:
            identity_id = await self._create_identity_with_imap()
            await self._mark_mailbox_history_completed(identity_id)
            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                new=AsyncMock(side_effect=RuntimeError("Too many requests")),
            ):
                detected = await sync_identity_incremental_once(self.session_factory, identity_id)

            paused = await is_imap_incremental_paused(self.session_factory, identity_id)
            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "inbox",
                        ImapMailboxSyncState.folder == "INBOX",
                    ),
                )
                return detected, paused, state.throttle_reason

        detected, paused, reason = self._run_async(scenario())
        self.assertEqual(detected, 0)
        self.assertTrue(paused)
        self.assertTrue((reason or "").startswith("account:"))

    def test_sent_incremental_select_failure_clears_sent_folder_cache(self) -> None:
        async def scenario() -> tuple[int, str | None, str | None]:
            identity_id = await self._create_identity_with_imap()
            async with self.session_factory() as session:
                session.add(
                    ImapMailboxSyncState(
                        identity_id=identity_id,
                        folder_role="sent",
                        folder="Sent",
                        discovered_sent_folder="Sent Items",
                        sent_folder_discovered_at=datetime(2026, 6, 30, tzinfo=UTC),
                    ),
                )
                await session.commit()

            await self._mark_mailbox_history_completed(identity_id, folder_role="sent", folder="Sent Items")

            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_mailbox_messages_with_uidvalidity",
                new=AsyncMock(side_effect=RuntimeError("IMAP 选择邮箱文件夹失败: no such mailbox")),
            ):
                detected = await sync_identity_incremental_once(
                    self.session_factory,
                    identity_id,
                    folder_role="sent",
                    folder="Sent Items",
                )

            async with self.session_factory() as session:
                cache_state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "sent",
                        ImapMailboxSyncState.folder == "Sent",
                    ),
                )
                actual_state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "sent",
                        ImapMailboxSyncState.folder == "Sent Items",
                    ),
                )
                return detected, cache_state.discovered_sent_folder, actual_state.last_error

        detected, cached_folder, last_error = self._run_async(scenario())
        self.assertEqual(detected, 0)
        self.assertIsNone(cached_folder)
        self.assertIn("no such mailbox", last_error or "")

    def test_identity_imap_once_discovers_sent_and_runs_inbox_and_sent_incremental_for_identity(self) -> None:
        async def scenario() -> tuple[int, tuple[object, ...], dict[str, object], tuple[dict[str, object], ...]]:
            identity_id = await self._create_identity_with_imap()
            with (
                patch(
                    "app.services.task_runtime.mail_runtime.discover_sent_folder",
                    new=AsyncMock(return_value="Sent"),
                ),
                patch(
                    "app.services.task_runtime.ensure_professor_scan_states_if_needed",
                    new=AsyncMock(return_value=0),
                ) as ensure_mock,
                patch(
                    "app.services.task_runtime.sync_identity_history_once",
                    new=AsyncMock(return_value=1),
                ),
                patch(
                    "app.services.task_runtime.sync_identity_incremental_once",
                    new=AsyncMock(side_effect=[2, 3]),
                ) as incremental_mock,
            ):
                detected = await _sync_identity_imap_once_unlocked(self.session_factory, identity_id)

            return detected, ensure_mock.await_args.args, ensure_mock.await_args.kwargs, tuple(
                call.kwargs for call in incremental_mock.await_args_list
            )

        self.assertEqual(
            self._run_async(scenario()),
            (
                6,
                (self.session_factory,),
                {"identity_id": 1, "sent_folder": "Sent"},
                (
                    {"folder_role": "inbox", "folder": "INBOX"},
                    {"folder_role": "sent", "folder": "Sent"},
                ),
            ),
        )

    def test_identity_imap_once_reuses_cached_sent_folder(self) -> None:
        async def scenario() -> tuple[int, int, tuple[dict[str, object], ...]]:
            identity_id = await self._create_identity_with_imap()
            async with self.session_factory() as session:
                session.add(
                    ImapMailboxSyncState(
                        identity_id=identity_id,
                        folder_role="sent",
                        folder="Sent",
                        discovered_sent_folder="Sent",
                    ),
                )
                await session.commit()

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.discover_sent_folder",
                    new=AsyncMock(return_value="Sent"),
                ) as discover_mock,
                patch(
                    "app.services.task_runtime.ensure_professor_scan_states_if_needed",
                    new=AsyncMock(return_value=0),
                ),
                patch(
                    "app.services.task_runtime.sync_identity_history_once",
                    new=AsyncMock(return_value=0),
                ),
                patch(
                    "app.services.task_runtime.sync_identity_incremental_once",
                    new=AsyncMock(side_effect=[1, 2]),
                ) as incremental_mock,
            ):
                detected = await _sync_identity_imap_once_unlocked(self.session_factory, identity_id)

            return detected, discover_mock.await_count, tuple(
                call.kwargs for call in incremental_mock.await_args_list
            )

        self.assertEqual(
            self._run_async(scenario()),
            (
                3,
                0,
                (
                    {"folder_role": "inbox", "folder": "INBOX"},
                    {"folder_role": "sent", "folder": "Sent"},
                ),
            ),
        )

    def test_identity_imap_once_reuses_cached_non_default_sent_folder(self) -> None:
        async def scenario() -> tuple[int, int, tuple[dict[str, object], ...]]:
            identity_id = await self._create_identity_with_imap()
            async with self.session_factory() as session:
                session.add(
                    ImapMailboxSyncState(
                        identity_id=identity_id,
                        folder_role="sent",
                        folder="Sent",
                        discovered_sent_folder="Sent Items",
                    ),
                )
                await session.commit()

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.discover_sent_folder",
                    new=AsyncMock(return_value="Sent Items"),
                ) as discover_mock,
                patch(
                    "app.services.task_runtime.ensure_professor_scan_states_if_needed",
                    new=AsyncMock(return_value=0),
                ),
                patch(
                    "app.services.task_runtime.sync_identity_history_once",
                    new=AsyncMock(return_value=0),
                ),
                patch(
                    "app.services.task_runtime.sync_identity_incremental_once",
                    new=AsyncMock(side_effect=[1, 2]),
                ) as incremental_mock,
            ):
                detected = await _sync_identity_imap_once_unlocked(self.session_factory, identity_id)

            return detected, discover_mock.await_count, tuple(
                call.kwargs for call in incremental_mock.await_args_list
            )

        self.assertEqual(
            self._run_async(scenario()),
            (
                3,
                0,
                (
                    {"folder_role": "inbox", "folder": "INBOX"},
                    {"folder_role": "sent", "folder": "Sent Items"},
                ),
            ),
        )

    def test_sent_folder_discovery_cache_can_be_cleared_for_identity_config_change(self) -> None:
        async def scenario() -> tuple[str | None, datetime | None, str | None]:
            identity_id = await self._create_identity_with_imap()
            async with self.session_factory() as session:
                session.add(
                    ImapMailboxSyncState(
                        identity_id=identity_id,
                        folder_role="sent",
                        folder="Sent",
                        discovered_sent_folder="Sent Items",
                        sent_folder_discovered_at=datetime(2026, 6, 30, tzinfo=UTC),
                        sent_folder_discovery_failed_at=datetime(2026, 6, 30, tzinfo=UTC),
                        sent_folder_discovery_error="old failure",
                    ),
                )
                await session.commit()

            await clear_identity_sent_folder_discovery_cache(self.session_factory, identity_id)

            async with self.session_factory() as session:
                state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        ImapMailboxSyncState.folder_role == "sent",
                        ImapMailboxSyncState.folder == "Sent",
                    ),
                )
                return (
                    state.discovered_sent_folder,
                    state.sent_folder_discovered_at,
                    state.sent_folder_discovery_error,
                )

        self.assertEqual(self._run_async(scenario()), (None, None, None))

    def test_identity_imap_once_respects_sent_discovery_failure_ttl(self) -> None:
        async def scenario() -> tuple[int, int]:
            identity_id = await self._create_identity_with_imap()
            async with self.session_factory() as session:
                session.add(
                    ImapMailboxSyncState(
                        identity_id=identity_id,
                        folder_role="sent",
                        folder="Sent",
                        sent_folder_discovery_failed_at=datetime(2026, 6, 30, tzinfo=UTC),
                        sent_folder_discovery_error="not found",
                    ),
                )
                await session.commit()

            with (
                patch("app.services.task_runtime.utc_now", return_value=datetime(2026, 6, 30, 0, 10, tzinfo=UTC)),
                patch(
                    "app.services.task_runtime.mail_runtime.discover_sent_folder",
                    new=AsyncMock(return_value="Sent"),
                ) as discover_mock,
                patch(
                    "app.services.task_runtime.ensure_professor_scan_states_if_needed",
                    new=AsyncMock(return_value=0),
                ),
                patch(
                    "app.services.task_runtime.sync_identity_history_once",
                    new=AsyncMock(return_value=2),
                ),
                patch(
                    "app.services.task_runtime.sync_identity_incremental_once",
                    new=AsyncMock(return_value=3),
                ) as incremental_mock,
            ):
                detected = await _sync_identity_imap_once_unlocked(self.session_factory, identity_id)

            return detected, discover_mock.await_count + incremental_mock.await_count

        self.assertEqual(self._run_async(scenario()), (5, 1))

    def test_sent_discovery_provider_throttle_pauses_account_and_skips_imap_work(self) -> None:
        async def scenario() -> tuple[int, bool, int, int]:
            identity_id = await self._create_identity_with_imap()
            with (
                patch(
                    "app.services.task_runtime.mail_runtime.discover_sent_folder",
                    new=AsyncMock(side_effect=RuntimeError("Too many requests")),
                ),
                patch(
                    "app.services.task_runtime.ensure_professor_scan_states_if_needed",
                    new=AsyncMock(return_value=0),
                ) as ensure_mock,
                patch(
                    "app.services.task_runtime.sync_identity_incremental_once",
                    new=AsyncMock(return_value=5),
                ) as incremental_mock,
                patch(
                    "app.services.task_runtime.sync_identity_history_once",
                    new=AsyncMock(return_value=7),
                ) as history_mock,
            ):
                detected = await _sync_identity_imap_once_unlocked(self.session_factory, identity_id)
            paused = await is_imap_incremental_paused(self.session_factory, identity_id)
            return (
                detected,
                paused,
                ensure_mock.await_count,
                incremental_mock.await_count + history_mock.await_count,
            )

        self.assertEqual(self._run_async(scenario()), (0, True, 0, 0))

    def test_throttle_pauses_history_but_not_incremental_until_account_backoff(self) -> None:
        async def scenario() -> tuple[int, int, int]:
            identity_id = await self._create_identity_with_imap()
            await mark_imap_throttled(
                self.session_factory,
                identity_id,
                reason="Fetch volume limit exceed",
                account_level=False,
            )

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.discover_sent_folder",
                    new=AsyncMock(return_value=None),
                ),
                patch(
                    "app.services.task_runtime.ensure_professor_scan_states_if_needed",
                    new=AsyncMock(return_value=0),
                ),
                patch(
                    "app.services.task_runtime.sync_identity_history_once",
                    new=AsyncMock(return_value=4),
                ) as history_mock,
                patch(
                    "app.services.task_runtime.sync_identity_incremental_once",
                    new=AsyncMock(return_value=5),
                ) as incremental_mock,
            ):
                detected = await _sync_identity_imap_once_unlocked(self.session_factory, identity_id)
            return detected, history_mock.await_count, incremental_mock.await_count

        self.assertEqual(self._run_async(scenario()), (5, 0, 1))

    def test_identity_imap_once_runs_incremental_before_history(self) -> None:
        async def scenario() -> tuple[str, ...]:
            identity_id = await self._create_identity_with_imap()
            calls: list[str] = []

            async def incremental_side_effect(*_args, folder_role: str, **_kwargs) -> int:
                calls.append(f"incremental:{folder_role}")
                return 1

            async def history_side_effect(*_args, **_kwargs) -> int:
                calls.append("history")
                return 1

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.discover_sent_folder",
                    new=AsyncMock(return_value="Sent"),
                ),
                patch(
                    "app.services.task_runtime.ensure_professor_scan_states_if_needed",
                    new=AsyncMock(return_value=0),
                ),
                patch(
                    "app.services.task_runtime.sync_identity_history_once",
                    new=AsyncMock(side_effect=history_side_effect),
                ),
                patch(
                    "app.services.task_runtime.sync_identity_incremental_once",
                    new=AsyncMock(side_effect=incremental_side_effect),
                ),
            ):
                await _sync_identity_imap_once_unlocked(self.session_factory, identity_id)

            return tuple(calls)

        self.assertEqual(
            self._run_async(scenario()),
            ("incremental:inbox", "incremental:sent", "history"),
        )

    def test_identity_imap_once_stops_after_inbox_marks_account_throttled(self) -> None:
        async def scenario() -> tuple[int, int, int, int]:
            identity_id = await self._create_identity_with_imap()

            async def incremental_side_effect(_session_factory, current_identity_id: int, *, folder_role: str, folder: str) -> int:
                if folder_role == "inbox":
                    await mark_imap_throttled(
                        self.session_factory,
                        current_identity_id,
                        reason="Too many requests",
                        account_level=True,
                    )
                return 0

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.discover_sent_folder",
                    new=AsyncMock(return_value="Sent"),
                ),
                patch(
                    "app.services.task_runtime.ensure_professor_scan_states_if_needed",
                    new=AsyncMock(return_value=0),
                ),
                patch(
                    "app.services.task_runtime.sync_identity_incremental_once",
                    new=AsyncMock(side_effect=incremental_side_effect),
                ) as incremental_mock,
                patch(
                    "app.services.task_runtime.sync_identity_history_once",
                    new=AsyncMock(return_value=3),
                ) as history_mock,
            ):
                detected = await _sync_identity_imap_once_unlocked(self.session_factory, identity_id)

            return detected, incremental_mock.await_count, history_mock.await_count, int(
                await is_imap_incremental_paused(self.session_factory, identity_id),
            )

        self.assertEqual(self._run_async(scenario()), (0, 1, 0, 1))

    def test_account_level_throttle_pauses_history_and_incremental(self) -> None:
        async def scenario() -> tuple[int, int, int]:
            identity_id = await self._create_identity_with_imap()
            await mark_imap_throttled(
                self.session_factory,
                identity_id,
                reason="Too many requests",
                account_level=True,
            )

            with (
                patch(
                    "app.services.task_runtime.mail_runtime.discover_sent_folder",
                    new=AsyncMock(return_value=None),
                ) as discover_mock,
                patch(
                    "app.services.task_runtime.ensure_professor_scan_states_if_needed",
                    new=AsyncMock(return_value=0),
                ),
                patch(
                    "app.services.task_runtime.sync_identity_history_once",
                    new=AsyncMock(return_value=4),
                ) as history_mock,
                patch(
                    "app.services.task_runtime.sync_identity_incremental_once",
                    new=AsyncMock(return_value=5),
                ) as incremental_mock,
            ):
                detected = await _sync_identity_imap_once_unlocked(self.session_factory, identity_id)
            return detected, discover_mock.await_count, history_mock.await_count + incremental_mock.await_count

        self.assertEqual(self._run_async(scenario()), (0, 0, 0))

    def test_sync_identity_imap_once_uses_unlocked_entrypoint_under_lock(self) -> None:
        async def scenario() -> int:
            identity_id = await self._create_identity_with_imap()
            with patch(
                "app.services.task_runtime._sync_identity_imap_once_unlocked",
                new=AsyncMock(return_value=7),
            ) as mocked:
                detected = await sync_identity_imap_once(self.session_factory, identity_id)
            mocked.assert_awaited_once_with(self.session_factory, identity_id)
            return detected

        self.assertEqual(self._run_async(scenario()), 7)

    def test_repair_identity_replies_with_professor_email_uses_imap_lock(self) -> None:
        async def scenario() -> tuple[int, int]:
            identity_id = await self._create_identity_with_imap()
            call_count = 0
            started = asyncio.Event()
            release = asyncio.Event()

            async def fake_fetch(*_args, **_kwargs):
                nonlocal call_count
                call_count += 1
                started.set()
                await release.wait()
                return []

            with patch(
                "app.services.task_runtime.mail_runtime.fetch_professor_history_inbox_messages",
                new=AsyncMock(side_effect=fake_fetch),
            ):
                first = asyncio.create_task(
                    repair_identity_replies(
                        self.session_factory,
                        identity_id,
                        professor_email="prof@example.edu",
                    ),
                )
                await started.wait()
                second = await repair_identity_replies(
                    self.session_factory,
                    identity_id,
                    professor_email="prof@example.edu",
                )
                release.set()
                first_result = await first
            return first_result, second + call_count

        self.assertEqual(self._run_async(scenario()), (0, 1))

    async def _create_reply_task(self, *, status: str) -> tuple[int, int, int]:
        async with self.session_factory() as session:
            identity = self._build_identity()
            llm = self._build_llm()
            professor = Professor(name="Reply Professor", email="prof@example.edu")
            session.add_all([identity, llm, professor])
            await session.flush()
            task = EmailTask(
                identity_id=identity.id,
                llm_profile_id=llm.id,
                professor_id=professor.id,
                status=status,
                sent_at=datetime(2026, 5, 1, tzinfo=UTC),
                approved_subject="Hello",
                last_rfc_message_id="<sent@example.com>",
            )
            session.add(task)
            await session.flush()
            session.add(
                EmailLog(
                    email_task_id=task.id,
                    identity_id=identity.id,
                    llm_profile_id=llm.id,
                    professor_id=professor.id,
                    direction=EmailDirection.SENT.value,
                    subject="Hello",
                    content="sent",
                    rfc_message_id="<sent@example.com>",
                ),
            )
            await session.commit()
            return identity.id, professor.id, task.id

    async def _create_identity_with_imap(self) -> int:
        async with self.session_factory() as session:
            identity = self._build_identity()
            session.add(identity)
            await session.commit()
            return identity.id

    async def _mark_mailbox_history_completed(
        self,
        identity_id: int,
        *,
        folder_role: str = "inbox",
        folder: str = "INBOX",
    ) -> None:
        async with self.session_factory() as session:
            state = await session.scalar(
                select(ImapMailboxSyncState).where(
                    ImapMailboxSyncState.identity_id == identity_id,
                    ImapMailboxSyncState.folder_role == folder_role,
                    ImapMailboxSyncState.folder == folder,
                ),
            )
            if state is None:
                state = ImapMailboxSyncState(
                    identity_id=identity_id,
                    folder_role=folder_role,
                    folder=folder,
                )
                session.add(state)
            state.history_scan_status = ImapMailboxHistoricalScanStatus.COMPLETED.value
            state.history_next_before_uid = 0
            await session.commit()

    async def _mark_targeted_history_baseline_ready(self, identity_id: int) -> None:
        async with self.session_factory() as session:
            state = await session.scalar(
                select(ImapMailboxSyncState).where(
                    ImapMailboxSyncState.identity_id == identity_id,
                    ImapMailboxSyncState.folder_role == "inbox",
                    ImapMailboxSyncState.folder == "INBOX",
                ),
            )
            if state is None:
                state = ImapMailboxSyncState(
                    identity_id=identity_id,
                    folder_role="inbox",
                    folder="INBOX",
                )
                session.add(state)
            state.professor_state_fingerprint = "targeted-baseline-ready"
            state.last_professor_state_ensure_at = datetime(2026, 7, 1, tzinfo=UTC)
            state.history_strategy_version = "folder-v1-targeted-baseline"
            await session.commit()

    async def _create_professor_task(self, identity_id: int, email: str) -> int:
        async with self.session_factory() as session:
            llm = await session.scalar(select(LLMProfile))
            if llm is None:
                llm = self._build_llm()
                session.add(llm)
                await session.flush()
            professor = Professor(name=email, email=email)
            session.add(professor)
            await session.flush()
            session.add(
                EmailTask(
                    identity_id=identity_id,
                    llm_profile_id=llm.id,
                    professor_id=professor.id,
                    status=EmailTaskStatus.SENT.value,
                ),
            )
            await session.commit()
            return professor.id

    @staticmethod
    def _build_fetched_message(
        *,
        message_id: str,
        uid: int = 1,
        uidvalidity: int | None = None,
        from_email: str = "prof@example.edu",
        subject: str = "Re: Hello",
        content: str = "reply content",
        to_emails: list[str] | None = None,
        cc_emails: list[str] | None = None,
        bcc_emails: list[str] | None = None,
    ) -> ImapFetchedMessage:
        return ImapFetchedMessage(
            uid=uid,
            uidvalidity=uidvalidity,
            from_email=from_email,
            subject=subject,
            message_id=message_id,
            in_reply_to="<sent@example.com>",
            references="<sent@example.com>",
            sent_at=datetime(2026, 5, 2, tzinfo=UTC),
            received_at=datetime(2026, 5, 2, 1, tzinfo=UTC),
            headers={"Message-ID": message_id},
            body_text=content,
            body_html="<p>reply</p>",
            to_emails=to_emails or [],
            cc_emails=cc_emails or [],
            bcc_emails=bcc_emails or [],
        )

    @staticmethod
    def _build_identity() -> IdentityProfile:
        return IdentityProfile(
            name="测试身份",
            profile_name="测试身份",
            sender_name="王同学",
            email_address="student@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="student@example.com",
            smtp_password="secret",
            imap_host="imap.example.com",
            imap_port=993,
            imap_username="student@example.com",
            imap_password="secret",
        )

    @staticmethod
    def _build_llm() -> LLMProfile:
        return LLMProfile(
            name="默认模型",
            provider="openai",
            api_key="key",
            model_name="gpt-test",
        )
