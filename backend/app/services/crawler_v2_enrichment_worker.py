from __future__ import annotations

from datetime import datetime
from sqlalchemy import select

from app.core.time import as_utc_aware, utc_now
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CrawlCandidate, CrawlCandidateEnrichmentTask, CrawlCandidateEnrichmentTaskStatus, CrawlJob, CrawlWorkerKind, LLMProfile
from app.services.crawler_tools import CandidateEnrichmentPayload, CrawlToolContext, PageSnapshot, crawl_page_with_crawl4ai
from app.services.crawler_v2_scheduler import ensure_job_active
from app.services.crawler_v2_token_usage import record_crawler_v2_token_usage
from app.services.crawl_job_runs import extract_token_usage_from_llm_response

MAX_ENRICHMENT_ATTEMPTS = 3


async def run_crawler_v2_enrichment_worker_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    worker_id: str,
) -> int:
    async with session_factory() as session:
        task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        if task is None or not _enrichment_task_owned_by_worker(task, worker_id):
            return 0
        if not await ensure_job_active(session, task.job_id):
            return 0
        candidate = await session.get(CrawlCandidate, task.candidate_id)
        if candidate is None:
            task.status = CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value
            task.last_error = "candidate_missing"
            await session.commit()
            return 1
        job = await session.get(CrawlJob, task.job_id)
        model_name = None
        if job is not None:
            profile = await _resolve_llm_profile(session, job)
            model_name = getattr(profile, "model_name", None) if profile is not None else None
        job_id = task.job_id
        if not (candidate.profile_url or "").strip():
            task.status = CrawlCandidateEnrichmentTaskStatus.SKIPPED.value
            task.worker_id = None
            task.claimed_at = None
            task.lease_expires_at = None
            await session.commit()
            return 1

    try:
        enrichment_result = await enrich_candidate_once_with_usage(session_factory, candidate_id=candidate.id)
        if isinstance(enrichment_result, tuple):
            payload, usage = enrichment_result
        else:
            payload = enrichment_result
            usage = None
        if not await _enrichment_task_can_commit(session_factory, task_id=task_id, worker_id=worker_id):
            return 0
        if usage is not None:
            await record_crawler_v2_token_usage(
                session_factory,
                job_id=job_id,
                worker_kind=CrawlWorkerKind.ENRICHMENT,
                work_item_id=task_id,
                model_name=model_name,
                input_tokens=usage.get("input_tokens") or 0,
                output_tokens=usage.get("output_tokens") or 0,
                cached_tokens=usage.get("cached_tokens") or 0,
                raw_usage=dict(usage),
            )
        async with session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            candidate = await session.get(CrawlCandidate, candidate.id)
            if task is None or candidate is None or not _enrichment_task_owned_by_worker(task, worker_id):
                return 0
            if not await ensure_job_active(session, task.job_id):
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
            if task is not None and _enrichment_task_owned_by_worker(task, worker_id) and await ensure_job_active(session, task.job_id):
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
    payload, _ = await enrich_candidate_once_with_usage(session_factory, candidate_id=candidate_id)
    return payload

async def _enrichment_task_can_commit(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    worker_id: str,
) -> bool:
    async with session_factory() as session:
        task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        if task is None or not _enrichment_task_owned_by_worker(task, worker_id):
            return False
        return await ensure_job_active(session, task.job_id)


def _enrichment_task_owned_by_worker(task: CrawlCandidateEnrichmentTask, worker_id: str) -> bool:
    if task.status != CrawlCandidateEnrichmentTaskStatus.PROCESSING.value or task.worker_id != worker_id:
        return False
    if task.lease_expires_at is None:
        return True
    return as_utc_aware(task.lease_expires_at) > utc_now()

async def enrich_candidate_once_with_usage(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    candidate_id: int,
) -> tuple[CandidateEnrichmentPayload, dict[str, int | None] | None]:
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
    payload, usage = await enrich_candidate_profile_with_llm_with_usage(ctx, llm_profile, candidate, page_text)
    return payload, usage

async def enrich_candidate_profile_with_llm_with_usage(
    ctx: CrawlToolContext,
    llm_profile: LLMProfile,
    candidate: CrawlCandidate,
    page_text: str,
) -> tuple[CandidateEnrichmentPayload, dict[str, int | None] | None]:
    from app.services.crawl_job_runtime import build_candidate_enrichment_prompt
    from app.services.crawl_job_runtime import _extract_model_message_content, _build_structured_retry_prompt
    from app.services.crawl_job_runtime import DIRECT_LLM_STRUCTURED_MAX_ATTEMPTS
    from app.services.crawl_job_runtime import build_faculty_crawler_model
    from app.services.llm_runtime import LLMRuntimeError, parse_structured_result

    model = build_faculty_crawler_model(llm_profile, extra_body=ctx.thinking_extra_body)
    prompt = build_candidate_enrichment_prompt(candidate, page_text)
    current_prompt = prompt
    last_error: Exception | None = None
    last_response: object | None = None
    for attempt in range(DIRECT_LLM_STRUCTURED_MAX_ATTEMPTS):
        response = await model.ainvoke(current_prompt)
        last_response = response
        content = _extract_model_message_content(response)
        if not content:
            last_error = ValueError("模型补全返回空响应")
        else:
            try:
                payload = parse_structured_result(content, CandidateEnrichmentPayload)
                return payload, extract_token_usage_from_llm_response(response)
            except LLMRuntimeError as exc:
                last_error = exc
        if attempt + 1 >= DIRECT_LLM_STRUCTURED_MAX_ATTEMPTS:
            break
        current_prompt = _build_structured_retry_prompt(original_prompt=prompt, parse_error=str(last_error))
    if last_error is None:
        raise ValueError("模型补全返回空响应")
    raise ValueError(f"模型补全返回空响应: {last_error}")


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