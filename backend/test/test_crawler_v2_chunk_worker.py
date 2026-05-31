from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, CrawlCandidate, CrawlCandidateEnrichmentTask, CrawlJob, CrawlJobStatus, CrawlPageChunk, CrawlPageChunkStatus, CrawlPageTask, CrawlWorkerTokenUsage, LLMProfile
from app.services.crawler_v2_chunk_worker import complete_current_chunk, run_crawler_v2_chunk_worker_once
from app.services.crawler_tools import ProfessorCandidatePayload


class CrawlerV2ChunkWorkerTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_complete_chunk_saves_candidates_urls_and_enrichment_tasks_atomically(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()
        candidate = ProfessorCandidatePayload(name="张三", profile_url="https://example.edu/zhang.html", source_url="https://example.edu/faculty", confidence=0.9)

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[candidate],
            discovered_urls=["https://example.edu/page2.html", "https://other.edu/nope"],
            chunk_status="completed",
        )

        self.assertEqual(result["saved_count"], 1)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
            page_tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id).order_by(CrawlPageTask.id)))
            enrichment_tasks = list(await session.scalars(select(CrawlCandidateEnrichmentTask).where(CrawlCandidateEnrichmentTask.job_id == job_id)))
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.COMPLETED.value)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].name, "张三")
        self.assertEqual([task.normalized_url for task in page_tasks], ["https://example.edu/page2.html"])
        self.assertEqual(len(enrichment_tasks), 1)
        self.assertEqual(enrichment_tasks[0].candidate_id, candidates[0].id)


    async def test_chunk_worker_without_llm_profile_marks_retryable(self) -> None:
        _, chunk_id = await self._seed_processing_chunk()

        processed = await run_crawler_v2_chunk_worker_once(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
        )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.FAILED_RETRYABLE.value)
        self.assertIsNone(chunk.worker_id)
        self.assertIn("LLM Profile", chunk.last_error or "")


    async def test_chunk_worker_uses_single_tool_payload_instead_of_legacy_agent(self) -> None:
        _, chunk_id = await self._seed_processing_chunk(with_profile=True)
        payload = {
            "candidates": [
                {
                    "name": "张三",
                    "profile_url": "https://example.edu/zhang.html",
                    "source_url": "https://example.edu/faculty",
                    "confidence": 0.9,
                }
            ],
            "discovered_urls": ["https://example.edu/page2.html"],
            "chunk_status": "completed",
        }

        with patch("app.services.crawler_v2_chunk_worker.invoke_v2_chunk_agent", new=AsyncMock(return_value=payload)) as invoke_mock:
            processed = await run_crawler_v2_chunk_worker_once(
                self.session_factory,
                chunk_id=chunk_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        import app.services.crawler_v2_chunk_worker as module
        self.assertFalse(hasattr(module, "run_faculty_crawler_agent"))
        invoke_mock.assert_awaited_once()
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            candidates = list(await session.scalars(select(CrawlCandidate)))
            tasks = list(await session.scalars(select(CrawlPageTask).order_by(CrawlPageTask.id)))
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.COMPLETED.value)
        self.assertEqual([candidate.name for candidate in candidates], ["张三"])
        self.assertEqual([task.normalized_url for task in tasks], ["https://example.edu/page2.html"])

    async def test_complete_chunk_idempotently_ignores_url_already_found_by_page_worker(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()
        async with self.session_factory() as session:
            session.add(
                CrawlPageTask(
                    job_id=job_id,
                    normalized_url="https://example.edu/page2.html",
                    original_url="https://example.edu/page2.html",
                )
            )
            await session.commit()

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[ProfessorCandidatePayload(name="张三", email="zhang@example.edu", confidence=0.9)],
            discovered_urls=["https://example.edu/page2.html", "https://example.edu/page2.html#section"],
            chunk_status="completed",
        )

        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(result["url_count"], 0)
        async with self.session_factory() as session:
            tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)))
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
        self.assertEqual(len(tasks), 1)
        self.assertEqual(len(candidates), 1)

    async def test_complete_chunk_keeps_candidate_save_when_url_insert_hits_unique_conflict(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()
        flush_calls = 0

        async def flush_with_url_conflict(self_session, *args, **kwargs):
            nonlocal flush_calls
            flush_calls += 1
            if flush_calls == 2:
                raise IntegrityError("insert", {}, Exception("unique conflict"))
            return await original_flush(self_session, *args, **kwargs)

        async with self.session_factory() as probe_session:
            original_flush = type(probe_session).flush

        with patch("sqlalchemy.ext.asyncio.AsyncSession.flush", flush_with_url_conflict):
            result = await complete_current_chunk(
                self.session_factory,
                chunk_id=chunk_id,
                worker_id="w1",
                candidates=[ProfessorCandidatePayload(name="张三", email="zhang@example.edu", confidence=0.9)],
                discovered_urls=["https://example.edu/race.html"],
                chunk_status="completed",
            )

        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(result["url_count"], 0)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
            tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)))
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.COMPLETED.value)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(tasks), 0)

    async def test_complete_chunk_rejects_expired_lease_without_writing(self) -> None:
        job_id, chunk_id = await self._seed_processing_chunk()
        expired = datetime.now(UTC) - timedelta(seconds=1)
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            assert chunk is not None
            chunk.lease_expires_at = expired
            await session.commit()
        candidate = ProfessorCandidatePayload(name="张三", profile_url="https://example.edu/zhang.html", source_url="https://example.edu/faculty", confidence=0.9)

        result = await complete_current_chunk(
            self.session_factory,
            chunk_id=chunk_id,
            worker_id="w1",
            candidates=[candidate],
            discovered_urls=["https://example.edu/page2.html"],
            chunk_status="completed",
        )

        self.assertEqual(result["status"], "lease_expired")
        async with self.session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            candidates = list(await session.scalars(select(CrawlCandidate).where(CrawlCandidate.job_id == job_id)))
            page_tasks = list(await session.scalars(select(CrawlPageTask).where(CrawlPageTask.job_id == job_id)))
        assert chunk is not None
        self.assertEqual(chunk.status, CrawlPageChunkStatus.PROCESSING.value)
        self.assertEqual(len(candidates), 0)
        self.assertEqual(len(page_tasks), 0)
    async def test_chunk_worker_records_llm_token_usage(self) -> None:
        _, chunk_id = await self._seed_processing_chunk(with_profile=True)

        class FakeResponse:
            content = '{"candidates": [], "discovered_urls": [], "chunk_status": "no_candidates"}'
            usage_metadata = {"input_tokens": 100, "output_tokens": 20, "cached_tokens": 80, "total_tokens": 120}

        fake_model = AsyncMock()
        fake_model.ainvoke = AsyncMock(return_value=FakeResponse())

        with patch("app.services.crawler_v2_chunk_worker.build_faculty_crawler_model", return_value=fake_model):
            processed = await run_crawler_v2_chunk_worker_once(
                self.session_factory,
                chunk_id=chunk_id,
                worker_id="w1",
            )

        self.assertEqual(processed, 1)
        async with self.session_factory() as session:
            usage = await session.scalar(select(CrawlWorkerTokenUsage))
        assert usage is not None
        self.assertEqual(usage.worker_kind, "chunk")
        self.assertEqual(usage.work_item_id, str(chunk_id))
        self.assertEqual(usage.model_name, "deepseek")
        self.assertEqual(usage.input_tokens, 100)
        self.assertEqual(usage.output_tokens, 20)
        self.assertEqual(usage.cached_tokens, 80)
    async def _seed_processing_chunk(self, *, with_profile: bool = False) -> tuple[int, int]:
        async with self.session_factory() as session:
            llm_profile_id = None
            if with_profile:
                profile = LLMProfile(name="默认", provider="openai", api_base_url="https://api.example.com/v1", api_key="sk-test", model_name="deepseek", is_default=True)
                session.add(profile)
                await session.flush()
                llm_profile_id = profile.id
            job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://example.edu/faculty", status=CrawlJobStatus.RUNNING.value, runtime_version="v2", llm_profile_id=llm_profile_id)
            session.add(job)
            await session.flush()
            chunk = CrawlPageChunk(job_id=job.id, page_id=None, source_url="https://example.edu/faculty", page_fingerprint="p", chunk_id="c1", chunk_index=0, chunk_hash="h", content="张三", status=CrawlPageChunkStatus.PROCESSING.value, worker_id="w1")
            session.add(chunk)
            await session.commit()
            return job.id, chunk.id


if __name__ == "__main__":
    unittest.main()