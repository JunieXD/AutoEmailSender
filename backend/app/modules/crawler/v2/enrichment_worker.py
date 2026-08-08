from __future__ import annotations

from datetime import datetime

from app.core.time import as_utc_aware, utc_now

from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    CrawlCandidate,
    CrawlCandidateEnrichmentTask,
    CrawlCandidateEnrichmentTaskStatus,
    CrawlJob,
    CrawlJobKind,
    CrawlJobStatus,
    CrawlPage,
    CrawlPageChunk,
    CrawlWorkerKind,
    LLMProfile,
)
from ..pages.tools import (
    CandidateEnrichmentPayload,
    CrawlToolContext,
    PageSnapshot,
    build_candidate_enrichment_prompt,
    crawl_page_with_browser_fallback,
    profile_text_has_meaningful_content,
    validate_safe_public_crawl_url,
)
from ..jobs.llm_context import resolve_crawl_job_runtime_profile
from ..pages.debug import append_crawler_v2_debug_event
from .profile_url_policy import (
    CandidateProfileUrlPolicyError,
    has_explicit_markdown_link,
)
from .retry import mark_crawler_v2_failed
from .profile_text_cache import profile_text_cache
from .scheduler import ensure_job_active
from .token_usage import record_crawler_v2_token_usage
from .lease import CrawlerV2ClaimFence, fence_crawler_v2_claim
from .models import CrawlerV2WorkKind
from .url_utils import is_same_domain
from ..jobs.runs import extract_token_usage_from_llm_response
from app.modules.llm.public import ensure_llm_runtime_adaptation
from ..llm.structured_output import (
    CandidateEnrichmentWirePayload,
    request_crawler_structured_completion,
)
from app.services.operation_logs import record_operation_log, sanitize_user_visible_error
from app.modules.professors.public import (
    MISSING_PROFILE_URL_SKIP_REASON,
    NO_NEW_INFORMATION_SKIP_REASON,
    apply_enrichment_to_professor,
)
from app.modules.crawler.candidate_identity import (
    apply_candidate_enrichment_values,
    consolidate_candidate_identity,
)
from app.modules.professors.public import normalize_recent_papers


_PROFILE_TEXT_CACHE = profile_text_cache
_ACTIVE_JOB_STATUSES = {
    CrawlJobStatus.QUEUED.value,
    CrawlJobStatus.RUNNING.value,
}
_TERMINAL_ENRICHMENT_TASK_STATUSES = {
    CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value,
    CrawlCandidateEnrichmentTaskStatus.SKIPPED.value,
    CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
    CrawlCandidateEnrichmentTaskStatus.CANCELED.value,
}
_TERMINAL_JOB_STATUSES = {
    CrawlJobStatus.NEEDS_REVIEW.value,
    CrawlJobStatus.PARTIALLY_COMPLETED.value,
    CrawlJobStatus.COMPLETED.value,
    CrawlJobStatus.FAILED.value,
    CrawlJobStatus.CANCELED.value,
}


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
            job_id = task.job_id
            candidate_id = task.candidate_id
            task.status = CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value
            task.last_error = "candidate_missing"
            task.finished_at = utc_now()
            await session.commit()
            _discard_cached_profile_text(
                session_factory,
                job_id=job_id,
                candidate_id=candidate_id,
            )
            return 1
        job = await session.get(CrawlJob, task.job_id)
        model_name = None
        if job is not None:
            profile = await _resolve_llm_profile(session, job)
            model_name = getattr(profile, "model_name", None) if profile is not None else None
        job_id = task.job_id
        candidate_id = candidate.id
        if not (candidate.profile_url or "").strip():
            task.status = CrawlCandidateEnrichmentTaskStatus.SKIPPED.value
            task.skip_reason = MISSING_PROFILE_URL_SKIP_REASON.legacy_message
            task.finished_at = utc_now()
            task.worker_id = None
            task.claimed_at = None
            task.lease_expires_at = None
            await session.commit()
            _discard_cached_profile_text(
                session_factory,
                job_id=job_id,
                candidate_id=candidate_id,
            )
            return 1

    try:
        enrichment_result = await enrich_candidate_once_with_usage(session_factory, candidate_id=candidate_id)
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
            await _discard_cached_profile_text_if_terminal(
                session_factory,
                task_id=task_id,
                job_id=job_id,
                candidate_id=candidate_id,
            )
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
                claim=CrawlerV2ClaimFence(
                    kind=CrawlerV2WorkKind.ENRICHMENT,
                    work_item_id=task_id,
                    worker_id=worker_id,
                ),
            )
        async with session_factory() as session:
            if not await fence_crawler_v2_claim(
                session,
                CrawlerV2ClaimFence(
                    kind=CrawlerV2WorkKind.ENRICHMENT,
                    work_item_id=task_id,
                    worker_id=worker_id,
                ),
            ):
                await session.rollback()
                await _discard_cached_profile_text_if_terminal(
                    session_factory,
                    task_id=task_id,
                    job_id=job_id,
                    candidate_id=candidate_id,
                )
                return 0
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            current_candidate = await session.get(CrawlCandidate, candidate_id)
            if task is None or current_candidate is None:
                _discard_cached_profile_text(
                    session_factory,
                    job_id=job_id,
                    candidate_id=candidate_id,
                )
                return 0
            job = await session.get(CrawlJob, task.job_id)
            if (
                not _enrichment_task_owned_by_worker(task, worker_id)
                or job is None
                or job.status not in _ACTIVE_JOB_STATUSES
            ):
                if (
                    task.status in _TERMINAL_ENRICHMENT_TASK_STATUSES
                    or job is None
                    or job.status in _TERMINAL_JOB_STATUSES
                ):
                    _discard_cached_profile_text(
                        session_factory,
                        job_id=task.job_id,
                        candidate_id=task.candidate_id,
                    )
                return 0
            candidate = current_candidate
            candidate_enriched_fields = _apply_enrichment(candidate, payload)
            await consolidate_candidate_identity(session, candidate)
            enriched_fields: list[str] = []
            skip_reason = None
            if job is not None and job.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value:
                enriched_fields, skip_reason = await apply_enrichment_to_professor(
                    session,
                    task=task,
                    candidate=candidate,
                )
                if not enriched_fields and skip_reason is None:
                    skip_reason = NO_NEW_INFORMATION_SKIP_REASON.legacy_message
            else:
                enriched_fields = candidate_enriched_fields
                if not enriched_fields:
                    skip_reason = NO_NEW_INFORMATION_SKIP_REASON.legacy_message
            append_crawler_v2_debug_event(
                task.job_id,
                worker_kind="enrichment",
                event_name="enrichment_completed",
                work_item_id=task_id,
                payload={
                    "candidate_id": candidate.id,
                    "professor_id": task.professor_id,
                    "email": candidate.email,
                    "title": candidate.title,
                    "department": candidate.department,
                    "enriched_fields": enriched_fields,
                    "skip_reason": skip_reason,
                },
            )
            task.status = (
                CrawlCandidateEnrichmentTaskStatus.SKIPPED.value
                if skip_reason is not None
                else CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value
            )
            task.worker_id = None
            task.claimed_at = None
            task.lease_expires_at = None
            task.last_error = None
            task.skip_reason = skip_reason
            task.enriched_fields = enriched_fields
            task.finished_at = utc_now()
            if skip_reason is None:
                await _append_enrichment_success_event(session, task=task, candidate=candidate)
            else:
                await _append_enrichment_unchanged_event(
                    session,
                    task=task,
                    candidate=candidate,
                    reason=skip_reason,
                )
            terminal_job_id = task.job_id
            terminal_candidate_id = task.candidate_id
            await session.commit()
        _discard_cached_profile_text(
            session_factory,
            job_id=terminal_job_id,
            candidate_id=terminal_candidate_id,
        )
        return 1
    except Exception as exc:
        terminal_cache_identity: tuple[int, int] | None = None
        async with session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            candidate = await session.get(CrawlCandidate, task.candidate_id) if task is not None else None
            if task is not None and _enrichment_task_owned_by_worker(task, worker_id) and await ensure_job_active(session, task.job_id):
                job = await session.get(CrawlJob, task.job_id)
                error_message = (
                    sanitize_user_visible_error(exc)
                    if job is not None
                    and job.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value
                    else str(exc)
                )
                mark_crawler_v2_failed(
                    task,
                    message=error_message,
                    retryable_status=CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value,
                    terminal_status=CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
                    max_attempts=(
                        1
                        if isinstance(exc, CandidateProfileUrlPolicyError)
                        else None
                    ),
                )
                if task.status == CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value:
                    task.finished_at = utc_now()
                    terminal_cache_identity = (task.job_id, task.candidate_id)
                await _append_enrichment_failure_event(
                    session,
                    task=task,
                    candidate=candidate,
                    error_message=error_message,
                )
                if job is not None and job.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value:
                    append_crawler_v2_debug_event(
                        task.job_id,
                        worker_kind="enrichment",
                        event_name="information_enrichment_failed",
                        work_item_id=task.id,
                        payload={
                            "candidate_id": task.candidate_id,
                            "professor_id": task.professor_id,
                            "task_status": task.status,
                            "attempt_count": int(task.attempt_count or 0),
                            "error_message": error_message,
                        },
                    )
                    await record_operation_log(
                        session,
                        category="professor_information_enrichment",
                        event_name="professor_information_enrichment.item_failed",
                        level=(
                            "error"
                            if task.status == CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value
                            else "warning"
                        ),
                        message=error_message,
                        entity_type="professor",
                        entity_id=str(task.professor_id) if task.professor_id is not None else None,
                        metadata={
                            "job_id": task.job_id,
                            "task_id": task.id,
                            "task_status": task.status,
                            "attempt_count": int(task.attempt_count or 0),
                        },
                    )
            await session.commit()
        if terminal_cache_identity is not None:
            terminal_job_id, terminal_candidate_id = terminal_cache_identity
            _discard_cached_profile_text(
                session_factory,
                job_id=terminal_job_id,
                candidate_id=terminal_candidate_id,
            )
        else:
            await _discard_cached_profile_text_if_terminal(
                session_factory,
                task_id=task_id,
                job_id=job_id,
                candidate_id=candidate_id,
            )
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
        profile_url = (candidate.profile_url or "").strip()
        profile_crawl_root = await _resolve_profile_crawl_root(
            session,
            candidate=candidate,
            job=job,
            profile_url=profile_url,
        )
        llm_profile = await _resolve_llm_profile(session, job)
        if llm_profile is None:
            raise ValueError("缺少可用的 LLM Profile")
        adaptation = await ensure_llm_runtime_adaptation(session, llm_profile)
        await session.commit()
        ctx = CrawlToolContext(
            session_factory=session_factory,
            job_id=job.id,
            university=job.university,
            school=job.school,
            start_url=profile_crawl_root,
            llm_adaptation=adaptation,
            allow_public_dns_fallback=True,
        )
    page_text = await get_or_fetch_profile_text(ctx, candidate.id, profile_url)
    return await enrich_candidate_profile_with_llm_with_usage(ctx, llm_profile, candidate, page_text)


async def _resolve_profile_crawl_root(
    session: AsyncSession,
    *,
    candidate: CrawlCandidate,
    job: CrawlJob,
    profile_url: str,
) -> str:
    try:
        validate_safe_public_crawl_url(profile_url)
    except ValueError as exc:
        raise CandidateProfileUrlPolicyError(str(exc)) from exc

    if job.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value:
        return profile_url
    if is_same_domain(profile_url, job.start_url):
        return job.start_url

    chunks = list(
        await session.scalars(
            select(CrawlPageChunk).where(CrawlPageChunk.job_id == candidate.job_id)
        )
    )
    if any(
        has_explicit_markdown_link(
            chunk.content,
            base_url=chunk.source_url,
            target_url=profile_url,
        )
        for chunk in chunks
    ):
        return profile_url

    raise CandidateProfileUrlPolicyError(
        "跨主域导师主页未在来源列表原文中出现，已拒绝补全"
    )

async def enrich_candidate_profile_with_llm_with_usage(
    ctx: CrawlToolContext,
    llm_profile: LLMProfile,
    candidate: CrawlCandidate,
    page_text: str,
) -> tuple[CandidateEnrichmentPayload, dict[str, int | None] | None, str | None]:
    prompt = build_candidate_enrichment_prompt(candidate, page_text)
    completion, wire_payload, _structured_mode = await request_crawler_structured_completion(
        ctx.session_factory,
        llm_profile,
        ctx.llm_adaptation,
        prompt=prompt,
        result_model=CandidateEnrichmentWirePayload,
    )
    payload = CandidateEnrichmentPayload.model_validate(wire_payload.model_dump())
    return (
        payload,
        extract_token_usage_from_llm_response(completion),
        completion.content,
    )


async def fetch_profile_text(ctx: CrawlToolContext, profile_url: str) -> str:
    snapshot: PageSnapshot = await crawl_page_with_browser_fallback(ctx, profile_url, intent="profile")
    if snapshot.status != "succeeded":
        raise ValueError(snapshot.error_message or "详情页抓取失败")
    return snapshot.text or snapshot.html


async def get_or_fetch_profile_text(ctx: CrawlToolContext, candidate_id: int, profile_url: str) -> str:
    cache_key = (id(ctx.session_factory), ctx.job_id, candidate_id, profile_url.strip())
    cached = _PROFILE_TEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    stored = await _load_successful_profile_text(ctx, profile_url)
    if stored:
        _PROFILE_TEXT_CACHE.put(cache_key, stored)
        return stored
    page_text = await fetch_profile_text(ctx, profile_url)
    _PROFILE_TEXT_CACHE.put(cache_key, page_text)
    return page_text


def _discard_cached_profile_text(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    candidate_id: int,
) -> None:
    _PROFILE_TEXT_CACHE.discard_candidate(
        session_factory_id=id(session_factory),
        job_id=job_id,
        candidate_id=candidate_id,
    )


async def _discard_cached_profile_text_if_terminal(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    job_id: int,
    candidate_id: int,
) -> None:
    async with session_factory() as session:
        task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        job = await session.get(CrawlJob, job_id)
        should_discard = (
            task is None
            or task.status in _TERMINAL_ENRICHMENT_TASK_STATUSES
            or job is None
            or job.status in _TERMINAL_JOB_STATUSES
        )
    if should_discard:
        _discard_cached_profile_text(
            session_factory,
            job_id=job_id,
            candidate_id=candidate_id,
        )


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
    if (
        page is None
        or not page.text_excerpt
        or not profile_text_has_meaningful_content(page.text_excerpt)
    ):
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
    trace = [
        item
        for item in list(job.agent_trace or [])
        if not _is_previous_failed_enrichment_event(item, task=task, candidate_name=candidate_name)
    ]
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


async def _append_enrichment_unchanged_event(
    session: AsyncSession,
    *,
    task: CrawlCandidateEnrichmentTask,
    candidate: CrawlCandidate,
    reason: str,
) -> None:
    job = await session.get(CrawlJob, task.job_id)
    if job is None:
        return
    candidate_name = candidate.name if candidate.name else "未知导师"
    trace = list(job.agent_trace or [])
    trace.append(
        {
            "event_type": "enrichment",
            "message": f"候选导师详情未发现新信息：{candidate_name}",
            "created_at": utc_now().isoformat(),
            "raw": {
                "candidate_id": task.candidate_id,
                "task_id": task.id,
                "status": "skipped",
                "task_status": CrawlCandidateEnrichmentTaskStatus.SKIPPED.value,
                "reason": reason,
            },
        }
    )
    job.agent_trace = trace[-100:]


def _is_previous_failed_enrichment_event(
    event: object,
    *,
    task: CrawlCandidateEnrichmentTask,
    candidate_name: str,
) -> bool:
    if not isinstance(event, dict):
        return False
    if event.get("event_type") != "enrichment":
        return False
    raw = event.get("raw")
    if isinstance(raw, dict) and raw.get("status") == "failed":
        if raw.get("task_id") == task.id:
            return True
        if raw.get("candidate_id") == task.candidate_id:
            return True
    return event.get("message") == f"候选导师详情补全失败：{candidate_name}"


async def _resolve_llm_profile(session: AsyncSession, job: CrawlJob) -> LLMProfile | None:
    return await resolve_crawl_job_runtime_profile(session, job)  # type: ignore[return-value]


def _apply_enrichment(
    candidate: CrawlCandidate,
    payload: CandidateEnrichmentPayload,
) -> list[str]:
    field_names = (
        "email",
        "title",
        "department",
        "research_direction",
        "recent_papers",
    )
    before = {field_name: getattr(candidate, field_name) for field_name in field_names}
    apply_candidate_enrichment_values(candidate, payload.model_dump())
    return [
        field_name
        for field_name in field_names
        if before[field_name] != getattr(candidate, field_name)
    ]
