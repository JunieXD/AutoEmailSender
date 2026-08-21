from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base,
    EmailDeliveryAttempt,
    EmailDeliveryAttemptStatus,
    EmailDirection,
    EmailLog,
    EmailLogRecordState,
    EmailObservation,
    EmailObservationResolution,
    Professor,
)
from app.modules.communications.events import load_communication_events
from app.modules.communications.ingestion import (
    EmailLogIngestRecord,
    attach_delivery_observations,
    build_reconciliation_fingerprint,
    ingest_sent_email_observation,
    release_delivery_observation_candidates,
)
from app.modules.professors.query import (
    _dashboard_summary_expressions,
    _join_dashboard_summaries,
)
from app.services.contact_status import build_contact_status_by_professor


class EmailDeliveryReconciliationTestCase(unittest.TestCase):
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

    @staticmethod
    def _record(
        *,
        sent_at: datetime,
        message_id: str | None = "<provider@example.edu>",
        subject: str = "Research opportunity",
        content: str = "Dear Professor,\n\nBody",
        uid: int = 10,
        uidvalidity: int = 100,
        delivery_key: str | None = None,
        professor_id: int = 2,
        to_emails: list[str] | None = None,
    ) -> EmailLogIngestRecord:
        headers = {
            "message_id": message_id or "",
            "x-autoemailsender-delivery-id": delivery_key or "",
        }
        return EmailLogIngestRecord(
            identity_id=1,
            professor_id=professor_id,
            direction=EmailDirection.SENT.value,
            subject=subject,
            content=content,
            content_html=f"<p>{content}</p>",
            message_id=message_id,
            from_email="Student <student@example.edu>",
            to_emails=to_emails or ["Teacher <teacher@example.edu>"],
            cc_emails=None,
            bcc_emails=None,
            created_at=sent_at,
            ingest_source="imap",
            folder_role="sent",
            folder="Sent",
            uidvalidity=uidvalidity,
            imap_uid=uid,
            email_task_id=None,
            llm_profile_id=None,
            provider_payload=None,
            reply_headers=headers,
            delivery_key=delivery_key,
        )

    def test_qq_rewritten_message_id_matches_unique_system_send_even_when_sync_is_late(
        self,
    ) -> None:
        sent_at = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)

        async def scenario() -> tuple[
            int, int, str, int | None, str, datetime, datetime
        ]:
            async with self.session_factory() as session:
                system_log = EmailLog(
                    email_task_id=99,
                    identity_id=1,
                    llm_profile_id=7,
                    professor_id=2,
                    direction=EmailDirection.SENT.value,
                    subject="Research opportunity",
                    content="Dear Professor,\n\nBody",
                    rfc_message_id="<178642000000.1.1@example.edu>",
                    ingest_source="system",
                    created_at=sent_at,
                )
                session.add(system_log)
                await session.flush()

                result = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at + timedelta(seconds=2),
                        message_id="<tencent_rewritten@example.edu>",
                    ),
                )
                await session.commit()
                log_count = await session.scalar(
                    select(func.count()).select_from(EmailLog)
                )
                observation_count = await session.scalar(
                    select(func.count()).select_from(EmailObservation),
                )
                await session.refresh(system_log)
                return (
                    int(log_count or 0),
                    int(observation_count or 0),
                    result.resolution,
                    result.observation.candidate_email_log_id,
                    system_log.rfc_message_id or "",
                    result.observation.message_sent_at,
                    result.observation.observed_at,
                )

        result = self._run_async(scenario())
        self.assertEqual(
            result[:5],
            (
                1,
                1,
                EmailObservationResolution.PENDING.value,
                1,
                "<178642000000.1.1@example.edu>",
            ),
        )
        self.assertGreater(result[6], result[5])

    def test_missing_message_id_and_stripped_header_stays_pending(self) -> None:
        sent_at = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)

        async def scenario() -> tuple[int, str, int | None, str]:
            async with self.session_factory() as session:
                session.add(
                    EmailLog(
                        email_task_id=98,
                        identity_id=1,
                        llm_profile_id=7,
                        professor_id=2,
                        direction=EmailDirection.SENT.value,
                        subject="Research opportunity",
                        content="Dear Professor,\n\nBody",
                        ingest_source="system",
                        created_at=sent_at,
                    ),
                )
                await session.flush()
                result = await ingest_sent_email_observation(
                    session,
                    self._record(sent_at=sent_at, message_id=None),
                )
                await session.commit()
                count = await session.scalar(select(func.count()).select_from(EmailLog))
                return (
                    int(count or 0),
                    result.resolution,
                    result.observation.candidate_email_log_id,
                    result.match_method or "",
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                1,
                EmailObservationResolution.PENDING.value,
                1,
                "automatic_fold_exact_body",
            ),
        )

    def test_two_equally_plausible_real_sends_are_never_auto_merged(self) -> None:
        base = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)

        async def scenario() -> tuple[int, str, int | None]:
            async with self.session_factory() as session:
                session.add_all(
                    [
                        EmailLog(
                            email_task_id=101,
                            identity_id=1,
                            llm_profile_id=7,
                            professor_id=2,
                            direction=EmailDirection.SENT.value,
                            subject="Research opportunity",
                            content="Dear Professor,\n\nBody",
                            rfc_message_id="<first@example.edu>",
                            ingest_source="system",
                            created_at=base,
                        ),
                        EmailLog(
                            email_task_id=102,
                            identity_id=1,
                            llm_profile_id=7,
                            professor_id=2,
                            direction=EmailDirection.SENT.value,
                            subject="Research opportunity",
                            content="Dear Professor,\n\nBody",
                            rfc_message_id="<second@example.edu>",
                            ingest_source="system",
                            created_at=base + timedelta(seconds=20),
                        ),
                    ],
                )
                await session.flush()
                result = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=base + timedelta(seconds=10),
                        message_id="<rewritten-ambiguous@example.edu>",
                    ),
                )
                await session.commit()
                count = await session.scalar(select(func.count()).select_from(EmailLog))
                return (
                    int(count or 0),
                    result.resolution,
                    result.observation.email_log_id,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (2, EmailObservationResolution.PENDING.value, None),
        )

    def test_provider_body_mutation_stays_pending_instead_of_creating_duplicate(
        self,
    ) -> None:
        sent_at = datetime(2026, 8, 11, 11, 0, tzinfo=UTC)

        async def scenario() -> tuple[int, str]:
            async with self.session_factory() as session:
                session.add(
                    EmailLog(
                        email_task_id=103,
                        identity_id=1,
                        llm_profile_id=7,
                        professor_id=2,
                        direction=EmailDirection.SENT.value,
                        subject="Research opportunity",
                        content="Original body",
                        rfc_message_id="<original@example.edu>",
                        ingest_source="system",
                        created_at=sent_at,
                    ),
                )
                await session.flush()
                result = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at + timedelta(seconds=1),
                        message_id="<provider-mutated@example.edu>",
                        content="Provider-added footer\nOriginal body",
                    ),
                )
                await session.commit()
                count = await session.scalar(select(func.count()).select_from(EmailLog))
                return int(count or 0), result.resolution

        self.assertEqual(
            self._run_async(scenario()),
            (1, EmailObservationResolution.PENDING.value),
        )

    def test_same_subject_with_different_body_remains_external(self) -> None:
        sent_at = datetime(2026, 8, 11, 11, 30, tzinfo=UTC)

        async def scenario() -> tuple[int, str, int | None]:
            async with self.session_factory() as session:
                session.add(
                    EmailLog(
                        email_task_id=104,
                        identity_id=1,
                        llm_profile_id=7,
                        professor_id=2,
                        direction=EmailDirection.SENT.value,
                        subject="Research opportunity",
                        content="Application body written by the software",
                        ingest_source="system",
                        created_at=sent_at,
                    ),
                )
                await session.flush()
                result = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at + timedelta(seconds=10),
                        message_id="<manual-webmail@example.edu>",
                        content="A separate message written manually in webmail",
                    ),
                )
                await session.commit()
                count = await session.scalar(select(func.count()).select_from(EmailLog))
                return (
                    int(count or 0),
                    result.resolution,
                    result.observation.candidate_email_log_id,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (2, EmailObservationResolution.EXTERNAL.value, None),
        )

    def test_mailbox_copy_can_arrive_minutes_later_and_still_fold(self) -> None:
        sent_at = datetime(2026, 8, 11, 11, 45, tzinfo=UTC)

        async def scenario() -> tuple[int, str, int | None]:
            async with self.session_factory() as session:
                session.add(
                    EmailLog(
                        email_task_id=105,
                        identity_id=1,
                        llm_profile_id=7,
                        professor_id=2,
                        direction=EmailDirection.SENT.value,
                        subject="Research opportunity",
                        content="Dear Professor,\n\nBody",
                        ingest_source="system",
                        created_at=sent_at,
                    ),
                )
                await session.flush()
                result = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at + timedelta(minutes=8),
                        message_id="<delayed-provider-copy@example.edu>",
                    ),
                )
                await session.commit()
                count = await session.scalar(select(func.count()).select_from(EmailLog))
                return (
                    int(count or 0),
                    result.resolution,
                    result.observation.candidate_email_log_id,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (1, EmailObservationResolution.PENDING.value, 1),
        )

    def test_better_mailbox_copy_replaces_earlier_manual_candidate(self) -> None:
        sent_at = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)

        async def scenario() -> tuple[
            int, int, list[tuple[str, int | None, int | None]]
        ]:
            async with self.session_factory() as session:
                session.add(
                    EmailLog(
                        email_task_id=106,
                        identity_id=1,
                        llm_profile_id=7,
                        professor_id=2,
                        direction=EmailDirection.SENT.value,
                        subject="Research opportunity",
                        content="Dear Professor,\n\nBody",
                        ingest_source="system",
                        created_at=sent_at,
                    ),
                )
                await session.flush()
                manual = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at + timedelta(minutes=4),
                        message_id="<manual-resend@example.edu>",
                        uid=31,
                    ),
                )
                provider = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at + timedelta(seconds=5),
                        message_id="<provider-copy@example.edu>",
                        uid=32,
                    ),
                )
                await session.commit()
                await session.refresh(manual.observation)
                await session.refresh(provider.observation)
                log_count = await session.scalar(
                    select(func.count()).select_from(EmailLog)
                )
                observation_count = await session.scalar(
                    select(func.count()).select_from(EmailObservation),
                )
                observations = list(
                    await session.scalars(
                        select(EmailObservation).order_by(EmailObservation.id),
                    ),
                )
                return (
                    int(log_count or 0),
                    int(observation_count or 0),
                    [
                        (
                            item.resolution,
                            item.email_log_id,
                            item.candidate_email_log_id,
                        )
                        for item in observations
                    ],
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                2,
                2,
                [
                    (EmailObservationResolution.EXTERNAL.value, 2, None),
                    (EmailObservationResolution.PENDING.value, None, 1),
                ],
            ),
        )

    def test_delivery_key_always_releases_conflicting_weak_candidate(self) -> None:
        sent_at = datetime(2026, 8, 11, 12, 15, tzinfo=UTC)
        delivery_key = str(uuid.uuid4())

        async def scenario() -> tuple[int, list[tuple[str, int | None, int | None]]]:
            async with self.session_factory() as session:
                session.add_all(
                    [
                        EmailDeliveryAttempt(
                            id=delivery_key,
                            email_task_id=109,
                            identity_id=1,
                            professor_id=2,
                            attempt_number=1,
                            recipient_email="teacher@example.edu",
                            subject_fingerprint=build_reconciliation_fingerprint(
                                "Research opportunity",
                            ),
                            content_fingerprint=build_reconciliation_fingerprint(
                                "Dear Professor,\n\nBody",
                            ),
                            status=EmailDeliveryAttemptStatus.ACCEPTED.value,
                            started_at=sent_at,
                        ),
                        EmailLog(
                            delivery_attempt_id=delivery_key,
                            email_task_id=109,
                            identity_id=1,
                            llm_profile_id=7,
                            professor_id=2,
                            direction=EmailDirection.SENT.value,
                            subject="Research opportunity",
                            content="Dear Professor,\n\nBody",
                            rfc_message_id="<app-strong@example.edu>",
                            ingest_source="system",
                            created_at=sent_at,
                        ),
                    ],
                )
                await session.flush()
                await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at + timedelta(seconds=1),
                        message_id="<manual-first@example.edu>",
                        uid=35,
                    ),
                )
                await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at + timedelta(minutes=10),
                        message_id="<provider-strong@example.edu>",
                        uid=36,
                        delivery_key=delivery_key,
                    ),
                )
                await session.commit()
                observations = list(
                    await session.scalars(
                        select(EmailObservation).order_by(EmailObservation.id),
                    ),
                )
                return (
                    int(
                        await session.scalar(select(func.count()).select_from(EmailLog))
                        or 0
                    ),
                    [
                        (
                            item.resolution,
                            item.email_log_id,
                            item.candidate_email_log_id,
                        )
                        for item in observations
                    ],
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                2,
                [
                    (EmailObservationResolution.EXTERNAL.value, 2, None),
                    (EmailObservationResolution.MATCHED.value, 1, None),
                ],
            ),
        )

    def test_one_app_send_folds_only_one_distinct_mailbox_message(self) -> None:
        sent_at = datetime(2026, 8, 11, 12, 30, tzinfo=UTC)

        async def scenario() -> tuple[int, list[str]]:
            async with self.session_factory() as session:
                session.add(
                    EmailLog(
                        email_task_id=107,
                        identity_id=1,
                        llm_profile_id=7,
                        professor_id=2,
                        direction=EmailDirection.SENT.value,
                        subject="Research opportunity",
                        content="Dear Professor,\n\nBody",
                        ingest_source="system",
                        created_at=sent_at,
                    ),
                )
                await session.flush()
                first = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at + timedelta(seconds=1),
                        message_id="<first-mailbox-message@example.edu>",
                        uid=41,
                    ),
                )
                second = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at + timedelta(seconds=2),
                        message_id="<second-mailbox-message@example.edu>",
                        uid=42,
                    ),
                )
                await session.commit()
                return (
                    int(
                        await session.scalar(select(func.count()).select_from(EmailLog))
                        or 0
                    ),
                    [first.resolution, second.resolution],
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                2,
                [
                    EmailObservationResolution.PENDING.value,
                    EmailObservationResolution.EXTERNAL.value,
                ],
            ),
        )

    def test_same_provider_message_id_across_folders_shares_folded_delivery(
        self,
    ) -> None:
        sent_at = datetime(2026, 8, 11, 12, 45, tzinfo=UTC)

        async def scenario() -> tuple[int, int, list[str]]:
            async with self.session_factory() as session:
                session.add(
                    EmailLog(
                        email_task_id=108,
                        identity_id=1,
                        llm_profile_id=7,
                        professor_id=2,
                        direction=EmailDirection.SENT.value,
                        subject="Research opportunity",
                        content="Dear Professor,\n\nBody",
                        rfc_message_id="<app-message@example.edu>",
                        ingest_source="system",
                        created_at=sent_at,
                    ),
                )
                await session.flush()
                first = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at,
                        message_id="<provider-message@example.edu>",
                        uid=51,
                    ),
                )
                moved = self._record(
                    sent_at=sent_at,
                    message_id="<provider-message@example.edu>",
                    uid=52,
                )
                second = await ingest_sent_email_observation(
                    session,
                    EmailLogIngestRecord(
                        **{
                            **moved.__dict__,
                            "folder": "Archive/Sent",
                        },
                    ),
                )
                await session.commit()
                return (
                    int(
                        await session.scalar(select(func.count()).select_from(EmailLog))
                        or 0
                    ),
                    int(
                        await session.scalar(
                            select(func.count()).select_from(EmailObservation),
                        )
                        or 0
                    ),
                    [first.resolution, second.resolution],
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                1,
                2,
                [
                    EmailObservationResolution.PENDING.value,
                    EmailObservationResolution.PENDING.value,
                ],
            ),
        )

    def test_custom_delivery_key_matches_without_relying_on_provider_message_id(
        self,
    ) -> None:
        sent_at = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
        delivery_key = str(uuid.uuid4())

        async def scenario() -> tuple[str, int, str]:
            async with self.session_factory() as session:
                attempt = EmailDeliveryAttempt(
                    id=delivery_key,
                    email_task_id=104,
                    identity_id=1,
                    professor_id=2,
                    attempt_number=1,
                    recipient_email="teacher@example.edu",
                    subject_fingerprint=build_reconciliation_fingerprint(
                        "Research opportunity"
                    ),
                    content_fingerprint=build_reconciliation_fingerprint(
                        "Dear Professor,\n\nBody"
                    ),
                    status=EmailDeliveryAttemptStatus.ACCEPTED.value,
                    started_at=sent_at,
                )
                email_log = EmailLog(
                    delivery_attempt_id=delivery_key,
                    email_task_id=104,
                    identity_id=1,
                    llm_profile_id=7,
                    professor_id=2,
                    direction=EmailDirection.SENT.value,
                    subject="Research opportunity",
                    content="Dear Professor,\n\nBody",
                    rfc_message_id="<app-id@example.edu>",
                    normalized_message_id="<app-id@example.edu>",
                    ingest_source="system",
                    created_at=sent_at,
                )
                session.add_all([attempt, email_log])
                await session.flush()
                result = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at,
                        message_id="<provider-id@example.edu>",
                        delivery_key=delivery_key,
                    ),
                )
                await session.commit()
                return result.resolution, result.email_log.id, result.match_method or ""

        self.assertEqual(
            self._run_async(scenario()),
            (EmailObservationResolution.MATCHED.value, 1, "delivery_key"),
        )

    def test_unknown_or_wrong_professor_delivery_key_is_quarantined(self) -> None:
        sent_at = datetime(2026, 8, 11, 13, 0, tzinfo=UTC)
        delivery_key = str(uuid.uuid4())

        async def scenario() -> tuple[int, str, int | None]:
            async with self.session_factory() as session:
                session.add(
                    EmailDeliveryAttempt(
                        id=delivery_key,
                        email_task_id=105,
                        identity_id=1,
                        professor_id=2,
                        attempt_number=1,
                        recipient_email="teacher@example.edu",
                        subject_fingerprint=build_reconciliation_fingerprint(
                            "Research opportunity"
                        ),
                        content_fingerprint=build_reconciliation_fingerprint(
                            "Dear Professor,\n\nBody"
                        ),
                        status=EmailDeliveryAttemptStatus.ACCEPTED.value,
                        started_at=sent_at,
                    ),
                )
                await session.flush()
                result = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at,
                        delivery_key=delivery_key,
                        professor_id=3,
                    ),
                )
                await session.commit()
                count = await session.scalar(select(func.count()).select_from(EmailLog))
                return (
                    int(count or 0),
                    result.resolution,
                    result.observation.email_log_id,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (0, EmailObservationResolution.PENDING.value, None),
        )

    def test_external_sent_message_is_created_once_across_folder_move(self) -> None:
        sent_at = datetime(2026, 8, 11, 14, 0, tzinfo=UTC)

        async def scenario() -> tuple[int, int, list[str]]:
            async with self.session_factory() as session:
                first = await ingest_sent_email_observation(
                    session,
                    self._record(sent_at=sent_at, message_id="<external@example.edu>"),
                )
                moved_record = self._record(
                    sent_at=sent_at,
                    message_id="<external@example.edu>",
                    uid=20,
                    uidvalidity=200,
                )
                moved_record = EmailLogIngestRecord(
                    **{
                        **moved_record.__dict__,
                        "folder": "Archive/Sent",
                    },
                )
                second = await ingest_sent_email_observation(session, moved_record)
                await session.commit()
                log_count = await session.scalar(
                    select(func.count()).select_from(EmailLog)
                )
                observation_count = await session.scalar(
                    select(func.count()).select_from(EmailObservation),
                )
                return (
                    int(log_count or 0),
                    int(observation_count or 0),
                    [first.resolution, second.resolution],
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                1,
                2,
                [
                    EmailObservationResolution.EXTERNAL.value,
                    EmailObservationResolution.MATCHED.value,
                ],
            ),
        )

    def test_delivery_key_observation_recovers_missing_smtp_log(self) -> None:
        sent_at = datetime(2026, 8, 11, 15, 0, tzinfo=UTC)
        delivery_key = str(uuid.uuid4())

        async def scenario() -> tuple[str, str, int, str, int]:
            async with self.session_factory() as session:
                attempt = EmailDeliveryAttempt(
                    id=delivery_key,
                    email_task_id=106,
                    identity_id=1,
                    professor_id=2,
                    attempt_number=1,
                    recipient_email="teacher@example.edu",
                    subject_fingerprint=build_reconciliation_fingerprint(
                        "Research opportunity"
                    ),
                    content_fingerprint=build_reconciliation_fingerprint(
                        "Dear Professor,\n\nBody"
                    ),
                    status=EmailDeliveryAttemptStatus.PREPARED.value,
                    started_at=sent_at,
                )
                session.add(attempt)
                await session.flush()
                recovered = await ingest_sent_email_observation(
                    session,
                    self._record(sent_at=sent_at, delivery_key=delivery_key),
                )
                assert recovered.email_log is not None
                await attach_delivery_observations(
                    session,
                    delivery_attempt_id=delivery_key,
                    email_log=recovered.email_log,
                )
                await session.commit()
                await session.refresh(recovered.observation)
                await session.refresh(attempt)
                log_count = await session.scalar(
                    select(func.count()).select_from(EmailLog)
                )
                return (
                    recovered.resolution,
                    recovered.observation.resolution,
                    recovered.observation.email_log_id or 0,
                    attempt.status,
                    int(log_count or 0),
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                EmailObservationResolution.MATCHED.value,
                EmailObservationResolution.MATCHED.value,
                1,
                EmailDeliveryAttemptStatus.ACCEPTED.value,
                1,
            ),
        )

    def test_prepared_attempt_candidate_attaches_only_after_send_succeeds(self) -> None:
        sent_at = datetime(2026, 8, 11, 15, 30, tzinfo=UTC)
        delivery_key = str(uuid.uuid4())

        async def scenario() -> tuple[int, str, int | None, str]:
            async with self.session_factory() as session:
                attempt = EmailDeliveryAttempt(
                    id=delivery_key,
                    email_task_id=107,
                    identity_id=1,
                    professor_id=2,
                    attempt_number=1,
                    recipient_email="teacher@example.edu",
                    subject_fingerprint=build_reconciliation_fingerprint(
                        "Research opportunity",
                    ),
                    content_fingerprint=build_reconciliation_fingerprint(
                        "Dear Professor,\n\nBody",
                    ),
                    status=EmailDeliveryAttemptStatus.PREPARED.value,
                    started_at=sent_at,
                )
                session.add(attempt)
                await session.flush()
                result = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at,
                        message_id="<stripped-header@example.edu>",
                    ),
                )
                system_log = EmailLog(
                    delivery_attempt_id=delivery_key,
                    email_task_id=107,
                    identity_id=1,
                    llm_profile_id=7,
                    professor_id=2,
                    direction=EmailDirection.SENT.value,
                    subject="Research opportunity",
                    content="Dear Professor,\n\nBody",
                    ingest_source="system",
                    created_at=sent_at,
                )
                session.add(system_log)
                await session.flush()
                await attach_delivery_observations(
                    session,
                    delivery_attempt_id=delivery_key,
                    email_log=system_log,
                )
                await session.commit()
                await session.refresh(result.observation)
                return (
                    int(
                        await session.scalar(select(func.count()).select_from(EmailLog))
                        or 0
                    ),
                    result.observation.resolution,
                    result.observation.email_log_id,
                    result.observation.match_method or "",
                )

        self.assertEqual(
            self._run_async(scenario()),
            (1, EmailObservationResolution.MATCHED.value, 1, "delivery_key"),
        )

    def test_prepared_attempt_candidate_becomes_external_when_send_fails(self) -> None:
        sent_at = datetime(2026, 8, 11, 15, 45, tzinfo=UTC)
        delivery_key = str(uuid.uuid4())

        async def scenario() -> tuple[int, str, int | None, str]:
            async with self.session_factory() as session:
                session.add(
                    EmailDeliveryAttempt(
                        id=delivery_key,
                        email_task_id=108,
                        identity_id=1,
                        professor_id=2,
                        attempt_number=1,
                        recipient_email="teacher@example.edu",
                        subject_fingerprint=build_reconciliation_fingerprint(
                            "Research opportunity",
                        ),
                        content_fingerprint=build_reconciliation_fingerprint(
                            "Dear Professor,\n\nBody",
                        ),
                        status=EmailDeliveryAttemptStatus.PREPARED.value,
                        started_at=sent_at,
                    ),
                )
                await session.flush()
                result = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at,
                        message_id="<manual-during-failure@example.edu>",
                    ),
                )
                await release_delivery_observation_candidates(
                    session,
                    delivery_attempt_id=delivery_key,
                )
                await session.commit()
                await session.refresh(result.observation)
                return (
                    int(
                        await session.scalar(select(func.count()).select_from(EmailLog))
                        or 0
                    ),
                    result.observation.resolution,
                    result.observation.candidate_email_log_id,
                    result.observation.match_method or "",
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                1,
                EmailObservationResolution.EXTERNAL.value,
                None,
                "automatic_fold_released_external",
            ),
        )

    def test_two_legitimate_repeated_sends_keep_separate_delivery_attempts(
        self,
    ) -> None:
        base = datetime(2026, 8, 11, 16, 0, tzinfo=UTC)
        delivery_keys = [str(uuid.uuid4()), str(uuid.uuid4())]

        async def scenario() -> tuple[int, int, list[str], list[int]]:
            async with self.session_factory() as session:
                for index, delivery_key in enumerate(delivery_keys):
                    sent_at = base + timedelta(minutes=index)
                    session.add_all(
                        [
                            EmailDeliveryAttempt(
                                id=delivery_key,
                                email_task_id=200 + index,
                                identity_id=1,
                                professor_id=2,
                                attempt_number=1,
                                recipient_email="teacher@example.edu",
                                subject_fingerprint=build_reconciliation_fingerprint(
                                    "Research opportunity",
                                ),
                                content_fingerprint=build_reconciliation_fingerprint(
                                    "Dear Professor,\n\nBody",
                                ),
                                status=EmailDeliveryAttemptStatus.ACCEPTED.value,
                                started_at=sent_at,
                            ),
                            EmailLog(
                                delivery_attempt_id=delivery_key,
                                email_task_id=200 + index,
                                identity_id=1,
                                llm_profile_id=7,
                                professor_id=2,
                                direction=EmailDirection.SENT.value,
                                subject="Research opportunity",
                                content="Dear Professor,\n\nBody",
                                rfc_message_id=f"<app-{index}@example.edu>",
                                ingest_source="system",
                                created_at=sent_at,
                            ),
                        ],
                    )
                await session.flush()
                results = []
                for index, delivery_key in enumerate(delivery_keys):
                    results.append(
                        await ingest_sent_email_observation(
                            session,
                            self._record(
                                sent_at=base + timedelta(minutes=index),
                                message_id=f"<provider-{index}@example.edu>",
                                delivery_key=delivery_key,
                                uid=20 + index,
                            ),
                        ),
                    )
                await session.commit()
                log_count = await session.scalar(
                    select(func.count()).select_from(EmailLog)
                )
                observation_count = await session.scalar(
                    select(func.count()).select_from(EmailObservation),
                )
                return (
                    int(log_count or 0),
                    int(observation_count or 0),
                    [result.resolution for result in results],
                    [
                        result.email_log.id
                        for result in results
                        if result.email_log is not None
                    ],
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                2,
                2,
                [
                    EmailObservationResolution.MATCHED.value,
                    EmailObservationResolution.MATCHED.value,
                ],
                [1, 2],
            ),
        )

    def test_retry_observation_matches_successful_attempt_not_failed_attempt(
        self,
    ) -> None:
        base = datetime(2026, 8, 11, 17, 0, tzinfo=UTC)
        failed_key = str(uuid.uuid4())
        accepted_key = str(uuid.uuid4())

        async def scenario() -> tuple[int, str, str, str]:
            async with self.session_factory() as session:
                session.add_all(
                    [
                        EmailDeliveryAttempt(
                            id=failed_key,
                            email_task_id=210,
                            identity_id=1,
                            professor_id=2,
                            attempt_number=1,
                            recipient_email="teacher@example.edu",
                            subject_fingerprint=build_reconciliation_fingerprint(
                                "Research opportunity",
                            ),
                            content_fingerprint=build_reconciliation_fingerprint(
                                "Dear Professor,\n\nBody",
                            ),
                            status=EmailDeliveryAttemptStatus.FAILED.value,
                            started_at=base,
                        ),
                        EmailLog(
                            delivery_attempt_id=failed_key,
                            email_task_id=210,
                            identity_id=1,
                            llm_profile_id=7,
                            professor_id=2,
                            direction=EmailDirection.SENT.value,
                            subject="Research opportunity",
                            content="Dear Professor,\n\nBody",
                            failure_summary="SMTP rejected",
                            ingest_source="system",
                            created_at=base,
                        ),
                        EmailDeliveryAttempt(
                            id=accepted_key,
                            email_task_id=210,
                            identity_id=1,
                            professor_id=2,
                            attempt_number=2,
                            recipient_email="teacher@example.edu",
                            subject_fingerprint=build_reconciliation_fingerprint(
                                "Research opportunity",
                            ),
                            content_fingerprint=build_reconciliation_fingerprint(
                                "Dear Professor,\n\nBody",
                            ),
                            status=EmailDeliveryAttemptStatus.ACCEPTED.value,
                            started_at=base + timedelta(minutes=1),
                        ),
                        EmailLog(
                            delivery_attempt_id=accepted_key,
                            email_task_id=210,
                            identity_id=1,
                            llm_profile_id=7,
                            professor_id=2,
                            direction=EmailDirection.SENT.value,
                            subject="Research opportunity",
                            content="Dear Professor,\n\nBody",
                            ingest_source="system",
                            created_at=base + timedelta(minutes=1),
                        ),
                    ],
                )
                await session.flush()
                result = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=base + timedelta(minutes=1),
                        delivery_key=accepted_key,
                    ),
                )
                await session.commit()
                count = await session.scalar(select(func.count()).select_from(EmailLog))
                return (
                    int(count or 0),
                    result.resolution,
                    result.email_log.delivery_attempt_id if result.email_log else "",
                    result.match_method or "",
                )

        self.assertEqual(
            self._run_async(scenario()),
            (2, EmailObservationResolution.MATCHED.value, accepted_key, "delivery_key"),
        )

    def test_weak_similarity_never_hides_message_behind_failed_attempt(self) -> None:
        sent_at = datetime(2026, 8, 11, 17, 30, tzinfo=UTC)
        failed_key = str(uuid.uuid4())

        async def scenario() -> tuple[int, str, int | None]:
            async with self.session_factory() as session:
                session.add_all(
                    [
                        EmailDeliveryAttempt(
                            id=failed_key,
                            email_task_id=211,
                            identity_id=1,
                            professor_id=2,
                            attempt_number=1,
                            recipient_email="teacher@example.edu",
                            subject_fingerprint=build_reconciliation_fingerprint(
                                "Research opportunity",
                            ),
                            content_fingerprint=build_reconciliation_fingerprint(
                                "Dear Professor,\n\nBody",
                            ),
                            status=EmailDeliveryAttemptStatus.FAILED.value,
                            started_at=sent_at,
                        ),
                        EmailLog(
                            delivery_attempt_id=failed_key,
                            email_task_id=211,
                            identity_id=1,
                            llm_profile_id=7,
                            professor_id=2,
                            direction=EmailDirection.SENT.value,
                            subject="Research opportunity",
                            content="Dear Professor,\n\nBody",
                            failure_summary="SMTP rejected",
                            ingest_source="system",
                            created_at=sent_at,
                        ),
                    ],
                )
                await session.flush()
                result = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at + timedelta(seconds=1),
                        message_id="<manual-after-failure@example.edu>",
                    ),
                )
                await session.commit()
                count = await session.scalar(select(func.count()).select_from(EmailLog))
                return (
                    int(count or 0),
                    result.resolution,
                    result.observation.candidate_email_log_id,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (2, EmailObservationResolution.EXTERNAL.value, None),
        )

    def test_provider_without_sent_folder_copy_keeps_system_log_authoritative(
        self,
    ) -> None:
        delivery_key = str(uuid.uuid4())
        sent_at = datetime(2026, 8, 11, 18, 0, tzinfo=UTC)

        async def scenario() -> tuple[int, int, str]:
            async with self.session_factory() as session:
                session.add_all(
                    [
                        EmailDeliveryAttempt(
                            id=delivery_key,
                            email_task_id=220,
                            identity_id=1,
                            professor_id=2,
                            attempt_number=1,
                            recipient_email="teacher@example.edu",
                            subject_fingerprint=build_reconciliation_fingerprint(
                                "Subject"
                            ),
                            content_fingerprint=build_reconciliation_fingerprint(
                                "Body"
                            ),
                            status=EmailDeliveryAttemptStatus.ACCEPTED.value,
                            started_at=sent_at,
                        ),
                        EmailLog(
                            delivery_attempt_id=delivery_key,
                            email_task_id=220,
                            identity_id=1,
                            llm_profile_id=7,
                            professor_id=2,
                            direction=EmailDirection.SENT.value,
                            subject="Subject",
                            content="Body",
                            ingest_source="system",
                            created_at=sent_at,
                        ),
                    ],
                )
                await session.commit()
                log_count = await session.scalar(
                    select(func.count()).select_from(EmailLog)
                )
                observation_count = await session.scalar(
                    select(func.count()).select_from(EmailObservation),
                )
                attempt = await session.get(EmailDeliveryAttempt, delivery_key)
                return int(log_count or 0), int(observation_count or 0), attempt.status

        self.assertEqual(
            self._run_async(scenario()),
            (1, 0, EmailDeliveryAttemptStatus.ACCEPTED.value),
        )

    def test_shared_recipient_address_does_not_create_log_for_wrong_professor(
        self,
    ) -> None:
        delivery_key = str(uuid.uuid4())
        sent_at = datetime(2026, 8, 11, 19, 0, tzinfo=UTC)

        async def scenario() -> tuple[int, str, int | None]:
            async with self.session_factory() as session:
                session.add_all(
                    [
                        EmailDeliveryAttempt(
                            id=delivery_key,
                            email_task_id=230,
                            identity_id=1,
                            professor_id=2,
                            attempt_number=1,
                            recipient_email="shared@example.edu",
                            subject_fingerprint=build_reconciliation_fingerprint(
                                "Research opportunity",
                            ),
                            content_fingerprint=build_reconciliation_fingerprint(
                                "Dear Professor,\n\nBody",
                            ),
                            status=EmailDeliveryAttemptStatus.ACCEPTED.value,
                            started_at=sent_at,
                        ),
                        EmailLog(
                            delivery_attempt_id=delivery_key,
                            email_task_id=230,
                            identity_id=1,
                            llm_profile_id=7,
                            professor_id=2,
                            direction=EmailDirection.SENT.value,
                            subject="Research opportunity",
                            content="Dear Professor,\n\nBody",
                            ingest_source="system",
                            created_at=sent_at,
                        ),
                    ],
                )
                await session.flush()
                result = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at,
                        message_id="<provider-shared@example.edu>",
                        professor_id=3,
                        to_emails=["shared@example.edu"],
                    ),
                )
                await session.commit()
                count = await session.scalar(select(func.count()).select_from(EmailLog))
                return (
                    int(count or 0),
                    result.resolution,
                    result.observation.email_log_id,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (2, EmailObservationResolution.EXTERNAL.value, 2),
        )

    def test_nearby_external_webmail_with_different_subject_remains_canonical(
        self,
    ) -> None:
        sent_at = datetime(2026, 8, 11, 20, 0, tzinfo=UTC)

        async def scenario() -> tuple[int, str]:
            async with self.session_factory() as session:
                session.add(
                    EmailLog(
                        email_task_id=240,
                        identity_id=1,
                        llm_profile_id=7,
                        professor_id=2,
                        direction=EmailDirection.SENT.value,
                        subject="Application message",
                        content="Application body",
                        ingest_source="system",
                        created_at=sent_at,
                    ),
                )
                await session.flush()
                result = await ingest_sent_email_observation(
                    session,
                    self._record(
                        sent_at=sent_at + timedelta(seconds=10),
                        message_id="<external-nearby@example.edu>",
                        subject="Independent webmail message",
                        content="Different body",
                    ),
                )
                await session.commit()
                count = await session.scalar(select(func.count()).select_from(EmailLog))
                return int(count or 0), result.resolution

        self.assertEqual(
            self._run_async(scenario()),
            (2, EmailObservationResolution.EXTERNAL.value),
        )

    def test_missing_message_id_folder_copy_remains_separate_without_strong_evidence(
        self,
    ) -> None:
        sent_at = datetime(2026, 8, 11, 21, 0, tzinfo=UTC)

        async def scenario() -> tuple[int, list[str], int | None]:
            async with self.session_factory() as session:
                first = await ingest_sent_email_observation(
                    session,
                    self._record(sent_at=sent_at, message_id=None),
                )
                copied = self._record(
                    sent_at=sent_at,
                    message_id=None,
                    uid=99,
                    uidvalidity=999,
                )
                copied = EmailLogIngestRecord(
                    **{
                        **copied.__dict__,
                        "folder": "Archive/Sent",
                    },
                )
                second = await ingest_sent_email_observation(session, copied)
                await session.commit()
                count = await session.scalar(select(func.count()).select_from(EmailLog))
                return (
                    int(count or 0),
                    [first.resolution, second.resolution],
                    second.observation.candidate_email_log_id,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                2,
                [
                    EmailObservationResolution.EXTERNAL.value,
                    EmailObservationResolution.EXTERNAL.value,
                ],
                None,
            ),
        )

    def test_pending_and_merged_legacy_rows_are_excluded_from_all_counts(self) -> None:
        sent_at = datetime(2026, 8, 11, 22, 0, tzinfo=UTC)

        async def scenario() -> tuple[int, int, int]:
            async with self.session_factory() as session:
                session.add(
                    Professor(id=2, name="Teacher", email="teacher@example.edu")
                )
                session.add_all(
                    [
                        EmailLog(
                            identity_id=1,
                            professor_id=2,
                            direction=EmailDirection.SENT.value,
                            subject="Subject",
                            content="Body",
                            rfc_message_id="<app@example.edu>",
                            record_state=EmailLogRecordState.CANONICAL.value,
                            ingest_source="system",
                            created_at=sent_at,
                        ),
                        EmailLog(
                            identity_id=1,
                            professor_id=2,
                            direction=EmailDirection.SENT.value,
                            subject="Subject",
                            content="Body",
                            rfc_message_id="<provider-pending@example.edu>",
                            record_state=EmailLogRecordState.PENDING.value,
                            ingest_source="imap",
                            created_at=sent_at,
                        ),
                        EmailLog(
                            identity_id=1,
                            professor_id=2,
                            direction=EmailDirection.SENT.value,
                            subject="Subject",
                            content="Body",
                            rfc_message_id="<provider-merged@example.edu>",
                            record_state=EmailLogRecordState.MERGED.value,
                            ingest_source="imap",
                            created_at=sent_at,
                        ),
                    ],
                )
                await session.commit()

                events = await load_communication_events(
                    session,
                    identity_ids=(1,),
                    professor_ids=(2,),
                    include_source_identities=False,
                    include_professors=False,
                )
                statuses = await build_contact_status_by_professor(
                    session,
                    identity_id=1,
                    professor_ids=[2],
                )
                expressions = _dashboard_summary_expressions(
                    identity_id=1,
                    communication_identity_ids=(1,),
                    match_source_identity_id=1,
                )
                statement = _join_dashboard_summaries(
                    select(
                        Professor.id,
                        expressions["sent_count"].label("sent_count"),
                    ),
                    expressions["joins"],
                ).where(Professor.id == 2)
                dashboard_sent_count = (
                    (await session.execute(statement)).one().sent_count
                )
                return (
                    len(events),
                    statuses[2].sent_count,
                    int(dashboard_sent_count or 0),
                )

        self.assertEqual(self._run_async(scenario()), (1, 1, 1))
