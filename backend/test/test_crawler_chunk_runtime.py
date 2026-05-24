from __future__ import annotations

import asyncio
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import CrawlCandidate, CrawlJob, CrawlJobStatus, CrawlPage, CrawlPageChunk, CrawlPageChunkStatus
from app.models.base import Base
from app.services.crawler_chunking import ChunkingConfig, build_page_chunks
from app.services.crawler_chunk_runtime import claim_next_page_chunk, create_chunks_for_page, submit_chunk_candidates
from app.services.crawler_tools import CrawlToolContext


async def _session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


class CrawlerChunkRuntimeTests(unittest.TestCase):
    def test_claim_next_page_chunk_marks_chunk_processing(self) -> None:
        async def run() -> None:
            session_factory = await _session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu", status=CrawlJobStatus.RUNNING.value)
                page = CrawlPage(job=job, url="https://cs.example.edu/faculty", fetch_method="http", status="succeeded")
                session.add_all([job, page])
                await session.commit()
                await session.refresh(job)
                await session.refresh(page)
            drafts = build_page_chunks(source_url="https://cs.example.edu/faculty", html="<p>张三</p>", text="张三", config=ChunkingConfig())
            await create_chunks_for_page(session_factory, job_id=job.id, page_id=page.id, drafts=drafts)
            claimed = await claim_next_page_chunk(session_factory, job_id=job.id)
            self.assertEqual(claimed.status, "ok")
            self.assertIn("张三", claimed.content)
            async with session_factory() as session:
                row = (await session.scalars(select(CrawlPageChunk))).one()
                self.assertEqual(row.status, CrawlPageChunkStatus.PROCESSING.value)
        asyncio.run(run())


    def test_claim_next_page_chunk_returns_processing_chunk_before_pending(self) -> None:
        async def run() -> None:
            session_factory = await _session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu", status=CrawlJobStatus.RUNNING.value)
                page = CrawlPage(job=job, url="https://cs.example.edu/faculty", fetch_method="http", status="succeeded")
                session.add_all([job, page])
                await session.commit()
                await session.refresh(job)
                await session.refresh(page)
            drafts = build_page_chunks(source_url="https://cs.example.edu/faculty", html="<p>张三</p>", text="张三", config=ChunkingConfig())
            await create_chunks_for_page(session_factory, job_id=job.id, page_id=page.id, drafts=drafts)
            async with session_factory() as session:
                session.add(
                    CrawlPageChunk(
                        job_id=job.id,
                        page_id=page.id,
                        source_url="https://cs.example.edu/faculty-2",
                        page_fingerprint="fp-2",
                        chunk_id="pending-2",
                        chunk_index=1,
                        chunk_hash="hash-2",
                        status=CrawlPageChunkStatus.PENDING.value,
                        content="李四",
                        token_estimate=1,
                        text_start_offset=0,
                        text_end_offset=2,
                    )
                )
                await session.commit()
            first = await claim_next_page_chunk(session_factory, job_id=job.id)
            second = await claim_next_page_chunk(session_factory, job_id=job.id)
            self.assertEqual(second.status, "ok")
            self.assertEqual(second.chunk_id, first.chunk_id)
            self.assertIn("继续处理", second.message or "")
        asyncio.run(run())

    def test_submit_chunk_candidates_marks_no_candidates(self) -> None:
        async def run() -> None:
            session_factory = await _session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu", status=CrawlJobStatus.RUNNING.value)
                page = CrawlPage(job=job, url="https://cs.example.edu/faculty", fetch_method="http", status="succeeded")
                session.add_all([job, page])
                await session.commit()
                await session.refresh(job)
                await session.refresh(page)
            drafts = build_page_chunks(source_url="https://cs.example.edu/faculty", html="<p>导航</p>", text="导航", config=ChunkingConfig())
            await create_chunks_for_page(session_factory, job_id=job.id, page_id=page.id, drafts=drafts)
            claimed = await claim_next_page_chunk(session_factory, job_id=job.id)
            ctx = CrawlToolContext(job_id=job.id, start_url="https://cs.example.edu", university="示例大学", school="计算机学院", session_factory=session_factory)
            result = await submit_chunk_candidates(ctx, chunk_id=claimed.chunk_id or "", chunk_status="no_candidates", has_more_candidates_in_chunk=False, candidates=[])
            self.assertEqual(result["chunk_status"], CrawlPageChunkStatus.NO_CANDIDATES.value)
            async with session_factory() as session:
                row = (await session.scalars(select(CrawlPageChunk))).one()
                self.assertEqual(row.status, CrawlPageChunkStatus.NO_CANDIDATES.value)
        asyncio.run(run())


    def test_submit_unsplittable_chunk_reports_failed(self) -> None:
        async def run() -> None:
            session_factory = await _session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu", status=CrawlJobStatus.RUNNING.value)
                page = CrawlPage(job=job, url="https://cs.example.edu/faculty", fetch_method="http", status="succeeded")
                session.add_all([job, page])
                await session.commit()
                await session.refresh(job)
                await session.refresh(page)
                chunk = CrawlPageChunk(
                    job_id=job.id,
                    page_id=page.id,
                    source_url="https://cs.example.edu/faculty",
                    page_fingerprint="fp",
                    chunk_id="unsplittable",
                    chunk_index=0,
                    chunk_hash="hash",
                    status=CrawlPageChunkStatus.PROCESSING.value,
                    content="张三",
                    token_estimate=1,
                    text_start_offset=0,
                    text_end_offset=2,
                    split_depth=4,
                )
                session.add(chunk)
                await session.commit()
            ctx = CrawlToolContext(job_id=job.id, start_url="https://cs.example.edu", university="示例大学", school="计算机学院", session_factory=session_factory)
            result = await submit_chunk_candidates(
                ctx,
                chunk_id="unsplittable",
                chunk_status="too_many_candidates",
                has_more_candidates_in_chunk=True,
                candidates=[],
            )
            self.assertEqual(result["chunk_status"], CrawlPageChunkStatus.FAILED.value)
            self.assertIn("无法继续拆分", result["next_instruction"])
            async with session_factory() as session:
                row = (await session.scalars(select(CrawlPageChunk))).one()
                self.assertEqual(row.status, CrawlPageChunkStatus.FAILED.value)
                self.assertIn("超过最大拆分深度", row.last_error or "")
        asyncio.run(run())

    def test_submit_chunk_candidates_persists_source_metadata(self) -> None:
        async def run() -> None:
            session_factory = await _session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu", status=CrawlJobStatus.RUNNING.value)
                page = CrawlPage(job=job, url="https://cs.example.edu/faculty", fetch_method="http", status="succeeded")
                session.add_all([job, page])
                await session.commit()
                await session.refresh(job)
                await session.refresh(page)
            drafts = build_page_chunks(source_url="https://cs.example.edu/faculty", html="<p>张三</p>", text="张三", config=ChunkingConfig())
            await create_chunks_for_page(session_factory, job_id=job.id, page_id=page.id, drafts=drafts)
            claimed = await claim_next_page_chunk(session_factory, job_id=job.id)
            ctx = CrawlToolContext(job_id=job.id, start_url="https://cs.example.edu", university="示例大学", school="计算机学院", session_factory=session_factory)
            await submit_chunk_candidates(
                ctx,
                chunk_id=claimed.chunk_id or "",
                chunk_status="completed",
                has_more_candidates_in_chunk=False,
                candidates=[{"name": "张三", "profile_url": "https://cs.example.edu/zhang", "source_url": "https://cs.example.edu/faculty", "boundary_risk": True}],
            )
            async with session_factory() as session:
                row = (await session.scalars(select(CrawlCandidate))).one()
                self.assertEqual(row.source_chunk_id, claimed.chunk_id)
                self.assertEqual(row.source_kind, "list_chunk")
                self.assertTrue(row.boundary_risk)
                self.assertEqual(row.identity_key, "https://cs.example.edu/zhang")
                self.assertEqual(row.field_sources["profile_url"]["source_chunk_id"], claimed.chunk_id)
        asyncio.run(run())

    def test_submit_ten_candidates_splits_parent_chunk_into_children(self) -> None:
        async def run() -> None:
            session_factory = await _session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu", status=CrawlJobStatus.RUNNING.value)
                page = CrawlPage(job=job, url="https://cs.example.edu/faculty", fetch_method="http", status="succeeded")
                session.add_all([job, page])
                await session.commit()
                await session.refresh(job)
                await session.refresh(page)
            content = "\n".join(f"教师{i} [详情](https://cs.example.edu/t{i}.htm) 研究方向 数据库" for i in range(40))
            drafts = build_page_chunks(source_url="https://cs.example.edu/faculty", html="", text=content, config=ChunkingConfig(target_tokens=1000, soft_max_tokens=1200, hard_max_tokens=1400, overlap_tokens=30))[:1]
            await create_chunks_for_page(session_factory, job_id=job.id, page_id=page.id, drafts=drafts)
            claimed = await claim_next_page_chunk(session_factory, job_id=job.id)
            ctx = CrawlToolContext(job_id=job.id, start_url="https://cs.example.edu", university="示例大学", school="计算机学院", session_factory=session_factory)
            candidates = [{"name": f"教师{i}", "profile_url": f"https://cs.example.edu/t{i}.htm", "source_url": "https://cs.example.edu/faculty"} for i in range(10)]
            result = await submit_chunk_candidates(ctx, chunk_id=claimed.chunk_id or "", chunk_status="completed", has_more_candidates_in_chunk=True, candidates=candidates)
            self.assertEqual(result["chunk_status"], CrawlPageChunkStatus.SPLIT_REQUIRED.value)
            async with session_factory() as session:
                rows = list(await session.scalars(select(CrawlPageChunk).order_by(CrawlPageChunk.id)))
                self.assertEqual(rows[0].status, CrawlPageChunkStatus.SUPERSEDED.value)
                self.assertGreaterEqual(len(rows), 3)
                self.assertTrue(all(row.parent_chunk_id == rows[0].chunk_id for row in rows[1:]))
                self.assertTrue(all(row.status == CrawlPageChunkStatus.PENDING.value for row in rows[1:]))
        asyncio.run(run())

    def test_submit_exactly_ten_candidates_without_more_marks_completed(self) -> None:
        async def run() -> None:
            session_factory = await _session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu", status=CrawlJobStatus.RUNNING.value)
                page = CrawlPage(job=job, url="https://cs.example.edu/faculty", fetch_method="http", status="succeeded")
                session.add_all([job, page])
                await session.commit()
                await session.refresh(job)
                await session.refresh(page)
            content = "\n".join(f"教师{i} [详情](https://cs.example.edu/t{i}.htm)" for i in range(10))
            drafts = build_page_chunks(source_url="https://cs.example.edu/faculty", html="", text=content, config=ChunkingConfig())
            await create_chunks_for_page(session_factory, job_id=job.id, page_id=page.id, drafts=drafts)
            claimed = await claim_next_page_chunk(session_factory, job_id=job.id)
            ctx = CrawlToolContext(job_id=job.id, start_url="https://cs.example.edu", university="示例大学", school="计算机学院", session_factory=session_factory)
            candidates = [{"name": f"教师{i}", "profile_url": f"https://cs.example.edu/t{i}.htm", "source_url": "https://cs.example.edu/faculty"} for i in range(10)]

            result = await submit_chunk_candidates(ctx, chunk_id=claimed.chunk_id or "", chunk_status="completed", has_more_candidates_in_chunk=False, candidates=candidates)

            self.assertEqual(result["chunk_status"], CrawlPageChunkStatus.COMPLETED.value)
            async with session_factory() as session:
                rows = list(await session.scalars(select(CrawlPageChunk).order_by(CrawlPageChunk.id)))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].status, CrawlPageChunkStatus.COMPLETED.value)
        asyncio.run(run())

    def test_submit_unclaimed_pending_chunk_is_rejected(self) -> None:
        async def run() -> None:
            session_factory = await _session_factory()
            async with session_factory() as session:
                job = CrawlJob(university="示例大学", school="计算机学院", start_url="https://cs.example.edu", status=CrawlJobStatus.RUNNING.value)
                page = CrawlPage(job=job, url="https://cs.example.edu/faculty", fetch_method="http", status="succeeded")
                session.add_all([job, page])
                await session.commit()
                await session.refresh(job)
                await session.refresh(page)
            drafts = build_page_chunks(source_url="https://cs.example.edu/faculty", html="<p>张三</p>", text="张三", config=ChunkingConfig())
            await create_chunks_for_page(session_factory, job_id=job.id, page_id=page.id, drafts=drafts)
            ctx = CrawlToolContext(job_id=job.id, start_url="https://cs.example.edu", university="示例大学", school="计算机学院", session_factory=session_factory)

            result = await submit_chunk_candidates(ctx, chunk_id=drafts[0].chunk_id, chunk_status="no_candidates", has_more_candidates_in_chunk=False, candidates=[])

            self.assertEqual(result["chunk_status"], "failed")
            async with session_factory() as session:
                row = (await session.scalars(select(CrawlPageChunk))).one()
                self.assertEqual(row.status, CrawlPageChunkStatus.PENDING.value)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
