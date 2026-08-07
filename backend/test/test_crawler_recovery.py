from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import tempfile
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base,
    CrawlCandidate,
    CrawlJob,
    CrawlJobStatus,
    CrawlPageTask,
    CrawlPageTaskStatus,
)
from app.modules.crawler.jobs.recovery import (
    INTERRUPTED_JOB_ERROR,
    recover_interrupted_crawl_jobs,
)


class CrawlerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{Path(self.db_path).as_posix()}"
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    async def test_running_job_without_work_or_candidates_is_failed(self) -> None:
        job_id = await self._create_running_job()

        await recover_interrupted_crawl_jobs(self.session_factory)

        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            await session.refresh(job, ["current_run"])
            self.assertEqual(job.status, CrawlJobStatus.FAILED.value)
            self.assertEqual(job.error_message, INTERRUPTED_JOB_ERROR)
            self.assertEqual(job.current_run.status, CrawlJobStatus.FAILED.value)

    async def test_running_job_with_candidates_moves_to_review(self) -> None:
        job_id = await self._create_running_job()
        async with self.session_factory() as session:
            session.add(
                CrawlCandidate(
                    job_id=job_id,
                    name="张三",
                    email="zhang@example.edu",
                    profile_url="https://example.edu/zhang",
                )
            )
            await session.commit()

        await recover_interrupted_crawl_jobs(self.session_factory)

        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            self.assertEqual(job.status, CrawlJobStatus.NEEDS_REVIEW.value)
            self.assertIsNone(job.error_message)

    async def test_running_job_with_processing_work_is_requeued_and_lease_expires(self) -> None:
        job_id = await self._create_running_job()
        future_lease = datetime.now(UTC) + timedelta(minutes=5)
        async with self.session_factory() as session:
            task = CrawlPageTask(
                job_id=job_id,
                normalized_url="https://example.edu/faculty",
                original_url="https://example.edu/faculty",
                status=CrawlPageTaskStatus.PROCESSING.value,
                worker_id="interrupted-worker",
                lease_expires_at=future_lease,
            )
            session.add(task)
            await session.commit()
            task_id = task.id

        recovered_at = datetime.now(UTC)
        await recover_interrupted_crawl_jobs(self.session_factory)

        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            task = await session.get(CrawlPageTask, task_id)
            assert job is not None and task is not None
            await session.refresh(job, ["current_run"])
            self.assertEqual(job.status, CrawlJobStatus.QUEUED.value)
            self.assertEqual(job.current_run.status, CrawlJobStatus.QUEUED.value)
            self.assertLessEqual(task.lease_expires_at, datetime.now(UTC))
            self.assertGreaterEqual(task.lease_expires_at, recovered_at)

    async def _create_running_job(self) -> int:
        async with self.session_factory() as session:
            job = CrawlJob(
                university="示例大学",
                school="计算机学院",
                start_url="https://example.edu/faculty",
                status=CrawlJobStatus.RUNNING.value,
            )
            session.add(job)
            await session.commit()
            return job.id


if __name__ == "__main__":
    unittest.main()
