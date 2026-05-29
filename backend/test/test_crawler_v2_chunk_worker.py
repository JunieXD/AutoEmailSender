from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, CrawlCandidate, CrawlCandidateEnrichmentTask, CrawlJob, CrawlJobStatus, CrawlPageChunk, CrawlPageChunkStatus, CrawlPageTask
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

    async def _seed_processing_chunk(self) -> tuple[int, int]:
        async with self.session_factory() as session:
            job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://example.edu/faculty", status=CrawlJobStatus.RUNNING.value, runtime_version="v2")
            session.add(job)
            await session.flush()
            chunk = CrawlPageChunk(job_id=job.id, page_id=None, source_url="https://example.edu/faculty", page_fingerprint="p", chunk_id="c1", chunk_index=0, chunk_hash="h", content="张三", status=CrawlPageChunkStatus.PROCESSING.value, worker_id="w1")
            session.add(chunk)
            await session.commit()
            return job.id, chunk.id


if __name__ == "__main__":
    unittest.main()