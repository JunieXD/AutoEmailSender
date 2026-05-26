from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Base,
    AppSetting,
    BatchTask,
    BatchTaskStatus,
    EmailTask,
    EmailTaskSource,
    EmailTaskStatus,
    IdentityMaterial,
    IdentityMaterialType,
    IdentityProfile,
    LLMProfile,
    Professor,
)
from app.services import llm_runtime
from app.services.batch_draft_generation_runtime import (
    BatchDraftGenerationCoordinator,
    recover_stale_generating_drafts,
    run_queued_batch_drafts_once,
)


class BatchDraftGenerationRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "batch_draft_generation_test.db"
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

    def test_run_queued_batch_drafts_limits_llm_concurrency(self) -> None:
        self._run_async(self._create_batch_with_tasks([EmailTaskStatus.DISCOVERED.value, EmailTaskStatus.MATCHED.value]))
        max_seen = 0
        active = 0

        async def fake_generate(**kwargs):
            nonlocal active, max_seen
            active += 1
            max_seen = max(max_seen, active)
            await asyncio.sleep(0.01)
            active -= 1
            return self._build_draft_generation_result()

        with patch(
            "app.services.task_runtime.llm_runtime.generate_draft_content",
            new=AsyncMock(side_effect=fake_generate),
        ):
            processed = self._run_async(
                run_queued_batch_drafts_once(
                    self.session_factory,
                    concurrency=1,
                    coordinator=BatchDraftGenerationCoordinator(),
                ),
            )

        self.assertEqual(processed, 2)
        self.assertEqual(max_seen, 1)

    def test_run_queued_batch_drafts_claims_task_before_generation(self) -> None:
        self._run_async(self._create_batch_with_tasks([EmailTaskStatus.DISCOVERED.value]))

        async def fake_generate(**kwargs):
            await asyncio.sleep(0.05)
            return self._build_draft_generation_result()

        async def run_twice() -> list[int]:
            return list(
                await asyncio.gather(
                    run_queued_batch_drafts_once(
                        self.session_factory,
                        concurrency=1,
                        coordinator=BatchDraftGenerationCoordinator(),
                    ),
                    run_queued_batch_drafts_once(
                        self.session_factory,
                        concurrency=1,
                        coordinator=BatchDraftGenerationCoordinator(),
                    ),
                ),
            )

        with patch(
            "app.services.task_runtime.llm_runtime.generate_draft_content",
            new=AsyncMock(side_effect=fake_generate),
        ) as mocked_generate:
            processed_counts = self._run_async(run_twice())

        self.assertEqual(sum(processed_counts), 1)
        mocked_generate.assert_awaited_once()

    def test_recover_stale_generating_draft_restores_previous_status(self) -> None:
        task_ids = self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.GENERATING_DRAFT.value],
                previous_status=EmailTaskStatus.MATCHED.value,
                updated_at=datetime.now(UTC) - timedelta(minutes=45),
            ),
        )

        restored_count = self._run_async(
            recover_stale_generating_drafts(
                self.session_factory,
                stale_after=timedelta(minutes=30),
            ),
        )
        task = self._run_async(self._get_task(task_ids[0]))

        self.assertEqual(restored_count, 1)
        self.assertEqual(task.status, EmailTaskStatus.MATCHED.value)
        self.assertIsNone(task.draft_generation_previous_status)

    def test_llm_failure_marks_draft_failed_without_retry(self) -> None:
        task_ids = self._run_async(self._create_batch_with_tasks([EmailTaskStatus.DISCOVERED.value]))

        with patch(
            "app.services.task_runtime.llm_runtime.generate_draft_content",
            new=AsyncMock(side_effect=llm_runtime.LLMRuntimeError("LLM 请求失败")),
        ):
            processed = self._run_async(
                run_queued_batch_drafts_once(
                    self.session_factory,
                    concurrency=1,
                    coordinator=BatchDraftGenerationCoordinator(),
                ),
            )

        task = self._run_async(self._get_task(task_ids[0]))
        self.assertEqual(processed, 1)
        self.assertEqual(task.status, EmailTaskStatus.DRAFT_FAILED.value)
        self.assertIn("LLM", task.last_error or "")

    def test_draft_failed_is_not_claimed_again(self) -> None:
        self._run_async(self._create_batch_with_tasks([EmailTaskStatus.DRAFT_FAILED.value]))

        with patch(
            "app.services.task_runtime.llm_runtime.generate_draft_content",
            new=AsyncMock(side_effect=AssertionError("draft_failed 不应被自动重试")),
        ) as mocked_generate:
            processed = self._run_async(
                run_queued_batch_drafts_once(
                    self.session_factory,
                    concurrency=1,
                    coordinator=BatchDraftGenerationCoordinator(),
                ),
            )

        self.assertEqual(processed, 0)
        mocked_generate.assert_not_awaited()

    def test_null_generation_mode_batch_items_are_claimed_as_llm_drafts(self) -> None:
        task_ids = self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.DISCOVERED.value],
                outreach_generation_mode=None,
            ),
        )

        with patch(
            "app.services.task_runtime.llm_runtime.generate_draft_content",
            new=AsyncMock(return_value=self._build_draft_generation_result()),
        ):
            processed = self._run_async(
                run_queued_batch_drafts_once(
                    self.session_factory,
                    concurrency=1,
                    coordinator=BatchDraftGenerationCoordinator(),
                ),
            )

        task = self._run_async(self._get_task(task_ids[0]))
        self.assertEqual(processed, 1)
        self.assertEqual(task.status, EmailTaskStatus.REVIEW_REQUIRED.value)
        self.assertEqual(task.outreach_generation_mode, "llm")

    def test_items_missing_primary_material_are_not_claimed_for_generation(self) -> None:
        task_ids = self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.DISCOVERED.value],
                with_primary_material=False,
            ),
        )

        with patch(
            "app.services.task_runtime.llm_runtime.generate_draft_content",
            new=AsyncMock(side_effect=AssertionError("缺默认材料的任务不应被认领")),
        ) as mocked_generate:
            processed = self._run_async(
                run_queued_batch_drafts_once(
                    self.session_factory,
                    concurrency=1,
                    coordinator=BatchDraftGenerationCoordinator(),
                ),
            )

        task = self._run_async(self._get_task(task_ids[0]))
        self.assertEqual(processed, 0)
        self.assertEqual(task.status, EmailTaskStatus.DISCOVERED.value)
        mocked_generate.assert_not_awaited()

    def test_items_missing_professor_research_direction_are_not_claimed_for_generation(self) -> None:
        task_ids = self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.DISCOVERED.value],
                professor_research_direction="",
            ),
        )

        with patch(
            "app.services.task_runtime.llm_runtime.generate_draft_content",
            new=AsyncMock(side_effect=AssertionError("缺研究方向的任务不应被认领")),
        ) as mocked_generate:
            processed = self._run_async(
                run_queued_batch_drafts_once(
                    self.session_factory,
                    concurrency=1,
                    coordinator=BatchDraftGenerationCoordinator(),
                ),
            )

        task = self._run_async(self._get_task(task_ids[0]))
        self.assertEqual(processed, 0)
        self.assertEqual(task.status, EmailTaskStatus.DISCOVERED.value)
        mocked_generate.assert_not_awaited()


    def test_batch_draft_generation_keeps_batch_selected_materials(self) -> None:
        task_ids = self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.DISCOVERED.value],
                selected_material_ids=[101, 102],
            ),
        )

        with patch(
            "app.services.task_runtime.llm_runtime.generate_draft_content",
            new=AsyncMock(return_value=self._build_draft_generation_result()),
        ):
            processed = self._run_async(
                run_queued_batch_drafts_once(
                    self.session_factory,
                    concurrency=1,
                    coordinator=BatchDraftGenerationCoordinator(),
                ),
            )

        task = self._run_async(self._get_task(task_ids[0]))
        self.assertEqual(processed, 1)
        self.assertEqual(task.selected_material_ids, [101, 102])

    def test_batch_draft_generation_keeps_empty_selected_materials(self) -> None:
        task_ids = self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.DISCOVERED.value],
                selected_material_ids=None,
            ),
        )

        with patch(
            "app.services.task_runtime.llm_runtime.generate_draft_content",
            new=AsyncMock(return_value=self._build_draft_generation_result()),
        ):
            processed = self._run_async(
                run_queued_batch_drafts_once(
                    self.session_factory,
                    concurrency=1,
                    coordinator=BatchDraftGenerationCoordinator(),
                ),
            )

        task = self._run_async(self._get_task(task_ids[0]))
        self.assertEqual(processed, 1)
        self.assertIsNone(task.selected_material_ids)

    def test_coordinator_cancel_batch_cancels_tracked_tasks(self) -> None:
        async def scenario() -> bool:
            coordinator = BatchDraftGenerationCoordinator()
            task = asyncio.create_task(asyncio.sleep(60))
            async with coordinator.track(123, task):
                coordinator.cancel_batch(123)
                await asyncio.gather(task, return_exceptions=True)
            return task.cancelled()

        self.assertTrue(self._run_async(scenario()))

    async def _create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def _create_batch_with_tasks(
        self,
        statuses: list[str],
        *,
        previous_status: str | None = None,
        updated_at: datetime | None = None,
        selected_material_ids: list[int] | None = None,
        outreach_generation_mode: str | None = "llm",
        with_primary_material: bool = True,
        professor_research_direction: str = "Large language models",
    ) -> list[int]:
        async with self.session_factory() as session:
            session.add(AppSetting(id=1))
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
                outreach_generation_mode="llm",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="老师您好，我是{{sender_name}}。",
                is_default=True,
            )
            material = IdentityMaterial(
                identity=identity,
                display_name="简历",
                original_filename="resume.txt",
                file_path="resume.txt",
                mime_type="text/plain",
                size_bytes=32,
                sha256="0" * 64,
                extracted_text="My research focuses on agents.",
                material_type=IdentityMaterialType.RESUME.value,
            )
            if with_primary_material:
                identity.current_primary_material = material
            llm_profile = LLMProfile(
                name=f"默认模型-{datetime.now(UTC).timestamp()}",
                provider="openai",
                api_base_url="https://api.example.com/v1",
                api_key="sk-test-key",
                model_name="gpt-test",
                is_default=True,
            )
            batch_task = BatchTask(
                identity=identity,
                llm_profile=llm_profile,
                name="批量草稿任务",
                schedule_type="immediate",
                status=BatchTaskStatus.RUNNING.value,
                primary_material=material if with_primary_material else None,
                email_subject="申请与{{name}}老师交流",
                email_body="老师您好，我是{{sender_name}}。",
                selected_material_ids=selected_material_ids,
                target_count=len(statuses),
            )
            tasks = [
                EmailTask(
                    source=EmailTaskSource.BATCH.value,
                    batch_task=batch_task,
                    identity=identity,
                    llm_profile=llm_profile,
                    professor=Professor(
                        name=f"张教授{index}",
                        email=f"professor-{index}-{datetime.now(UTC).timestamp()}@example.edu",
                        title="Professor",
                        university="Example University",
                        school="School of AI",
                        department="Computer Science",
                        research_direction=professor_research_direction,
                        recent_papers=[],
                    ),
                    primary_material=material if with_primary_material else None,
                    status=status,
                    draft_generation_previous_status=previous_status,
                    outreach_generation_mode=outreach_generation_mode,
                    outreach_template_subject="申请与{{name}}老师交流",
                    outreach_template_body_text="老师您好，我是{{sender_name}}。",
                    selected_material_ids=selected_material_ids,
                    updated_at=updated_at or datetime.now(UTC),
                )
                for index, status in enumerate(statuses, start=1)
            ]
            session.add_all([batch_task, *tasks])
            await session.commit()
            return [task.id for task in tasks]

    async def _get_task(self, task_id: int) -> EmailTask:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            return task

    @staticmethod
    def _build_draft_generation_result() -> llm_runtime.GeneratedDraftContent:
        return llm_runtime.GeneratedDraftContent(
            result=llm_runtime.DraftGenerationResult(
                subject="生成主题",
                body_text="生成正文",
                body_html="<p>生成正文</p>",
            ),
            usage=llm_runtime.ChatCompletionUsage(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
        )

    @staticmethod
    def _run_async(coro):
        return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()


