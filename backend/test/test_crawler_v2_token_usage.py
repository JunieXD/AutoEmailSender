from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, CrawlJob, CrawlJobRun, CrawlJobStatus, CrawlPageTask, CrawlPageTaskStatus, CrawlWorkerKind, CrawlWorkerTokenUsage
from app.modules.crawler.v2.lease import CrawlerV2ClaimFence
from app.modules.crawler.v2.models import CrawlerV2WorkKind
from app.modules.crawler.v2.token_usage import record_crawler_v2_token_usage


class CrawlerV2TokenUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_usage_by_worker_kind_and_item(self) -> None:
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(db_path).as_posix()}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://example.edu", status=CrawlJobStatus.RUNNING.value)
                session.add(job)
                await session.commit()
                await session.refresh(job)

            await record_crawler_v2_token_usage(session_factory, job_id=job.id, worker_kind=CrawlWorkerKind.CHUNK, work_item_id=123, model_name="deepseek", input_tokens=10, output_tokens=5, cached_tokens=8, raw_usage={"x": 1})

            async with session_factory() as session:
                usage = await session.scalar(select(CrawlWorkerTokenUsage))
            assert usage is not None
            self.assertEqual(usage.worker_kind, "chunk")
            self.assertEqual(usage.work_item_id, "123")
            self.assertEqual(usage.cached_tokens, 8)
        finally:
            await engine.dispose()
            try:
                os.unlink(db_path)
            except FileNotFoundError:
                pass



    async def test_record_usage_also_updates_current_run_for_frontend_summary(self) -> None:
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(db_path).as_posix()}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://example.edu", status=CrawlJobStatus.RUNNING.value)
                session.add(job)
                await session.flush()
                run = CrawlJobRun(job_id=job.id, attempt_number=1, status=CrawlJobStatus.RUNNING.value, input_tokens=3, output_tokens=2, total_tokens=5, cached_tokens=1)
                session.add(run)
                await session.flush()
                job.current_run_id = run.id
                await session.commit()
                job_id = job.id
                run_id = run.id

            await record_crawler_v2_token_usage(session_factory, job_id=job_id, worker_kind=CrawlWorkerKind.ENRICHMENT, work_item_id="e1", model_name="deepseek", input_tokens=10, output_tokens=5, cached_tokens=8, raw_usage={"x": 1})

            async with session_factory() as session:
                run = await session.get(CrawlJobRun, run_id)
                detail = await session.scalar(select(CrawlWorkerTokenUsage))
            assert run is not None and detail is not None
            self.assertEqual(run.input_tokens, 13)
            self.assertEqual(run.output_tokens, 7)
            self.assertEqual(run.total_tokens, 20)
            self.assertEqual(run.cached_tokens, 9)
            self.assertEqual(detail.input_tokens, 10)
            self.assertEqual(detail.output_tokens, 5)
            self.assertEqual(detail.cached_tokens, 8)
        finally:
            await engine.dispose()
            try:
                os.unlink(db_path)
            except FileNotFoundError:
                pass

    async def test_record_usage_creates_current_run_when_missing(self) -> None:
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(db_path).as_posix()}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://example.edu", status=CrawlJobStatus.RUNNING.value)
                session.add(job)
                await session.commit()
                await session.refresh(job)
                job_id = job.id

            await record_crawler_v2_token_usage(session_factory, job_id=job_id, worker_kind=CrawlWorkerKind.CHUNK, work_item_id="c1", input_tokens=4, output_tokens=6, cached_tokens=2)

            async with session_factory() as session:
                job = await session.get(CrawlJob, job_id)
                assert job is not None and job.current_run_id is not None
                run = await session.get(CrawlJobRun, job.current_run_id)
            assert run is not None
            self.assertEqual(run.input_tokens, 4)
            self.assertEqual(run.output_tokens, 6)
            self.assertEqual(run.total_tokens, 10)
            self.assertEqual(run.cached_tokens, 2)
        finally:
            await engine.dispose()
            try:
                os.unlink(db_path)
            except FileNotFoundError:
                pass

    async def test_same_claim_usage_is_recorded_once_and_attached_to_run(self) -> None:
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(db_path).as_posix()}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                job = CrawlJob(
                    university="示例大学",
                    school="计算机学院",
                    start_url="https://example.edu",
                    status=CrawlJobStatus.RUNNING.value,
                )
                session.add(job)
                await session.flush()
                task = CrawlPageTask(
                    job_id=job.id,
                    normalized_url="https://example.edu/a",
                    original_url="https://example.edu/a",
                    status=CrawlPageTaskStatus.PROCESSING.value,
                    worker_id="claim-1",
                    lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                )
                session.add(task)
                await session.commit()
                job_id = job.id
                task_id = task.id
            claim = CrawlerV2ClaimFence(
                kind=CrawlerV2WorkKind.PAGE,
                work_item_id=task_id,
                worker_id="claim-1",
            )

            first = await record_crawler_v2_token_usage(
                session_factory,
                job_id=job_id,
                worker_kind=CrawlWorkerKind.PAGE,
                work_item_id=task_id,
                input_tokens=10,
                output_tokens=5,
                claim=claim,
            )
            second = await record_crawler_v2_token_usage(
                session_factory,
                job_id=job_id,
                worker_kind=CrawlWorkerKind.PAGE,
                work_item_id=task_id,
                input_tokens=10,
                output_tokens=5,
                claim=claim,
            )

            self.assertTrue(first)
            self.assertFalse(second)
            async with session_factory() as session:
                rows = list(await session.scalars(select(CrawlWorkerTokenUsage)))
                job = await session.get(CrawlJob, job_id)
                assert job is not None and job.current_run_id is not None
                run = await session.get(CrawlJobRun, job.current_run_id)
            assert run is not None
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].run_id, run.id)
            self.assertEqual(rows[0].claim_id, "claim-1")
            self.assertEqual(run.total_tokens, 15)
        finally:
            await engine.dispose()
            try:
                os.unlink(db_path)
            except FileNotFoundError:
                pass

if __name__ == "__main__":
    unittest.main()
