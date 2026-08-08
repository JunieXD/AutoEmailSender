from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.time import utc_now
from app.schemas.selection import SelectionSpec
from app.models import (
    CrawlCandidate,
    CrawlCandidateEnrichmentTask,
    CrawlCandidateEnrichmentTaskStatus,
    CrawlJob,
    CrawlJobKind,
    CrawlJobStatus,
    CrawlPage,
    CrawlPageChunk,
    CrawlPageChunkStatus,
    CrawlPageTask,
    CrawlPageTaskStatus,
    CrawlWorkerTokenUsage,
    LLMProfile,
)
from ..schemas import (
    CrawlCandidateRead,
    CrawlCandidateUpdatePayload,
    CrawlJobEnrichResult,
    CrawlJobCreatePayload,
    CrawlJobRetryPayload,
    CrawlJobResumePayload,
    CrawlJobSummaryRead,
    CrawlPageRead,
)
from .events import normalize_agent_trace_event
from .metrics import build_crawl_job_metrics
from .llm_context import public_llm_context, snapshot_crawl_job_llm_profile
from .runs import (
    create_initial_crawl_job_run,
    create_retry_crawl_job_run,
    mark_crawl_job_run_finished,
    mark_crawl_job_run_paused,
    mark_crawl_job_run_queued,
    mark_crawl_job_run_running,
)
from ..v2.profile_text_cache import profile_text_cache
from ..v2.routing import (
    ENTRY_EXPANSION_MODE,
    NO_EXPANSION_MODE,
    START_DISCOVERY_REASON,
)
from ..v2.url_utils import normalize_url
from app.services.operation_logs import record_operation_log
from ..candidate_identity import (
    candidate_identity_values,
    canonical_candidate_clause,
    canonicalize_candidate_ids,
    consolidate_candidate_identity,
    mark_candidate_fields_manual,
    rebuild_candidate_identity_keys,
    resolve_canonical_candidate,
)


CRAWL_JOB_DELETABLE_STATUSES = {
    CrawlJobStatus.COMPLETED.value,
    CrawlJobStatus.FAILED.value,
    CrawlJobStatus.CANCELED.value,
    CrawlJobStatus.NEEDS_REVIEW.value,
    CrawlJobStatus.PARTIALLY_COMPLETED.value,
}


class CrawlJobRecordError(Exception):
    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


async def create_faculty_crawl_job_record(
    session: AsyncSession,
    payload: CrawlJobCreatePayload,
    *,
    event_name: str = "crawl_job.created",
    actor: str | None = None,
) -> CrawlJob:
    job = CrawlJob(
        university=payload.university,
        school=payload.school,
        start_url=payload.start_url,
        start_urls=payload.start_urls,
        entry_type=payload.entry_type,
        job_kind=CrawlJobKind.FACULTY_CRAWL.value,
        llm_profile_id=payload.llm_profile_id,
        status=CrawlJobStatus.QUEUED.value,
        progress_current=0,
        progress_total=0,
    )
    session.add(job)
    await session.flush()
    for start_url, normalized_url in _iter_unique_start_urls_for_page_tasks(job):
        session.add(
            CrawlPageTask(
                job_id=job.id,
                normalized_url=normalized_url,
                original_url=start_url,
                parent_url=None,
                discovery_reason=START_DISCOVERY_REASON,
                expansion_mode=(
                    NO_EXPANSION_MODE if job.entry_type == "profile" else ENTRY_EXPANSION_MODE
                ),
                depth=0,
                status=CrawlPageTaskStatus.PENDING.value,
            ),
        )
    await create_initial_crawl_job_run(session, job)
    if payload.llm_profile_id is not None:
        await _resolve_and_refresh_llm_profile(
            session,
            job,
            payload.llm_profile_id,
            trigger="create",
            actor=actor,
        )
    metadata: dict[str, object] = {
        "university": job.university,
        "school": job.school,
        "start_url": job.start_url,
        "start_urls": job.start_urls or [job.start_url],
        "entry_type": job.entry_type,
        "llm_profile_id": job.llm_profile_id,
    }
    if actor is not None:
        metadata["actor"] = actor
    await record_operation_log(
        session,
        category="crawler",
        event_name=event_name,
        entity_type="crawl_job",
        entity_id=str(job.id),
        metadata=metadata,
    )
    return job


async def list_faculty_crawl_job_records(
    session: AsyncSession,
    *,
    view: str,
    offset: int,
    limit: int,
) -> list[CrawlJobSummaryRead]:
    statement = (
        select(CrawlJob)
        .options(selectinload(CrawlJob.current_run))
        .where(CrawlJob.job_kind == CrawlJobKind.FACULTY_CRAWL.value)
    )
    if view == "current":
        statement = statement.where(CrawlJob.deleted_at.is_(None))
    elif view == "trash":
        statement = statement.where(CrawlJob.deleted_at.is_not(None))
    else:
        raise CrawlJobRecordError(
            status_code=422,
            code="INVALID_CRAWL_JOB_VIEW",
            message="未知任务视图",
        )
    jobs = list(
        await session.scalars(
            statement.order_by(CrawlJob.created_at.desc(), CrawlJob.id.desc())
            .offset(offset)
            .limit(limit),
        ),
    )
    return await _build_crawl_job_summaries(session, jobs)


async def get_faculty_crawl_job_summary(
    session: AsyncSession,
    job_id: int,
) -> CrawlJobSummaryRead:
    job = await get_faculty_crawl_job_or_raise(session, job_id)
    return (await _build_crawl_job_summaries(session, [job]))[0]


async def list_faculty_crawl_pages(
    session: AsyncSession,
    job_id: int,
    *,
    offset: int,
    limit: int,
) -> list[CrawlPageRead]:
    await get_faculty_crawl_job_or_raise(session, job_id)
    pages = list(
        await session.scalars(
            select(CrawlPage)
            .where(CrawlPage.job_id == job_id)
            .order_by(CrawlPage.created_at.asc(), CrawlPage.id.asc())
            .offset(offset)
            .limit(limit),
        ),
    )
    return [CrawlPageRead.model_validate(page) for page in pages]


async def list_faculty_crawl_candidates(
    session: AsyncSession,
    job_id: int,
    *,
    offset: int,
    limit: int,
) -> list[CrawlCandidateRead]:
    await get_faculty_crawl_job_or_raise(session, job_id)
    candidates = list(
        await session.scalars(
            select(CrawlCandidate)
            .where(
                CrawlCandidate.job_id == job_id,
                canonical_candidate_clause(),
            )
            .order_by(
                CrawlCandidate.confidence.desc(),
                CrawlCandidate.created_at.asc(),
                CrawlCandidate.id.asc(),
            )
            .offset(offset)
            .limit(limit),
        ),
    )
    return [CrawlCandidateRead.model_validate(candidate) for candidate in candidates]


async def update_faculty_crawl_candidate_record(
    session: AsyncSession,
    candidate_id: int,
    payload: CrawlCandidateUpdatePayload,
    *,
    event_name: str = "crawl_candidate.updated",
    actor: str | None = None,
) -> CrawlCandidateRead:
    candidate = await get_faculty_crawl_candidate_or_raise(session, candidate_id)
    candidate = await resolve_canonical_candidate(session, candidate)
    previous_identities = set(
        candidate_identity_values(
            email=candidate.email,
            profile_url=candidate.profile_url,
        )
    )
    candidate.name = payload.name
    candidate.email = payload.email.lower() if payload.email else None
    candidate.title = payload.title
    candidate.university = payload.university
    candidate.school = payload.school
    candidate.department = payload.department
    candidate.research_direction = payload.research_direction
    candidate.recent_papers = payload.recent_papers
    candidate.profile_url = payload.profile_url
    candidate.source_url = payload.source_url
    candidate.review_status = payload.review_status
    mark_candidate_fields_manual(
        candidate,
        (
            "name",
            "email",
            "title",
            "university",
            "school",
            "department",
            "research_direction",
            "recent_papers",
            "profile_url",
            "source_url",
        ),
    )
    candidate.updated_at = utc_now()
    current_identities = set(
        candidate_identity_values(
            email=candidate.email,
            profile_url=candidate.profile_url,
        )
    )
    candidate = await rebuild_candidate_identity_keys(
        session,
        candidate,
        exclude_identities=previous_identities - current_identities,
    )
    metadata: dict[str, object] = {
        "job_id": candidate.job_id,
        "review_status": candidate.review_status,
        "has_email": bool(candidate.email),
    }
    if actor is not None:
        metadata["actor"] = actor
    await record_operation_log(
        session,
        category="crawler",
        event_name=event_name,
        entity_type="crawl_candidate",
        entity_id=str(candidate.id),
        metadata=metadata,
    )
    return CrawlCandidateRead.model_validate(candidate)


async def _resolve_candidate_selection(
    session: AsyncSession,
    *,
    job_id: int,
    selection: SelectionSpec,
) -> tuple[list[CrawlCandidate], int]:
    if selection.mode == "ids":
        candidates, missing_candidate_ids = await canonicalize_candidate_ids(
            session,
            job_id=job_id,
            candidate_ids=selection.ids,
        )
        if missing_candidate_ids:
            raise CrawlJobRecordError(
                status_code=404,
                code="CRAWL_CANDIDATES_NOT_FOUND",
                message="部分候选导师不存在或不属于该抓取任务。",
            )
    else:
        statement = select(CrawlCandidate).where(
            CrawlCandidate.job_id == job_id,
            canonical_candidate_clause(),
        )
        filters = selection.filter if selection.mode == "filter" else {}
        unknown_filters = sorted(set(filters) - {"review_status", "has_profile_url"})
        if unknown_filters:
            raise CrawlJobRecordError(
                status_code=422,
                code="INVALID_CRAWL_CANDIDATE_SELECTION_FILTER",
                message=f"不支持的候选筛选字段：{', '.join(unknown_filters)}",
            )
        if "review_status" in filters:
            raw_statuses = filters["review_status"]
            statuses = [raw_statuses] if isinstance(raw_statuses, str) else raw_statuses
            allowed_statuses = {"pending", "accepted", "rejected", "merged"}
            if (
                not isinstance(statuses, list)
                or not statuses
                or any(not isinstance(item, str) or item not in allowed_statuses for item in statuses)
            ):
                raise CrawlJobRecordError(
                    status_code=422,
                    code="INVALID_CRAWL_CANDIDATE_SELECTION_FILTER",
                    message="review_status 必须是 pending、accepted、rejected 或 merged。",
                )
            statement = statement.where(CrawlCandidate.review_status.in_(statuses))
        if "has_profile_url" in filters:
            has_profile_url = filters["has_profile_url"]
            if not isinstance(has_profile_url, bool):
                raise CrawlJobRecordError(
                    status_code=422,
                    code="INVALID_CRAWL_CANDIDATE_SELECTION_FILTER",
                    message="has_profile_url 必须是布尔值。",
                )
            profile_url_length = func.length(func.trim(func.coalesce(CrawlCandidate.profile_url, "")))
            statement = statement.where(
                profile_url_length > 0 if has_profile_url else profile_url_length == 0,
            )
        candidates = list(await session.scalars(statement.order_by(CrawlCandidate.id.asc())))

    excluded_ids: set[int] = set()
    if selection.exclude_ids:
        excluded_candidates, missing_exclude_ids = await canonicalize_candidate_ids(
            session,
            job_id=job_id,
            candidate_ids=selection.exclude_ids,
        )
        if missing_exclude_ids:
            raise CrawlJobRecordError(
                status_code=404,
                code="CRAWL_CANDIDATE_EXCLUSIONS_NOT_FOUND",
                message="部分排除候选不存在或不属于该抓取任务。",
            )
        excluded_ids = {candidate.id for candidate in excluded_candidates}
    selected_candidates = [candidate for candidate in candidates if candidate.id not in excluded_ids]
    return selected_candidates, len(candidates) - len(selected_candidates)


def _enrichment_skip_summary(skipped_count: int) -> dict[str, object]:
    reasons: list[dict[str, object]] = []
    if skipped_count:
        reasons.append(
            {
                "code": "MISSING_PROFILE_URL",
                "count": skipped_count,
                "message": "缺少有效个人主页",
                "recoverable": True,
                "suggested_action": "crawler.candidates.update",
            },
        )
    return {"count": skipped_count, "by_reason": reasons}


def _enrichment_observation(job: CrawlJob) -> dict[str, object]:
    terminal = job.status in {
        CrawlJobStatus.COMPLETED.value,
        CrawlJobStatus.PARTIALLY_COMPLETED.value,
        CrawlJobStatus.FAILED.value,
        CrawlJobStatus.CANCELED.value,
    }
    settled = terminal or job.status in {
        CrawlJobStatus.NEEDS_REVIEW.value,
        CrawlJobStatus.PAUSED.value,
    }
    return {
        "id": job.id,
        "status": job.status,
        "settled": settled,
        "terminal": terminal,
    }


async def enqueue_faculty_crawl_candidate_enrichment_records(
    session: AsyncSession,
    job_id: int,
    selection: SelectionSpec,
    *,
    llm_profile_id: int | None,
    event_name: str = "crawl_job.candidate_enrichment_queued",
    actor: str | None = None,
) -> CrawlJobEnrichResult:
    job = await get_faculty_crawl_job_or_raise(session, job_id)
    if job.status == CrawlJobStatus.RUNNING.value:
        raise CrawlJobRecordError(
            status_code=409,
            code="CRAWL_CANDIDATE_ENRICHMENT_RUNNING",
            message="候选信息正在补全中，请稍后再试。",
        )
    if job.status not in {
        CrawlJobStatus.NEEDS_REVIEW.value,
        CrawlJobStatus.PARTIALLY_COMPLETED.value,
    }:
        raise CrawlJobRecordError(
            status_code=409,
            code="CRAWL_CANDIDATE_ENRICHMENT_NOT_REVIEWABLE",
            message="抓取任务尚未进入审核状态，不能补全候选资料。",
        )
    candidates, excluded_count = await _resolve_candidate_selection(
        session,
        job_id=job.id,
        selection=selection,
    )
    await _resolve_and_refresh_llm_profile(
        session,
        job,
        llm_profile_id,
        trigger="enrich",
        actor=actor,
    )

    enrichable_candidates = [candidate for candidate in candidates if (candidate.profile_url or "").strip()]
    skipped_count = len(candidates) - len(enrichable_candidates)
    if not enrichable_candidates:
        message = (
            f"跳过 {skipped_count} 位缺少详情页 URL 的候选。"
            if skipped_count
            else "没有候选导师匹配当前选择条件。"
        )
        return CrawlJobEnrichResult(
            selected_count=0,
            enriched_count=0,
            unchanged_count=0,
            failed_count=0,
            skipped_count=skipped_count,
            message=message,
            selection={
                "mode": selection.mode,
                "matched_count": len(candidates),
                "eligible_count": 0,
                "excluded_count": excluded_count,
            },
            submission={
                "queued_count": 0,
                "already_active_count": 0,
                "already_completed_count": 0,
                "rejected_count": 0,
            },
            skips=_enrichment_skip_summary(skipped_count),
            observation=_enrichment_observation(job),
        )

    now = utc_now()
    enqueued_count = 0
    already_active_count = 0
    already_completed_count = 0
    for candidate in enrichable_candidates:
        existing_task = await session.scalar(
            select(CrawlCandidateEnrichmentTask).where(
                CrawlCandidateEnrichmentTask.job_id == job.id,
                CrawlCandidateEnrichmentTask.candidate_id == candidate.id,
            ),
        )
        if existing_task is not None:
            if existing_task.status == CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value:
                if _candidate_has_missing_enrichment_fields(candidate):
                    existing_task.status = CrawlCandidateEnrichmentTaskStatus.PENDING.value
                    existing_task.worker_id = None
                    existing_task.claimed_at = None
                    existing_task.lease_expires_at = None
                    existing_task.last_error = None
                    existing_task.updated_at = now
                    enqueued_count += 1
                    continue
                already_completed_count += 1
                continue
            if existing_task.status in {
                CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
                CrawlCandidateEnrichmentTaskStatus.PENDING.value,
            }:
                already_active_count += 1
                continue
            existing_task.status = CrawlCandidateEnrichmentTaskStatus.PENDING.value
            existing_task.worker_id = None
            existing_task.claimed_at = None
            existing_task.lease_expires_at = None
            existing_task.last_error = None
            existing_task.updated_at = now
            enqueued_count += 1
            continue
        try:
            async with session.begin_nested():
                session.add(
                    CrawlCandidateEnrichmentTask(
                        job_id=job.id,
                        candidate_id=candidate.id,
                        status=CrawlCandidateEnrichmentTaskStatus.PENDING.value,
                    ),
                )
                await session.flush()
        except IntegrityError:
            already_active_count += 1
            continue
        enqueued_count += 1

    if enqueued_count > 0 or already_active_count > 0:
        job.status = CrawlJobStatus.RUNNING.value
        job.error_message = None
        job.updated_at = now
        await mark_crawl_job_run_running(session, job, now=now)

    selected_count = len(enrichable_candidates)
    existing_count = already_active_count + already_completed_count
    skipped_message = f"跳过 {skipped_count} 位缺少详情页 URL 的候选。" if skipped_count > 0 else ""
    completed_skipped_message = f"已补全跳过 {already_completed_count} 位。" if already_completed_count > 0 else ""
    if enqueued_count > 0:
        message = f"已加入补全队列：选中 {selected_count} 位，入队 {enqueued_count} 位。{completed_skipped_message}{skipped_message}"
    elif already_completed_count > 0 and existing_count == already_completed_count:
        message = f"选中 {selected_count} 位，已补全跳过 {already_completed_count} 位。{skipped_message}"
    else:
        message = f"选中的 {selected_count} 位候选已在补全队列中或已补全。{completed_skipped_message}{skipped_message}"
    await _record_job_event(
        session,
        job,
        event_name,
        metadata={
            "selected_count": selected_count,
            "matched_count": len(candidates),
            "enqueued_count": enqueued_count,
            "existing_count": existing_count,
            "already_active_count": already_active_count,
            "already_completed_count": already_completed_count,
            "skipped_count": skipped_count,
            "selection_mode": selection.mode,
            "llm_profile_id": job.llm_profile_id,
        },
        actor=actor,
    )
    return CrawlJobEnrichResult(
        selected_count=selected_count,
        enriched_count=0,
        unchanged_count=existing_count,
        failed_count=0,
        skipped_count=skipped_count,
        message=message,
        selection={
            "mode": selection.mode,
            "matched_count": len(candidates),
            "eligible_count": selected_count,
            "excluded_count": excluded_count,
        },
        submission={
            "queued_count": enqueued_count,
            "already_active_count": already_active_count,
            "already_completed_count": already_completed_count,
            "rejected_count": 0,
        },
        skips=_enrichment_skip_summary(skipped_count),
        observation=_enrichment_observation(job),
    )


async def get_faculty_crawl_candidate_or_raise(
    session: AsyncSession,
    candidate_id: int,
) -> CrawlCandidate:
    candidate = await session.scalar(
        select(CrawlCandidate)
        .join(CrawlJob, CrawlJob.id == CrawlCandidate.job_id)
        .where(
            CrawlCandidate.id == candidate_id,
            CrawlJob.job_kind == CrawlJobKind.FACULTY_CRAWL.value,
        ),
    )
    if candidate is None:
        raise CrawlJobRecordError(
            status_code=404,
            code="CRAWL_CANDIDATE_NOT_FOUND",
            message="未找到候选导师",
        )
    return await resolve_canonical_candidate(session, candidate)


def _candidate_has_missing_enrichment_fields(candidate: CrawlCandidate) -> bool:
    return any(
        (
            not (candidate.email or "").strip(),
            not (candidate.title or "").strip(),
            not (candidate.department or "").strip(),
            not (candidate.research_direction or "").strip(),
            not any(str(item).strip() for item in candidate.recent_papers or []),
        ),
    )


async def cancel_faculty_crawl_job_record(
    session: AsyncSession,
    job_id: int,
    *,
    event_name: str = "crawl_job.canceled",
    actor: str | None = None,
) -> CrawlJob:
    job = await get_faculty_crawl_job_or_raise(session, job_id)
    if job.status in {
        CrawlJobStatus.COMPLETED.value,
        CrawlJobStatus.FAILED.value,
        CrawlJobStatus.CANCELED.value,
    }:
        profile_text_cache.discard_job(job_id=job.id)
        return job
    now = utc_now()
    job.status = CrawlJobStatus.CANCELED.value
    job.updated_at = now
    await _release_processing_work(session, job.id, reason="任务已取消，释放处理中工作项")
    await mark_crawl_job_run_finished(
        session,
        job,
        status=CrawlJobStatus.CANCELED.value,
        now=now,
    )
    await _record_job_event(
        session,
        job,
        event_name,
        metadata={"status": job.status},
        actor=actor,
    )
    profile_text_cache.discard_job(job_id=job.id)
    return job


async def pause_faculty_crawl_job_record(
    session: AsyncSession,
    job_id: int,
    *,
    event_name: str = "crawl_job.paused",
    actor: str | None = None,
) -> CrawlJob:
    job = await get_faculty_crawl_job_or_raise(session, job_id)
    if job.status == CrawlJobStatus.PAUSED.value:
        return job
    if job.status not in {CrawlJobStatus.QUEUED.value, CrawlJobStatus.RUNNING.value}:
        raise CrawlJobRecordError(
            status_code=409,
            code="CRAWL_JOB_NOT_PAUSABLE",
            message="仅允许暂停排队中或运行中的抓取任务",
        )
    now = utc_now()
    job.status = CrawlJobStatus.PAUSED.value
    job.updated_at = now
    await _release_processing_work(session, job.id, reason="任务已暂停，释放处理中工作项")
    await mark_crawl_job_run_paused(session, job, now=now)
    await _record_job_event(
        session,
        job,
        event_name,
        metadata={"status": job.status},
        actor=actor,
    )
    return job


async def resume_faculty_crawl_job_record(
    session: AsyncSession,
    job_id: int,
    payload: CrawlJobResumePayload | None = None,
    *,
    event_name: str = "crawl_job.resumed",
    actor: str | None = None,
) -> CrawlJob:
    job = await get_faculty_crawl_job_or_raise(session, job_id)
    if job.status != CrawlJobStatus.PAUSED.value:
        raise CrawlJobRecordError(
            status_code=409,
            code="CRAWL_JOB_NOT_RESUMABLE",
            message="仅允许继续已暂停的抓取任务",
        )
    if payload is not None and payload.llm_profile_id is not None:
        await _resolve_and_refresh_llm_profile(
            session,
            job,
            payload.llm_profile_id,
            trigger="resume",
            actor=actor,
        )
    now = utc_now()
    job.status = CrawlJobStatus.QUEUED.value
    job.error_message = None
    job.updated_at = now
    await mark_crawl_job_run_queued(session, job, now=now)
    await _record_job_event(
        session,
        job,
        event_name,
        metadata={"status": job.status},
        actor=actor,
    )
    return job


async def retry_faculty_crawl_job_record(
    session: AsyncSession,
    job_id: int,
    payload: CrawlJobRetryPayload,
    *,
    event_name: str = "crawl_job.retried",
    actor: str | None = None,
) -> CrawlJob:
    job = await get_faculty_crawl_job_or_raise(session, job_id)
    if job.status not in {CrawlJobStatus.FAILED.value, CrawlJobStatus.CANCELED.value}:
        raise CrawlJobRecordError(
            status_code=409,
            code="CRAWL_JOB_NOT_RETRYABLE",
            message="仅允许重试状态为“失败”或“已取消”的抓取任务",
        )

    await session.execute(
        delete(CrawlCandidateEnrichmentTask).where(
            CrawlCandidateEnrichmentTask.job_id == job.id,
        ),
    )
    await session.execute(delete(CrawlPageTask).where(CrawlPageTask.job_id == job.id))
    if payload.clear_existing_data:
        await session.execute(
            delete(CrawlWorkerTokenUsage).where(CrawlWorkerTokenUsage.job_id == job.id),
        )

    if payload.clear_existing_data:
        await session.execute(delete(CrawlCandidate).where(CrawlCandidate.job_id == job.id))
        await session.execute(delete(CrawlPageChunk).where(CrawlPageChunk.job_id == job.id))
        await session.execute(delete(CrawlPage).where(CrawlPage.job_id == job.id))
        job.agent_trace = []

    for start_url, normalized_url in _iter_unique_start_urls_for_page_tasks(job):
        session.add(
            CrawlPageTask(
                job_id=job.id,
                normalized_url=normalized_url,
                original_url=start_url,
                parent_url=None,
                discovery_reason=START_DISCOVERY_REASON,
                expansion_mode=(
                    NO_EXPANSION_MODE if job.entry_type == "profile" else ENTRY_EXPANSION_MODE
                ),
                depth=0,
                status=CrawlPageTaskStatus.PENDING.value,
            ),
        )

    now = utc_now()
    job.status = CrawlJobStatus.QUEUED.value
    job.progress_current = 0
    job.progress_total = 0
    job.error_message = None
    job.updated_at = now
    await create_retry_crawl_job_run(session, job, now=now)
    await _resolve_and_refresh_llm_profile(
        session,
        job,
        payload.llm_profile_id,
        trigger="retry",
        actor=actor,
    )
    await _record_job_event(
        session,
        job,
        event_name,
        metadata={
            "status": job.status,
            "clear_existing_data": payload.clear_existing_data,
            "llm_profile_id": job.llm_profile_id,
        },
        actor=actor,
    )
    profile_text_cache.discard_job(job_id=job.id)
    return job


async def resume_faculty_crawl_job_review_record(
    session: AsyncSession,
    job_id: int,
    *,
    event_name: str = "crawl_job.review_resumed",
    actor: str | None = None,
) -> CrawlJob:
    job = await get_faculty_crawl_job_or_raise(session, job_id)
    original_status = job.status
    if job.status not in {CrawlJobStatus.CANCELED.value, CrawlJobStatus.FAILED.value}:
        raise CrawlJobRecordError(
            status_code=409,
            code="CRAWL_JOB_REVIEW_NOT_RESUMABLE",
            message="仅允许已取消或失败的抓取任务转入待审核",
        )
    candidate_count = int(
        await session.scalar(
            select(func.count())
            .select_from(CrawlCandidate)
            .where(
                CrawlCandidate.job_id == job_id,
                canonical_candidate_clause(),
            ),
        )
        or 0
    )
    if candidate_count <= 0:
        raise CrawlJobRecordError(
            status_code=400,
            code="CRAWL_JOB_NO_REVIEW_CANDIDATES",
            message="当前任务没有可审核的候选导师",
        )
    now = utc_now()
    job.status = CrawlJobStatus.NEEDS_REVIEW.value
    job.error_message = None
    job.updated_at = now
    await _freeze_unfinished_discovery_work_for_review(session, job.id)
    if job.current_run is not None:
        job.current_run.status = CrawlJobStatus.NEEDS_REVIEW.value
        job.current_run.updated_at = now
    await _record_job_event(
        session,
        job,
        event_name,
        metadata={"from_status": original_status, "candidate_count": candidate_count},
        actor=actor,
    )
    return job


async def delete_faculty_crawl_job_record(
    session: AsyncSession,
    job_id: int,
    *,
    event_name: str = "crawl_job.deleted",
    actor: str | None = None,
) -> CrawlJob:
    job = await get_faculty_crawl_job_or_raise(session, job_id)
    if job.status not in CRAWL_JOB_DELETABLE_STATUSES:
        raise CrawlJobRecordError(
            status_code=400,
            code="CRAWL_JOB_NOT_DELETABLE",
            message="请先中止/取消任务后再删除",
        )
    previous_deleted_at = job.deleted_at
    if job.deleted_at is None:
        now = utc_now()
        job.deleted_at = now
        job.updated_at = now
    await _record_job_event(
        session,
        job,
        event_name,
        metadata={
            "status": job.status,
            "llm_profile_id": job.llm_profile_id,
            "previous_deleted_at": previous_deleted_at.isoformat() if previous_deleted_at else None,
        },
        actor=actor,
    )
    profile_text_cache.discard_job(job_id=job.id)
    return job


async def restore_faculty_crawl_job_record(
    session: AsyncSession,
    job_id: int,
    *,
    event_name: str = "crawl_job.restored",
    actor: str | None = None,
) -> CrawlJob:
    job = await get_faculty_crawl_job_or_raise(session, job_id)
    previous_deleted_at = job.deleted_at
    if job.deleted_at is not None:
        job.deleted_at = None
        job.updated_at = utc_now()
    await _record_job_event(
        session,
        job,
        event_name,
        metadata={
            "status": job.status,
            "llm_profile_id": job.llm_profile_id,
            "previous_deleted_at": previous_deleted_at.isoformat() if previous_deleted_at else None,
        },
        actor=actor,
    )
    return job


async def get_faculty_crawl_job_or_raise(
    session: AsyncSession,
    job_id: int,
) -> CrawlJob:
    job = await session.scalar(
        select(CrawlJob)
        .options(selectinload(CrawlJob.current_run))
        .where(
            CrawlJob.id == job_id,
            CrawlJob.job_kind == CrawlJobKind.FACULTY_CRAWL.value,
        ),
    )
    if job is None:
        raise CrawlJobRecordError(
            status_code=404,
            code="CRAWL_JOB_NOT_FOUND",
            message="未找到抓取任务",
        )
    return job


def _iter_unique_start_urls_for_page_tasks(job: CrawlJob) -> list[tuple[str, str]]:
    urls = job.start_urls or [job.start_url]
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url in urls:
        if not isinstance(url, str):
            continue
        stripped = url.strip()
        if not stripped:
            continue
        normalized_url = normalize_url(stripped)
        if normalized_url in seen:
            continue
        seen.add(normalized_url)
        unique.append((stripped, normalized_url))
    if unique:
        return unique
    fallback_url = job.start_url.strip()
    return [(fallback_url, normalize_url(fallback_url))]


async def _build_crawl_job_summaries(
    session: AsyncSession,
    jobs: list[CrawlJob],
) -> list[CrawlJobSummaryRead]:
    if not jobs:
        return []
    job_ids = [job.id for job in jobs]
    page_counts = await _count_by_job_id(session, CrawlPage.job_id, job_ids)
    candidate_count_rows = await session.execute(
        select(CrawlCandidate.job_id, func.count())
        .where(
            CrawlCandidate.job_id.in_(job_ids),
            canonical_candidate_clause(),
        )
        .group_by(CrawlCandidate.job_id)
    )
    candidate_counts = dict(candidate_count_rows.all())
    model_rows = (
        await session.execute(
            select(CrawlWorkerTokenUsage.job_id, CrawlWorkerTokenUsage.model_name)
            .where(
                CrawlWorkerTokenUsage.job_id.in_(job_ids),
                CrawlWorkerTokenUsage.model_name.is_not(None),
            )
            .distinct()
            .order_by(CrawlWorkerTokenUsage.job_id.asc(), CrawlWorkerTokenUsage.model_name.asc()),
        )
    ).all()
    effective_models: dict[int, list[str]] = {}
    for job_id, model_name in model_rows:
        if isinstance(model_name, str) and model_name:
            effective_models.setdefault(int(job_id), []).append(model_name)
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
                "llm_context": public_llm_context(
                    job.current_run.llm_runtime_snapshot if job.current_run is not None else None,
                    effective_models=effective_models.get(job.id, []),
                ),
            },
        )
        for job in jobs
        for metrics in [build_crawl_job_metrics(job)]
    ]


async def _count_by_job_id(
    session: AsyncSession,
    job_id_column: object,
    job_ids: list[int],
) -> dict[int, int]:
    rows = (
        await session.execute(
            select(job_id_column, func.count())
            .where(job_id_column.in_(job_ids))
            .group_by(job_id_column),
        )
    ).all()
    return {int(job_id): int(count) for job_id, count in rows}


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


async def _record_job_event(
    session: AsyncSession,
    job: CrawlJob,
    event_name: str,
    *,
    metadata: dict[str, object],
    actor: str | None,
) -> None:
    event_metadata = dict(metadata)
    if actor is not None:
        event_metadata["actor"] = actor
    await record_operation_log(
        session,
        category="crawler",
        event_name=event_name,
        entity_type="crawl_job",
        entity_id=str(job.id),
        metadata=event_metadata,
    )


async def _resolve_and_refresh_llm_profile(
    session: AsyncSession,
    job: CrawlJob,
    requested_llm_profile_id: int | None,
    *,
    trigger: str,
    actor: str | None,
) -> LLMProfile:
    old_profile = await session.get(LLMProfile, job.llm_profile_id) if job.llm_profile_id else None
    if requested_llm_profile_id is not None:
        profile_source = "explicit"
        llm_profile = await session.get(LLMProfile, requested_llm_profile_id)
        if llm_profile is None:
            raise CrawlJobRecordError(
                status_code=404,
                code="CRAWL_LLM_PROFILE_NOT_FOUND",
                message="模型配置不存在",
            )
    elif old_profile is not None:
        profile_source = "job"
        llm_profile = old_profile
    else:
        profile_source = "global_default"
        llm_profile = await session.scalar(
            select(LLMProfile)
            .where(LLMProfile.is_default.is_(True))
            .order_by(LLMProfile.created_at.asc(), LLMProfile.id.asc())
            .limit(1),
        )
        if llm_profile is None:
            raise CrawlJobRecordError(
                status_code=409,
                code="CRAWL_LLM_PROFILE_REQUIRED",
                message="请先配置可用的 LLM Profile",
            )
    if job.llm_profile_id != llm_profile.id:
        await _record_job_event(
            session,
            job,
            "crawl_job.llm_profile_refreshed",
            metadata={
                "old_llm_profile_id": job.llm_profile_id,
                "old_model_name": old_profile.model_name if old_profile else None,
                "new_llm_profile_id": llm_profile.id,
                "new_model_name": llm_profile.model_name,
                "trigger": trigger,
            },
            actor=actor,
        )
        job.llm_profile_id = llm_profile.id
    await snapshot_crawl_job_llm_profile(
        session,
        job,
        llm_profile,
        source=profile_source,
    )
    return llm_profile


async def _freeze_unfinished_discovery_work_for_review(session: AsyncSession, job_id: int) -> None:
    terminal_values = {
        "status": "failed_terminal",
        "last_error": "任务已转入待审核，停止继续发现新候选",
        "worker_id": None,
        "claimed_at": None,
        "lease_expires_at": None,
    }
    await session.execute(
        update(CrawlPageTask)
        .where(
            CrawlPageTask.job_id == job_id,
            CrawlPageTask.status.in_(
                [
                    CrawlPageTaskStatus.PENDING.value,
                    CrawlPageTaskStatus.PROCESSING.value,
                    CrawlPageTaskStatus.FAILED_RETRYABLE.value,
                ],
            ),
        )
        .values(**terminal_values),
    )
    await session.execute(
        update(CrawlPageChunk)
        .where(
            CrawlPageChunk.job_id == job_id,
            CrawlPageChunk.status.in_(
                [
                    CrawlPageChunkStatus.PENDING.value,
                    CrawlPageChunkStatus.PROCESSING.value,
                    CrawlPageChunkStatus.SPLIT_REQUIRED.value,
                    CrawlPageChunkStatus.FAILED_RETRYABLE.value,
                ],
            ),
        )
        .values(**terminal_values),
    )


async def _release_processing_work(session: AsyncSession, job_id: int, *, reason: str) -> None:
    clear_values = {
        "status": "pending",
        "last_error": reason,
        "worker_id": None,
        "claimed_at": None,
        "lease_expires_at": None,
    }
    await session.execute(
        update(CrawlPageTask)
        .where(
            CrawlPageTask.job_id == job_id,
            CrawlPageTask.status == CrawlPageTaskStatus.PROCESSING.value,
        )
        .values(**clear_values),
    )
    await session.execute(
        update(CrawlPageChunk)
        .where(
            CrawlPageChunk.job_id == job_id,
            CrawlPageChunk.status == CrawlPageChunkStatus.PROCESSING.value,
        )
        .values(**clear_values),
    )
    await session.execute(
        update(CrawlCandidateEnrichmentTask)
        .where(
            CrawlCandidateEnrichmentTask.job_id == job_id,
            CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
        )
        .values(**clear_values),
    )
