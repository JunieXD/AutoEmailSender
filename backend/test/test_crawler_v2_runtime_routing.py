from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.crawl_jobs import create_crawl_job
from app.models import Base, CrawlJob, CrawlJobStatus, CrawlPageChunk, CrawlPageChunkStatus, CrawlPageTask, LLMProfile
from app.modules.crawler.schemas import CrawlJobCreatePayload
from app.services.runtime_manager import RuntimeManager


class CrawlerV2RuntimeRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_job_defaults_to_v2_and_seeds_page_tasks(self) -> None:
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
            self.assertEqual(job.runtime_version, "v2")
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
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://example.edu", status=CrawlJobStatus.RUNNING.value, runtime_version="v2")
                session.add(job)
                await session.flush()
                session.add(CrawlPageChunk(job_id=job.id, page_id=None, source_url="https://example.edu", page_fingerprint="p", chunk_id="c1", chunk_index=0, chunk_hash="h", content="张三", status=CrawlPageChunkStatus.PENDING.value))
                await session.commit()

            with patch("app.services.crawl_job_runtime.run_faculty_crawler_agent", new=AsyncMock(return_value={"ok": True})):
                from app.services.crawler_v2_scheduler import run_crawler_v2_once

                processed = await run_crawler_v2_once(session_factory, worker_id="w1")

            self.assertEqual(processed, 1)
        finally:
            await engine.dispose()
            try:
                os.unlink(db_path)
            except FileNotFoundError:
                pass
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
            self.assertNotEqual(args[2].__name__, "run_queued_crawl_jobs_once")
            return idle_loop()

        async def fake_load_worker_runtime_settings(session_arg: object) -> SimpleNamespace:
            _ = session_arg
            return SimpleNamespace(crawler_worker_count=1, match_analysis_job_worker_count=1, match_analysis_job_interval_seconds=10)

        with patch("app.services.runtime_manager.get_settings") as mocked_get_settings:
            mocked_get_settings.return_value = type("SettingsStub", (), {"dispatcher_interval_seconds": 30, "imap_poll_interval_seconds": 60, "crawler_worker_count": 1, "match_analysis_job_worker_count": 1, "match_analysis_job_interval_seconds": 10})()
            with patch("app.services.runtime_manager._load_worker_runtime_settings", new=fake_load_worker_runtime_settings), patch.object(manager, "_loop", new=Mock(side_effect=build_idle_loop)) as mocked_loop:
                await manager.start()
        worker_calls = {call.args[0]: call.args for call in mocked_loop.call_args_list}
        self.assertEqual(worker_calls["crawler-worker-1"][2].__name__, "run_crawler_v2_once")
        await manager.stop()


if __name__ == "__main__":
    unittest.main()
