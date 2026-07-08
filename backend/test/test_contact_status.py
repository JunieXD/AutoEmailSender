from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import EmailDirection, EmailLog, EmailTask, EmailTaskStatus, IdentityProfile, LLMProfile, Professor
from app.api.professors import list_professors
from app.services.contact_status import build_contact_status_by_professor
from test.schema_database import create_schema_sqlite_database


class ContactStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "contact_status_test.db"
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

    def tearDown(self) -> None:
        self._run_async(self.engine.dispose())
        self.temp_dir.cleanup()

    def _run_async(self, awaitable):
        return asyncio.run(awaitable)

    def test_successful_sent_log_keeps_professor_contacted_after_later_preparing_task(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                now = datetime.now(UTC)
                identity = self._build_identity()
                llm_profile = self._build_llm_profile()
                professor = Professor(name="张老师", email="zhang@example.edu")
                session.add_all([identity, llm_profile, professor])
                await session.flush()
                sent_task = EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    status=EmailTaskStatus.SENT.value,
                    sent_at=now - timedelta(days=2),
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                )
                session.add(sent_task)
                await session.flush()
                later_task = EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    parent_task_id=sent_task.id,
                    status=EmailTaskStatus.MATCHED.value,
                    created_at=now - timedelta(hours=1),
                    updated_at=now - timedelta(hours=1),
                )
                session.add(later_task)
                await session.flush()
                session.add(
                    EmailLog(
                        email_task_id=sent_task.id,
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        professor_id=professor.id,
                        direction=EmailDirection.SENT.value,
                        subject="申请交流",
                        content="老师您好",
                        created_at=now - timedelta(days=2),
                    ),
                )
                await session.commit()

                statuses = await build_contact_status_by_professor(
                    session,
                    identity_id=identity.id,
                    professor_ids=[professor.id],
                )
                return statuses[professor.id]

        status = self._run_async(scenario())

        self.assertEqual(status.status, "contacted")
        self.assertEqual(status.sent_count, 1)
        self.assertIsNotNone(status.last_sent_at)

    def test_reply_log_has_priority_over_sent_state(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                now = datetime.now(UTC)
                identity = self._build_identity()
                llm_profile = self._build_llm_profile()
                professor = Professor(name="李老师", email="li@example.edu")
                session.add_all([identity, llm_profile, professor])
                await session.flush()
                task = EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    status=EmailTaskStatus.SENT.value,
                    sent_at=now - timedelta(days=2),
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                )
                session.add(task)
                await session.flush()
                session.add_all(
                    [
                        EmailLog(
                            email_task_id=task.id,
                            identity_id=identity.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.SENT.value,
                            subject="申请交流",
                            content="老师您好",
                            created_at=now - timedelta(days=2),
                        ),
                        EmailLog(
                            email_task_id=task.id,
                            identity_id=identity.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Re: 申请交流",
                            content="欢迎交流",
                            created_at=now - timedelta(days=1),
                        ),
                    ],
                )
                await session.commit()

                statuses = await build_contact_status_by_professor(
                    session,
                    identity_id=identity.id,
                    professor_ids=[professor.id],
                )
                return statuses[professor.id]

        status = self._run_async(scenario())

        self.assertEqual(status.status, "replied")
        self.assertIsNotNone(status.last_replied_at)

    def test_failed_send_log_does_not_mark_professor_contacted(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                now = datetime.now(UTC)
                identity = self._build_identity()
                llm_profile = self._build_llm_profile()
                professor = Professor(name="王老师", email="wang@example.edu")
                session.add_all([identity, llm_profile, professor])
                await session.flush()
                task = EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    status=EmailTaskStatus.SEND_FAILED.value,
                    created_at=now - timedelta(hours=2),
                    updated_at=now - timedelta(hours=1),
                )
                session.add(task)
                await session.flush()
                session.add(
                    EmailLog(
                        email_task_id=task.id,
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        professor_id=professor.id,
                        direction=EmailDirection.SENT.value,
                        subject="申请交流",
                        content="老师您好",
                        failure_summary="SMTP 失败",
                        created_at=now - timedelta(hours=1),
                    ),
                )
                await session.commit()

                statuses = await build_contact_status_by_professor(
                    session,
                    identity_id=identity.id,
                    professor_ids=[professor.id],
                )
                return statuses[professor.id]

        status = self._run_async(scenario())

        self.assertEqual(status.status, "failed")
        self.assertEqual(status.sent_count, 0)
        self.assertIsNone(status.last_sent_at)

    def test_received_log_without_task_marks_professor_replied(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                now = datetime.now(UTC)
                identity = self._build_identity()
                llm_profile = self._build_llm_profile()
                professor = Professor(name="无任务回复老师", email="reply-only@example.edu")
                session.add_all([identity, llm_profile, professor])
                await session.flush()
                session.add(
                    EmailLog(
                        email_task_id=None,
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        professor_id=professor.id,
                        direction=EmailDirection.RECEIVED.value,
                        subject="Re: 交流",
                        content="欢迎交流",
                        created_at=now,
                    ),
                )
                await session.commit()

                statuses = await build_contact_status_by_professor(
                    session,
                    identity_id=identity.id,
                    professor_ids=[professor.id],
                )
                return statuses[professor.id]

        status = self._run_async(scenario())

        self.assertEqual(status.status, "replied")
        self.assertEqual(status.sent_count, 0)
        self.assertIsNotNone(status.last_replied_at)

    def test_sent_log_without_task_marks_professor_contacted(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                now = datetime.now(UTC)
                identity = self._build_identity()
                llm_profile = self._build_llm_profile()
                professor = Professor(name="无任务发送老师", email="sent-only@example.edu")
                session.add_all([identity, llm_profile, professor])
                await session.flush()
                session.add(
                    EmailLog(
                        email_task_id=None,
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        professor_id=professor.id,
                        direction=EmailDirection.SENT.value,
                        subject="申请交流",
                        content="老师您好",
                        created_at=now,
                    ),
                )
                await session.commit()

                statuses = await build_contact_status_by_professor(
                    session,
                    identity_id=identity.id,
                    professor_ids=[professor.id],
                )
                return statuses[professor.id]

        status = self._run_async(scenario())

        self.assertEqual(status.status, "contacted")
        self.assertEqual(status.sent_count, 1)
        self.assertIsNotNone(status.last_sent_at)

    def test_contact_status_ignores_logs_from_other_identity(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                now = datetime.now(UTC)
                identity = self._build_identity()
                other_identity = self._build_identity()
                other_identity.email_address = "other-sender@example.com"
                other_identity.smtp_username = "other-sender@example.com"
                llm_profile = self._build_llm_profile()
                professor = Professor(name="隔离老师", email="scoped@example.edu")
                session.add_all([identity, other_identity, llm_profile, professor])
                await session.flush()
                session.add_all(
                    [
                        EmailLog(
                            email_task_id=None,
                            identity_id=other_identity.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.SENT.value,
                            subject="其他身份发送",
                            content="老师您好",
                            created_at=now - timedelta(hours=1),
                        ),
                        EmailLog(
                            email_task_id=None,
                            identity_id=other_identity.id,
                            llm_profile_id=llm_profile.id,
                            professor_id=professor.id,
                            direction=EmailDirection.RECEIVED.value,
                            subject="Re: 其他身份发送",
                            content="欢迎",
                            created_at=now,
                        ),
                    ],
                )
                await session.commit()

                statuses = await build_contact_status_by_professor(
                    session,
                    identity_id=identity.id,
                    professor_ids=[professor.id],
                )
                return statuses[professor.id]

        status = self._run_async(scenario())

        self.assertEqual(status.status, "not_contacted")
        self.assertEqual(status.sent_count, 0)
        self.assertIsNone(status.last_sent_at)
        self.assertIsNone(status.last_replied_at)

    def test_contact_status_queries_only_log_columns_needed_for_status(self) -> None:
        async def scenario():
            now = datetime.now(UTC)
            test_case = self

            class ProjectionOnlySession:
                async def execute(self, statement):
                    selected_columns = [
                        getattr(column, "key", None)
                        for column in statement.selected_columns
                    ]
                    test_case.assertEqual(
                        selected_columns,
                        ["professor_id", "direction", "failure_summary", "created_at"],
                    )
                    return [
                        (
                            1,
                            EmailDirection.SENT.value,
                            None,
                            now,
                        ),
                    ]

                async def scalars(self, statement):
                    raise AssertionError("EmailLog status query should use projected columns")

            statuses = await build_contact_status_by_professor(
                ProjectionOnlySession(),
                identity_id=1,
                professor_ids=[1],
                tasks_by_professor={},
            )
            return statuses[1]

        status = self._run_async(scenario())

        self.assertEqual(status.status, "contacted")
        self.assertEqual(status.sent_count, 1)
        self.assertIsNotNone(status.last_sent_at)

    def test_dashboard_professor_list_reuses_loaded_tasks_for_contact_status(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                identity = self._build_identity()
                llm_profile = self._build_llm_profile()
                professor = Professor(name="复用任务老师", email="reuse-task@example.edu")
                session.add_all([identity, llm_profile, professor])
                await session.flush()
                task = EmailTask(
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    status=EmailTaskStatus.SENT.value,
                    match_score=88,
                )
                session.add(task)
                await session.commit()

                with patch(
                    "app.api.professors.build_contact_status_by_professor",
                    new_callable=AsyncMock,
                ) as build_status:
                    build_status.return_value = {}

                    items = await list_professors(
                        identity_id=identity.id,
                        llm_profile_id=None,
                        ids=None,
                        session=session,
                    )

                return items, build_status, task.id

        items, build_status, task_id = self._run_async(scenario())

        self.assertEqual(items[0].match_score, 88)
        self.assertIn("tasks_by_professor", build_status.await_args.kwargs)
        tasks_by_professor = build_status.await_args.kwargs["tasks_by_professor"]
        self.assertEqual([task.id for task in tasks_by_professor[items[0].id]], [task_id])

    @staticmethod
    def _build_identity() -> IdentityProfile:
        return IdentityProfile(
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
        )

    @staticmethod
    def _build_llm_profile() -> LLMProfile:
        return LLMProfile(
            name=f"默认模型-{datetime.now(UTC).timestamp()}",
            provider="openai",
            api_base_url="https://api.example.com/v1",
            api_key="sk-test-key",
            model_name="gpt-test",
        )
