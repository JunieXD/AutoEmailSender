from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, CrawlJob, CrawlJobStatus, CrawlWorkerKind, CrawlWorkerTokenUsage
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


if __name__ == "__main__":
    unittest.main()