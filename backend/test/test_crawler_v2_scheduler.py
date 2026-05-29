from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base,
    CrawlCandidate,
    CrawlCandidateEnrichmentTask,
    CrawlCandidateEnrichmentTaskStatus,
    CrawlJob,
    CrawlJobStatus,
    CrawlPageTask,
    CrawlPageTaskStatus,
    CrawlPageChunk,
    CrawlPageChunkStatus,
)
from app.services.crawler_v2_models import CrawlerV2WorkKind, CrawlerV2WorkerConfig
from app.services.crawler_v2_scheduler import claim_next_v2_work, run_crawler_v2_scheduler_once


class CrawlerV2SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.db_path).as_posix()}")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    async def test_claims_page_before_chunk_and_enrichment(self) -> None:
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

        self.assertEqual(claimed.kind, CrawlerV2WorkKind.PAGE)
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, claimed.work_item_id)
            assert task is not None
            self.assertEqual(task.status, CrawlPageTaskStatus.PROCESSING.value)
            self.assertEqual(task.worker_id, "w1")
            self.assertIsNotNone(task.lease_expires_at)

    async def test_does_not_claim_when_job_paused(self) -> None:
        job_id = await self._create_job(status=CrawlJobStatus.PAUSED.value)
        async with self.session_factory() as session:
            session.add(CrawlPageTask(job_id=job_id, normalized_url="https://example.edu/a", original_url="https://example.edu/a"))
            await session.commit()

        claimed = await claim_next_v2_work(self.session_factory, worker_id="w1", config=CrawlerV2WorkerConfig())

        self.assertEqual(claimed.kind, CrawlerV2WorkKind.IDLE)

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

    async def test_scheduler_marks_job_needs_review_when_no_work_remains(self) -> None:
        job_id = await self._create_job()

        processed = await run_crawler_v2_scheduler_once(self.session_factory, worker_id="w1")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            self.assertEqual(job.status, CrawlJobStatus.NEEDS_REVIEW.value)

    async def _create_job(self, *, status: str = CrawlJobStatus.RUNNING.value) -> int:
        async with self.session_factory() as session:
            job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://example.edu", status=status, runtime_version="v2")
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job.id


if __name__ == "__main__":
    unittest.main()