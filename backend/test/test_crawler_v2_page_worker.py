from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, CrawlJob, CrawlJobStatus, CrawlPage, CrawlPageChunk, CrawlPageTask, CrawlPageTaskStatus
from app.services.crawler_tools import PageSnapshot
from app.services.crawler_v2_page_worker import run_crawler_v2_page_worker_once


class CrawlerV2PageWorkerTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_successful_page_creates_page_chunks_and_same_domain_tasks(self) -> None:
        job_id, task_id = await self._seed_page_task()
        snapshot = PageSnapshot(
            url="https://example.edu/faculty",
            title="师资队伍",
            text="张三 教授\n李四 副教授",
            html="<a href='/profile/zhang.html'>张三</a>",
            links=["https://example.edu/profile/zhang.html", "https://other.edu/nope"],
            fetch_method="http",
            status="succeeded",
        )

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(return_value=snapshot)):
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
            assert task is not None
            self.assertEqual(task.status, CrawlPageTaskStatus.SUCCEEDED.value)
            self.assertEqual(task.fetch_mode, "direct")
            pages = list(await session.scalars(select(CrawlPage).where(CrawlPage.job_id == job_id)))
            chunks = list(await session.scalars(select(CrawlPageChunk).where(CrawlPageChunk.job_id == job_id)))
            tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id).order_by(CrawlPageTask.id)))
        self.assertEqual(len(pages), 1)
        self.assertGreaterEqual(len(chunks), 1)
        self.assertEqual([task.normalized_url for task in tasks], ["https://example.edu/faculty", "https://example.edu/profile/zhang.html"])

    async def test_direct_failure_uses_browser_fallback_without_terminal_failure(self) -> None:
        _, task_id = await self._seed_page_task()
        direct = PageSnapshot(url="https://example.edu/faculty", text="", html="", links=[], fetch_method="http", status="failed", error_message="403")
        browser = PageSnapshot(url="https://example.edu/faculty", text="张三", html="<p>张三</p>", links=[], fetch_method="browser", status="succeeded")

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(return_value=direct)), patch("app.services.crawler_v2_page_worker.fetch_page_browser", new=AsyncMock(return_value=browser)):
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
            assert task is not None
            self.assertEqual(task.status, CrawlPageTaskStatus.SUCCEEDED.value)
            self.assertEqual(task.fetch_mode, "browser")
            self.assertEqual(task.direct_status, "failed")
            self.assertIsNotNone(task.fallback_reason)

    async def _seed_page_task(self) -> tuple[int, int]:
        async with self.session_factory() as session:
            job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://example.edu/faculty", status=CrawlJobStatus.RUNNING.value, runtime_version="v2")
            session.add(job)
            await session.flush()
            task = CrawlPageTask(job_id=job.id, normalized_url="https://example.edu/faculty", original_url="https://example.edu/faculty", status=CrawlPageTaskStatus.PROCESSING.value, worker_id="w1")
            session.add(task)
            await session.commit()
            return job.id, task.id


if __name__ == "__main__":
    unittest.main()