from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, CrawlCandidate, CrawlJob, CrawlJobRun, CrawlJobStatus, CrawlPage, CrawlPageChunk, CrawlPageChunkStatus, LLMProfile
from app.services import crawl_job_runtime
from app.services.crawl_job_runtime import (
    crawl_job_has_pending_work,
    create_chunks_for_successful_page_snapshot,
    _enrich_saved_candidates,
    enrich_selected_crawl_candidates,
    enrich_candidate_profile_with_llm,
    extract_profile_candidate_with_llm,
    recover_interrupted_crawl_jobs,
    resolve_crawl_runtime_concurrency,
    run_queued_crawl_jobs_once,
)
from app.services.crawl_job_runs import create_initial_crawl_job_run
from app.services.crawler_tools import (
    CandidateEnrichmentPayload,
    CrawlJobSaveBudgetExceeded,
    CrawlToolContext,
    PageSnapshot,
    ProfessorCandidatePayload,
    save_candidates,
)


class CrawlJobRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_crawl_job_has_pending_work_tracks_pending_chunks(self) -> None:
        async with self.session_factory() as session:
            job = CrawlJob(
                university="示例大学",
                school="计算机学院",
                start_url="https://cs.example.edu/faculty",
                status=CrawlJobStatus.RUNNING.value,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)

        self.assertFalse(await crawl_job_has_pending_work(self.session_factory, job_id=job.id))

        async with self.session_factory() as session:
            session.add(
                CrawlPageChunk(
                    job_id=job.id,
                    page_id=None,
                    source_url="https://cs.example.edu/faculty",
                    page_fingerprint="page",
                    chunk_id="chunk-1",
                    chunk_index=0,
                    chunk_hash="hash",
                    status=CrawlPageChunkStatus.PENDING.value,
                    content="张三",
                )
            )
            await session.commit()

        self.assertTrue(await crawl_job_has_pending_work(self.session_factory, job_id=job.id))

    async def test_successful_directory_page_snapshot_creates_chunks(self) -> None:
        async with self.session_factory() as session:
            job = CrawlJob(
                university="示例大学",
                school="计算机学院",
                start_url="https://cs.example.edu/faculty",
                status=CrawlJobStatus.RUNNING.value,
            )
            page = CrawlPage(
                job=job,
                url="https://cs.example.edu/faculty",
                fetch_method="http",
                status="succeeded",
            )
            session.add_all([job, page])
            await session.commit()
            await session.refresh(job)
            await session.refresh(page)

        snapshot = PageSnapshot(
            url="https://cs.example.edu/faculty",
            title="师资队伍",
            text="张三\n李四",
            html="<main><p><a href='/zhang.htm'>张三</a></p><p><a href='/li.htm'>李四</a></p></main>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )
        created = await create_chunks_for_successful_page_snapshot(
            self.session_factory,
            job_id=job.id,
            page_id=page.id,
            snapshot=snapshot,
        )

        self.assertGreaterEqual(created, 1)
        async with self.session_factory() as session:
            self.assertGreaterEqual(len(list(await session.scalars(select(CrawlPageChunk)))), 1)

    async def test_run_keeps_job_running_when_chunks_remain_after_candidate_save(self) -> None:
        job_id = await self._create_default_profile_and_job()

        async def fake_run(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            trace_callback=None,
            **kwargs,
        ) -> dict[str, object]:
            _ = llm_profile, trace_callback, kwargs
            async with ctx.session_factory() as session:
                session.add(
                    CrawlCandidate(
                        job_id=ctx.job_id,
                        name="张三",
                        university=ctx.university,
                        school=ctx.school,
                        profile_url="https://example.edu/zhang.htm",
                    )
                )
                session.add(
                    CrawlPageChunk(
                        job_id=ctx.job_id,
                        page_id=None,
                        source_url="https://example.edu/faculty",
                        page_fingerprint="page",
                        chunk_id="chunk-1",
                        chunk_index=0,
                        chunk_hash="hash",
                        status=CrawlPageChunkStatus.PENDING.value,
                        content="李四",
                    )
                )
                await session.commit()
            return {}

        with patch("app.services.crawl_job_runtime.run_faculty_crawler_agent", new=fake_run):
            processed = await run_queued_crawl_jobs_once(self.session_factory)

        self.assertEqual(processed, 1)
        job = await self._get_job(job_id)
        self.assertEqual(job.status, CrawlJobStatus.RUNNING.value)
        current_run = await self._get_current_run(job_id)
        self.assertEqual(current_run.status, CrawlJobStatus.RUNNING.value)

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "crawl_job_runtime.db"
        self.debug_dir = Path(self.temp_dir.name) / "crawler-debug"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        # Stub thinking adaptation so legacy tests don't hit the network.
        # Behaviour matches a non-thinking model (no extra_body required), preserving
        # pre-task-8 semantics for tests written before the adaptation hook.
        self._thinking_adaptation_patch = patch(
            "app.services.crawl_job_runtime.ensure_thinking_adaptation",
            AsyncMock(return_value=None),
        )
        self._thinking_adaptation_patch.start()

    async def asyncTearDown(self) -> None:
        self._thinking_adaptation_patch.stop()
        await self.engine.dispose()
        os.environ.pop("CRAWLER_DEBUG", None)
        os.environ.pop("CRAWLER_DEBUG_DIR", None)
        from app.core.config import get_settings

        get_settings.cache_clear()
        self.temp_dir.cleanup()

    async def test_resolve_crawl_runtime_concurrency_prefers_database_settings(self) -> None:
        settings = type(
            "SettingsStub",
            (),
            {
                "crawler_profile_enrichment_concurrency": 6,
                "crawler_host_concurrency": 2,
            },
        )()

        resolved = resolve_crawl_runtime_concurrency(settings)

        self.assertEqual(resolved.profile_enrichment_concurrency, 6)
        self.assertEqual(resolved.host_concurrency, 2)

    async def test_run_queued_crawl_job_moves_to_needs_review_when_candidates_saved(self) -> None:
        job_id = await self._create_default_profile_and_job()

        async def fake_run(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            trace_callback=None,
            **kwargs,
        ) -> dict[str, object]:
            _ = llm_profile
            if trace_callback is not None:
                await trace_callback(
                    {
                        "type": "updates",
                        "data": {
                            "model": {
                                "messages": [
                                    "tool_calls=[{'name': 'save_professor_candidates'}]",
                                ]
                            }
                        },
                    }
                )
            await save_candidates(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        email="zhang@example.edu",
                        title="教授",
                    )
                ],
            )
            return {}

        with patch(
            "app.services.crawl_job_runtime.run_faculty_crawler_agent",
            new=fake_run,
        ):
            processed = await run_queued_crawl_jobs_once(self.session_factory)

        self.assertEqual(processed, 1)
        job = await self._get_job(job_id)
        self.assertEqual(job.status, CrawlJobStatus.NEEDS_REVIEW.value)
        self.assertIsNone(job.error_message)
        self.assertEqual(await self._count_candidates(job_id), 1)
        run = await self._get_current_run(job_id)
        self.assertEqual(run.status, CrawlJobStatus.NEEDS_REVIEW.value)
        self.assertIsNotNone(run.finished_at)

    async def test_list_entry_type_runs_each_unique_start_url_once(self) -> None:
        job_id = await self._create_default_profile_and_job(
            start_url="https://example.edu/faculty",
            start_urls=[
                "https://example.edu/faculty",
                "https://example.edu/faculty?page=2",
                "https://example.edu/faculty",
            ],
        )
        calls: list[str] = []

        async def fake_run(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            trace_callback=None,
            **kwargs,
        ) -> dict[str, object]:
            _ = llm_profile, trace_callback
            calls.append(ctx.start_url)
            await save_candidates(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name=f"教师{len(calls)}",
                        email=f"teacher{len(calls)}@example.edu",
                        title="教授",
                        source_url=ctx.start_url,
                    )
                ],
            )
            return {}

        with patch(
            "app.services.crawl_job_runtime.run_faculty_crawler_agent",
            new=fake_run,
        ):
            processed = await run_queued_crawl_jobs_once(self.session_factory)

        self.assertEqual(processed, 1)
        self.assertEqual(
            calls,
            [
                "https://example.edu/faculty",
                "https://example.edu/faculty?page=2",
            ],
        )
        self.assertEqual(await self._count_candidates(job_id), 2)

    async def test_run_queued_crawl_job_accumulates_tokens_on_current_run(self) -> None:
        job_id = await self._create_default_profile_and_job()

        async def fake_run(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            trace_callback=None,
            **kwargs,
        ) -> dict[str, object]:
            _ = llm_profile
            if trace_callback is not None:
                await trace_callback(
                    {
                        "type": "updates",
                        "data": {
                            "model": {
                                "messages": [
                                    "usage_metadata={'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120}",
                                ]
                            }
                        },
                    }
                )
                await trace_callback(
                    {
                        "type": "updates",
                        "data": {
                            "model": {
                                "messages": [
                                    "response_metadata={'token_usage': {'completion_tokens': 12, 'prompt_tokens': 34, 'total_tokens': 46}}",
                                ]
                            }
                        },
                    }
                )
            await save_candidates(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        email="zhang@example.edu",
                        title="教授",
                    )
                ],
            )
            return {}

        with patch("app.services.crawl_job_runtime.run_faculty_crawler_agent", new=fake_run):
            processed = await run_queued_crawl_jobs_once(self.session_factory)

        self.assertEqual(processed, 1)
        run = await self._get_current_run(job_id)
        self.assertEqual(run.input_tokens, 134)
        self.assertEqual(run.output_tokens, 32)
        self.assertEqual(run.total_tokens, 166)

    async def test_worker_does_not_claim_paused_crawl_job(self) -> None:
        job_id = await self._create_default_profile_and_job()
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            job.status = CrawlJobStatus.PAUSED.value
            await session.commit()

        processed = await run_queued_crawl_jobs_once(self.session_factory)

        self.assertEqual(processed, 0)
        job = await self._get_job(job_id)
        self.assertEqual(job.status, CrawlJobStatus.PAUSED.value)

    async def test_run_queued_crawl_jobs_ignores_deleted_job(self) -> None:
        job_id = await self._create_default_profile_and_job()
        await self._mark_job_deleted(job_id)

        processed = await run_queued_crawl_jobs_once(self.session_factory)

        self.assertEqual(processed, 0)
        job = await self._get_job(job_id)
        self.assertEqual(job.status, CrawlJobStatus.QUEUED.value)

    async def test_running_job_without_candidates_is_marked_failed_after_worker_restart(self) -> None:
        job_id = await self._create_default_profile_and_job()
        await self._mark_job_running_after_interrupted_worker(job_id)

        await recover_interrupted_crawl_jobs(self.session_factory)

        job = await self._get_job(job_id)
        self.assertEqual(job.status, CrawlJobStatus.FAILED.value)
        self.assertEqual(job.error_message, crawl_job_runtime.INTERRUPTED_JOB_ERROR)
        run = await self._get_current_run(job_id)
        self.assertEqual(run.status, CrawlJobStatus.FAILED.value)
        self.assertEqual(run.error_message, crawl_job_runtime.INTERRUPTED_JOB_ERROR)
        self.assertIsNotNone(run.finished_at)
        self.assertIsNone(run.active_started_at)

    async def test_running_job_with_candidates_is_moved_to_review_after_worker_restart(self) -> None:
        job_id = await self._create_default_profile_and_job()
        await self._seed_candidates(job_id, count=1, host="example.edu")
        await self._mark_job_running_after_interrupted_worker(job_id)

        await recover_interrupted_crawl_jobs(self.session_factory)

        job = await self._get_job(job_id)
        self.assertEqual(job.status, CrawlJobStatus.NEEDS_REVIEW.value)
        self.assertEqual(await self._count_candidates(job_id), 1)
        run = await self._get_current_run(job_id)
        self.assertEqual(run.status, CrawlJobStatus.NEEDS_REVIEW.value)
        self.assertIsNotNone(run.finished_at)
        self.assertIsNone(run.active_started_at)

    async def test_running_job_paused_by_tool_stays_paused(self) -> None:
        job_id = await self._create_default_profile_and_job()

        async def fake_run(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            trace_callback=None,
            **kwargs,
        ) -> dict[str, object]:
            _ = llm_profile, trace_callback
            async with ctx.session_factory() as session:
                job = await session.get(CrawlJob, ctx.job_id)
                assert job is not None
                job.status = CrawlJobStatus.PAUSED.value
                await session.commit()
            from app.services.crawler_tools import CrawlJobPaused

            raise CrawlJobPaused()

        with patch("app.services.crawl_job_runtime.run_faculty_crawler_agent", new=fake_run):
            processed = await run_queued_crawl_jobs_once(self.session_factory)

        self.assertEqual(processed, 1)
        job = await self._get_job(job_id)
        self.assertEqual(job.status, CrawlJobStatus.PAUSED.value)
        self.assertIsNone(job.error_message)
        run = await self._get_current_run(job_id)
        self.assertEqual(run.status, CrawlJobStatus.PAUSED.value)
        self.assertIsNone(run.active_started_at)

    async def test_enrichment_stops_when_job_is_paused(self) -> None:
        job_id = await self._create_default_profile_and_job(
            start_url="https://example.edu/faculty",
        )
        enrichment_calls: list[str] = []

        async def fake_run(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            trace_callback=None,
            **kwargs,
        ) -> dict[str, object]:
            _ = llm_profile, trace_callback
            await save_candidates(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        email="zhang@example.edu",
                        profile_url="https://example.edu/zhang",
                    )
                ],
            )
            async with ctx.session_factory() as session:
                job = await session.get(CrawlJob, ctx.job_id)
                assert job is not None
                job.status = CrawlJobStatus.PAUSED.value
                await session.commit()
            return {}

        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            _ = ctx, intent
            enrichment_calls.append(url)
            return PageSnapshot(
                url=url,
                title="张三",
                text="院系：计算机学院",
                html="<html></html>",
                links=[],
                fetch_method="http",
                status="succeeded",
            )

        with patch(
            "app.services.crawl_job_runtime.run_faculty_crawler_agent",
            new=fake_run,
        ), patch(
            "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
            new=fake_crawl_page_with_crawl4ai,
        ):
            processed = await run_queued_crawl_jobs_once(self.session_factory)

        self.assertEqual(processed, 1)
        self.assertEqual(enrichment_calls, [])
        job = await self._get_job(job_id)
        self.assertEqual(job.status, CrawlJobStatus.PAUSED.value)

    async def test_discovery_stage_saves_candidates_without_detail_fields(self) -> None:
        job_id = await self._create_default_profile_and_job(
            start_url="https://example.edu/faculty/list",
        )

        async def fake_run(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            trace_callback=None,
            **kwargs,
        ) -> dict[str, object]:
            _ = llm_profile
            if trace_callback is not None:
                await trace_callback(
                    {
                        "type": "updates",
                        "data": {
                            "model": {
                                "messages": [
                                    "tool_calls=[{'name': 'save_professor_candidates'}]",
                                ]
                            }
                        },
                    }
                )
            await save_candidates(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="李四",
                        email="li.si@example.edu",
                        title="副教授",
                    )
                ],
            )
            return {}

        with patch(
            "app.services.crawl_job_runtime.run_faculty_crawler_agent",
            new=fake_run,
        ):
            processed = await run_queued_crawl_jobs_once(self.session_factory)

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            candidate = await session.scalar(
                select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)
            )
            self.assertIsNotNone(candidate)
            assert candidate is not None
            self.assertIsNone(candidate.department)
            self.assertIsNone(candidate.research_direction)
            self.assertEqual(candidate.recent_papers, [])
            self.assertIsNone(candidate.profile_url)

    async def test_profile_entry_type_extracts_single_candidate(self) -> None:
        job_id = await self._create_default_profile_and_job(
            start_url="https://example.edu/faculty/zhang",
            entry_type="profile",
        )
        calls: list[tuple[str, str]] = []

        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            _ = ctx
            calls.append((url, intent))
            return PageSnapshot(
                url="https://example.edu/faculty/zhang",
                title="张三",
                text="张三\n教授\n邮箱：zhang@example.edu\n研究方向：机器学习",
                html="<html></html>",
                links=[],
                fetch_method="http",
                status="succeeded",
            )

        async def fake_extract_profile_candidate_with_llm(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            page_text: str,
        ) -> ProfessorCandidatePayload:
            _ = llm_profile, page_text
            return ProfessorCandidatePayload(
                name="张三",
                email="zhang@example.edu",
                title="教授",
                research_direction="机器学习",
                profile_url=ctx.start_url,
                source_url=ctx.start_url,
                confidence=0.9,
            )

        with patch(
            "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
            new=fake_crawl_page_with_crawl4ai,
        ), patch(
            "app.services.crawl_job_runtime.extract_profile_candidate_with_llm",
            new=fake_extract_profile_candidate_with_llm,
        ):
            processed = await run_queued_crawl_jobs_once(self.session_factory)

        self.assertEqual(processed, 1)
        self.assertEqual(calls, [("https://example.edu/faculty/zhang", "profile")])
        job = await self._get_job(job_id)
        self.assertEqual(job.status, CrawlJobStatus.NEEDS_REVIEW.value)
        async with self.session_factory() as session:
            candidate = await session.scalar(
                select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)
            )
            self.assertIsNotNone(candidate)
            assert candidate is not None
            self.assertEqual(candidate.name, "张三")
            self.assertEqual(candidate.university, "示例大学")
            self.assertEqual(candidate.school, "计算机学院")
            self.assertEqual(candidate.profile_url, "https://example.edu/faculty/zhang")

    async def test_profile_entry_type_records_direct_llm_token_usage(self) -> None:
        job_id = await self._create_default_profile_and_job(
            start_url="https://example.edu/faculty/zhang",
            entry_type="profile",
        )

        class FakeLLMMessage:
            content = json.dumps(
                {
                    "name": "张三",
                    "email": "zhang@example.edu",
                    "title": "教授",
                    "research_direction": "机器学习",
                    "profile_url": "https://example.edu/faculty/zhang",
                    "source_url": "https://example.edu/faculty/zhang",
                    "confidence": 0.9,
                },
                ensure_ascii=False,
            )
            response_metadata = {
                "token_usage": {
                    "prompt_tokens": 31,
                    "completion_tokens": 17,
                    "total_tokens": 48,
                }
            }

        class FakeModel:
            async def ainvoke(self, prompt: str) -> FakeLLMMessage:
                _ = prompt
                return FakeLLMMessage()

        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            _ = ctx, url, intent
            return PageSnapshot(
                url="https://example.edu/faculty/zhang",
                title="张三",
                text="张三\n教授\n邮箱：zhang@example.edu\n研究方向：机器学习",
                html="<html></html>",
                links=[],
                fetch_method="http",
                status="succeeded",
            )

        with patch(
            "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
            new=fake_crawl_page_with_crawl4ai,
        ), patch(
            "app.services.crawl_job_runtime.build_faculty_crawler_model",
            return_value=FakeModel(),
        ):
            processed = await run_queued_crawl_jobs_once(self.session_factory)

        self.assertEqual(processed, 1)
        run = await self._get_current_run(job_id)
        self.assertEqual(run.input_tokens, 31)
        self.assertEqual(run.output_tokens, 17)
        self.assertEqual(run.total_tokens, 48)

    async def test_extract_profile_candidate_with_llm_retries_after_invalid_json_response(self) -> None:
        job_id = await self._create_default_profile_and_job(
            start_url="https://example.edu/faculty/zhang",
            entry_type="profile",
        )
        llm_profile = await self._get_default_llm_profile()
        ctx = CrawlToolContext(
            job_id=job_id,
            start_url="https://example.edu/faculty/zhang",
            university="example university",
            school="computer school",
            session_factory=self.session_factory,
        )

        class FakeModel:
            def __init__(self) -> None:
                self.responses = [
                    '{"name":"zhangsan","confidence":"high"',
                    """```json
                    {
                      "name": "zhangsan",
                      "email": "zhang@example.edu",
                      "confidence": 0.9
                    }
                    ```""",
                ]
                self.prompts: list[str] = []

            async def ainvoke(self, prompt: str) -> str:
                self.prompts.append(prompt)
                return self.responses[len(self.prompts) - 1]

        fake_model = FakeModel()

        with patch(
            "app.services.crawl_job_runtime.build_faculty_crawler_model",
            return_value=fake_model,
        ):
            candidate = await extract_profile_candidate_with_llm(
                ctx,
                llm_profile,
                "zhangsan\nemail: zhang@example.edu",
            )

        self.assertEqual(candidate.name, "zhangsan")
        self.assertEqual(candidate.email, "zhang@example.edu")
        self.assertEqual(candidate.confidence, 0.9)
        self.assertEqual(len(fake_model.prompts), 2)

    async def test_extract_profile_candidate_with_llm_parses_wrapped_json_and_semantic_confidence(self) -> None:
        job_id = await self._create_default_profile_and_job(
            start_url="https://example.edu/faculty/zhang",
            entry_type="profile",
        )
        llm_profile = await self._get_default_llm_profile()
        ctx = CrawlToolContext(
            job_id=job_id,
            start_url="https://example.edu/faculty/zhang",
            university="example university",
            school="computer school",
            session_factory=self.session_factory,
        )

        class FakeModel:
            async def ainvoke(self, prompt: str) -> str:
                _ = prompt
                return """The result is:
```json
{
  "name": "zhangsan",
  "email": "zhang@example.edu",
  "confidence": "high"
}
```"""

        with patch(
            "app.services.crawl_job_runtime.build_faculty_crawler_model",
            return_value=FakeModel(),
        ):
            candidate = await extract_profile_candidate_with_llm(
                ctx,
                llm_profile,
                "zhangsan\nemail: zhang@example.edu",
            )

        self.assertEqual(candidate.name, "zhangsan")
        self.assertEqual(candidate.email, "zhang@example.edu")
        self.assertEqual(candidate.confidence, 0.9)

    async def test_enrich_candidate_profile_with_llm_parses_wrapped_json_response(self) -> None:
        job_id = await self._create_default_profile_and_job()
        llm_profile = await self._get_default_llm_profile()
        ctx = CrawlToolContext(
            job_id=job_id,
            start_url="https://example.edu/faculty",
            university="example university",
            school="computer school",
            session_factory=self.session_factory,
        )
        candidate = CrawlCandidate(
            id=1,
            job_id=job_id,
            name="zhangsan",
            email=None,
            title="professor",
            university="example university",
            school="computer school",
            department=None,
            research_direction=None,
            recent_papers=[],
            profile_url="https://example.edu/faculty/zhang",
            source_url="https://example.edu/faculty/zhang",
            confidence=0.0,
        )

        class FakeModel:
            async def ainvoke(self, prompt: str) -> str:
                _ = prompt
                return """```json
                {
                  "email": "zhang@example.edu",
                  "department": "AI Lab",
                  "recent_papers": ["Paper A"]
                }
                ```"""

        with patch(
            "app.services.crawl_job_runtime.build_faculty_crawler_model",
            return_value=FakeModel(),
        ):
            enrichment = await enrich_candidate_profile_with_llm(
                ctx,
                llm_profile,
                candidate,
                "email: zhang@example.edu\ndepartment: AI Lab",
            )

        self.assertEqual(enrichment.email, "zhang@example.edu")
        self.assertEqual(enrichment.department, "AI Lab")
        self.assertEqual(enrichment.recent_papers, ["Paper A"])

    async def test_profile_entry_type_fails_when_page_fetch_fails(self) -> None:
        job_id = await self._create_default_profile_and_job(
            start_url="https://example.edu/faculty/zhang",
            entry_type="profile",
        )

        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            _ = ctx, url, intent
            return PageSnapshot(
                url="https://example.edu/faculty/zhang",
                title=None,
                text="",
                html="",
                links=[],
                fetch_method="http",
                status="failed",
                error_message="详情页抓取失败",
            )

        with patch(
            "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
            new=fake_crawl_page_with_crawl4ai,
        ):
            await run_queued_crawl_jobs_once(self.session_factory)

        job = await self._get_job(job_id)
        self.assertEqual(job.status, CrawlJobStatus.FAILED.value)
        self.assertEqual(job.error_message, "详情页抓取失败")

    async def test_profile_entry_type_fails_when_name_is_missing(self) -> None:
        job_id = await self._create_default_profile_and_job(
            start_url="https://example.edu/faculty/unknown",
            entry_type="profile",
        )

        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            _ = ctx, url, intent
            return PageSnapshot(
                url="https://example.edu/faculty/unknown",
                title="Profile",
                text="研究方向：机器学习",
                html="<html></html>",
                links=[],
                fetch_method="http",
                status="succeeded",
            )

        async def fake_extract_profile_candidate_with_llm(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            page_text: str,
        ) -> ProfessorCandidatePayload:
            _ = ctx, llm_profile, page_text
            raise ValueError("未能从详情页识别导师信息")

        with patch(
            "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
            new=fake_crawl_page_with_crawl4ai,
        ), patch(
            "app.services.crawl_job_runtime.extract_profile_candidate_with_llm",
            new=fake_extract_profile_candidate_with_llm,
        ):
            await run_queued_crawl_jobs_once(self.session_factory)

        job = await self._get_job(job_id)
        self.assertEqual(job.status, CrawlJobStatus.FAILED.value)
        self.assertEqual(job.error_message, "未能从详情页识别导师信息")

    async def test_run_queued_crawl_job_fails_when_agent_finishes_without_saved_candidates(self) -> None:
        job_id = await self._create_default_profile_and_job()

        async def fake_run(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            trace_callback=None,
            **kwargs,
        ) -> dict[str, object]:
            _ = ctx, llm_profile
            if trace_callback is not None:
                await trace_callback(
                    {
                        "type": "updates",
                        "data": {
                            "model": {
                                "messages": [
                                    "invalid_tool_calls=[{'name': 'save_professor_candidates'}] finish_reason='length'",
                                ]
                            }
                        },
                    }
                )
            return {}

        with patch(
            "app.services.crawl_job_runtime.run_faculty_crawler_agent",
            new=fake_run,
        ):
            processed = await run_queued_crawl_jobs_once(self.session_factory)

        self.assertEqual(processed, 1)
        job = await self._get_job(job_id)
        self.assertEqual(job.status, CrawlJobStatus.FAILED.value)
        self.assertIsNotNone(job.error_message)
        self.assertIn("save_professor_candidates", job.error_message)
        self.assertEqual(await self._count_candidates(job_id), 0)
        run = await self._get_current_run(job_id)
        self.assertEqual(run.status, CrawlJobStatus.FAILED.value)
        self.assertIsNotNone(run.error_message)
        self.assertIsNotNone(run.finished_at)

    async def test_run_queued_crawl_job_fails_when_save_failure_budget_is_exceeded(self) -> None:
        job_id = await self._create_default_profile_and_job()

        async def fake_run(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            trace_callback=None,
            **kwargs,
        ) -> dict[str, object]:
            _ = ctx, llm_profile, trace_callback
            raise CrawlJobSaveBudgetExceeded(
                terminal_reason="同一候选批次连续保存失败 2 次，已停止以避免继续消耗 token",
                failure_fingerprint="abc123def456",
                same_batch_save_failures=2,
                total_save_failures=2,
                latest_failure_summary="张三: name: Field required",
            )

        with patch(
            "app.services.crawl_job_runtime.run_faculty_crawler_agent",
            new=fake_run,
        ):
            processed = await run_queued_crawl_jobs_once(self.session_factory)

        self.assertEqual(processed, 1)
        job = await self._get_job(job_id)
        self.assertEqual(job.status, CrawlJobStatus.FAILED.value)
        self.assertIsNotNone(job.error_message)
        self.assertIn("同一候选批次连续保存失败 2 次", job.error_message)
        self.assertIn("张三: name: Field required", job.error_message)
        run = await self._get_current_run(job_id)
        self.assertEqual(run.status, CrawlJobStatus.FAILED.value)
        self.assertEqual(run.error_message, job.error_message)
        self.assertIsNotNone(run.finished_at)

        trace_events = [item for item in job.agent_trace or [] if isinstance(item, dict)]
        breaker_events = [
            item
            for item in trace_events
            if item.get("event_type") == "save_failure_circuit_breaker"
        ]
        self.assertEqual(len(breaker_events), 1)
        raw_event = breaker_events[0]["raw"]
        self.assertIsInstance(raw_event, dict)
        assert isinstance(raw_event, dict)
        self.assertEqual(raw_event["failure_fingerprint"], "abc123def456")
        self.assertEqual(raw_event["consecutive_same_batch_failures"], 2)
        self.assertEqual(raw_event["total_save_failures"], 2)

    async def test_run_queued_crawl_job_surfaces_latest_page_failure_when_no_candidates_saved(self) -> None:
        job_id = await self._create_default_profile_and_job()

        async def fake_run(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            trace_callback=None,
            **kwargs,
        ) -> dict[str, object]:
            _ = llm_profile, trace_callback
            async with ctx.session_factory() as session:
                session.add(
                    CrawlPage(
                        job_id=ctx.job_id,
                        url=ctx.start_url,
                        fetch_method="browser",
                        page_type="list",
                        status="failed",
                        error_message="Crawl4AI browser fetch failed: NotImplementedError",
                    )
                )
                await session.commit()
            return {}

        with patch(
            "app.services.crawl_job_runtime.run_faculty_crawler_agent",
            new=fake_run,
        ):
            processed = await run_queued_crawl_jobs_once(self.session_factory)

        self.assertEqual(processed, 1)
        job = await self._get_job(job_id)
        self.assertEqual(job.status, CrawlJobStatus.FAILED.value)
        self.assertEqual(
            job.error_message,
            "Crawl4AI browser fetch failed: NotImplementedError",
        )
        self.assertEqual(await self._count_candidates(job_id), 0)

    async def test_run_queued_crawl_job_writes_full_debug_trace_when_enabled(self) -> None:
        os.environ["CRAWLER_DEBUG"] = "1"
        os.environ["CRAWLER_DEBUG_DIR"] = self.debug_dir.as_posix()
        from app.core.config import get_settings

        get_settings.cache_clear()
        job_id = await self._create_default_profile_and_job()
        oversized_payload = "x" * 25000

        async def fake_run(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            trace_callback=None,
            **kwargs,
        ) -> dict[str, object]:
            _ = ctx, llm_profile
            if trace_callback is not None:
                await trace_callback(
                    {
                        "type": "updates",
                        "data": {
                            "model": {
                                "messages": [
                                    {
                                        "role": "assistant",
                                        "content": oversized_payload,
                                    }
                                ],
                                "finish_reason": "tool_calls",
                            }
                        },
                    }
                )
            return {}

        with patch(
            "app.services.crawl_job_runtime.run_faculty_crawler_agent",
            new=fake_run,
        ):
            await run_queued_crawl_jobs_once(self.session_factory)

        debug_file = self.debug_dir / f"crawl-job-{job_id}.jsonl"
        self.assertTrue(debug_file.exists())
        records = [
            json.loads(line)
            for line in debug_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertGreaterEqual(len(records), 1)
        self.assertEqual(records[0]["job_id"], job_id)
        serialized_raw_event = json.dumps(records[0]["raw_event"], ensure_ascii=False)
        self.assertIn(oversized_payload, serialized_raw_event)

    async def test_run_queued_crawl_job_does_not_auto_enrich_profiles_after_discovery(self) -> None:
        job_id = await self._create_default_profile_and_job(
            start_url="https://cai.jxufe.edu.cn/lists/26.html",
        )
        sequence: list[str] = []

        async def fake_run(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            trace_callback=None,
            **kwargs,
        ) -> dict[str, object]:
            _ = llm_profile
            sequence.append("discover")
            if trace_callback is not None:
                await trace_callback(
                    {
                        "type": "updates",
                        "data": {
                            "model": {
                                "messages": [
                                    "tool_calls=[{'name': 'save_professor_candidates'}]",
                                ]
                            }
                        },
                    }
                )
            await save_candidates(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="张三",
                        email="zhang@example.edu",
                        title="教授、博导",
                        profile_url="https://cta.jxufe.edu.cn/home/teacherInfo/detail?uid=1",
                    )
                ],
            )
            return {}

        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            _ = ctx
            sequence.append(f"enrich:{url}:{intent}")
            return PageSnapshot(
                url=url,
                title="张三",
                text="院系：计算机科学系\n研究方向：大语言模型、智能体\n代表论文：Paper A；Paper B",
                html="<html></html>",
                links=[],
                fetch_method="http",
                status="succeeded",
            )

        async def fake_enrich_with_llm(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            candidate: CrawlCandidate,
            page_text: str,
        ) -> CandidateEnrichmentPayload:
            _ = ctx, llm_profile, candidate, page_text
            return CandidateEnrichmentPayload(
                department="计算机科学系",
                research_direction="大语言模型、智能体",
                recent_papers=["Paper A", "Paper B"],
            )

        with patch(
            "app.services.crawl_job_runtime.run_faculty_crawler_agent",
            new=fake_run,
        ), patch(
            "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
            new=fake_crawl_page_with_crawl4ai,
        ), patch(
            "app.services.crawl_job_runtime.enrich_candidate_profile_with_llm",
            new=fake_enrich_with_llm,
        ):
            processed = await run_queued_crawl_jobs_once(self.session_factory)

        self.assertEqual(processed, 1)
        self.assertEqual(sequence, ["discover"])
        job = await self._get_job(job_id)
        self.assertEqual(job.status, CrawlJobStatus.NEEDS_REVIEW.value)
        trace_messages = [
            item.get("message")
            for item in job.agent_trace or []
            if isinstance(item, dict)
        ]
        self.assertNotIn("开始统一补全候选导师详情，共 1 位待补全", trace_messages)
        self.assertNotIn("开始补全候选导师详情：张三", trace_messages)

        async with self.session_factory() as session:
            candidate = await session.scalar(
                select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)
            )
            self.assertIsNotNone(candidate)
            assert candidate is not None
            self.assertIsNone(candidate.research_direction)
            self.assertIsNone(candidate.department)
            self.assertEqual(candidate.recent_papers, [])

    async def test_enrich_saved_candidates_fills_candidate_email_when_missing(self) -> None:
        job_id = await self._create_default_profile_and_job(
            start_url="https://cai.jxufe.edu.cn/lists/26.html",
        )
        ctx = CrawlToolContext(
            job_id=job_id,
            start_url="https://cai.jxufe.edu.cn/lists/26.html",
            university="示例大学",
            school="计算机学院",
            session_factory=self.session_factory,
        )
        await save_candidates(
            ctx,
            [
                ProfessorCandidatePayload(
                    name="王五",
                    email=None,
                    title="教授、博导",
                    department="计算机学院",
                    research_direction="网络安全",
                    recent_papers=["Paper X"],
                    profile_url="https://cta.jxufe.edu.cn/home/teacherInfo/detail?uid=3",
                )
            ],
        )
        llm_profile = await self._get_default_llm_profile()

        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            _ = ctx, intent
            return PageSnapshot(
                url=url,
                title="王五",
                text="邮箱：wang5 (AT) example DOT edu\n院系：计算机学院\n研究方向：教育学",
                html="<html></html>",
                links=[],
                fetch_method="http",
                status="succeeded",
            )

        async def fake_enrich_with_llm(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            candidate: CrawlCandidate,
            page_text: str,
        ) -> CandidateEnrichmentPayload:
            _ = ctx, llm_profile, candidate, page_text
            return CandidateEnrichmentPayload(email="wang5@example.edu")

        with patch(
            "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
            new=fake_crawl_page_with_crawl4ai,
        ), patch(
            "app.services.crawl_job_runtime.enrich_candidate_profile_with_llm",
            new=fake_enrich_with_llm,
        ):
            await _enrich_saved_candidates(
                self.session_factory,
                ctx,
                llm_profile=llm_profile,
            )

        async with self.session_factory() as session:
            candidate = await session.scalar(
                select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)
            )
            self.assertIsNotNone(candidate)
            assert candidate is not None
            self.assertEqual(candidate.email, "wang5@example.edu")

    async def test_enrich_saved_candidates_limits_concurrency(self) -> None:
        job_id = await self._create_default_profile_and_job()
        ctx = self._build_crawl_context(job_id)
        await self._seed_candidates(job_id, count=5, host="example.edu")
        llm_profile = await self._get_default_llm_profile()
        active = 0
        max_active = 0

        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            nonlocal active, max_active
            _ = ctx, intent
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(0.01)
                return PageSnapshot(
                    url=url,
                    title="Profile",
                    text="research direction: embodied ai",
                    html="<html></html>",
                    links=[],
                    fetch_method="http",
                    status="succeeded",
                )
            finally:
                active -= 1

        async def fake_enrich_with_llm(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            candidate: CrawlCandidate,
            page_text: str,
        ) -> CandidateEnrichmentPayload:
            _ = ctx, llm_profile, candidate, page_text
            return CandidateEnrichmentPayload(
                department="AI Lab",
                research_direction="Embodied AI",
                recent_papers=["Paper A"],
            )

        settings_stub = type(
            "SettingsStub",
            (),
            {
                "crawler_profile_enrichment_concurrency": 3,
                "crawler_host_concurrency": 5,
                "crawler_profile_fetch_max_retries": 0,
            },
        )()

        with patch(
            "app.services.crawl_job_runtime.get_settings",
            return_value=settings_stub,
        ), patch(
            "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
            new=fake_crawl_page_with_crawl4ai,
        ), patch(
            "app.services.crawl_job_runtime.enrich_candidate_profile_with_llm",
            new=fake_enrich_with_llm,
        ):
            enriched_count = await _enrich_saved_candidates(
                self.session_factory,
                ctx,
                llm_profile=llm_profile,
            )

        self.assertEqual(enriched_count, 5)
        self.assertLessEqual(max_active, 3)

    async def test_enrich_saved_candidates_limits_same_host_to_one_request(self) -> None:
        job_id = await self._create_default_profile_and_job()
        ctx = self._build_crawl_context(job_id)
        await self._seed_candidates(job_id, count=3, host="same.example.edu")
        llm_profile = await self._get_default_llm_profile()
        host_active = 0
        max_host_active = 0

        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            nonlocal host_active, max_host_active
            _ = ctx, intent
            host_active += 1
            max_host_active = max(max_host_active, host_active)
            try:
                await asyncio.sleep(0.01)
                return PageSnapshot(
                    url=url,
                    title="Profile",
                    text="research direction: systems",
                    html="<html></html>",
                    links=[],
                    fetch_method="http",
                    status="succeeded",
                )
            finally:
                host_active -= 1

        async def fake_enrich_with_llm(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            candidate: CrawlCandidate,
            page_text: str,
        ) -> CandidateEnrichmentPayload:
            _ = ctx, llm_profile, candidate, page_text
            return CandidateEnrichmentPayload(
                department="Systems Lab",
                research_direction="Distributed Systems",
            )

        settings_stub = type(
            "SettingsStub",
            (),
            {
                "crawler_profile_enrichment_concurrency": 3,
                "crawler_host_concurrency": 1,
                "crawler_profile_fetch_max_retries": 0,
            },
        )()

        with patch(
            "app.services.crawl_job_runtime.get_settings",
            return_value=settings_stub,
        ), patch(
            "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
            new=fake_crawl_page_with_crawl4ai,
        ), patch(
            "app.services.crawl_job_runtime.enrich_candidate_profile_with_llm",
            new=fake_enrich_with_llm,
        ):
            enriched_count = await _enrich_saved_candidates(
                self.session_factory,
                ctx,
                llm_profile=llm_profile,
            )

        self.assertEqual(enriched_count, 3)
        self.assertLessEqual(max_host_active, 1)

    async def test_enrich_saved_candidates_updates_run_metrics_for_retry_host_limit_and_failures(self) -> None:
        job_id = await self._create_default_profile_and_job()
        ctx = self._build_crawl_context(job_id)
        await self._seed_candidates(job_id, count=3, host="metric.example.edu")
        llm_profile = await self._get_default_llm_profile()
        attempts: dict[str, int] = {}

        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            _ = ctx, intent
            attempts[url] = attempts.get(url, 0) + 1
            if url.endswith("/0") and attempts[url] <= 2:
                await asyncio.sleep(0.01)
                raise httpx.TimeoutException("timeout")
            if url.endswith("/1"):
                await asyncio.sleep(0.01)
                return PageSnapshot(
                    url=url,
                    title="Profile",
                    text="",
                    html="",
                    links=[],
                    fetch_method="http",
                    status="failed",
                    error_message="detail fetch failed",
                )
            await asyncio.sleep(0.01)
            return PageSnapshot(
                url=url,
                title="Profile",
                text="plain profile page",
                html="<html></html>",
                links=[],
                fetch_method="http",
                status="succeeded",
            )

        async def fake_enrich_with_llm(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            candidate: CrawlCandidate,
            page_text: str,
        ) -> CandidateEnrichmentPayload:
            _ = ctx, llm_profile, page_text
            if (candidate.profile_url or "").endswith("/0"):
                return CandidateEnrichmentPayload(email="teacher0@example.edu")
            return CandidateEnrichmentPayload()

        settings_stub = type(
            "SettingsStub",
            (),
            {
                "crawler_profile_enrichment_concurrency": 3,
                "crawler_host_concurrency": 1,
                "crawler_profile_fetch_max_retries": 2,
            },
        )()

        with patch(
            "app.services.crawl_job_runtime.get_settings",
            return_value=settings_stub,
        ), patch(
            "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
            new=fake_crawl_page_with_crawl4ai,
        ), patch(
            "app.services.crawl_job_runtime.enrich_candidate_profile_with_llm",
            new=fake_enrich_with_llm,
        ):
            enriched_count = await _enrich_saved_candidates(
                self.session_factory,
                ctx,
                llm_profile=llm_profile,
            )

        self.assertEqual(enriched_count, 1)
        run = await self._get_current_run(job_id)
        self.assertEqual(run.retry_count, 2)
        self.assertGreaterEqual(run.host_limited_count, 1)
        self.assertEqual(run.failed_candidate_count, 1)
        self.assertEqual(run.unchanged_candidate_count, 1)

    async def test_run_queued_crawl_job_does_not_overwrite_existing_email(self) -> None:
        job_id = await self._create_default_profile_and_job(
            start_url="https://cai.jxufe.edu.cn/lists/26.html",
        )

        async def fake_run(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            trace_callback=None,
            **kwargs,
        ) -> dict[str, object]:
            _ = llm_profile
            if trace_callback is not None:
                await trace_callback(
                    {
                        "type": "updates",
                        "data": {
                            "model": {
                                "messages": [
                                    "tool_calls=[{'name': 'save_professor_candidates'}]",
                                ]
                            }
                        },
                    }
                )
            await save_candidates(
                ctx,
                [
                    ProfessorCandidatePayload(
                        name="赵六",
                        email="existing@example.edu",
                        title="教授",
                        profile_url="https://cta.jxufe.edu.cn/home/teacherInfo/detail?uid=4",
                    )
                ],
            )
            return {}

        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            _ = ctx, intent
            return PageSnapshot(
                url=url,
                title="赵六",
                text="邮箱：replace@other.example.edu\n院系：法学院\n研究方向：法学",
                html="<html></html>",
                links=[],
                fetch_method="http",
                status="succeeded",
            )

        async def fake_enrich_with_llm(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            candidate: CrawlCandidate,
            page_text: str,
        ) -> CandidateEnrichmentPayload:
            _ = ctx, llm_profile, candidate, page_text
            return CandidateEnrichmentPayload(email="replace@other.example.edu")

        with patch(
            "app.services.crawl_job_runtime.run_faculty_crawler_agent",
            new=fake_run,
        ), patch(
            "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
            new=fake_crawl_page_with_crawl4ai,
        ), patch(
            "app.services.crawl_job_runtime.enrich_candidate_profile_with_llm",
            new=fake_enrich_with_llm,
        ):
            await run_queued_crawl_jobs_once(self.session_factory)

        async with self.session_factory() as session:
            candidate = await session.scalar(
                select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)
            )
            self.assertIsNotNone(candidate)
            assert candidate is not None
            self.assertEqual(candidate.email, "existing@example.edu")

    async def test_enrich_saved_candidates_records_failure_events(self) -> None:
        job_id = await self._create_default_profile_and_job(
            start_url="https://cai.jxufe.edu.cn/lists/26.html",
        )
        ctx = CrawlToolContext(
            job_id=job_id,
            start_url="https://cai.jxufe.edu.cn/lists/26.html",
            university="示例大学",
            school="计算机学院",
            session_factory=self.session_factory,
        )
        await save_candidates(
            ctx,
            [
                ProfessorCandidatePayload(
                    name="张三",
                    email="zhang@example.edu",
                    title="教授、博导",
                    profile_url="https://cta.jxufe.edu.cn/home/teacherInfo/detail?uid=1",
                )
            ],
        )
        llm_profile = await self._get_default_llm_profile()
        trace_events: list[dict[str, object]] = []

        async def trace_callback(event: dict[str, object]) -> None:
            trace_events.append(event)

        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            _ = ctx, url, intent
            return PageSnapshot(
                url="https://cta.jxufe.edu.cn/home/teacherInfo/detail?uid=1",
                title="张三",
                text="",
                html="",
                links=[],
                fetch_method="http",
                status="failed",
                error_message="详情页抓取失败",
            )

        with patch(
            "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
            new=fake_crawl_page_with_crawl4ai,
        ):
            enriched_count = await _enrich_saved_candidates(
                self.session_factory,
                ctx,
                llm_profile=llm_profile,
                trace_callback=trace_callback,
            )

        self.assertEqual(enriched_count, 0)
        trace_messages = [
            item.get("message")
            for item in trace_events
            if isinstance(item, dict)
        ]
        self.assertIn("开始统一补全候选导师详情，共 1 位待补全", trace_messages)
        self.assertIn("候选导师详情补全失败：张三", trace_messages)
        self.assertIn("候选导师详情补全完成：成功 0 位，未变化 0 位，失败 1 位", trace_messages)

    async def test_enrich_saved_candidates_does_not_fall_back_to_text_rules_when_llm_returns_empty(self) -> None:
        job_id = await self._create_default_profile_and_job(
            start_url="https://cai.jxufe.edu.cn/lists/26.html",
        )
        ctx = CrawlToolContext(
            job_id=job_id,
            start_url="https://cai.jxufe.edu.cn/lists/26.html",
            university="示例大学",
            school="计算机学院",
            session_factory=self.session_factory,
        )
        await save_candidates(
            ctx,
            [
                ProfessorCandidatePayload(
                    name="王五",
                    email="wang.wu@example.edu",
                    title="教授、博导",
                    profile_url="https://cta.jxufe.edu.cn/home/teacherInfo/detail?uid=2",
                )
            ],
        )
        llm_profile = await self._get_default_llm_profile()

        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            _ = ctx, intent
            return PageSnapshot(
                url=url,
                title="王五",
                text="院系：人工智能学院\n研究方向：具身智能\n代表论文：Paper X；Paper Y",
                html="<html></html>",
                links=[],
                fetch_method="http",
                status="succeeded",
            )

        async def fake_enrich_with_llm(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            candidate: CrawlCandidate,
            page_text: str,
        ) -> CandidateEnrichmentPayload:
            _ = ctx, llm_profile, candidate, page_text
            return CandidateEnrichmentPayload()

        with patch(
            "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
            new=fake_crawl_page_with_crawl4ai,
        ), patch(
            "app.services.crawl_job_runtime.enrich_candidate_profile_with_llm",
            new=fake_enrich_with_llm,
        ):
            enriched_count = await _enrich_saved_candidates(
                self.session_factory,
                ctx,
                llm_profile=llm_profile,
            )

        self.assertEqual(enriched_count, 0)
        async with self.session_factory() as session:
            candidate = await session.scalar(
                select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)
            )
            self.assertIsNotNone(candidate)
            assert candidate is not None
            self.assertIsNone(candidate.department)
            self.assertIsNone(candidate.research_direction)
            self.assertEqual(candidate.recent_papers, [])

    async def test_enrich_selected_crawl_candidates_only_updates_selected_ids(self) -> None:
        job_id = await self._create_default_profile_and_job()
        await self._seed_candidates(job_id, count=2, host="example.edu")
        llm_profile = await self._get_default_llm_profile()

        async with self.session_factory() as session:
            candidates = list(
                (
                    await session.scalars(
                        select(CrawlCandidate)
                        .where(CrawlCandidate.job_id == job_id)
                        .order_by(CrawlCandidate.id.asc())
                    )
                )
            )
            selected_id = candidates[0].id
            unselected_id = candidates[1].id

        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            _ = ctx, intent
            return PageSnapshot(
                url=url,
                title="Teacher",
                text="邮箱：selected@example.edu\n院系：计算机学院",
                html="<html></html>",
                links=[],
                fetch_method="http",
                status="succeeded",
            )

        async def fake_enrich_with_llm(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            candidate: CrawlCandidate,
            page_text: str,
        ) -> CandidateEnrichmentPayload:
            _ = ctx, llm_profile, candidate, page_text
            return CandidateEnrichmentPayload(email="selected@example.edu")

        with patch(
            "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
            new=fake_crawl_page_with_crawl4ai,
        ), patch(
            "app.services.crawl_job_runtime.enrich_candidate_profile_with_llm",
            new=fake_enrich_with_llm,
        ):
            result = await enrich_selected_crawl_candidates(
                self.session_factory,
                job_id=job_id,
                candidate_ids=[selected_id],
                llm_profile=llm_profile,
            )

        self.assertEqual(result.enriched_count, 1)
        self.assertEqual(result.selected_count, 1)

        async with self.session_factory() as session:
            selected = await session.get(CrawlCandidate, selected_id)
            unselected = await session.get(CrawlCandidate, unselected_id)
            assert selected is not None
            assert unselected is not None
            self.assertEqual(selected.email, "selected@example.edu")
            self.assertIsNone(unselected.email)

    async def test_enrich_selected_crawl_candidates_enriches_complete_candidates_when_user_selected(self) -> None:
        job_id = await self._create_default_profile_and_job()
        await self._seed_candidates(job_id, count=1, host="example.edu")
        llm_profile = await self._get_default_llm_profile()

        async with self.session_factory() as session:
            candidate = await session.scalar(
                select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)
            )
            assert candidate is not None
            candidate.email = "teacher@example.edu"
            candidate.department = "计算机学院"
            candidate.research_direction = "机器学习"
            candidate.recent_papers = ["Paper A"]
            selected_id = candidate.id
            await session.commit()

        crawled_urls: list[str] = []
        enriched_candidate_ids: list[int] = []

        async def fake_crawl_page_with_crawl4ai(
            ctx: CrawlToolContext,
            url: str,
            *,
            intent: str = "generic",
        ) -> PageSnapshot:
            _ = ctx, intent
            crawled_urls.append(url)
            return PageSnapshot(
                url=url,
                title="Teacher",
                text="邮箱：teacher@example.edu\n院系：计算机学院\n研究方向：机器学习",
                html="<html></html>",
                links=[],
                fetch_method="http",
                status="succeeded",
            )

        async def fake_enrich_with_llm(
            ctx: CrawlToolContext,
            llm_profile: LLMProfile,
            candidate: CrawlCandidate,
            page_text: str,
        ) -> CandidateEnrichmentPayload:
            _ = ctx, llm_profile, candidate, page_text
            enriched_candidate_ids.append(candidate.id)
            return CandidateEnrichmentPayload(research_direction="强化学习")

        with patch(
            "app.services.crawl_job_runtime.crawl_page_with_crawl4ai",
            new=fake_crawl_page_with_crawl4ai,
        ), patch(
            "app.services.crawl_job_runtime.enrich_candidate_profile_with_llm",
            new=fake_enrich_with_llm,
        ):
            result = await enrich_selected_crawl_candidates(
                self.session_factory,
                job_id=job_id,
                candidate_ids=[selected_id],
                llm_profile=llm_profile,
            )

        self.assertEqual(result.selected_count, 1)
        self.assertEqual(result.enriched_count, 0)
        self.assertEqual(result.unchanged_count, 1)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(crawled_urls, ["https://example.edu/teacher/0"])
        self.assertEqual(enriched_candidate_ids, [selected_id])
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, selected_id)
            assert candidate is not None
            self.assertEqual(candidate.research_direction, "机器学习")

    async def test_enrichment_worker_pause_exception_does_not_hang(self) -> None:
        job_id = await self._create_default_profile_and_job()
        await self._seed_candidates(job_id, count=1, host="example.edu")
        llm_profile = await self._get_default_llm_profile()

        async with self.session_factory() as session:
            candidate = await session.scalar(
                select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)
            )
            assert candidate is not None
            candidate_id = candidate.id

        async def fake_enrich_candidate_work_item(
            *args: object,
            **kwargs: object,
        ) -> None:
            _ = args, kwargs
            raise crawl_job_runtime.CrawlJobPaused()

        with patch(
            "app.services.crawl_job_runtime._enrich_candidate_work_item",
            new=fake_enrich_candidate_work_item,
        ):
            result = await asyncio.wait_for(
                enrich_selected_crawl_candidates(
                    self.session_factory,
                    job_id=job_id,
                    candidate_ids=[candidate_id],
                    llm_profile=llm_profile,
                ),
                timeout=1.0,
            )

        self.assertEqual(result.selected_count, 1)
        self.assertEqual(result.enriched_count, 0)
        self.assertEqual(result.unchanged_count, 0)
        self.assertEqual(result.failed_count, 0)

    async def test_enrichment_worker_unexpected_exception_is_counted_as_failure(self) -> None:
        job_id = await self._create_default_profile_and_job()
        await self._seed_candidates(job_id, count=1, host="example.edu")
        llm_profile = await self._get_default_llm_profile()

        async with self.session_factory() as session:
            candidate = await session.scalar(
                select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)
            )
            assert candidate is not None
            candidate_id = candidate.id

        async def fake_enrich_candidate_work_item(
            *args: object,
            **kwargs: object,
        ) -> None:
            _ = args, kwargs
            raise RuntimeError("boom")

        with patch(
            "app.services.crawl_job_runtime._enrich_candidate_work_item",
            new=fake_enrich_candidate_work_item,
        ):
            result = await asyncio.wait_for(
                enrich_selected_crawl_candidates(
                    self.session_factory,
                    job_id=job_id,
                    candidate_ids=[candidate_id],
                    llm_profile=llm_profile,
                ),
                timeout=1.0,
            )

        self.assertEqual(result.selected_count, 1)
        self.assertEqual(result.enriched_count, 0)
        self.assertEqual(result.unchanged_count, 0)
        self.assertEqual(result.failed_count, 1)

    async def _create_default_profile_and_job(
        self,
        *,
        start_url: str = "https://example.edu/faculty",
        start_urls: list[str] | None = None,
        entry_type: str = "list",
    ) -> int:
        async with self.session_factory() as session:
            profile = LLMProfile(
                name="default",
                provider="openai",
                api_key="test-key",
                model_name="test-model",
                is_default=True,
            )
            job = CrawlJob(
                university="示例大学",
                school="计算机学院",
                start_url=start_url,
                start_urls=start_urls or [start_url],
                entry_type=entry_type,
                status=CrawlJobStatus.QUEUED.value,
                progress_current=0,
                progress_total=0,
            )
            session.add(profile)
            session.add(job)
            await session.flush()
            await create_initial_crawl_job_run(session, job)
            await session.commit()
            await session.refresh(job)
            return job.id

    async def _get_default_llm_profile(self) -> LLMProfile:
        async with self.session_factory() as session:
            profile = await session.scalar(
                select(LLMProfile)
                .where(LLMProfile.is_default.is_(True))
                .limit(1)
            )
            if profile is None:
                raise AssertionError("default LLM profile not found")
            return profile

    async def _get_current_run(self, job_id: int) -> CrawlJobRun:
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            if job is None or job.current_run_id is None:
                raise AssertionError(f"crawl job {job_id} current run not found")
            run = await session.get(CrawlJobRun, job.current_run_id)
            if run is None:
                raise AssertionError(f"crawl job run {job.current_run_id} not found")
            return run

    async def _get_job(self, job_id: int) -> CrawlJob:
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            if job is None:
                raise AssertionError(f"crawl job {job_id} not found")
            return job

    async def _mark_job_deleted(self, job_id: int) -> None:
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            if job is None:
                raise AssertionError(f"crawl job {job_id} not found")
            job.deleted_at = datetime.now(UTC)
            await session.commit()

    async def _mark_job_running_after_interrupted_worker(self, job_id: int) -> None:
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            if job is None:
                raise AssertionError(f"crawl job {job_id} not found")
            run = await session.get(CrawlJobRun, job.current_run_id)
            if run is None:
                raise AssertionError(f"crawl job run {job.current_run_id} not found")
            now = datetime.now(UTC)
            job.status = CrawlJobStatus.RUNNING.value
            job.updated_at = now
            run.status = CrawlJobStatus.RUNNING.value
            run.started_at = now
            run.active_started_at = now
            run.updated_at = now
            await session.commit()

    def _build_crawl_context(self, job_id: int) -> CrawlToolContext:
        return CrawlToolContext(
            job_id=job_id,
            start_url="https://example.edu/faculty",
            university="example university",
            school="computer school",
            session_factory=self.session_factory,
        )

    async def _count_candidates(self, job_id: int) -> int:
        async with self.session_factory() as session:
            rows = await session.scalars(
                select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)
            )
            return len(list(rows))

    async def _seed_candidates(self, job_id: int, *, count: int, host: str) -> None:
        async with self.session_factory() as session:
            for index in range(count):
                session.add(
                    CrawlCandidate(
                        job_id=job_id,
                        name=f"Teacher {index}",
                        email=None,
                        title="Professor",
                        university="example university",
                        school="computer school",
                        department=None,
                        research_direction=None,
                        recent_papers=[],
                        profile_url=f"https://{host}/teacher/{index}",
                        source_url=f"https://{host}/faculty",
                        confidence=0.0,
                    )
                )
            await session.commit()


def _make_thinking_test_session_factory() -> tuple[async_sessionmaker, Path]:
    """Return (session_factory, db_path) bound to a fresh migrated sqlite file."""
    from test.migrated_database import create_migrated_sqlite_database

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    create_migrated_sqlite_database(db_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return session_factory, db_path


async def _seed_queued_crawl_job_with_default_profile(session_factory: async_sessionmaker) -> int:
    """Insert one is_default LLMProfile + one queued CrawlJob; return the job_id."""
    async with session_factory() as session:
        profile = LLMProfile(
            name="thinking-test-profile",
            provider="openai",
            api_base_url="https://api.example.com/v1",
            api_key="sk-test",
            model_name="acme-think-v1",
            is_default=True,
        )
        session.add(profile)
        await session.flush()

        job = CrawlJob(
            llm_profile_id=profile.id,
            entry_type="college",
            status="queued",
            start_url="https://example.edu/faculty",
            start_urls=["https://example.edu/faculty"],
            university="Example U",
            school="School of CS",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


class CrawlJobThinkingAdaptationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session_factory, self.db_path = _make_thinking_test_session_factory()

    async def asyncTearDown(self) -> None:
        engine = self.session_factory.kw.get("bind")
        if engine is not None:
            await engine.dispose()
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    async def test_run_queued_crawl_jobs_calls_ensure_thinking_adaptation(self) -> None:
        await _seed_queued_crawl_job_with_default_profile(self.session_factory)

        with (
            patch(
                "app.services.crawl_job_runtime.ensure_thinking_adaptation",
                AsyncMock(return_value={"thinking": {"type": "disabled"}}),
            ) as ensure_mock,
            patch(
                "app.services.crawl_job_runtime.run_faculty_crawler_agent",
                AsyncMock(return_value={"event_type": "completed"}),
            ) as run_mock,
        ):
            await run_queued_crawl_jobs_once(self.session_factory)

        ensure_mock.assert_awaited_once()
        run_mock.assert_awaited_once()
        kwargs = run_mock.call_args.kwargs
        self.assertEqual(kwargs.get("extra_body"), {"thinking": {"type": "disabled"}})

    async def test_thinking_adaptation_failure_marks_job_failed_and_skips_run(self) -> None:
        from app.services.thinking_adaptation import ThinkingAdaptationFailed

        job_id = await _seed_queued_crawl_job_with_default_profile(self.session_factory)

        with (
            patch(
                "app.services.crawl_job_runtime.ensure_thinking_adaptation",
                AsyncMock(
                    side_effect=ThinkingAdaptationFailed(
                        "已尝试全部候选 extra_body，仍无法绕开思考模式协议错。",
                        attempted_extra_bodies=[None, {"thinking": {"type": "disabled"}}],
                    )
                ),
            ),
            patch(
                "app.services.crawl_job_runtime.run_faculty_crawler_agent",
                AsyncMock(),
            ) as run_mock,
        ):
            await run_queued_crawl_jobs_once(self.session_factory)

        run_mock.assert_not_awaited()

        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            self.assertIsNotNone(job)
            self.assertEqual(job.status, "failed")
            assert job.error_message is not None
            self.assertIn("思考模式", job.error_message)


async def _seed_running_crawl_job_with_default_profile(
    session_factory: async_sessionmaker,
    *,
    agent_trace: list[dict[str, object]] | None = None,
) -> int:
    """Insert one is_default LLMProfile + one RUNNING CrawlJob; return the job_id."""
    from app.models import CrawlJob, LLMProfile

    async with session_factory() as session:
        profile = LLMProfile(
            name="thinking-running-profile",
            provider="openai",
            api_base_url="https://api.example.com/v1",
            api_key="sk-test",
            model_name="acme-think-v1",
            is_default=True,
        )
        session.add(profile)
        await session.flush()

        job = CrawlJob(
            llm_profile_id=profile.id,
            entry_type="college",
            status="running",
            start_url="https://example.edu/faculty",
            start_urls=["https://example.edu/faculty"],
            university="Example U",
            school="School of CS",
            agent_trace=agent_trace,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


class CrawlJobErrorMessageAdaptationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session_factory, self.db_path = _make_thinking_test_session_factory()

    async def asyncTearDown(self) -> None:
        engine = self.session_factory.kw.get("bind")
        if engine is not None:
            await engine.dispose()
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    async def test_mark_job_failed_appends_thinking_hint(self) -> None:
        from app.services.crawl_job_runtime import _mark_job_failed
        from app.models import CrawlJob, CrawlJobStatus

        job_id = await _seed_running_crawl_job_with_default_profile(self.session_factory)

        await _mark_job_failed(
            self.session_factory,
            job_id,
            "Error code: 400 - The reasoning_content in the thinking mode must be passed back to the API.",
        )

        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            self.assertEqual(job.status, CrawlJobStatus.FAILED.value)
            assert job.error_message is not None
            self.assertIn("测试连接", job.error_message)

    async def test_complete_running_job_appends_thinking_hint_when_no_candidates(self) -> None:
        from app.services.crawl_job_runtime import _complete_running_job
        from app.models import CrawlJob

        # _derive_job_failure_message 优先返回 trace 中的候选保存失败摘要；
        # 通过让 trace 为空、且不插入任何 CrawlPage 行，就会回落到查询 CrawlPage
        # 错误信息的分支。我们直接插入一行 failed CrawlPage 携带协议错文本。
        job_id = await _seed_running_crawl_job_with_default_profile(self.session_factory)
        async with self.session_factory() as session:
            from app.models import CrawlPage

            session.add(
                CrawlPage(
                    job_id=job_id,
                    url="https://example.edu/faculty",
                    fetch_method="http",
                    status="failed",
                    error_message=(
                        "Error code: 400 - The reasoning_content in the thinking "
                        "mode must be passed back to the API."
                    ),
                )
            )
            await session.commit()

        await _complete_running_job(self.session_factory, job_id)

        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            assert job is not None
            assert job.error_message is not None
            self.assertIn("测试连接", job.error_message)


if __name__ == "__main__":
    unittest.main()
