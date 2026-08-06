from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CrawlPageChunk, CrawlPageChunkStatus
from .chunking import ChunkingConfig, PageChunkDraft, estimate_tokens, split_chunk_content
from .fetch_ledger import mark_page_chunks_processed
from .tools import CrawlToolContext, ProfessorCandidatePayload, save_candidate_batch


@dataclass(frozen=True)
class ClaimedChunk:
    status: Literal["ok", "empty", "already_processed"]
    chunk_id: str | None = None
    source_url: str | None = None
    chunk_index: int | None = None
    content: str | None = None
    max_candidates: int = 10
    message: str | None = None

ACTIVE_CHUNK_STATUSES = frozenset(
    {
        CrawlPageChunkStatus.PENDING.value,
        CrawlPageChunkStatus.PROCESSING.value,
        CrawlPageChunkStatus.SPLIT_REQUIRED.value,
    }
)

AFTER_CHUNK_DONE_INSTRUCTION = (
    "不要再提交这个 chunk。请立即调用 claim_next_page_chunk 获取下一个待处理 chunk；"
    "如果 claim_next_page_chunk 返回 empty，再访问你刚才已明确发现的新分页 URL，或结束任务。"
)

RECLAIM_CHUNK_INSTRUCTION = (
    "不要猜测或复用 chunk_id。请立即调用 claim_next_page_chunk 重新领取当前可处理的 chunk，"
    "并只提交 claim_next_page_chunk 返回的 chunk_id。"
)

FIX_CURRENT_CANDIDATES_INSTRUCTION = (
    "请修正当前 candidates 的字段格式后，对同一个 chunk_id 重试 submit_page_chunk_candidates 一次；"
    "不要更换 chunk_id。若无法从当前 chunk 正文确认有效候选，请用 chunk_status=\"no_candidates\" 标记当前 chunk。"
)


async def create_chunks_for_page(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    page_id: int | None,
    drafts: list[PageChunkDraft],
) -> int:
    async with session_factory() as session:
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
            return ClaimedChunk(
                status="empty",
                message=(
                    "当前没有待处理页面片段。如已完成入口页及其明确分页/候选列表页，请结束任务并总结。"
                    "只有当你在最近处理内容中明确发现尚未访问的候选列表页 URL 时，才调用 crawl_page；不要重新访问已完成页面。"
                ),
            )
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


async def submit_page_chunk_candidates(
    ctx: CrawlToolContext,
    *,
    chunk_id: str,
    chunk_status: str,
    candidates: list[dict[str, object]],
    has_unsubmitted_candidates_in_current_chunk: bool = False,
    has_more_candidates_in_chunk: bool | None = None,
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
            return {
                "chunk_status": "failed",
                "message": "chunk 不存在，当前提交没有对应的已领取页面片段。",
                "next_instruction": RECLAIM_CHUNK_INSTRUCTION,
            }
        if chunk.status in {
            CrawlPageChunkStatus.COMPLETED.value,
            CrawlPageChunkStatus.NO_CANDIDATES.value,
            CrawlPageChunkStatus.SUPERSEDED.value,
        }:
            return {
                "chunk_status": "already_processed",
                "message": "当前 chunk 已经处理完成，重复调用 submit_page_chunk_candidates 不会产生任何效果。",
                "next_instruction": AFTER_CHUNK_DONE_INSTRUCTION,
            }
        if chunk.status != CrawlPageChunkStatus.PROCESSING.value:
            return {
                "chunk_status": "failed",
                "message": "该页面片段尚未领取或当前不可提交。",
                "next_instruction": RECLAIM_CHUNK_INSTRUCTION,
            }

    if chunk_status == "no_candidates":
        async with ctx.session_factory() as session:
            chunk = await session.scalar(
                select(CrawlPageChunk).where(
                    CrawlPageChunk.job_id == ctx.job_id,
                    CrawlPageChunk.chunk_id == chunk_id,
                )
            )
            source_url = chunk.source_url if chunk is not None else None
            if chunk is not None:
                chunk.status = CrawlPageChunkStatus.NO_CANDIDATES.value
                await session.commit()
        await _mark_source_processed_if_all_chunks_done(ctx, source_url)
        return {
            "chunk_status": CrawlPageChunkStatus.NO_CANDIDATES.value,
            "saved_count": 0,
            "merged_count": 0,
            "skipped_duplicate_count": 0,
            "rejected_count": 0,
            "next_instruction": AFTER_CHUNK_DONE_INSTRUCTION,
        }

    enriched_candidates = [
        {
            **candidate,
            "source_chunk_id": chunk_id,
            "source_kind": "page_chunk",
            "boundary_risk": bool(candidate.get("boundary_risk", False)),
        }
        for candidate in candidates
    ]
    try:
        payloads = [ProfessorCandidatePayload.model_validate(candidate) for candidate in enriched_candidates]
    except ValidationError as exc:
        return {
            "chunk_status": "failed",
            "message": str(exc),
            "next_instruction": FIX_CURRENT_CANDIDATES_INSTRUCTION,
        }

    save_result = await save_candidate_batch(ctx, payloads)
    should_split = chunk_status == "too_many_candidates"
    legacy_more_flag = bool(has_more_candidates_in_chunk)
    if should_split:
        split_result = await _mark_chunk_split_required(ctx, chunk_id, "too_many_candidates")
        return {**save_result, **split_result}

    async with ctx.session_factory() as session:
        chunk = await session.scalar(
            select(CrawlPageChunk).where(
                CrawlPageChunk.job_id == ctx.job_id,
                CrawlPageChunk.chunk_id == chunk_id,
            )
        )
        source_url = chunk.source_url if chunk is not None else None
        if chunk is not None:
            chunk.status = CrawlPageChunkStatus.COMPLETED.value
            await session.commit()
    await _mark_source_processed_if_all_chunks_done(ctx, source_url)
    result = {**save_result, "chunk_status": CrawlPageChunkStatus.COMPLETED.value, "next_instruction": AFTER_CHUNK_DONE_INSTRUCTION}
    if legacy_more_flag or has_unsubmitted_candidates_in_current_chunk:
        result["warning"] = "只有 chunk_status=too_many_candidates 才会拆分当前 chunk；本次已按 completed 处理。"
    return result



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
                "message": "chunk 不存在，无法拆分。",
                "next_instruction": RECLAIM_CHUNK_INSTRUCTION,
            }
        child_count = await _split_chunk_in_session(session, ctx.job_id, chunk, reason)
        await session.commit()
        if child_count <= 0:
            return {
                "chunk_status": CrawlPageChunkStatus.FAILED.value,
                "split_reason": reason,
                "created_child_chunks": 0,
                "last_error": chunk.last_error,
                "next_instruction": "该 chunk 无法继续拆分。不要再提交这个 chunk；请立即调用 claim_next_page_chunk 获取下一个待处理 chunk。如果 claim_next_page_chunk 返回 empty，请访问已明确发现的新分页 URL，或结束任务。",
            }
        return {
            "chunk_status": CrawlPageChunkStatus.SPLIT_REQUIRED.value,
            "split_reason": reason,
            "created_child_chunks": child_count,
            "next_instruction": "父 chunk 已拆分并不再可提交；不要再提交父 chunk。请立即调用 claim_next_page_chunk 领取拆分后的子 chunk。",
        }


async def split_page_chunk_for_retry(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    chunk_pk: int,
    reason: str,
) -> dict[str, Any]:
    async with session_factory() as session:
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
            return {"status": CrawlPageChunkStatus.FAILED_TERMINAL.value, "child_count": 0, "split_reason": reason}
        return {"status": CrawlPageChunkStatus.SPLIT_REQUIRED.value, "child_count": child_count, "split_reason": reason}

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


async def _mark_source_processed_if_all_chunks_done(ctx: CrawlToolContext, source_url: str | None) -> None:
    if not source_url:
        return
    state = await get_source_url_chunk_state(
        ctx.session_factory,
        job_id=ctx.job_id,
        source_url=source_url,
    )
    if state == "completed":
        await mark_page_chunks_processed(
            ctx.session_factory,
            job_id=ctx.job_id,
            source_url=source_url,
        )


async def get_source_url_chunk_state(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    source_url: str,
) -> str | None:
    async with session_factory() as session:
        statuses = list(
            await session.scalars(
                select(distinct(CrawlPageChunk.status)).where(
                    CrawlPageChunk.job_id == job_id,
                    CrawlPageChunk.source_url == source_url,
                )
            )
        )
    if not statuses:
        return None
    if any(status in ACTIVE_CHUNK_STATUSES for status in statuses):
        return "active"
    if all(
        status
        in {
            CrawlPageChunkStatus.COMPLETED.value,
            CrawlPageChunkStatus.NO_CANDIDATES.value,
            CrawlPageChunkStatus.SUPERSEDED.value,
        }
        for status in statuses
    ):
        return "completed"
    return "active"

async def source_urls_have_chunks(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    source_urls: set[str],
) -> set[str]:
    if not source_urls:
        return set()
    async with session_factory() as session:
        rows = await session.scalars(
            select(distinct(CrawlPageChunk.source_url)).where(
                CrawlPageChunk.job_id == job_id,
                CrawlPageChunk.source_url.in_(source_urls),
            )
        )
        return set(rows)
