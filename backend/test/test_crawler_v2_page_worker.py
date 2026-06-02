from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test.schema_database import create_schema_sqlite_database

from app.models import CrawlJob, CrawlJobStatus, CrawlPage, CrawlPageChunk, CrawlPageFetchState, CrawlPageFetchStatus, CrawlPageTask, CrawlPageTaskStatus
from app.services.crawler_tools import PageSnapshot
from app.services.crawler_v2_page_worker import fetch_page_browser, fetch_page_direct, run_crawler_v2_page_worker_once


class CrawlerV2PageWorkerTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_successful_page_creates_page_chunks_without_enqueuing_links(self) -> None:
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
        self.assertEqual([task.normalized_url for task in tasks], ["https://example.edu/faculty"])
        self.assertTrue(
            any("[张三](https://example.edu/profile/zhang.html)" in chunk.content for chunk in chunks),
            "chunk content should retain link context for Chunk Worker URL discovery",
        )

    async def test_page_worker_does_not_write_after_job_is_paused(self) -> None:
        job_id, task_id = await self._seed_page_task()
        snapshot = PageSnapshot(
            url="https://example.edu/faculty",
            title="师资队伍",
            text="张三 教授",
            html="<p>张三</p>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )

        async def pause_job_during_fetch(*_args, **_kwargs):
            async with self.session_factory() as session:
                job = await session.get(CrawlJob, job_id)
                task = await session.get(CrawlPageTask, task_id)
                assert job is not None and task is not None
                job.status = CrawlJobStatus.PAUSED.value
                task.status = CrawlPageTaskStatus.PENDING.value
                task.worker_id = None
                await session.commit()
            return snapshot

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(side_effect=pause_job_during_fetch)):
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
            pages = list(await session.scalars(select(CrawlPage).where(CrawlPage.job_id == job_id)))
        assert task is not None
        self.assertEqual(task.status, CrawlPageTaskStatus.PENDING.value)
        self.assertEqual(len(pages), 0)


    async def test_page_worker_writes_v2_debug_jsonl(self) -> None:
        job_id, task_id = await self._seed_page_task()
        snapshot = PageSnapshot(
            url="https://example.edu/faculty",
            status="succeeded",
            title="教师名录",
            text="张三 教授",
            html="<html>张三</html>",
            markdown="[张三](https://example.edu/zhang.html)",
            links=[],
            fetch_method="http",
        )

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(return_value=snapshot)), patch("app.services.crawler_v2_page_worker.append_crawler_v2_debug_event") as debug_mock:
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        events = [call.kwargs["event_name"] for call in debug_mock.call_args_list]
        self.assertIn("page_fetched", events)
        self.assertIn("page_chunked", events)
        page_call = next(call for call in debug_mock.call_args_list if call.kwargs["event_name"] == "page_fetched")
        self.assertEqual(page_call.args[0], job_id)
        self.assertEqual(page_call.kwargs["worker_kind"], "page")
        self.assertEqual(page_call.kwargs["work_item_id"], task_id)
        self.assertEqual(page_call.kwargs["payload"]["snapshot"]["status"], "succeeded")

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

    async def test_successful_page_reuses_fetch_tool_page_record(self) -> None:
        job_id, task_id = await self._seed_page_task()
        async with self.session_factory() as session:
            page = CrawlPage(
                job_id=job_id,
                url="https://example.edu/faculty",
                parent_url=None,
                fetch_method="http",
                status="succeeded",
                title="师资队伍",
                text_excerpt="张三 教授",
                error_message=None,
            )
            session.add(page)
            await session.commit()
            await session.refresh(page)
            page_id = page.id
        snapshot = PageSnapshot(
            page_id=page_id,
            url="https://example.edu/faculty",
            title="师资队伍",
            text="张三 教授",
            html="<p>张三</p>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(return_value=snapshot)):
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            pages = list(await session.scalars(select(CrawlPage).where(CrawlPage.job_id == job_id)))
            chunks = list(await session.scalars(select(CrawlPageChunk).where(CrawlPageChunk.job_id == job_id)))
        self.assertEqual([page.id for page in pages], [page_id])
        self.assertTrue(chunks)
        self.assertTrue(all(chunk.page_id == page_id for chunk in chunks))


    async def test_successful_page_does_not_reuse_page_record_from_other_job(self) -> None:
        job_id, task_id = await self._seed_page_task()
        async with self.session_factory() as session:
            other_job = CrawlJob(
                university="其他大学",
                school="计算机学院",
                start_url="https://other.example.edu/faculty",
                status="running",
                runtime_version="v2",
            )
            session.add(other_job)
            await session.flush()
            other_page = CrawlPage(
                job_id=other_job.id,
                url="https://example.edu/faculty",
                parent_url=None,
                fetch_method="http",
                status="succeeded",
                title="其他师资队伍",
                text_excerpt="李四 教授",
                error_message=None,
            )
            session.add(other_page)
            await session.commit()
            await session.refresh(other_page)
            other_page_id = other_page.id
        snapshot = PageSnapshot(
            page_id=other_page_id,
            url="https://example.edu/faculty",
            title="师资队伍",
            text="张三 教授",
            html="<p>张三</p>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(return_value=snapshot)):
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            pages = list(await session.scalars(select(CrawlPage).where(CrawlPage.job_id == job_id)))
            chunks = list(await session.scalars(select(CrawlPageChunk).where(CrawlPageChunk.job_id == job_id)))
        self.assertEqual(len(pages), 1)
        self.assertNotEqual(pages[0].id, other_page_id)
        self.assertTrue(chunks)
        self.assertTrue(all(chunk.page_id == pages[0].id for chunk in chunks))
    async def test_page_worker_skips_processed_url_without_fetching_again(self) -> None:
        job_id, task_id = await self._seed_page_task()
        async with self.session_factory() as session:
            session.add(
                CrawlPageFetchState(
                    job_id=job_id,
                    normalized_url="https://example.edu/faculty",
                    original_url="https://example.edu/faculty",
                    status=CrawlPageFetchStatus.PROCESSED.value,
                )
            )
            await session.commit()

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock()) as fetch_mock:
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        fetch_mock.assert_not_awaited()
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
            pages = list(await session.scalars(select(CrawlPage).where(CrawlPage.job_id == job_id)))
            chunks = list(await session.scalars(select(CrawlPageChunk).where(CrawlPageChunk.job_id == job_id)))
        assert task is not None
        self.assertEqual(task.status, CrawlPageTaskStatus.SKIPPED_DUPLICATE.value)
        self.assertEqual(len(pages), 0)
        self.assertEqual(len(chunks), 0)

    async def test_page_worker_does_not_write_after_lease_expires(self) -> None:
        job_id, task_id = await self._seed_page_task()
        expired = datetime.now(UTC) - timedelta(seconds=1)
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
            assert task is not None
            task.lease_expires_at = expired
            await session.commit()
        snapshot = PageSnapshot(
            url="https://example.edu/faculty",
            text="张三 教授",
            html="<p>张三</p>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(return_value=snapshot)):
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
            pages = list(await session.scalars(select(CrawlPage).where(CrawlPage.job_id == job_id)))
        assert task is not None
        self.assertEqual(task.status, CrawlPageTaskStatus.PROCESSING.value)
        self.assertEqual(len(pages), 0)
    async def test_page_worker_treats_naive_lease_timestamp_as_utc(self) -> None:
        job_id, task_id = await self._seed_page_task()
        naive_future_utc = (datetime.now(UTC) + timedelta(minutes=5)).replace(tzinfo=None)
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
            assert task is not None
            task.lease_expires_at = naive_future_utc
            await session.commit()
        snapshot = PageSnapshot(
            url="https://example.edu/faculty",
            text="张三 教授",
            html="<p>张三</p>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(return_value=snapshot)):
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
            pages = list(await session.scalars(select(CrawlPage).where(CrawlPage.job_id == job_id)))
        assert task is not None
        self.assertEqual(task.status, CrawlPageTaskStatus.SUCCEEDED.value)
        self.assertEqual(len(pages), 1)
    async def test_fetch_modes_use_distinct_underlying_paths(self) -> None:
        ctx = object()
        direct_snapshot = PageSnapshot(url="https://example.edu", text="direct", html="", links=[], fetch_method="http", status="succeeded")
        browser_snapshot = PageSnapshot(url="https://example.edu", text="browser", html="", links=[], fetch_method="browser", status="succeeded")

        with patch("app.services.crawler_v2_page_worker.crawl_page_with_http", new=AsyncMock(return_value=direct_snapshot)) as http_mock, patch("app.services.crawler_v2_page_worker.browser_investigate", new=AsyncMock(return_value=browser_snapshot)) as browser_mock:
            direct_result = await fetch_page_direct(ctx, "https://example.edu")
            browser_result = await fetch_page_browser(ctx, "https://example.edu")

        self.assertEqual(direct_result.fetch_method, "http")
        self.assertEqual(browser_result.fetch_method, "browser")
        http_mock.assert_awaited_once()
        browser_mock.assert_awaited_once()

    async def test_page_worker_ignores_snapshot_links_after_chunks_are_created(self) -> None:
        job_id, task_id = await self._seed_page_task()
        snapshot = PageSnapshot(
            url="https://example.edu/faculty",
            text="张三 教授",
            html="<a href='/race.html'>下一页</a>",
            links=["https://example.edu/race.html"],
            fetch_method="http",
            status="succeeded",
        )

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(return_value=snapshot)):
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
            tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)))
            chunks = list(await session.scalars(select(CrawlPageChunk).where(CrawlPageChunk.job_id == job_id)))
        assert task is not None
        self.assertEqual(task.status, CrawlPageTaskStatus.SUCCEEDED.value)
        self.assertGreaterEqual(len(chunks), 1)
        self.assertEqual([item.normalized_url for item in tasks], ["https://example.edu/faculty"])
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
