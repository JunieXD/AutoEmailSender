from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import (
    Base,
    EmailDirection,
    EmailLog,
    IdentityProfile,
    ImapMailboxSyncState,
    ImapProfessorHistoricalScanStatus,
    ImapProfessorSyncState,
    Professor,
)
from app.modules.communications.imap.fetcher import ImapFetchedMessage
from app.modules.communications.imap.state import (
    RECENT_V2_OBSOLETE_STRATEGY_VERSION,
    RECENT_V2_STRATEGY_VERSION,
    claim_recent_v2_professor_scans,
    ensure_recent_v2_professor_scan_states,
    get_recent_v2_due_summary,
    mark_recent_v2_batch_completed,
    prepare_recent_v2_bulk_sent_batch,
)
from app.modules.communications.transport import (
    ImapHistoryHeaderFetchResult,
    ImapMailboxUidSearchResult,
)
from app.modules.communications.imap.sync import (
    _recent_v2_targeted_professor_limit,
    _should_use_recent_v2_bulk_sent,
    sync_identity_history_once,
)


BASE_TIME = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
HISTORY_START_DATE = date(2025, 1, 1)


class ImapRecentV2QueueTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self._run_async(self._create_schema())

    def tearDown(self) -> None:
        self._run_async(self.engine.dispose())

    @staticmethod
    def _run_async(awaitable):
        return asyncio.run(awaitable)

    async def _create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    def test_professor_sync_version_changes_only_for_email_and_archive_state(self) -> None:
        async def scenario() -> tuple[int, int, int, int, int]:
            async with self.session_factory() as session:
                professor = Professor(
                    name="Original",
                    email="original@example.edu",
                    created_at=BASE_TIME,
                    updated_at=BASE_TIME,
                )
                session.add(professor)
                await session.commit()
                initial = professor.communication_sync_version

                professor.name = "Profile-only update"
                await session.commit()
                after_profile_update = professor.communication_sync_version

                professor.email = "changed@example.edu"
                await session.commit()
                after_email_update = professor.communication_sync_version

                professor.archived_at = BASE_TIME + timedelta(minutes=1)
                await session.commit()
                after_archive = professor.communication_sync_version

                professor.archived_at = None
                await session.commit()
                after_restore = professor.communication_sync_version

                return (
                    initial,
                    after_profile_update,
                    after_email_update,
                    after_archive,
                    after_restore,
                )

        self.assertEqual(self._run_async(scenario()), (1, 1, 2, 3, 4))

    def test_profile_update_does_not_requeue_but_restore_does(self) -> None:
        async def scenario() -> tuple[int, list[str], int, int, list[tuple[str, int]]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(
                    name="Lifecycle",
                    email="lifecycle@example.edu",
                    created_at=BASE_TIME - timedelta(days=1),
                    updated_at=BASE_TIME - timedelta(days=1),
                )
                session.add_all([identity, professor])
                await session.commit()
                identity_id = identity.id
                professor_id = professor.id

            with patch("app.modules.communications.imap.state.utc_now", return_value=BASE_TIME):
                await ensure_recent_v2_professor_scan_states(
                    self.session_factory,
                    identity_id=identity_id,
                    sent_folder="Sent",
                    history_start_date=HISTORY_START_DATE,
                    settle_seconds=0,
                )

            async with self.session_factory() as session:
                states = list(
                    (
                        await session.execute(
                            select(ImapProfessorSyncState).where(
                                ImapProfessorSyncState.identity_id == identity_id,
                            ),
                        )
                    ).scalars(),
                )
                for state in states:
                    state.historical_scan_status = "completed"
                professor = await session.get(Professor, professor_id)
                professor.name = "Profile-only change"
                await session.commit()
                version_after_profile_update = professor.communication_sync_version

            with patch(
                "app.modules.communications.imap.state.utc_now",
                return_value=BASE_TIME + timedelta(minutes=1),
            ):
                profile_touched = await ensure_recent_v2_professor_scan_states(
                    self.session_factory,
                    identity_id=identity_id,
                    sent_folder="Sent",
                    history_start_date=HISTORY_START_DATE,
                    settle_seconds=0,
                )

            async with self.session_factory() as session:
                statuses_after_profile_update = list(
                    await session.scalars(
                        select(ImapProfessorSyncState.historical_scan_status).where(
                            ImapProfessorSyncState.identity_id == identity_id,
                        ),
                    ),
                )
                professor = await session.get(Professor, professor_id)
                professor.archived_at = BASE_TIME + timedelta(minutes=2)
                await session.commit()

            with patch(
                "app.modules.communications.imap.state.utc_now",
                return_value=BASE_TIME + timedelta(minutes=2),
            ):
                archived_touched = await ensure_recent_v2_professor_scan_states(
                    self.session_factory,
                    identity_id=identity_id,
                    sent_folder="Sent",
                    history_start_date=HISTORY_START_DATE,
                    settle_seconds=0,
                )
                archived_summary = await get_recent_v2_due_summary(
                    self.session_factory,
                    identity_id,
                )

            async with self.session_factory() as session:
                professor = await session.get(Professor, professor_id)
                professor.archived_at = None
                await session.commit()
                restored_version = professor.communication_sync_version

            with patch(
                "app.modules.communications.imap.state.utc_now",
                return_value=BASE_TIME + timedelta(minutes=3),
            ):
                restored_touched = await ensure_recent_v2_professor_scan_states(
                    self.session_factory,
                    identity_id=identity_id,
                    sent_folder="Sent",
                    history_start_date=HISTORY_START_DATE,
                    settle_seconds=0,
                )

            async with self.session_factory() as session:
                restored_states = list(
                    (
                        await session.execute(
                            select(ImapProfessorSyncState).order_by(
                                ImapProfessorSyncState.folder_role,
                            ),
                        )
                    ).scalars(),
                )
                return (
                    version_after_profile_update,
                    statuses_after_profile_update,
                    profile_touched + archived_touched + archived_summary.professor_count,
                    restored_touched,
                    [
                        (state.historical_scan_status, state.professor_sync_version)
                        for state in restored_states
                    ],
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                1,
                ["completed", "completed"],
                0,
                2,
                [("pending", 3), ("pending", 3)],
            ),
        )

    def test_recent_v2_states_are_created_per_identity(self) -> None:
        async def scenario() -> tuple[int, int, list[tuple[int, str, str]]]:
            async with self.session_factory() as session:
                first_identity = self._build_identity("first@example.com")
                second_identity = self._build_identity("second@example.com")
                professor = Professor(
                    name="Existing",
                    email="existing@example.edu",
                    created_at=BASE_TIME - timedelta(days=30),
                    updated_at=BASE_TIME - timedelta(days=30),
                )
                session.add_all([first_identity, second_identity, professor])
                await session.commit()
                first_identity_id = first_identity.id
                second_identity_id = second_identity.id

            with patch("app.modules.communications.imap.state.utc_now", return_value=BASE_TIME):
                first_touched = await ensure_recent_v2_professor_scan_states(
                    self.session_factory,
                    identity_id=first_identity_id,
                    sent_folder="Sent",
                    history_start_date=HISTORY_START_DATE,
                    settle_seconds=10,
                )
                second_touched = await ensure_recent_v2_professor_scan_states(
                    self.session_factory,
                    identity_id=second_identity_id,
                    sent_folder="Sent Items",
                    history_start_date=HISTORY_START_DATE,
                    settle_seconds=10,
                )

            async with self.session_factory() as session:
                states = list(
                    (
                        await session.execute(
                            select(ImapProfessorSyncState).order_by(
                                ImapProfessorSyncState.identity_id,
                                ImapProfessorSyncState.folder_role,
                            ),
                        )
                    ).scalars(),
                )
                return (
                    first_touched,
                    second_touched,
                    [(state.identity_id, state.folder_role, state.folder) for state in states],
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                2,
                2,
                [
                    (1, "inbox", "INBOX"),
                    (1, "sent", "Sent"),
                    (2, "inbox", "INBOX"),
                    (2, "sent", "Sent Items"),
                ],
            ),
        )

    def test_settle_window_is_fixed_and_not_claimed_early(self) -> None:
        async def scenario() -> tuple[datetime | None, datetime | None, int, int]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(
                    name="New",
                    email="new@example.edu",
                    created_at=BASE_TIME,
                    updated_at=BASE_TIME,
                )
                session.add_all([identity, professor])
                await session.commit()
                identity_id = identity.id

            with patch("app.modules.communications.imap.state.utc_now", return_value=BASE_TIME):
                await ensure_recent_v2_professor_scan_states(
                    self.session_factory,
                    identity_id=identity_id,
                    sent_folder="Sent",
                    history_start_date=HISTORY_START_DATE,
                    settle_seconds=10,
                )

            async with self.session_factory() as session:
                first_available_at = await session.scalar(
                    select(ImapProfessorSyncState.available_at).where(
                        ImapProfessorSyncState.identity_id == identity_id,
                    ),
                )

            with patch(
                "app.modules.communications.imap.state.utc_now",
                return_value=BASE_TIME + timedelta(seconds=5),
            ):
                touched_again = await ensure_recent_v2_professor_scan_states(
                    self.session_factory,
                    identity_id=identity_id,
                    sent_folder="Sent",
                    history_start_date=HISTORY_START_DATE,
                    settle_seconds=10,
                )
                early_claims = await claim_recent_v2_professor_scans(
                    self.session_factory,
                    identity_id,
                    limit=10,
                )

            async with self.session_factory() as session:
                second_available_at = await session.scalar(
                    select(ImapProfessorSyncState.available_at).where(
                        ImapProfessorSyncState.identity_id == identity_id,
                    ),
                )

            with patch(
                "app.modules.communications.imap.state.utc_now",
                return_value=BASE_TIME + timedelta(seconds=10),
            ):
                due_claims = await claim_recent_v2_professor_scans(
                    self.session_factory,
                    identity_id,
                    limit=10,
                )

            return first_available_at, second_available_at, touched_again, len(early_claims) + len(due_claims)

        first_available_at, second_available_at, touched_again, total_claims = self._run_async(
            scenario(),
        )
        self.assertEqual(first_available_at, BASE_TIME + timedelta(seconds=10))
        self.assertEqual(second_available_at, first_available_at)
        self.assertEqual(touched_again, 0)
        self.assertEqual(total_claims, 2)

    def test_claim_skips_old_email_archived_and_version_mismatched_states(self) -> None:
        async def scenario() -> tuple[list[str], int, int]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                active = Professor(
                    name="Active",
                    email="active@example.edu",
                    communication_sync_version=2,
                )
                mismatched = Professor(
                    name="Mismatch",
                    email="mismatch@example.edu",
                    communication_sync_version=2,
                )
                archived = Professor(
                    name="Archived",
                    email="archived@example.edu",
                    communication_sync_version=2,
                    archived_at=BASE_TIME,
                )
                session.add_all([identity, active, mismatched, archived])
                await session.flush()
                session.add_all(
                    [
                        self._build_queue_state(active.id, "active@example.edu", version=2),
                        self._build_queue_state(active.id, "old@example.edu", version=1),
                        self._build_queue_state(mismatched.id, "mismatch@example.edu", version=1),
                        self._build_queue_state(archived.id, "archived@example.edu", version=2),
                    ],
                )
                await session.commit()
                identity_id = identity.id

            with patch("app.modules.communications.imap.state.utc_now", return_value=BASE_TIME):
                summary = await get_recent_v2_due_summary(self.session_factory, identity_id)
                claims = await claim_recent_v2_professor_scans(
                    self.session_factory,
                    identity_id,
                    limit=10,
                )
            return [state.professor_email for state in claims], summary.professor_count, summary.inbox_state_count

        self.assertEqual(
            self._run_async(scenario()),
            (["active@example.edu"], 1, 1),
        )

    def test_legacy_state_is_reset_to_recent_v2_without_using_old_cursor(self) -> None:
        async def scenario() -> tuple[str, str, int | None, str | None, int]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(
                    name="Legacy",
                    email="legacy@example.edu",
                    created_at=BASE_TIME - timedelta(days=30),
                    updated_at=BASE_TIME - timedelta(days=30),
                )
                session.add_all([identity, professor])
                await session.flush()
                legacy_state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="legacy@example.edu",
                    folder_role="inbox",
                    folder="INBOX",
                    historical_scan_status=ImapProfessorHistoricalScanStatus.COMPLETED.value,
                    history_strategy_version="recent-v1-2026",
                    last_scanned_uid=999,
                    last_error="old error",
                )
                session.add(legacy_state)
                await session.commit()
                identity_id = identity.id
                legacy_state_id = legacy_state.id

            with patch("app.modules.communications.imap.state.utc_now", return_value=BASE_TIME):
                touched = await ensure_recent_v2_professor_scan_states(
                    self.session_factory,
                    identity_id=identity_id,
                    sent_folder="Sent",
                    history_start_date=HISTORY_START_DATE,
                    settle_seconds=10,
                )

            async with self.session_factory() as session:
                state = await session.get(ImapProfessorSyncState, legacy_state_id)
                return (
                    state.history_strategy_version,
                    state.historical_scan_status,
                    state.last_scanned_uid,
                    state.last_error,
                    touched,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (RECENT_V2_STRATEGY_VERSION, "pending", None, None, 2),
        )

    def test_replaced_sent_folder_state_is_retired_and_does_not_get_claimed(self) -> None:
        async def scenario() -> tuple[str, str, list[str]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(
                    name="Folder Change",
                    email="folder-change@example.edu",
                    created_at=BASE_TIME - timedelta(days=1),
                    updated_at=BASE_TIME - timedelta(days=1),
                )
                session.add_all([identity, professor])
                await session.flush()
                old_sent_state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="folder-change@example.edu",
                    folder_role="sent",
                    folder="Sent",
                    historical_scan_status="pending",
                    history_strategy_version=RECENT_V2_STRATEGY_VERSION,
                    history_start_date=HISTORY_START_DATE,
                    batch_id="bulk:old-folder",
                    available_at=BASE_TIME - timedelta(minutes=1),
                    professor_sync_version=1,
                )
                inbox_state = ImapProfessorSyncState(
                    identity_id=identity.id,
                    professor_id=professor.id,
                    professor_email="folder-change@example.edu",
                    folder_role="inbox",
                    folder="INBOX",
                    historical_scan_status="completed",
                    history_strategy_version=RECENT_V2_STRATEGY_VERSION,
                    history_start_date=HISTORY_START_DATE,
                    batch_id="queue:done",
                    available_at=BASE_TIME - timedelta(minutes=1),
                    professor_sync_version=1,
                )
                session.add_all([old_sent_state, inbox_state])
                await session.commit()
                identity_id = identity.id
                old_sent_state_id = old_sent_state.id

            with patch("app.modules.communications.imap.state.utc_now", return_value=BASE_TIME):
                await ensure_recent_v2_professor_scan_states(
                    self.session_factory,
                    identity_id=identity_id,
                    sent_folder="Sent Items",
                    history_start_date=HISTORY_START_DATE,
                    settle_seconds=0,
                )
                claims = await claim_recent_v2_professor_scans(
                    self.session_factory,
                    identity_id,
                    limit=10,
                )

            async with self.session_factory() as session:
                old_state = await session.get(ImapProfessorSyncState, old_sent_state_id)
                return (
                    old_state.history_strategy_version,
                    old_state.historical_scan_status,
                    [state.folder for state in claims],
                )

        self.assertEqual(
            self._run_async(scenario()),
            (RECENT_V2_OBSOLETE_STRATEGY_VERSION, "completed", ["Sent Items"]),
        )

    def test_bulk_batch_completion_does_not_complete_late_professor(self) -> None:
        async def scenario() -> tuple[str, str, str, str, bool]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                first = Professor(
                    name="First",
                    email="first@example.edu",
                    created_at=BASE_TIME - timedelta(days=1),
                    updated_at=BASE_TIME - timedelta(days=1),
                )
                session.add_all([identity, first])
                await session.commit()
                identity_id = identity.id

            with patch("app.modules.communications.imap.state.utc_now", return_value=BASE_TIME):
                await ensure_recent_v2_professor_scan_states(
                    self.session_factory,
                    identity_id=identity_id,
                    sent_folder="Sent",
                    history_start_date=HISTORY_START_DATE,
                    settle_seconds=0,
                )
                batch_id, frozen_ids = await prepare_recent_v2_bulk_sent_batch(
                    self.session_factory,
                    identity_id,
                )

            async with self.session_factory() as session:
                late = Professor(
                    name="Late",
                    email="late@example.edu",
                    created_at=BASE_TIME + timedelta(seconds=1),
                    updated_at=BASE_TIME + timedelta(seconds=1),
                )
                session.add(late)
                await session.commit()

            with patch(
                "app.modules.communications.imap.state.utc_now",
                return_value=BASE_TIME + timedelta(seconds=1),
            ):
                await ensure_recent_v2_professor_scan_states(
                    self.session_factory,
                    identity_id=identity_id,
                    sent_folder="Sent",
                    history_start_date=HISTORY_START_DATE,
                    settle_seconds=0,
                )
                completed = await mark_recent_v2_batch_completed(
                    self.session_factory,
                    batch_id=batch_id or "",
                    folder_role="sent",
                )

            async with self.session_factory() as session:
                sent_states = list(
                    (
                        await session.execute(
                            select(ImapProfessorSyncState)
                            .where(ImapProfessorSyncState.folder_role == "sent")
                            .order_by(ImapProfessorSyncState.professor_email),
                        )
                    ).scalars(),
                )
                first_state, late_state = sent_states
                return (
                    first_state.historical_scan_status,
                    first_state.batch_id or "",
                    late_state.historical_scan_status,
                    late_state.batch_id or "",
                    len(frozen_ids) == completed == 1,
                )

        first_status, first_batch, late_status, late_batch, counts_match = self._run_async(
            scenario(),
        )
        self.assertEqual(first_status, "completed")
        self.assertTrue(first_batch.startswith("bulk:"))
        self.assertEqual(late_status, "pending")
        self.assertTrue(late_batch.startswith("queue:"))
        self.assertTrue(counts_match)

    def test_incomplete_bulk_batch_resumes_even_below_threshold(self) -> None:
        async def scenario() -> tuple[int, int, int | None, str]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professor = Professor(
                    name="Resume",
                    email="resume@example.edu",
                    created_at=BASE_TIME - timedelta(days=1),
                    updated_at=BASE_TIME - timedelta(days=1),
                )
                session.add_all([identity, professor])
                await session.flush()
                session.add_all(
                    [
                        ImapProfessorSyncState(
                            identity_id=identity.id,
                            professor_id=professor.id,
                            professor_email="resume@example.edu",
                            folder_role="sent",
                            folder="Sent",
                            historical_scan_status="pending",
                            history_strategy_version=RECENT_V2_STRATEGY_VERSION,
                            history_start_date=HISTORY_START_DATE,
                            batch_id="bulk:resume",
                            available_at=BASE_TIME - timedelta(minutes=1),
                            professor_sync_version=1,
                        ),
                        ImapProfessorSyncState(
                            identity_id=identity.id,
                            professor_id=professor.id,
                            professor_email="resume@example.edu",
                            folder_role="inbox",
                            folder="INBOX",
                            historical_scan_status="completed",
                            history_strategy_version=RECENT_V2_STRATEGY_VERSION,
                            history_start_date=HISTORY_START_DATE,
                            batch_id="queue:done",
                            available_at=BASE_TIME - timedelta(minutes=1),
                            professor_sync_version=1,
                        ),
                        ImapMailboxSyncState(
                            identity_id=identity.id,
                            folder_role="sent",
                            folder="Sent",
                            history_strategy_version=RECENT_V2_STRATEGY_VERSION,
                            history_batch_id="bulk:resume",
                            history_high_water_uid=50,
                        ),
                    ],
                )
                await session.commit()
                identity_id = identity.id

            async def fake_bulk_headers(
                _identity,
                _folder,
                _since_date,
                *,
                min_uid,
                max_fetch_batches,
                expected_uidvalidity=None,
            ):
                self.assertEqual(min_uid, 50)
                self.assertGreater(max_fetch_batches, 0)
                return ImapHistoryHeaderFetchResult(
                    messages=[],
                    command_count=1,
                    exhausted=False,
                )

            settings = replace(get_settings(), imap_history_queue_settle_seconds=0)
            with (
                patch("app.modules.communications.imap.sync.get_settings", return_value=settings),
                patch("app.modules.communications.imap.sync.utc_now", return_value=BASE_TIME),
                patch("app.modules.communications.imap.state.utc_now", return_value=BASE_TIME),
                patch(
                    "app.modules.communications.imap.sync.get_cached_or_discover_sent_folder",
                    new=AsyncMock(return_value="Sent"),
                ),
                patch(
                    "app.modules.communications.imap.sync.mail_runtime.search_mailbox_uids_since_date",
                    new=AsyncMock(side_effect=AssertionError("resumed bulk batch must not be re-probed")),
                ) as probe_mock,
                patch(
                    "app.modules.communications.imap.sync.mail_runtime.fetch_recent_mailbox_message_headers_since",
                    new=AsyncMock(side_effect=fake_bulk_headers),
                ) as bulk_mock,
            ):
                detected = await sync_identity_history_once(self.session_factory, identity_id)

            async with self.session_factory() as session:
                professor_state = await session.scalar(
                    select(ImapProfessorSyncState).where(
                        ImapProfessorSyncState.folder_role == "sent",
                    ),
                )
                mailbox_state = await session.scalar(
                    select(ImapMailboxSyncState).where(
                        ImapMailboxSyncState.folder_role == "sent",
                    ),
                )
                return (
                    detected,
                    bulk_mock.await_count + probe_mock.await_count,
                    mailbox_state.history_high_water_uid,
                    professor_state.historical_scan_status,
                )

        self.assertEqual(self._run_async(scenario()), (0, 1, 50, "completed"))

    def test_identity_scanned_before_professor_still_backfills_sent_and_inbox(self) -> None:
        async def scenario() -> tuple[int, int, list[tuple[str, str]], list[tuple[str, int | None]]]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                session.add(identity)
                await session.flush()
                session.add(
                    ImapMailboxSyncState(
                        identity_id=identity.id,
                        folder_role="sent",
                        folder="Sent",
                        history_strategy_version="recent-v1-2026",
                        history_high_water_uid=999,
                    ),
                )
                await session.commit()
                identity_id = identity.id

            settings = replace(get_settings(), imap_history_queue_settle_seconds=0)
            first_targeted_mock = AsyncMock(
                side_effect=AssertionError("empty professor library must not search IMAP"),
            )
            with (
                patch("app.modules.communications.imap.sync.get_settings", return_value=settings),
                patch(
                    "app.modules.communications.imap.sync.get_cached_or_discover_sent_folder",
                    new=AsyncMock(return_value="Sent"),
                ),
                patch(
                    "app.modules.communications.imap.sync.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=first_targeted_mock,
                ),
            ):
                first_detected = await sync_identity_history_once(
                    self.session_factory,
                    identity_id,
                )

            async with self.session_factory() as session:
                professor = Professor(name="Later", email="later@example.edu")
                session.add(professor)
                await session.commit()

            sent_header = self._build_message(
                uid=41,
                message_id="<old-sent@example.com>",
                from_email="student@example.com",
                to_emails=["later@example.edu"],
                content="",
            )
            sent_body = self._build_message(
                uid=41,
                message_id="<old-sent@example.com>",
                from_email="student@example.com",
                to_emails=["later@example.edu"],
                content="old sent body",
            )
            inbox_header = self._build_message(
                uid=42,
                message_id="<old-reply@example.com>",
                from_email="later@example.edu",
                content="",
            )
            inbox_body = self._build_message(
                uid=42,
                message_id="<old-reply@example.com>",
                from_email="later@example.edu",
                content="old reply body",
            )
            targeted_calls: list[tuple[str, int | None]] = []

            async def fake_targeted_headers(
                _identity,
                _folder,
                professor_email,
                *,
                folder_role,
                min_uid,
                max_fetch_batches,
                since_date=None,
                expected_uidvalidity=None,
            ):
                self.assertEqual(professor_email, "later@example.edu")
                self.assertEqual(since_date, HISTORY_START_DATE)
                self.assertGreater(max_fetch_batches, 0)
                targeted_calls.append((folder_role, min_uid))
                message = sent_header if folder_role == "sent" else inbox_header
                return ImapHistoryHeaderFetchResult(
                    messages=[message],
                    command_count=1,
                    exhausted=False,
                )

            async def fake_bodies(_identity, folder, uids):
                self.assertEqual(uids, [41] if folder == "Sent" else [42])
                return [sent_body] if folder == "Sent" else [inbox_body]

            with (
                patch("app.modules.communications.imap.sync.get_settings", return_value=settings),
                patch(
                    "app.modules.communications.imap.sync.get_cached_or_discover_sent_folder",
                    new=AsyncMock(return_value="Sent"),
                ),
                patch(
                    "app.modules.communications.imap.sync.mail_runtime.search_mailbox_uids_since_date",
                    new=AsyncMock(side_effect=AssertionError("small batch must not probe whole Sent")),
                ),
                patch(
                    "app.modules.communications.imap.sync.mail_runtime.fetch_recent_mailbox_message_headers_since",
                    new=AsyncMock(side_effect=AssertionError("small batch must not scan whole Sent")),
                ),
                patch(
                    "app.modules.communications.imap.sync.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(side_effect=fake_targeted_headers),
                ),
                patch(
                    "app.modules.communications.imap.sync.mail_runtime.fetch_professor_history_mailbox_messages_by_uid",
                    new=AsyncMock(side_effect=fake_bodies),
                ),
            ):
                second_detected = await sync_identity_history_once(
                    self.session_factory,
                    identity_id,
                )

            async with self.session_factory() as session:
                logs = list(
                    (
                        await session.execute(
                            select(EmailLog).order_by(EmailLog.direction),
                        )
                    ).scalars(),
                )
                return (
                    first_detected,
                    second_detected,
                    [(log.direction, log.content) for log in logs],
                    targeted_calls,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                0,
                2,
                [
                    (EmailDirection.RECEIVED.value, "old reply body"),
                    (EmailDirection.SENT.value, "old sent body"),
                ],
                [("sent", None), ("inbox", None)],
            ),
        )

    def test_strategy_threshold_and_bulk_header_limit(self) -> None:
        settings = get_settings()

        self.assertEqual(_recent_v2_targeted_professor_limit(settings), 15)
        self.assertFalse(
            _should_use_recent_v2_bulk_sent(
                professor_count=15,
                recent_sent_uid_count=1,
                settings=settings,
            ),
        )
        self.assertTrue(
            _should_use_recent_v2_bulk_sent(
                professor_count=16,
                recent_sent_uid_count=20,
                settings=settings,
            ),
        )
        self.assertFalse(
            _should_use_recent_v2_bulk_sent(
                professor_count=16,
                recent_sent_uid_count=5001,
                settings=settings,
            ),
        )

    def test_large_sent_mailbox_falls_back_to_targeted_search(self) -> None:
        async def scenario() -> tuple[int, int, int]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                professors = [
                    Professor(name=f"Professor {index}", email=f"p{index}@example.edu")
                    for index in range(16)
                ]
                session.add_all([identity, *professors])
                await session.commit()
                identity_id = identity.id

            settings = replace(get_settings(), imap_history_queue_settle_seconds=0)
            with (
                patch("app.modules.communications.imap.sync.get_settings", return_value=settings),
                patch(
                    "app.modules.communications.imap.sync.get_cached_or_discover_sent_folder",
                    new=AsyncMock(return_value="Sent"),
                ),
                patch(
                    "app.modules.communications.imap.sync.mail_runtime.search_mailbox_uids_since_date",
                    new=AsyncMock(
                        return_value=ImapMailboxUidSearchResult(
                            uid_count=5001,
                            command_count=1,
                        ),
                    ),
                ) as probe_mock,
                patch(
                    "app.modules.communications.imap.sync.mail_runtime.fetch_recent_mailbox_message_headers_since",
                    new=AsyncMock(side_effect=AssertionError("oversized Sent must not use bulk")),
                ) as bulk_mock,
                patch(
                    "app.modules.communications.imap.sync.mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count",
                    new=AsyncMock(
                        return_value=ImapHistoryHeaderFetchResult(
                            messages=[],
                            command_count=1,
                            exhausted=False,
                        ),
                    ),
                ) as targeted_mock,
            ):
                await sync_identity_history_once(self.session_factory, identity_id)

            return probe_mock.await_count, bulk_mock.await_count, targeted_mock.await_count

        self.assertEqual(self._run_async(scenario()), (1, 0, 32))

    @staticmethod
    def _build_identity(email: str = "student@example.com") -> IdentityProfile:
        return IdentityProfile(
            name=email,
            profile_name=email,
            sender_name="Student",
            email_address=email,
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username=email,
            smtp_password="secret",
            imap_host="imap.example.com",
            imap_port=993,
            imap_username=email,
            imap_password="secret",
        )

    @staticmethod
    def _build_queue_state(
        professor_id: int,
        professor_email: str,
        *,
        version: int,
    ) -> ImapProfessorSyncState:
        return ImapProfessorSyncState(
            identity_id=1,
            professor_id=professor_id,
            professor_email=professor_email,
            folder_role="inbox",
            folder="INBOX",
            historical_scan_status="pending",
            history_strategy_version=RECENT_V2_STRATEGY_VERSION,
            history_start_date=HISTORY_START_DATE,
            batch_id="queue:test",
            available_at=BASE_TIME - timedelta(minutes=1),
            professor_sync_version=version,
        )

    @staticmethod
    def _build_message(
        *,
        uid: int,
        message_id: str,
        from_email: str,
        content: str,
        to_emails: list[str] | None = None,
    ) -> ImapFetchedMessage:
        return ImapFetchedMessage(
            uid=uid,
            uidvalidity=777,
            from_email=from_email,
            subject="Hello",
            message_id=message_id,
            in_reply_to=None,
            references=None,
            sent_at=datetime(2026, 5, 2, tzinfo=UTC),
            received_at=datetime(2026, 5, 2, 1, tzinfo=UTC),
            headers={"Message-ID": message_id},
            body_text=content,
            body_html=None,
            to_emails=to_emails or [],
        )


if __name__ == "__main__":
    unittest.main()
