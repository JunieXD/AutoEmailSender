from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test.schema_database import create_schema_sqlite_database

from app.models import CrawlCandidate, CrawlJob, CrawlJobStatus, CrawlPage, CrawlPageChunk, CrawlPageFetchState, CrawlPageFetchStatus, CrawlPageTask, CrawlPageTaskStatus, CrawlWorkerKind, CrawlWorkerTokenUsage, LLMProfile
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

    async def test_profile_entry_extracts_candidate_without_creating_chunks(self) -> None:
        job_id, task_id = await self._seed_page_task(
            original_url="https://example.edu/teacher/zhang.html",
            entry_type="profile",
        )
        snapshot = PageSnapshot(
            url="https://example.edu/teacher/zhang.html",
            title="张三",
            text="张三 教授 邮箱 zhang@example.edu",
            html="<h1>张三</h1>",
            links=["https://example.edu/faculty"],
            fetch_method="http",
            status="succeeded",
        )
        extraction = SimpleNamespace(
            payload={
                "status": "candidate",
                "candidate": {"name": "张三", "email": "zhang@example.edu", "confidence": 0.9},
            },
            usage={"input_tokens": 10, "output_tokens": 4, "cached_tokens": 0},
            attempts=[],
            page_text_hash="hash",
            page_text_length=len(snapshot.text),
        )

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(return_value=snapshot)), \
            patch("app.services.crawler_v2_page_worker.ensure_thinking_adaptation", new=AsyncMock(return_value={"thinking": {"type": "disabled"}})), \
            patch("app.services.crawler_v2_page_worker.invoke_v2_profile_extraction_agent", new=AsyncMock(return_value=extraction)):
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
            chunks = list(await session.scalars(select(CrawlPageChunk).where(CrawlPageChunk.job_id == job_id)))
            candidate = await session.scalar(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id))
        assert task is not None and candidate is not None
        self.assertEqual(task.status, CrawlPageTaskStatus.SUCCEEDED.value)
        self.assertEqual(len(chunks), 0)
        self.assertEqual(candidate.name, "张三")
        self.assertEqual(candidate.profile_url, "https://example.edu/teacher/zhang.html")
        self.assertEqual(candidate.source_url, "https://example.edu/teacher/zhang.html")

    async def test_profile_entry_no_candidate_marks_page_terminal(self) -> None:
        job_id, task_id = await self._seed_page_task(
            original_url="https://example.edu/teacher/unknown.html",
            entry_type="profile",
        )
        snapshot = PageSnapshot(
            url="https://example.edu/teacher/unknown.html",
            title="学院新闻",
            text="学院新闻",
            html="<p>学院新闻</p>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )
        extraction = SimpleNamespace(
            payload={"status": "no_candidate", "candidate": None},
            usage=None,
            attempts=[],
            page_text_hash="hash",
            page_text_length=len(snapshot.text),
        )

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(return_value=snapshot)), \
            patch("app.services.crawler_v2_page_worker.ensure_thinking_adaptation", new=AsyncMock(return_value=None)), \
            patch("app.services.crawler_v2_page_worker.invoke_v2_profile_extraction_agent", new=AsyncMock(return_value=extraction)):
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
            chunks = list(await session.scalars(select(CrawlPageChunk).where(CrawlPageChunk.job_id == job_id)))
        assert task is not None
        self.assertEqual(task.status, CrawlPageTaskStatus.FAILED_TERMINAL.value)
        self.assertIn("详情页未识别到导师候选", task.last_error or "")
        self.assertEqual(len(candidates), 0)
        self.assertEqual(len(chunks), 0)
    async def test_profile_entry_browser_fallback_uses_profile_intent(self) -> None:
        _, task_id = await self._seed_page_task(
            original_url="https://example.edu/teacher/zhang.html",
            entry_type="profile",
        )
        direct = PageSnapshot(url="https://example.edu/teacher/zhang.html", text="", html="", links=[], fetch_method="http", status="failed", error_message="403")
        browser = PageSnapshot(url="https://example.edu/teacher/zhang.html", text="张三", html="<p>张三</p>", links=[], fetch_method="browser", status="succeeded")
        extraction = SimpleNamespace(
            payload={"status": "candidate", "candidate": {"name": "张三", "profile_url": "https://example.edu/teacher/zhang.html"}},
            usage=None,
            attempts=[],
            page_text_hash="hash",
            page_text_length=2,
        )

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(return_value=direct)), \
            patch("app.services.crawler_v2_page_worker.fetch_page_browser", new=AsyncMock(return_value=browser)) as browser_mock, \
            patch("app.services.crawler_v2_page_worker.ensure_thinking_adaptation", new=AsyncMock(return_value=None)), \
            patch("app.services.crawler_v2_page_worker.invoke_v2_profile_extraction_agent", new=AsyncMock(return_value=extraction)):
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        self.assertEqual(browser_mock.await_args.kwargs["intent"], "profile")

    async def test_profile_entry_renders_client_encrypted_contact_fields(self) -> None:
        profile_url = "https://faculty.sdu.edu.cn/wanglingyun1/zh_CN/index.htm"
        _, task_id = await self._seed_page_task(original_url=profile_url, entry_type="profile")
        encrypted_email = "72dafd1db91b8976288f94160a5e2779" * 8
        direct = PageSnapshot(
            url=profile_url,
            title="山东大学教师主页 王凌云 首页 中文主页",
            text=f"王凌云 研究员 电子邮箱：{encrypted_email} 个人简介",
            html=(
                f"<html><!--{'x' * 2500}--><body>"
                '<span _tsites_encrypt_field="_tsites_encrypt_field" '
                'id="_tsites_encryp_tsteacher_tsemail" style="display:none;">'
                f"{encrypted_email}</span></body></html>"
            ),
            links=[],
            fetch_method="http",
            status="succeeded",
        )
        browser = PageSnapshot(
            url=profile_url,
            title=direct.title,
            text="王凌云 研究员 电子邮箱：lingyunwang@sdu.edu.cn 个人简介",
            html="<html><body>王凌云 lingyunwang@sdu.edu.cn</body></html>",
            links=[],
            fetch_method="browser",
            status="succeeded",
        )
        extraction = SimpleNamespace(
            payload={"status": "candidate", "candidate": {"name": "王凌云", "email": "lingyunwang@sdu.edu.cn"}},
            usage=None,
            attempts=[],
            page_text_hash="hash",
            page_text_length=len(browser.text),
        )

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(return_value=direct)), \
            patch("app.services.crawler_v2_page_worker.fetch_page_browser", new=AsyncMock(return_value=browser)) as browser_mock, \
            patch("app.services.crawler_v2_page_worker.ensure_thinking_adaptation", new=AsyncMock(return_value=None)), \
            patch("app.services.crawler_v2_page_worker.invoke_v2_profile_extraction_agent", new=AsyncMock(return_value=extraction)):
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        browser_mock.assert_awaited_once()
        self.assertEqual(browser_mock.await_args.kwargs["intent"], "profile")
        async with self.session_factory() as session:
            candidate = await session.scalar(
                select(CrawlCandidate).where(CrawlCandidate.profile_url == profile_url)
            )
        assert candidate is not None
        self.assertEqual(candidate.email, "lingyunwang@sdu.edu.cn")

    async def test_fetch_page_browser_accepts_profile_intent(self) -> None:
        ctx = object()
        browser_snapshot = PageSnapshot(url="https://example.edu/teacher/zhang.html", text="张三", html="", links=[], fetch_method="browser", status="succeeded")

        with patch("app.services.crawler_v2_page_worker.browser_investigate", new=AsyncMock(return_value=browser_snapshot)) as browser_mock:
            result = await fetch_page_browser(ctx, "https://example.edu/teacher/zhang.html", intent="profile")

        self.assertEqual(result.fetch_method, "browser")
        browser_mock.assert_awaited_once_with(ctx, "https://example.edu/teacher/zhang.html", goal="", intent="profile")

    async def test_profile_entry_does_not_save_after_pause_during_llm(self) -> None:
        job_id, task_id = await self._seed_page_task(entry_type="profile")
        snapshot = PageSnapshot(url="https://example.edu/faculty", text="张三", html="<p>张三</p>", links=[], fetch_method="http", status="succeeded")

        async def pause_then_return(*_args, **_kwargs):
            async with self.session_factory() as session:
                job = await session.get(CrawlJob, job_id)
                task = await session.get(CrawlPageTask, task_id)
                assert job is not None and task is not None
                job.status = CrawlJobStatus.PAUSED.value
                task.status = CrawlPageTaskStatus.PENDING.value
                task.worker_id = None
                await session.commit()
            return SimpleNamespace(
                payload={"status": "candidate", "candidate": {"name": "张三", "profile_url": "https://example.edu/faculty"}},
                usage={"input_tokens": 1, "output_tokens": 1, "cached_tokens": 0},
                attempts=[],
                page_text_hash="hash",
                page_text_length=2,
            )

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(return_value=snapshot)), \
            patch("app.services.crawler_v2_page_worker.ensure_thinking_adaptation", new=AsyncMock(return_value=None)), \
            patch("app.services.crawler_v2_page_worker.invoke_v2_profile_extraction_agent", new=AsyncMock(side_effect=pause_then_return)):
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
            task = await session.get(CrawlPageTask, task_id)
        assert task is not None
        self.assertEqual(task.status, CrawlPageTaskStatus.PENDING.value)
        self.assertEqual(len(candidates), 0)

    async def test_profile_entry_records_page_worker_token_usage(self) -> None:
        job_id, task_id = await self._seed_page_task(entry_type="profile")
        snapshot = PageSnapshot(url="https://example.edu/faculty", text="张三", html="<p>张三</p>", links=[], fetch_method="http", status="succeeded")
        extraction = SimpleNamespace(
            payload={"status": "candidate", "candidate": {"name": "张三", "profile_url": "https://example.edu/faculty"}},
            usage={"input_tokens": 12, "output_tokens": 5, "cached_tokens": 2},
            attempts=[],
            page_text_hash="hash",
            page_text_length=2,
        )

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(return_value=snapshot)), \
            patch("app.services.crawler_v2_page_worker.ensure_thinking_adaptation", new=AsyncMock(return_value=None)), \
            patch("app.services.crawler_v2_page_worker.invoke_v2_profile_extraction_agent", new=AsyncMock(return_value=extraction)):
            await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        async with self.session_factory() as session:
            usage = await session.scalar(select(CrawlWorkerTokenUsage).where(CrawlWorkerTokenUsage.job_id == job_id))
        assert usage is not None
        self.assertEqual(usage.worker_kind, CrawlWorkerKind.PAGE.value)
        self.assertEqual(usage.work_item_id, str(task_id))
        self.assertEqual(usage.input_tokens, 12)
        self.assertEqual(usage.output_tokens, 5)
        self.assertEqual(usage.cached_tokens, 2)

    async def test_profile_entry_writes_debug_events_without_page_text(self) -> None:
        _, task_id = await self._seed_page_task(entry_type="profile")
        snapshot = PageSnapshot(url="https://example.edu/faculty", text="张三", html="<p>张三</p>", links=[], fetch_method="http", status="succeeded")
        attempt = SimpleNamespace(
            attempt_number=1,
            raw_model_text="{\"status\":\"candidate\"}",
            raw_payload={"status": "candidate"},
            error=None,
            usage={"input_tokens": 1, "output_tokens": 1, "cached_tokens": 0},
        )
        extraction = SimpleNamespace(
            payload={"status": "candidate", "candidate": {"name": "张三", "profile_url": "https://example.edu/faculty"}},
            usage={"input_tokens": 1, "output_tokens": 1, "cached_tokens": 0},
            attempts=[attempt],
            page_text_hash="hash",
            page_text_length=2,
        )

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(return_value=snapshot)), \
            patch("app.services.crawler_v2_page_worker.ensure_thinking_adaptation", new=AsyncMock(return_value=None)), \
            patch("app.services.crawler_v2_page_worker.invoke_v2_profile_extraction_agent", new=AsyncMock(return_value=extraction)), \
            patch("app.services.crawler_v2_page_worker.append_crawler_v2_debug_event") as debug_mock:
            await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        events = [call.kwargs["event_name"] for call in debug_mock.call_args_list]
        self.assertIn("page_fetched", events)
        self.assertIn("profile_extract_requested", events)
        self.assertIn("profile_extract_llm_response", events)
        self.assertIn("profile_extract_completed", events)
        llm_call = next(call for call in debug_mock.call_args_list if call.kwargs["event_name"] == "profile_extract_llm_response")
        payload = llm_call.kwargs["payload"]
        self.assertEqual(payload["source_url"], "https://example.edu/faculty")
        self.assertEqual(payload["attempt_number"], 1)
        self.assertEqual(payload["raw_model_text"], "{\"status\":\"candidate\"}")
        self.assertEqual(payload["raw_payload"], {"status": "candidate"})
        self.assertEqual(payload["page_text_hash"], "hash")
        self.assertEqual(payload["page_text_length"], 2)
        self.assertNotIn("page_text", payload)

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


    async def test_page_worker_failure_sets_retry_backoff(self) -> None:
        _, task_id = await self._seed_page_task()

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(side_effect=ValueError("429 Too Many Requests"))):
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
        assert task is not None
        self.assertEqual(task.status, CrawlPageTaskStatus.FAILED_RETRYABLE.value)
        self.assertIn("429", task.last_error or "")
        self.assertIsNone(task.worker_id)
        self.assertIsNone(task.claimed_at)
        self.assertIsNotNone(task.lease_expires_at)
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

    async def test_dynamic_webplus_teacher_directory_uses_browser_fallback(self) -> None:
        job_id, task_id = await self._seed_page_task(
            original_url="https://software.fudan.edu.cn/zzjs/list.htm",
        )
        direct_html = """
        <html>
          <head><title>在职教师</title></head>
          <body class="teacher" id="zzjs">
            <div class="teachers-list">
              <ul class="teacher_list career_list">
                <li class="career_1">
                  <div class="career_name clearfix"><div class="title zc">教授</div></div>
                  <div class="type_info clearfix"></div>
                </li>
              </ul>
            </div>
            <img src="/_visitcount?siteId=619&type=2&columnId=29336" />
            <script src="/_upload/tpl/0d/27/3367/template3367/js/search_teacher.js"></script>
          </body>
        </html>
        """
        direct = PageSnapshot(
            url="https://software.fudan.edu.cn/zzjs/list.htm",
            title="在职教师",
            text="师资队伍 在职教师 教授",
            html=direct_html,
            links=["https://software.fudan.edu.cn/_upload/tpl/0d/27/3367/template3367/js/search_teacher.js"],
            fetch_method="http",
            status="succeeded",
        )
        browser = PageSnapshot(
            url="https://software.fudan.edu.cn/zzjs/list.htm",
            title="在职教师",
            text="在职教师 教授 赵文耘",
            html="""
            <html><body>
              <div class="teachers-list">
                <ul class="teacher_list career_list">
                  <li class="career_1">
                    <div class="type_info clearfix">
                      <li><span class="news_title"><a href="/b5/cd/c29336a308685/page.htm">赵文耘</a></span></li>
                    </div>
                  </li>
                </ul>
              </div>
            </body></html>
            """,
            links=["https://software.fudan.edu.cn/b5/cd/c29336a308685/page.htm"],
            fetch_method="browser",
            status="succeeded",
        )

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock(return_value=direct)), \
            patch("app.services.crawler_v2_page_worker.fetch_page_browser", new=AsyncMock(return_value=browser)) as browser_mock:
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        browser_mock.assert_awaited_once()
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
            chunks = list(await session.scalars(select(CrawlPageChunk).where(CrawlPageChunk.job_id == job_id)))
        assert task is not None
        self.assertEqual(task.status, CrawlPageTaskStatus.SUCCEEDED.value)
        self.assertEqual(task.fetch_mode, "browser")
        self.assertTrue(
            any("[赵文耘](https://software.fudan.edu.cn/b5/cd/c29336a308685/page.htm)" in chunk.content for chunk in chunks),
            "browser-rendered teacher links should be chunked for candidate extraction",
        )

    async def test_same_job_same_domain_uses_browser_after_prior_direct_fallback(self) -> None:
        job_id, task_id = await self._seed_page_task(original_url="https://example.edu/faculty/page2")
        async with self.session_factory() as session:
            session.add(
                CrawlPageFetchState(
                    job_id=job_id,
                    normalized_url="https://example.edu/faculty",
                    original_url="https://example.edu/faculty",
                    status=CrawlPageFetchStatus.SUCCEEDED.value,
                    fetch_mode="browser",
                    direct_status="failed",
                    fallback_reason="HTTP 412 blocked, browser fallback advised",
                    browser_status="succeeded",
                ),
            )
            await session.commit()
        browser = PageSnapshot(
            url="https://example.edu/faculty/page2",
            text="李四",
            html="<p>李四</p>",
            links=[],
            fetch_method="browser",
            status="succeeded",
        )

        with patch("app.services.crawler_v2_page_worker.fetch_page_direct", new=AsyncMock()) as direct_mock, \
            patch("app.services.crawler_v2_page_worker.fetch_page_browser", new=AsyncMock(return_value=browser)):
            processed = await run_crawler_v2_page_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        direct_mock.assert_not_awaited()
        async with self.session_factory() as session:
            task = await session.get(CrawlPageTask, task_id)
        assert task is not None
        self.assertEqual(task.status, CrawlPageTaskStatus.SUCCEEDED.value)
        self.assertEqual(task.fetch_mode, "browser")
        self.assertEqual(task.direct_status, "skipped_by_domain_browser_preference")
        self.assertEqual(task.browser_status, "succeeded")

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
    async def _seed_page_task(self, *, original_url: str = "https://example.edu/faculty", entry_type: str = "list") -> tuple[int, int]:
        async with self.session_factory() as session:
            profile = LLMProfile(name="默认模型", provider="openai", api_key="test", model_name="test-model", is_default=True)
            session.add(profile)
            await session.flush()
            job = CrawlJob(university="示例大学", school="计算机学院", start_url=original_url, start_urls=[original_url], status=CrawlJobStatus.RUNNING.value, runtime_version="v2", entry_type=entry_type, llm_profile_id=profile.id)
            session.add(job)
            await session.flush()
            task = CrawlPageTask(job_id=job.id, normalized_url=original_url, original_url=original_url, status=CrawlPageTaskStatus.PROCESSING.value, worker_id="w1")
            session.add(task)
            await session.commit()
            return job.id, task.id


if __name__ == "__main__":
    unittest.main()
