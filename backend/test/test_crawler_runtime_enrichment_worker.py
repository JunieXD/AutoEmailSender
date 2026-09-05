from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test.schema_database import create_schema_sqlite_database

from app.models import (
    CrawlCandidate,
    CrawlCandidateEnrichmentTask,
    CrawlCandidateEnrichmentTaskStatus,
    CrawlJob,
    CrawlJobKind,
    CrawlJobStatus,
    CrawlPage,
    CrawlPageChunk,
    CrawlPageChunkStatus,
    CrawlWorkerTokenUsage,
    LLMProfile,
)
from app.modules.crawler.pages.tools import CandidateEnrichmentPayload, PageSnapshot
from app.modules.crawler.llm.structured_output import (
    CandidateEmailSelectionWirePayload,
    CandidateEnrichmentWirePayload,
    ProfileLinkSelectionWirePayload,
)
from app.modules.llm.runtime import (
    ChatCompletionResult,
    ChatCompletionUsage,
    LLMRuntimeError,
    LLMRuntimeAdaptation,
)
from app.modules.crawler.runtime import enrichment_worker as crawler_runtime_enrichment_worker
from app.modules.crawler.runtime.enrichment_worker import (
    enrich_candidate_once,
    run_crawler_enrichment_worker_once,
)
from app.modules.crawler.runtime.profile_fallbacks import EmailEvidence
from app.modules.crawler.runtime.profile_documents import EmbeddedProfilePdfText


class CrawlerRuntimeEnrichmentWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_enrichment_adaptation_cache_is_committed_before_context_session_closes(
        self,
    ) -> None:
        from app.modules.llm.adaptation.endpoint import (
            get_cached_endpoint_kind,
            record_endpoint_adaptation,
        )

        candidate_id, _ = await self._seed_task(
            profile_url="https://example.edu/zhang.html"
        )
        adaptation = LLMRuntimeAdaptation("responses", None)

        async def fake_ensure(session, profile):
            await record_endpoint_adaptation(
                session,
                api_base_url=profile.api_base_url or "",
                model_name=profile.model_name,
                endpoint_kind="responses",
            )
            return adaptation

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(side_effect=fake_ensure),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.get_or_fetch_profile_text",
                new=AsyncMock(return_value="张三"),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_profile_with_llm_with_usage",
                new=AsyncMock(return_value=(CandidateEnrichmentPayload(), None, "")),
            ),
        ):
            await crawler_runtime_enrichment_worker.enrich_candidate_once_with_usage(
                self.session_factory,
                candidate_id=candidate_id,
            )

        async with self.session_factory() as session:
            self.assertEqual(
                await get_cached_endpoint_kind(
                    session,
                    api_base_url="https://api.example.com/v1",
                    model_name="deepseek",
                ),
                "responses",
            )

    async def asyncSetUp(self) -> None:
        crawler_runtime_enrichment_worker._PROFILE_CHILD_SNAPSHOT_CACHE.clear()
        crawler_runtime_enrichment_worker._PROFILE_CHILD_SNAPSHOT_INFLIGHT.clear()
        crawler_runtime_enrichment_worker._PROFILE_TEXT_CACHE.clear()
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        create_schema_sqlite_database(Path(self.db_path))
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{Path(self.db_path).as_posix()}"
        )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    async def test_enrichment_updates_missing_fields(self) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html"
        )
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            second_candidate = CrawlCandidate(
                job_id=task.job_id,
                name="李四",
                profile_url="https://example.edu/li.html",
            )
            session.add(second_candidate)
            await session.flush()
            session.add(
                CrawlCandidateEnrichmentTask(
                    job_id=task.job_id,
                    candidate_id=second_candidate.id,
                    enrichment_operation_id=task.enrichment_operation_id,
                    status=CrawlCandidateEnrichmentTaskStatus.PENDING.value,
                )
            )
            await session.commit()
        papers = [f"P{index}" for index in range(1, 13)]
        payload = CandidateEnrichmentPayload.model_construct(
            email="zhang@example.edu",
            title="教授",
            department="计算机系",
            research_direction="AI",
            recent_papers=papers,
        )

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(return_value=(payload, None, None)),
        ):
            runtime_session_factory = async_sessionmaker(
                self.engine,
                autoflush=False,
                expire_on_commit=False,
            )
            processed = await run_crawler_enrichment_worker_once(
                runtime_session_factory, task_id=task_id, worker_id="w1"
            )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert candidate is not None and task is not None
        self.assertEqual(candidate.email, "zhang@example.edu")
        self.assertEqual(candidate.title, "教授")
        self.assertEqual(candidate.department, "计算机系")
        self.assertEqual(candidate.recent_papers, papers[:8])
        self.assertEqual(
            task.status, CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value
        )
        self.assertEqual(
            task.enriched_fields,
            ["email", "title", "department", "research_direction", "recent_papers"],
        )
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, task.job_id)
        assert job is not None
        trace = [item for item in job.agent_trace or [] if isinstance(item, dict)]
        self.assertTrue(trace)
        self.assertEqual(trace[-1]["event_type"], "enrichment")
        self.assertEqual(trace[-1]["message"], "候选导师详情补全成功：张三（1 / 2）")
        self.assertEqual(trace[-1]["raw"]["candidate_id"], candidate_id)
        self.assertEqual(trace[-1]["raw"]["progress_current"], 1)
        self.assertEqual(trace[-1]["raw"]["progress_total"], 2)
        self.assertEqual(trace[-1]["raw"]["status"], "succeeded")

    async def test_enrichment_preserves_existing_candidate_fields(self) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html",
        )
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            assert candidate is not None
            candidate.email = "manual@example.edu"
            candidate.title = "手动职称"
            candidate.department = "手动部门"
            candidate.research_direction = "手动研究方向"
            candidate.recent_papers = ["手动论文"]
            await session.commit()

        payload = CandidateEnrichmentPayload(
            email="crawler@example.edu",
            title="抓取职称",
            department="抓取部门",
            research_direction="抓取研究方向",
            recent_papers=["抓取论文"],
            confidence=0.8,
            field_confidence={},
        )
        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(return_value=(payload, None, None)),
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert candidate is not None and task is not None
        self.assertEqual(candidate.email, "manual@example.edu")
        self.assertEqual(candidate.title, "手动职称")
        self.assertEqual(candidate.department, "手动部门")
        self.assertEqual(candidate.research_direction, "手动研究方向")
        self.assertEqual(candidate.recent_papers, ["手动论文"])
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.SKIPPED.value)
        self.assertEqual(task.skip_reason, "个人主页未提供可补全的新信息")
        self.assertEqual(task.enriched_fields, [])

    async def test_empty_enrichment_payload_is_recorded_as_unchanged(self) -> None:
        _, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html",
        )

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(return_value=(CandidateEnrichmentPayload(), None, None)),
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            job = await session.get(CrawlJob, task.job_id)
        assert job is not None
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.SKIPPED.value)
        self.assertEqual(task.skip_reason, "个人主页未提供可补全的新信息")
        trace = [item for item in job.agent_trace or [] if isinstance(item, dict)]
        self.assertEqual(
            trace[-1]["message"], "候选导师详情未发现新信息：张三（1 / 1）"
        )
        self.assertEqual(trace[-1]["raw"]["progress_current"], 1)
        self.assertEqual(trace[-1]["raw"]["progress_total"], 1)
        self.assertEqual(trace[-1]["raw"]["status"], "skipped")

    async def test_saved_profile_shell_is_refetched(self) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html",
        )
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            session.add(
                CrawlPage(
                    job_id=task.job_id,
                    url="https://example.edu/zhang.html",
                    parent_url="https://example.edu/faculty",
                    fetch_method="browser",
                    status="succeeded",
                    title="个人主页",
                    text_excerpt="首页 导航 版权所有",
                    error_message=None,
                )
            )
            await session.commit()
            job_id = task.job_id

        ctx = crawler_runtime_enrichment_worker.CrawlToolContext(
            job_id=job_id,
            start_url="https://example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=self.session_factory,
        )
        with patch(
            "app.modules.crawler.runtime.enrichment_worker.fetch_profile_text",
            new=AsyncMock(return_value="张三 zhang@example.edu"),
        ) as fetch_mock:
            text = await crawler_runtime_enrichment_worker.get_or_fetch_profile_text(
                ctx,
                candidate_id,
                "https://example.edu/zhang.html",
            )

        self.assertEqual(text, "张三 zhang@example.edu")
        fetch_mock.assert_awaited_once()

    async def test_fresh_saved_profile_is_reused(self) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html",
        )
        saved_text = "张三 教授 邮箱 zhang@example.edu"
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            session.add(
                CrawlPage(
                    job_id=task.job_id,
                    url="https://example.edu/zhang.html",
                    parent_url="https://example.edu/faculty",
                    fetch_method="browser",
                    status="succeeded",
                    title="个人主页",
                    text_excerpt=saved_text,
                    error_message=None,
                    created_at=crawler_runtime_enrichment_worker.utc_now(),
                )
            )
            await session.commit()
            job_id = task.job_id

        ctx = crawler_runtime_enrichment_worker.CrawlToolContext(
            job_id=job_id,
            start_url="https://example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=self.session_factory,
        )
        with patch(
            "app.modules.crawler.runtime.enrichment_worker.fetch_profile_text",
            new=AsyncMock(return_value="不应抓取"),
        ) as fetch_mock:
            text = await crawler_runtime_enrichment_worker.get_or_fetch_profile_text(
                ctx,
                candidate_id,
                "https://example.edu/zhang.html",
            )

        self.assertEqual(text, saved_text)
        fetch_mock.assert_not_awaited()

    async def test_previous_operation_saved_profile_is_not_reused(self) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html",
        )
        operation_started_at = crawler_runtime_enrichment_worker.utc_now()
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            session.add(
                CrawlPage(
                    job_id=task.job_id,
                    url="https://example.edu/zhang.html",
                    fetch_method="browser",
                    status="succeeded",
                    text_excerpt="张三 旧邮箱 old@example.edu",
                    created_at=operation_started_at - timedelta(seconds=1),
                )
            )
            await session.commit()
            job_id = task.job_id

        ctx = crawler_runtime_enrichment_worker.CrawlToolContext(
            job_id=job_id,
            start_url="https://example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=self.session_factory,
        )
        with patch(
            "app.modules.crawler.runtime.enrichment_worker.fetch_profile_text",
            new=AsyncMock(return_value="张三 新邮箱 new@example.edu"),
        ) as fetch_mock:
            text = await crawler_runtime_enrichment_worker.get_or_fetch_profile_text(
                ctx,
                candidate_id,
                "https://example.edu/zhang.html",
                fresh_after=operation_started_at,
            )

        self.assertEqual(text, "张三 新邮箱 new@example.edu")
        fetch_mock.assert_awaited_once()

    async def test_same_operation_saved_profile_is_reused_on_retry(self) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html",
        )
        operation_started_at = crawler_runtime_enrichment_worker.utc_now() - timedelta(
            seconds=1
        )
        saved_text = "张三 本轮邮箱 current@example.edu"
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            session.add(
                CrawlPage(
                    job_id=task.job_id,
                    url="https://example.edu/zhang.html",
                    fetch_method="browser",
                    status="succeeded",
                    text_excerpt=saved_text,
                    created_at=crawler_runtime_enrichment_worker.utc_now(),
                )
            )
            await session.commit()
            job_id = task.job_id

        ctx = crawler_runtime_enrichment_worker.CrawlToolContext(
            job_id=job_id,
            start_url="https://example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=self.session_factory,
        )
        with patch(
            "app.modules.crawler.runtime.enrichment_worker.fetch_profile_text",
            new=AsyncMock(return_value="不应抓取"),
        ) as fetch_mock:
            text = await crawler_runtime_enrichment_worker.get_or_fetch_profile_text(
                ctx,
                candidate_id,
                "https://example.edu/zhang.html",
                fresh_after=operation_started_at,
            )

        self.assertEqual(text, saved_text)
        fetch_mock.assert_not_awaited()

    async def test_fetch_profile_text_forces_network_fetch_past_page_ledger(
        self,
    ) -> None:
        ctx = crawler_runtime_enrichment_worker.CrawlToolContext(
            job_id=1,
            start_url="https://example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=self.session_factory,
        )
        snapshot = PageSnapshot(
            url="https://example.edu/zhang.html",
            text="张三 zhang@example.edu",
            fetch_method="http",
            status="succeeded",
        )
        with patch(
            "app.modules.crawler.runtime.enrichment_worker.crawl_page_with_browser_fallback",
            new=AsyncMock(return_value=snapshot),
        ) as crawl_mock:
            text = await crawler_runtime_enrichment_worker.fetch_profile_text(
                ctx,
                "https://example.edu/zhang.html",
            )

        self.assertEqual(text, snapshot.text)
        crawl_mock.assert_awaited_once_with(
            ctx,
            "https://example.edu/zhang.html",
            intent="profile",
            force_fetch=True,
        )

    async def test_unavailable_profile_fails_once_without_rejecting_candidate(
        self,
    ) -> None:
        profile_url = "https://example.edu/missing.html"
        candidate_id, task_id = await self._seed_task(profile_url=profile_url)
        snapshot = PageSnapshot(
            url=profile_url,
            title="404错误提示",
            text="系统提示 您访问的页面未找到，5秒后自动跳转到首页",
            fetch_method="browser",
            status="succeeded",
            http_status_code=404,
        )

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(
                    return_value=LLMRuntimeAdaptation("chat_completions", None)
                ),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.crawl_page_with_browser_fallback",
                new=AsyncMock(return_value=snapshot),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.request_crawler_structured_completion",
                new=AsyncMock(),
            ) as llm_mock,
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        llm_mock.assert_not_awaited()
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert candidate is not None and task is not None
        self.assertEqual(candidate.profile_url, profile_url)
        self.assertNotEqual(candidate.review_status, "rejected")
        self.assertEqual(
            task.status,
            CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
        )
        self.assertEqual(task.failure_count, 1)
        self.assertEqual(task.last_error, "个人资料页不存在或已失效")

    async def test_unavailable_profile_with_existing_email_is_unchanged(self) -> None:
        profile_url = "https://example.edu/missing-with-email.html"
        candidate_id, task_id = await self._seed_task(profile_url=profile_url)
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            assert candidate is not None
            candidate.email = "zhang@example.edu"
            await session.commit()
        snapshot = PageSnapshot(
            url=profile_url,
            title="404错误提示",
            text="系统提示 您访问的页面未找到，5秒后自动跳转到首页",
            fetch_method="browser",
            status="succeeded",
            http_status_code=404,
        )

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(
                    return_value=LLMRuntimeAdaptation("chat_completions", None)
                ),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.crawl_page_with_browser_fallback",
                new=AsyncMock(return_value=snapshot),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.request_crawler_structured_completion",
                new=AsyncMock(),
            ) as llm_mock,
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        llm_mock.assert_not_awaited()
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            job = await session.get(CrawlJob, task.job_id)
        assert task is not None and job is not None
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.SKIPPED.value)
        self.assertEqual(
            task.skip_reason,
            "个人资料页不可用，已保留候选导师已有信息",
        )
        self.assertIsNone(task.last_error)
        self.assertEqual(task.failure_count, 0)
        trace = [item for item in job.agent_trace or [] if isinstance(item, dict)]
        self.assertEqual(trace[-1]["raw"]["status"], "skipped")

    async def test_fetch_profile_text_merges_embedded_pdf_and_updates_saved_page(
        self,
    ) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html",
        )
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            page = CrawlPage(
                job_id=task.job_id,
                url="https://example.edu/zhang.html",
                fetch_method="browser",
                status="succeeded",
                text_excerpt="张三个人主页",
            )
            session.add(page)
            await session.commit()
            await session.refresh(page)
            job_id = task.job_id
            page_id = page.id

        ctx = crawler_runtime_enrichment_worker.CrawlToolContext(
            job_id=job_id,
            start_url="https://example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=self.session_factory,
        )
        snapshot = PageSnapshot(
            page_id=page_id,
            url="https://example.edu/zhang.html",
            text="张三个人主页",
            html='<iframe src="/viewer.html?file=/zhang.pdf"></iframe>',
            fetch_method="browser",
            status="succeeded",
        )
        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.crawl_page_with_browser_fallback",
                new=AsyncMock(return_value=snapshot),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.extract_primary_embedded_profile_pdf_text",
                new=AsyncMock(
                    return_value=EmbeddedProfilePdfText(
                        source_url="https://example.edu/zhang.pdf",
                        text="张三 教授 邮箱 zhang@example.edu",
                    )
                ),
            ),
        ):
            text = await crawler_runtime_enrichment_worker.fetch_profile_text(
                ctx,
                "https://example.edu/zhang.html",
            )

        self.assertIn("张三个人主页", text)
        self.assertIn("zhang@example.edu", text)
        self.assertEqual(snapshot.text, text)
        async with self.session_factory() as session:
            saved_page = await session.get(CrawlPage, page_id)
        assert saved_page is not None
        self.assertEqual(saved_page.text_excerpt, text)

    async def test_worker_passes_operation_start_to_profile_cache_boundary(
        self,
    ) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html",
        )
        operation_started_at = datetime.now(UTC) - timedelta(seconds=2)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            task.started_at = operation_started_at
            await session.commit()

        payload = CandidateEnrichmentPayload(email="zhang@example.edu")
        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(return_value=(payload, None, None)),
        ) as enrich_mock:
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        enrich_mock.assert_awaited_once_with(
            self.session_factory,
            candidate_id=candidate_id,
            fresh_after=operation_started_at,
            prefer_compact_input=False,
        )

    async def test_fresh_saved_profile_with_trailing_slash_is_reused(self) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang",
        )
        saved_text = "张三 教授 邮箱 zhang@example.edu"
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            session.add(
                CrawlPage(
                    job_id=task.job_id,
                    url="https://example.edu/zhang/",
                    parent_url="https://example.edu/faculty",
                    fetch_method="browser",
                    status="succeeded",
                    title="个人主页",
                    text_excerpt=saved_text,
                    error_message=None,
                    created_at=crawler_runtime_enrichment_worker.utc_now(),
                )
            )
            await session.commit()
            job_id = task.job_id

        ctx = crawler_runtime_enrichment_worker.CrawlToolContext(
            job_id=job_id,
            start_url="https://example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=self.session_factory,
        )
        with patch(
            "app.modules.crawler.runtime.enrichment_worker.fetch_profile_text",
            new=AsyncMock(return_value="不应抓取"),
        ) as fetch_mock:
            text = await crawler_runtime_enrichment_worker.get_or_fetch_profile_text(
                ctx,
                candidate_id,
                "https://example.edu/zhang",
            )

        self.assertEqual(text, saved_text)
        fetch_mock.assert_not_awaited()

    async def test_expired_saved_profile_is_refetched(self) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html",
        )
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            session.add(
                CrawlPage(
                    job_id=task.job_id,
                    url="https://example.edu/zhang.html",
                    parent_url="https://example.edu/faculty",
                    fetch_method="browser",
                    status="succeeded",
                    title="个人主页",
                    text_excerpt="张三 旧邮箱 old@example.edu",
                    error_message=None,
                    created_at=crawler_runtime_enrichment_worker.utc_now()
                    - timedelta(hours=2),
                )
            )
            await session.commit()
            job_id = task.job_id

        ctx = crawler_runtime_enrichment_worker.CrawlToolContext(
            job_id=job_id,
            start_url="https://example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=self.session_factory,
        )
        with patch(
            "app.modules.crawler.runtime.enrichment_worker.fetch_profile_text",
            new=AsyncMock(return_value="张三 新邮箱 new@example.edu"),
        ) as fetch_mock:
            text = await crawler_runtime_enrichment_worker.get_or_fetch_profile_text(
                ctx,
                candidate_id,
                "https://example.edu/zhang.html",
            )

        self.assertEqual(text, "张三 新邮箱 new@example.edu")
        fetch_mock.assert_awaited_once()

    async def test_capped_saved_profile_with_html_remnants_is_refetched(self) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html",
        )
        markup = "<ul><li>隐藏导航 old@example.edu</li></ul>"
        corrupted_text = (
            markup * (crawler_runtime_enrichment_worker.MAX_TEXT_CHARS // len(markup) + 1)
        )[: crawler_runtime_enrichment_worker.MAX_TEXT_CHARS]
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            session.add(
                CrawlPage(
                    job_id=task.job_id,
                    url="https://example.edu/zhang.html",
                    parent_url="https://example.edu/faculty",
                    fetch_method="browser",
                    status="succeeded",
                    title="个人主页",
                    text_excerpt=corrupted_text,
                    error_message=None,
                    created_at=crawler_runtime_enrichment_worker.utc_now(),
                )
            )
            await session.commit()
            job_id = task.job_id

        ctx = crawler_runtime_enrichment_worker.CrawlToolContext(
            job_id=job_id,
            start_url="https://example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=self.session_factory,
        )
        with patch(
            "app.modules.crawler.runtime.enrichment_worker.fetch_profile_text",
            new=AsyncMock(return_value="张三 zhang@example.edu"),
        ) as fetch_mock:
            text = await crawler_runtime_enrichment_worker.get_or_fetch_profile_text(
                ctx,
                candidate_id,
                "https://example.edu/zhang.html",
            )

        self.assertEqual(text, "张三 zhang@example.edu")
        fetch_mock.assert_awaited_once()

    async def test_enrichment_skips_candidate_without_profile_url(self) -> None:
        candidate_id, task_id = await self._seed_task(profile_url=None)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert task is not None
        cache_key = (
            id(self.session_factory),
            task.job_id,
            candidate_id,
            "https://example.edu/old",
        )
        crawler_runtime_enrichment_worker._PROFILE_TEXT_CACHE.put(cache_key, "old profile")

        processed = await run_crawler_enrichment_worker_once(
            self.session_factory, task_id=task_id, worker_id="w1"
        )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert task is not None
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.SKIPPED.value)
        self.assertNotIn(cache_key, crawler_runtime_enrichment_worker._PROFILE_TEXT_CACHE)

    async def test_enrichment_adapter_fetches_profile_and_invokes_existing_llm(
        self,
    ) -> None:
        candidate_id, _ = await self._seed_task(
            profile_url="https://example.edu/zhang.html"
        )
        payload = CandidateEnrichmentPayload(
            email="zhang@example.edu",
            department="计算机系",
            research_direction="AI",
            recent_papers=[],
            confidence=0.8,
            field_confidence={},
        )

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.fetch_profile_text",
                new=AsyncMock(return_value="张三 邮箱 zhang@example.edu"),
            ) as fetch_mock,
            patch(
                "app.modules.crawler.runtime.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(
                    return_value=LLMRuntimeAdaptation("chat_completions", None)
                ),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_profile_with_llm_with_usage",
                new=AsyncMock(return_value=(payload, None)),
            ) as enrich_mock,
        ):
            result = await enrich_candidate_once(
                self.session_factory, candidate_id=candidate_id
            )

        self.assertEqual(result.email, "zhang@example.edu")
        fetch_mock.assert_awaited_once()
        self.assertEqual(
            fetch_mock.await_args.args[0].start_url, "https://example.edu/faculty"
        )
        enrich_mock.assert_awaited_once()

    async def test_explicit_cross_domain_profile_uses_itself_as_single_page_root(
        self,
    ) -> None:
        profile_url = "https://people.example.net/zhang"
        candidate_id, _ = await self._seed_task(profile_url=profile_url)
        await self._add_source_chunk(
            candidate_id=candidate_id,
            content=f"[张三]({profile_url}) 教授",
        )
        payload = CandidateEnrichmentPayload(email="zhang@people.example.net")

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(
                    return_value=LLMRuntimeAdaptation("chat_completions", None)
                ),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.get_or_fetch_profile_text",
                new=AsyncMock(return_value="张三"),
            ) as fetch_mock,
            patch(
                "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_profile_with_llm_with_usage",
                new=AsyncMock(return_value=(payload, None, "")),
            ),
        ):
            result = await enrich_candidate_once(
                self.session_factory,
                candidate_id=candidate_id,
            )

        self.assertEqual(result.email, "zhang@people.example.net")
        fetch_mock.assert_awaited_once()
        context = fetch_mock.await_args.args[0]
        self.assertEqual(context.start_url, profile_url)
        self.assertTrue(context.allow_public_dns_fallback)

    async def test_encoded_cross_domain_profile_uses_itself_as_single_page_root(
        self,
    ) -> None:
        profile_url = "https://guanwei49.github.io/"
        candidate_id, _ = await self._seed_task(profile_url=profile_url)
        await self._add_source_chunk(
            candidate_id=candidate_id,
            content=(
                "[关威](https://faculty.example.edu/detail?"
                "home=https%3A%2F%2Fguanwei49.github.io%2F) 教授"
            ),
        )
        payload = CandidateEnrichmentPayload(email="guan_wei@tju.edu.cn")

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(
                    return_value=LLMRuntimeAdaptation("chat_completions", None)
                ),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.get_or_fetch_profile_text",
                new=AsyncMock(return_value="关威"),
            ) as fetch_mock,
            patch(
                "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_profile_with_llm_with_usage",
                new=AsyncMock(return_value=(payload, None, "")),
            ),
        ):
            result = await enrich_candidate_once(
                self.session_factory,
                candidate_id=candidate_id,
            )

        self.assertEqual(result.email, "guan_wei@tju.edu.cn")
        self.assertEqual(fetch_mock.await_args.args[0].start_url, profile_url)

    async def test_unproven_cross_domain_profile_fails_terminal_without_retry(
        self,
    ) -> None:
        _, task_id = await self._seed_task(
            profile_url="https://people.example.net/invented"
        )
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            task.attempt_count = 1
            await session.commit()

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(),
            ) as adaptation_mock,
            patch(
                "app.modules.crawler.runtime.enrichment_worker.fetch_profile_text",
                new=AsyncMock(),
            ) as fetch_mock,
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert task is not None
        self.assertEqual(
            task.status,
            CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
        )
        self.assertIn("未在来源列表原文中出现", task.last_error or "")
        adaptation_mock.assert_not_awaited()
        fetch_mock.assert_not_awaited()

    async def test_embedded_unproven_cross_domain_profile_stays_blocked(self) -> None:
        _, task_id = await self._seed_task(
            profile_url=(
                "https://example.edu/redirect/https://people.example.net/invented"
            )
        )

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.fetch_profile_text",
            new=AsyncMock(),
        ) as fetch_mock:
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert task is not None
        self.assertEqual(
            task.status,
            CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
        )
        self.assertIn("未在来源列表原文中出现", task.last_error or "")
        fetch_mock.assert_not_awaited()

    async def test_unsafe_cross_domain_profile_stays_blocked(self) -> None:
        profile_url = "http://127.0.0.1/private"
        candidate_id, task_id = await self._seed_task(profile_url=profile_url)
        await self._add_source_chunk(
            candidate_id=candidate_id,
            content=f"[张三]({profile_url}) 教授",
        )
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            task.attempt_count = 1
            await session.commit()

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.fetch_profile_text",
            new=AsyncMock(),
        ) as fetch_mock:
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert task is not None
        self.assertEqual(
            task.status,
            CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
        )
        self.assertIn("内网", task.last_error or "")
        fetch_mock.assert_not_awaited()

    async def test_information_enrichment_can_use_profiles_from_multiple_domains(
        self,
    ) -> None:
        profile_url = "https://people.example.net/zhang"
        candidate_id, _ = await self._seed_task(profile_url=profile_url)
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            assert candidate is not None
            job = await session.get(CrawlJob, candidate.job_id)
            assert job is not None
            job.job_kind = CrawlJobKind.PROFESSOR_ENRICHMENT.value
            await session.commit()

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(
                    return_value=LLMRuntimeAdaptation("chat_completions", None)
                ),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.get_or_fetch_profile_text",
                new=AsyncMock(return_value="张三"),
            ) as fetch_mock,
            patch(
                "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_profile_with_llm_with_usage",
                new=AsyncMock(return_value=(CandidateEnrichmentPayload(), None, "")),
            ),
        ):
            await enrich_candidate_once(
                self.session_factory,
                candidate_id=candidate_id,
            )

        fetch_mock.assert_awaited_once()
        self.assertEqual(fetch_mock.await_args.args[0].start_url, profile_url)

    async def test_enrichment_worker_does_not_write_after_job_is_paused(self) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html"
        )
        payload = CandidateEnrichmentPayload(
            email="zhang@example.edu",
            department="计算机系",
            research_direction="AI",
            recent_papers=[],
            confidence=0.8,
            field_confidence={},
        )
        cache_key: tuple[int, int, int, str] | None = None

        async def pause_job_during_enrichment(*_args, **_kwargs):
            nonlocal cache_key
            async with self.session_factory() as session:
                task = await session.get(CrawlCandidateEnrichmentTask, task_id)
                assert task is not None
                job = await session.get(CrawlJob, task.job_id)
                assert job is not None
                cache_key = (
                    id(self.session_factory),
                    job.id,
                    candidate_id,
                    "https://example.edu/zhang.html",
                )
                crawler_runtime_enrichment_worker._PROFILE_TEXT_CACHE.put(cache_key, "张三")
                job.status = CrawlJobStatus.PAUSED.value
                task.status = CrawlCandidateEnrichmentTaskStatus.PENDING.value
                task.worker_id = None
                await session.commit()
            return payload, {"input_tokens": 10, "output_tokens": 5, "cached_tokens": 0}, None

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(side_effect=pause_job_during_enrichment),
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory, task_id=task_id, worker_id="w1"
            )

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            token_usage = list(await session.scalars(select(CrawlWorkerTokenUsage)))
        assert candidate is not None and task is not None
        self.assertIsNone(candidate.email)
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.PENDING.value)
        self.assertEqual(token_usage, [])
        assert cache_key is not None
        self.assertIn(cache_key, crawler_runtime_enrichment_worker._PROFILE_TEXT_CACHE)

    async def test_enrichment_worker_clears_cache_if_task_is_canceled_during_fetch(
        self,
    ) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html"
        )
        payload = CandidateEnrichmentPayload(email="zhang@example.edu")
        cache_key: tuple[int, int, int, str] | None = None

        async def cancel_task_during_enrichment(*_args, **_kwargs):
            nonlocal cache_key
            async with self.session_factory() as session:
                task = await session.get(CrawlCandidateEnrichmentTask, task_id)
                assert task is not None
                job = await session.get(CrawlJob, task.job_id)
                assert job is not None
                cache_key = (
                    id(self.session_factory),
                    job.id,
                    candidate_id,
                    "https://example.edu/zhang.html",
                )
                crawler_runtime_enrichment_worker._PROFILE_TEXT_CACHE.put(cache_key, "张三")
                task.status = CrawlCandidateEnrichmentTaskStatus.CANCELED.value
                task.worker_id = None
                job.status = CrawlJobStatus.CANCELED.value
                await session.commit()
            return payload, None, None

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(side_effect=cancel_task_during_enrichment),
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 0)
        assert cache_key is not None
        self.assertNotIn(cache_key, crawler_runtime_enrichment_worker._PROFILE_TEXT_CACHE)

    async def test_enrichment_worker_clears_cache_if_task_is_canceled_before_final_commit(
        self,
    ) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html"
        )
        payload = CandidateEnrichmentPayload(email="zhang@example.edu")
        cache_key: tuple[int, int, int, str] | None = None

        async def cache_profile_text(*_args, **_kwargs):
            nonlocal cache_key
            async with self.session_factory() as session:
                task = await session.get(CrawlCandidateEnrichmentTask, task_id)
                assert task is not None
                cache_key = (
                    id(self.session_factory),
                    task.job_id,
                    candidate_id,
                    "https://example.edu/zhang.html",
                )
            assert cache_key is not None
            crawler_runtime_enrichment_worker._PROFILE_TEXT_CACHE.put(cache_key, "张三")
            return payload, {"input_tokens": 1, "output_tokens": 1, "cached_tokens": 0}, None

        async def cancel_before_final_commit(*_args, **_kwargs):
            async with self.session_factory() as session:
                task = await session.get(CrawlCandidateEnrichmentTask, task_id)
                assert task is not None
                job = await session.get(CrawlJob, task.job_id)
                assert job is not None
                task.status = CrawlCandidateEnrichmentTaskStatus.CANCELED.value
                task.worker_id = None
                job.status = CrawlJobStatus.CANCELED.value
                await session.commit()

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
                new=AsyncMock(side_effect=cache_profile_text),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.record_crawler_token_usage",
                new=AsyncMock(side_effect=cancel_before_final_commit),
            ),
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 0)
        assert cache_key is not None
        self.assertNotIn(cache_key, crawler_runtime_enrichment_worker._PROFILE_TEXT_CACHE)

    async def test_enrichment_worker_clears_cache_if_task_disappears_after_fetch(
        self,
    ) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html"
        )
        cache_key: tuple[int, int, int, str] | None = None

        async def delete_task_after_fetch(*_args, **_kwargs):
            nonlocal cache_key
            async with self.session_factory() as session:
                task = await session.get(CrawlCandidateEnrichmentTask, task_id)
                assert task is not None
                cache_key = (
                    id(self.session_factory),
                    task.job_id,
                    candidate_id,
                    "https://example.edu/zhang.html",
                )
                assert cache_key is not None
                crawler_runtime_enrichment_worker._PROFILE_TEXT_CACHE.put(cache_key, "张三")
                await session.delete(task)
                await session.commit()
            raise ValueError("task disappeared")

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(side_effect=delete_task_after_fetch),
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        assert cache_key is not None
        self.assertNotIn(cache_key, crawler_runtime_enrichment_worker._PROFILE_TEXT_CACHE)

    async def test_enrichment_worker_does_not_write_after_lease_expires(self) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html"
        )
        expired = datetime.now(UTC) - timedelta(seconds=1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            task.lease_expires_at = expired
            await session.commit()
        payload = CandidateEnrichmentPayload(
            email="zhang@example.edu",
            department="计算机系",
            research_direction="AI",
            recent_papers=[],
            confidence=0.8,
            field_confidence={},
        )

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(return_value=(payload, None, None)),
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory, task_id=task_id, worker_id="w1"
            )

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert candidate is not None and task is not None
        self.assertIsNone(candidate.email)
        self.assertEqual(
            task.status, CrawlCandidateEnrichmentTaskStatus.PROCESSING.value
        )

    async def test_enrichment_worker_failure_sets_retry_backoff(self) -> None:
        _, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(side_effect=ValueError("429 Too Many Requests")),
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory, task_id=task_id, worker_id="w1"
            )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert task is not None
        self.assertEqual(
            task.status, CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value
        )
        self.assertIn("429", task.last_error or "")
        self.assertIsNone(task.worker_id)
        self.assertIsNone(task.claimed_at)
        self.assertIsNotNone(task.lease_expires_at)

    async def test_content_policy_retry_uses_compact_page_input(self) -> None:
        _, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
        compact_flags: list[bool] = []

        async def enrich_with_one_content_policy_failure(*_args, **kwargs):
            compact_flags.append(bool(kwargs.get("prefer_compact_input")))
            if len(compact_flags) == 1:
                raise LLMRuntimeError(
                    '模型接口返回错误 451: {"code":"censorship_blocked"}',
                    status_code=451,
                )
            return CandidateEnrichmentPayload(), None, ""

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(side_effect=enrich_with_one_content_policy_failure),
        ):
            await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )
            async with self.session_factory() as session:
                task = await session.get(CrawlCandidateEnrichmentTask, task_id)
                assert task is not None
                task.status = CrawlCandidateEnrichmentTaskStatus.PROCESSING.value
                task.worker_id = "w1"
                task.lease_expires_at = None
                await session.commit()
            await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(compact_flags, [False, True])

    async def test_ordinary_retry_keeps_full_page_input(self) -> None:
        _, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            task.last_error = "模型请求超时（120 秒）"
            await session.commit()

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(return_value=(CandidateEnrichmentPayload(), None, "")),
        ) as enrichment_mock:
            await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertFalse(enrichment_mock.await_args.kwargs["prefer_compact_input"])

    def test_compact_enrichment_text_keeps_fields_and_drops_unrelated_biography(
        self,
    ) -> None:
        candidate = CrawlCandidate(name="邱澎生", profile_url="https://example.edu/qiu")
        page_text = "\n".join(
            (
                "邱澎生",
                "职称",
                "特聘教授",
                "联系方式",
                "pengshan1963@sjtu.edu.cn",
                "研究专长：明清制度经济史、明清法制史、明清城市史",
                "个人经历",
                "研究专长：",
                "这是一段与补全字段无关且可能触发服务商内容策略的长篇经历。",
            )
        )

        compact = crawler_runtime_enrichment_worker._compact_enrichment_page_text(
            candidate,
            page_text,
        )

        self.assertIn("邱澎生", compact)
        self.assertIn("特聘教授", compact)
        self.assertIn("pengshan1963@sjtu.edu.cn", compact)
        self.assertIn("明清制度经济史", compact)
        self.assertNotIn("长篇经历", compact)

    async def test_existing_embedded_profile_url_is_repaired_before_enrichment(
        self,
    ) -> None:
        malformed_url = (
            "https://webplus.zuel.edu.cn/_web/_customize/folder/react/"
            "http://xagx.zuel.edu.cn/2021/1110/c3560a282079/page.htm"
        )
        candidate_id, task_id = await self._seed_task(profile_url=malformed_url)

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(return_value=(CandidateEnrichmentPayload(), None, "")),
        ) as enrichment_mock:
            await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
        assert candidate is not None
        self.assertEqual(
            candidate.profile_url,
            "http://xagx.zuel.edu.cn/2021/1110/c3560a282079/page.htm",
        )
        self.assertEqual(
            enrichment_mock.await_args.kwargs["candidate_id"], candidate_id
        )

    async def test_llm_retry_reuses_profile_text_after_fetch_succeeds(self) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html"
        )
        fetched_texts: list[str] = []

        async def fake_fetch(_ctx, _profile_url):
            fetched_texts.append("fetched")
            return "张三 邮箱 zhang@example.edu"

        async def fail_llm(*_args, **_kwargs):
            raise ValueError("LLM 401")

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.fetch_profile_text",
                new=AsyncMock(side_effect=fake_fetch),
            ) as fetch_mock,
            patch(
                "app.modules.crawler.runtime.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(
                    return_value=LLMRuntimeAdaptation("chat_completions", None)
                ),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_profile_with_llm_with_usage",
                new=AsyncMock(side_effect=fail_llm),
            ),
        ):
            processed_first = await run_crawler_enrichment_worker_once(
                self.session_factory, task_id=task_id, worker_id="w1"
            )
            async with self.session_factory() as session:
                task = await session.get(CrawlCandidateEnrichmentTask, task_id)
                assert task is not None
                task.status = CrawlCandidateEnrichmentTaskStatus.PROCESSING.value
                task.worker_id = "w1"
                task.lease_expires_at = None
                await session.commit()
            processed_second = await run_crawler_enrichment_worker_once(
                self.session_factory, task_id=task_id, worker_id="w1"
            )

        self.assertEqual(processed_first, 1)
        self.assertEqual(processed_second, 1)
        fetch_mock.assert_awaited_once()
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert candidate is not None and task is not None
        self.assertIsNone(candidate.email)
        self.assertEqual(
            task.status, CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value
        )
        self.assertEqual(len(crawler_runtime_enrichment_worker._PROFILE_TEXT_CACHE), 1)

    async def test_llm_retry_reuses_empty_profile_text_after_fetch_succeeds(
        self,
    ) -> None:
        _, task_id = await self._seed_task(profile_url="https://example.edu/empty.html")

        async def fail_llm(*_args, **_kwargs):
            raise ValueError("LLM empty")

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.fetch_profile_text",
                new=AsyncMock(return_value=""),
            ) as fetch_mock,
            patch(
                "app.modules.crawler.runtime.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(
                    return_value=LLMRuntimeAdaptation("chat_completions", None)
                ),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_profile_with_llm_with_usage",
                new=AsyncMock(side_effect=fail_llm),
            ),
        ):
            processed_first = await run_crawler_enrichment_worker_once(
                self.session_factory, task_id=task_id, worker_id="w1"
            )
            async with self.session_factory() as session:
                task = await session.get(CrawlCandidateEnrichmentTask, task_id)
                assert task is not None
                task.status = CrawlCandidateEnrichmentTaskStatus.PROCESSING.value
                task.worker_id = "w1"
                task.lease_expires_at = None
                await session.commit()
            processed_second = await run_crawler_enrichment_worker_once(
                self.session_factory, task_id=task_id, worker_id="w1"
            )

        self.assertEqual(processed_first, 1)
        self.assertEqual(processed_second, 1)
        fetch_mock.assert_awaited_once()
        self.assertEqual(len(crawler_runtime_enrichment_worker._PROFILE_TEXT_CACHE), 1)

    async def test_successful_task_clears_cached_profile_text(self) -> None:
        _, task_id = await self._seed_task(
            profile_url="https://example.edu/success.html"
        )
        payload = CandidateEnrichmentPayload(email="zhang@example.edu")

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.fetch_profile_text",
                new=AsyncMock(return_value="张三 邮箱 zhang@example.edu"),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(
                    return_value=LLMRuntimeAdaptation("chat_completions", None)
                ),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_profile_with_llm_with_usage",
                new=AsyncMock(return_value=(payload, None, "")),
            ),
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        self.assertEqual(len(crawler_runtime_enrichment_worker._PROFILE_TEXT_CACHE), 0)

    async def test_terminal_failure_clears_cached_profile_text(self) -> None:
        _, task_id = await self._seed_task(
            profile_url="https://example.edu/failure.html"
        )
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            task.attempt_count = 4
            task.failure_count = 3
            await session.commit()

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.fetch_profile_text",
                new=AsyncMock(return_value="张三"),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(
                    return_value=LLMRuntimeAdaptation("chat_completions", None)
                ),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_profile_with_llm_with_usage",
                new=AsyncMock(side_effect=ValueError("LLM terminal failure")),
            ),
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert task is not None
        self.assertEqual(
            task.status, CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value
        )
        self.assertEqual(len(crawler_runtime_enrichment_worker._PROFILE_TEXT_CACHE), 0)

    async def test_enrichment_worker_failure_appends_trace_event_with_reason(
        self,
    ) -> None:
        _, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(side_effect=ValueError("LLM 401")),
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory, task_id=task_id, worker_id="w1"
            )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            job = await session.get(CrawlJob, task.job_id)
        assert job is not None
        trace = [item for item in job.agent_trace or [] if isinstance(item, dict)]
        self.assertTrue(trace)
        self.assertEqual(trace[-1]["event_type"], "enrichment")
        self.assertEqual(trace[-1]["message"], "候选导师详情补全失败：张三（1 / 1）")
        self.assertEqual(trace[-1]["raw"]["candidate_id"], 1)
        self.assertEqual(trace[-1]["raw"]["progress_current"], 1)
        self.assertEqual(trace[-1]["raw"]["progress_total"], 1)
        self.assertEqual(trace[-1]["raw"]["status"], "failed")
        self.assertEqual(
            trace[-1]["raw"]["task_status"],
            CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value,
        )
        self.assertIn("LLM 401", trace[-1]["raw"]["error_message"])

    async def test_enrichment_worker_keeps_only_latest_failure_for_candidate(
        self,
    ) -> None:
        _, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html"
        )

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(side_effect=[ValueError("第一次失败"), ValueError("第二次失败")]),
        ):
            await run_crawler_enrichment_worker_once(
                self.session_factory, task_id=task_id, worker_id="w1"
            )
            async with self.session_factory() as session:
                task = await session.get(CrawlCandidateEnrichmentTask, task_id)
                assert task is not None
                task.status = CrawlCandidateEnrichmentTaskStatus.PROCESSING.value
                task.worker_id = "w1"
                task.claimed_at = None
                task.lease_expires_at = None
                await session.commit()
            await run_crawler_enrichment_worker_once(
                self.session_factory, task_id=task_id, worker_id="w1"
            )

        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            job = await session.get(CrawlJob, task.job_id)
        assert job is not None
        failure_events = [
            item
            for item in job.agent_trace or []
            if isinstance(item, dict)
            and item.get("event_type") == "enrichment"
            and isinstance(item.get("raw"), dict)
            and item["raw"].get("status") == "failed"
        ]
        self.assertEqual(len(failure_events), 1)
        self.assertEqual(
            failure_events[0]["message"],
            "候选导师详情补全失败：张三（1 / 1）",
        )
        self.assertEqual(failure_events[0]["raw"]["error_message"], "第二次失败")

    async def test_enrichment_success_clears_previous_failure_state_and_trace(
        self,
    ) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/zhang.html"
        )
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            task.last_error = "Connection error."
            job = await session.get(CrawlJob, task.job_id)
            assert job is not None
            job.agent_trace = [
                {
                    "event_type": "enrichment",
                    "message": "候选导师详情补全失败：张三",
                    "created_at": "2026-07-09T00:00:00+00:00",
                    "raw": {
                        "candidate_id": candidate_id,
                        "task_id": task_id,
                        "status": "failed",
                        "task_status": CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value,
                        "error_message": "Connection error.",
                    },
                }
            ]
            await session.commit()
        payload = CandidateEnrichmentPayload(
            email="zhang@example.edu",
            title="教授",
            department="计算机系",
            research_direction="AI",
            recent_papers=[],
        )

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(return_value=(payload, None, None)),
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory, task_id=task_id, worker_id="w1"
            )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            job = await session.get(CrawlJob, task.job_id)
        assert job is not None
        self.assertIsNone(task.last_error)
        messages = [
            item.get("message")
            for item in job.agent_trace or []
            if isinstance(item, dict)
        ]
        self.assertNotIn("候选导师详情补全失败：张三", messages)
        self.assertIn("候选导师详情补全成功：张三（1 / 1）", messages)

    async def test_enrichment_worker_writes_runtime_debug_jsonl(self) -> None:
        _, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
        payload = CandidateEnrichmentPayload(
            email="zhang@example.edu",
            department="计算机系",
            research_direction="AI",
            recent_papers=[],
            confidence=0.8,
            field_confidence={},
        )
        usage = {
            "input_tokens": 90,
            "output_tokens": 10,
            "cached_tokens": 70,
            "total_tokens": 100,
        }

        raw_model_text = "模型原始补全输出"
        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
                new=AsyncMock(return_value=(payload, usage, raw_model_text)),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.append_crawler_worker_debug_event"
            ) as debug_mock,
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory, task_id=task_id, worker_id="w1"
            )

        self.assertEqual(processed, 1)
        events = [call.kwargs["event_name"] for call in debug_mock.call_args_list]
        self.assertIn("llm_response", events)
        self.assertIn("enrichment_completed", events)
        llm_call = next(
            call
            for call in debug_mock.call_args_list
            if call.kwargs["event_name"] == "llm_response"
        )
        self.assertEqual(llm_call.kwargs["worker_kind"], "enrichment")
        self.assertEqual(llm_call.kwargs["work_item_id"], task_id)
        self.assertEqual(
            llm_call.kwargs["payload"]["raw_payload"], payload.model_dump()
        )
        self.assertEqual(llm_call.kwargs["payload"]["raw_model_text"], raw_model_text)
        self.assertEqual(llm_call.kwargs["payload"]["token_usage"], usage)

    async def test_enrichment_adapter_passes_runtime_adaptation_to_structured_request(
        self,
    ) -> None:
        candidate_id, _ = await self._seed_task(
            profile_url="https://example.edu/zhang.html"
        )
        adaptation = LLMRuntimeAdaptation("responses", {"enable_thinking": False})

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.fetch_profile_text",
                new=AsyncMock(return_value="张三 邮箱 zhang@example.edu"),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(return_value=adaptation),
            ) as adaptation_mock,
            patch(
                "app.modules.crawler.runtime.enrichment_worker.request_crawler_structured_completion",
                new=AsyncMock(),
            ) as invoke_mock,
        ):
            completion = ChatCompletionResult(
                content='{"email":"zhang@example.edu"}',
                usage=ChatCompletionUsage(
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                    cached_tokens=0,
                ),
            )
            wire_payload = CandidateEnrichmentWirePayload(
                email="zhang@example.edu",
                title="",
                department="计算机系",
                research_direction="AI",
                recent_papers=[],
            )
            invoke_mock.return_value = (
                completion,
                wire_payload,
                "json_schema_strict",
            )

            result = await enrich_candidate_once(
                self.session_factory, candidate_id=candidate_id
            )

        self.assertEqual(result.email, "zhang@example.edu")
        adaptation_mock.assert_awaited_once()
        invoke_mock.assert_awaited_once()
        self.assertIs(invoke_mock.await_args.args[0], self.session_factory)
        self.assertIs(invoke_mock.await_args.args[2], adaptation)
        self.assertIs(
            invoke_mock.await_args.kwargs["result_model"],
            CandidateEnrichmentWirePayload,
        )

    async def test_enrichment_worker_records_llm_token_usage(self) -> None:
        _, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
        payload = CandidateEnrichmentPayload(
            email="zhang@example.edu",
            department="计算机系",
            research_direction="AI",
            recent_papers=[],
            confidence=0.8,
            field_confidence={},
        )
        usage = {
            "input_tokens": 90,
            "output_tokens": 10,
            "cached_tokens": 70,
            "total_tokens": 100,
        }

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(return_value=(payload, usage, None)),
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory, task_id=task_id, worker_id="w1"
            )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            record = await session.scalar(select(CrawlWorkerTokenUsage))
        assert record is not None
        self.assertEqual(record.worker_kind, "enrichment")
        self.assertEqual(record.work_item_id, str(task_id))
        self.assertEqual(record.model_name, "deepseek")
        self.assertEqual(record.input_tokens, 90)
        self.assertEqual(record.output_tokens, 10)
        self.assertEqual(record.cached_tokens, 70)

    async def test_matched_profile_uses_contextual_text_email_selection(self) -> None:
        candidate_id, _ = await self._seed_task(
            profile_url="https://example.edu/zhang.html",
        )
        candidate, profile, ctx = await self._load_enrichment_context(candidate_id)
        primary_completion = self._completion('{"page_relation":"matched","email":""}')
        selection_completion = self._completion('{"email":"zhang@example.edu"}')

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.request_crawler_structured_completion",
                new=AsyncMock(
                    side_effect=[
                        (
                            primary_completion,
                            CandidateEnrichmentWirePayload(
                                page_relation="matched",
                                email="",
                                title="教授",
                                department="计算机系",
                                research_direction="AI",
                                recent_papers=[],
                            ),
                            "json_schema_strict",
                        ),
                        (
                            selection_completion,
                            CandidateEmailSelectionWirePayload(
                                email="zhang@example.edu",
                            ),
                            "json_schema_strict",
                        ),
                    ]
                ),
            ) as request_mock,
            patch(
                "app.modules.crawler.runtime.enrichment_worker.crawl_page_with_browser_fallback",
                new=AsyncMock(),
            ) as crawl_mock,
        ):
            (
                payload,
                usage,
                _raw,
            ) = await crawler_runtime_enrichment_worker.enrich_candidate_profile_with_llm_with_usage(
                ctx,
                profile,
                candidate,
                (
                    "张三教授的邮箱是 zhang@example.edu。"
                    "学院事务邮箱为 office@example.edu。"
                ),
            )

        self.assertEqual(payload.email, "zhang@example.edu")
        self.assertEqual(request_mock.await_count, 2)
        crawl_mock.assert_not_awaited()
        assert usage is not None
        self.assertEqual(usage["input_tokens"], 2)
        self.assertIn("学院公共邮箱", request_mock.await_args_list[1].kwargs["prompt"])
        self.assertIn(
            '{"email":"zhang@example.edu"}',
            request_mock.await_args_list[1].kwargs["prompt"],
        )

    async def test_ocr_runs_only_after_matched_primary_and_text_fallback_miss(
        self,
    ) -> None:
        candidate_id, _ = await self._seed_task(
            profile_url="https://example.edu/zhang.html",
        )
        candidate, profile, ctx = await self._load_enrichment_context(candidate_id)
        snapshot = PageSnapshot(
            url="https://example.edu/zhang.html",
            text="张三 教授",
            html='<p>邮箱 <img src="email.gif"></p>',
            fetch_method="http",
            status="succeeded",
        )

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.request_crawler_structured_completion",
                new=AsyncMock(
                    side_effect=[
                        (
                            self._completion("primary"),
                            CandidateEnrichmentWirePayload(
                                page_relation="matched",
                                email="",
                                title="教授",
                                department="",
                                research_direction="",
                                recent_papers=[],
                            ),
                            "json_schema_strict",
                        ),
                        (
                            self._completion("ocr selection"),
                            CandidateEmailSelectionWirePayload(
                                email="zhang@example.edu",
                            ),
                            "json_schema_strict",
                        ),
                    ]
                ),
            ),
            patch(
                "app.modules.crawler.runtime.enrichment_worker.crawl_page_with_browser_fallback",
                new=AsyncMock(return_value=snapshot),
            ) as crawl_mock,
            patch(
                "app.modules.crawler.runtime.enrichment_worker.extract_ocr_email_evidence",
                new=AsyncMock(
                    return_value=(
                        EmailEvidence(
                            email="zhang@example.edu",
                            context="邮箱 zhang@example.edu",
                            source_url="https://example.edu/email.gif",
                            source_kind="ocr_image",
                        ),
                    )
                ),
            ) as ocr_mock,
        ):
            (
                payload,
                _usage,
                _raw,
            ) = await crawler_runtime_enrichment_worker.enrich_candidate_profile_with_llm_with_usage(
                ctx,
                profile,
                candidate,
                "张三 教授",
                page_snapshot=snapshot,
            )

        self.assertEqual(payload.email, "zhang@example.edu")
        crawl_mock.assert_not_awaited()
        ocr_mock.assert_awaited_once_with(ctx, snapshot)

    async def test_subpage_fallback_is_one_hop_and_uses_only_selected_link(
        self,
    ) -> None:
        candidate_id, _ = await self._seed_task(
            profile_url="https://example.edu/zhang/",
        )
        candidate, profile, ctx = await self._load_enrichment_context(candidate_id)
        parent = PageSnapshot(
            url="https://example.edu/zhang/",
            text="张三 教授",
            html='<main>张三 <a href="details">查看更多</a></main>',
            fetch_method="http",
            status="succeeded",
        )
        child = PageSnapshot(
            url="https://example.edu/zhang/details",
            text="张三 联系方式 zhang@example.edu",
            html='<a href="deeper">更深页面</a>',
            fetch_method="http",
            status="succeeded",
        )

        with (
            patch(
                "app.modules.crawler.runtime.enrichment_worker.request_crawler_structured_completion",
                new=AsyncMock(
                    side_effect=[
                        (
                            self._completion("primary"),
                            CandidateEnrichmentWirePayload(
                                page_relation="matched",
                                email="",
                                title="教授",
                                department="",
                                research_direction="",
                                recent_papers=[],
                            ),
                            "json_schema_strict",
                        ),
                        (
                            self._completion("link selection"),
                            ProfileLinkSelectionWirePayload(link_ids=[1]),
                            "json_schema_strict",
                        ),
                        (
                            self._completion("email selection"),
                            CandidateEmailSelectionWirePayload(
                                email="zhang@example.edu",
                            ),
                            "json_schema_strict",
                        ),
                    ]
                ),
            ) as request_mock,
            patch(
                "app.modules.crawler.runtime.enrichment_worker.crawl_page_with_browser_fallback",
                new=AsyncMock(return_value=child),
            ) as crawl_mock,
            patch(
                "app.modules.crawler.runtime.enrichment_worker.extract_ocr_email_evidence",
                new=AsyncMock(return_value=()),
            ) as ocr_mock,
        ):
            (
                payload,
                _usage,
                _raw,
            ) = await crawler_runtime_enrichment_worker.enrich_candidate_profile_with_llm_with_usage(
                ctx,
                profile,
                candidate,
                "张三 教授",
                page_snapshot=parent,
            )

        self.assertEqual(payload.email, "zhang@example.edu")
        self.assertEqual(request_mock.await_count, 3)
        self.assertEqual(crawl_mock.await_count, 1)
        self.assertEqual(ocr_mock.await_count, 1)
        self.assertEqual(crawl_mock.await_args_list[0].args[1], child.url)
        self.assertIn(
            '{"link_ids":[2]}',
            request_mock.await_args_list[1].kwargs["prompt"],
        )

    async def test_profile_child_snapshot_is_reused_within_crawl_run(self) -> None:
        candidate_id, _ = await self._seed_task(
            profile_url="https://example.edu/zhang/",
        )
        _candidate, _profile, ctx = await self._load_enrichment_context(candidate_id)
        child = PageSnapshot(
            url="https://example.edu/common-contact",
            text="公共联系页面",
            html="<main>公共联系页面</main>",
            fetch_method="http",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.crawl_page_with_browser_fallback",
            new=AsyncMock(return_value=child),
        ) as crawl_mock:
            first = await crawler_runtime_enrichment_worker._fetch_profile_child_snapshot(
                ctx,
                child.url,
            )
            second = await crawler_runtime_enrichment_worker._fetch_profile_child_snapshot(
                ctx,
                child.url,
            )

        self.assertEqual(first.text, child.text)
        self.assertEqual(second.text, child.text)
        self.assertIsNot(first, second)
        crawl_mock.assert_awaited_once()

    async def test_concurrent_profile_child_fetches_share_one_request(self) -> None:
        candidate_id, _ = await self._seed_task(
            profile_url="https://example.edu/zhang/",
        )
        _candidate, _profile, ctx = await self._load_enrichment_context(candidate_id)
        child = PageSnapshot(
            url="https://example.edu/common-contact",
            text="公共联系页面",
            html="<main>公共联系页面</main>",
            fetch_method="http",
            status="succeeded",
        )
        fetch_started = asyncio.Event()
        allow_fetch_to_finish = asyncio.Event()

        async def fetch_once(*_args, **_kwargs):
            fetch_started.set()
            await allow_fetch_to_finish.wait()
            return child

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.crawl_page_with_browser_fallback",
            new=AsyncMock(side_effect=fetch_once),
        ) as crawl_mock:
            first_task = asyncio.create_task(
                crawler_runtime_enrichment_worker._fetch_profile_child_snapshot(
                    ctx,
                    child.url,
                )
            )
            await fetch_started.wait()
            second_task = asyncio.create_task(
                crawler_runtime_enrichment_worker._fetch_profile_child_snapshot(
                    ctx,
                    child.url,
                )
            )
            await asyncio.sleep(0)
            self.assertEqual(crawl_mock.await_count, 1)
            allow_fetch_to_finish.set()
            first, second = await asyncio.gather(first_task, second_task)

        self.assertEqual(first.text, child.text)
        self.assertEqual(second.text, child.text)
        self.assertIsNot(first, second)
        crawl_mock.assert_awaited_once()

    async def test_known_listing_page_is_not_offered_as_profile_child(self) -> None:
        candidate_id, _ = await self._seed_task(
            profile_url="https://example.edu/zhang/",
        )
        _candidate, _profile, ctx = await self._load_enrichment_context(candidate_id)
        ctx.known_listing_urls.add("https://example.edu/faculty")

        self.assertTrue(
            crawler_runtime_enrichment_worker._is_known_listing_url(
                ctx,
                "https://example.edu/faculty",
            )
        )
        self.assertFalse(
            crawler_runtime_enrichment_worker._is_known_listing_url(
                ctx,
                "https://example.edu/zhang/contact",
            )
        )

    async def test_clear_mismatch_removes_wrong_profile_and_rejects_contactless_candidate(
        self,
    ) -> None:
        candidate_id, task_id = await self._seed_task(
            profile_url="https://example.edu/college-home",
        )
        payload = CandidateEnrichmentPayload(
            page_relation="mismatched",
            email="office@example.edu",
            title="教授",
        )

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(return_value=(payload, None, "")),
        ):
            processed = await run_crawler_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert candidate is not None and task is not None
        self.assertIsNone(candidate.profile_url)
        self.assertIsNone(candidate.email)
        self.assertEqual(candidate.review_status, "rejected")
        self.assertEqual(
            candidate.evidence["profile_url_removed_reason"],
            "confirmed_profile_page_mismatch",
        )
        self.assertEqual(task.enriched_fields, ["profile_url"])

    async def test_empty_visible_body_never_falls_back_to_hidden_html(self) -> None:
        candidate_id, _ = await self._seed_task(
            profile_url="https://example.edu/zhang.html",
        )
        _candidate, _profile, ctx = await self._load_enrichment_context(candidate_id)
        snapshot = PageSnapshot(
            url="https://example.edu/zhang.html",
            text="",
            html="<!-- zhang@example.edu -->",
            fetch_method="http",
            status="succeeded",
        )

        with patch(
            "app.modules.crawler.runtime.enrichment_worker.crawl_page_with_browser_fallback",
            new=AsyncMock(return_value=snapshot),
        ):
            with self.assertRaisesRegex(ValueError, "未提供可见正文"):
                await crawler_runtime_enrichment_worker.fetch_profile_text(
                    ctx,
                    snapshot.url,
                )

    def test_exact_name_downgrades_only_a_model_mismatch_to_uncertain(self) -> None:
        self.assertEqual(
            crawler_runtime_enrichment_worker._guard_page_relation(
                "mismatched",
                candidate_name="张三",
                page_text="张三 教授 个人简介",
            ),
            "uncertain",
        )
        self.assertEqual(
            crawler_runtime_enrichment_worker._guard_page_relation(
                "mismatched",
                candidate_name="张三",
                page_text="示例大学学院首页",
            ),
            "mismatched",
        )

    async def _load_enrichment_context(
        self,
        candidate_id: int,
    ) -> tuple[
        CrawlCandidate, LLMProfile, crawler_runtime_enrichment_worker.CrawlToolContext
    ]:
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            assert candidate is not None
            job = await session.get(CrawlJob, candidate.job_id)
            assert job is not None and job.llm_profile_id is not None
            profile = await session.get(LLMProfile, job.llm_profile_id)
            assert profile is not None
        ctx = crawler_runtime_enrichment_worker.CrawlToolContext(
            job_id=job.id,
            start_url=job.start_url,
            university=job.university,
            school=job.school,
            session_factory=self.session_factory,
            llm_adaptation=LLMRuntimeAdaptation("chat_completions", None),
        )
        return candidate, profile, ctx

    @staticmethod
    def _completion(content: str) -> ChatCompletionResult:
        return ChatCompletionResult(
            content=content,
            usage=ChatCompletionUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                cached_tokens=0,
            ),
        )

    async def _seed_task(self, *, profile_url: str | None) -> tuple[int, int]:
        async with self.session_factory() as session:
            profile = LLMProfile(
                name="默认",
                provider="openai",
                api_base_url="https://api.example.com/v1",
                api_key="sk-test",
                model_name="deepseek",
                is_default=True,
            )
            session.add(profile)
            await session.flush()
            job = CrawlJob(
                university="示例大学",
                school="计算机学院",
                start_url="https://example.edu/faculty",
                status=CrawlJobStatus.RUNNING.value,
                llm_profile_id=profile.id,
                active_candidate_enrichment_operation_id="test-operation",
            )
            session.add(job)
            await session.flush()
            candidate = CrawlCandidate(
                job_id=job.id, name="张三", profile_url=profile_url
            )
            session.add(candidate)
            await session.flush()
            task = CrawlCandidateEnrichmentTask(
                job_id=job.id,
                candidate_id=candidate.id,
                enrichment_operation_id="test-operation",
                status=CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
                worker_id="w1",
            )
            session.add(task)
            await session.commit()
            return candidate.id, task.id

    async def _add_source_chunk(self, *, candidate_id: int, content: str) -> None:
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            assert candidate is not None
            job = await session.get(CrawlJob, candidate.job_id)
            assert job is not None
            session.add(
                CrawlPageChunk(
                    job_id=job.id,
                    page_id=None,
                    source_url=job.start_url,
                    page_fingerprint="profile-policy-page",
                    chunk_id="profile-policy-chunk",
                    chunk_index=0,
                    chunk_hash="profile-policy-hash",
                    content=content,
                    status=CrawlPageChunkStatus.COMPLETED.value,
                )
            )
            await session.commit()


if __name__ == "__main__":
    unittest.main()
