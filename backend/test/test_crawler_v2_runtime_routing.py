from __future__ import annotations

import asyncio
from functools import partial
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.crawler.api import create_crawl_job
from app.models import Base, CrawlJob, CrawlJobStatus, CrawlPageChunk, CrawlPageChunkStatus, CrawlPageTask, LLMProfile
from app.modules.crawler.schemas import CrawlJobCreatePayload
from app.modules.crawler.v2.models import CrawlerV2ClaimedWork, CrawlerV2WorkKind
from app.services.runtime_manager import RuntimeManager


class CrawlerV2RuntimeRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_job_seeds_page_tasks(self) -> None:
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(db_path).as_posix()}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                job = await create_crawl_job(
                    CrawlJobCreatePayload(
                        university="示例大学",
                        school="计算机学院",
                        start_url="https://example.edu/faculty",
                        start_urls=["https://example.edu/faculty", "https://example.edu/page2"],
                    ),
                    session,
                )
                tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job.id).order_by(CrawlPageTask.id)))
            self.assertEqual([task.normalized_url for task in tasks], ["https://example.edu/faculty", "https://example.edu/page2"])
        finally:
            await engine.dispose()
            try:
                os.unlink(db_path)
            except FileNotFoundError:
                pass


    async def test_v2_runtime_dispatches_claimed_chunk_worker(self) -> None:
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
                session.add(CrawlPageChunk(job_id=job.id, page_id=None, source_url="https://example.edu", page_fingerprint="p", chunk_id="c1", chunk_index=0, chunk_hash="h", content="张三", status=CrawlPageChunkStatus.PENDING.value))
                await session.commit()

            from app.modules.crawler.v2.scheduler import run_crawler_v2_once

            processed = await run_crawler_v2_once(session_factory, worker_id="w1")

            self.assertEqual(processed, 1)
        finally:
            await engine.dispose()
            try:
                os.unlink(db_path)
            except FileNotFoundError:
                pass

    async def test_v2_runtime_uses_unique_owner_for_each_claim(self) -> None:
        session_factory = Mock()
        claimed = CrawlerV2ClaimedWork(
            kind=CrawlerV2WorkKind.PAGE,
            work_item_id=42,
            job_id=7,
        )
        with patch(
            "app.modules.crawler.v2.scheduler.uuid.uuid4",
        ) as mocked_uuid, patch(
            "app.modules.crawler.v2.scheduler.claim_next_v2_work",
            new=AsyncMock(return_value=claimed),
        ) as mocked_claim, patch(
            "app.modules.crawler.v2.page_worker.run_crawler_v2_page_worker_once",
            new=AsyncMock(return_value=1),
        ) as mocked_page_worker:
            mocked_uuid.return_value.hex = "claim-token"
            from app.modules.crawler.v2.scheduler import run_crawler_v2_once

            processed = await run_crawler_v2_once(session_factory, worker_id="crawler-worker-1")

        self.assertEqual(processed, 1)
        expected_owner = "crawler-worker-1:claim-token"
        mocked_claim.assert_awaited_once_with(
            session_factory,
            worker_id=expected_owner,
            config=None,
        )
        mocked_page_worker.assert_awaited_once_with(
            session_factory,
            task_id=42,
            worker_id=expected_owner,
        )
    async def test_runtime_manager_uses_v2_worker_entry(self) -> None:
        session = object()
        session_context = MagicMock()
        session_context.__aenter__ = AsyncMock(return_value=session)
        session_context.__aexit__ = AsyncMock(return_value=None)
        session_factory = Mock(return_value=session_context)
        manager = RuntimeManager(session_factory)

        async def idle_loop() -> None:
            await asyncio.Event().wait()

        def build_idle_loop(*args: object, **kwargs: object):
            _ = kwargs
            worker = args[2]
            return idle_loop()

        async def fake_load_worker_runtime_settings(session_arg: object) -> SimpleNamespace:
            _ = session_arg
            return SimpleNamespace(crawler_worker_count=1, match_analysis_job_worker_count=1, match_analysis_job_interval_seconds=10)

        with patch("app.services.runtime_manager.get_settings") as mocked_get_settings:
            mocked_get_settings.return_value = type("SettingsStub", (), {"dispatcher_interval_seconds": 30, "imap_poll_interval_seconds": 60, "crawler_worker_count": 1, "match_analysis_job_worker_count": 1, "match_analysis_job_interval_seconds": 10})()
            with patch("app.services.runtime_manager._load_worker_runtime_settings", new=fake_load_worker_runtime_settings), patch.object(manager, "_loop", new=Mock(side_effect=build_idle_loop)) as mocked_loop:
                await manager.start()
        worker_calls = {call.args[0]: call.args for call in mocked_loop.call_args_list}
        crawler_worker = worker_calls["crawler-worker-1"][2]
        self.assertIsInstance(crawler_worker, partial)
        self.assertEqual(crawler_worker.func.__name__, "run_crawler_v2_once")
        self.assertEqual(crawler_worker.keywords["worker_id"], "crawler-worker-1")
        await manager.stop()


if __name__ == "__main__":
    unittest.main()
