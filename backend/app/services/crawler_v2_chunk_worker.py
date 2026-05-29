from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    CrawlCandidate,
    CrawlCandidateEnrichmentTask,
    CrawlCandidateEnrichmentTaskStatus,
    CrawlJob,
    CrawlPageChunk,
    CrawlPageChunkStatus,
    CrawlPageTask,
    CrawlPageTaskStatus,
)
from app.services.crawler_tools import ProfessorCandidatePayload
from app.services.crawler_v2_scheduler import ensure_job_active
from app.services.crawler_v2_url_utils import is_same_domain, normalize_url



async def run_crawler_v2_chunk_worker_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    chunk_id: int,
    worker_id: str,
) -> int:
    async with session_factory() as session:
        chunk = await session.get(CrawlPageChunk, chunk_id)
        if chunk is None or chunk.status != CrawlPageChunkStatus.PROCESSING.value or chunk.worker_id != worker_id:
            return 0
        if not await ensure_job_active(session, chunk.job_id):
            return 0
        job = await session.get(CrawlJob, chunk.job_id)
        if job is None:
            return 0
        llm_profile = await _resolve_llm_profile(session, job)
        if llm_profile is None:
            chunk.status = CrawlPageChunkStatus.FAILED_RETRYABLE.value
            chunk.last_error = "缺少可用的 LLM Profile"
            chunk.worker_id = None
            chunk.claimed_at = None
            chunk.lease_expires_at = None
            await session.commit()
            return 1
        ctx = CrawlToolContext(
            session_factory=session_factory,
            job_id=job.id,
            university=job.university,
            school=job.school,
            start_url=job.start_url,
        )
    try:
        from app.agents.faculty_crawler_agent import CrawlerAgentRunBudget
        from app.services.crawl_job_runtime import run_faculty_crawler_agent

        await run_faculty_crawler_agent(
            ctx,
            llm_profile,
            run_budget=CrawlerAgentRunBudget(max_completed_chunks=1),
        )
        return 1
    except Exception as exc:
        async with session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            if chunk is not None:
                chunk.last_error = str(exc)
                chunk.status = (
                    CrawlPageChunkStatus.FAILED_TERMINAL.value
                    if int(chunk.attempt_count or 0) >= MAX_CHUNK_ATTEMPTS
                    else CrawlPageChunkStatus.FAILED_RETRYABLE.value
                )
                chunk.worker_id = None
                chunk.claimed_at = None
                chunk.lease_expires_at = None
            await session.commit()
        return 1


async def _resolve_llm_profile(session: AsyncSession, job: CrawlJob):
    from app.models import LLMProfile

    if job.llm_profile_id is not None:
        return await session.get(LLMProfile, job.llm_profile_id)
    return await session.scalar(
        select(LLMProfile)
        .where(LLMProfile.is_default.is_(True))
        .order_by(LLMProfile.id.asc())
        .limit(1)
    )


def candidate_needs_enrichment(candidate: CrawlCandidate) -> bool:
    return bool(
        (candidate.profile_url or "").strip()
        and (
            not (candidate.email or "").strip()
            or not (candidate.department or "").strip()
            or not (candidate.research_direction or "").strip()
        )
    )


async def complete_current_chunk(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    chunk_id: int,
    worker_id: str,
    candidates: Sequence[ProfessorCandidatePayload],
    discovered_urls: Sequence[str],
    chunk_status: str,
) -> dict[str, int | str]:
    async with session_factory() as session:
        chunk = await session.get(CrawlPageChunk, chunk_id)
        if chunk is None:
            return {"status": "missing", "saved_count": 0, "url_count": 0, "enrichment_count": 0}
        if chunk.status != CrawlPageChunkStatus.PROCESSING.value or chunk.worker_id != worker_id:
            return {"status": "not_claimed", "saved_count": 0, "url_count": 0, "enrichment_count": 0}
        if not await ensure_job_active(session, chunk.job_id):
            return {"status": "inactive", "saved_count": 0, "url_count": 0, "enrichment_count": 0}
        job = await session.get(CrawlJob, chunk.job_id)
        if job is None:
            return {"status": "missing_job", "saved_count": 0, "url_count": 0, "enrichment_count": 0}

        saved_candidates: list[CrawlCandidate] = []
        for payload in candidates:
            candidate = CrawlCandidate(
                job_id=chunk.job_id,
                name=payload.name.strip(),
                email=_clean(payload.email),
                title=_clean(payload.title),
                university=_clean(payload.university) or job.university,
                school=_clean(payload.school) or job.school,
                department=_clean(payload.department),
                research_direction=_clean(payload.research_direction),
                recent_papers=payload.recent_papers,
                profile_url=_clean(payload.profile_url),
                source_url=_clean(payload.source_url) or chunk.source_url,
                confidence=payload.confidence,
                field_confidence=payload.field_confidence,
                evidence=payload.evidence,
                source_chunk_id=chunk.chunk_id,
                source_kind="chunk",
                boundary_risk=payload.boundary_risk,
                identity_key=payload.identity_key,
                merge_history=payload.merge_history,
                field_sources=payload.field_sources,
                conflicts=payload.conflicts,
            )
            session.add(candidate)
            saved_candidates.append(candidate)
        await session.flush()

        enrichment_count = 0
        for candidate in saved_candidates:
            if not candidate_needs_enrichment(candidate):
                continue
            exists = await session.scalar(
                select(CrawlCandidateEnrichmentTask.id).where(
                    CrawlCandidateEnrichmentTask.job_id == chunk.job_id,
                    CrawlCandidateEnrichmentTask.candidate_id == candidate.id,
                )
            )
            if exists is not None:
                continue
            session.add(
                CrawlCandidateEnrichmentTask(
                    job_id=chunk.job_id,
                    candidate_id=candidate.id,
                    status=CrawlCandidateEnrichmentTaskStatus.PENDING.value,
                )
            )
            enrichment_count += 1

        url_count = 0
        for url in discovered_urls:
            normalized = normalize_url(url, base_url=chunk.source_url)
            if not is_same_domain(normalized, job.start_url):
                continue
            exists = await session.scalar(
                select(CrawlPageTask.id).where(
                    CrawlPageTask.job_id == chunk.job_id,
                    CrawlPageTask.normalized_url == normalized,
                )
            )
            if exists is not None:
                continue
            session.add(
                CrawlPageTask(
                    job_id=chunk.job_id,
                    normalized_url=normalized,
                    original_url=url,
                    status=CrawlPageTaskStatus.PENDING.value,
                )
            )
            url_count += 1

        chunk.status = _normalize_chunk_status(chunk_status)
        chunk.worker_id = None
        chunk.claimed_at = None
        chunk.lease_expires_at = None
        await session.commit()
        return {
            "status": "saved",
            "saved_count": len(saved_candidates),
            "url_count": url_count,
            "enrichment_count": enrichment_count,
        }


def _normalize_chunk_status(chunk_status: str) -> str:
    if chunk_status in {CrawlPageChunkStatus.COMPLETED.value, CrawlPageChunkStatus.NO_CANDIDATES.value, CrawlPageChunkStatus.SPLIT_REQUIRED.value}:
        return chunk_status
    return CrawlPageChunkStatus.COMPLETED.value


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None