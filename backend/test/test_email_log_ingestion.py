from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, EmailDirection, EmailLog
from app.services.email_log_ingestion import EmailLogIngestRecord, upsert_email_log


class EmailLogIngestionTestCase(unittest.TestCase):
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

    def test_merges_sent_copy_by_normalized_message_id_and_preserves_body(self) -> None:
        async def scenario() -> tuple[int, EmailLog]:
            async with self.session_factory() as session:
                existing = EmailLog(
                    email_task_id=99,
                    identity_id=1,
                    llm_profile_id=7,
                    professor_id=2,
                    direction=EmailDirection.SENT.value,
                    subject="Original",
                    content="keep text body",
                    content_html="<p>keep html body</p>",
                    rfc_message_id="<Message.ID@Example.edu>",
                    normalized_message_id="<message.id@example.edu>",
                    ingest_source="system",
                    from_email=None,
                    to_emails=None,
                    created_at=datetime(2026, 6, 30, 9, 15, tzinfo=UTC),
                )
                session.add(existing)
                await session.flush()

                merged = await upsert_email_log(
                    session,
                    EmailLogIngestRecord(
                        identity_id=1,
                        professor_id=2,
                        direction=EmailDirection.SENT.value,
                        subject="Sent folder subject",
                        content="imap body should not replace",
                        content_html="<p>imap html should not replace</p>",
                        message_id="  <MESSAGE.ID@example.edu>  ",
                        from_email="Student <STUDENT@example.edu>",
                        to_emails=["Teacher <teacher@example.edu>"],
                        cc_emails=["CC <copy@example.edu>"],
                        bcc_emails=None,
                        created_at=datetime(2026, 6, 30, 9, 16, tzinfo=UTC),
                        ingest_source="imap",
                        folder_role="sent",
                        folder="Sent",
                        uidvalidity=123,
                        imap_uid=456,
                        email_task_id=None,
                        llm_profile_id=None,
                        provider_payload={"imap": {"flags": ["\\Seen"]}},
                        reply_headers={"in_reply_to": "<parent@example.edu>"},
                    ),
                )
                await session.commit()

                count = await session.scalar(select(func.count()).select_from(EmailLog))
                await session.refresh(merged)
                assert count is not None
                return count, merged

        count, merged = self._run_async(scenario())

        self.assertEqual(count, 1)
        self.assertEqual(merged.content, "keep text body")
        self.assertEqual(merged.content_html, "<p>keep html body</p>")
        self.assertEqual(merged.email_task_id, 99)
        self.assertEqual(merged.llm_profile_id, 7)
        self.assertEqual(merged.ingest_source, "system")
        self.assertEqual(merged.folder_role, "sent")
        self.assertEqual(merged.folder, "Sent")
        self.assertEqual(merged.uidvalidity, 123)
        self.assertEqual(merged.imap_uid, 456)
        self.assertEqual(merged.from_email, "student@example.edu")
        self.assertEqual(merged.to_emails, ["teacher@example.edu"])
        self.assertEqual(merged.cc_emails, ["copy@example.edu"])
        self.assertIsNotNone(merged.synced_at)
        self.assertEqual(merged.normalized_message_id, "<message.id@example.edu>")
        self.assertEqual(merged.provider_payload, {"imap": {"flags": ["\\Seen"]}})
        self.assertEqual(merged.reply_headers, {"in_reply_to": "<parent@example.edu>"})

    def test_merges_legacy_system_log_by_normalized_rfc_message_id_fallback(self) -> None:
        async def scenario() -> tuple[int, EmailLog]:
            async with self.session_factory() as session:
                existing = EmailLog(
                    email_task_id=99,
                    identity_id=1,
                    llm_profile_id=7,
                    professor_id=2,
                    direction=EmailDirection.SENT.value,
                    subject="Legacy system sent",
                    content="legacy body",
                    content_html="<p>legacy body</p>",
                    rfc_message_id="<Message.ID@Example.edu>",
                    normalized_message_id=None,
                    ingest_source="system",
                    provider_payload={"smtp": {"message_id": "<Message.ID@Example.edu>"}},
                    reply_headers={"references": ["<existing@example.edu>"]},
                    created_at=datetime(2026, 6, 30, 9, 15, tzinfo=UTC),
                )
                session.add(existing)
                await session.flush()

                merged = await upsert_email_log(
                    session,
                    EmailLogIngestRecord(
                        identity_id=1,
                        professor_id=2,
                        direction=EmailDirection.SENT.value,
                        subject="Sent folder subject",
                        content="imap body should not replace",
                        content_html="<p>imap html should not replace</p>",
                        message_id="  <message.id@example.edu>  ",
                        from_email="Student <STUDENT@example.edu>",
                        to_emails=["Teacher <teacher@example.edu>"],
                        cc_emails=None,
                        bcc_emails=None,
                        created_at=datetime(2026, 6, 30, 9, 16, tzinfo=UTC),
                        ingest_source="imap",
                        folder_role="sent",
                        folder="Sent",
                        uidvalidity=123,
                        imap_uid=456,
                        email_task_id=None,
                        llm_profile_id=None,
                        provider_payload={
                            "smtp": {"message_id": "<new-should-not-overwrite@example.edu>"},
                            "imap": {"uid": 456},
                        },
                        reply_headers={
                            "references": ["<new-should-not-overwrite@example.edu>"],
                            "in_reply_to": "<parent@example.edu>",
                        },
                    ),
                )
                await session.commit()

                count = await session.scalar(select(func.count()).select_from(EmailLog))
                await session.refresh(merged)
                assert count is not None
                return count, merged

        count, merged = self._run_async(scenario())

        self.assertEqual(count, 1)
        self.assertEqual(merged.id, 1)
        self.assertEqual(merged.normalized_message_id, "<message.id@example.edu>")
        self.assertEqual(merged.folder_role, "sent")
        self.assertEqual(merged.folder, "Sent")
        self.assertEqual(merged.uidvalidity, 123)
        self.assertEqual(merged.imap_uid, 456)
        self.assertEqual(merged.from_email, "student@example.edu")
        self.assertEqual(merged.to_emails, ["teacher@example.edu"])
        self.assertEqual(
            merged.provider_payload,
            {
                "smtp": {"message_id": "<Message.ID@Example.edu>"},
                "imap": {"uid": 456},
            },
        )
        self.assertEqual(
            merged.reply_headers,
            {
                "references": ["<existing@example.edu>"],
                "in_reply_to": "<parent@example.edu>",
            },
        )

    def test_creates_external_sent_record_without_task_or_llm_profile_and_normalizes_headers(self) -> None:
        async def scenario() -> EmailLog:
            async with self.session_factory() as session:
                created = await upsert_email_log(
                    session,
                    EmailLogIngestRecord(
                        identity_id=1,
                        professor_id=2,
                        direction=EmailDirection.SENT.value,
                        subject="External sent",
                        content="Body",
                        content_html=None,
                        message_id="  <External.ID@Example.EDU>  ",
                        from_email="Student <Student@Example.EDU>",
                        to_emails=["Teacher <teacher@example.edu>", "Teacher Again <TEACHER@example.edu>"],
                        cc_emails=("Copy <Copy@Example.edu>",),
                        bcc_emails=None,
                        created_at=datetime(2026, 6, 30, 10, 2, 30, tzinfo=UTC),
                        ingest_source="imap",
                        folder_role="sent",
                        folder="Sent",
                        uidvalidity=555,
                        imap_uid=777,
                        email_task_id=None,
                        llm_profile_id=None,
                        provider_payload=None,
                        reply_headers=None,
                    ),
                )
                await session.commit()
                await session.refresh(created)
                return created

        created = self._run_async(scenario())

        self.assertIsNone(created.email_task_id)
        self.assertIsNone(created.llm_profile_id)
        self.assertEqual(created.normalized_message_id, "<external.id@example.edu>")
        self.assertEqual(created.rfc_message_id, "  <External.ID@Example.EDU>  ")
        self.assertEqual(created.from_email, "student@example.edu")
        self.assertEqual(created.to_emails, ["teacher@example.edu"])
        self.assertEqual(created.cc_emails, ["copy@example.edu"])
        self.assertIsNone(created.bcc_emails)
        self.assertEqual(created.ingest_source, "imap")

    def test_distinct_message_ids_do_not_dedupe_by_matching_fingerprint(self) -> None:
        async def scenario() -> list[EmailLog]:
            async with self.session_factory() as session:
                base_kwargs = {
                    "identity_id": 1,
                    "professor_id": 2,
                    "direction": EmailDirection.SENT.value,
                    "subject": "Same subject",
                    "content": "Same body",
                    "content_html": None,
                    "from_email": "Student <student@example.edu>",
                    "to_emails": ["Teacher <teacher@example.edu>"],
                    "cc_emails": None,
                    "bcc_emails": None,
                    "created_at": datetime(2026, 6, 30, 10, 2, 30, tzinfo=UTC),
                    "ingest_source": "imap",
                    "folder_role": None,
                    "folder": None,
                    "uidvalidity": None,
                    "imap_uid": None,
                    "email_task_id": None,
                    "llm_profile_id": None,
                    "provider_payload": None,
                    "reply_headers": None,
                }
                await upsert_email_log(
                    session,
                    EmailLogIngestRecord(message_id="<first@example.edu>", **base_kwargs),
                )
                await upsert_email_log(
                    session,
                    EmailLogIngestRecord(message_id="<second@example.edu>", **base_kwargs),
                )
                await session.commit()
                return list(
                    (
                        await session.execute(select(EmailLog).order_by(EmailLog.normalized_message_id.asc()))
                    ).scalars(),
                )

        saved = self._run_async(scenario())

        self.assertEqual(len(saved), 2)
        self.assertEqual(
            [log.normalized_message_id for log in saved],
            ["<first@example.edu>", "<second@example.edu>"],
        )
        self.assertEqual([log.message_fingerprint for log in saved], [None, None])

    def test_deduplicates_missing_message_id_by_content_fingerprint(self) -> None:
        record = EmailLogIngestRecord(
            identity_id=1,
            professor_id=2,
            direction=EmailDirection.RECEIVED.value,
            subject="Reply",
            content="Same reply body",
            content_html=None,
            message_id=None,
            from_email="Teacher <teacher@example.edu>",
            to_emails=["Student <student@example.edu>"],
            cc_emails=None,
            bcc_emails=None,
            created_at=datetime(2026, 6, 30, 11, 2, 59, tzinfo=UTC),
            ingest_source="imap",
            folder_role=None,
            folder=None,
            uidvalidity=None,
            imap_uid=None,
            email_task_id=None,
            llm_profile_id=None,
            provider_payload=None,
            reply_headers=None,
        )

        async def scenario() -> tuple[int, EmailLog]:
            async with self.session_factory() as session:
                first = await upsert_email_log(session, record)
                second = await upsert_email_log(session, record)
                await session.commit()
                count = await session.scalar(select(func.count()).select_from(EmailLog))
                assert count is not None
                return count, second

        count, saved = self._run_async(scenario())

        self.assertEqual(count, 1)
        self.assertEqual(saved.id, 1)
        self.assertIsNotNone(saved.message_fingerprint)
        self.assertTrue(saved.message_fingerprint.startswith("sha256:"))

    def test_html_only_records_with_different_html_have_different_fingerprints(self) -> None:
        async def scenario() -> list[EmailLog]:
            async with self.session_factory() as session:
                base_kwargs = {
                    "identity_id": 1,
                    "professor_id": 2,
                    "direction": EmailDirection.RECEIVED.value,
                    "subject": "HTML reply",
                    "content": "",
                    "message_id": None,
                    "from_email": "Teacher <teacher@example.edu>",
                    "to_emails": ["Student <student@example.edu>"],
                    "cc_emails": None,
                    "bcc_emails": None,
                    "created_at": datetime(2026, 6, 30, 11, 2, 59, tzinfo=UTC),
                    "ingest_source": "imap",
                    "folder_role": None,
                    "folder": None,
                    "uidvalidity": None,
                    "imap_uid": None,
                    "email_task_id": None,
                    "llm_profile_id": None,
                    "provider_payload": None,
                    "reply_headers": None,
                }
                await upsert_email_log(
                    session,
                    EmailLogIngestRecord(content_html="<p>First HTML body</p>", **base_kwargs),
                )
                await upsert_email_log(
                    session,
                    EmailLogIngestRecord(content_html="<p>Second HTML body</p>", **base_kwargs),
                )
                await session.commit()
                return list((await session.execute(select(EmailLog).order_by(EmailLog.id.asc()))).scalars())

        saved = self._run_async(scenario())

        self.assertEqual(len(saved), 2)
        self.assertIsNotNone(saved[0].message_fingerprint)
        self.assertIsNotNone(saved[1].message_fingerprint)
        self.assertNotEqual(saved[0].message_fingerprint, saved[1].message_fingerprint)

    def test_html_only_record_with_same_html_still_deduplicates_by_fingerprint(self) -> None:
        record = EmailLogIngestRecord(
            identity_id=1,
            professor_id=2,
            direction=EmailDirection.RECEIVED.value,
            subject="HTML reply",
            content="",
            content_html="<p>Same HTML body</p>",
            message_id=None,
            from_email="Teacher <teacher@example.edu>",
            to_emails=["Student <student@example.edu>"],
            cc_emails=None,
            bcc_emails=None,
            created_at=datetime(2026, 6, 30, 11, 2, 59, tzinfo=UTC),
            ingest_source="imap",
            folder_role=None,
            folder=None,
            uidvalidity=None,
            imap_uid=None,
            email_task_id=None,
            llm_profile_id=None,
            provider_payload=None,
            reply_headers=None,
        )

        async def scenario() -> tuple[int, EmailLog]:
            async with self.session_factory() as session:
                await upsert_email_log(session, record)
                second = await upsert_email_log(session, record)
                await session.commit()
                count = await session.scalar(select(func.count()).select_from(EmailLog))
                assert count is not None
                return count, second

        count, saved = self._run_async(scenario())

        self.assertEqual(count, 1)
        self.assertIsNotNone(saved.message_fingerprint)

    def test_deduplicates_by_imap_location_without_message_id(self) -> None:
        async def scenario() -> tuple[int, EmailLog]:
            async with self.session_factory() as session:
                first = await upsert_email_log(
                    session,
                    EmailLogIngestRecord(
                        identity_id=1,
                        professor_id=2,
                        direction=EmailDirection.RECEIVED.value,
                        subject="Initial",
                        content="",
                        content_html=None,
                        message_id=None,
                        from_email="Teacher <teacher@example.edu>",
                        to_emails=["Student <student@example.edu>"],
                        cc_emails=None,
                        bcc_emails=None,
                        created_at=datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
                        ingest_source="imap",
                        folder_role="inbox",
                        folder="INBOX",
                        uidvalidity=123,
                        imap_uid=456,
                        email_task_id=None,
                        llm_profile_id=None,
                        provider_payload=None,
                        reply_headers=None,
                    ),
                )
                second = await upsert_email_log(
                    session,
                    EmailLogIngestRecord(
                        identity_id=1,
                        professor_id=2,
                        direction=EmailDirection.RECEIVED.value,
                        subject="Filled later",
                        content="filled body",
                        content_html="<p>filled body</p>",
                        message_id=None,
                        from_email="Teacher <teacher@example.edu>",
                        to_emails=["Student <student@example.edu>"],
                        cc_emails=None,
                        bcc_emails=None,
                        created_at=datetime(2026, 6, 30, 12, 1, tzinfo=UTC),
                        ingest_source="imap",
                        folder_role="inbox",
                        folder="INBOX",
                        uidvalidity=123,
                        imap_uid=456,
                        email_task_id=None,
                        llm_profile_id=None,
                        provider_payload=None,
                        reply_headers=None,
                    ),
                )
                await session.commit()
                count = await session.scalar(select(func.count()).select_from(EmailLog))
                assert count is not None
                return count, second

        count, saved = self._run_async(scenario())

        self.assertEqual(count, 1)
        self.assertEqual(saved.id, 1)
        self.assertEqual(saved.subject, "Initial")
        self.assertEqual(saved.content, "filled body")
        self.assertEqual(saved.content_html, "<p>filled body</p>")

    def test_merge_fills_empty_fields_and_metadata_without_overwriting_existing_bodies(self) -> None:
        async def scenario() -> EmailLog:
            async with self.session_factory() as session:
                existing = EmailLog(
                    identity_id=1,
                    llm_profile_id=None,
                    professor_id=2,
                    direction=EmailDirection.RECEIVED.value,
                    subject=None,
                    content="existing text",
                    content_html="<p>existing html</p>",
                    rfc_message_id=None,
                    ingest_source="imap",
                    folder_role="inbox",
                    folder="INBOX",
                    uidvalidity=123,
                    imap_uid=456,
                    from_email=None,
                    to_emails=None,
                    cc_emails=None,
                    bcc_emails=None,
                    created_at=datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
                )
                session.add(existing)
                await session.flush()

                merged = await upsert_email_log(
                    session,
                    EmailLogIngestRecord(
                        identity_id=1,
                        professor_id=2,
                        direction=EmailDirection.RECEIVED.value,
                        subject="Filled subject",
                        content="new text",
                        content_html="<p>new html</p>",
                        message_id="<Filled@example.edu>",
                        from_email="Teacher <teacher@example.edu>",
                        to_emails=["Student <student@example.edu>"],
                        cc_emails=["Copy <copy@example.edu>"],
                        bcc_emails=["Hidden <hidden@example.edu>"],
                        created_at=datetime(2026, 6, 30, 12, 1, tzinfo=UTC),
                        ingest_source="imap",
                        folder_role="inbox",
                        folder="INBOX",
                        uidvalidity=123,
                        imap_uid=456,
                        email_task_id=88,
                        llm_profile_id=9,
                        provider_payload={"headers": {"Message-ID": "<Filled@example.edu>"}},
                        reply_headers={"references": ["<previous@example.edu>"]},
                    ),
                )
                await session.commit()
                await session.refresh(merged)
                return merged

        merged = self._run_async(scenario())

        self.assertEqual(merged.subject, "Filled subject")
        self.assertEqual(merged.content, "existing text")
        self.assertEqual(merged.content_html, "<p>existing html</p>")
        self.assertEqual(merged.rfc_message_id, "<Filled@example.edu>")
        self.assertEqual(merged.normalized_message_id, "<filled@example.edu>")
        self.assertEqual(merged.email_task_id, 88)
        self.assertEqual(merged.llm_profile_id, 9)
        self.assertEqual(merged.from_email, "teacher@example.edu")
        self.assertEqual(merged.to_emails, ["student@example.edu"])
        self.assertEqual(merged.cc_emails, ["copy@example.edu"])
        self.assertEqual(merged.bcc_emails, ["hidden@example.edu"])
        self.assertIsNone(merged.message_fingerprint)
        self.assertEqual(merged.provider_payload, {"headers": {"Message-ID": "<Filled@example.edu>"}})
        self.assertEqual(merged.reply_headers, {"references": ["<previous@example.edu>"]})
        self.assertIsNotNone(merged.synced_at)


if __name__ == "__main__":
    unittest.main()
