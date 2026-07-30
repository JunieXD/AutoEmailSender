from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    BatchTask,
    BatchTaskStatus,
    EmailTask,
    EmailTaskStatus,
    IdentityProfile,
    LLMProfile,
    Professor,
)
from app.services.professor_schedule import load_active_scheduled_professor_ids
from test.schema_database import create_schema_sqlite_database


class ProfessorScheduleStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "professor_schedule_test.db"
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

    def test_active_schedule_matches_dispatchable_manual_and_batch_tasks(self) -> None:
        async def scenario() -> tuple[set[int], dict[str, int]]:
            async with self.session_factory() as session:
                identity = self._build_identity("active")
                llm_profile = self._build_llm_profile()
                professors = {
                    name: Professor(name=name, email=f"{name}@example.edu")
                    for name in [
                        "manual_scheduled",
                        "manual_approved",
                        "batch_scheduled",
                        "batch_approved",
                    ]
                }
                session.add_all([identity, llm_profile, *professors.values()])
                await session.flush()

                batch_task = BatchTask(
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    name="运行中的定时批量任务",
                    schedule_type="scheduled",
                    status=BatchTaskStatus.RUNNING.value,
                    target_count=2,
                )
                session.add(batch_task)
                await session.flush()

                scheduled_at = datetime.now(UTC) + timedelta(hours=1)
                session.add_all(
                    [
                        self._build_email_task(
                            identity,
                            llm_profile,
                            professors["manual_scheduled"],
                            status=EmailTaskStatus.SCHEDULED.value,
                            scheduled_at=scheduled_at,
                        ),
                        self._build_email_task(
                            identity,
                            llm_profile,
                            professors["manual_approved"],
                            status=EmailTaskStatus.APPROVED.value,
                            scheduled_at=scheduled_at,
                        ),
                        self._build_email_task(
                            identity,
                            llm_profile,
                            professors["batch_scheduled"],
                            status=EmailTaskStatus.SCHEDULED.value,
                            scheduled_at=scheduled_at,
                            batch_task_id=batch_task.id,
                        ),
                        self._build_email_task(
                            identity,
                            llm_profile,
                            professors["batch_approved"],
                            status=EmailTaskStatus.APPROVED.value,
                            scheduled_at=scheduled_at,
                            batch_task_id=batch_task.id,
                        ),
                    ],
                )
                await session.commit()

                ids_by_name = {
                    name: professor.id for name, professor in professors.items()
                }
                active_ids = await load_active_scheduled_professor_ids(
                    session,
                    identity_id=identity.id,
                    professor_ids=list(ids_by_name.values()),
                )
                return active_ids, ids_by_name

        active_ids, ids_by_name = self._run_async(scenario())

        self.assertEqual(active_ids, set(ids_by_name.values()))

    def test_active_schedule_excludes_incomplete_terminal_paused_and_other_identity_tasks(self) -> None:
        async def scenario() -> tuple[set[int], dict[str, int]]:
            async with self.session_factory() as session:
                identity = self._build_identity("current")
                other_identity = self._build_identity("other")
                llm_profile = self._build_llm_profile()
                case_names = [
                    "missing_time",
                    "review_required",
                    "sent",
                    "paused_batch",
                    "stopped_batch",
                    "other_identity",
                ]
                professors = {
                    name: Professor(name=name, email=f"{name}@example.edu")
                    for name in case_names
                }
                session.add_all(
                    [identity, other_identity, llm_profile, *professors.values()],
                )
                await session.flush()

                paused_batch = self._build_batch_task(
                    identity,
                    llm_profile,
                    name="暂停任务",
                    status=BatchTaskStatus.PAUSED.value,
                )
                stopped_batch = self._build_batch_task(
                    identity,
                    llm_profile,
                    name="停止任务",
                    status=BatchTaskStatus.STOPPED.value,
                )
                session.add_all([paused_batch, stopped_batch])
                await session.flush()

                scheduled_at = datetime.now(UTC) - timedelta(minutes=1)
                session.add_all(
                    [
                        self._build_email_task(
                            identity,
                            llm_profile,
                            professors["missing_time"],
                            status=EmailTaskStatus.SCHEDULED.value,
                            scheduled_at=None,
                        ),
                        self._build_email_task(
                            identity,
                            llm_profile,
                            professors["review_required"],
                            status=EmailTaskStatus.REVIEW_REQUIRED.value,
                            scheduled_at=scheduled_at,
                        ),
                        self._build_email_task(
                            identity,
                            llm_profile,
                            professors["sent"],
                            status=EmailTaskStatus.SENT.value,
                            scheduled_at=scheduled_at,
                        ),
                        self._build_email_task(
                            identity,
                            llm_profile,
                            professors["paused_batch"],
                            status=EmailTaskStatus.SCHEDULED.value,
                            scheduled_at=scheduled_at,
                            batch_task_id=paused_batch.id,
                        ),
                        self._build_email_task(
                            identity,
                            llm_profile,
                            professors["stopped_batch"],
                            status=EmailTaskStatus.SCHEDULED.value,
                            scheduled_at=scheduled_at,
                            batch_task_id=stopped_batch.id,
                        ),
                        self._build_email_task(
                            other_identity,
                            llm_profile,
                            professors["other_identity"],
                            status=EmailTaskStatus.SCHEDULED.value,
                            scheduled_at=scheduled_at,
                        ),
                    ],
                )
                await session.commit()

                ids_by_name = {
                    name: professor.id for name, professor in professors.items()
                }
                active_ids = await load_active_scheduled_professor_ids(
                    session,
                    identity_id=identity.id,
                    professor_ids=list(ids_by_name.values()),
                )
                return active_ids, ids_by_name

        active_ids, _ = self._run_async(scenario())

        self.assertEqual(active_ids, set())

    @staticmethod
    def _build_identity(suffix: str) -> IdentityProfile:
        return IdentityProfile(
            name=f"测试身份-{suffix}",
            profile_name=f"测试身份-{suffix}",
            sender_name="王同学",
            email_address=f"sender-{suffix}@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username=f"sender-{suffix}@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="template",
        )

    @staticmethod
    def _build_llm_profile() -> LLMProfile:
        return LLMProfile(
            name="测试模型",
            provider="openai",
            api_key="sk-test",
            model_name="gpt-test",
        )

    @staticmethod
    def _build_batch_task(
        identity: IdentityProfile,
        llm_profile: LLMProfile,
        *,
        name: str,
        status: str,
    ) -> BatchTask:
        return BatchTask(
            identity_id=identity.id,
            llm_profile_id=llm_profile.id,
            name=name,
            schedule_type="scheduled",
            status=status,
            target_count=1,
        )

    @staticmethod
    def _build_email_task(
        identity: IdentityProfile,
        llm_profile: LLMProfile,
        professor: Professor,
        *,
        status: str,
        scheduled_at: datetime | None,
        batch_task_id: int | None = None,
    ) -> EmailTask:
        return EmailTask(
            identity_id=identity.id,
            llm_profile_id=llm_profile.id,
            professor_id=professor.id,
            batch_task_id=batch_task_id,
            status=status,
            scheduled_at=scheduled_at,
        )


if __name__ == "__main__":
    unittest.main()
