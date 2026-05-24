from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CrawlPageChunk, CrawlPageChunkStatus
from app.services.crawler_chunking import ChunkingConfig, PageChunkDraft, split_chunk_content
from app.services.crawler_tools import CrawlToolContext, ProfessorCandidatePayload, save_candidate_batch


@dataclass(frozen=True)
class ClaimedChunk:
    status: Literal["ok", "empty", "already_processed"]
    chunk_id: str | None = None
    source_url: str | None = None
    chunk_index: int | None = None
    content: str | None = None
    max_candidates: int = 10
    message: str | None = None


async def create_chunks_for_page(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    page_id: int | None,
    drafts: list[PageChunkDraft],
) -> int:
    async with session_factory() as session:
        created = 0
        for draft in drafts:
            exists = await session.scalar(
                select(CrawlPageChunk.id).where(
                    CrawlPageChunk.job_id == job_id,
                    CrawlPageChunk.chunk_id == draft.chunk_id,
                )
            )
            if exists is not None:
                continue
            session.add(
                CrawlPageChunk(
                    job_id=job_id,
                    page_id=page_id,
                    source_url=draft.source_url,
                    page_fingerprint=draft.page_fingerprint,
                    chunk_id=draft.chunk_id,
                    parent_chunk_id=draft.parent_chunk_id,
                    chunk_index=draft.chunk_index,
                    chunk_hash=draft.chunk_hash,
                    status=CrawlPageChunkStatus.PENDING.value,
                    content=draft.content,
                    token_estimate=draft.token_estimate,
                    text_start_offset=draft.text_start_offset,
                    text_end_offset=draft.text_end_offset,
                    overlap_prefix=draft.overlap_prefix,
                    overlap_suffix=draft.overlap_suffix,
                    split_depth=draft.split_depth,
                )
            )
            created += 1
        await session.commit()
        return created


async def claim_next_page_chunk(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
) -> ClaimedChunk:
    async with session_factory() as session:
        processing_chunk = await session.scalar(
            select(CrawlPageChunk)
            .where(
                CrawlPageChunk.job_id == job_id,
                CrawlPageChunk.status == CrawlPageChunkStatus.PROCESSING.value,
            )
            .order_by(CrawlPageChunk.id.asc())
            .limit(1)
        )
        if processing_chunk is not None:
            return ClaimedChunk(
                status="ok",
                chunk_id=processing_chunk.chunk_id,
                source_url=processing_chunk.source_url,
                chunk_index=processing_chunk.chunk_index,
                content=processing_chunk.content,
                max_candidates=10,
                message="继续处理上次已领取但尚未提交的页面片段。",
            )

        chunk = await session.scalar(
            select(CrawlPageChunk)
            .where(
                CrawlPageChunk.job_id == job_id,
                CrawlPageChunk.status == CrawlPageChunkStatus.PENDING.value,
            )
            .order_by(CrawlPageChunk.id.asc())
            .limit(1)
        )
        if chunk is None:
            return ClaimedChunk(status="empty", message="当前没有待处理页面片段。请探索新页面或结束任务。")
        chunk.status = CrawlPageChunkStatus.PROCESSING.value
        chunk.attempt_count += 1
        await session.commit()
        await session.refresh(chunk)
        return ClaimedChunk(
            status="ok",
            chunk_id=chunk.chunk_id,
            source_url=chunk.source_url,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            max_candidates=10,
        )


async def submit_chunk_candidates(
    ctx: CrawlToolContext,
    *,
    chunk_id: str,
    chunk_status: str,
    has_more_candidates_in_chunk: bool,
    candidates: list[dict[str, object]],
) -> dict[str, Any]:
    if len(candidates) > 10:
        return await _mark_chunk_split_required(ctx, chunk_id, "candidate_count_exceeded")

    async with ctx.session_factory() as session:
        chunk = await session.scalar(
            select(CrawlPageChunk).where(
                CrawlPageChunk.job_id == ctx.job_id,
                CrawlPageChunk.chunk_id == chunk_id,
            )
        )
        if chunk is None:
            return {"chunk_status": "failed", "message": "chunk 不存在"}
        if chunk.status in {
            CrawlPageChunkStatus.COMPLETED.value,
            CrawlPageChunkStatus.NO_CANDIDATES.value,
            CrawlPageChunkStatus.SUPERSEDED.value,
        }:
            return {"chunk_status": "already_processed", "message": "该页面片段已处理，请获取下一个未处理 chunk。"}
        if chunk.status != CrawlPageChunkStatus.PROCESSING.value:
            return {"chunk_status": "failed", "message": "该页面片段尚未领取或当前不可提交，请先调用 claim_next_page_chunk。"}

    if chunk_status == "no_candidates":
        async with ctx.session_factory() as session:
            chunk = await session.scalar(
                select(CrawlPageChunk).where(
                    CrawlPageChunk.job_id == ctx.job_id,
                    CrawlPageChunk.chunk_id == chunk_id,
                )
            )
            if chunk is not None:
                chunk.status = CrawlPageChunkStatus.NO_CANDIDATES.value
                await session.commit()
        return {
            "chunk_status": CrawlPageChunkStatus.NO_CANDIDATES.value,
            "saved_count": 0,
            "merged_count": 0,
            "skipped_duplicate_count": 0,
            "rejected_count": 0,
        }

    enriched_candidates = [
        {
            **candidate,
            "source_chunk_id": chunk_id,
            "source_kind": "list_chunk",
            "boundary_risk": bool(candidate.get("boundary_risk", False)),
        }
        for candidate in candidates
    ]
    try:
        payloads = [ProfessorCandidatePayload.model_validate(candidate) for candidate in enriched_candidates]
    except ValidationError as exc:
        return {"chunk_status": "failed", "message": str(exc)}

    save_result = await save_candidate_batch(ctx, payloads)
    if has_more_candidates_in_chunk or chunk_status == "too_many_candidates":
        split_result = await _mark_chunk_split_required(ctx, chunk_id, "too_many_candidates")
        return {**save_result, **split_result}

    async with ctx.session_factory() as session:
        chunk = await session.scalar(
            select(CrawlPageChunk).where(
                CrawlPageChunk.job_id == ctx.job_id,
                CrawlPageChunk.chunk_id == chunk_id,
            )
        )
        if chunk is not None:
            chunk.status = CrawlPageChunkStatus.COMPLETED.value
            await session.commit()
    return {**save_result, "chunk_status": CrawlPageChunkStatus.COMPLETED.value}


async def _mark_chunk_split_required(ctx: CrawlToolContext, chunk_id: str, reason: str) -> dict[str, Any]:
    async with ctx.session_factory() as session:
        chunk = await session.scalar(
            select(CrawlPageChunk).where(
                CrawlPageChunk.job_id == ctx.job_id,
                CrawlPageChunk.chunk_id == chunk_id,
            )
        )
        if chunk is None:
            return {
                "chunk_status": "failed",
                "split_reason": reason,
                "created_child_chunks": 0,
            }
        child_count = await _split_chunk_in_session(session, ctx.job_id, chunk, reason)
        await session.commit()
        if child_count <= 0:
            return {
                "chunk_status": CrawlPageChunkStatus.FAILED.value,
                "split_reason": reason,
                "created_child_chunks": 0,
                "last_error": chunk.last_error,
                "next_instruction": "该 chunk 无法继续拆分，请跳过当前 chunk，获取下一个页面片段。",
            }
        return {
            "chunk_status": CrawlPageChunkStatus.SPLIT_REQUIRED.value,
            "split_reason": reason,
            "created_child_chunks": child_count,
        }


async def _split_chunk_in_session(
    session: AsyncSession,
    job_id: int,
    chunk: CrawlPageChunk,
    reason: str,
) -> int:
    config = ChunkingConfig()
    if chunk.split_depth >= config.max_split_depth:
        chunk.status = CrawlPageChunkStatus.FAILED.value
        chunk.last_error = f"超过最大拆分深度：{reason}"
        return 0

    drafts = split_chunk_content(
        source_url=chunk.source_url,
        content=chunk.content,
        parent_chunk_id=chunk.chunk_id,
        page_fingerprint=chunk.page_fingerprint,
        split_depth=chunk.split_depth + 1,
        config=config,
    )
    if not drafts:
        chunk.status = CrawlPageChunkStatus.FAILED.value
        chunk.last_error = f"chunk 太小，无法继续拆分：{reason}"
        return 0

    chunk.status = CrawlPageChunkStatus.SUPERSEDED.value
    chunk.split_reason = reason
    created = 0
    for draft in drafts:
        exists = await session.scalar(
            select(CrawlPageChunk.id).where(
                CrawlPageChunk.job_id == job_id,
                CrawlPageChunk.chunk_id == draft.chunk_id,
            )
        )
        if exists is not None:
            continue
        session.add(
            CrawlPageChunk(
                job_id=job_id,
                page_id=chunk.page_id,
                source_url=draft.source_url,
                page_fingerprint=draft.page_fingerprint,
                chunk_id=draft.chunk_id,
                parent_chunk_id=draft.parent_chunk_id,
                chunk_index=draft.chunk_index,
                chunk_hash=draft.chunk_hash,
                status=CrawlPageChunkStatus.PENDING.value,
                content=draft.content,
                token_estimate=draft.token_estimate,
                text_start_offset=draft.text_start_offset,
                text_end_offset=draft.text_end_offset,
                overlap_prefix=draft.overlap_prefix,
                overlap_suffix=draft.overlap_suffix,
                split_depth=draft.split_depth,
            )
        )
        created += 1
    return created


async def has_chunks_for_source_url(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    source_url: str,
) -> bool:
    async with session_factory() as session:
        chunk_id = await session.scalar(
            select(CrawlPageChunk.id)
            .where(
                CrawlPageChunk.job_id == job_id,
                CrawlPageChunk.source_url == source_url,
            )
            .limit(1)
        )
        return chunk_id is not None
