from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    AppSetting,
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
    CrawlPageFetchState,
)
from app.modules.crawler.v2.models import CrawlerV2WorkKind, CrawlerV2WorkerConfig
from test.schema_database import create_schema_sqlite_database
from app.modules.crawler.v2.scheduler import (
    ZERO_CANDIDATE_BROWSER_RETRY_REASON,
    _run_claimed_work_with_heartbeat,
    claim_next_v2_work,
    ensure_job_active,
    run_crawler_v2_scheduler_once,
)
from app.modules.crawler.v2.lease import CrawlerV2ClaimFence


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

    async def test_default_job_concurrency_keeps_later_job_queued(self) -> None:
        first_job_id = await self._create_job(status=CrawlJobStatus.QUEUED.value)
        second_job_id = await self._create_job(status=CrawlJobStatus.QUEUED.value)
        async with self.session_factory() as session:
            session.add_all(
                [
                    CrawlPageTask(
                        job_id=first_job_id,
                        normalized_url="https://first.example.edu",
                        original_url="https://first.example.edu",
                    ),
                    CrawlPageTask(
                        job_id=second_job_id,
                        normalized_url="https://second.example.edu",
                        original_url="https://second.example.edu",
                    ),
                ]
            )
            await session.commit()

        first = await claim_next_v2_work(self.session_factory, worker_id="w1")
        second = await claim_next_v2_work(self.session_factory, worker_id="w2")

        self.assertEqual(first.job_id, first_job_id)
        self.assertEqual(second.kind, CrawlerV2WorkKind.IDLE)
        async with self.session_factory() as session:
            first_job = await session.get(CrawlJob, first_job_id)
            second_job = await session.get(CrawlJob, second_job_id)
        assert first_job is not None and second_job is not None
        self.assertEqual(first_job.status, CrawlJobStatus.RUNNING.value)
        self.assertEqual(second_job.status, CrawlJobStatus.QUEUED.value)

    async def test_job_concurrency_two_admits_two_different_jobs(self) -> None:
        first_job_id = await self._create_job(status=CrawlJobStatus.QUEUED.value)
        second_job_id = await self._create_job(status=CrawlJobStatus.QUEUED.value)
        async with self.session_factory() as session:
            session.add(AppSetting(id=1, crawler_worker_count=2))
            session.add_all(
                [
                    CrawlPageTask(
                        job_id=first_job_id,
                        normalized_url="https://first.example.edu",
                        original_url="https://first.example.edu",
                    ),
                    CrawlPageTask(
                        job_id=second_job_id,
                        normalized_url="https://second.example.edu",
                        original_url="https://second.example.edu",
                    ),
                ]
            )
            await session.commit()

        first = await claim_next_v2_work(self.session_factory, worker_id="w1")
        second = await claim_next_v2_work(self.session_factory, worker_id="w2")

        self.assertEqual(first.job_id, first_job_id)
        self.assertEqual(second.job_id, second_job_id)

    async def test_single_job_can_use_multiple_internal_page_workers(self) -> None:
        job_id = await self._create_job(status=CrawlJobStatus.QUEUED.value)
        async with self.session_factory() as session:
            session.add_all(
                [
                    CrawlPageTask(
                        job_id=job_id,
                        normalized_url="https://example.edu/a",
                        original_url="https://example.edu/a",
                    ),
                    CrawlPageTask(
                        job_id=job_id,
                        normalized_url="https://example.edu/b",
                        original_url="https://example.edu/b",
                    ),
                ]
            )
            await session.commit()

        first = await claim_next_v2_work(self.session_factory, worker_id="w1")
        second = await claim_next_v2_work(self.session_factory, worker_id="w2")

        self.assertEqual(first.job_id, job_id)
        self.assertEqual(second.job_id, job_id)
        self.assertEqual(first.kind, CrawlerV2WorkKind.PAGE)
        self.assertEqual(second.kind, CrawlerV2WorkKind.PAGE)

    async def test_additional_claim_does_not_reset_run_active_start(self) -> None:
        job_id = await self._create_job(status=CrawlJobStatus.QUEUED.value)
        async with self.session_factory() as session:
            session.add_all(
                [
                    CrawlPageTask(
                        job_id=job_id,
                        normalized_url="https://example.edu/a",
                        original_url="https://example.edu/a",
                    ),
                    CrawlPageTask(
                        job_id=job_id,
                        normalized_url="https://example.edu/b",
                        original_url="https://example.edu/b",
                    ),
                ]
            )
            await session.commit()

        await claim_next_v2_work(self.session_factory, worker_id="w1")
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None and job.current_run_id is not None
            run = await session.get(CrawlJobRun, job.current_run_id)
        assert run is not None and run.active_started_at is not None
        first_active_start = run.active_started_at
        await asyncio.sleep(0.01)

        await claim_next_v2_work(self.session_factory, worker_id="w2")

        async with self.session_factory() as session:
            run = await session.get(CrawlJobRun, job.current_run_id)
        assert run is not None
        self.assertEqual(run.active_started_at, first_active_start)

    async def test_active_jobs_rotate_before_reusing_stage_priority(self) -> None:
        first_job_id = await self._create_job()
        second_job_id = await self._create_job()
        async with self.session_factory() as session:
            session.add(AppSetting(id=1, crawler_worker_count=2))
            first_job = await session.get(CrawlJob, first_job_id)
            second_job = await session.get(CrawlJob, second_job_id)
            assert first_job is not None and second_job is not None
            first_job.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
            second_job.updated_at = datetime(2026, 1, 2, tzinfo=UTC)
            session.add(
                CrawlPageChunk(
                    job_id=first_job_id,
                    page_id=None,
                    source_url="https://first.example.edu",
                    page_fingerprint="first",
                    chunk_id="first-chunk",
                    chunk_index=0,
                    chunk_hash="first-hash",
                    content="张三",
                )
            )
            session.add(
                CrawlPageTask(
                    job_id=second_job_id,
                    normalized_url="https://second.example.edu",
                    original_url="https://second.example.edu",
                )
            )
            await session.commit()

        first = await claim_next_v2_work(self.session_factory, worker_id="w1")
        second = await claim_next_v2_work(self.session_factory, worker_id="w2")

        self.assertEqual(first.job_id, first_job_id)
        self.assertEqual(first.kind, CrawlerV2WorkKind.CHUNK)
        self.assertEqual(second.job_id, second_job_id)
        self.assertEqual(second.kind, CrawlerV2WorkKind.PAGE)

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

    async def test_processing_page_without_lease_can_be_reclaimed(self) -> None:
        job_id = await self._create_job()
        async with self.session_factory() as session:
            session.add(
                CrawlPageTask(
                    job_id=job_id,
                    normalized_url="https://example.edu/a",
                    original_url="https://example.edu/a",
                    status=CrawlPageTaskStatus.PROCESSING.value,
                    worker_id="dead-process",
                    lease_expires_at=None,
                )
            )
            await session.commit()

        claimed = await claim_next_v2_work(
            self.session_factory,
            worker_id="new",
            config=CrawlerV2WorkerConfig(),
        )

        self.assertEqual(claimed.kind, CrawlerV2WorkKind.PAGE)
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, claimed.work_item_id)
        assert task is not None
        self.assertEqual(task.worker_id, "new")
        self.assertEqual(task.attempt_count, 1)
        self.assertEqual(task.failure_count, 0)

    async def test_runtime_concurrency_reduction_demotes_overflow_job(self) -> None:
        first_job_id = await self._create_job()
        second_job_id = await self._create_job()
        async with self.session_factory() as session:
            session.add(AppSetting(id=1, crawler_worker_count=1))
            first_job = await session.get(CrawlJob, first_job_id)
            second_job = await session.get(CrawlJob, second_job_id)
            assert first_job is not None and second_job is not None
            first_job.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
            second_job.updated_at = datetime(2026, 1, 2, tzinfo=UTC)
            session.add_all(
                [
                    CrawlPageTask(
                        job_id=first_job_id,
                        normalized_url="https://first.example.edu",
                        original_url="https://first.example.edu",
                    ),
                    CrawlPageTask(
                        job_id=second_job_id,
                        normalized_url="https://second.example.edu",
                        original_url="https://second.example.edu",
                        status=CrawlPageTaskStatus.PROCESSING.value,
                        worker_id="overflow-worker",
                        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                    ),
                ]
            )
            await session.commit()

        claimed = await claim_next_v2_work(self.session_factory, worker_id="w1")

        self.assertEqual(claimed.job_id, first_job_id)
        async with self.session_factory() as session:
            second_job = await session.get(CrawlJob, second_job_id)
            assert second_job is not None
            second_run = await session.get(CrawlJobRun, second_job.current_run_id)
            overflow_task = await session.scalar(
                select(CrawlPageTask).where(CrawlPageTask.job_id == second_job_id)
            )
        assert second_run is not None and overflow_task is not None
        self.assertEqual(second_job.status, CrawlJobStatus.QUEUED.value)
        self.assertEqual(second_run.status, CrawlJobStatus.QUEUED.value)
        self.assertIsNone(second_run.active_started_at)
        assert overflow_task.lease_expires_at is not None
        self.assertLessEqual(overflow_task.lease_expires_at, datetime.now(UTC))

    async def test_heartbeat_renews_claim_during_long_work(self) -> None:
        job_id = await self._create_job()
        async with self.session_factory() as session:
            session.add(
                CrawlPageTask(
                    job_id=job_id,
                    normalized_url="https://example.edu/a",
                    original_url="https://example.edu/a",
                )
            )
            await session.commit()
        config = CrawlerV2WorkerConfig(lease_seconds=1)
        claimed = await claim_next_v2_work(
            self.session_factory,
            worker_id="heartbeat-worker",
            config=config,
        )
        assert claimed.work_item_id is not None

        async def long_work() -> int:
            await asyncio.sleep(1.2)
            return 7

        result = await _run_claimed_work_with_heartbeat(
            self.session_factory,
            claim=CrawlerV2ClaimFence(
                kind=claimed.kind,
                work_item_id=claimed.work_item_id,
                worker_id="heartbeat-worker",
            ),
            lease_seconds=1,
            work=long_work(),
        )

        self.assertEqual(result, 7)
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, claimed.work_item_id)
        assert task is not None and task.lease_expires_at is not None
        self.assertGreater(task.lease_expires_at, datetime.now(UTC))

    async def test_lost_claim_cancels_stale_work(self) -> None:
        job_id = await self._create_job()
        async with self.session_factory() as session:
            session.add(
                CrawlPageTask(
                    job_id=job_id,
                    normalized_url="https://example.edu/a",
                    original_url="https://example.edu/a",
                )
            )
            await session.commit()
        config = CrawlerV2WorkerConfig(lease_seconds=1)
        claimed = await claim_next_v2_work(
            self.session_factory,
            worker_id="old-worker",
            config=config,
        )
        assert claimed.work_item_id is not None
        canceled = asyncio.Event()

        async def stale_work() -> int:
            try:
                await asyncio.Event().wait()
            finally:
                canceled.set()
            return 1

        wrapper = asyncio.create_task(
            _run_claimed_work_with_heartbeat(
                self.session_factory,
                claim=CrawlerV2ClaimFence(
                    kind=claimed.kind,
                    work_item_id=claimed.work_item_id,
                    worker_id="old-worker",
                ),
                lease_seconds=1,
                work=stale_work(),
            )
        )
        await asyncio.sleep(0.05)
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, claimed.work_item_id)
            assert task is not None
            task.worker_id = "replacement-worker"
            task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
            await session.commit()

        self.assertEqual(await asyncio.wait_for(wrapper, timeout=2), 0)
        self.assertTrue(canceled.is_set())

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

    async def test_scheduler_requeues_direct_list_entry_once_when_no_candidates(self) -> None:
        job_id = await self._create_job()
        url = "https://example.edu"
        async with self.session_factory() as session:
            session.add_all(
                [
                    CrawlPageTask(
                        job_id=job_id,
                        normalized_url=url,
                        original_url=url,
                        parent_url=None,
                        discovery_reason="start",
                        expansion_mode="entry",
                        allow_expansion=False,
                        depth=0,
                        status=CrawlPageTaskStatus.SUCCEEDED.value,
                        fetch_mode="direct",
                        direct_status="succeeded",
                    ),
                    CrawlPageFetchState(
                        job_id=job_id,
                        normalized_url=url,
                        original_url=url,
                        status="processed",
                        fetch_mode="direct",
                        direct_status="succeeded",
                    ),
                ]
            )
            await session.commit()

        processed = await run_crawler_v2_scheduler_once(
            self.session_factory,
            worker_id="scheduler",
        )

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            task = await session.scalar(
                select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)
            )
        assert job is not None and task is not None
        self.assertEqual(job.status, CrawlJobStatus.RUNNING.value)
        self.assertEqual(task.status, CrawlPageTaskStatus.PENDING.value)
        self.assertEqual(task.fallback_reason, ZERO_CANDIDATE_BROWSER_RETRY_REASON)
        self.assertIsNone(task.allow_expansion)

    async def test_scheduler_does_not_requeue_entry_after_browser_was_attempted(self) -> None:
        job_id = await self._create_job()
        url = "https://example.edu"
        async with self.session_factory() as session:
            session.add_all(
                [
                    CrawlPageTask(
                        job_id=job_id,
                        normalized_url=url,
                        original_url=url,
                        parent_url=None,
                        depth=0,
                        status=CrawlPageTaskStatus.SUCCEEDED.value,
                        fetch_mode="direct",
                        direct_status="succeeded",
                        browser_status="failed",
                    ),
                    CrawlPageFetchState(
                        job_id=job_id,
                        normalized_url=url,
                        original_url=url,
                        status="processed",
                        fetch_mode="direct",
                        direct_status="succeeded",
                        browser_status="failed",
                    ),
                ]
            )
            await session.commit()

        await run_crawler_v2_scheduler_once(self.session_factory, worker_id="scheduler")

        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            task = await session.scalar(
                select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)
            )
        assert job is not None and task is not None
        self.assertEqual(job.status, CrawlJobStatus.FAILED.value)
        self.assertEqual(task.status, CrawlPageTaskStatus.SUCCEEDED.value)

    async def test_scheduler_does_not_requeue_profile_entry_for_zero_candidates(self) -> None:
        job_id = await self._create_job(entry_type="profile")
        async with self.session_factory() as session:
            session.add(
                CrawlPageTask(
                    job_id=job_id,
                    normalized_url="https://example.edu",
                    original_url="https://example.edu",
                    parent_url=None,
                    depth=0,
                    status=CrawlPageTaskStatus.SUCCEEDED.value,
                    fetch_mode="direct",
                    direct_status="succeeded",
                )
            )
            await session.commit()

        await run_crawler_v2_scheduler_once(self.session_factory, worker_id="scheduler")

        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            task = await session.scalar(
                select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)
            )
        assert job is not None and task is not None
        self.assertEqual(job.status, CrawlJobStatus.FAILED.value)
        self.assertEqual(task.status, CrawlPageTaskStatus.SUCCEEDED.value)

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

    async def test_scheduler_records_enrichment_completion_event_when_queue_finishes(self) -> None:
        job_id = await self._create_job()
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            job.active_candidate_enrichment_operation_id = "enrichment-operation-1"
            candidate_a = CrawlCandidate(job_id=job_id, name="张三", profile_url="https://example.edu/a")
            candidate_b = CrawlCandidate(job_id=job_id, name="李四", profile_url="https://example.edu/b")
            candidate_c = CrawlCandidate(job_id=job_id, name="王五", profile_url="https://example.edu/c")
            session.add_all([candidate_a, candidate_b, candidate_c])
            await session.flush()
            session.add_all([
                CrawlCandidateEnrichmentTask(
                    job_id=job_id,
                    candidate_id=candidate_a.id,
                    status=CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value,
                ),
                CrawlCandidateEnrichmentTask(
                    job_id=job_id,
                    candidate_id=candidate_b.id,
                    status=CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
                    last_error="详情页抓取失败",
                ),
                CrawlCandidateEnrichmentTask(
                    job_id=job_id,
                    candidate_id=candidate_c.id,
                    status=CrawlCandidateEnrichmentTaskStatus.SKIPPED.value,
                ),
            ])
            await session.commit()

        processed = await run_crawler_v2_scheduler_once(self.session_factory, worker_id="scheduler")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
        assert job is not None
        trace_messages = [
            item.get("message")
            for item in job.agent_trace or []
            if isinstance(item, dict)
        ]
        self.assertIn(
            "候选导师详情补全完成：成功 1 位，未变化 1 位，失败 1 位",
            trace_messages,
        )
        terminal_event = next(
            item
            for item in job.agent_trace or []
            if isinstance(item, dict)
            and item.get("message")
            == "候选导师详情补全完成：成功 1 位，未变化 1 位，失败 1 位"
        )
        self.assertEqual(terminal_event["raw"]["operation_id"], "enrichment-operation-1")
        self.assertEqual(terminal_event["raw"]["status"], "partially_completed")
        self.assertIsNone(job.active_candidate_enrichment_operation_id)

    async def test_scheduler_enrichment_completion_event_counts_current_run_only(self) -> None:
        job_id = await self._create_job()
        current_run_started_at = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            run = CrawlJobRun(
                job_id=job_id,
                attempt_number=1,
                status=CrawlJobStatus.RUNNING.value,
                active_started_at=current_run_started_at,
            )
            session.add(run)
            await session.flush()
            job.current_run_id = run.id
            old_candidate = CrawlCandidate(job_id=job_id, name="旧候选", profile_url="https://example.edu/old")
            current_candidate = CrawlCandidate(job_id=job_id, name="新候选", profile_url="https://example.edu/new")
            session.add_all([old_candidate, current_candidate])
            await session.flush()
            old_task = CrawlCandidateEnrichmentTask(
                job_id=job_id,
                candidate_id=old_candidate.id,
                status=CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value,
            )
            current_task = CrawlCandidateEnrichmentTask(
                job_id=job_id,
                candidate_id=current_candidate.id,
                status=CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
                last_error="详情页抓取失败",
            )
            session.add_all([old_task, current_task])
            await session.flush()
            old_task.updated_at = datetime(2026, 6, 29, 11, 30, tzinfo=UTC)
            current_task.updated_at = datetime(2026, 6, 29, 12, 1, tzinfo=UTC)
            await session.commit()

        processed = await run_crawler_v2_scheduler_once(self.session_factory, worker_id="scheduler")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
        assert job is not None
        trace_messages = [
            item.get("message")
            for item in job.agent_trace or []
            if isinstance(item, dict)
        ]
        self.assertIn(
            "候选导师详情补全完成：成功 0 位，未变化 0 位，失败 1 位",
            trace_messages,
        )

    async def test_scheduler_counts_merged_alias_tasks_as_one_candidate(self) -> None:
        job_id = await self._create_job()
        async with self.session_factory() as session:
            canonical = CrawlCandidate(
                job_id=job_id,
                name="张三",
                email="same@example.edu",
                profile_url="https://example.edu/a",
            )
            alias = CrawlCandidate(
                job_id=job_id,
                name="Zhang San",
                email="same@example.edu",
                profile_url="https://example.edu/b",
            )
            session.add_all([canonical, alias])
            await session.flush()
            session.add_all(
                [
                    CrawlCandidateEnrichmentTask(
                        job_id=job_id,
                        candidate_id=canonical.id,
                        status=CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value,
                    ),
                    CrawlCandidateEnrichmentTask(
                        job_id=job_id,
                        candidate_id=alias.id,
                        status=CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
                        last_error="旧别名补全失败",
                    ),
                ]
            )
            await session.commit()

        await run_crawler_v2_scheduler_once(
            self.session_factory,
            worker_id="scheduler",
        )

        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
        assert job is not None
        trace_messages = [
            item.get("message")
            for item in job.agent_trace or []
            if isinstance(item, dict)
        ]
        self.assertIn(
            "候选导师详情补全完成：成功 1 位，未变化 0 位，失败 0 位",
            trace_messages,
        )

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
            session.add(AppSetting(id=1, crawler_worker_count=2))
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

    async def test_no_candidates_with_terminal_chunk_failure_uses_terminal_error_message(self) -> None:
        job_id = await self._create_job()
        async with self.session_factory() as session:
            run = CrawlJobRun(
                job_id=job_id,
                attempt_number=1,
                status=CrawlJobStatus.RUNNING.value,
                active_started_at=datetime.now(UTC),
            )
            session.add(run)
            session.add(
                CrawlPageChunk(
                    job_id=job_id,
                    page_id=None,
                    source_url="https://example.edu/faculty",
                    page_fingerprint="p",
                    chunk_id="c1",
                    chunk_index=0,
                    chunk_hash="h1",
                    content="张三",
                    status=CrawlPageChunkStatus.FAILED_TERMINAL.value,
                    last_error="No module named 'crawler_dependency'",
                )
            )
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
        self.assertEqual(job.status, CrawlJobStatus.FAILED.value)
        self.assertEqual(job.error_message, "No module named 'crawler_dependency'")
        self.assertEqual(run.error_message, "No module named 'crawler_dependency'")

    async def test_terminal_connection_error_is_adapted_for_final_job_message(self) -> None:
        job_id = await self._create_job()
        async with self.session_factory() as session:
            session.add(
                CrawlPageChunk(
                    job_id=job_id,
                    page_id=None,
                    source_url="https://example.edu/faculty",
                    page_fingerprint="p",
                    chunk_id="c-connect",
                    chunk_index=0,
                    chunk_hash="h-connect",
                    content="张三",
                    status=CrawlPageChunkStatus.FAILED_TERMINAL.value,
                    last_error="模型请求失败: All connection attempts failed",
                )
            )
            await session.commit()

        processed = await run_crawler_v2_scheduler_once(self.session_factory, worker_id="w1")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
        assert job is not None
        self.assertEqual(job.status, CrawlJobStatus.FAILED.value)
        self.assertIn("模型服务连接失败", job.error_message or "")
        self.assertIn("系统代理", job.error_message or "")

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

        with patch("app.modules.crawler.v2.scheduler._conditional_claim_page_task", return_value=LostRaceResult()):
            claimed = await claim_next_v2_work(self.session_factory, worker_id="w1", config=CrawlerV2WorkerConfig())

        self.assertEqual(claimed.kind, CrawlerV2WorkKind.IDLE)
        async with self.session_factory() as session:
            task = await session.scalar(select(CrawlPageTask))
        assert task is not None
        self.assertEqual(task.status, CrawlPageTaskStatus.PENDING.value)
        self.assertIsNone(task.worker_id)

    async def _create_job(self, *, status: str = CrawlJobStatus.RUNNING.value, entry_type: str = "list") -> int:
        async with self.session_factory() as session:
            job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://example.edu", status=status, entry_type=entry_type)
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job.id


if __name__ == "__main__":
    unittest.main()
