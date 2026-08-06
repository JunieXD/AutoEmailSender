from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.time import utc_now
from app.models import (
    CrawlCandidateEnrichmentTask,
    CrawlCandidateEnrichmentTaskStatus,
    CrawlJob,
    CrawlJobKind,
    CrawlJobRun,
    CrawlJobStatus,
    LLMProfile,
    Professor,
)
from app.modules.crawler.pages.tools import CandidateEnrichmentPayload
from app.services.crawler_v2_enrichment_worker import run_crawler_v2_enrichment_worker_once
from app.services.crawler_v2_scheduler import finalize_idle_jobs
from app.services.crawl_job_runtime import recover_interrupted_crawl_jobs
from app.modules.professors.public import (
    create_professor_information_enrichment_job,
    finalize_professor_information_enrichment_job,
    get_professor_information_enrichment_job,
    list_professor_information_enrichment_items,
)
from app.services.token_usage_records import list_token_usage_records
from test.schema_database import create_schema_sqlite_database


class ProfessorInformationEnrichmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        create_schema_sqlite_database(Path(self.db_path))
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{Path(self.db_path).as_posix()}",
        )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.session_factory() as session:
            profile = LLMProfile(
                name="默认模型",
                provider="openai",
                api_base_url="https://api.example.com/v1",
                api_key="sk-test",
                model_name="gpt-test",
                is_default=True,
            )
            session.add(profile)
            await session.commit()
            self.llm_profile_id = profile.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    async def test_batch_creation_keeps_ineligible_professors_as_skipped_items(self) -> None:
        active_id = await self._create_professor(
            name="可补全导师",
            profile_url="https://example.edu/active",
        )
        missing_url_id = await self._create_professor(name="缺少主页导师")
        complete_id = await self._create_professor(
            name="资料完整导师",
            email="complete@example.edu",
            title="教授",
            department="计算机系",
            research_direction="人工智能",
            recent_papers=["Paper A"],
            profile_url="https://example.edu/complete",
        )
        archived_id = await self._create_professor(
            name="回收站导师",
            profile_url="https://example.edu/archived",
            archived=True,
        )

        job_id = await create_professor_information_enrichment_job(
            self.session_factory,
            professor_ids=[active_id, missing_url_id, complete_id, archived_id, active_id],
            llm_profile_id=self.llm_profile_id,
            trigger_mode="batch",
        )

        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            items = await list_professor_information_enrichment_items(session, job_id)
        assert job is not None and items is not None
        self.assertEqual(job.job_kind, CrawlJobKind.PROFESSOR_ENRICHMENT.value)
        self.assertTrue(job.task_center_visible)
        self.assertEqual(job.progress_total, 4)
        self.assertEqual([item.status for item in items], ["queued", "skipped", "skipped", "skipped"])
        self.assertEqual(items[1].skip_reason, "缺少有效的导师主页链接")
        self.assertEqual(items[2].skip_reason, "资料已完整，无需补全")
        self.assertEqual(items[3].skip_reason, "导师已在回收站")

    async def test_successes_and_skips_finalize_as_completed(self) -> None:
        active_id = await self._create_professor(
            name="可补全导师",
            profile_url="https://example.edu/active",
        )
        skipped_id = await self._create_professor(name="缺少主页导师")
        job_id = await create_professor_information_enrichment_job(
            self.session_factory,
            professor_ids=[active_id, skipped_id],
            llm_profile_id=self.llm_profile_id,
            trigger_mode="batch",
        )

        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            succeeded_task = await session.scalar(
                select(CrawlCandidateEnrichmentTask).where(
                    CrawlCandidateEnrichmentTask.job_id == job_id,
                    CrawlCandidateEnrichmentTask.professor_id == active_id,
                )
            )
            assert job is not None and succeeded_task is not None
            succeeded_task.status = CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value
            succeeded_task.finished_at = utc_now()
            await finalize_professor_information_enrichment_job(session, job)
            await session.commit()

        async with self.session_factory() as session:
            job_read = await get_professor_information_enrichment_job(session, job_id)

        assert job_read is not None
        self.assertEqual(job_read.status, CrawlJobStatus.COMPLETED.value)
        self.assertEqual(job_read.succeeded_count, 1)
        self.assertEqual(job_read.failed_count, 0)
        self.assertEqual(job_read.skipped_count, 1)

    async def test_single_creation_rejects_an_existing_active_job(self) -> None:
        professor_id = await self._create_professor(
            name="重复导师",
            profile_url="https://example.edu/repeated",
        )
        await create_professor_information_enrichment_job(
            self.session_factory,
            professor_ids=[professor_id],
            llm_profile_id=self.llm_profile_id,
            trigger_mode="single",
        )

        with self.assertRaisesRegex(RuntimeError, "已有信息补全"):
            await create_professor_information_enrichment_job(
                self.session_factory,
                professor_ids=[professor_id],
                llm_profile_id=self.llm_profile_id,
                trigger_mode="single",
            )

    async def test_worker_only_fills_fields_that_are_still_empty_at_commit(self) -> None:
        professor_id = await self._create_professor(
            name="并发编辑导师",
            email="existing@example.edu",
            title="副教授",
            profile_url="https://example.edu/concurrent",
        )
        job_id = await create_professor_information_enrichment_job(
            self.session_factory,
            professor_ids=[professor_id],
            llm_profile_id=self.llm_profile_id,
            trigger_mode="single",
        )
        task_id = await self._claim_only_task(job_id)
        async with self.session_factory() as session:
            professor = await session.get(Professor, professor_id)
            assert professor is not None
            professor.department = "用户刚保存的系所"
            await session.commit()

        payload = CandidateEnrichmentPayload(
            email="model@example.edu",
            title="教授",
            department="模型识别的系所",
            research_direction="可信人工智能",
            recent_papers=["Paper A"],
        )
        usage = {
            "input_tokens": 120,
            "output_tokens": 30,
            "cached_tokens": 10,
            "total_tokens": 150,
        }
        with patch(
            "app.services.crawler_v2_enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(return_value=(payload, usage)),
        ):
            processed = await run_crawler_v2_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="test-worker",
            )
        self.assertEqual(processed, 1)

        async with self.session_factory() as session:
            await finalize_idle_jobs(session)
            await session.commit()
            professor = await session.get(Professor, professor_id)
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            job_read = await get_professor_information_enrichment_job(session, job_id)
            token_records = await list_token_usage_records(
                session,
                feature_type="information_enrichment",
            )

        assert professor is not None and task is not None and job_read is not None
        self.assertEqual(professor.email, "existing@example.edu")
        self.assertEqual(professor.title, "副教授")
        self.assertEqual(professor.department, "用户刚保存的系所")
        self.assertEqual(professor.research_direction, "可信人工智能")
        self.assertEqual(professor.recent_papers, ["Paper A"])
        self.assertEqual(task.enriched_fields, ["research_direction", "recent_papers"])
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value)
        self.assertEqual(job_read.status, CrawlJobStatus.COMPLETED.value)
        self.assertEqual(job_read.total_tokens, 150)
        self.assertEqual(len(token_records.records), 1)
        self.assertEqual(token_records.records[0].feature_type, "information_enrichment")
        self.assertEqual(token_records.records[0].title, "并发编辑导师 · 信息补全")

    async def test_terminal_error_is_sanitized_without_losing_original_reason(self) -> None:
        professor_id = await self._create_professor(
            name="失败导师",
            profile_url="https://example.edu/failure",
        )
        job_id = await create_professor_information_enrichment_job(
            self.session_factory,
            professor_ids=[professor_id],
            llm_profile_id=self.llm_profile_id,
            trigger_mode="single",
        )
        task_id = await self._claim_only_task(job_id, attempt_count=4)

        with patch(
            "app.services.crawler_v2_enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(
                side_effect=ValueError(
                    "HTTP 401 Authorization: Bearer top-secret\n"
                    "Cookie: session=session-secret; secondary=other-secret\n"
                    "trace=upstream-timeout",
                ),
            ),
        ):
            await run_crawler_v2_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="test-worker",
            )

        async with self.session_factory() as session:
            await finalize_idle_jobs(session)
            await session.commit()
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            job_read = await get_professor_information_enrichment_job(session, job_id)
        assert task is not None and job_read is not None
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value)
        self.assertIn("HTTP 401", task.last_error or "")
        self.assertIn("[REDACTED]", task.last_error or "")
        self.assertNotIn("top-secret", task.last_error or "")
        self.assertNotIn("session-secret", task.last_error or "")
        self.assertNotIn("other-secret", task.last_error or "")
        self.assertIn("upstream-timeout", task.last_error or "")
        self.assertEqual(job_read.status, CrawlJobStatus.FAILED.value)

    async def test_interrupted_information_enrichment_is_requeued_for_scheduler_finalization(
        self,
    ) -> None:
        professor_id = await self._create_professor(
            name="重启恢复导师",
            profile_url="https://example.edu/recovery",
        )
        job_id = await create_professor_information_enrichment_job(
            self.session_factory,
            professor_ids=[professor_id],
            llm_profile_id=self.llm_profile_id,
            trigger_mode="single",
        )
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            task = await session.scalar(
                select(CrawlCandidateEnrichmentTask).where(
                    CrawlCandidateEnrichmentTask.job_id == job_id,
                )
            )
            assert job is not None and task is not None and job.current_run_id is not None
            run = await session.get(CrawlJobRun, job.current_run_id)
            assert run is not None
            job.status = CrawlJobStatus.RUNNING.value
            run.status = CrawlJobStatus.RUNNING.value
            task.status = CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value
            task.finished_at = utc_now()
            await session.commit()

        await recover_interrupted_crawl_jobs(self.session_factory)
        async with self.session_factory() as session:
            recovered = await session.get(CrawlJob, job_id)
            assert recovered is not None
            self.assertEqual(recovered.status, CrawlJobStatus.QUEUED.value)
            await finalize_idle_jobs(session)
            await session.commit()

        async with self.session_factory() as session:
            finalized = await session.get(CrawlJob, job_id)
            assert finalized is not None
            self.assertEqual(finalized.status, CrawlJobStatus.COMPLETED.value)

    async def _create_professor(
        self,
        *,
        name: str,
        email: str | None = None,
        title: str | None = None,
        department: str | None = None,
        research_direction: str | None = None,
        recent_papers: list[str] | None = None,
        profile_url: str | None = None,
        archived: bool = False,
    ) -> int:
        async with self.session_factory() as session:
            professor = Professor(
                name=name,
                email=email,
                title=title,
                university="示例大学",
                school="计算机学院",
                department=department,
                research_direction=research_direction,
                recent_papers=recent_papers,
                profile_url=profile_url,
                archived_at=utc_now() if archived else None,
            )
            session.add(professor)
            await session.commit()
            return professor.id

    async def _claim_only_task(self, job_id: int, *, attempt_count: int = 1) -> int:
        async with self.session_factory() as session:
            task = await session.scalar(
                select(CrawlCandidateEnrichmentTask).where(
                    CrawlCandidateEnrichmentTask.job_id == job_id,
                )
            )
            assert task is not None
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            job.status = CrawlJobStatus.RUNNING.value
            task.status = CrawlCandidateEnrichmentTaskStatus.PROCESSING.value
            task.worker_id = "test-worker"
            task.attempt_count = attempt_count
            await session.commit()
            return task.id

if __name__ == "__main__":
    unittest.main()
