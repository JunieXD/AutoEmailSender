from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Base,
    BatchTask,
    BatchTaskStatus,
    EmailTask,
    EmailTaskCancellationReason,
    EmailTaskSource,
    EmailTaskStatus,
    IdentityProfile,
    LLMProfile,
    Professor,
)
from app.services.mail_runtime import SendMailResult
from app.schemas.email_task import EmailTaskApprovalRequest
from app.services.task_runtime import (
    approve_and_send_task,
    approve_draft_task,
    dispatch_due_tasks_once,
    dispatch_email_task,
)


class BatchTaskDispatchScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "dispatch_schedule_test.db"
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
            "app.services.task_runtime.mail_runtime.send_email",
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
            "app.services.task_runtime.mail_runtime.send_email",
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
            "app.services.task_runtime.mail_runtime.send_email",
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
            "app.services.task_runtime.mail_runtime.send_email",
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
            "app.services.task_runtime.mail_runtime.send_email",
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

        with self.assertRaisesRegex(ValueError, "草稿正在生成"):
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
            "app.services.task_runtime.mail_runtime.send_email",
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
            "app.services.task_runtime.mail_runtime.send_email",
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
                "app.services.task_runtime.mail_runtime.send_email",
                AsyncMock(return_value=self._build_send_result()),
            ) as mocked_send:
                self._run_async(dispatch_email_task(self.session_factory, task_id))
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", reschedule_before_claim)

        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.APPROVED.value)
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
            "app.services.task_runtime.mail_runtime.send_email",
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

    def test_dispatch_email_task_skips_task_no_longer_dispatchable(self) -> None:
        task_id = self._run_async(self._create_manual_approved_task())
        self._run_async(self._set_task_status(task_id, EmailTaskStatus.REVIEW_REQUIRED.value))

        with patch(
            "app.services.task_runtime.mail_runtime.send_email",
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
            "app.services.task_runtime.mail_runtime.send_email",
            AsyncMock(side_effect=delayed_send),
        ) as mocked_send:
            self._run_async(dispatch_twice())

        self.assertEqual(self._run_async(self._get_task_status(task_id)), EmailTaskStatus.SENT.value)
        mocked_send.assert_awaited_once()

    def test_dispatch_due_tasks_recovers_stale_sending_task(self) -> None:
        task_id = self._run_async(self._create_manual_approved_task())
        now = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        self._run_async(self._set_task_sending(task_id, now - timedelta(minutes=45)))

        with patch(
            "app.services.task_runtime.mail_runtime.send_email",
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

    def test_dispatch_due_tasks_keeps_recent_sending_task_claimed(self) -> None:
        task_id = self._run_async(self._create_manual_approved_task())
        now = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        self._run_async(self._set_task_sending(task_id, now - timedelta(minutes=5)))

        with patch(
            "app.services.task_runtime.mail_runtime.send_email",
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
            "app.services.task_runtime.mail_runtime.send_email",
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
            "app.services.task_runtime.mail_runtime.send_email",
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

    def test_dispatch_due_tasks_expires_batch_without_dispatchable_items(self) -> None:
        task_id = self._run_async(
            self._create_batch_task_with_review_required_task(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )

        with patch(
            "app.services.task_runtime.mail_runtime.send_email",
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
            "app.services.task_runtime.mail_runtime.send_email",
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
            "app.services.task_runtime.mail_runtime.send_email",
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

    def test_expiring_batch_preserves_final_item_statuses(self) -> None:
        sent_task_id, failed_task_id, pending_task_id = self._run_async(
            self._create_batch_task_with_final_and_pending_tasks(
                scheduled_dates=["2026-05-04"],
                emails_per_window=20,
            ),
        )

        with patch(
            "app.services.task_runtime.mail_runtime.send_email",
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
            "app.services.task_runtime.mail_runtime.send_email",
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
            "app.services.task_runtime.mail_runtime.send_email",
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
            "app.services.task_runtime.mail_runtime.send_email",
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
            "app.services.task_runtime.mail_runtime.send_email",
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
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

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

    async def _set_task_scheduled_at(self, task_id: int, scheduled_at: datetime) -> None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            task.scheduled_at = scheduled_at
            task.updated_at = datetime.now(UTC)
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

    async def _get_task_scheduled_at(self, task_id: int) -> datetime | None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            return task.scheduled_at

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
