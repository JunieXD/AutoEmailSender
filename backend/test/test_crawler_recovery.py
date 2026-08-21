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
    CrawlJobKind,
    CrawlJobStatus,
    CrawlPageTask,
    CrawlPageTaskStatus,
)
from app.modules.crawler.jobs.recovery import (
    INTERRUPTED_JOB_ERROR,
    INTERRUPTED_JOB_PAUSED_MESSAGE,
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
        job_id = await self._create_running_job(job_kind="legacy_crawl")

        await recover_interrupted_crawl_jobs(self.session_factory)

        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            await session.refresh(job, ["current_run"])
            self.assertEqual(job.status, CrawlJobStatus.FAILED.value)
            self.assertEqual(job.error_message, INTERRUPTED_JOB_ERROR)
            self.assertEqual(job.current_run.status, CrawlJobStatus.FAILED.value)

    async def test_running_job_with_candidates_moves_to_review(self) -> None:
        job_id = await self._create_running_job(job_kind="legacy_crawl")
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

    async def test_running_faculty_crawl_is_paused_and_processing_work_released(
        self,
    ) -> None:
        job_id = await self._create_running_job()
        future_lease = datetime.now(UTC) + timedelta(minutes=5)
        claimed_at = datetime.now(UTC)
        async with self.session_factory() as session:
            task = CrawlPageTask(
                job_id=job_id,
                normalized_url="https://example.edu/faculty",
                original_url="https://example.edu/faculty",
                status=CrawlPageTaskStatus.PROCESSING.value,
                worker_id="interrupted-worker",
                claimed_at=claimed_at,
                lease_expires_at=future_lease,
            )
            session.add(task)
            await session.commit()
            task_id = task.id

        await recover_interrupted_crawl_jobs(self.session_factory)

        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            task = await session.get(CrawlPageTask, task_id)
            assert job is not None and task is not None
            await session.refresh(job, ["current_run"])
            self.assertEqual(job.status, CrawlJobStatus.PAUSED.value)
            self.assertEqual(job.current_run.status, CrawlJobStatus.PAUSED.value)
            self.assertEqual(task.status, CrawlPageTaskStatus.PENDING.value)
            self.assertIsNone(task.worker_id)
            self.assertIsNone(task.claimed_at)
            self.assertIsNone(task.lease_expires_at)
            self.assertEqual(task.last_error, "任务已暂停，释放处理中工作项")
            self.assertEqual(
                job.agent_trace[-1]["message"], INTERRUPTED_JOB_PAUSED_MESSAGE
            )

    async def test_running_enrichment_job_is_still_requeued(self) -> None:
        job_id = await self._create_running_job(
            job_kind=CrawlJobKind.PROFESSOR_ENRICHMENT.value,
        )

        await recover_interrupted_crawl_jobs(self.session_factory)

        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            await session.refresh(job, ["current_run"])
            self.assertEqual(job.status, CrawlJobStatus.QUEUED.value)
            self.assertEqual(job.current_run.status, CrawlJobStatus.QUEUED.value)

    async def test_queued_faculty_crawl_is_not_paused(self) -> None:
        async with self.session_factory() as session:
            job = CrawlJob(
                university="示例大学",
                school="计算机学院",
                start_url="https://example.edu/faculty",
                status=CrawlJobStatus.QUEUED.value,
            )
            session.add(job)
            await session.commit()
            job_id = job.id

        await recover_interrupted_crawl_jobs(self.session_factory)

        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            self.assertEqual(job.status, CrawlJobStatus.QUEUED.value)

    async def _create_running_job(
        self,
        *,
        job_kind: str = CrawlJobKind.FACULTY_CRAWL.value,
    ) -> int:
        async with self.session_factory() as session:
            job = CrawlJob(
                university="示例大学",
                school="计算机学院",
                start_url="https://example.edu/faculty",
                job_kind=job_kind,
                status=CrawlJobStatus.RUNNING.value,
            )
            session.add(job)
            await session.commit()
            return job.id


if __name__ == "__main__":
    unittest.main()
