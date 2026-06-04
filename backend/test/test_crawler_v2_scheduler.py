from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    CrawlCandidate,
    CrawlCandidateEnrichmentTask,
    CrawlCandidateEnrichmentTaskStatus,
    CrawlJob,
    CrawlJobRun,
    CrawlJobStatus,
    CrawlPageTask,
    CrawlPageTaskStatus,
    CrawlPageChunk,
    CrawlPageChunkStatus,
)
from app.services.crawler_v2_models import CrawlerV2WorkKind, CrawlerV2WorkerConfig
from test.schema_database import create_schema_sqlite_database
from app.services.crawler_v2_scheduler import claim_next_v2_work, ensure_job_active, run_crawler_v2_scheduler_once


class CrawlerV2SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        create_schema_sqlite_database(Path(self.db_path))
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.db_path).as_posix()}")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass


    async def test_default_chunk_concurrency_is_three(self) -> None:
        self.assertEqual(CrawlerV2WorkerConfig().chunk_concurrency, 3)

    async def test_claims_chunk_before_page_and_enrichment(self) -> None:
        job_id = await self._create_job()
        async with self.session_factory() as session:
            session.add(CrawlPageTask(job_id=job_id, normalized_url="https://example.edu/a", original_url="https://example.edu/a"))
            session.add(CrawlPageChunk(job_id=job_id, page_id=None, source_url="https://example.edu/a", page_fingerprint="p", chunk_id="c1", chunk_index=0, chunk_hash="h", content="张三"))
            candidate = CrawlCandidate(job_id=job_id, name="张三")
            session.add(candidate)
            await session.flush()
            session.add(CrawlCandidateEnrichmentTask(job_id=job_id, candidate_id=candidate.id))
            await session.commit()

        claimed = await claim_next_v2_work(self.session_factory, worker_id="w1", config=CrawlerV2WorkerConfig())

        self.assertEqual(claimed.kind, CrawlerV2WorkKind.CHUNK)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, claimed.work_item_id)
            assert chunk is not None
            self.assertEqual(chunk.status, CrawlPageChunkStatus.PROCESSING.value)
            self.assertEqual(chunk.worker_id, "w1")
            self.assertIsNotNone(chunk.lease_expires_at)


    async def test_does_not_claim_new_page_while_chunk_is_pending(self) -> None:
        job_id = await self._create_job()
        async with self.session_factory() as session:
            session.add(CrawlPageTask(job_id=job_id, normalized_url="https://example.edu/page2", original_url="https://example.edu/page2"))
            session.add(CrawlPageChunk(job_id=job_id, page_id=None, source_url="https://example.edu/faculty", page_fingerprint="p", chunk_id="c1", chunk_index=0, chunk_hash="h", content="张三"))
            await session.commit()

        claimed = await claim_next_v2_work(self.session_factory, worker_id="w1", config=CrawlerV2WorkerConfig())

        self.assertEqual(claimed.kind, CrawlerV2WorkKind.CHUNK)
        async with self.session_factory() as session:
            page_task = await session.scalar(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id))
            chunk = await session.get(CrawlPageChunk, claimed.work_item_id)
        assert page_task is not None and chunk is not None
        self.assertEqual(page_task.status, CrawlPageTaskStatus.PENDING.value)
        self.assertEqual(chunk.status, CrawlPageChunkStatus.PROCESSING.value)

    async def test_does_not_claim_when_job_paused(self) -> None:
        job_id = await self._create_job(status=CrawlJobStatus.PAUSED.value)
        async with self.session_factory() as session:
            session.add(CrawlPageTask(job_id=job_id, normalized_url="https://example.edu/a", original_url="https://example.edu/a"))
            await session.commit()

        claimed = await claim_next_v2_work(self.session_factory, worker_id="w1", config=CrawlerV2WorkerConfig())

        self.assertEqual(claimed.kind, CrawlerV2WorkKind.IDLE)
    async def test_claiming_work_marks_queued_job_and_run_running(self) -> None:
        job_id = await self._create_job(status=CrawlJobStatus.QUEUED.value)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            run = CrawlJobRun(job_id=job_id, attempt_number=1, status=CrawlJobStatus.QUEUED.value)
            session.add(run)
            await session.flush()
            job.current_run_id = run.id
            session.add(CrawlPageTask(job_id=job_id, normalized_url="https://example.edu/a", original_url="https://example.edu/a"))
            await session.commit()
            run_id = run.id

        claimed = await claim_next_v2_work(self.session_factory, worker_id="w1", config=CrawlerV2WorkerConfig())

        self.assertEqual(claimed.kind, CrawlerV2WorkKind.PAGE)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            run = await session.get(CrawlJobRun, run_id)
        assert job is not None and run is not None
        self.assertEqual(job.status, CrawlJobStatus.RUNNING.value)
        self.assertEqual(run.status, CrawlJobStatus.RUNNING.value)
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.active_started_at)

    async def test_expired_processing_page_can_be_reclaimed(self) -> None:
        job_id = await self._create_job()
        expired = datetime.now(UTC) - timedelta(seconds=1)
        async with self.session_factory() as session:
            session.add(CrawlPageTask(job_id=job_id, normalized_url="https://example.edu/a", original_url="https://example.edu/a", status=CrawlPageTaskStatus.PROCESSING.value, worker_id="old", lease_expires_at=expired))
            await session.commit()

        claimed = await claim_next_v2_work(self.session_factory, worker_id="new", config=CrawlerV2WorkerConfig())

        self.assertEqual(claimed.kind, CrawlerV2WorkKind.PAGE)
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, claimed.work_item_id)
            assert task is not None
            self.assertEqual(task.worker_id, "new")

    async def test_claim_uses_runtime_enrichment_concurrency_setting(self) -> None:
        job_id = await self._create_job()
        active_until = datetime.now(UTC) + timedelta(minutes=5)
        async with self.session_factory() as session:
            from app.models.app_setting import AppSetting

            session.add(AppSetting(crawler_profile_enrichment_concurrency=1, crawler_host_concurrency=1))
            first = CrawlCandidate(job_id=job_id, name="张三", profile_url="https://example.edu/a")
            second = CrawlCandidate(job_id=job_id, name="李四", profile_url="https://example.edu/b")
            session.add_all([first, second])
            await session.flush()
            session.add_all([
                CrawlCandidateEnrichmentTask(job_id=job_id, candidate_id=first.id, status=CrawlCandidateEnrichmentTaskStatus.PROCESSING.value, worker_id="active", lease_expires_at=active_until),
                CrawlCandidateEnrichmentTask(job_id=job_id, candidate_id=second.id, status=CrawlCandidateEnrichmentTaskStatus.PENDING.value),
            ])
            await session.commit()

        claimed = await claim_next_v2_work(self.session_factory, worker_id="w1")

        self.assertEqual(claimed.kind, CrawlerV2WorkKind.IDLE)

    async def test_claim_respects_runtime_host_concurrency_for_enrichment(self) -> None:
        job_id = await self._create_job()
        active_until = datetime.now(UTC) + timedelta(minutes=5)
        async with self.session_factory() as session:
            from app.models.app_setting import AppSetting

            session.add(AppSetting(crawler_profile_enrichment_concurrency=3, crawler_host_concurrency=1))
            active = CrawlCandidate(job_id=job_id, name="张三", profile_url="https://same.example.edu/a")
            same_host = CrawlCandidate(job_id=job_id, name="李四", profile_url="https://same.example.edu/b")
            other_host = CrawlCandidate(job_id=job_id, name="王五", profile_url="https://other.example.edu/c")
            session.add_all([active, same_host, other_host])
            await session.flush()
            session.add_all([
                CrawlCandidateEnrichmentTask(job_id=job_id, candidate_id=active.id, status=CrawlCandidateEnrichmentTaskStatus.PROCESSING.value, worker_id="active", lease_expires_at=active_until),
                CrawlCandidateEnrichmentTask(job_id=job_id, candidate_id=same_host.id, status=CrawlCandidateEnrichmentTaskStatus.PENDING.value),
                CrawlCandidateEnrichmentTask(job_id=job_id, candidate_id=other_host.id, status=CrawlCandidateEnrichmentTaskStatus.PENDING.value),
            ])
            await session.commit()
            other_id = other_host.id

        claimed = await claim_next_v2_work(self.session_factory, worker_id="w1")

        self.assertEqual(claimed.kind, CrawlerV2WorkKind.ENRICHMENT)
        async with self.session_factory() as session:
            claimed_task = await session.get(CrawlCandidateEnrichmentTask, claimed.work_item_id)
        assert claimed_task is not None
        self.assertEqual(claimed_task.candidate_id, other_id)

    async def test_scheduler_marks_job_failed_when_no_work_and_no_candidates_remain(self) -> None:
        job_id = await self._create_job()

        processed = await run_crawler_v2_scheduler_once(self.session_factory, worker_id="w1")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            self.assertEqual(job.status, CrawlJobStatus.FAILED.value)
            self.assertEqual(job.error_message, "抓取未发现候选导师")

    async def test_scheduler_marks_job_needs_review_when_candidates_exist_and_no_work_remains(self) -> None:
        job_id = await self._create_job()
        async with self.session_factory() as session:
            session.add(CrawlCandidate(job_id=job_id, name="张三"))
            await session.commit()

        processed = await run_crawler_v2_scheduler_once(self.session_factory, worker_id="w1")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            self.assertEqual(job.status, CrawlJobStatus.NEEDS_REVIEW.value)
            self.assertIsNone(job.error_message)

    async def test_scheduler_does_not_finalize_job_with_active_processing_page(self) -> None:
        job_id = await self._create_job()
        active_until = datetime.now(UTC) + timedelta(minutes=5)
        async with self.session_factory() as session:
            session.add(
                CrawlPageTask(
                    job_id=job_id,
                    normalized_url="https://example.edu/a",
                    original_url="https://example.edu/a",
                    status=CrawlPageTaskStatus.PROCESSING.value,
                    worker_id="active-worker",
                    lease_expires_at=active_until,
                )
            )
            await session.commit()

        processed = await run_crawler_v2_scheduler_once(self.session_factory, worker_id="scheduler")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            task = await session.scalar(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id))
        assert job is not None and task is not None
        self.assertEqual(job.status, CrawlJobStatus.RUNNING.value)
        self.assertEqual(task.status, CrawlPageTaskStatus.PROCESSING.value)

    async def test_scheduler_does_not_finalize_job_with_active_processing_chunk(self) -> None:
        job_id = await self._create_job()
        active_until = datetime.now(UTC) + timedelta(minutes=5)
        async with self.session_factory() as session:
            session.add(
                CrawlPageChunk(
                    job_id=job_id,
                    page_id=None,
                    source_url="https://example.edu/a",
                    page_fingerprint="p",
                    chunk_id="c-active",
                    chunk_index=0,
                    chunk_hash="h-active",
                    content="张三",
                    status=CrawlPageChunkStatus.PROCESSING.value,
                    worker_id="active-worker",
                    lease_expires_at=active_until,
                )
            )
            await session.commit()

        processed = await run_crawler_v2_scheduler_once(self.session_factory, worker_id="scheduler")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            chunk = await session.scalar(select(CrawlPageChunk).where(CrawlPageChunk.job_id == job_id))
        assert job is not None and chunk is not None
        self.assertEqual(job.status, CrawlJobStatus.RUNNING.value)
        self.assertEqual(chunk.status, CrawlPageChunkStatus.PROCESSING.value)

    async def test_scheduler_does_not_finalize_job_with_active_processing_enrichment(self) -> None:
        job_id = await self._create_job()
        active_until = datetime.now(UTC) + timedelta(minutes=5)
        async with self.session_factory() as session:
            candidate = CrawlCandidate(job_id=job_id, name="张三")
            session.add(candidate)
            await session.flush()
            session.add(
                CrawlCandidateEnrichmentTask(
                    job_id=job_id,
                    candidate_id=candidate.id,
                    status=CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
                    worker_id="active-worker",
                    lease_expires_at=active_until,
                )
            )
            await session.commit()

        processed = await run_crawler_v2_scheduler_once(self.session_factory, worker_id="scheduler")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            task = await session.scalar(select(CrawlCandidateEnrichmentTask).where(CrawlCandidateEnrichmentTask.job_id == job_id))
        assert job is not None and task is not None
        self.assertEqual(job.status, CrawlJobStatus.RUNNING.value)
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.PROCESSING.value)

    async def test_scheduler_does_not_finalize_job_with_expired_reclaimable_page(self) -> None:
        job_id = await self._create_job()
        other_job_id = await self._create_job()
        expired = datetime.now(UTC) - timedelta(seconds=1)
        active_until = datetime.now(UTC) + timedelta(seconds=60)
        async with self.session_factory() as session:
            session.add(
                CrawlPageTask(
                    job_id=job_id,
                    normalized_url="https://example.edu/expired",
                    original_url="https://example.edu/expired",
                    status=CrawlPageTaskStatus.PROCESSING.value,
                    worker_id="old",
                    lease_expires_at=expired,
                )
            )
            session.add(
                CrawlPageTask(
                    job_id=other_job_id,
                    normalized_url="https://example.edu/active",
                    original_url="https://example.edu/active",
                    status=CrawlPageTaskStatus.PROCESSING.value,
                    worker_id="busy",
                    lease_expires_at=active_until,
                )
            )
            await session.commit()

        processed = await run_crawler_v2_scheduler_once(
            self.session_factory,
            worker_id="w1",
            config=CrawlerV2WorkerConfig(page_concurrency=1),
        )

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            task = await session.scalar(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id))
        assert job is not None and task is not None
        self.assertEqual(job.status, CrawlJobStatus.RUNNING.value)
        self.assertEqual(task.status, CrawlPageTaskStatus.PROCESSING.value)

    async def test_terminal_jobs_are_not_active_for_workers(self) -> None:
        statuses = [
            CrawlJobStatus.FAILED.value,
            CrawlJobStatus.NEEDS_REVIEW.value,
            CrawlJobStatus.PARTIALLY_COMPLETED.value,
        ]
        for status in statuses:
            job_id = await self._create_job(status=status)
            async with self.session_factory() as session:
                self.assertFalse(await ensure_job_active(session, job_id), msg=status)
    async def test_scheduler_finishes_current_run_when_job_becomes_reviewable(self) -> None:
        job_id = await self._create_job()
        async with self.session_factory() as session:
            run = CrawlJobRun(job_id=job_id, attempt_number=1, status=CrawlJobStatus.RUNNING.value, active_started_at=datetime.now(UTC))
            session.add(run)
            session.add(CrawlCandidate(job_id=job_id, name="张三"))
            await session.flush()
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            job.current_run_id = run.id
            await session.commit()
            run_id = run.id

        processed = await run_crawler_v2_scheduler_once(self.session_factory, worker_id="w1")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            run = await session.get(CrawlJobRun, run_id)
        assert job is not None and run is not None
        self.assertEqual(job.status, CrawlJobStatus.NEEDS_REVIEW.value)
        self.assertEqual(run.status, CrawlJobStatus.NEEDS_REVIEW.value)
        self.assertIsNotNone(run.finished_at)


    async def test_no_candidates_with_terminal_page_failure_marks_failed(self) -> None:
        job_id = await self._create_job()
        async with self.session_factory() as session:
            session.add(CrawlPageTask(job_id=job_id, normalized_url="https://example.edu/a", original_url="https://example.edu/a", status=CrawlPageTaskStatus.FAILED_TERMINAL.value))
            await session.commit()

        processed = await run_crawler_v2_scheduler_once(self.session_factory, worker_id="w1")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
        assert job is not None
        self.assertEqual(job.status, CrawlJobStatus.FAILED.value)

    async def test_terminal_failures_with_candidates_mark_needs_review_when_no_retryable_work(self) -> None:
        job_id = await self._create_job()
        async with self.session_factory() as session:
            session.add(CrawlCandidate(job_id=job_id, name="张三"))
            session.add(CrawlPageTask(job_id=job_id, normalized_url="https://example.edu/a", original_url="https://example.edu/a", status=CrawlPageTaskStatus.FAILED_TERMINAL.value))
            await session.commit()

        processed = await run_crawler_v2_scheduler_once(self.session_factory, worker_id="w1")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            self.assertEqual(job.status, CrawlJobStatus.NEEDS_REVIEW.value)

    async def test_profile_job_with_all_terminal_pages_and_no_candidates_fails(self) -> None:
        job_id = await self._create_job(entry_type="profile")
        async with self.session_factory() as session:
            session.add_all([
                CrawlPageTask(
                    job_id=job_id,
                    normalized_url="https://example.edu/teacher/a",
                    original_url="https://example.edu/teacher/a",
                    status=CrawlPageTaskStatus.FAILED_TERMINAL.value,
                ),
                CrawlPageTask(
                    job_id=job_id,
                    normalized_url="https://example.edu/teacher/b",
                    original_url="https://example.edu/teacher/b",
                    status=CrawlPageTaskStatus.FAILED_TERMINAL.value,
                ),
            ])
            await session.commit()

        processed = await run_crawler_v2_scheduler_once(self.session_factory, worker_id="w1")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
        assert job is not None
        self.assertEqual(job.status, CrawlJobStatus.FAILED.value)
        self.assertEqual(job.error_message, "抓取未发现候选导师")

    async def test_profile_job_with_candidate_enters_needs_review_after_terminal_pages(self) -> None:
        job_id = await self._create_job(entry_type="profile")
        async with self.session_factory() as session:
            session.add(CrawlCandidate(job_id=job_id, name="张三", profile_url="https://example.edu/teacher/a"))
            session.add_all([
                CrawlPageTask(
                    job_id=job_id,
                    normalized_url="https://example.edu/teacher/a",
                    original_url="https://example.edu/teacher/a",
                    status=CrawlPageTaskStatus.SUCCEEDED.value,
                ),
                CrawlPageTask(
                    job_id=job_id,
                    normalized_url="https://example.edu/teacher/b",
                    original_url="https://example.edu/teacher/b",
                    status=CrawlPageTaskStatus.FAILED_TERMINAL.value,
                ),
            ])
            await session.commit()

        processed = await run_crawler_v2_scheduler_once(self.session_factory, worker_id="w1")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
        assert job is not None
        self.assertEqual(job.status, CrawlJobStatus.NEEDS_REVIEW.value)
        self.assertIsNone(job.error_message)
    async def test_retryable_failures_are_claimed_before_job_completion(self) -> None:
        job_id = await self._create_job()
        async with self.session_factory() as session:
            session.add(CrawlPageTask(job_id=job_id, normalized_url="https://example.edu/a", original_url="https://example.edu/a", status=CrawlPageTaskStatus.FAILED_RETRYABLE.value))
            await session.commit()

        claimed = await claim_next_v2_work(self.session_factory, worker_id="w1", config=CrawlerV2WorkerConfig())

        self.assertEqual(claimed.kind, CrawlerV2WorkKind.PAGE)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            self.assertEqual(job.status, CrawlJobStatus.RUNNING.value)

    async def test_retryable_page_in_backoff_is_not_claimed_until_delay_expires(self) -> None:
        job_id = await self._create_job()
        delayed_until = datetime.now(UTC) + timedelta(seconds=30)
        async with self.session_factory() as session:
            session.add(CrawlPageTask(job_id=job_id, normalized_url="https://example.edu/a", original_url="https://example.edu/a", status=CrawlPageTaskStatus.FAILED_RETRYABLE.value, lease_expires_at=delayed_until))
            await session.commit()

        claimed = await claim_next_v2_work(self.session_factory, worker_id="w1", config=CrawlerV2WorkerConfig())

        self.assertEqual(claimed.kind, CrawlerV2WorkKind.IDLE)

    async def test_scheduler_does_not_finalize_job_with_retryable_page_in_backoff(self) -> None:
        job_id = await self._create_job()
        delayed_until = datetime.now(UTC) + timedelta(seconds=30)
        async with self.session_factory() as session:
            session.add(CrawlPageTask(job_id=job_id, normalized_url="https://example.edu/a", original_url="https://example.edu/a", status=CrawlPageTaskStatus.FAILED_RETRYABLE.value, lease_expires_at=delayed_until))
            await session.commit()

        processed = await run_crawler_v2_scheduler_once(self.session_factory, worker_id="scheduler")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
        assert job is not None
        self.assertEqual(job.status, CrawlJobStatus.RUNNING.value)
    async def test_claim_skips_row_when_conditional_update_loses_race(self) -> None:
        job_id = await self._create_job()
        async with self.session_factory() as session:
            session.add(CrawlPageTask(job_id=job_id, normalized_url="https://example.edu/a", original_url="https://example.edu/a"))
            await session.commit()

        class LostRaceResult:
            rowcount = 0

        with patch("app.services.crawler_v2_scheduler._conditional_claim_page_task", return_value=LostRaceResult()):
            claimed = await claim_next_v2_work(self.session_factory, worker_id="w1", config=CrawlerV2WorkerConfig())

        self.assertEqual(claimed.kind, CrawlerV2WorkKind.IDLE)
        async with self.session_factory() as session:
            task = await session.scalar(select(CrawlPageTask))
        assert task is not None
        self.assertEqual(task.status, CrawlPageTaskStatus.PENDING.value)
        self.assertIsNone(task.worker_id)

    async def _create_job(self, *, status: str = CrawlJobStatus.RUNNING.value, entry_type: str = "list") -> int:
        async with self.session_factory() as session:
            job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://example.edu", status=status, runtime_version="v2", entry_type=entry_type)
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job.id


if __name__ == "__main__":
    unittest.main()
