from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
from app.modules.workspace.deliveries.service import (
    cancel_email_delivery,
    list_email_deliveries,
    reschedule_email_delivery,
)
from app.modules.workspace.tasks.delivery import mark_overdue_manual_schedules_missed


class EmailDeliveryManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "email-deliveries.db"
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path.as_posix()}",
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self.temp_dir.cleanup()

    @staticmethod
    def _identity(name: str, email: str) -> IdentityProfile:
        return IdentityProfile(
            name=name,
            profile_name=name,
            sender_name=name,
            email_address=email,
            smtp_host="smtp.example.com",
            smtp_username=email,
            smtp_password="secret",
        )

    @staticmethod
    def _profile() -> LLMProfile:
        return LLMProfile(
            name="测试模型",
            api_key="test-key",
            model_name="test-model",
        )

    async def _seed_delivery_matrix(self) -> dict[str, int]:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            identity_a = self._identity("申请身份 A", "a@example.com")
            identity_b = self._identity("申请身份 B", "b@example.com")
            llm_profile = self._profile()
            session.add_all([identity_a, identity_b, llm_profile])
            await session.flush()

            scheduled_tasks: list[EmailTask] = []
            for index in range(25):
                professor = Professor(
                    name=f"计划导师 {index:02d}",
                    email=f"scheduled-{index:02d}@example.edu",
                )
                session.add(professor)
                await session.flush()
                scheduled_tasks.append(
                    EmailTask(
                        professor_id=professor.id,
                        identity_id=identity_a.id,
                        llm_profile_id=llm_profile.id,
                        source=EmailTaskSource.MANUAL.value,
                        status=EmailTaskStatus.SCHEDULED.value,
                        approved_subject=(
                            "Unique Subject 07"
                            if index == 7
                            else f"计划主题 {index:02d}"
                        ),
                        scheduled_at=now + timedelta(hours=index + 1),
                    ),
                )

            attention_tasks: list[EmailTask] = []
            for index, status in enumerate(
                [EmailTaskStatus.SEND_FAILED.value, EmailTaskStatus.SCHEDULE_MISSED.value],
            ):
                professor = Professor(
                    name=f"需处理导师 {index}",
                    email=f"attention-{index}@example.edu",
                )
                session.add(professor)
                await session.flush()
                attention_tasks.append(
                    EmailTask(
                        professor_id=professor.id,
                        identity_id=identity_a.id,
                        llm_profile_id=llm_profile.id,
                        source=EmailTaskSource.MANUAL.value,
                        status=status,
                        approved_subject=f"需处理主题 {index}",
                        scheduled_at=now - timedelta(hours=index + 1),
                    ),
                )

            sent_professor = Professor(name="历史导师", email="sent@example.edu")
            canceled_professor = Professor(
                name="取消导师",
                email="canceled@example.edu",
            )
            batch_professor = Professor(name="批量导师", email="batch@example.edu")
            session.add_all([sent_professor, canceled_professor, batch_professor])
            await session.flush()

            batch_task = BatchTask(
                identity_id=identity_b.id,
                llm_profile_id=llm_profile.id,
                name="秋季申请批次",
                status=BatchTaskStatus.RUNNING.value,
            )
            session.add(batch_task)
            await session.flush()

            sent_task = EmailTask(
                professor_id=sent_professor.id,
                identity_id=identity_a.id,
                llm_profile_id=llm_profile.id,
                source=EmailTaskSource.MANUAL.value,
                status=EmailTaskStatus.SENT.value,
                approved_subject="已发送主题",
                sent_at=now - timedelta(days=1),
            )
            canceled_task = EmailTask(
                professor_id=canceled_professor.id,
                identity_id=identity_a.id,
                llm_profile_id=llm_profile.id,
                source=EmailTaskSource.MANUAL.value,
                status=EmailTaskStatus.REVIEW_REQUIRED.value,
                approved_subject="已取消主题",
                last_scheduled_at=now + timedelta(days=1),
                schedule_canceled_at=now,
            )
            batch_task_item = EmailTask(
                professor_id=batch_professor.id,
                identity_id=identity_b.id,
                llm_profile_id=llm_profile.id,
                source=EmailTaskSource.BATCH.value,
                batch_task_id=batch_task.id,
                status=EmailTaskStatus.SCHEDULED.value,
                approved_subject="批量计划主题",
                scheduled_at=now + timedelta(hours=2),
            )
            session.add_all(
                [
                    *scheduled_tasks,
                    *attention_tasks,
                    sent_task,
                    canceled_task,
                    batch_task_item,
                ],
            )
            await session.commit()
            return {
                "identity_a": identity_a.id,
                "identity_b": identity_b.id,
                "scheduled_task": scheduled_tasks[0].id,
                "sent_task": sent_task.id,
            }

    async def test_server_side_pagination_counts_and_filters(self) -> None:
        ids = await self._seed_delivery_matrix()
        async with self.session_factory() as session:
            first_page = await list_email_deliveries(
                session,
                view="upcoming",
                page=1,
                page_size=20,
                identity_id=ids["identity_a"],
                source="manual",
                status=None,
                query=None,
                task_id=None,
            )
            second_page = await list_email_deliveries(
                session,
                view="upcoming",
                page=2,
                page_size=20,
                identity_id=ids["identity_a"],
                source="manual",
                status="waiting_scheduled",
                query=None,
                task_id=None,
            )
            search_result = await list_email_deliveries(
                session,
                view="upcoming",
                page=1,
                page_size=20,
                identity_id=None,
                source="all",
                status=None,
                query="unique subject 07",
                task_id=None,
            )
            subject_search_result = await list_email_deliveries(
                session,
                view="upcoming",
                page=1,
                page_size=20,
                identity_id=None,
                source="all",
                status=None,
                query="unique subject 07",
                task_id=None,
                search_fields=("subject",),
            )
            recipient_search_result = await list_email_deliveries(
                session,
                view="upcoming",
                page=1,
                page_size=20,
                identity_id=None,
                source="all",
                status=None,
                query="unique subject 07",
                task_id=None,
                search_fields=("recipient_name", "recipient_email"),
            )
            batch_result = await list_email_deliveries(
                session,
                view="upcoming",
                page=1,
                page_size=20,
                identity_id=ids["identity_b"],
                source="batch",
                status=None,
                query=None,
                task_id=None,
            )
            latest_schedule_first = await list_email_deliveries(
                session,
                view="upcoming",
                page=1,
                page_size=20,
                identity_id=ids["identity_a"],
                source="manual",
                status=None,
                query=None,
                task_id=None,
                sort="scheduled_desc",
            )
            located_history_result = await list_email_deliveries(
                session,
                view="upcoming",
                page=1,
                page_size=20,
                identity_id=None,
                source="all",
                status=None,
                query=None,
                task_id=ids["sent_task"],
            )

        self.assertEqual(first_page.total_count, 25)
        self.assertEqual(first_page.total_pages, 2)
        self.assertEqual(len(first_page.items), 20)
        self.assertEqual(first_page.counts.upcoming, 25)
        self.assertEqual(first_page.counts.attention, 2)
        self.assertEqual(first_page.counts.history, 2)
        self.assertEqual(second_page.total_count, 25)
        self.assertEqual(len(second_page.items), 5)
        self.assertEqual(search_result.total_count, 1)
        self.assertEqual(search_result.items[0].subject, "Unique Subject 07")
        self.assertEqual(subject_search_result.total_count, 1)
        self.assertEqual(recipient_search_result.total_count, 0)
        self.assertEqual(batch_result.total_count, 1)
        self.assertEqual(batch_result.items[0].batch_task_name, "秋季申请批次")
        self.assertEqual(latest_schedule_first.items[0].professor_name, "计划导师 24")
        self.assertEqual(located_history_result.total_count, 1)
        self.assertEqual(located_history_result.items[0].status, "sent")

    async def test_approved_delivery_with_schedule_is_reported_as_waiting(self) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            identity = self._identity("兼容状态身份", "compat@example.com")
            llm_profile = self._profile()
            batch_task = BatchTask(
                identity=identity,
                llm_profile=llm_profile,
                name="兼容状态批次",
                status=BatchTaskStatus.RUNNING.value,
            )
            scheduled_professor = Professor(
                name="已有计划导师",
                email="scheduled-compat@example.edu",
            )
            asap_professor = Professor(
                name="尽快发送导师",
                email="asap@example.edu",
            )
            session.add_all(
                [
                    batch_task,
                    scheduled_professor,
                    asap_professor,
                ],
            )
            await session.flush()
            session.add_all(
                [
                    EmailTask(
                        professor_id=scheduled_professor.id,
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        source=EmailTaskSource.BATCH.value,
                        batch_task_id=batch_task.id,
                        status=EmailTaskStatus.APPROVED.value,
                        approved_subject="已有明确计划时间",
                        scheduled_at=now + timedelta(days=1),
                    ),
                    EmailTask(
                        professor_id=asap_professor.id,
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        source=EmailTaskSource.BATCH.value,
                        batch_task_id=batch_task.id,
                        status=EmailTaskStatus.APPROVED.value,
                        approved_subject="没有明确计划时间",
                        scheduled_at=None,
                    ),
                ],
            )
            await session.commit()

            waiting_result = await list_email_deliveries(
                session,
                view="upcoming",
                page=1,
                page_size=20,
                identity_id=None,
                source="all",
                status="waiting_scheduled",
                query=None,
                task_id=None,
            )
            asap_result = await list_email_deliveries(
                session,
                view="upcoming",
                page=1,
                page_size=20,
                identity_id=None,
                source="all",
                status="send_asap",
                query=None,
                task_id=None,
            )

        self.assertEqual(waiting_result.total_count, 1)
        self.assertEqual(waiting_result.items[0].status, "waiting_scheduled")
        self.assertEqual(waiting_result.items[0].status_label, "等待发送")
        self.assertIsNotNone(waiting_result.items[0].scheduled_at)
        self.assertEqual(asap_result.total_count, 1)
        self.assertEqual(asap_result.items[0].status, "send_asap")
        self.assertIsNone(asap_result.items[0].scheduled_at)

    async def test_delivery_hot_queries_use_dedicated_indexes(self) -> None:
        async with self.engine.connect() as connection:
            upcoming_plan = (
                await connection.execute(
                    text(
                        """
                        EXPLAIN QUERY PLAN
                        SELECT id
                        FROM email_tasks
                        WHERE schedule_canceled_at IS NULL
                          AND batch_send_canceled_at IS NULL
                          AND status IN ('approved', 'scheduled', 'sending')
                        ORDER BY scheduled_at ASC, id ASC
                        LIMIT 20
                        """,
                    ),
                )
            ).all()
            attention_plan = (
                await connection.execute(
                    text(
                        """
                        EXPLAIN QUERY PLAN
                        SELECT id
                        FROM email_tasks
                        WHERE schedule_canceled_at IS NULL
                          AND batch_send_canceled_at IS NULL
                          AND status IN ('send_failed', 'schedule_missed')
                        ORDER BY updated_at DESC, id DESC
                        LIMIT 20
                        """,
                    ),
                )
            ).all()

        self.assertIn(
            "ix_email_tasks_delivery_upcoming_schedule",
            " ".join(str(row) for row in upcoming_plan),
        )
        self.assertIn(
            "ix_email_tasks_delivery_attention_updated",
            " ".join(str(row) for row in attention_plan),
        )

    async def test_attention_statuses_explain_the_actual_unsent_reason(self) -> None:
        async with self.session_factory() as session:
            identity = self._identity("原因测试身份", "reason@example.com")
            llm_profile = self._profile()
            batch_task = BatchTask(
                identity=identity,
                llm_profile=llm_profile,
                name="原因测试批次",
                status=BatchTaskStatus.STOPPED.value,
            )
            professors = [
                Professor(name="草稿失败导师", email="draft-failed@example.edu"),
                Professor(name="任务终止导师", email="stopped@example.edu"),
                Professor(name="窗口过期导师", email="expired@example.edu"),
            ]
            session.add_all([identity, llm_profile, batch_task, *professors])
            await session.flush()
            session.add_all(
                [
                    EmailTask(
                        professor_id=professors[0].id,
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        source=EmailTaskSource.BATCH.value,
                        batch_task_id=batch_task.id,
                        status=EmailTaskStatus.CANCELED.value,
                        cancellation_reason=EmailTaskCancellationReason.BATCH_STOPPED.value,
                        last_error="模型返回的 JSON 结构无效",
                    ),
                    EmailTask(
                        professor_id=professors[1].id,
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        source=EmailTaskSource.BATCH.value,
                        batch_task_id=batch_task.id,
                        status=EmailTaskStatus.CANCELED.value,
                        cancellation_reason=EmailTaskCancellationReason.BATCH_STOPPED.value,
                    ),
                    EmailTask(
                        professor_id=professors[2].id,
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        source=EmailTaskSource.BATCH.value,
                        batch_task_id=batch_task.id,
                        status=EmailTaskStatus.CANCELED.value,
                        cancellation_reason=EmailTaskCancellationReason.SCHEDULE_EXPIRED.value,
                    ),
                ],
            )
            await session.commit()

            result = await list_email_deliveries(
                session,
                view="attention",
                page=1,
                page_size=1,
                identity_id=None,
                source="all",
                status=None,
                query=None,
                task_id=None,
            )
            all_items = []
            for page in range(1, result.total_pages + 1):
                page_result = await list_email_deliveries(
                    session,
                    view="attention",
                    page=page,
                    page_size=1,
                    identity_id=None,
                    source="all",
                    status=None,
                    query=None,
                    task_id=None,
                )
                all_items.extend(page_result.items)
            filtered_counts = {}
            for status in ("draft_failed", "batch_stopped", "schedule_expired"):
                filtered_result = await list_email_deliveries(
                    session,
                    view="attention",
                    page=1,
                    page_size=1,
                    identity_id=None,
                    source="all",
                    status=status,
                    query=None,
                    task_id=None,
                )
                filtered_counts[status] = filtered_result.total_count

        items_by_name = {item.professor_name: item for item in all_items}
        self.assertEqual(items_by_name["草稿失败导师"].status, "draft_failed")
        self.assertEqual(items_by_name["草稿失败导师"].status_label, "草稿生成失败")
        self.assertEqual(
            items_by_name["草稿失败导师"].last_error,
            "模型返回的 JSON 结构无效",
        )
        self.assertEqual(items_by_name["任务终止导师"].status, "batch_stopped")
        self.assertEqual(items_by_name["任务终止导师"].status_label, "批量任务已终止")
        self.assertEqual(items_by_name["窗口过期导师"].status, "schedule_expired")
        self.assertEqual(items_by_name["窗口过期导师"].status_label, "发送窗口已过期")
        self.assertEqual(
            filtered_counts,
            {"draft_failed": 1, "batch_stopped": 1, "schedule_expired": 1},
        )

    async def test_reschedule_conflict_and_cancel_preserve_history(self) -> None:
        ids = await self._seed_delivery_matrix()
        async with self.session_factory() as session:
            task = await session.get(EmailTask, ids["scheduled_task"])
            assert task is not None
            original_schedule = task.scheduled_at
            original_updated_at = task.updated_at

            with self.assertRaises(HTTPException) as conflict:
                await reschedule_email_delivery(
                    session,
                    task_id=task.id,
                    scheduled_at=datetime.now(UTC) + timedelta(days=3),
                    expected_updated_at=original_updated_at + timedelta(seconds=1),
                )
            self.assertEqual(conflict.exception.status_code, 409)

            next_schedule = datetime.now(UTC) + timedelta(days=4)
            result = await reschedule_email_delivery(
                session,
                task_id=task.id,
                scheduled_at=next_schedule,
                expected_updated_at=original_updated_at,
            )
            self.assertTrue(result.ok)

        async with self.session_factory() as session:
            task = await session.get(EmailTask, ids["scheduled_task"])
            assert task is not None
            self.assertEqual(task.last_scheduled_at, original_schedule)
            self.assertEqual(task.scheduled_at, next_schedule)
            rescheduled_updated_at = task.updated_at

            await cancel_email_delivery(
                session,
                task_id=task.id,
                expected_updated_at=rescheduled_updated_at,
            )

        async with self.session_factory() as session:
            task = await session.get(EmailTask, ids["scheduled_task"])
            assert task is not None
            self.assertEqual(task.status, EmailTaskStatus.REVIEW_REQUIRED.value)
            self.assertIsNone(task.scheduled_at)
            self.assertEqual(task.last_scheduled_at, next_schedule)
            self.assertIsNotNone(task.schedule_canceled_at)

            history = await list_email_deliveries(
                session,
                view="history",
                page=1,
                page_size=20,
                identity_id=None,
                source="all",
                status="canceled_schedule",
                query=None,
                task_id=task.id,
            )
            self.assertEqual(history.total_count, 1)
            self.assertEqual(history.items[0].status, "canceled_schedule")

    async def test_startup_marks_only_overdue_manual_schedules_missed(self) -> None:
        now = datetime(2026, 8, 7, 10, 0, tzinfo=UTC)
        async with self.session_factory() as session:
            identity = self._identity("启动恢复身份", "recovery@example.com")
            llm_profile = self._profile()
            session.add_all([identity, llm_profile])
            await session.flush()

            professors = [
                Professor(name="过期手动", email="overdue@example.edu"),
                Professor(name="宽限期手动", email="grace@example.edu"),
                Professor(name="过期批量", email="batch-overdue@example.edu"),
            ]
            session.add_all(professors)
            await session.flush()
            batch_task = BatchTask(
                identity_id=identity.id,
                llm_profile_id=llm_profile.id,
                name="恢复测试批次",
            )
            session.add(batch_task)
            await session.flush()

            tasks = [
                EmailTask(
                    professor_id=professors[0].id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    source=EmailTaskSource.MANUAL.value,
                    status=EmailTaskStatus.SCHEDULED.value,
                    scheduled_at=now - timedelta(minutes=3),
                ),
                EmailTask(
                    professor_id=professors[1].id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    source=EmailTaskSource.MANUAL.value,
                    status=EmailTaskStatus.SCHEDULED.value,
                    scheduled_at=now - timedelta(minutes=1),
                ),
                EmailTask(
                    professor_id=professors[2].id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    source=EmailTaskSource.BATCH.value,
                    batch_task_id=batch_task.id,
                    status=EmailTaskStatus.SCHEDULED.value,
                    scheduled_at=now - timedelta(minutes=3),
                ),
            ]
            session.add_all(tasks)
            await session.commit()
            task_ids = [task.id for task in tasks]

        recovered = await mark_overdue_manual_schedules_missed(
            self.session_factory,
            now=now,
        )
        self.assertEqual(recovered, 1)

        async with self.session_factory() as session:
            loaded = list(
                (
                    await session.execute(
                        select(EmailTask)
                        .where(EmailTask.id.in_(task_ids))
                        .order_by(EmailTask.id),
                    )
                ).scalars(),
            )
        self.assertEqual(loaded[0].status, EmailTaskStatus.SCHEDULE_MISSED.value)
        self.assertEqual(loaded[1].status, EmailTaskStatus.SCHEDULED.value)
        self.assertEqual(loaded[2].status, EmailTaskStatus.SCHEDULED.value)


if __name__ == "__main__":
    unittest.main()
