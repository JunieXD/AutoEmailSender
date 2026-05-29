from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CrawlCandidate, CrawlCandidateEnrichmentTask, CrawlCandidateEnrichmentTaskStatus, CrawlJob, LLMProfile
from app.services.crawler_tools import CandidateEnrichmentPayload, CrawlToolContext, PageSnapshot, crawl_page_with_crawl4ai
from app.services.crawler_v2_scheduler import ensure_job_active
from app.services.crawl_job_runtime import enrich_candidate_profile_with_llm

MAX_ENRICHMENT_ATTEMPTS = 3


async def run_crawler_v2_enrichment_worker_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    worker_id: str,
) -> int:
    async with session_factory() as session:
        task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        if task is None or task.status != CrawlCandidateEnrichmentTaskStatus.PROCESSING.value or task.worker_id != worker_id:
            return 0
        if not await ensure_job_active(session, task.job_id):
            return 0
        candidate = await session.get(CrawlCandidate, task.candidate_id)
        if candidate is None:
            task.status = CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value
            task.last_error = "candidate_missing"
            await session.commit()
            return 1
        if not (candidate.profile_url or "").strip():
            task.status = CrawlCandidateEnrichmentTaskStatus.SKIPPED.value
            task.worker_id = None
            task.claimed_at = None
            task.lease_expires_at = None
            await session.commit()
            return 1

    try:
        payload = await enrich_candidate_once(session_factory, candidate_id=candidate.id)
        async with session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            candidate = await session.get(CrawlCandidate, candidate.id)
            if task is None or candidate is None:
                return 0
            _apply_enrichment(candidate, payload)
            task.status = CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value
            task.worker_id = None
            task.claimed_at = None
            task.lease_expires_at = None
            await session.commit()
        return 1
    except Exception as exc:
        async with session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            if task is not None:
                task.last_error = str(exc)
                task.status = (
                    CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value
                    if int(task.attempt_count or 0) >= MAX_ENRICHMENT_ATTEMPTS
                    else CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value
                )
                task.worker_id = None
                task.claimed_at = None
                task.lease_expires_at = None
            await session.commit()
        return 1


async def enrich_candidate_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    candidate_id: int,
) -> CandidateEnrichmentPayload:
    async with session_factory() as session:
        candidate = await session.get(CrawlCandidate, candidate_id)
        if candidate is None:
            raise ValueError("candidate_missing")
        job = await session.get(CrawlJob, candidate.job_id)
        if job is None:
            raise ValueError("job_missing")
        llm_profile = await _resolve_llm_profile(session, job)
        if llm_profile is None:
            raise ValueError("缺少可用的 LLM Profile")
        ctx = CrawlToolContext(
            session_factory=session_factory,
            job_id=job.id,
            university=job.university,
            school=job.school,
            start_url=job.start_url,
        )
        profile_url = candidate.profile_url or ""
    page_text = await fetch_profile_text(ctx, profile_url)
    return await enrich_candidate_profile_with_llm(ctx, llm_profile, candidate, page_text)


async def fetch_profile_text(ctx: CrawlToolContext, profile_url: str) -> str:
    snapshot: PageSnapshot = await crawl_page_with_crawl4ai(ctx, profile_url, intent="profile")
    if snapshot.status != "succeeded":
        raise ValueError(snapshot.error_message or "详情页抓取失败")
    return snapshot.text or snapshot.html


async def _resolve_llm_profile(session: AsyncSession, job: CrawlJob) -> LLMProfile | None:
    if job.llm_profile_id is not None:
        return await session.get(LLMProfile, job.llm_profile_id)
    return await session.scalar(
        select(LLMProfile)
        .where(LLMProfile.is_default.is_(True))
        .order_by(LLMProfile.id.asc())
        .limit(1)
    )


def _apply_enrichment(candidate: CrawlCandidate, payload: CandidateEnrichmentPayload) -> None:
    if payload.email and not candidate.email:
        candidate.email = payload.email.strip()
    if payload.department and not candidate.department:
        candidate.department = payload.department.strip()
    if payload.research_direction and not candidate.research_direction:
        candidate.research_direction = payload.research_direction.strip()
    if payload.recent_papers and not candidate.recent_papers:
        candidate.recent_papers = payload.recent_papers