from __future__ import annotations

from datetime import datetime

from app.core.time import as_utc_aware, utc_now

from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CrawlCandidate, CrawlCandidateEnrichmentTask, CrawlCandidateEnrichmentTaskStatus, CrawlJob, CrawlPage, CrawlWorkerKind, LLMProfile
from app.services.crawler_tools import CandidateEnrichmentPayload, CrawlToolContext, PageSnapshot, crawl_page_with_browser_fallback
from app.services.crawler_debug import append_crawler_v2_debug_event
from app.services.crawler_v2_retry import mark_crawler_v2_failed
from app.services.crawler_v2_scheduler import ensure_job_active
from app.services.crawler_v2_token_usage import record_crawler_v2_token_usage
from app.services.crawl_job_runs import extract_token_usage_from_llm_response
from app.services.thinking_adaptation import ensure_thinking_adaptation


_PROFILE_TEXT_CACHE: dict[tuple[int, int, int, str], str] = {}


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
        raw_model_text = None
        if isinstance(enrichment_result, tuple):
            if len(enrichment_result) >= 3:
                payload, usage, raw_model_text = enrichment_result[:3]
            else:
                payload, usage = enrichment_result
        else:
            payload = enrichment_result
            usage = None
        if not await _enrichment_task_can_commit(session_factory, task_id=task_id, worker_id=worker_id):
            return 0
        append_crawler_v2_debug_event(
            job_id,
            worker_kind="enrichment",
            event_name="llm_response",
            work_item_id=task_id,
            payload={
                "candidate_id": candidate.id,
                "profile_url": candidate.profile_url,
                "raw_payload": payload.model_dump() if hasattr(payload, "model_dump") else payload,
                "raw_model_text": raw_model_text,
                "token_usage": dict(usage) if usage is not None else None,
            },
        )
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
            append_crawler_v2_debug_event(
                task.job_id,
                worker_kind="enrichment",
                event_name="enrichment_completed",
                work_item_id=task_id,
                payload={"candidate_id": candidate.id, "email": candidate.email, "department": candidate.department},
            )
            task.status = CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value
            task.worker_id = None
            task.claimed_at = None
            task.lease_expires_at = None
            await _append_enrichment_success_event(session, task=task, candidate=candidate)
            await session.commit()
        return 1
    except Exception as exc:
        async with session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            candidate = await session.get(CrawlCandidate, task.candidate_id) if task is not None else None
            if task is not None and _enrichment_task_owned_by_worker(task, worker_id) and await ensure_job_active(session, task.job_id):
                mark_crawler_v2_failed(
                    task,
                    message=str(exc),
                    retryable_status=CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value,
                    terminal_status=CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
                )
                await _append_enrichment_failure_event(session, task=task, candidate=candidate, error_message=str(exc))
            await session.commit()
        return 1


async def enrich_candidate_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    candidate_id: int,
) -> CandidateEnrichmentPayload:
    result = await enrich_candidate_once_with_usage(session_factory, candidate_id=candidate_id)
    return result[0]

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
) -> tuple[CandidateEnrichmentPayload, dict[str, int | None] | None, str | None]:
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
        thinking_extra_body = await ensure_thinking_adaptation(session, llm_profile)
        ctx = CrawlToolContext(
            session_factory=session_factory,
            job_id=job.id,
            university=job.university,
            school=job.school,
            start_url=job.start_url,
            thinking_extra_body=thinking_extra_body,
        )
        profile_url = candidate.profile_url or ""
    page_text = await get_or_fetch_profile_text(ctx, candidate.id, profile_url)
    return await enrich_candidate_profile_with_llm_with_usage(ctx, llm_profile, candidate, page_text)

async def enrich_candidate_profile_with_llm_with_usage(
    ctx: CrawlToolContext,
    llm_profile: LLMProfile,
    candidate: CrawlCandidate,
    page_text: str,
) -> tuple[CandidateEnrichmentPayload, dict[str, int | None] | None, str | None]:
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
                return payload, extract_token_usage_from_llm_response(response), content
            except LLMRuntimeError as exc:
                last_error = exc
        if attempt + 1 >= DIRECT_LLM_STRUCTURED_MAX_ATTEMPTS:
            break
        current_prompt = _build_structured_retry_prompt(original_prompt=prompt, parse_error=str(last_error))
    if last_error is None:
        raise ValueError("模型补全返回空响应")
    raise ValueError(f"模型补全返回空响应: {last_error}")


async def fetch_profile_text(ctx: CrawlToolContext, profile_url: str) -> str:
    snapshot: PageSnapshot = await crawl_page_with_browser_fallback(ctx, profile_url, intent="profile")
    if snapshot.status != "succeeded":
        raise ValueError(snapshot.error_message or "详情页抓取失败")
    return snapshot.text or snapshot.html


async def get_or_fetch_profile_text(ctx: CrawlToolContext, candidate_id: int, profile_url: str) -> str:
    cache_key = (id(ctx.session_factory), ctx.job_id, candidate_id, profile_url.strip())
    if cache_key in _PROFILE_TEXT_CACHE:
        return _PROFILE_TEXT_CACHE[cache_key]
    stored = await _load_successful_profile_text(ctx, profile_url)
    if stored:
        _PROFILE_TEXT_CACHE[cache_key] = stored
        return stored
    page_text = await fetch_profile_text(ctx, profile_url)
    _PROFILE_TEXT_CACHE[cache_key] = page_text
    return page_text


async def _load_successful_profile_text(ctx: CrawlToolContext, profile_url: str) -> str | None:
    if not profile_url.strip():
        return None
    async with ctx.session_factory() as session:
        page = await session.scalar(
            select(CrawlPage)
            .where(
                CrawlPage.job_id == ctx.job_id,
                CrawlPage.url == profile_url,
                CrawlPage.status == "succeeded",
                CrawlPage.text_excerpt.is_not(None),
            )
            .order_by(CrawlPage.created_at.desc(), CrawlPage.id.desc())
            .limit(1)
        )
    if page is None or not page.text_excerpt:
        return None
    return page.text_excerpt


async def _append_enrichment_failure_event(
    session: AsyncSession,
    *,
    task: CrawlCandidateEnrichmentTask,
    candidate: CrawlCandidate | None,
    error_message: str,
) -> None:
    job = await session.get(CrawlJob, task.job_id)
    if job is None:
        return
    candidate_name = candidate.name if candidate is not None and candidate.name else "未知导师"
    trace = list(job.agent_trace or [])
    trace.append(
        {
            "event_type": "enrichment",
            "message": f"候选导师详情补全失败：{candidate_name}",
            "created_at": utc_now().isoformat(),
            "raw": {
                "candidate_id": task.candidate_id,
                "task_id": task.id,
                "status": "failed",
                "task_status": task.status,
                "attempt_count": int(task.attempt_count or 0),
                "error_message": error_message,
            },
        }
    )
    job.agent_trace = trace[-100:]


async def _append_enrichment_success_event(
    session: AsyncSession,
    *,
    task: CrawlCandidateEnrichmentTask,
    candidate: CrawlCandidate,
) -> None:
    job = await session.get(CrawlJob, task.job_id)
    if job is None:
        return
    candidate_name = candidate.name if candidate.name else "未知导师"
    trace = list(job.agent_trace or [])
    trace.append(
        {
            "event_type": "enrichment",
            "message": f"候选导师详情补全成功：{candidate_name}",
            "created_at": utc_now().isoformat(),
            "raw": {
                "candidate_id": task.candidate_id,
                "task_id": task.id,
                "status": "succeeded",
                "task_status": task.status,
            },
        }
    )
    job.agent_trace = trace[-100:]


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
