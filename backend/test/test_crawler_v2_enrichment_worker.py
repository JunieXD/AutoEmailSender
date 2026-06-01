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
from app.services.crawler_v2_enrichment_worker import enrich_candidate_once, run_crawler_v2_enrichment_worker_once


class CrawlerV2EnrichmentWorkerTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_enrichment_updates_missing_fields(self) -> None:
        candidate_id, task_id = await self._seed_task(profile_url="https://example.edu/zhang.html")
        payload = CandidateEnrichmentPayload(email="zhang@example.edu", department="计算机系", research_direction="AI", recent_papers=["P1"], confidence=0.8, field_confidence={})

        with patch("app.services.crawler_v2_enrichment_worker.enrich_candidate_once_with_usage", new=AsyncMock(return_value=(payload, None))):
            processed = await run_crawler_v2_enrichment_worker_once(self.session_factory, task_id=task_id, worker_id="w1")

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            candidate = await session.get(CrawlCandidate, candidate_id)
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        assert candidate is not None and task is not None
        self.assertEqual(candidate.email, "zhang@example.edu")
        self.assertEqual(candidate.department, "计算机系")
        self.assertEqual(task.status, CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value)

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

        with patch("app.services.crawler_v2_enrichment_worker.fetch_profile_text", new=AsyncMock(return_value="张三 邮箱 zhang@example.edu")) as fetch_mock, patch("app.services.crawler_v2_enrichment_worker.enrich_candidate_profile_with_llm_with_usage", new=AsyncMock(return_value=(payload, None))) as enrich_mock:
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
