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
from app.modules.crawler.pages.tools import CandidateEnrichmentPayload
from app.modules.crawler.llm.structured_output import CandidateEnrichmentWirePayload
from app.modules.llm.runtime import (
    ChatCompletionResult,
    ChatCompletionUsage,
    LLMRuntimeAdaptation,
)
from app.modules.crawler.v2 import enrichment_worker as crawler_v2_enrichment_worker
from app.modules.crawler.v2.enrichment_worker import enrich_candidate_once, run_crawler_v2_enrichment_worker_once


class CrawlerV2EnrichmentWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_enrichment_adaptation_cache_is_committed_before_context_session_closes(self) -> None:
        from app.modules.llm.adaptation.endpoint import (
            get_cached_endpoint_kind,
            record_endpoint_adaptation,
        )

        candidate_id, _ = await self._seed_task(profile_url="https://example.edu/zhang.html")
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
            patch("app.modules.crawler.v2.enrichment_worker.ensure_llm_runtime_adaptation", new=AsyncMock(side_effect=fake_ensure)),
            patch("app.modules.crawler.v2.enrichment_worker.get_or_fetch_profile_text", new=AsyncMock(return_value="张三")),
            patch(
                "app.modules.crawler.v2.enrichment_worker.enrich_candidate_profile_with_llm_with_usage",
                new=AsyncMock(return_value=(CandidateEnrichmentPayload(), None, "")),
            ),
        ):
            await crawler_v2_enrichment_worker.enrich_candidate_once_with_usage(
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
        crawler_v2_enrichment_worker._PROFILE_TEXT_CACHE.clear()
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

    async def test_enrichment_updates_missing_fields(self) -> None:
        candidate_id, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
        papers = [f"P{index}" for index in range(1, 13)]
        payload = CandidateEnrichmentPayload.model_construct(
            email="zhang@example.edu",
            title="教授",
            department="计算机系",
            research_direction="AI",
            recent_papers=papers,
        )

        with patch("app.modules.crawler.v2.enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(return_value=(payload, None))):
            processed = await run_crawler_v2_enrichment_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert candidate is not None and task is not None
        self.assertEqual(candidate.email, "zhang@example.edu")
        self.assertEqual(candidate.title, "教授")
        self.assertEqual(candidate.department, "计算机系")
        self.assertEqual(candidate.recent_papers, papers[:8])
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value)
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
        self.assertEqual(trace[-1]["message"], "候选导师详情补全成功：张三")
        self.assertEqual(trace[-1]["raw"]["candidate_id"], candidate_id)
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
            "app.modules.crawler.v2.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(return_value=(payload, None)),
        ):
            processed = await run_crawler_v2_enrichment_worker_once(
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
            "app.modules.crawler.v2.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(return_value=(CandidateEnrichmentPayload(), None)),
        ):
            processed = await run_crawler_v2_enrichment_worker_once(
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
        self.assertEqual(trace[-1]["message"], "候选导师详情未发现新信息：张三")
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

        ctx = crawler_v2_enrichment_worker.CrawlToolContext(
            job_id=job_id,
            start_url="https://example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=self.session_factory,
        )
        with patch(
            "app.modules.crawler.v2.enrichment_worker.fetch_profile_text",
            new=AsyncMock(return_value="张三 zhang@example.edu"),
        ) as fetch_mock:
            text = await crawler_v2_enrichment_worker.get_or_fetch_profile_text(
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
        cache_key = (id(self.session_factory), task.job_id, candidate_id, "https://example.edu/old")
        crawler_v2_enrichment_worker._PROFILE_TEXT_CACHE.put(cache_key, "old profile")

        processed = await run_crawler_v2_enrichment_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert task is not None
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.SKIPPED.value)
        self.assertNotIn(cache_key, crawler_v2_enrichment_worker._PROFILE_TEXT_CACHE)


    async def test_enrichment_adapter_fetches_profile_and_invokes_existing_llm(self) -> None:
        candidate_id, _ = await self._seed_task(profile_url="https://example.edu/zhang.html")
        payload = CandidateEnrichmentPayload(email="zhang@example.edu", department="计算机系", research_direction="AI", recent_papers=[], confidence=0.8, field_confidence={})

        with patch("app.modules.crawler.v2.enrichment_worker.fetch_profile_text", new=AsyncMock(return_value="张三 邮箱 zhang@example.edu")) as fetch_mock, patch("app.modules.crawler.v2.enrichment_worker.ensure_llm_runtime_adaptation", new=AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None))), patch("app.modules.crawler.v2.enrichment_worker.enrich_candidate_profile_with_llm_with_usage", new=AsyncMock(return_value=(payload, None))) as enrich_mock:
            result = await enrich_candidate_once(self.session_factory, candidate_id=candidate_id)

        self.assertEqual(result.email, "zhang@example.edu")
        fetch_mock.assert_awaited_once()
        self.assertEqual(fetch_mock.await_args.args[0].start_url, "https://example.edu/faculty")
        enrich_mock.assert_awaited_once()

    async def test_explicit_cross_domain_profile_uses_itself_as_single_page_root(self) -> None:
        profile_url = "https://people.example.net/zhang"
        candidate_id, _ = await self._seed_task(profile_url=profile_url)
        await self._add_source_chunk(
            candidate_id=candidate_id,
            content=f"[张三]({profile_url}) 教授",
        )
        payload = CandidateEnrichmentPayload(email="zhang@people.example.net")

        with (
            patch(
                "app.modules.crawler.v2.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None)),
            ),
            patch(
                "app.modules.crawler.v2.enrichment_worker.get_or_fetch_profile_text",
                new=AsyncMock(return_value="张三"),
            ) as fetch_mock,
            patch(
                "app.modules.crawler.v2.enrichment_worker.enrich_candidate_profile_with_llm_with_usage",
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

    async def test_encoded_cross_domain_profile_uses_itself_as_single_page_root(self) -> None:
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
                "app.modules.crawler.v2.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None)),
            ),
            patch(
                "app.modules.crawler.v2.enrichment_worker.get_or_fetch_profile_text",
                new=AsyncMock(return_value="关威"),
            ) as fetch_mock,
            patch(
                "app.modules.crawler.v2.enrichment_worker.enrich_candidate_profile_with_llm_with_usage",
                new=AsyncMock(return_value=(payload, None, "")),
            ),
        ):
            result = await enrich_candidate_once(
                self.session_factory,
                candidate_id=candidate_id,
            )

        self.assertEqual(result.email, "guan_wei@tju.edu.cn")
        self.assertEqual(fetch_mock.await_args.args[0].start_url, profile_url)

    async def test_unproven_cross_domain_profile_fails_terminal_without_retry(self) -> None:
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
                "app.modules.crawler.v2.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(),
            ) as adaptation_mock,
            patch(
                "app.modules.crawler.v2.enrichment_worker.fetch_profile_text",
                new=AsyncMock(),
            ) as fetch_mock,
        ):
            processed = await run_crawler_v2_enrichment_worker_once(
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
            "app.modules.crawler.v2.enrichment_worker.fetch_profile_text",
            new=AsyncMock(),
        ) as fetch_mock:
            processed = await run_crawler_v2_enrichment_worker_once(
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

    async def test_information_enrichment_can_use_profiles_from_multiple_domains(self) -> None:
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
                "app.modules.crawler.v2.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None)),
            ),
            patch(
                "app.modules.crawler.v2.enrichment_worker.get_or_fetch_profile_text",
                new=AsyncMock(return_value="张三"),
            ) as fetch_mock,
            patch(
                "app.modules.crawler.v2.enrichment_worker.enrich_candidate_profile_with_llm_with_usage",
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
        candidate_id, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
        payload = CandidateEnrichmentPayload(email="zhang@example.edu", department="计算机系", research_direction="AI", recent_papers=[], confidence=0.8, field_confidence={})
        cache_key: tuple[int, int, int, str] | None = None

        async def pause_job_during_enrichment(*_args, **_kwargs):
            nonlocal cache_key
            async with self.session_factory() as session:
                task = await session.get(CrawlCandidateEnrichmentTask, task_id)
                assert task is not None
                job = await session.get(CrawlJob, task.job_id)
                assert job is not None
                cache_key = (id(self.session_factory), job.id, candidate_id, "https://example.edu/zhang.html")
                crawler_v2_enrichment_worker._PROFILE_TEXT_CACHE.put(cache_key, "张三")
                job.status = CrawlJobStatus.PAUSED.value
                task.status = CrawlCandidateEnrichmentTaskStatus.PENDING.value
                task.worker_id = None
                await session.commit()
            return payload, {"input_tokens": 10, "output_tokens": 5, "cached_tokens": 0}

        with patch("app.modules.crawler.v2.enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(side_effect=pause_job_during_enrichment)):
            processed = await run_crawler_v2_enrichment_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

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
        self.assertIn(cache_key, crawler_v2_enrichment_worker._PROFILE_TEXT_CACHE)

    async def test_enrichment_worker_clears_cache_if_task_is_canceled_during_fetch(self) -> None:
        candidate_id, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
        payload = CandidateEnrichmentPayload(email="zhang@example.edu")
        cache_key: tuple[int, int, int, str] | None = None

        async def cancel_task_during_enrichment(*_args, **_kwargs):
            nonlocal cache_key
            async with self.session_factory() as session:
                task = await session.get(CrawlCandidateEnrichmentTask, task_id)
                assert task is not None
                job = await session.get(CrawlJob, task.job_id)
                assert job is not None
                cache_key = (id(self.session_factory), job.id, candidate_id, "https://example.edu/zhang.html")
                crawler_v2_enrichment_worker._PROFILE_TEXT_CACHE.put(cache_key, "张三")
                task.status = CrawlCandidateEnrichmentTaskStatus.CANCELED.value
                task.worker_id = None
                job.status = CrawlJobStatus.CANCELED.value
                await session.commit()
            return payload, None

        with patch(
            "app.modules.crawler.v2.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(side_effect=cancel_task_during_enrichment),
        ):
            processed = await run_crawler_v2_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 0)
        assert cache_key is not None
        self.assertNotIn(cache_key, crawler_v2_enrichment_worker._PROFILE_TEXT_CACHE)

    async def test_enrichment_worker_clears_cache_if_task_is_canceled_before_final_commit(self) -> None:
        candidate_id, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
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
            crawler_v2_enrichment_worker._PROFILE_TEXT_CACHE.put(cache_key, "张三")
            return payload, {"input_tokens": 1, "output_tokens": 1, "cached_tokens": 0}

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
                "app.modules.crawler.v2.enrichment_worker.enrich_candidate_once_with_usage",
                new=AsyncMock(side_effect=cache_profile_text),
            ),
            patch(
                "app.modules.crawler.v2.enrichment_worker.record_crawler_v2_token_usage",
                new=AsyncMock(side_effect=cancel_before_final_commit),
            ),
        ):
            processed = await run_crawler_v2_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 0)
        assert cache_key is not None
        self.assertNotIn(cache_key, crawler_v2_enrichment_worker._PROFILE_TEXT_CACHE)

    async def test_enrichment_worker_clears_cache_if_task_disappears_after_fetch(self) -> None:
        candidate_id, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
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
                crawler_v2_enrichment_worker._PROFILE_TEXT_CACHE.put(cache_key, "张三")
                await session.delete(task)
                await session.commit()
            raise ValueError("task disappeared")

        with patch(
            "app.modules.crawler.v2.enrichment_worker.enrich_candidate_once_with_usage",
            new=AsyncMock(side_effect=delete_task_after_fetch),
        ):
            processed = await run_crawler_v2_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        assert cache_key is not None
        self.assertNotIn(cache_key, crawler_v2_enrichment_worker._PROFILE_TEXT_CACHE)

    async def test_enrichment_worker_does_not_write_after_lease_expires(self) -> None:
        candidate_id, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
        expired = datetime.now(UTC) - timedelta(seconds=1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            task.lease_expires_at = expired
            await session.commit()
        payload = CandidateEnrichmentPayload(email="zhang@example.edu", department="计算机系", research_direction="AI", recent_papers=[], confidence=0.8, field_confidence={})

        with patch("app.modules.crawler.v2.enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(return_value=(payload, None))):
            processed = await run_crawler_v2_enrichment_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 0)
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert candidate is not None and task is not None
        self.assertIsNone(candidate.email)
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.PROCESSING.value)

    async def test_enrichment_worker_failure_sets_retry_backoff(self) -> None:
        _, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")

        with patch("app.modules.crawler.v2.enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(side_effect=ValueError("429 Too Many Requests"))):
            processed = await run_crawler_v2_enrichment_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert task is not None
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value)
        self.assertIn("429", task.last_error or "")
        self.assertIsNone(task.worker_id)
        self.assertIsNone(task.claimed_at)
        self.assertIsNotNone(task.lease_expires_at)

    async def test_llm_retry_reuses_profile_text_after_fetch_succeeds(self) -> None:
        candidate_id, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
        fetched_texts: list[str] = []

        async def fake_fetch(_ctx, _profile_url):
            fetched_texts.append("fetched")
            return "张三 邮箱 zhang@example.edu"

        async def fail_llm(*_args, **_kwargs):
            raise ValueError("LLM 401")

        with patch("app.modules.crawler.v2.enrichment_worker.fetch_profile_text", new=AsyncMock(side_effect=fake_fetch)) as fetch_mock, \
            patch("app.modules.crawler.v2.enrichment_worker.ensure_llm_runtime_adaptation", new=AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None))), \
            patch("app.modules.crawler.v2.enrichment_worker.enrich_candidate_profile_with_llm_with_usage", new=AsyncMock(side_effect=fail_llm)):
            processed_first = await run_crawler_v2_enrichment_worker_once(self.session_factory, task_id=task_id, worker_id="w1")
            async with self.session_factory() as session:
                task = await session.get(CrawlCandidateEnrichmentTask, task_id)
                assert task is not None
                task.status = CrawlCandidateEnrichmentTaskStatus.PROCESSING.value
                task.worker_id = "w1"
                task.lease_expires_at = None
                await session.commit()
            processed_second = await run_crawler_v2_enrichment_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed_first, 1)
        self.assertEqual(processed_second, 1)
        fetch_mock.assert_awaited_once()
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert candidate is not None and task is not None
        self.assertIsNone(candidate.email)
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value)
        self.assertEqual(len(crawler_v2_enrichment_worker._PROFILE_TEXT_CACHE), 1)

    async def test_llm_retry_reuses_empty_profile_text_after_fetch_succeeds(self) -> None:
        _, task_id = await self._seed_task(profile_url="https://example.edu/empty.html")

        async def fail_llm(*_args, **_kwargs):
            raise ValueError("LLM empty")

        with patch("app.modules.crawler.v2.enrichment_worker.fetch_profile_text", new=AsyncMock(return_value="")) as fetch_mock, \
            patch("app.modules.crawler.v2.enrichment_worker.ensure_llm_runtime_adaptation", new=AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None))), \
            patch("app.modules.crawler.v2.enrichment_worker.enrich_candidate_profile_with_llm_with_usage", new=AsyncMock(side_effect=fail_llm)):
            processed_first = await run_crawler_v2_enrichment_worker_once(self.session_factory, task_id=task_id, worker_id="w1")
            async with self.session_factory() as session:
                task = await session.get(CrawlCandidateEnrichmentTask, task_id)
                assert task is not None
                task.status = CrawlCandidateEnrichmentTaskStatus.PROCESSING.value
                task.worker_id = "w1"
                task.lease_expires_at = None
                await session.commit()
            processed_second = await run_crawler_v2_enrichment_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed_first, 1)
        self.assertEqual(processed_second, 1)
        fetch_mock.assert_awaited_once()
        self.assertEqual(len(crawler_v2_enrichment_worker._PROFILE_TEXT_CACHE), 1)

    async def test_successful_task_clears_cached_profile_text(self) -> None:
        _, task_id = await self._seed_task(profile_url="https://example.edu/success.html")
        payload = CandidateEnrichmentPayload(email="zhang@example.edu")

        with (
            patch(
                "app.modules.crawler.v2.enrichment_worker.fetch_profile_text",
                new=AsyncMock(return_value="张三 邮箱 zhang@example.edu"),
            ),
            patch(
                "app.modules.crawler.v2.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None)),
            ),
            patch(
                "app.modules.crawler.v2.enrichment_worker.enrich_candidate_profile_with_llm_with_usage",
                new=AsyncMock(return_value=(payload, None, "")),
            ),
        ):
            processed = await run_crawler_v2_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        self.assertEqual(len(crawler_v2_enrichment_worker._PROFILE_TEXT_CACHE), 0)

    async def test_terminal_failure_clears_cached_profile_text(self) -> None:
        _, task_id = await self._seed_task(profile_url="https://example.edu/failure.html")
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            task.attempt_count = 4
            task.failure_count = 3
            await session.commit()

        with (
            patch(
                "app.modules.crawler.v2.enrichment_worker.fetch_profile_text",
                new=AsyncMock(return_value="张三"),
            ),
            patch(
                "app.modules.crawler.v2.enrichment_worker.ensure_llm_runtime_adaptation",
                new=AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None)),
            ),
            patch(
                "app.modules.crawler.v2.enrichment_worker.enrich_candidate_profile_with_llm_with_usage",
                new=AsyncMock(side_effect=ValueError("LLM terminal failure")),
            ),
        ):
            processed = await run_crawler_v2_enrichment_worker_once(
                self.session_factory,
                task_id=task_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert task is not None
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value)
        self.assertEqual(len(crawler_v2_enrichment_worker._PROFILE_TEXT_CACHE), 0)

    async def test_enrichment_worker_failure_appends_trace_event_with_reason(self) -> None:
        _, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")

        with patch("app.modules.crawler.v2.enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(side_effect=ValueError("LLM 401"))):
            processed = await run_crawler_v2_enrichment_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            job = await session.get(CrawlJob, task.job_id)
        assert job is not None
        trace = [item for item in job.agent_trace or [] if isinstance(item, dict)]
        self.assertTrue(trace)
        self.assertEqual(trace[-1]["event_type"], "enrichment")
        self.assertEqual(trace[-1]["message"], "候选导师详情补全失败：张三")
        self.assertEqual(trace[-1]["raw"]["candidate_id"], 1)
        self.assertEqual(trace[-1]["raw"]["status"], "failed")
        self.assertEqual(trace[-1]["raw"]["task_status"], CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value)
        self.assertIn("LLM 401", trace[-1]["raw"]["error_message"])

    async def test_enrichment_success_clears_previous_failure_state_and_trace(self) -> None:
        candidate_id, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
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
        payload = CandidateEnrichmentPayload(email="zhang@example.edu", title="教授", department="计算机系", research_direction="AI", recent_papers=[])

        with patch("app.modules.crawler.v2.enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(return_value=(payload, None))):
            processed = await run_crawler_v2_enrichment_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            job = await session.get(CrawlJob, task.job_id)
        assert job is not None
        self.assertIsNone(task.last_error)
        messages = [item.get("message") for item in job.agent_trace or [] if isinstance(item, dict)]
        self.assertNotIn("候选导师详情补全失败：张三", messages)
        self.assertIn("候选导师详情补全成功：张三", messages)

    async def test_enrichment_worker_writes_v2_debug_jsonl(self) -> None:
        _, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
        payload = CandidateEnrichmentPayload(email="zhang@example.edu", department="计算机系", research_direction="AI", recent_papers=[], confidence=0.8, field_confidence={})
        usage = {"input_tokens": 90, "output_tokens": 10, "cached_tokens": 70, "total_tokens": 100}

        raw_model_text = "模型原始补全输出"
        with patch("app.modules.crawler.v2.enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(return_value=(payload, usage, raw_model_text))), patch("app.modules.crawler.v2.enrichment_worker.append_crawler_v2_debug_event") as debug_mock:
            processed = await run_crawler_v2_enrichment_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        events = [call.kwargs["event_name"] for call in debug_mock.call_args_list]
        self.assertIn("llm_response", events)
        self.assertIn("enrichment_completed", events)
        llm_call = next(call for call in debug_mock.call_args_list if call.kwargs["event_name"] == "llm_response")
        self.assertEqual(llm_call.kwargs["worker_kind"], "enrichment")
        self.assertEqual(llm_call.kwargs["work_item_id"], task_id)
        self.assertEqual(llm_call.kwargs["payload"]["raw_payload"], payload.model_dump())
        self.assertEqual(llm_call.kwargs["payload"]["raw_model_text"], raw_model_text)
        self.assertEqual(llm_call.kwargs["payload"]["token_usage"], usage)

    async def test_enrichment_adapter_passes_runtime_adaptation_to_structured_request(self) -> None:
        candidate_id, _ = await self._seed_task(profile_url="https://example.edu/zhang.html")
        payload = CandidateEnrichmentPayload(email="zhang@example.edu", department="计算机系", research_direction="AI", recent_papers=[], confidence=0.8, field_confidence={})
        adaptation = LLMRuntimeAdaptation("responses", {"enable_thinking": False})

        with patch("app.modules.crawler.v2.enrichment_worker.fetch_profile_text", new=AsyncMock(return_value="张三 邮箱 zhang@example.edu")), \
            patch("app.modules.crawler.v2.enrichment_worker.ensure_llm_runtime_adaptation", new=AsyncMock(return_value=adaptation)) as adaptation_mock, \
            patch(
                "app.modules.crawler.v2.enrichment_worker.request_crawler_structured_completion",
                new=AsyncMock(),
            ) as invoke_mock:
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

            result = await enrich_candidate_once(self.session_factory, candidate_id=candidate_id)

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
        payload = CandidateEnrichmentPayload(email="zhang@example.edu", department="计算机系", research_direction="AI", recent_papers=[], confidence=0.8, field_confidence={})
        usage = {"input_tokens": 90, "output_tokens": 10, "cached_tokens": 70, "total_tokens": 100}

        with patch("app.modules.crawler.v2.enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(return_value=(payload, usage))):
            processed = await run_crawler_v2_enrichment_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

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

    async def _seed_task(self, *, profile_url: str | None) -> tuple[int, int]:
        async with self.session_factory() as session:
            profile = LLMProfile(name="默认", provider="openai", api_base_url="https://api.example.com/v1", api_key="sk-test", model_name="deepseek", is_default=True)
            session.add(profile)
            await session.flush()
            job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://example.edu/faculty", status=CrawlJobStatus.RUNNING.value, llm_profile_id=profile.id)
            session.add(job)
            await session.flush()
            candidate = CrawlCandidate(job_id=job.id, name="张三", profile_url=profile_url)
            session.add(candidate)
            await session.flush()
            task = CrawlCandidateEnrichmentTask(job_id=job.id, candidate_id=candidate.id, status=CrawlCandidateEnrichmentTaskStatus.PROCESSING.value, worker_id="w1")
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
