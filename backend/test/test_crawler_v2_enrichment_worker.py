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

from app.models import CrawlCandidate, CrawlCandidateEnrichmentTask, CrawlCandidateEnrichmentTaskStatus, CrawlJob, CrawlJobStatus, CrawlWorkerTokenUsage, LLMProfile
from app.services.crawler_tools import CandidateEnrichmentPayload
from app.services.llm_runtime import LLMRuntimeAdaptation
from app.services import crawler_v2_enrichment_worker
from app.services.crawler_v2_enrichment_worker import enrich_candidate_once, run_crawler_v2_enrichment_worker_once


class CrawlerV2EnrichmentWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_enrichment_adaptation_cache_is_committed_before_context_session_closes(self) -> None:
        from app.services.llm_endpoint_adaptation import (
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
            patch("app.services.crawler_v2_enrichment_worker.ensure_llm_runtime_adaptation", new=AsyncMock(side_effect=fake_ensure)),
            patch("app.services.crawler_v2_enrichment_worker.get_or_fetch_profile_text", new=AsyncMock(return_value="张三")),
            patch(
                "app.services.crawler_v2_enrichment_worker.enrich_candidate_profile_with_llm_with_usage",
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
        payload = CandidateEnrichmentPayload(email="zhang@example.edu", title="教授", department="计算机系", research_direction="AI", recent_papers=["P1"], confidence=0.8, field_confidence={})

        with patch("app.services.crawler_v2_enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(return_value=(payload, None))):
            processed = await run_crawler_v2_enrichment_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert candidate is not None and task is not None
        self.assertEqual(candidate.email, "zhang@example.edu")
        self.assertEqual(candidate.title, "教授")
        self.assertEqual(candidate.department, "计算机系")
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value)
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, task.job_id)
        assert job is not None
        trace = [item for item in job.agent_trace or [] if isinstance(item, dict)]
        self.assertTrue(trace)
        self.assertEqual(trace[-1]["event_type"], "enrichment")
        self.assertEqual(trace[-1]["message"], "候选导师详情补全成功：张三")
        self.assertEqual(trace[-1]["raw"]["candidate_id"], candidate_id)
        self.assertEqual(trace[-1]["raw"]["status"], "succeeded")

    async def test_enrichment_skips_candidate_without_profile_url(self) -> None:
        _, task_id = await self._seed_task(profile_url=None)

        processed = await run_crawler_v2_enrichment_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert task is not None
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.SKIPPED.value)


    async def test_enrichment_adapter_fetches_profile_and_invokes_existing_llm(self) -> None:
        candidate_id, _ = await self._seed_task(profile_url="https://example.edu/zhang.html")
        payload = CandidateEnrichmentPayload(email="zhang@example.edu", department="计算机系", research_direction="AI", recent_papers=[], confidence=0.8, field_confidence={})

        with patch("app.services.crawler_v2_enrichment_worker.fetch_profile_text", new=AsyncMock(return_value="张三 邮箱 zhang@example.edu")) as fetch_mock, patch("app.services.crawler_v2_enrichment_worker.ensure_llm_runtime_adaptation", new=AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None))), patch("app.services.crawler_v2_enrichment_worker.enrich_candidate_profile_with_llm_with_usage", new=AsyncMock(return_value=(payload, None))) as enrich_mock:
            result = await enrich_candidate_once(self.session_factory, candidate_id=candidate_id)

        self.assertEqual(result.email, "zhang@example.edu")
        fetch_mock.assert_awaited_once()
        enrich_mock.assert_awaited_once()

    async def test_enrichment_worker_does_not_write_after_job_is_paused(self) -> None:
        candidate_id, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
        payload = CandidateEnrichmentPayload(email="zhang@example.edu", department="计算机系", research_direction="AI", recent_papers=[], confidence=0.8, field_confidence={})

        async def pause_job_during_enrichment(*_args, **_kwargs):
            async with self.session_factory() as session:
                task = await session.get(CrawlCandidateEnrichmentTask, task_id)
                assert task is not None
                job = await session.get(CrawlJob, task.job_id)
                assert job is not None
                job.status = CrawlJobStatus.PAUSED.value
                task.status = CrawlCandidateEnrichmentTaskStatus.PENDING.value
                task.worker_id = None
                await session.commit()
            return payload, {"input_tokens": 10, "output_tokens": 5, "cached_tokens": 0}

        with patch("app.services.crawler_v2_enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(side_effect=pause_job_during_enrichment)):
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

    async def test_enrichment_worker_does_not_write_after_lease_expires(self) -> None:
        candidate_id, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
        expired = datetime.now(UTC) - timedelta(seconds=1)
        async with self.session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            assert task is not None
            task.lease_expires_at = expired
            await session.commit()
        payload = CandidateEnrichmentPayload(email="zhang@example.edu", department="计算机系", research_direction="AI", recent_papers=[], confidence=0.8, field_confidence={})

        with patch("app.services.crawler_v2_enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(return_value=(payload, None))):
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

        with patch("app.services.crawler_v2_enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(side_effect=ValueError("429 Too Many Requests"))):
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

        with patch("app.services.crawler_v2_enrichment_worker.fetch_profile_text", new=AsyncMock(side_effect=fake_fetch)) as fetch_mock, \
            patch("app.services.crawler_v2_enrichment_worker.ensure_llm_runtime_adaptation", new=AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None))), \
            patch("app.services.crawler_v2_enrichment_worker.enrich_candidate_profile_with_llm_with_usage", new=AsyncMock(side_effect=fail_llm)):
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

    async def test_llm_retry_reuses_empty_profile_text_after_fetch_succeeds(self) -> None:
        _, task_id = await self._seed_task(profile_url="https://example.edu/empty.html")

        async def fail_llm(*_args, **_kwargs):
            raise ValueError("LLM empty")

        with patch("app.services.crawler_v2_enrichment_worker.fetch_profile_text", new=AsyncMock(return_value="")) as fetch_mock, \
            patch("app.services.crawler_v2_enrichment_worker.ensure_llm_runtime_adaptation", new=AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None))), \
            patch("app.services.crawler_v2_enrichment_worker.enrich_candidate_profile_with_llm_with_usage", new=AsyncMock(side_effect=fail_llm)):
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

    async def test_enrichment_worker_failure_appends_trace_event_with_reason(self) -> None:
        _, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")

        with patch("app.services.crawler_v2_enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(side_effect=ValueError("LLM 401"))):
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

        with patch("app.services.crawler_v2_enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(return_value=(payload, None))):
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
        with patch("app.services.crawler_v2_enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(return_value=(payload, usage, raw_model_text))), patch("app.services.crawler_v2_enrichment_worker.append_crawler_v2_debug_event") as debug_mock:
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

    async def test_enrichment_adapter_passes_runtime_adaptation_to_model(self) -> None:
        candidate_id, _ = await self._seed_task(profile_url="https://example.edu/zhang.html")
        payload = CandidateEnrichmentPayload(email="zhang@example.edu", department="计算机系", research_direction="AI", recent_papers=[], confidence=0.8, field_confidence={})
        adaptation = LLMRuntimeAdaptation("responses", {"enable_thinking": False})

        with patch("app.services.crawler_v2_enrichment_worker.fetch_profile_text", new=AsyncMock(return_value="张三 邮箱 zhang@example.edu")), \
            patch("app.services.crawler_v2_enrichment_worker.ensure_llm_runtime_adaptation", new=AsyncMock(return_value=adaptation)) as adaptation_mock, \
            patch(
                "app.services.crawler_v2_enrichment_worker.invoke_crawler_llm_with_endpoint_retry",
                new=AsyncMock(),
                create=True,
            ) as invoke_mock:
            fake_response = type("FakeResponse", (), {"content": '{"email":"zhang@example.edu","department":"计算机系","research_direction":"AI","recent_papers":[],"confidence":0.8,"field_confidence":{}}', "usage_metadata": {"input_tokens": 1, "output_tokens": 1, "cached_tokens": 0}})()
            invoke_mock.return_value = (fake_response, adaptation)

            result = await enrich_candidate_once(self.session_factory, candidate_id=candidate_id)

        self.assertEqual(result.email, "zhang@example.edu")
        adaptation_mock.assert_awaited_once()
        invoke_mock.assert_awaited_once()
        self.assertIs(invoke_mock.await_args.args[0], self.session_factory)
        self.assertIs(invoke_mock.await_args.args[2], adaptation)

    async def test_enrichment_worker_records_llm_token_usage(self) -> None:
        _, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
        payload = CandidateEnrichmentPayload(email="zhang@example.edu", department="计算机系", research_direction="AI", recent_papers=[], confidence=0.8, field_confidence={})
        usage = {"input_tokens": 90, "output_tokens": 10, "cached_tokens": 70, "total_tokens": 100}

        with patch("app.services.crawler_v2_enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(return_value=(payload, usage))):
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
            job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://example.edu/faculty", status=CrawlJobStatus.RUNNING.value, runtime_version="v2", llm_profile_id=profile.id)
            session.add(job)
            await session.flush()
            candidate = CrawlCandidate(job_id=job.id, name="张三", profile_url=profile_url)
            session.add(candidate)
            await session.flush()
            task = CrawlCandidateEnrichmentTask(job_id=job.id, candidate_id=candidate.id, status=CrawlCandidateEnrichmentTaskStatus.PROCESSING.value, worker_id="w1")
            session.add(task)
            await session.commit()
            return candidate.id, task.id


if __name__ == "__main__":
    unittest.main()
