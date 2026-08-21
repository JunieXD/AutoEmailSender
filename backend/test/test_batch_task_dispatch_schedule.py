from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    BatchTask,
    BatchTaskStatus,
    EmailDeliveryAttempt,
    EmailDeliveryAttemptStatus,
    EmailLog,
    EmailObservation,
    EmailTask,
    EmailTaskCancellationReason,
    EmailTaskSource,
    EmailTaskStatus,
    IdentityProfile,
    LLMProfile,
    Professor,
)
from app.modules.communications.transport import MailRuntimeError, SendMailResult
from app.modules.communications.ingestion import (
    EmailLogIngestRecord,
    build_reconciliation_fingerprint,
    ingest_sent_email_observation,
)
from test.schema_database import create_schema_sqlite_database
from app.modules.workspace.tasks.delivery import (
    dispatch_due_tasks_once,
    dispatch_email_task,
)
from app.modules.workspace.tasks.runtime import (
    approve_and_send_task,
    approve_draft_task,
)
from app.modules.workspace.tasks.schemas import EmailTaskApprovalRequest


class BatchTaskDispatchScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "dispatch_schedule_test.db"
        create_schema_sqlite_database(self.db_path)
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_path.as_posix()}",
            future=True,
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )
        self._run_async(self._create_schema())

    def tearDown(self) -> None:
        self._run_async(self.engine.dispose())
        self.temp_dir.cleanup()

    def test_dispatch_due_tasks_skips_batch_task_on_unselected_date(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-06"],
                emails_per_window=20,
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 5, 10, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 0)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)
        mocked_send.assert_not_called()

    def test_dispatch_due_tasks_skips_batch_task_outside_time_window(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 8, 59, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 0)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)
        mocked_send.assert_not_called()

    def test_dispatch_due_tasks_skips_when_daily_limit_reached(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=1,
                existing_sent_at=datetime(2026, 5, 4, 9, 30, tzinfo=UTC),
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 0)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)
        mocked_send.assert_not_called()

    def test_dispatch_due_tasks_dispatches_on_selected_date_inside_window(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 1)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.SENT.value)
        mocked_send.assert_awaited_once()
        delivery_key = mocked_send.await_args.kwargs["delivery_key"]
        self.assertEqual(
            self._run_async(self._get_delivery_reconciliation_state(task_id)),
            (
                delivery_key,
                EmailDeliveryAttemptStatus.ACCEPTED.value,
                delivery_key,
                0,
            ),
        )

    def test_dispatch_due_tasks_reserves_identity_send_window(self) -> None:
        first_task_id, second_task_id = self._run_async(
            self._create_batch_task_with_multiple_approved_tasks(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
                task_count=2,
            ),
        )
        now = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    limit=10,
                    now=now,
                    local_timezone=UTC,
                ),
            )

        statuses = {
            first_task_id: self._run_async(self._get_task_status(first_task_id)),
            second_task_id: self._run_async(self._get_task_status(second_task_id)),
        }
        self.assertEqual(processed, 1)
        self.assertEqual(list(statuses.values()).count(EmailTaskStatus.SENT.value), 1)
        self.assertEqual(list(statuses.values()).count(EmailTaskStatus.APPROVED.value), 1)
        next_send_after = self._run_async(self._get_identity_next_send_after_by_task_id(first_task_id))
        self.assertIsNotNone(next_send_after)
        assert next_send_after is not None
        self.assertGreaterEqual(next_send_after, now + timedelta(seconds=1))
        self.assertLessEqual(next_send_after, now + timedelta(seconds=5))
        mocked_send.assert_awaited_once()

    def test_dispatch_due_tasks_does_not_starve_later_identities(self) -> None:
        first_identity_task_ids, second_identity_task_id = self._run_async(
            self._create_many_batch_tasks_for_one_identity_then_one_for_another(),
        )
        now = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    limit=10,
                    now=now,
                    local_timezone=UTC,
                ),
            )

        first_identity_statuses = [
            self._run_async(self._get_task_status(task_id))
            for task_id in first_identity_task_ids
        ]
        self.assertEqual(processed, 2)
        self.assertEqual(first_identity_statuses.count(EmailTaskStatus.SENT.value), 1)
        self.assertEqual(self._run_async(self._get_task_status(second_identity_task_id)), EmailTaskStatus.SENT.value)
        self.assertEqual(mocked_send.await_count, 2)

    def test_dispatch_due_tasks_skips_identity_before_next_send_after(self) -> None:
        task_id = self._run_async(self._create_manual_approved_task())
        now = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        self._run_async(
            self._set_identity_next_send_after_by_task_id(
                task_id,
                now + timedelta(seconds=2),
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    limit=10,
                    now=now,
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 0)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)
        mocked_send.assert_not_awaited()

    def test_dispatch_due_tasks_can_count_identity_window_deferral_for_runtime_wakeup(self) -> None:
        task_id = self._run_async(self._create_manual_approved_task())
        now = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        self._run_async(
            self._set_identity_next_send_after_by_task_id(
                task_id,
                now + timedelta(seconds=2),
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    limit=10,
                    now=now,
                    local_timezone=UTC,
                    count_identity_window_deferred=True,
                ),
            )

        self.assertEqual(processed, 1)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)
        mocked_send.assert_not_awaited()

    def test_dispatch_due_tasks_skips_batch_task_before_scheduled_at_inside_window(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )
        self._run_async(
            self._set_task_scheduled_at(
                task_id,
                datetime(2026, 5, 4, 10, 30, tzinfo=UTC),
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 0)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)
        mocked_send.assert_not_called()

    def test_approve_draft_preserves_scheduled_batch_plan(self) -> None:
        scheduled_date = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
        task_id = self._run_async(
            self._create_batch_task_with_review_required_task(
                scheduled_dates=[scheduled_date],
                emails_per_window=20,
            ),
        )
        scheduled_at = datetime.fromisoformat(f"{scheduled_date}T10:30:00+00:00")
        self._run_async(self._set_task_scheduled_at(task_id, scheduled_at))

        self._run_async(
            approve_draft_task(
                self.session_factory,
                task_id,
                EmailTaskApprovalRequest(
                    subject="申请交流",
                    body_text="老师您好。",
                    body_html=None,
                    selected_material_ids=[],
                ),
            ),
        )

        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.SCHEDULED.value)
        actual_scheduled_at = self._run_async(self._get_task_scheduled_at(task_id))
        self.assertIsNotNone(actual_scheduled_at)
        self.assertEqual(actual_scheduled_at.replace(tzinfo=UTC), scheduled_at)

    def test_approve_draft_rejects_generating_draft_task(self) -> None:
        scheduled_date = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
        task_id = self._run_async(
            self._create_batch_task_with_review_required_task(
                scheduled_dates=[scheduled_date],
                emails_per_window=20,
            ),
        )
        self._run_async(self._set_task_status(task_id, EmailTaskStatus.GENERATING_DRAFT.value))

        with self.assertRaisesRegex(ValueError, "AI 正在改写当前草稿"):
            self._run_async(
                approve_draft_task(
                    self.session_factory,
                    task_id,
                    EmailTaskApprovalRequest(
                        subject="申请交流",
                        body_text="老师您好。",
                        body_html=None,
                        selected_material_ids=[],
                    ),
                ),
            )

        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.GENERATING_DRAFT.value)

    def test_approve_and_send_rejects_user_removed_task_without_sending(self) -> None:
        scheduled_date = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
        task_id = self._run_async(
            self._create_batch_task_with_review_required_task(
                scheduled_dates=[scheduled_date],
                emails_per_window=20,
            ),
        )
        self._run_async(self._mark_task_user_removed(task_id))

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            with self.assertRaisesRegex(ValueError, "已从批量任务中移除"):
                self._run_async(
                    approve_and_send_task(
                        self.session_factory,
                        task_id,
                        EmailTaskApprovalRequest(
                            subject="申请交流",
                            body_text="老师您好。",
                            body_html=None,
                            selected_material_ids=[],
                        ),
                    ),
                )

        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.CANCELED.value)
        self.assertEqual(
            self._run_async(self._get_task_cancellation_reason(task_id)),
            EmailTaskCancellationReason.USER_REMOVED.value,
        )
        mocked_send.assert_not_awaited()

    def test_dispatch_email_task_skips_future_scheduled_task(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )
        self._run_async(
            self._set_task_scheduled_at(
                task_id,
                datetime.now(UTC) + timedelta(hours=1),
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            self._run_async(dispatch_email_task(self.session_factory, task_id))

        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)
        mocked_send.assert_not_awaited()

    def test_dispatch_email_task_claim_skips_task_rescheduled_during_claim(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )
        reschedule_once = True

        def reschedule_before_claim(conn, _cursor, statement, _parameters, _context, _executemany):
            nonlocal reschedule_once
            if not reschedule_once:
                return
            if not statement.lstrip().upper().startswith("UPDATE EMAIL_TASKS"):
                return
            reschedule_once = False
            conn.exec_driver_sql(
                "UPDATE email_tasks SET scheduled_at = ? WHERE id = ?",
                ((datetime.now(UTC) + timedelta(hours=1)).isoformat(), task_id),
            )

        event.listen(self.engine.sync_engine, "before_cursor_execute", reschedule_before_claim)
        try:
            with patch(
                "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
                AsyncMock(return_value=self._build_send_result()),
            ) as mocked_send:
                self._run_async(dispatch_email_task(self.session_factory, task_id))
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", reschedule_before_claim)

        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)
        mocked_send.assert_not_awaited()

    def test_dispatch_email_task_claim_skips_task_canceled_during_claim(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )
        cancel_once = True

        def cancel_before_claim(conn, _cursor, statement, _parameters, _context, _executemany):
            nonlocal cancel_once
            if not cancel_once:
                return
            normalized_statement = statement.lstrip().upper()
            if not normalized_statement.startswith("UPDATE IDENTITY_PROFILES"):
                return
            cancel_once = False
            connection = sqlite3.connect(self.db_path)
            try:
                connection.execute(
                    "UPDATE email_tasks SET batch_send_canceled_at = ? WHERE id = ?",
                    (datetime.now(UTC).isoformat(), task_id),
                )
                connection.commit()
            finally:
                connection.close()

        event.listen(self.engine.sync_engine, "before_cursor_execute", cancel_before_claim)
        try:
            with patch(
                "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
                AsyncMock(return_value=self._build_send_result()),
            ) as mocked_send:
                sent = self._run_async(
                    dispatch_email_task(self.session_factory, task_id),
                )
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", cancel_before_claim)

        self.assertFalse(sent)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)
        self.assertIsNotNone(
            self._run_async(self._get_task_batch_send_canceled_at(task_id)),
        )
        mocked_send.assert_not_awaited()

    def test_approve_and_send_explicitly_bypasses_future_scheduled_plan(self) -> None:
        scheduled_date = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
        task_id = self._run_async(
            self._create_batch_task_with_review_required_task(
                scheduled_dates=[scheduled_date],
                emails_per_window=20,
            ),
        )
        self._run_async(
            self._set_task_scheduled_at(
                task_id,
                datetime.fromisoformat(f"{scheduled_date}T10:30:00+00:00"),
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            self._run_async(
                approve_and_send_task(
                    self.session_factory,
                    task_id,
                    EmailTaskApprovalRequest(
                        subject="申请交流",
                        body_text="老师您好。",
                        body_html=None,
                        selected_material_ids=[],
                    ),
                ),
            )

        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.SENT.value)
        self.assertIsNone(self._run_async(self._get_task_scheduled_at(task_id)))
        mocked_send.assert_awaited_once()

    def test_approve_and_send_bypasses_identity_send_window(self) -> None:
        scheduled_date = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
        task_id = self._run_async(
            self._create_batch_task_with_review_required_task(
                scheduled_dates=[scheduled_date],
                emails_per_window=20,
            ),
        )
        now = datetime.now(UTC)
        self._run_async(
            self._set_identity_next_send_after_by_task_id(
                task_id,
                now + timedelta(seconds=3),
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            self._run_async(
                approve_and_send_task(
                    self.session_factory,
                    task_id,
                    EmailTaskApprovalRequest(
                        subject="申请交流",
                        body_text="老师您好。",
                        body_html=None,
                        selected_material_ids=[],
                    ),
                ),
            )

        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.SENT.value)
        mocked_send.assert_awaited_once()

    def test_dispatch_email_task_skips_task_no_longer_dispatchable(self) -> None:
        task_id = self._run_async(self._create_manual_approved_task())
        self._run_async(self._set_task_status(task_id, EmailTaskStatus.REVIEW_REQUIRED.value))

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            self._run_async(dispatch_email_task(self.session_factory, task_id))

        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.REVIEW_REQUIRED.value)
        mocked_send.assert_not_awaited()

    def test_dispatch_email_task_claims_task_before_sending(self) -> None:
        task_id = self._run_async(self._create_manual_approved_task())

        async def delayed_send(**_kwargs):
            await asyncio.sleep(0.05)
            return self._build_send_result()

        async def dispatch_twice() -> None:
            await asyncio.gather(
                dispatch_email_task(self.session_factory, task_id),
                dispatch_email_task(self.session_factory, task_id),
            )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(side_effect=delayed_send),
        ) as mocked_send:
            self._run_async(dispatch_twice())

        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.SENT.value)
        mocked_send.assert_awaited_once()

    def test_failed_send_retry_creates_two_distinct_attempts(self) -> None:
        task_id = self._run_async(self._create_manual_approved_task())

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(
                side_effect=[
                    MailRuntimeError("SMTP rejected"),
                    self._build_send_result(),
                ],
            ),
        ) as mocked_send:
            self._run_async(
                dispatch_email_task(
                    self.session_factory,
                    task_id,
                    respect_identity_send_window=False,
                ),
            )
            self.assertEqual(
                self._run_async(self._get_task_status(task_id)),
                EmailTaskStatus.SEND_FAILED.value,
            )
            self._run_async(self._set_task_status(task_id, EmailTaskStatus.APPROVED.value))
            self._run_async(
                dispatch_email_task(
                    self.session_factory,
                    task_id,
                    respect_identity_send_window=False,
                ),
            )

        attempt_ids = [
            call.kwargs["delivery_key"]
            for call in mocked_send.await_args_list
        ]
        self.assertNotEqual(attempt_ids[0], attempt_ids[1])
        self.assertEqual(
            self._run_async(self._get_attempt_statuses(task_id)),
            [
                (attempt_ids[0], EmailDeliveryAttemptStatus.FAILED.value),
                (attempt_ids[1], EmailDeliveryAttemptStatus.ACCEPTED.value),
            ],
        )
        self.assertEqual(self._run_async(self._get_email_log_count(task_id)), 2)
        self.assertEqual(
            self._run_async(self._get_task_status(task_id)),
            EmailTaskStatus.SENT.value,
        )

    def test_retry_uses_next_free_attempt_number_after_legacy_backfill(self) -> None:
        task_id = self._run_async(self._create_manual_approved_task())

        async def seed_legacy_attempt() -> None:
            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_id)
                assert task is not None
                professor = await session.get(Professor, task.professor_id)
                assert professor is not None
                task.retry_count = 2
                session.add(
                    EmailDeliveryAttempt(
                        id="34bbd1d5-ae60-4a09-95af-6f90bf33ddad",
                        email_task_id=task.id,
                        identity_id=task.identity_id,
                        professor_id=task.professor_id,
                        attempt_number=3,
                        recipient_email=professor.email or "",
                        subject_fingerprint=build_reconciliation_fingerprint(
                            task.approved_subject,
                        ),
                        content_fingerprint=build_reconciliation_fingerprint(
                            task.approved_body_text,
                        ),
                        status=EmailDeliveryAttemptStatus.UNKNOWN.value,
                        started_at=datetime(2026, 5, 4, 8, 0, tzinfo=UTC),
                    ),
                )
                await session.commit()

        self._run_async(seed_legacy_attempt())
        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ):
            self._run_async(
                dispatch_email_task(
                    self.session_factory,
                    task_id,
                    respect_identity_send_window=False,
                ),
            )

        self.assertEqual(self._run_async(self._get_attempt_numbers(task_id)), [3, 4])
        self.assertEqual(
            self._run_async(self._get_task_status(task_id)),
            EmailTaskStatus.SENT.value,
        )

    def test_sent_observation_wins_when_transport_reports_an_error_after_delivery(self) -> None:
        task_id = self._run_async(self._create_manual_approved_task())

        async def sent_copy_then_transport_error(**kwargs):
            identity = kwargs["identity"]
            professor = kwargs["professor"]
            delivery_key = kwargs["delivery_key"]
            sent_at = datetime.now(UTC)
            async with self.session_factory() as session:
                await ingest_sent_email_observation(
                    session,
                    EmailLogIngestRecord(
                        email_task_id=None,
                        identity_id=identity.id,
                        llm_profile_id=None,
                        professor_id=professor.id,
                        direction="sent",
                        subject=kwargs["subject"],
                        content=kwargs["body_text"],
                        content_html=kwargs["body_html"],
                        message_id="<provider-confirmed@example.edu>",
                        from_email=identity.email_address,
                        to_emails=[professor.email],
                        cc_emails=None,
                        bcc_emails=None,
                        created_at=sent_at,
                        ingest_source="imap",
                        folder_role="sent",
                        folder="Sent",
                        uidvalidity=777,
                        imap_uid=901,
                        provider_payload=None,
                        reply_headers={
                            "x-autoemailsender-delivery-id": delivery_key,
                        },
                        delivery_key=delivery_key,
                    ),
                )
                await session.commit()
            raise MailRuntimeError("connection dropped after send")

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(side_effect=sent_copy_then_transport_error),
        ):
            self._run_async(
                dispatch_email_task(
                    self.session_factory,
                    task_id,
                    respect_identity_send_window=False,
                ),
            )

        async def load_state() -> tuple[str, str, int, str, str | None]:
            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_id)
                attempt = await session.scalar(
                    select(EmailDeliveryAttempt).where(
                        EmailDeliveryAttempt.email_task_id == task_id,
                    ),
                )
                assert task is not None
                assert attempt is not None
                logs = list(
                    await session.scalars(
                        select(EmailLog).where(EmailLog.email_task_id == task_id),
                    ),
                )
                observation = await session.scalar(
                    select(EmailObservation).where(
                        EmailObservation.delivery_attempt_id == attempt.id,
                    ),
                )
                assert observation is not None
                return (
                    task.status,
                    attempt.status,
                    len(logs),
                    observation.resolution,
                    logs[0].failure_summary,
                )

        self.assertEqual(
            self._run_async(load_state()),
            (
                EmailTaskStatus.SENT.value,
                EmailDeliveryAttemptStatus.ACCEPTED.value,
                1,
                "matched",
                None,
            ),
        )

    def test_dispatch_due_tasks_recovers_stale_sending_task(self) -> None:
        task_id = self._run_async(self._create_manual_approved_task())
        now = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        self._run_async(self._set_task_sending(task_id, now - timedelta(minutes=45)))
        stale_attempt_id = self._run_async(
            self._add_prepared_attempt(
                task_id,
                started_at=now - timedelta(minutes=45),
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=now,
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 1)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.SENT.value)
        mocked_send.assert_awaited_once()
        self.assertEqual(
            self._run_async(self._get_attempt_statuses(task_id)),
            [
                (stale_attempt_id, EmailDeliveryAttemptStatus.UNKNOWN.value),
                (
                    mocked_send.await_args.kwargs["delivery_key"],
                    EmailDeliveryAttemptStatus.ACCEPTED.value,
                ),
            ],
        )

    def test_dispatch_due_tasks_keeps_recent_sending_task_claimed(self) -> None:
        task_id = self._run_async(self._create_manual_approved_task())
        now = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        self._run_async(self._set_task_sending(task_id, now - timedelta(minutes=5)))

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=now,
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 0)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.SENDING.value)
        mocked_send.assert_not_awaited()

    def test_dispatch_due_tasks_does_not_let_blocked_scheduled_task_consume_limit(self) -> None:
        blocked_task_id, dispatchable_task_id = self._run_async(
            self._create_blocked_scheduled_task_before_dispatchable_task(),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    limit=1,
                    now=datetime(2026, 5, 3, 10, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 1)
        self.assertEqual(
            self._run_async(self._get_task_status(blocked_task_id)),
            EmailTaskStatus.APPROVED.value,
        )
        self.assertEqual(
            self._run_async(self._get_task_status(dispatchable_task_id)),
            EmailTaskStatus.SENT.value,
        )
        mocked_send.assert_awaited_once()

    def test_dispatch_due_tasks_expires_batch_after_last_window(self) -> None:
        first_task_id, second_task_id = self._run_async(
            self._create_batch_task_with_multiple_approved_tasks(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
                task_count=2,
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 18, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 0)
        mocked_send.assert_not_called()
        self.assertEqual(
            self._run_async(self._get_batch_task_status_by_email_task_id(first_task_id)),
            BatchTaskStatus.EXPIRED.value,
        )
        self.assertEqual(self._run_async(self._get_task_status(first_task_id)), EmailTaskStatus.CANCELED.value)
        self.assertEqual(self._run_async(self._get_task_status(second_task_id)), EmailTaskStatus.CANCELED.value)
        self.assertEqual(
            self._run_async(self._get_task_cancellation_reason(first_task_id)),
            EmailTaskCancellationReason.SCHEDULE_EXPIRED.value,
        )

    def test_dispatch_due_tasks_sends_window_scheduled_item_during_grace_period(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )
        self._run_async(
            self._set_task_scheduled_at(
                task_id,
                datetime(2026, 5, 4, 17, 59, 50, tzinfo=UTC),
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 18, 0, 5, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 1)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.SENT.value)
        self.assertEqual(
            self._run_async(self._get_batch_task_status_by_email_task_id(task_id)),
            BatchTaskStatus.RUNNING.value,
        )
        mocked_send.assert_awaited_once()

    def test_dispatch_due_tasks_expires_window_scheduled_item_after_grace_period(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )
        self._run_async(
            self._set_task_scheduled_at(
                task_id,
                datetime(2026, 5, 4, 17, 59, 50, tzinfo=UTC),
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 18, 2, 1, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 0)
        mocked_send.assert_not_called()
        self.assertEqual(
            self._run_async(self._get_batch_task_status_by_email_task_id(task_id)),
            BatchTaskStatus.EXPIRED.value,
        )
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.CANCELED.value)
        self.assertEqual(
            self._run_async(self._get_task_cancellation_reason(task_id)),
            EmailTaskCancellationReason.SCHEDULE_EXPIRED.value,
        )

    def test_dispatch_due_tasks_does_not_grace_stale_window_scheduled_item(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )
        self._run_async(
            self._set_task_scheduled_at(
                task_id,
                datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 18, 0, 5, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 0)
        mocked_send.assert_not_called()
        self.assertEqual(
            self._run_async(self._get_batch_task_status_by_email_task_id(task_id)),
            BatchTaskStatus.EXPIRED.value,
        )
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.CANCELED.value)
        self.assertEqual(
            self._run_async(self._get_task_cancellation_reason(task_id)),
            EmailTaskCancellationReason.SCHEDULE_EXPIRED.value,
        )

    def test_dispatch_due_tasks_expires_batch_without_dispatchable_items(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_review_required_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 18, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 0)
        mocked_send.assert_not_called()
        self.assertEqual(
            self._run_async(self._get_batch_task_status_by_email_task_id(task_id)),
            BatchTaskStatus.EXPIRED.value,
        )
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.CANCELED.value)
        self.assertEqual(
            self._run_async(self._get_task_cancellation_reason(task_id)),
            EmailTaskCancellationReason.SCHEDULE_EXPIRED.value,
        )

    def test_dispatch_due_tasks_keeps_batch_running_when_future_window_exists(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04", "2026-05-05"],
                emails_per_window=20,
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 18, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 0)
        mocked_send.assert_not_called()
        self.assertEqual(
            self._run_async(self._get_batch_task_status_by_email_task_id(task_id)),
            BatchTaskStatus.RUNNING.value,
        )
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)

    def test_dispatch_due_tasks_keeps_future_scheduled_items_after_window_end(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )
        self._run_async(
            self._set_task_scheduled_at(
                task_id,
                datetime(2026, 5, 4, 19, 0, tzinfo=UTC),
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 18, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 0)
        mocked_send.assert_not_called()
        self.assertEqual(
            self._run_async(self._get_batch_task_status_by_email_task_id(task_id)),
            BatchTaskStatus.RUNNING.value,
        )
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)

    def test_canceled_item_stays_recoverable_until_original_time_then_completes(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )
        scheduled_at = datetime(2026, 5, 4, 10, 30, tzinfo=UTC)
        self._run_async(self._set_task_scheduled_at(task_id, scheduled_at))
        self._run_async(
            self._set_task_batch_send_canceled_at(
                task_id,
                datetime(2026, 5, 4, 9, 0, tzinfo=UTC),
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            before_due = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )
            self.assertEqual(before_due, 0)
            self.assertEqual(
                self._run_async(self._get_batch_task_status_by_email_task_id(task_id)),
                BatchTaskStatus.RUNNING.value,
            )

            after_due = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 10, 31, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(after_due, 0)
        self.assertEqual(
            self._run_async(self._get_batch_task_status_by_email_task_id(task_id)),
            BatchTaskStatus.COMPLETED.value,
        )
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)
        self.assertIsNotNone(
            self._run_async(self._get_task_batch_send_canceled_at(task_id)),
        )
        mocked_send.assert_not_awaited()

    def test_expiring_batch_preserves_final_item_statuses(self) -> None:
        sent_task_id, failed_task_id, pending_task_id = self._run_async(
            self._create_batch_task_with_final_and_pending_tasks(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ):
            self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 18, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(self._run_async(self._get_task_status(sent_task_id)), EmailTaskStatus.SENT.value)
        self.assertEqual(self._run_async(self._get_task_status(failed_task_id)), EmailTaskStatus.SEND_FAILED.value)
        self.assertEqual(self._run_async(self._get_task_status(pending_task_id)), EmailTaskStatus.CANCELED.value)

    def test_dispatch_due_tasks_uses_local_timezone_for_scheduled_window(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 1, 30, tzinfo=UTC),
                    local_timezone=ZoneInfo("Asia/Shanghai"),
                ),
            )

        self.assertEqual(processed, 1)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.SENT.value)
        mocked_send.assert_awaited_once()

    def test_dispatch_due_tasks_counts_daily_limit_by_local_date(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=1,
                existing_sent_at=datetime(2026, 5, 3, 16, 30, tzinfo=UTC),
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 1, 30, tzinfo=UTC),
                    local_timezone=ZoneInfo("Asia/Shanghai"),
                ),
            )

        self.assertEqual(processed, 0)
        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)
        mocked_send.assert_not_called()

    def test_dispatch_due_tasks_counts_selected_tasks_toward_daily_limit_in_same_run(self) -> None:
        first_task_id, second_task_id = self._run_async(
            self._create_batch_task_with_multiple_approved_tasks(
                scheduled_dates=["2026-05-04"],
                emails_per_window=1,
                task_count=2,
            ),
        )

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    limit=10,
                    now=datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        statuses = {
            first_task_id: self._run_async(self._get_task_status(first_task_id)),
            second_task_id: self._run_async(self._get_task_status(second_task_id)),
        }
        self.assertEqual(processed, 1)
        self.assertEqual(list(statuses.values()).count(EmailTaskStatus.SENT.value), 1)
        self.assertEqual(list(statuses.values()).count(EmailTaskStatus.APPROVED.value), 1)
        mocked_send.assert_awaited_once()

    def test_dispatch_due_tasks_ignores_deleted_batch_task(self) -> None:
        approved_task_id = self._run_async(
            self._create_batch_task_with_approved_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )
        self._run_async(self._mark_batch_task_deleted_by_email_task_id(approved_task_id))

        with patch(
            "app.modules.workspace.tasks.delivery.mail_runtime.send_email",
            AsyncMock(return_value=self._build_send_result()),
        ) as mocked_send:
            processed = self._run_async(
                dispatch_due_tasks_once(
                    self.session_factory,
                    now=datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
                    local_timezone=UTC,
                ),
            )

        self.assertEqual(processed, 0)
        self.assertEqual(self._run_async(self._get_task_status(approved_task_id)), EmailTaskStatus.APPROVED.value)
        mocked_send.assert_not_called()

    async def _create_schema(self) -> None:
        return None

    async def _create_batch_task_with_approved_task(
        self,
        *,
        scheduled_dates: list[str],
        emails_per_window: int,
        existing_sent_at: datetime | None = None,
    ) -> int:
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="测试身份",
                profile_name="测试身份",
                sender_name="王同学",
                email_address=f"sender-{datetime.now(UTC).timestamp()}@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="template",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="老师您好，我是{{sender_name}}。",
                is_default=True,
            )
            llm_profile = LLMProfile(
                name=f"默认模型-{datetime.now(UTC).timestamp()}",
                provider="openai",
                api_base_url="https://api.example.com/v1",
                api_key="sk-test-key",
                model_name="gpt-test",
                is_default=True,
            )
            professor = Professor(
                name="张教授",
                email=f"professor-{datetime.now(UTC).timestamp()}@example.edu",
                title="Professor",
                university="Example University",
                school="School of AI",
                department="Computer Science",
                research_direction="Large language models",
                recent_papers=[],
            )
            batch_task = BatchTask(
                identity=identity,
                llm_profile=llm_profile,
                name="定时批量任务",
                schedule_type="scheduled",
                window_start_time="09:00",
                window_end_time="18:00",
                emails_per_window=emails_per_window,
                scheduled_dates=scheduled_dates,
                status=BatchTaskStatus.RUNNING.value,
                target_count=1,
            )
            approved_task = self._build_email_task(
                batch_task=batch_task,
                identity=identity,
                llm_profile=llm_profile,
                professor=professor,
                status=EmailTaskStatus.APPROVED.value,
            )
            session.add_all([batch_task, approved_task])

            if existing_sent_at is not None:
                session.add(
                    self._build_email_task(
                        batch_task=batch_task,
                        identity=identity,
                        llm_profile=llm_profile,
                        professor=professor,
                        status=EmailTaskStatus.SENT.value,
                        sent_at=existing_sent_at,
                    ),
                )

            await session.commit()
            return approved_task.id

    async def _create_batch_task_with_multiple_approved_tasks(
        self,
        *,
        scheduled_dates: list[str],
        emails_per_window: int,
        task_count: int,
    ) -> tuple[int, ...]:
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="测试身份",
                profile_name="测试身份",
                sender_name="王同学",
                email_address=f"sender-multiple-{datetime.now(UTC).timestamp()}@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="template",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="老师您好，我是{{sender_name}}。",
                is_default=True,
            )
            llm_profile = LLMProfile(
                name=f"默认模型-multiple-{datetime.now(UTC).timestamp()}",
                provider="openai",
                api_base_url="https://api.example.com/v1",
                api_key="sk-test-key",
                model_name="gpt-test",
                is_default=True,
            )
            batch_task = BatchTask(
                identity=identity,
                llm_profile=llm_profile,
                name="多任务定时批量任务",
                schedule_type="scheduled",
                window_start_time="09:00",
                window_end_time="18:00",
                emails_per_window=emails_per_window,
                scheduled_dates=scheduled_dates,
                status=BatchTaskStatus.RUNNING.value,
                target_count=task_count,
            )
            tasks = [
                self._build_email_task(
                    batch_task=batch_task,
                    identity=identity,
                    llm_profile=llm_profile,
                    professor=Professor(
                        name=f"张教授{index}",
                        email=f"multiple-{index}-{datetime.now(UTC).timestamp()}@example.edu",
                        title="Professor",
                        university="Example University",
                        school="School of AI",
                        department="Computer Science",
                        research_direction="Large language models",
                        recent_papers=[],
                    ),
                    status=EmailTaskStatus.APPROVED.value,
                    approved_at=datetime(2026, 5, 3, 10, index, tzinfo=UTC),
                )
                for index in range(task_count)
            ]
            session.add_all([batch_task, *tasks])
            await session.commit()
            return tuple(task.id for task in tasks)

    async def _create_many_batch_tasks_for_one_identity_then_one_for_another(self) -> tuple[tuple[int, ...], int]:
        async with self.session_factory() as session:
            llm_profile = LLMProfile(
                name=f"默认模型-two-identities-{datetime.now(UTC).timestamp()}",
                provider="openai",
                api_base_url="https://api.example.com/v1",
                api_key="sk-test-key",
                model_name="gpt-test",
                is_default=True,
            )
            first_identity = IdentityProfile(
                name="第一身份",
                profile_name="第一身份",
                sender_name="王同学",
                email_address=f"sender-first-{datetime.now(UTC).timestamp()}@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="first@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="template",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="老师您好，我是{{sender_name}}。",
                is_default=True,
            )
            second_identity = IdentityProfile(
                name="第二身份",
                profile_name="第二身份",
                sender_name="李同学",
                email_address=f"sender-second-{datetime.now(UTC).timestamp()}@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="second@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="template",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="老师您好，我是{{sender_name}}。",
            )
            first_batch_task = BatchTask(
                identity=first_identity,
                llm_profile=llm_profile,
                name="第一身份立即批量任务",
                schedule_type="immediate",
                status=BatchTaskStatus.RUNNING.value,
                target_count=10,
            )
            second_batch_task = BatchTask(
                identity=second_identity,
                llm_profile=llm_profile,
                name="第二身份立即批量任务",
                schedule_type="immediate",
                status=BatchTaskStatus.RUNNING.value,
                target_count=1,
            )
            first_tasks = [
                self._build_email_task(
                    batch_task=first_batch_task,
                    identity=first_identity,
                    llm_profile=llm_profile,
                    professor=Professor(
                        name=f"第一身份导师{index}",
                        email=f"first-identity-{index}-{datetime.now(UTC).timestamp()}@example.edu",
                        title="Professor",
                        university="Example University",
                        school="School of AI",
                        department="Computer Science",
                        research_direction="Large language models",
                        recent_papers=[],
                    ),
                    status=EmailTaskStatus.APPROVED.value,
                    approved_at=datetime(2026, 5, 3, 9, index, tzinfo=UTC),
                )
                for index in range(10)
            ]
            second_task = self._build_email_task(
                batch_task=second_batch_task,
                identity=second_identity,
                llm_profile=llm_profile,
                professor=Professor(
                    name="第二身份导师",
                    email=f"second-identity-{datetime.now(UTC).timestamp()}@example.edu",
                    title="Professor",
                    university="Example University",
                    school="School of AI",
                    department="Computer Science",
                    research_direction="Large language models",
                    recent_papers=[],
                ),
                status=EmailTaskStatus.APPROVED.value,
                approved_at=datetime(2026, 5, 3, 10, 0, tzinfo=UTC),
            )
            session.add_all([first_batch_task, second_batch_task, *first_tasks, second_task])
            await session.commit()
            return tuple(task.id for task in first_tasks), second_task.id

    async def _create_batch_task_with_review_required_task(
        self,
        *,
        scheduled_dates: list[str],
        emails_per_window: int,
    ) -> int:
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="测试身份",
                profile_name="测试身份",
                sender_name="王同学",
                email_address=f"sender-review-{datetime.now(UTC).timestamp()}@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="template",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="老师您好，我是{{sender_name}}。",
                is_default=True,
            )
            llm_profile = LLMProfile(
                name=f"默认模型-review-{datetime.now(UTC).timestamp()}",
                provider="openai",
                api_base_url="https://api.example.com/v1",
                api_key="sk-test-key",
                model_name="gpt-test",
                is_default=True,
            )
            batch_task = BatchTask(
                identity=identity,
                llm_profile=llm_profile,
                name="待审核定时批量任务",
                schedule_type="scheduled",
                window_start_time="09:00",
                window_end_time="18:00",
                emails_per_window=emails_per_window,
                scheduled_dates=scheduled_dates,
                status=BatchTaskStatus.RUNNING.value,
                target_count=1,
            )
            review_required_task = self._build_email_task(
                batch_task=batch_task,
                identity=identity,
                llm_profile=llm_profile,
                professor=Professor(
                    name="待审核导师",
                    email=f"review-{datetime.now(UTC).timestamp()}@example.edu",
                    title="Professor",
                    university="Example University",
                    school="School of AI",
                    department="Computer Science",
                    research_direction="Large language models",
                    recent_papers=[],
                ),
                status=EmailTaskStatus.REVIEW_REQUIRED.value,
            )
            session.add_all([batch_task, review_required_task])
            await session.commit()
            return review_required_task.id

    async def _mark_batch_task_deleted_by_email_task_id(self, email_task_id: int) -> None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, email_task_id)
            assert task is not None
            batch_task = await session.get(BatchTask, task.batch_task_id)
            assert batch_task is not None
            batch_task.deleted_at = datetime.now(UTC)
            await session.commit()

    async def _create_batch_task_with_final_and_pending_tasks(
        self,
        *,
        scheduled_dates: list[str],
        emails_per_window: int,
    ) -> tuple[int, int, int]:
        sent_task_id, failed_task_id, pending_task_id = await self._create_batch_task_with_multiple_approved_tasks(
            scheduled_dates=scheduled_dates,
            emails_per_window=emails_per_window,
            task_count=3,
        )
        async with self.session_factory() as session:
            sent_task = await session.get(EmailTask, sent_task_id)
            failed_task = await session.get(EmailTask, failed_task_id)
            assert sent_task is not None
            assert failed_task is not None
            sent_task.status = EmailTaskStatus.SENT.value
            sent_task.sent_at = datetime(2026, 5, 4, 9, 30, tzinfo=UTC)
            failed_task.status = EmailTaskStatus.SEND_FAILED.value
            failed_task.last_error = "smtp timeout"
            await session.commit()
        return sent_task_id, failed_task_id, pending_task_id

    async def _create_manual_approved_task(self) -> int:
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="测试身份",
                profile_name="测试身份",
                sender_name="王同学",
                email_address=f"sender-manual-{datetime.now(UTC).timestamp()}@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="template",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="老师您好，我是{{sender_name}}。",
                is_default=True,
            )
            llm_profile = LLMProfile(
                name=f"默认模型-manual-{datetime.now(UTC).timestamp()}",
                provider="openai",
                api_base_url="https://api.example.com/v1",
                api_key="sk-test-key",
                model_name="gpt-test",
                is_default=True,
            )
            professor = Professor(
                name="张教授",
                email=f"manual-{datetime.now(UTC).timestamp()}@example.edu",
                title="Professor",
                university="Example University",
                school="School of AI",
                department="Computer Science",
                research_direction="Large language models",
                recent_papers=[],
            )
            task = self._build_email_task(
                batch_task=None,
                identity=identity,
                llm_profile=llm_profile,
                professor=professor,
                status=EmailTaskStatus.APPROVED.value,
            )
            session.add(task)
            await session.commit()
            return task.id

    async def _create_blocked_scheduled_task_before_dispatchable_task(self) -> tuple[int, int]:
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="测试身份",
                profile_name="测试身份",
                sender_name="王同学",
                email_address=f"sender-limit-{datetime.now(UTC).timestamp()}@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="template",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="老师您好，我是{{sender_name}}。",
                is_default=True,
            )
            llm_profile = LLMProfile(
                name=f"默认模型-limit-{datetime.now(UTC).timestamp()}",
                provider="openai",
                api_base_url="https://api.example.com/v1",
                api_key="sk-test-key",
                model_name="gpt-test",
                is_default=True,
            )
            blocked_professor = Professor(
                name="前置导师",
                email=f"blocked-{datetime.now(UTC).timestamp()}@example.edu",
                title="Professor",
                university="Example University",
                school="School of AI",
                department="Computer Science",
                research_direction="Large language models",
                recent_papers=[],
            )
            dispatchable_professor = Professor(
                name="后置导师",
                email=f"dispatchable-{datetime.now(UTC).timestamp()}@example.edu",
                title="Professor",
                university="Example University",
                school="School of AI",
                department="Computer Science",
                research_direction="Large language models",
                recent_papers=[],
            )
            blocked_batch_task = BatchTask(
                identity=identity,
                llm_profile=llm_profile,
                name="非当天定时批量任务",
                schedule_type="scheduled",
                window_start_time="09:00",
                window_end_time="18:00",
                emails_per_window=20,
                scheduled_dates=["2026-05-04"],
                status=BatchTaskStatus.RUNNING.value,
                target_count=1,
            )
            blocked_task = self._build_email_task(
                batch_task=blocked_batch_task,
                identity=identity,
                llm_profile=llm_profile,
                professor=blocked_professor,
                status=EmailTaskStatus.APPROVED.value,
                approved_at=datetime(2026, 5, 3, 9, 0, tzinfo=UTC),
            )
            dispatchable_task = self._build_email_task(
                batch_task=None,
                identity=identity,
                llm_profile=llm_profile,
                professor=dispatchable_professor,
                status=EmailTaskStatus.APPROVED.value,
                approved_at=datetime(2026, 5, 3, 10, 0, tzinfo=UTC),
            )
            session.add_all([blocked_batch_task, blocked_task, dispatchable_task])
            await session.commit()
            return blocked_task.id, dispatchable_task.id

    async def _set_task_status(self, task_id: int, status: str) -> None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            task.status = status
            task.updated_at = datetime.now(UTC)
            await session.commit()

    async def _mark_task_user_removed(self, task_id: int) -> None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            task.status = EmailTaskStatus.CANCELED.value
            task.cancellation_reason = EmailTaskCancellationReason.USER_REMOVED.value
            task.scheduled_at = None
            task.updated_at = datetime.now(UTC)
            await session.commit()

    async def _set_task_sending(self, task_id: int, last_send_attempt_at: datetime) -> None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            task.status = EmailTaskStatus.SENDING.value
            task.last_send_attempt_at = last_send_attempt_at
            task.updated_at = last_send_attempt_at
            await session.commit()

    async def _add_prepared_attempt(
        self,
        task_id: int,
        *,
        started_at: datetime,
    ) -> str:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            professor = await session.get(Professor, task.professor_id)
            assert professor is not None
            task.retry_count = max(1, task.retry_count)
            attempt = EmailDeliveryAttempt(
                id="2f3390ed-684f-4ac7-8a02-1dfb667b7d72",
                email_task_id=task.id,
                identity_id=task.identity_id,
                professor_id=task.professor_id,
                attempt_number=task.retry_count,
                recipient_email=professor.email or "",
                subject_fingerprint=build_reconciliation_fingerprint(
                    task.approved_subject,
                ),
                content_fingerprint=build_reconciliation_fingerprint(
                    task.approved_body_text,
                ),
                status=EmailDeliveryAttemptStatus.PREPARED.value,
                started_at=started_at,
            )
            session.add(attempt)
            await session.commit()
            return attempt.id

    async def _get_attempt_statuses(self, task_id: int) -> list[tuple[str, str]]:
        async with self.session_factory() as session:
            attempts = list(
                await session.scalars(
                    select(EmailDeliveryAttempt)
                    .where(EmailDeliveryAttempt.email_task_id == task_id)
                    .order_by(EmailDeliveryAttempt.attempt_number),
                ),
            )
            return [(attempt.id, attempt.status) for attempt in attempts]

    async def _get_attempt_numbers(self, task_id: int) -> list[int]:
        async with self.session_factory() as session:
            return list(
                await session.scalars(
                    select(EmailDeliveryAttempt.attempt_number)
                    .where(EmailDeliveryAttempt.email_task_id == task_id)
                    .order_by(EmailDeliveryAttempt.attempt_number),
                ),
            )

    async def _get_email_log_count(self, task_id: int) -> int:
        async with self.session_factory() as session:
            logs = list(
                await session.scalars(
                    select(EmailLog).where(EmailLog.email_task_id == task_id),
                ),
            )
            return len(logs)

    async def _set_task_scheduled_at(self, task_id: int, scheduled_at: datetime) -> None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            task.scheduled_at = scheduled_at
            task.updated_at = datetime.now(UTC)
            await session.commit()

    async def _set_task_batch_send_canceled_at(
        self,
        task_id: int,
        canceled_at: datetime,
    ) -> None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            task.batch_send_canceled_at = canceled_at
            task.updated_at = canceled_at
            await session.commit()

    def _build_email_task(
        self,
        *,
        batch_task: BatchTask | None,
        identity: IdentityProfile,
        llm_profile: LLMProfile,
        professor: Professor,
        status: str,
        approved_at: datetime | None = None,
        sent_at: datetime | None = None,
    ) -> EmailTask:
        return EmailTask(
            source=EmailTaskSource.BATCH.value if batch_task is not None else EmailTaskSource.MANUAL.value,
            batch_task=batch_task,
            identity=identity,
            llm_profile=llm_profile,
            professor=professor,
            status=status,
            outreach_generation_mode="template",
            approved_at=approved_at or datetime(2026, 5, 3, 10, 0, tzinfo=UTC),
            approved_subject="申请与{{name}}老师交流",
            approved_body_text="老师您好，我是{{sender_name}}。",
            approved_body_html="<p>老师您好，我是{{sender_name}}。</p>",
            scheduled_at=None,
            sent_at=sent_at,
            retry_count=0,
            is_read=False,
            is_replied=False,
        )

    async def _get_task_status(self, task_id: int) -> str:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            return task.status

    async def _get_delivery_reconciliation_state(
        self,
        task_id: int,
    ) -> tuple[str, str, str | None, int]:
        async with self.session_factory() as session:
            attempt = await session.scalar(
                select(EmailDeliveryAttempt).where(
                    EmailDeliveryAttempt.email_task_id == task_id,
                ),
            )
            email_log = await session.scalar(
                select(EmailLog).where(EmailLog.email_task_id == task_id),
            )
            assert attempt is not None
            assert email_log is not None
            observations = list(
                await session.scalars(
                    select(EmailObservation).where(
                        EmailObservation.delivery_attempt_id == attempt.id,
                    ),
                ),
            )
            return (
                attempt.id,
                attempt.status,
                email_log.delivery_attempt_id,
                len(observations),
            )

    async def _get_task_scheduled_at(self, task_id: int) -> datetime | None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            return task.scheduled_at

    async def _get_task_batch_send_canceled_at(self, task_id: int) -> datetime | None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            return task.batch_send_canceled_at

    async def _get_identity_next_send_after_by_task_id(self, task_id: int) -> datetime | None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            identity = await session.get(IdentityProfile, task.identity_id)
            assert identity is not None
            return identity.next_send_after

    async def _set_identity_next_send_after_by_task_id(
        self,
        task_id: int,
        next_send_after: datetime,
    ) -> None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            identity = await session.get(IdentityProfile, task.identity_id)
            assert identity is not None
            identity.next_send_after = next_send_after
            await session.commit()

    async def _get_batch_task_status_by_email_task_id(self, task_id: int) -> str:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            batch_task = await session.get(BatchTask, task.batch_task_id)
            assert batch_task is not None
            return batch_task.status

    async def _get_task_cancellation_reason(self, task_id: int) -> str | None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            return task.cancellation_reason

    @staticmethod
    def _build_send_result() -> SendMailResult:
        return SendMailResult(
            message_id="<dispatch-schedule@example.com>",
            provider_payload={"provider": "test"},
        )

    @staticmethod
    def _run_async(coro):
        return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
