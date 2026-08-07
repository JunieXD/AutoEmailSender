from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CrawlPageChunk, CrawlPageChunkStatus

from ..v2.lease import CrawlerV2ClaimFence, fence_crawler_v2_claim
from .chunking import ChunkingConfig, PageChunkDraft, estimate_tokens, split_chunk_content


async def create_chunks_for_page(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    page_id: int | None,
    drafts: list[PageChunkDraft],
    claim_fence: CrawlerV2ClaimFence | None = None,
) -> int:
    async with session_factory() as session:
        if claim_fence is not None and not await fence_crawler_v2_claim(
            session,
            claim_fence,
        ):
            await session.rollback()
            return 0
        created = 0
        seen_chunk_ids: set[str] = set()
        for draft in drafts:
            if draft.chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(draft.chunk_id)
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


async def split_page_chunk_for_retry(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    chunk_pk: int,
    reason: str,
    claim_fence: CrawlerV2ClaimFence | None = None,
) -> dict[str, Any]:
    async with session_factory() as session:
        if claim_fence is not None and not await fence_crawler_v2_claim(
            session,
            claim_fence,
        ):
            await session.rollback()
            return {"status": "claim_lost", "child_count": 0, "split_reason": reason}
        chunk = await session.get(CrawlPageChunk, chunk_pk)
        if chunk is None or chunk.job_id != job_id:
            return {"status": "missing_chunk", "child_count": 0, "split_reason": reason}
        child_count = await _split_chunk_in_session(session, job_id, chunk, reason)
        chunk.worker_id = None
        chunk.claimed_at = None
        chunk.lease_expires_at = None
        await session.commit()
        if child_count <= 0:
            chunk.status = CrawlPageChunkStatus.FAILED_TERMINAL.value
            await session.commit()
            return {
                "status": CrawlPageChunkStatus.FAILED_TERMINAL.value,
                "child_count": 0,
                "split_reason": reason,
            }
        return {
            "status": CrawlPageChunkStatus.SPLIT_REQUIRED.value,
            "child_count": child_count,
            "split_reason": reason,
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
        chunk.last_error = (
            "chunk_split_max_depth_exceeded "
            f"split_depth={chunk.split_depth} max_split_depth={config.max_split_depth} reason={reason}"
        )
        return 0

    token_estimate = estimate_tokens(chunk.content)
    if token_estimate <= config.min_split_tokens:
        chunk.status = CrawlPageChunkStatus.FAILED.value
        chunk.last_error = (
            "chunk_split_min_tokens_reached "
            f"token_estimate={token_estimate} min_split_tokens={config.min_split_tokens} reason={reason}"
        )
        return 0

    drafts = split_chunk_content(
        source_url=chunk.source_url,
        content=chunk.content,
        parent_chunk_id=chunk.chunk_id,
        page_fingerprint=chunk.page_fingerprint,
        split_depth=chunk.split_depth + 1,
        config=config,
        split_reason=reason,
    )
    if not drafts:
        chunk.status = CrawlPageChunkStatus.FAILED.value
        chunk.last_error = (
            "chunk_split_no_valid_children "
            f"token_estimate={token_estimate} split_depth={chunk.split_depth} reason={reason}"
        )
        return 0

    chunk.status = CrawlPageChunkStatus.SUPERSEDED.value
    chunk.split_reason = reason
    created = 0
    seen_chunk_ids: set[str] = set()
    for draft in drafts:
        if draft.chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(draft.chunk_id)
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
