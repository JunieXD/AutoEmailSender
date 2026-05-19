from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
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
from app.services.task_runtime import poll_for_replies_once
from app.services.task_runtime import process_imap_fetched_messages
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

    def test_ensure_professor_scan_states_only_tracks_contacted_professors(self) -> None:
        async def scenario() -> list[str]:
            async with self.session_factory() as session:
                identity = self._build_identity()
                llm = self._build_llm()
                contacted = Professor(name="Contacted", email="contacted@example.edu")
                untouched = Professor(name="Untouched", email="untouched@example.edu")
                session.add_all([identity, llm, contacted, untouched])
                await session.flush()
                session.add(
                    EmailTask(
                        identity_id=identity.id,
                        llm_profile_id=llm.id,
                        professor_id=contacted.id,
                    ),
                )
                await session.commit()

            await ensure_professor_scan_states(self.session_factory)

            async with self.session_factory() as session:
                rows = list((await session.execute(select(ImapProfessorSyncState))).scalars())
                return [row.professor_email for row in rows]

        self.assertEqual(self._run_async(scenario()), ["contacted@example.edu"])

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

    def test_incremental_sync_keeps_cursor_and_records_error_when_fetch_fails(self) -> None:
        async def scenario() -> tuple[int | None, str | None]:
            identity_id = await self._create_identity_with_imap()
            async with self.session_factory() as session:
                session.add(ImapMailboxSyncState(identity_id=identity_id, last_seen_uid=10))
                await session.commit()

            with patch(
                "app.services.task_runtime.mail_runtime.fetch_incremental_inbox_messages",
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
        content: str = "reply content",
    ) -> ImapFetchedMessage:
        return ImapFetchedMessage(
            uid=1,
            from_email="prof@example.edu",
            subject="Re: Hello",
            message_id=message_id,
            in_reply_to="<sent@example.com>",
            references="<sent@example.com>",
            sent_at=datetime(2026, 5, 2, tzinfo=UTC),
            received_at=datetime(2026, 5, 2, 1, tzinfo=UTC),
            headers={"Message-ID": message_id},
            body_text=content,
            body_html="<p>reply</p>",
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
