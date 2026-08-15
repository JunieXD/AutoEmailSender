from __future__ import annotations

import asyncio
import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    AppSetting,
    BatchTask,
    BatchTaskStatus,
    EmailTaskCancellationReason,
    EmailTask,
    EmailTaskSource,
    EmailTaskStatus,
    IdentityMaterial,
    IdentityMaterialType,
    IdentityProfile,
    LLMProfile,
    Professor,
)
from app.modules.llm import runtime as llm_runtime
from test.schema_database import create_schema_sqlite_database
from app.modules.campaigns.drafts.runtime import (
    BatchDraftGenerationCoordinator,
    BatchDraftScheduler,
    _recover_stale_batch_draft_task,
    recover_interrupted_workspace_draft_rewrites,
    recover_stale_generating_drafts,
    recover_stale_workspace_draft_rewrites,
    run_queued_batch_drafts_once,
)
from app.modules.workspace.tasks.runtime import (
    _lock_current_batch_draft_claim,
    generate_task_draft,
)


class BatchDraftGenerationRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._runtime_adaptation_patch = patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.ensure_llm_runtime_adaptation",
            new=AsyncMock(return_value=llm_runtime.LLMRuntimeAdaptation("chat_completions", None)),
        )
        self._runtime_adaptation_patch.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "batch_draft_generation_test.db"
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
        self._runtime_adaptation_patch.stop()
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
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
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
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            new=AsyncMock(side_effect=fake_generate),
        ) as mocked_generate:
            processed_counts = self._run_async(run_twice())

        self.assertEqual(sum(processed_counts), 1)
        mocked_generate.assert_awaited_once()

    def test_run_queued_batch_drafts_finishes_batch_warmup_before_remaining_items(self) -> None:
        self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.DISCOVERED.value, EmailTaskStatus.MATCHED.value],
            ),
        )

        async def scenario() -> tuple[bool, bool, int]:
            warmup_started = asyncio.Event()
            release_warmup = asyncio.Event()
            remaining_started = asyncio.Event()
            call_count = 0

            async def fake_generate(**_kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    warmup_started.set()
                    await release_warmup.wait()
                else:
                    remaining_started.set()
                return self._build_draft_generation_result()

            with patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
                new=AsyncMock(side_effect=fake_generate),
            ):
                worker = asyncio.create_task(
                    run_queued_batch_drafts_once(
                        self.session_factory,
                        concurrency=2,
                        coordinator=BatchDraftGenerationCoordinator(),
                    ),
                )
                await asyncio.wait_for(warmup_started.wait(), timeout=1)
                await asyncio.sleep(0.02)
                remaining_started_before_warmup_finished = remaining_started.is_set()
                release_warmup.set()
                processed = await asyncio.wait_for(worker, timeout=2)

            return (
                remaining_started_before_warmup_finished,
                remaining_started.is_set(),
                processed,
            )

        remaining_started_early, remaining_eventually_started, processed = self._run_async(
            scenario(),
        )
        self.assertFalse(remaining_started_early)
        self.assertTrue(remaining_eventually_started)
        self.assertEqual(processed, 2)

    def test_batch_draft_passes_workflow_session_and_runtime_adaptation(self) -> None:
        self._run_async(self._create_batch_with_tasks([EmailTaskStatus.DISCOVERED.value]))
        adaptation = llm_runtime.LLMRuntimeAdaptation("responses", {"enable_thinking": False})
        workflow_sessions: list[AsyncSession] = []

        async def fake_ensure(session: AsyncSession, _profile: object) -> llm_runtime.LLMRuntimeAdaptation:
            self.assertIsInstance(session, AsyncSession)
            workflow_sessions.append(session)
            return adaptation

        async def fake_generate(**kwargs: object) -> llm_runtime.GeneratedDraftContent:
            self.assertEqual(len(workflow_sessions), 1)
            self.assertIs(kwargs["session"], workflow_sessions[0])
            self.assertIs(kwargs["adaptation"], adaptation)
            return self._build_draft_generation_result()

        with (
            patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.ensure_llm_runtime_adaptation",
                new=AsyncMock(side_effect=fake_ensure),
            ) as adaptation_mock,
            patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
                new=AsyncMock(side_effect=fake_generate),
            ) as generate_mock,
        ):
            processed = self._run_async(
                run_queued_batch_drafts_once(
                    self.session_factory,
                    concurrency=1,
                    coordinator=BatchDraftGenerationCoordinator(),
                ),
            )

        self.assertEqual(processed, 1)
        adaptation_mock.assert_awaited_once()
        generate_mock.assert_awaited_once()

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

    def test_recover_stale_generating_draft_cancels_expired_batch_item(self) -> None:
        task_ids = self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.GENERATING_DRAFT.value],
                previous_status=EmailTaskStatus.MATCHED.value,
                updated_at=datetime.now(UTC) - timedelta(minutes=45),
                batch_status=BatchTaskStatus.EXPIRED.value,
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
        self.assertEqual(task.status, EmailTaskStatus.CANCELED.value)
        self.assertEqual(task.cancellation_reason, EmailTaskCancellationReason.SCHEDULE_EXPIRED.value)
        self.assertIsNone(task.draft_generation_previous_status)

    def test_recover_stale_workspace_rewrite_uses_started_at_and_restores_source(self) -> None:
        task_id = self._run_async(
            self._create_manual_workspace_rewrite_task(
                started_at=datetime.now(UTC) - timedelta(minutes=6),
                previous_status=EmailTaskStatus.MATCHED.value,
                source_body="改写前正文",
            ),
        )

        restored_count = self._run_async(recover_stale_workspace_draft_rewrites(self.session_factory))
        task = self._run_async(self._get_task(task_id))

        self.assertEqual(restored_count, 1)
        self.assertEqual(task.status, EmailTaskStatus.MATCHED.value)
        self.assertEqual(task.approved_body_text, "改写前正文")
        self.assertEqual(task.approved_body_html, "<p>改写前正文</p>")
        self.assertEqual(task.last_error, "AI 改写已中断，请重试")
        self.assertIsNone(task.draft_generation_previous_status)
        self.assertIsNone(task.draft_generation_started_at)

    def test_recover_stale_workspace_rewrite_skips_recent_started_at(self) -> None:
        task_id = self._run_async(
            self._create_manual_workspace_rewrite_task(
                started_at=datetime.now(UTC) - timedelta(minutes=4),
                previous_status=EmailTaskStatus.MATCHED.value,
                source_body="改写前正文",
            ),
        )

        restored_count = self._run_async(recover_stale_workspace_draft_rewrites(self.session_factory))
        task = self._run_async(self._get_task(task_id))

        self.assertEqual(restored_count, 0)
        self.assertEqual(task.status, EmailTaskStatus.GENERATING_DRAFT.value)
        self.assertEqual(task.draft_rewrite_source_body_text, "改写前正文")

    def test_recover_stale_workspace_rewrite_recovers_at_five_minute_cutoff(self) -> None:
        now = datetime.now(UTC)
        task_id = self._run_async(
            self._create_manual_workspace_rewrite_task(
                started_at=now - timedelta(minutes=5),
                previous_status=EmailTaskStatus.MATCHED.value,
                source_body="改写前正文",
            ),
        )

        restored_count = self._run_async(
            recover_stale_workspace_draft_rewrites(self.session_factory, now=now),
        )
        task = self._run_async(self._get_task(task_id))

        self.assertEqual(restored_count, 1)
        self.assertEqual(task.status, EmailTaskStatus.MATCHED.value)
        self.assertEqual(task.approved_body_text, "改写前正文")

    def test_recover_interrupted_workspace_rewrite_restores_recent_started_at(self) -> None:
        task_id = self._run_async(
            self._create_manual_workspace_rewrite_task(
                started_at=datetime.now(UTC) - timedelta(minutes=1),
                previous_status=EmailTaskStatus.MATCHED.value,
                source_body="重启前正文",
            ),
        )

        restored_count = self._run_async(
            recover_interrupted_workspace_draft_rewrites(self.session_factory),
        )
        task = self._run_async(self._get_task(task_id))

        self.assertEqual(restored_count, 1)
        self.assertEqual(task.status, EmailTaskStatus.MATCHED.value)
        self.assertEqual(task.approved_body_text, "重启前正文")
        self.assertEqual(task.last_error, "AI 改写已中断，请重试")

    def test_llm_failure_marks_draft_failed_without_retry(self) -> None:
        task_ids = self._run_async(self._create_batch_with_tasks([EmailTaskStatus.DISCOVERED.value]))

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
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
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
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
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
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
        self.assertEqual(task.draft_generation_source, "llm")
        self.assertIsNone(task.draft_fallback_reason)

    def test_items_missing_primary_material_are_not_claimed_for_generation(self) -> None:
        task_ids = self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.DISCOVERED.value],
                with_primary_material=False,
            ),
        )

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
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

    def test_items_missing_professor_research_direction_use_template_fallback(self) -> None:
        task_ids = self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.DISCOVERED.value],
                professor_research_direction="",
                identity_template_body_html="<p>当前身份模板不属于任务快照。</p>",
            ),
        )

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
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
        self.assertEqual(task.status, EmailTaskStatus.REVIEW_REQUIRED.value)
        self.assertEqual(task.outreach_generation_mode, "llm")
        self.assertEqual(task.draft_generation_source, "template_fallback")
        self.assertEqual(task.draft_fallback_reason, "missing_research_direction")
        self.assertEqual(task.generated_subject, "申请与张教授1老师交流")
        self.assertEqual(task.generated_content_text, "老师您好，我是王同学。")
        self.assertEqual(
            task.generated_content_html,
            "<p>老师您好，我是王同学。</p>",
        )
        self.assertIsNone(task.approved_at)
        mocked_generate.assert_not_awaited()

    def test_batch_draft_generation_keeps_batch_selected_materials(self) -> None:
        task_ids = self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.DISCOVERED.value],
                selected_material_ids=[101, 102],
            ),
        )

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
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
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
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

    def test_scheduler_round_robins_across_batch_tasks(self) -> None:
        self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.DISCOVERED.value] * 4,
                batch_name="A",
            )
        )
        self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.DISCOVERED.value],
                batch_name="B",
            )
        )
        started: list[str] = []

        async def fake_generate(**kwargs):
            started.append(kwargs["professor"].name.split("-")[0])
            await asyncio.sleep(0.01)
            return self._build_draft_generation_result()

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            new=AsyncMock(side_effect=fake_generate),
        ):
            processed = self._run_async(
                run_queued_batch_drafts_once(
                    self.session_factory,
                    concurrency=2,
                    coordinator=BatchDraftGenerationCoordinator(),
                )
            )

        self.assertEqual(processed, 5)
        self.assertEqual(set(started[:2]), {"A", "B"})

    def test_scheduler_refills_after_fast_item_while_another_item_is_slow(self) -> None:
        for batch_name in ("A", "B", "C"):
            self._run_async(
                self._create_batch_with_tasks(
                    [EmailTaskStatus.DISCOVERED.value],
                    batch_name=batch_name,
                )
            )

        async def scenario() -> bool:
            release_a = asyncio.Event()
            c_started = asyncio.Event()

            async def fake_generate(**kwargs):
                batch_name = kwargs["professor"].name.split("-")[0]
                if batch_name == "A":
                    await release_a.wait()
                if batch_name == "C":
                    c_started.set()
                return self._build_draft_generation_result()

            coordinator = BatchDraftGenerationCoordinator()
            scheduler = BatchDraftScheduler(
                self.session_factory,
                coordinator=coordinator,
            )
            with patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
                new=AsyncMock(side_effect=fake_generate),
            ):
                runner = asyncio.create_task(scheduler.run_until_idle(concurrency=2))
                await asyncio.wait_for(c_started.wait(), timeout=1)
                release_a.set()
                processed = await asyncio.wait_for(runner, timeout=3)
                return processed == 3

        self.assertTrue(self._run_async(scenario()))

    def test_scheduler_mixed_load_has_no_starvation_or_claim_leaks(self) -> None:
        task_ids: list[int] = []
        batch_names = {"A", "B", "C", "D", "E"}
        for batch_name in sorted(batch_names):
            task_ids.extend(
                self._run_async(
                    self._create_batch_with_tasks(
                        [EmailTaskStatus.DISCOVERED.value] * 5,
                        batch_name=batch_name,
                    )
                )
            )
        started: list[str] = []

        async def fake_generate(**kwargs):
            professor_name = kwargs["professor"].name
            started.append(professor_name)
            if professor_name == "A-0":
                await asyncio.sleep(0.1)
            if professor_name == "D-2":
                raise llm_runtime.LLMRuntimeError("mixed-load failure")
            return self._build_draft_generation_result()

        with patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
            new=AsyncMock(side_effect=fake_generate),
        ):
            processed = self._run_async(
                run_queued_batch_drafts_once(
                    self.session_factory,
                    concurrency=3,
                    coordinator=BatchDraftGenerationCoordinator(),
                )
            )

        tasks = [self._run_async(self._get_task(task_id)) for task_id in task_ids]
        self.assertEqual(processed, 25)
        self.assertEqual(len(started), 25)
        self.assertEqual(len(set(started)), 25)
        self.assertEqual(
            {professor_name.split("-")[0] for professor_name in started[:10]},
            batch_names,
            msg=f"unexpected early scheduler order: {started[:10]}",
        )
        self.assertEqual(
            sum(task.status == EmailTaskStatus.REVIEW_REQUIRED.value for task in tasks),
            24,
        )
        self.assertEqual(
            sum(task.status == EmailTaskStatus.DRAFT_FAILED.value for task in tasks),
            1,
        )
        self.assertTrue(all(task.draft_claim_id is None for task in tasks))
        self.assertTrue(all(task.draft_lease_expires_at is None for task in tasks))

    def test_batch_draft_total_timeout_marks_only_claimed_item_failed(self) -> None:
        task_ids = self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.DISCOVERED.value],
                batch_name="timeout",
            )
        )

        async def scenario() -> int:
            generation_started = asyncio.Event()

            async def never_finishes(**_kwargs):
                generation_started.set()
                await asyncio.Event().wait()

            with (
                patch(
                    "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
                    new=AsyncMock(side_effect=never_finishes),
                ),
                patch(
                    "app.modules.campaigns.drafts.runtime.WORKSPACE_DRAFT_REWRITE_TIMEOUT_SECONDS",
                    1.0,
                ),
            ):
                runner = asyncio.create_task(
                    run_queued_batch_drafts_once(
                        self.session_factory,
                        concurrency=1,
                        coordinator=BatchDraftGenerationCoordinator(),
                    )
                )
                await asyncio.wait_for(generation_started.wait(), timeout=3)
                return await asyncio.wait_for(runner, timeout=2)

        processed = self._run_async(scenario())

        task = self._run_async(self._get_task(task_ids[0]))
        self.assertEqual(processed, 1)
        self.assertEqual(task.status, EmailTaskStatus.DRAFT_FAILED.value)
        self.assertEqual(task.last_error, "AI 改写超时，请稍后重试")
        self.assertIsNone(task.draft_claim_id)

    def test_timeout_releases_slot_when_llm_ignores_cancellation(self) -> None:
        task_ids = self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.DISCOVERED.value],
                batch_name="uncancellable-timeout",
            )
        )

        async def scenario() -> int:
            release_late_response = asyncio.Event()
            generation_started = asyncio.Event()
            cancellation_seen = asyncio.Event()

            async def ignores_first_cancellation(**_kwargs):
                generation_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancellation_seen.set()
                    await release_late_response.wait()
                    return self._build_draft_generation_result()

            with (
                patch(
                    "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
                    new=AsyncMock(side_effect=ignores_first_cancellation),
                ),
                patch(
                    "app.modules.campaigns.drafts.runtime.WORKSPACE_DRAFT_REWRITE_TIMEOUT_SECONDS",
                    0.1,
                ),
                patch(
                    "app.modules.campaigns.drafts.runtime.BATCH_DRAFT_CANCEL_GRACE_SECONDS",
                    0.01,
                ),
            ):
                scheduler = BatchDraftScheduler(
                    self.session_factory,
                    coordinator=BatchDraftGenerationCoordinator(),
                )
                runner = asyncio.create_task(
                    scheduler.run_until_idle(concurrency=1)
                )
                await asyncio.wait_for(generation_started.wait(), timeout=1)
                processed = await asyncio.wait_for(
                    runner,
                    timeout=1,
                )
                await asyncio.wait_for(cancellation_seen.wait(), timeout=1)
                release_late_response.set()
                await asyncio.sleep(0.02)
                return processed

        processed = self._run_async(scenario())
        task = self._run_async(self._get_task(task_ids[0]))
        self.assertEqual(processed, 1)
        self.assertEqual(task.status, EmailTaskStatus.DRAFT_FAILED.value)
        self.assertEqual(task.last_error, "AI 改写超时，请稍后重试")
        self.assertIsNone(task.draft_claim_id)

    def test_recover_expired_batch_draft_lease_requeues_item(self) -> None:
        task_ids = self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.GENERATING_DRAFT.value],
                previous_status=EmailTaskStatus.MATCHED.value,
                batch_name="expired-lease",
            )
        )
        now = datetime.now(UTC)

        async def expire_claim() -> None:
            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_ids[0])
                assert task is not None
                task.draft_claim_id = "expired-claim"
                task.draft_claimed_at = now - timedelta(minutes=2)
                task.draft_generation_started_at = now - timedelta(minutes=2)
                task.draft_lease_expires_at = now - timedelta(seconds=1)
                await session.commit()

        self._run_async(expire_claim())
        recovered = self._run_async(
            recover_stale_generating_drafts(self.session_factory, now=now)
        )
        task = self._run_async(self._get_task(task_ids[0]))

        self.assertEqual(recovered, 1)
        self.assertEqual(task.status, EmailTaskStatus.MATCHED.value)
        self.assertIsNone(task.draft_claim_id)
        self.assertIsNone(task.draft_lease_expires_at)

    def test_recovery_does_not_requeue_claim_renewed_after_stale_read(self) -> None:
        task_ids = self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.GENERATING_DRAFT.value],
                previous_status=EmailTaskStatus.DISCOVERED.value,
                batch_name="lease-race",
            )
        )
        now = datetime.now(UTC)

        async def scenario() -> int:
            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_ids[0])
                assert task is not None
                task.draft_claim_id = "active-claim"
                task.draft_claimed_at = now - timedelta(minutes=2)
                task.draft_lease_expires_at = now - timedelta(seconds=1)
                await session.commit()

            async with self.session_factory() as stale_session:
                stale_task = await stale_session.get(EmailTask, task_ids[0])
                assert stale_task is not None
                await stale_session.refresh(stale_task, attribute_names=["batch_task"])
                async with self.session_factory() as heartbeat_session:
                    current_task = await heartbeat_session.get(EmailTask, task_ids[0])
                    assert current_task is not None
                    current_task.draft_lease_expires_at = now + timedelta(minutes=1)
                    await heartbeat_session.commit()
                recovered = await _recover_stale_batch_draft_task(
                    stale_session,
                    stale_task,
                    now=now,
                    cutoff=now - timedelta(minutes=30),
                )
                await stale_session.commit()
                return recovered

        recovered = self._run_async(scenario())
        task = self._run_async(self._get_task(task_ids[0]))
        self.assertEqual(recovered, 0)
        self.assertEqual(task.status, EmailTaskStatus.GENERATING_DRAFT.value)
        self.assertEqual(task.draft_claim_id, "active-claim")
        self.assertEqual(task.draft_lease_expires_at, now + timedelta(minutes=1))

    def test_cancelled_old_claim_does_not_overwrite_replacement_claim(self) -> None:
        task_ids = self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.GENERATING_DRAFT.value],
                previous_status=EmailTaskStatus.DISCOVERED.value,
                batch_name="claim-fence",
            )
        )

        async def scenario() -> None:
            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_ids[0])
                assert task is not None
                task.draft_claim_id = "old-claim"
                task.draft_claimed_at = datetime.now(UTC)
                task.draft_lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
                await session.commit()

            generation_started = asyncio.Event()

            async def blocked_generate(**_kwargs):
                generation_started.set()
                await asyncio.Event().wait()

            with patch(
                "app.modules.workspace.tasks.runtime.llm_runtime.generate_draft_content",
                new=AsyncMock(side_effect=blocked_generate),
            ):
                worker = asyncio.create_task(
                    generate_task_draft(
                        self.session_factory,
                        task_ids[0],
                        force=True,
                        automatic_batch=True,
                        require_running_batch=True,
                        draft_claim_id="old-claim",
                    )
                )
                await asyncio.wait_for(generation_started.wait(), timeout=1)
                async with self.session_factory() as session:
                    task = await session.get(EmailTask, task_ids[0])
                    assert task is not None
                    task.draft_claim_id = "replacement-claim"
                    task.draft_claimed_at = datetime.now(UTC)
                    task.draft_lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
                    await session.commit()
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker

        self._run_async(scenario())
        task = self._run_async(self._get_task(task_ids[0]))
        self.assertEqual(task.status, EmailTaskStatus.GENERATING_DRAFT.value)
        self.assertEqual(task.draft_claim_id, "replacement-claim")

    def test_finalize_lock_rejects_replacement_claim_after_stale_read(self) -> None:
        task_ids = self._run_async(
            self._create_batch_with_tasks(
                [EmailTaskStatus.GENERATING_DRAFT.value],
                previous_status=EmailTaskStatus.DISCOVERED.value,
                batch_name="finalize-fence",
            )
        )

        async def scenario() -> bool:
            async with self.session_factory() as session:
                task = await session.get(EmailTask, task_ids[0])
                assert task is not None
                task.draft_claim_id = "old-claim"
                await session.commit()

            async with self.session_factory() as stale_session:
                stale_task = await stale_session.get(EmailTask, task_ids[0])
                assert stale_task is not None
                async with self.session_factory() as replacement_session:
                    replacement = await replacement_session.get(EmailTask, task_ids[0])
                    assert replacement is not None
                    replacement.draft_claim_id = "replacement-claim"
                    await replacement_session.commit()
                locked = await _lock_current_batch_draft_claim(
                    stale_session,
                    stale_task,
                    "old-claim",
                )
                await stale_session.rollback()
                return locked

        self.assertFalse(self._run_async(scenario()))
        task = self._run_async(self._get_task(task_ids[0]))
        self.assertEqual(task.status, EmailTaskStatus.GENERATING_DRAFT.value)
        self.assertEqual(task.draft_claim_id, "replacement-claim")

    async def _create_schema(self) -> None:
        return None

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
        identity_template_body_html: str | None = None,
        batch_status: str = BatchTaskStatus.RUNNING.value,
        batch_name: str = "批量草稿任务",
    ) -> list[int]:
        fixture_id = uuid.uuid4().hex
        async with self.session_factory() as session:
            if await session.get(AppSetting, 1) is None:
                session.add(AppSetting(id=1))
            identity = IdentityProfile(
                name="测试身份",
                profile_name="测试身份",
                sender_name="王同学",
                email_address=f"sender-{fixture_id}@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="llm",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text="老师您好，我是{{sender_name}}。",
                outreach_template_body_html=identity_template_body_html,
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
                name=f"默认模型-{fixture_id}",
                provider="openai",
                api_base_url="https://api.example.com/v1",
                api_key="sk-test-key",
                model_name="gpt-test",
                is_default=True,
            )
            batch_task = BatchTask(
                identity=identity,
                llm_profile=llm_profile,
                name=batch_name,
                schedule_type="immediate",
                status=batch_status,
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
                        name=(
                            f"张教授{index}"
                            if batch_name == "批量草稿任务"
                            else f"{batch_name}-{index}"
                        ),
                        email=f"professor-{index}-{fixture_id}@example.edu",
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

    async def _create_manual_workspace_rewrite_task(
        self,
        *,
        started_at: datetime,
        previous_status: str,
        source_body: str,
    ) -> int:
        fixture_id = uuid.uuid4().hex
        async with self.session_factory() as session:
            session.add(AppSetting(id=1))
            identity = IdentityProfile(
                name="工作区恢复身份",
                profile_name="工作区恢复身份",
                sender_name="王同学",
                email_address=f"workspace-rewrite-{fixture_id}@example.com",
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
                sha256="1" * 64,
                extracted_text="My research focuses on agents.",
                material_type=IdentityMaterialType.RESUME.value,
            )
            identity.current_primary_material = material
            llm_profile = LLMProfile(
                name=f"工作区恢复模型-{fixture_id}",
                provider="openai",
                api_base_url="https://api.example.com/v1",
                api_key="sk-test-key",
                model_name="gpt-test",
                is_default=True,
            )
            task = EmailTask(
                source=EmailTaskSource.MANUAL.value,
                identity=identity,
                llm_profile=llm_profile,
                professor=Professor(
                    name="工作区恢复导师",
                    email=f"workspace-rewrite-professor-{fixture_id}@example.edu",
                    title="Professor",
                    university="Example University",
                    school="School of AI",
                    department="Computer Science",
                    research_direction="Large language models",
                    recent_papers=[],
                ),
                primary_material=material,
                status=EmailTaskStatus.GENERATING_DRAFT.value,
                draft_generation_previous_status=previous_status,
                draft_generation_started_at=started_at,
                draft_rewrite_source_subject="改写前主题",
                draft_rewrite_source_body_text=source_body,
                draft_rewrite_source_body_html=f"<p>{source_body}</p>",
                draft_rewrite_source_selected_material_ids=[],
                selected_material_ids=[],
                updated_at=started_at,
            )
            session.add(task)
            await session.commit()
            return task.id

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
