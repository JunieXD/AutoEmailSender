from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, EmailDirection, EmailLog


class UnifiedEmailLogModelsTestCase(unittest.TestCase):
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

    def test_imap_email_log_can_store_metadata_without_llm_profile(self) -> None:
        synced_at = datetime(2026, 6, 30, 9, 15, tzinfo=UTC)

        async def scenario() -> EmailLog:
            async with self.session_factory() as session:
                session.add(
                    EmailLog(
                        email_task_id=None,
                        identity_id=1,
                        llm_profile_id=None,
                        professor_id=2,
                        direction=EmailDirection.SENT.value,
                        subject="Hello",
                        content="Body",
                        rfc_message_id="<Message.ID@example.edu>",
                        ingest_source="imap",
                        folder_role="sent",
                        folder="Sent",
                        uidvalidity=123,
                        imap_uid=456,
                        normalized_message_id="<message.id@example.edu>",
                        message_fingerprint="sha256:abc",
                        from_email="me@example.com",
                        to_emails=["prof@example.edu"],
                        cc_emails=["cc@example.edu"],
                        bcc_emails=["bcc@example.edu"],
                        synced_at=synced_at,
                    ),
                )
                await session.commit()
                saved = await session.scalar(select(EmailLog))
                assert saved is not None
                return saved

        saved = self._run_async(scenario())

        self.assertIsNone(saved.llm_profile_id)
        self.assertEqual(saved.ingest_source, "imap")
        self.assertEqual(saved.folder_role, "sent")
        self.assertEqual(saved.folder, "Sent")
        self.assertEqual(saved.uidvalidity, 123)
        self.assertEqual(saved.imap_uid, 456)
        self.assertEqual(saved.from_email, "me@example.com")
        self.assertEqual(saved.to_emails, ["prof@example.edu"])
        self.assertEqual(saved.cc_emails, ["cc@example.edu"])
        self.assertEqual(saved.bcc_emails, ["bcc@example.edu"])
        self.assertEqual(saved.message_fingerprint, "sha256:abc")
        self.assertEqual(saved.synced_at, synced_at)

    def test_same_message_id_is_allowed_for_different_professors(self) -> None:
        async def scenario() -> int:
            async with self.session_factory() as session:
                session.add_all(
                    [
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=2,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Reply",
                            content="Body",
                            rfc_message_id="<reply@example.edu>",
                            normalized_message_id="<reply@example.edu>",
                        ),
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=3,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Reply",
                            content="Body",
                            rfc_message_id="<reply@example.edu>",
                            normalized_message_id="<reply@example.edu>",
                        ),
                    ],
                )
                await session.commit()
                return len(list((await session.execute(select(EmailLog))).scalars()))

        self.assertEqual(self._run_async(scenario()), 2)

    def test_same_identity_professor_direction_normalized_message_id_is_rejected(self) -> None:
        async def scenario() -> None:
            async with self.session_factory() as session:
                session.add_all(
                    [
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=2,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Reply",
                            content="Body",
                            normalized_message_id="<reply@example.edu>",
                        ),
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=2,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Reply again",
                            content="Body",
                            normalized_message_id="<reply@example.edu>",
                        ),
                    ],
                )
                await session.commit()

        with self.assertRaises(IntegrityError):
            self._run_async(scenario())

    def test_same_identity_professor_folder_uid_is_rejected(self) -> None:
        async def scenario() -> None:
            async with self.session_factory() as session:
                session.add_all(
                    [
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=2,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Reply",
                            content="Body",
                            folder_role="sent",
                            folder="Sent",
                            uidvalidity=123,
                            imap_uid=456,
                        ),
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=2,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Reply duplicate",
                            content="Body",
                            folder_role="sent",
                            folder="Sent",
                            uidvalidity=123,
                            imap_uid=456,
                        ),
                    ],
                )
                await session.commit()

        with self.assertRaises(IntegrityError):
            self._run_async(scenario())

    def test_same_imap_uid_is_allowed_for_different_professor_or_folder(self) -> None:
        async def scenario() -> int:
            async with self.session_factory() as session:
                session.add_all(
                    [
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=2,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Reply",
                            content="Body",
                            folder_role="sent",
                            folder="Sent",
                            uidvalidity=123,
                            imap_uid=456,
                        ),
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=3,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Reply",
                            content="Body",
                            folder_role="sent",
                            folder="Sent",
                            uidvalidity=123,
                            imap_uid=456,
                        ),
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=2,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Reply",
                            content="Body",
                            folder_role="sent",
                            folder="Sent Items",
                            uidvalidity=123,
                            imap_uid=456,
                        ),
                    ],
                )
                await session.commit()
                return len(list((await session.execute(select(EmailLog))).scalars()))

        self.assertEqual(self._run_async(scenario()), 3)

    def test_same_identity_professor_direction_fingerprint_is_rejected(self) -> None:
        async def scenario() -> None:
            async with self.session_factory() as session:
                session.add_all(
                    [
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=2,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Reply",
                            content="Body",
                            message_fingerprint="sha256:duplicate",
                        ),
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=2,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Reply duplicate",
                            content="Body",
                            message_fingerprint="sha256:duplicate",
                        ),
                    ],
                )
                await session.commit()

        with self.assertRaises(IntegrityError):
            self._run_async(scenario())

    def test_partial_unique_indexes_allow_null_keys(self) -> None:
        async def scenario() -> int:
            async with self.session_factory() as session:
                session.add_all(
                    [
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=2,
                            direction=EmailDirection.RECEIVED.value,
                            subject="No message id",
                            content="Body",
                            normalized_message_id=None,
                        ),
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=2,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Still no message id",
                            content="Body",
                            normalized_message_id=None,
                        ),
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=2,
                            direction=EmailDirection.SENT.value,
                            subject="No fingerprint",
                            content="Body",
                            message_fingerprint=None,
                        ),
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=2,
                            direction=EmailDirection.SENT.value,
                            subject="Still no fingerprint",
                            content="Body",
                            message_fingerprint=None,
                        ),
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=2,
                            direction=EmailDirection.RECEIVED.value,
                            subject="No imap uid",
                            content="Body",
                            folder_role="sent",
                            folder="Sent",
                            uidvalidity=123,
                            imap_uid=None,
                        ),
                        EmailLog(
                            identity_id=1,
                            llm_profile_id=None,
                            professor_id=2,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Still no imap uid",
                            content="Body",
                            folder_role="sent",
                            folder="Sent",
                            uidvalidity=123,
                            imap_uid=None,
                        ),
                    ],
                )
                await session.commit()
                return len(list((await session.execute(select(EmailLog))).scalars()))

        self.assertEqual(self._run_async(scenario()), 6)
