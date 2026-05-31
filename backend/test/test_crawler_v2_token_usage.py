from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, CrawlJob, CrawlJobRun, CrawlJobStatus, CrawlWorkerKind, CrawlWorkerTokenUsage
from app.services.crawler_v2_token_usage import record_crawler_v2_token_usage


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
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://example.edu", status=CrawlJobStatus.RUNNING.value, runtime_version="v2")
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
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://example.edu", status=CrawlJobStatus.RUNNING.value, runtime_version="v2")
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
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://example.edu", status=CrawlJobStatus.RUNNING.value, runtime_version="v2")
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

if __name__ == "__main__":
    unittest.main()