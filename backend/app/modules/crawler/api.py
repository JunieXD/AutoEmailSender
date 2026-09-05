from __future__ import annotations

from app.core.time import utc_now

# -*- coding: utf-8 -*-

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Float, String, case, cast, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_async_session
from app.core.query_chunks import chunked_values, unique_positive_ids
from app.schemas.selection import SelectionSpec
from app.models import (
    CrawlCandidate,
    CrawlCandidateReviewStatus,
    CrawlJob,
    CrawlJobKind,
    CrawlJobStatus,
    CrawlPage,
    CrawlPageChunk,
    CrawlPageChunkStatus,
    CrawlPageFetchState,
    CrawlPageTask,
    CrawlPageTaskStatus,
    CrawlCandidateEnrichmentTask,
    CrawlCandidateEnrichmentTaskStatus,
    CrawlWorkerTokenUsage,
    LLMProfile,
)
from app.modules.llm.public import get_active_llm_profile
from .schemas import (
    CrawlCandidateRead,
    CrawlCandidateUpdatePayload,
    CrawlJobEnrichPayload,
    CrawlJobEnrichResult,
    CrawlJobApprovePayload,
    CrawlJobApproveResult,
    CrawlJobCreatePayload,
    CrawlJobDetailsRead,
    CrawlJobEventRead,
    CrawlJobRead,
    CrawlJobStatusDTO,
    CrawlJobSummaryPageRead,
    CrawlJobSummaryRead,
    CrawlPageRead,
    CrawlJobRetryPayload,
    CrawlJobResumePayload,
)
from .jobs.events import build_crawl_job_events, normalize_agent_trace_event
from .jobs.query import (
    parse_crawl_task_search_scopes,
    query_crawl_task_center_jobs,
)
from .jobs.enrichment_operations import (
    append_candidate_enrichment_terminal_event,
    start_candidate_enrichment_operation,
)
from .candidate_identity import (
    candidate_identity_values,
    canonical_candidate_clause,
    canonicalize_candidate_ids,
    mark_candidate_fields_manual,
    rebuild_candidate_identity_keys,
)
from .jobs.metrics import build_crawl_job_metrics
from .jobs.runs import (
    create_initial_crawl_job_run,
    create_retry_crawl_job_run,
    mark_crawl_job_run_finished,
    mark_crawl_job_run_paused,
    mark_crawl_job_run_queued,
    mark_crawl_job_run_running,
)
from .jobs.records import (
    CrawlJobRecordError,
    approve_faculty_crawl_candidates,
    cancel_faculty_crawl_job_record,
    create_faculty_crawl_job_record,
    delete_faculty_crawl_job_record,
    enqueue_faculty_crawl_candidate_enrichment_records,
    get_faculty_crawl_job_summary,
    list_faculty_crawl_job_records,
    pause_faculty_crawl_job_record,
    restore_faculty_crawl_job_record,
    resume_faculty_crawl_job_record,
    resume_faculty_crawl_job_review_record,
    retry_faculty_crawl_job_record,
    update_faculty_crawl_candidate_record,
)
from app.services.operation_logs import record_operation_log
from app.modules.professors.public import (
    get_or_create_professor_by_email,
    is_valid_professor_email,
    normalize_professor_email,
    normalize_recent_papers,
)
from .runtime.url_utils import normalize_url
from .runtime.profile_text_cache import profile_text_cache
from .runtime.routing import (
    ENTRY_EXPANSION_MODE,
    NO_EXPANSION_MODE,
    START_DISCOVERY_REASON,
)


router = APIRouter(prefix="/api/crawl-jobs", tags=["crawl-jobs"])
CrawlJobListLimit = Annotated[int, Query(ge=1, le=50)]


def _raise_http_record_error(error: CrawlJobRecordError) -> None:
    message = {
        "CRAWL_CANDIDATE_ENRICHMENT_RUNNING": "候选信息正在补全中，请稍后再试",
        "CRAWL_CANDIDATE_ENRICHMENT_NOT_REVIEWABLE": "抓取任务尚未进入审核状态",
    }.get(error.code, error.message)
    raise HTTPException(status_code=error.status_code, detail=message) from error


@router.post("", response_model=CrawlJobRead, status_code=status.HTTP_201_CREATED)
async def create_crawl_job(
    payload: CrawlJobCreatePayload,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJob:
    try:
        job = await create_faculty_crawl_job_record(session, payload)
    except CrawlJobRecordError as exc:
        _raise_http_record_error(exc)
    await session.commit()
    await session.refresh(job)
    return job


@router.get("", response_model=list[CrawlJobSummaryRead])
async def list_crawl_jobs(
    limit: CrawlJobListLimit = 50,
    view: str = "current",
    session: AsyncSession = Depends(get_async_session),
) -> list[CrawlJobSummaryRead]:
    try:
        return await list_faculty_crawl_job_records(
            session,
            view=view,
            offset=0,
            limit=limit,
        )
    except CrawlJobRecordError as exc:
        _raise_http_record_error(exc)


@router.get("/page", response_model=CrawlJobSummaryPageRead)
async def list_crawl_jobs_page(
    view: Literal["current", "trash"] = Query(default="current"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=8, ge=1, le=100),
    keyword: str | None = Query(default=None, max_length=200),
    search_scopes: str | None = Query(default=None),
    status_filter: CrawlJobStatusDTO | None = Query(default=None, alias="status"),
    sort_key: Literal["updated", "created", "progress"] = Query(default="created"),
    sort_direction: Literal["asc", "desc"] = Query(default="desc"),
    unpaged: bool = Query(default=False),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryPageRead:
    try:
        scopes = parse_crawl_task_search_scopes(search_scopes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    jobs, total_count = await query_crawl_task_center_jobs(
        session,
        view=view,
        offset=offset,
        limit=limit,
        keyword=keyword,
        search_scopes=scopes,
        status_filter=status_filter,
        sort_key=sort_key,
        sort_direction=sort_direction,
        unpaged=unpaged,
    )
    if view == "current" and keyword is None and status_filter is None:
        current_total_count = total_count
    else:
        current_total_count = int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(CrawlJob)
                    .where(
                        CrawlJob.job_kind == CrawlJobKind.FACULTY_CRAWL.value,
                        CrawlJob.deleted_at.is_(None),
                    )
                )
            )
            or 0
        )
    return CrawlJobSummaryPageRead(
        items=await _build_crawl_job_summaries(session, jobs),
        total_count=total_count,
        current_total_count=current_total_count,
    )


@router.patch("/candidates/{candidate_id}", response_model=CrawlCandidateRead)
async def update_crawl_candidate(
    candidate_id: int,
    payload: CrawlCandidateUpdatePayload,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlCandidateRead:
    try:
        candidate = await update_faculty_crawl_candidate_record(
            session,
            candidate_id,
            payload,
        )
    except CrawlJobRecordError as exc:
        _raise_http_record_error(exc)
    await session.commit()
    return candidate


@router.get("/{job_id}", response_model=CrawlJobSummaryRead)
async def get_crawl_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await get_faculty_crawl_job_summary(session, job_id)
    except CrawlJobRecordError as exc:
        _raise_http_record_error(exc)


@router.get("/{job_id}/details", response_model=CrawlJobDetailsRead)
async def get_crawl_job_details(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobDetailsRead:
    job = await _get_crawl_job_or_404(session, job_id)
    pages = await _list_crawl_pages_for_job(session, job_id)
    candidates = await _list_crawl_candidates_for_job(session, job_id)
    metrics = build_crawl_job_metrics(job)
    summary = CrawlJobSummaryRead.model_validate(job).model_copy(
        update={
            "page_count": len(pages),
            "candidate_count": len(candidates),
            "latest_event_message": _latest_event_message(job.agent_trace),
            "input_tokens": metrics.input_tokens,
            "output_tokens": metrics.output_tokens,
            "cached_tokens": metrics.cached_tokens,
            "total_tokens": metrics.total_tokens,
            "duration_seconds": metrics.duration_seconds,
        },
    )
    return CrawlJobDetailsRead(
        job=summary,
        pages=pages,
        candidates=candidates,
        events=build_crawl_job_events(job, pages=pages, candidates=candidates),
    )


@router.get("/{job_id}/events", response_model=list[CrawlJobEventRead])
async def list_crawl_job_events(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> list[dict[str, object]]:
    job = await _get_crawl_job_or_404(session, job_id)
    pages = await _list_crawl_pages_for_job(session, job_id)
    candidates = await _list_crawl_candidates_for_job(session, job_id)
    return build_crawl_job_events(job, pages=pages, candidates=candidates)


@router.get("/{job_id}/pages", response_model=list[CrawlPageRead])
async def list_crawl_pages(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> list[CrawlPage]:
    await _get_crawl_job_or_404(session, job_id)
    return await _list_crawl_pages_for_job(session, job_id)


@router.get("/{job_id}/candidates", response_model=list[CrawlCandidateRead])
async def list_crawl_candidates(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> list[CrawlCandidate]:
    await _get_crawl_job_or_404(session, job_id)
    return await _list_crawl_candidates_for_job(session, job_id)


async def _list_crawl_pages_for_job(
    session: AsyncSession, job_id: int
) -> list[CrawlPage]:
    attempts = list(
        (
            await session.execute(
                select(CrawlPage)
                .where(CrawlPage.job_id == job_id)
                .order_by(CrawlPage.created_at.asc(), CrawlPage.id.asc()),
            )
        ).scalars(),
    )
    return _select_canonical_crawl_pages(attempts)


def _select_canonical_crawl_pages(attempts: list[CrawlPage]) -> list[CrawlPage]:
    pages_by_url: dict[str, CrawlPage] = {}
    for page in attempts:
        key = _crawl_page_normalized_url(page.url)
        current = pages_by_url.get(key)
        if current is None or _crawl_page_display_rank(page) > _crawl_page_display_rank(
            current
        ):
            pages_by_url[key] = page
    return list(pages_by_url.values())


def _crawl_page_normalized_url(url: str) -> str:
    try:
        return normalize_url(url)
    except ValueError:
        return url.strip()


def _crawl_page_display_rank(page: CrawlPage) -> tuple[bool, bool, bool, int, int]:
    title = (page.title or "").strip()
    text_excerpt = (page.text_excerpt or "").strip()
    return (
        page.status == "succeeded",
        bool(title),
        bool(text_excerpt),
        len(text_excerpt),
        int(page.id or 0),
    )


async def _list_crawl_candidates_for_job(
    session: AsyncSession, job_id: int
) -> list[CrawlCandidate]:
    return list(
        (
            await session.execute(
                select(CrawlCandidate)
                .where(
                    CrawlCandidate.job_id == job_id,
                    canonical_candidate_clause(),
                )
                .order_by(
                    CrawlCandidate.confidence.desc(),
                    CrawlCandidate.created_at.asc(),
                    CrawlCandidate.id.asc(),
                ),
            )
        ).scalars(),
    )


@router.post("/{job_id}/approve", response_model=CrawlJobApproveResult)
async def approve_crawl_candidates(
    job_id: int,
    payload: CrawlJobApprovePayload,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobApproveResult:
    try:
        result = await approve_faculty_crawl_candidates(
            session,
            job_id,
            payload.candidate_ids,
        )
    except CrawlJobRecordError as exc:
        _raise_http_record_error(exc)
    await session.commit()
    return result


@router.post("/{job_id}/enrich", response_model=CrawlJobEnrichResult)
async def enrich_crawl_candidates(
    job_id: int,
    payload: CrawlJobEnrichPayload,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobEnrichResult:
    if not payload.candidate_ids:
        raise HTTPException(status_code=400, detail="请至少选择一位候选导师")
    if (
        payload.llm_profile_id is not None
        and await get_active_llm_profile(session, payload.llm_profile_id) is None
    ):
        raise HTTPException(status_code=404, detail="模型配置不存在")
    try:
        result = await enqueue_faculty_crawl_candidate_enrichment_records(
            session,
            job_id,
            SelectionSpec(
                mode="ids",
                ids=list(dict.fromkeys(payload.candidate_ids)),
            ),
            llm_profile_id=payload.llm_profile_id,
        )
    except CrawlJobRecordError as exc:
        _raise_http_record_error(exc)
    await session.commit()
    return result


@router.post("/{job_id}/resume-review", response_model=CrawlJobRead)
async def resume_crawl_job_review(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJob:
    try:
        job = await resume_faculty_crawl_job_review_record(session, job_id)
    except CrawlJobRecordError as exc:
        _raise_http_record_error(exc)
    await session.commit()
    await session.refresh(job)
    return job


@router.post("/{job_id}/cancel", response_model=CrawlJobRead)
async def cancel_crawl_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJob:
    try:
        job = await cancel_faculty_crawl_job_record(session, job_id)
    except CrawlJobRecordError as exc:
        _raise_http_record_error(exc)
    await session.commit()
    profile_text_cache.discard_job(job_id=job.id)
    await session.refresh(job)
    return job


@router.post("/{job_id}/pause", response_model=CrawlJobRead)
async def pause_crawl_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJob:
    try:
        job = await pause_faculty_crawl_job_record(session, job_id)
    except CrawlJobRecordError as exc:
        _raise_http_record_error(exc)
    await session.commit()
    await session.refresh(job)
    return job


@router.post("/{job_id}/resume", response_model=CrawlJobRead)
async def resume_crawl_job(
    job_id: int,
    payload: CrawlJobResumePayload | None = None,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJob:
    try:
        job = await resume_faculty_crawl_job_record(session, job_id, payload)
    except CrawlJobRecordError as exc:
        _raise_http_record_error(exc)
    await session.commit()
    await session.refresh(job)
    return job


@router.post("/{job_id}/retry", response_model=CrawlJobRead)
async def retry_crawl_job(
    job_id: int,
    payload: CrawlJobRetryPayload,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJob:
    try:
        job = await retry_faculty_crawl_job_record(
            session,
            job_id,
            payload,
            resolve_default_llm_profile=False,
        )
    except CrawlJobRecordError as exc:
        _raise_http_record_error(exc)
    await session.commit()
    profile_text_cache.discard_job(job_id=job.id)
    await session.refresh(job)
    return job


@router.post("/{job_id}/delete", response_model=CrawlJobRead)
async def delete_crawl_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJob:
    try:
        job = await delete_faculty_crawl_job_record(session, job_id)
    except CrawlJobRecordError as exc:
        _raise_http_record_error(exc)
    await session.commit()
    profile_text_cache.discard_job(job_id=job.id)
    await session.refresh(job)
    return job


@router.post("/{job_id}/restore", response_model=CrawlJobRead)
async def restore_crawl_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJob:
    try:
        job = await restore_faculty_crawl_job_record(session, job_id)
    except CrawlJobRecordError as exc:
        _raise_http_record_error(exc)
    await session.commit()
    await session.refresh(job)
    return job


async def _get_crawl_job_or_404(session: AsyncSession, job_id: int) -> CrawlJob:
    job = await session.scalar(
        select(CrawlJob)
        .options(selectinload(CrawlJob.current_run))
        .where(
            CrawlJob.id == job_id,
            CrawlJob.job_kind == CrawlJobKind.FACULTY_CRAWL.value,
        ),
    )
    if job is None:
        raise HTTPException(status_code=404, detail="未找到抓取任务")
    return job


async def _build_crawl_job_summaries(
    session: AsyncSession,
    jobs: list[CrawlJob],
) -> list[CrawlJobSummaryRead]:
    if not jobs:
        return []

    job_ids = [job.id for job in jobs]
    page_counts = await _count_unique_crawl_pages_by_job_id(session, job_ids)
    candidate_counts: dict[int, int] = {}
    for job_id_chunk in chunked_values(unique_positive_ids(job_ids)):
        candidate_count_rows = await session.execute(
            select(CrawlCandidate.job_id, func.count())
            .where(
                CrawlCandidate.job_id.in_(job_id_chunk),
                canonical_candidate_clause(),
            )
            .group_by(CrawlCandidate.job_id)
        )
        candidate_counts.update(
            {int(job_id): int(count) for job_id, count in candidate_count_rows},
        )

    return [
        CrawlJobSummaryRead.model_validate(job).model_copy(
            update={
                "page_count": page_counts.get(job.id, 0),
                "candidate_count": candidate_counts.get(job.id, 0),
                "latest_event_message": _latest_event_message(job.agent_trace),
                "input_tokens": metrics.input_tokens,
                "output_tokens": metrics.output_tokens,
                "cached_tokens": metrics.cached_tokens,
                "total_tokens": metrics.total_tokens,
                "duration_seconds": metrics.duration_seconds,
            },
        )
        for job in jobs
        for metrics in [build_crawl_job_metrics(job)]
    ]


async def _count_unique_crawl_pages_by_job_id(
    session: AsyncSession,
    job_ids: list[int],
) -> dict[int, int]:
    rows = []
    for job_id_chunk in chunked_values(unique_positive_ids(job_ids)):
        rows.extend(
            (
                await session.execute(
                    select(CrawlPage.job_id, CrawlPage.url)
                    .where(CrawlPage.job_id.in_(job_id_chunk))
                    .distinct(),
                )
            ).all(),
        )
    urls_by_job: dict[int, set[str]] = {}
    for job_id, url in rows:
        urls_by_job.setdefault(int(job_id), set()).add(
            _crawl_page_normalized_url(str(url))
        )
    return {job_id: len(urls) for job_id, urls in urls_by_job.items()}


def _latest_event_message(agent_trace: object) -> str | None:
    if not isinstance(agent_trace, list):
        return None

    trace_events = [item for item in agent_trace if isinstance(item, dict)]
    if not trace_events:
        return None

    latest_event = trace_events[-1]
    summary = latest_event.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()

    message = normalize_agent_trace_event(latest_event).get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return None
