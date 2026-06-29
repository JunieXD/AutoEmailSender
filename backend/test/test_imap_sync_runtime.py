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
    ImapMailboxSyncState,
    ImapProfessorSyncState,
    LLMProfile,
    Professor,
)
from app.services.imap_message_fetcher import ImapFetchedMessage
from app.services.imap_sync_state import ensure_professor_scan_states
from app.services.task_runtime import _sync_identity_imap_once_unlocked
from app.services.task_runtime import poll_for_replies_once
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

    def test_poll_for_replies_uses_identity_sync_entrypoint(self) -> None:
        async def scenario() -> int:
            identity_id = await self._create_identity_with_imap()
            with patch(
                "app.services.task_runtime.sync_identity_imap_once",
                new=AsyncMock(return_value=2),
            ) as mocked:
                result = await poll_for_replies_once(self.session_factory)
            mocked.assert_awaited_once_with(self.session_factory, identity_id)
            return result

        self.assertEqual(self._run_async(scenario()), 2)

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
                "app.services.task_runtime.sync_identity_imap_once",
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

    def test_sent_incremental_sync_ignores_non_system_professors(self) -> None:
        async def scenario() -> int:
            identity_id = await self._create_identity_with_imap()
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

            message = self._build_fetched_message(
                uid=41,
                message_id="<sent-history@example.com>",
                from_email="student@example.com",
                to_emails=["known@example.edu"],
            )
            with patch(
                "app.services.task_runtime.mail_runtime.fetch_professor_history_mailbox_messages",
                new=AsyncMock(return_value=[message]),
            ) as mocked:
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

    def test_identity_imap_once_discovers_sent_and_runs_inbox_and_sent_incremental_for_identity(self) -> None:
        async def scenario() -> tuple[int, tuple[object, ...], dict[str, object], tuple[dict[str, object], ...]]:
            identity_id = await self._create_identity_with_imap()
            with (
                patch(
                    "app.services.task_runtime.mail_runtime.discover_sent_folder",
                    new=AsyncMock(return_value="Sent"),
                ),
                patch(
                    "app.services.task_runtime.ensure_professor_scan_states",
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
