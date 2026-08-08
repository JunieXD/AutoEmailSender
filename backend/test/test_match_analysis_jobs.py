from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base,
    EmailTask,
    EmailTaskStatus,
    IdentityMaterial,
    IdentityMaterialType,
    IdentityProfessorMatchResult,
    IdentityProfile,
    LLMProfile,
    MatchAnalysisJob,
    MatchAnalysisJobItem,
    MatchAnalysisJobItemStatus,
    MatchAnalysisJobStatus,
    MatchAnalysisRun,
    OperationLog,
    Professor,
)
from app.modules.llm import runtime as llm_runtime
from app.modules.matching.public import (
    create_match_analysis_job,
    request_match_analysis_job_cancel,
    run_queued_match_analysis_jobs_once,
)
from app.modules.matching.job_runtime import (
    _MatchAnalysisItemClaim,
    _claim_next_match_analysis_item,
    _mark_item_succeeded,
    _recover_expired_match_analysis_items,
)


class MatchAnalysisJobRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._runtime_adaptation_patch = patch(
            "app.modules.matching.task_analysis.llm_runtime.ensure_llm_runtime_adaptation",
            new=AsyncMock(
                return_value=llm_runtime.LLMRuntimeAdaptation("chat_completions", None),
            ),
        )
        self._runtime_adaptation_patch.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "match_jobs.db"
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

    def test_create_job_deduplicates_professors_and_skips_missing_evidence(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )

        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0], professor_ids[0], professor_ids[1]],
                name="首轮匹配",
            ),
        )

        self.assertEqual(job.name, "首轮匹配")
        self.assertEqual(job.status, "queued")
        self.assertEqual(job.target_count, 1)
        self.assertEqual(job.skipped_count, 1)
        items = self._run_async(self._get_job_items(job.id))
        self.assertEqual(len(items), 2)
        self.assertEqual([item.status for item in items], ["queued", "skipped"])
        self.assertIsNone(items[0].email_task_id)
        self.assertEqual(items[1].skip_reason, "缺少研究方向或近期论文")

    def test_create_job_uses_local_time_in_default_name(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )
        utc_time = datetime(2026, 8, 7, 0, 15, tzinfo=UTC)
        local_time = utc_time.astimezone(timezone(timedelta(hours=8)))

        with (
            patch("app.modules.matching.job_runtime.utc_now", return_value=utc_time),
            patch("app.modules.matching.job_runtime.local_now", return_value=local_time),
        ):
            job = self._run_async(
                create_match_analysis_job(
                    self.session_factory,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                    professor_ids=[professor_ids[0]],
                ),
            )

        self.assertEqual(job.name, "批量匹配分析 2026-08-07 08:15")
        self.assertEqual(job.created_at, utc_time)

    def test_create_job_records_operation_log(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )

        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0], professor_ids[1]],
                name="首轮匹配",
            ),
        )

        logs = self._run_async(self._get_operation_logs("match_analysis_job.created"))
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].category, "match_analysis")
        self.assertEqual(logs[0].entity_type, "match_analysis_job")
        self.assertEqual(logs[0].entity_id, str(job.id))
        self.assertEqual(logs[0].event_metadata["target_count"], 1)
        self.assertEqual(logs[0].event_metadata["skipped_count"], 1)
        self.assertEqual(logs[0].event_metadata["identity_id"], identity_id)
        self.assertEqual(logs[0].event_metadata["llm_profile_id"], llm_profile_id)

    def test_run_queued_job_marks_success_and_updates_counts(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )
        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0]],
                name=None,
            ),
        )

        with patch(
            "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
            AsyncMock(return_value=self._build_match_evaluation_result(match_score=88)),
        ):
            processed = self._run_async(
                run_queued_match_analysis_jobs_once(
                    self.session_factory,
                    item_concurrency=1,
                ),
            )

        self.assertEqual(processed, 1)
        stored = self._run_async(self._get_job(job.id))
        self.assertEqual(stored.status, "completed")
        self.assertEqual(stored.succeeded_count, 1)
        self.assertEqual(stored.failed_count, 0)
        self.assertEqual(stored.total_cached_tokens, 25)
        self.assertEqual(stored.total_tokens, 100)
        items = self._run_async(self._get_job_items(job.id))
        self.assertEqual(items[0].cached_tokens, 25)
        self.assertIsNone(items[0].email_task_id)
        self.assertEqual(
            self._run_async(
                self._list_email_task_ids(
                    identity_id=identity_id,
                    professor_id=professor_ids[0],
                )
            ),
            [],
        )
        [run] = self._run_async(self._list_match_analysis_runs())
        [match_result] = self._run_async(self._list_canonical_match_results())
        self.assertIsNone(run.email_task_id)
        self.assertIsNone(match_result.source_email_task_id)

    def test_llm_runtime_failure_marks_item_and_job_failed(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )
        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0]],
            ),
        )

        with patch(
            "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
            AsyncMock(side_effect=llm_runtime.LLMRuntimeError("模型请求超时")),
        ):
            self._run_async(
                run_queued_match_analysis_jobs_once(
                    self.session_factory,
                    item_concurrency=1,
                ),
            )

        stored = self._run_async(self._get_job(job.id))
        [item] = self._run_async(self._get_job_items(job.id))
        self.assertEqual(stored.status, MatchAnalysisJobStatus.FAILED.value)
        self.assertEqual(stored.failed_count, 1)
        self.assertEqual(stored.succeeded_count, 0)
        self.assertEqual(item.status, MatchAnalysisJobItemStatus.FAILED.value)
        self.assertIn("模型请求超时", item.error_message)

    def test_item_timeout_finishes_when_calculation_ignores_cancellation(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )
        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0]],
            ),
        )

        async def scenario() -> tuple[int, str, bool]:
            release = False
            ignored_cancellation = asyncio.Event()
            generation_task: asyncio.Task[object] | None = None

            async def stubborn_generation(**_kwargs):
                nonlocal generation_task
                generation_task = asyncio.current_task()
                while not release:
                    try:
                        await asyncio.sleep(0.001)
                    except asyncio.CancelledError:
                        ignored_cancellation.set()
                return self._build_match_evaluation_result(match_score=88)

            real_timeout = asyncio.timeout
            with (
                patch(
                    "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
                    side_effect=stubborn_generation,
                ),
                patch(
                    "app.modules.matching.job_runtime.asyncio.timeout",
                    new=lambda _seconds: real_timeout(1),
                ),
                patch(
                    "app.modules.matching.job_runtime._MATCH_ANALYSIS_CANCEL_GRACE_SECONDS",
                    0.01,
                ),
            ):
                processed = await asyncio.wait_for(
                    run_queued_match_analysis_jobs_once(
                        self.session_factory,
                        item_concurrency=1,
                    ),
                    timeout=4,
                )

            [item] = await self._get_job_items(job.id)
            async with self.session_factory() as session:
                running_runs = list(
                    await session.scalars(
                        select(MatchAnalysisRun).where(
                            MatchAnalysisRun.professor_id == item.professor_id,
                            MatchAnalysisRun.status == "running",
                        )
                    )
                )
            cancellation_was_ignored = ignored_cancellation.is_set()
            release = True
            generation_finished = False
            if generation_task is not None:
                done, _ = await asyncio.wait({generation_task}, timeout=2)
                generation_finished = generation_task in done
            return (
                processed,
                item.status,
                cancellation_was_ignored and not running_runs and generation_finished,
            )

        processed, item_status, ignored_cancellation = self._run_async(scenario())
        self.assertEqual(processed, 1)
        self.assertEqual(item_status, MatchAnalysisJobItemStatus.FAILED.value)
        self.assertTrue(ignored_cancellation)

    def test_item_scheduler_round_robins_across_jobs(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(extra_analyzable_professor=True),
        )
        first_job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0], professor_ids[2]],
                name="large",
            ),
        )
        second_job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0]],
                name="small",
            ),
        )

        with patch(
            "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
            AsyncMock(return_value=self._build_match_evaluation_result(match_score=88)),
        ):
            processed = self._run_async(
                run_queued_match_analysis_jobs_once(
                    self.session_factory,
                    item_concurrency=2,
                ),
            )

        self.assertEqual(processed, 2)
        stored_first = self._run_async(self._get_job(first_job.id))
        stored_second = self._run_async(self._get_job(second_job.id))
        self.assertEqual(stored_first.status, MatchAnalysisJobStatus.QUEUED.value)
        self.assertEqual(stored_first.succeeded_count, 1)
        self.assertEqual(stored_second.status, MatchAnalysisJobStatus.COMPLETED.value)

    def test_concurrent_schedulers_do_not_duplicate_item_claims(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(extra_analyzable_professor=True),
        )
        jobs = [
            self._run_async(
                create_match_analysis_job(
                    self.session_factory,
                    identity_id=identity_id,
                    llm_profile_id=llm_profile_id,
                    professor_ids=[professor_id],
                ),
            )
            for professor_id in (professor_ids[0], professor_ids[2])
        ]

        async def delayed_generation(**_kwargs):
            await asyncio.sleep(0.05)
            return self._build_match_evaluation_result(match_score=88)

        async def run_both() -> list[int]:
            return await asyncio.gather(
                run_queued_match_analysis_jobs_once(
                    self.session_factory,
                    item_concurrency=1,
                ),
                run_queued_match_analysis_jobs_once(
                    self.session_factory,
                    item_concurrency=1,
                ),
            )

        with patch(
            "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
            AsyncMock(side_effect=delayed_generation),
        ) as mocked_generation:
            processed = self._run_async(run_both())

        stored_items = [
            item
            for job in jobs
            for item in self._run_async(self._get_job_items(job.id))
        ]
        self.assertEqual(processed, [1, 1])
        self.assertEqual(
            mocked_generation.await_count,
            2,
            [
                (item.status, item.skip_reason, item.error_message, item.attempt_count)
                for item in stored_items
            ],
        )
        self.assertTrue(
            all(
                self._run_async(self._get_job(job.id)).status
                == MatchAnalysisJobStatus.COMPLETED.value
                for job in jobs
            )
        )

    def test_expired_claim_is_recovered_but_late_result_is_fenced(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )
        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0]],
            ),
        )
        old_claim = self._run_async(
            _claim_next_match_analysis_item(self.session_factory),
        )
        assert old_claim is not None

        async def expire_claim() -> None:
            async with self.session_factory() as session:
                item = await session.get(MatchAnalysisJobItem, old_claim.item_id)
                assert item is not None
                item.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
                await session.commit()

        self._run_async(expire_claim())
        self.assertEqual(
            self._run_async(_recover_expired_match_analysis_items(self.session_factory)),
            1,
        )
        new_claim = self._run_async(
            _claim_next_match_analysis_item(self.session_factory),
        )
        assert new_claim is not None
        self._run_async(
            _mark_item_succeeded(
                self.session_factory,
                old_claim,
                run_id=None,
                prompt_tokens=10,
                completion_tokens=5,
                cached_tokens=0,
                total_tokens=15,
            ),
        )

        [item] = self._run_async(self._get_job_items(job.id))
        self.assertEqual(item.status, MatchAnalysisJobItemStatus.RUNNING.value)
        self.assertEqual(item.claim_id, new_claim.claim_id)
        self.assertEqual(item.total_tokens, 0)

    def test_nonexpired_claim_is_not_recovered(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )
        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0]],
            ),
        )
        claim = self._run_async(_claim_next_match_analysis_item(self.session_factory))
        assert claim is not None

        recovered = self._run_async(
            _recover_expired_match_analysis_items(self.session_factory),
        )

        [item] = self._run_async(self._get_job_items(job.id))
        self.assertEqual(recovered, 0)
        self.assertEqual(item.status, MatchAnalysisJobItemStatus.RUNNING.value)
        self.assertEqual(item.claim_id, claim.claim_id)

    def test_terminal_item_update_keeps_job_summary_current_and_idempotent(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )
        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0]],
                name=None,
            ),
        )

        async def mark_running() -> None:
            async with self.session_factory() as session:
                item = await session.scalar(
                    select(MatchAnalysisJobItem).where(MatchAnalysisJobItem.job_id == job.id)
                )
                assert item is not None
                item.status = MatchAnalysisJobItemStatus.RUNNING.value
                item.claim_id = "test-claim"
                item.claimed_at = datetime.now(UTC)
                item.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
                await session.commit()

        self._run_async(mark_running())
        self._run_async(
            _mark_item_succeeded(
                self.session_factory,
                _MatchAnalysisItemClaim(
                    job_id=job.id,
                    item_id=self._run_async(self._get_job_items(job.id))[0].id,
                    claim_id="test-claim",
                ),
                run_id=None,
                prompt_tokens=11,
                completion_tokens=7,
                cached_tokens=3,
                total_tokens=18,
            ),
        )
        self._run_async(
            _mark_item_succeeded(
                self.session_factory,
                _MatchAnalysisItemClaim(
                    job_id=job.id,
                    item_id=self._run_async(self._get_job_items(job.id))[0].id,
                    claim_id="test-claim",
                ),
                run_id=None,
                prompt_tokens=11,
                completion_tokens=7,
                cached_tokens=3,
                total_tokens=18,
            ),
        )

        stored = self._run_async(self._get_job(job.id))
        self.assertEqual(stored.status, MatchAnalysisJobStatus.QUEUED.value)
        self.assertEqual(stored.succeeded_count, 1)
        self.assertEqual(stored.total_prompt_tokens, 11)
        self.assertEqual(stored.total_completion_tokens, 7)
        self.assertEqual(stored.total_cached_tokens, 3)
        self.assertEqual(stored.total_tokens, 18)

    def test_run_queued_job_with_successes_and_skips_is_completed(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )
        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0], professor_ids[1]],
                name=None,
            ),
        )

        with patch(
            "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
            AsyncMock(return_value=self._build_match_evaluation_result(match_score=88)),
        ):
            self._run_async(
                run_queued_match_analysis_jobs_once(
                    self.session_factory,
                    item_concurrency=1,
                ),
            )

        stored = self._run_async(self._get_job(job.id))
        self.assertEqual(stored.status, MatchAnalysisJobStatus.COMPLETED.value)
        self.assertEqual(stored.succeeded_count, 1)
        self.assertEqual(stored.failed_count, 0)
        self.assertEqual(stored.skipped_count, 1)

    def test_legacy_queued_item_uses_job_profile_without_mutating_email_task(self) -> None:
        identity_id, first_llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )
        second_llm_profile_id = self._run_async(self._create_llm_profile(name="备用匹配模型"))
        existing_task_id = self._run_async(
            self._create_email_task(
                identity_id=identity_id,
                llm_profile_id=first_llm_profile_id,
                professor_id=professor_ids[0],
            ),
        )
        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=second_llm_profile_id,
                professor_ids=[professor_ids[0]],
                name=None,
            ),
        )
        self._run_async(self._link_job_item_to_email_task(job.id, existing_task_id))

        used_llm_profile_ids: list[int] = []

        async def fake_generate_match_evaluation(**kwargs):
            used_llm_profile_ids.append(kwargs["llm_profile"].id)
            return self._build_match_evaluation_result(match_score=93)

        with patch(
            "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
            AsyncMock(side_effect=fake_generate_match_evaluation),
        ):
            processed = self._run_async(
                run_queued_match_analysis_jobs_once(
                    self.session_factory,
                    item_concurrency=1,
                ),
            )

        items = self._run_async(self._get_job_items(job.id))
        task_ids = self._run_async(
            self._list_email_task_ids(identity_id=identity_id, professor_id=professor_ids[0]),
        )
        task = self._run_async(self._get_email_task(existing_task_id))
        self.assertEqual(processed, 1)
        self.assertEqual(items[0].email_task_id, existing_task_id)
        self.assertEqual(task_ids, [existing_task_id])
        self.assertEqual(used_llm_profile_ids, [second_llm_profile_id])
        self.assertEqual(task.llm_profile_id, first_llm_profile_id)
        [run] = self._run_async(self._list_match_analysis_runs())
        [match_result] = self._run_async(self._list_canonical_match_results())
        self.assertIsNone(run.email_task_id)
        self.assertIsNone(match_result.source_email_task_id)

    def test_match_job_does_not_touch_existing_email_task(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )
        existing_task_id = self._run_async(
            self._create_email_task(
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_id=professor_ids[0],
            ),
        )

        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0]],
                name=None,
            ),
        )

        items = self._run_async(self._get_job_items(job.id))
        task = self._run_async(self._get_email_task(existing_task_id))
        self.assertIsNone(items[0].email_task_id)
        self.assertIsNone(task.primary_material_id)

    def test_run_queued_job_records_completion_operation_log(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )
        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0]],
                name=None,
            ),
        )

        with patch(
            "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
            AsyncMock(return_value=self._build_match_evaluation_result(match_score=88)),
        ):
            self._run_async(
                run_queued_match_analysis_jobs_once(
                    self.session_factory,
                    item_concurrency=1,
                ),
            )

        logs = self._run_async(self._get_operation_logs("match_analysis_job.completed"))
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].category, "match_analysis")
        self.assertEqual(logs[0].entity_type, "match_analysis_job")
        self.assertEqual(logs[0].entity_id, str(job.id))
        self.assertEqual(logs[0].event_metadata["succeeded_count"], 1)
        self.assertEqual(logs[0].event_metadata["status"], "completed")

    def test_run_queued_job_keeps_going_after_item_failure(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(extra_analyzable_professor=True),
        )
        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0], professor_ids[2]],
                name=None,
            ),
        )

        failure = RuntimeError("模型临时失败")
        success = self._build_match_evaluation_result(match_score=91)
        with patch(
            "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
            AsyncMock(side_effect=[failure, success]),
        ):
            first_processed = self._run_async(
                run_queued_match_analysis_jobs_once(
                    self.session_factory,
                    item_concurrency=1,
                ),
            )
            second_processed = self._run_async(
                run_queued_match_analysis_jobs_once(
                    self.session_factory,
                    item_concurrency=1,
                ),
            )

        self.assertEqual(first_processed + second_processed, 2)
        stored = self._run_async(self._get_job(job.id))
        self.assertEqual(stored.status, "partial_failed")
        self.assertEqual(stored.failed_count, 1)
        self.assertEqual(stored.succeeded_count, 1)

    def test_run_queued_job_finishes_warmup_item_before_starting_remaining_items(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(extra_analyzable_professor=True),
        )
        self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0], professor_ids[2]],
                name=None,
            ),
        )

        async def scenario() -> tuple[bool, bool, int]:
            warmup_started = asyncio.Event()
            release_warmup = asyncio.Event()
            remaining_started = asyncio.Event()
            call_count = 0

            async def fake_generate_match_evaluation(**_kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    warmup_started.set()
                    await release_warmup.wait()
                else:
                    remaining_started.set()
                return self._build_match_evaluation_result(match_score=88)

            with patch(
                "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
                AsyncMock(side_effect=fake_generate_match_evaluation),
            ):
                worker = asyncio.create_task(
                    run_queued_match_analysis_jobs_once(
                        self.session_factory,
                        item_concurrency=2,
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
        self.assertEqual(processed, 1)

    def test_run_queued_match_analysis_jobs_ignores_deleted_job(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )
        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0]],
                name=None,
            ),
        )
        self._run_async(self._mark_job_deleted(job.id))

        processed = self._run_async(
            run_queued_match_analysis_jobs_once(
                self.session_factory,
                item_concurrency=1,
            ),
        )

        self.assertEqual(processed, 0)
        stored = self._run_async(self._get_job(job.id))
        self.assertEqual(stored.status, "queued")

    def test_cancel_job_marks_queued_items_canceled(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )
        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0]],
                name=None,
            ),
        )

        self._run_async(request_match_analysis_job_cancel(self.session_factory, job.id))
        processed = self._run_async(
            run_queued_match_analysis_jobs_once(
                self.session_factory,
                item_concurrency=1,
            ),
        )

        self.assertEqual(processed, 0)
        stored = self._run_async(self._get_job(job.id))
        self.assertEqual(stored.status, "canceled")
        items = self._run_async(self._get_job_items(job.id))
        self.assertEqual(items[0].status, "canceled")

    def test_cancel_running_job_cancels_active_llm_call(self) -> None:
        llm_call_canceled, stored, items = self._run_async(
            self._cancel_running_job_during_active_llm_call(),
        )

        self.assertTrue(llm_call_canceled)
        self.assertEqual(stored.status, "canceled")
        self.assertEqual(stored.succeeded_count, 0)
        self.assertEqual(stored.total_tokens, 0)
        self.assertEqual(items[0].status, "canceled")

    def test_cancel_requested_job_with_success_and_canceled_items_stays_canceled(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(extra_analyzable_professor=True),
        )
        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0], professor_ids[2]],
                name=None,
            ),
        )
        self._run_async(self._mark_job_partially_canceled(job.id))

        processed = self._run_async(
            run_queued_match_analysis_jobs_once(
                self.session_factory,
                item_concurrency=1,
            ),
        )

        self.assertEqual(processed, 0)
        stored = self._run_async(self._get_job(job.id))
        self.assertEqual(stored.status, "canceled")
        self.assertEqual(stored.succeeded_count, 1)

    def test_running_job_is_recovered_and_processed_after_worker_restart(self) -> None:
        identity_id, llm_profile_id, professor_ids = self._run_async(
            self._seed_create_job_data(),
        )
        job = self._run_async(
            create_match_analysis_job(
                self.session_factory,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_ids=[professor_ids[0]],
                name=None,
            ),
        )
        self._run_async(self._mark_job_running_after_interrupted_worker(job.id))

        with patch(
            "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
            AsyncMock(return_value=self._build_match_evaluation_result(match_score=88)),
        ):
            processed = self._run_async(
                run_queued_match_analysis_jobs_once(
                    self.session_factory,
                    item_concurrency=1,
                ),
            )

        self.assertEqual(processed, 1)
        stored = self._run_async(self._get_job(job.id))
        self.assertEqual(stored.status, "completed")
        self.assertEqual(stored.succeeded_count, 1)

    async def _create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def _seed_create_job_data(
        self,
        *,
        extra_analyzable_professor: bool = False,
    ) -> tuple[int, int, list[int]]:
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="测试身份",
                profile_name="测试身份",
                sender_name="测试学生",
                email_address="sender@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
            )
            llm_profile = LLMProfile(
                name="默认模型",
                provider="openai",
                api_base_url="https://api.example.com/v1",
                api_key="sk-test-key",
                model_name="gpt-4o-mini",
            )
            session.add_all([identity, llm_profile])
            await session.flush()
            material = IdentityMaterial(
                identity_id=identity.id,
                display_name="简历",
                original_filename="resume.txt",
                file_path="data/uploads/resume.txt",
                mime_type="text/plain",
                size_bytes=12,
                sha256="a" * 64,
                extracted_text="AI systems background",
                material_type=IdentityMaterialType.RESUME.value,
            )
            session.add(material)
            await session.flush()
            identity.current_primary_material_id = material.id
            analyzable = Professor(
                name="可分析导师",
                email="matchable@example.edu",
                title="Professor",
                university="Example University",
                school="Computing",
                research_direction="AI agents",
                recent_papers=[],
            )
            missing_evidence = Professor(
                name="缺少证据导师",
                email="missing@example.edu",
                title="Professor",
                university="Example University",
                school="Computing",
                research_direction=None,
                recent_papers=[],
            )
            professors = [analyzable, missing_evidence]
            if extra_analyzable_professor:
                professors.append(
                    Professor(
                        name="第二位可分析导师",
                        email="matchable-2@example.edu",
                        title="Professor",
                        university="Example University",
                        school="Computing",
                        research_direction="Information Extraction",
                        recent_papers=[],
                    )
                )
            session.add_all(professors)
            await session.commit()
            return identity.id, llm_profile.id, [professor.id for professor in professors]

    async def _get_job(self, job_id: int) -> MatchAnalysisJob:
        async with self.session_factory() as session:
            job = await session.get(MatchAnalysisJob, job_id)
            assert job is not None
            return job

    async def _get_email_task(self, task_id: int) -> EmailTask:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, task_id)
            assert task is not None
            return task

    async def _get_job_items(self, job_id: int) -> list[MatchAnalysisJobItem]:
        async with self.session_factory() as session:
            return list(
                await session.scalars(
                    select(MatchAnalysisJobItem)
                    .where(MatchAnalysisJobItem.job_id == job_id)
                    .order_by(MatchAnalysisJobItem.id.asc()),
                ),
            )

    async def _get_operation_logs(self, event_name: str) -> list[OperationLog]:
        async with self.session_factory() as session:
            return list(
                await session.scalars(
                    select(OperationLog)
                    .where(OperationLog.event_name == event_name)
                    .order_by(OperationLog.id.asc()),
                ),
            )

    async def _list_match_analysis_runs(self) -> list[MatchAnalysisRun]:
        async with self.session_factory() as session:
            return list(
                await session.scalars(
                    select(MatchAnalysisRun).order_by(MatchAnalysisRun.id.asc()),
                ),
            )

    async def _list_canonical_match_results(
        self,
    ) -> list[IdentityProfessorMatchResult]:
        async with self.session_factory() as session:
            return list(
                await session.scalars(
                    select(IdentityProfessorMatchResult).order_by(
                        IdentityProfessorMatchResult.id.asc()
                    ),
                ),
            )

    async def _create_llm_profile(self, *, name: str) -> int:
        async with self.session_factory() as session:
            llm_profile = LLMProfile(
                name=name,
                provider="openai",
                api_base_url="https://api-alt.example.com/v1",
                api_key="sk-test-alt",
                model_name="gpt-alt",
            )
            session.add(llm_profile)
            await session.commit()
            return llm_profile.id

    async def _create_email_task(
        self,
        *,
        identity_id: int,
        llm_profile_id: int,
        professor_id: int,
    ) -> int:
        async with self.session_factory() as session:
            task = EmailTask(
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                professor_id=professor_id,
                status=EmailTaskStatus.DISCOVERED.value,
            )
            session.add(task)
            await session.commit()
            return task.id

    async def _list_email_task_ids(self, *, identity_id: int, professor_id: int) -> list[int]:
        async with self.session_factory() as session:
            return list(
                await session.scalars(
                    select(EmailTask.id)
                    .where(
                        EmailTask.identity_id == identity_id,
                        EmailTask.professor_id == professor_id,
                    )
                    .order_by(EmailTask.id.asc()),
                ),
            )

    async def _link_job_item_to_email_task(self, job_id: int, task_id: int) -> None:
        async with self.session_factory() as session:
            item = await session.scalar(
                select(MatchAnalysisJobItem).where(
                    MatchAnalysisJobItem.job_id == job_id
                )
            )
            assert item is not None
            item.email_task_id = task_id
            await session.commit()

    async def _mark_job_partially_canceled(self, job_id: int) -> None:
        async with self.session_factory() as session:
            job = await session.get(MatchAnalysisJob, job_id)
            assert job is not None
            items = list(
                await session.scalars(
                    select(MatchAnalysisJobItem)
                    .where(MatchAnalysisJobItem.job_id == job_id)
                    .order_by(MatchAnalysisJobItem.id.asc()),
                ),
            )
            job.status = MatchAnalysisJobStatus.RUNNING.value
            job.cancel_requested_at = job.updated_at
            items[0].status = MatchAnalysisJobItemStatus.SUCCEEDED.value
            items[0].prompt_tokens = 60
            items[0].completion_tokens = 40
            items[0].total_tokens = 100
            items[1].status = MatchAnalysisJobItemStatus.CANCELED.value
            await session.commit()

    async def _mark_job_deleted(self, job_id: int) -> None:
        async with self.session_factory() as session:
            job = await session.get(MatchAnalysisJob, job_id)
            assert job is not None
            job.deleted_at = datetime.now(UTC)
            await session.commit()

    async def _mark_job_running_after_interrupted_worker(self, job_id: int) -> None:
        async with self.session_factory() as session:
            job = await session.get(MatchAnalysisJob, job_id)
            assert job is not None
            job.status = MatchAnalysisJobStatus.RUNNING.value
            await session.commit()

    async def _cancel_running_job_during_active_llm_call(
        self,
    ) -> tuple[bool, MatchAnalysisJob, list[MatchAnalysisJobItem]]:
        identity_id, llm_profile_id, professor_ids = await self._seed_create_job_data()
        job = await create_match_analysis_job(
            self.session_factory,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            professor_ids=[professor_ids[0]],
            name=None,
        )
        llm_call_started = asyncio.Event()
        llm_call_canceled = asyncio.Event()
        release_llm_call = asyncio.Event()

        async def fake_generate_match_evaluation(**kwargs):
            llm_call_started.set()
            try:
                await release_llm_call.wait()
            except asyncio.CancelledError:
                llm_call_canceled.set()
                raise
            return self._build_match_evaluation_result(match_score=88)

        with patch(
            "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
            AsyncMock(side_effect=fake_generate_match_evaluation),
        ):
            worker_task = asyncio.create_task(
                run_queued_match_analysis_jobs_once(
                    self.session_factory,
                    item_concurrency=1,
                ),
            )
            await asyncio.wait_for(llm_call_started.wait(), timeout=1)
            await request_match_analysis_job_cancel(self.session_factory, job.id)
            try:
                await asyncio.wait_for(llm_call_canceled.wait(), timeout=0.5)
            except TimeoutError:
                release_llm_call.set()
            await asyncio.wait_for(worker_task, timeout=1)

        return llm_call_canceled.is_set(), await self._get_job(job.id), await self._get_job_items(job.id)

    @staticmethod
    def _run_async(awaitable):
        return asyncio.run(awaitable)

    @staticmethod
    def _build_match_evaluation_result(*, match_score: int):
        return SimpleNamespace(
            result=SimpleNamespace(
                match_score=match_score,
                match_reason="研究方向匹配",
                fit_points=["方向一致"],
                risk_points=[],
                keywords=["AI agents"],
            ),
            usage=SimpleNamespace(
                prompt_tokens=60,
                completion_tokens=40,
                total_tokens=100,
                cached_tokens=25,
            ),
            duration_ms=1200,
            endpoint_kind="chat_completions",
            status_code=200,
            prompt_hash="prompt-hash",
            stable_prefix_hash="prefix-hash",
        )
