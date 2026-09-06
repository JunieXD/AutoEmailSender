from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.core.agent_revisions import ensure_revision, revision_for
from app.core.database import get_async_session, get_session_factory
from app.models import CrawlCandidate, CrawlPage
from app.modules.crawler.public import (
    CrawlCandidateUpdatePayload,
    CrawlJobCreatePayload,
    CrawlJobEnrichResult,
    CrawlJobRecordError,
    CrawlJobResumePayload,
    CrawlJobRetryPayload,
    CrawlJobSummaryRead,
    build_crawl_job_events,
    cancel_faculty_crawl_job_record,
    canonical_candidate_clause,
    create_faculty_crawl_job_record,
    delete_faculty_crawl_job_record,
    enqueue_faculty_crawl_candidate_enrichment_records,
    get_faculty_crawl_candidate_or_raise,
    get_faculty_crawl_job_or_raise,
    get_faculty_crawl_job_summary,
    list_faculty_crawl_candidates,
    list_faculty_crawl_job_records,
    list_faculty_crawl_pages,
    pause_faculty_crawl_job_record,
    restore_faculty_crawl_job_record,
    resume_faculty_crawl_job_record,
    resume_faculty_crawl_job_review_record,
    update_faculty_crawl_candidate_record,
)
from app.schemas.agent import (
    AgentBatchItemsRequest,
    AgentChangePlanRead,
    AgentCrawlCandidateRead,
    AgentCrawlCandidateUpdateRequest,
    AgentCrawlJobApproveRequest,
    AgentCrawlJobBatchCreateRead,
    AgentCrawlJobBatchEnrichItem,
    AgentCrawlJobBatchEnrichRead,
    AgentCrawlJobEnrichRequest,
    AgentCrawlJobEventRead,
    AgentCrawlJobRetryRequest,
    AgentCrawlPageRead,
    AgentPage,
    AgentUiHandoffRead,
)
from app.services.agent_change_plans import (
    create_crawl_candidate_approval_change_plan,
    create_crawl_job_retry_change_plan,
)
from app.services.agent_mutations import execute_agent_mutation
from app.services.agent_ui_handoffs import create_crawl_job_ui_handoff

from .support import (
    _slice_page,
)

router = APIRouter()


@router.post(
    "/crawler/jobs/{job_id}/present",
    response_model=AgentUiHandoffRead,
    status_code=status.HTTP_201_CREATED,
)
async def present_agent_crawl_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentUiHandoffRead:
    return await create_crawl_job_ui_handoff(
        get_session_factory(),
        job_id,
        idempotency_key=idempotency_key,
    )


@router.get("/crawler/jobs", response_model=AgentPage[CrawlJobSummaryRead])
async def list_agent_faculty_crawl_jobs(
    view: Literal["current", "trash"] = Query(default="current"),
    status_filter: str | None = Query(default=None, alias="status"),
    llm_profile_id: int | None = Query(default=None, ge=1),
    requested_model_name: str | None = Query(default=None),
    effective_model_name: str | None = Query(default=None),
    university: str | None = Query(default=None),
    school: str | None = Query(default=None),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[CrawlJobSummaryRead]:
    try:
        jobs = await list_faculty_crawl_job_records(
            session,
            view=view,
            offset=cursor,
            limit=limit + 1,
            status=status_filter,
            llm_profile_id=llm_profile_id,
            requested_model_name=requested_model_name,
            effective_model_name=effective_model_name,
            university=university,
            school=school,
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc
    page, next_cursor, has_more = _slice_page(jobs, cursor=cursor, limit=limit)
    return AgentPage(items=list(page), next_cursor=next_cursor, has_more=has_more)


@router.post(
    "/crawler/jobs",
    response_model=CrawlJobSummaryRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_faculty_crawl_job(
    payload: CrawlJobCreatePayload,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.jobs.create",
            request_data=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            response_type=CrawlJobSummaryRead,
            mutation=lambda: _create_agent_faculty_crawl_job(session, payload),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.post(
    "/crawler/jobs/create-many",
    response_model=AgentCrawlJobBatchCreateRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_many_agent_faculty_crawl_jobs(
    payload: AgentBatchItemsRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCrawlJobBatchCreateRead:
    return await execute_agent_mutation(
        session,
        command="crawler.jobs.create-many",
        request_data=payload.model_dump(mode="json"),
        idempotency_key=idempotency_key,
        response_type=AgentCrawlJobBatchCreateRead,
        mutation=lambda: _create_many_agent_faculty_crawl_jobs(session, payload.items),
    )


@router.get("/crawler/jobs/{job_id}", response_model=CrawlJobSummaryRead)
async def read_agent_faculty_crawl_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await get_faculty_crawl_job_summary(session, job_id)
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.get(
    "/crawler/jobs/{job_id}/events",
    response_model=AgentPage[AgentCrawlJobEventRead],
)
async def list_agent_faculty_crawl_job_events(
    job_id: int,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentCrawlJobEventRead]:
    try:
        job = await get_faculty_crawl_job_or_raise(session, job_id)
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc
    pages = list(
        await session.scalars(
            select(CrawlPage)
            .where(CrawlPage.job_id == job_id)
            .order_by(CrawlPage.created_at.asc(), CrawlPage.id.asc()),
        ),
    )
    candidates = list(
        await session.scalars(
            select(CrawlCandidate)
            .where(
                CrawlCandidate.job_id == job_id,
                canonical_candidate_clause(),
            )
            .order_by(CrawlCandidate.created_at.asc(), CrawlCandidate.id.asc()),
        ),
    )
    events = build_crawl_job_events(job, pages=pages, candidates=candidates)
    page, next_cursor, has_more = _slice_page(events, cursor=cursor, limit=limit)
    return AgentPage(
        items=[AgentCrawlJobEventRead.model_validate(item) for item in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get(
    "/crawler/jobs/{job_id}/pages", response_model=AgentPage[AgentCrawlPageRead]
)
async def list_agent_faculty_crawl_pages(
    job_id: int,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentCrawlPageRead]:
    try:
        pages = await list_faculty_crawl_pages(
            session,
            job_id,
            offset=cursor,
            limit=limit + 1,
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc
    page, next_cursor, has_more = _slice_page(pages, cursor=cursor, limit=limit)
    return AgentPage(
        items=[AgentCrawlPageRead.model_validate(item) for item in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get(
    "/crawler/jobs/{job_id}/candidates",
    response_model=AgentPage[AgentCrawlCandidateRead],
)
async def list_agent_faculty_crawl_candidates(
    job_id: int,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentCrawlCandidateRead]:
    try:
        candidates = await list_faculty_crawl_candidates(
            session,
            job_id,
            offset=cursor,
            limit=limit + 1,
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc
    page, next_cursor, has_more = _slice_page(candidates, cursor=cursor, limit=limit)
    return AgentPage(
        items=[_serialize_crawl_candidate(item) for item in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post(
    "/crawler/jobs/{job_id}/prepare-approve",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_crawl_candidate_approval(
    job_id: int,
    payload: AgentCrawlJobApproveRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_crawl_candidate_approval_change_plan(
        get_session_factory(),
        job_id,
        payload.resolved_selection(),
        idempotency_key=idempotency_key,
    )


@router.post(
    "/crawler/jobs/{job_id}/prepare-retry",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_crawl_job_retry(
    job_id: int,
    payload: AgentCrawlJobRetryRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_crawl_job_retry_change_plan(
        get_session_factory(),
        job_id,
        CrawlJobRetryPayload.model_validate(payload.model_dump()),
        idempotency_key=idempotency_key,
    )


@router.post(
    "/crawler/jobs/{job_id}/enrich",
    response_model=CrawlJobEnrichResult,
    status_code=status.HTTP_201_CREATED,
)
async def enrich_agent_crawl_candidates(
    job_id: int,
    payload: AgentCrawlJobEnrichRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobEnrichResult:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.jobs.enrich",
            request_data={
                "job_id": job_id,
                **payload.model_dump(mode="json", exclude_none=True),
            },
            idempotency_key=idempotency_key,
            response_type=CrawlJobEnrichResult,
            mutation=lambda: enqueue_faculty_crawl_candidate_enrichment_records(
                session,
                job_id,
                payload.resolved_selection(),
                llm_profile_id=payload.llm_profile_id,
                event_name="agent_cli.crawl_candidate_enrichment.queued",
                actor="agent_cli",
            ),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.post(
    "/crawler/jobs/enrich-many",
    response_model=AgentCrawlJobBatchEnrichRead,
    status_code=status.HTTP_201_CREATED,
)
async def enrich_many_agent_crawl_candidates(
    payload: AgentBatchItemsRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCrawlJobBatchEnrichRead:
    return await execute_agent_mutation(
        session,
        command="crawler.jobs.enrich-many",
        request_data=payload.model_dump(mode="json"),
        idempotency_key=idempotency_key,
        response_type=AgentCrawlJobBatchEnrichRead,
        mutation=lambda: _enrich_many_agent_crawl_candidates(session, payload.items),
    )


@router.patch(
    "/crawler/candidates/{candidate_id}", response_model=AgentCrawlCandidateRead
)
async def update_agent_faculty_crawl_candidate(
    candidate_id: int,
    payload: AgentCrawlCandidateUpdateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCrawlCandidateRead:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.candidates.update",
            request_data={
                "candidate_id": candidate_id,
                "if_revision": if_revision,
                **payload.model_dump(mode="json", exclude_unset=True),
            },
            idempotency_key=idempotency_key,
            response_type=AgentCrawlCandidateRead,
            mutation=lambda: _update_agent_faculty_crawl_candidate(
                session,
                candidate_id,
                payload,
                if_revision=if_revision,
            ),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.post("/crawler/jobs/{job_id}/pause", response_model=CrawlJobSummaryRead)
async def pause_agent_faculty_crawl_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.jobs.pause",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=CrawlJobSummaryRead,
            mutation=lambda: _pause_agent_faculty_crawl_job(session, job_id),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.post("/crawler/jobs/{job_id}/resume", response_model=CrawlJobSummaryRead)
async def resume_agent_faculty_crawl_job(
    job_id: int,
    payload: CrawlJobResumePayload | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.jobs.resume",
            request_data={
                "job_id": job_id,
                "payload": payload.model_dump(mode="json")
                if payload is not None
                else None,
            },
            idempotency_key=idempotency_key,
            response_type=CrawlJobSummaryRead,
            mutation=lambda: _resume_agent_faculty_crawl_job(session, job_id, payload),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.post("/crawler/jobs/{job_id}/cancel", response_model=CrawlJobSummaryRead)
async def cancel_agent_faculty_crawl_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.jobs.cancel",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=CrawlJobSummaryRead,
            mutation=lambda: _cancel_agent_faculty_crawl_job(session, job_id),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.post("/crawler/jobs/{job_id}/resume-review", response_model=CrawlJobSummaryRead)
async def resume_agent_faculty_crawl_job_review(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.jobs.resume-review",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=CrawlJobSummaryRead,
            mutation=lambda: _resume_agent_faculty_crawl_job_review(session, job_id),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.post("/crawler/jobs/{job_id}/delete", response_model=CrawlJobSummaryRead)
async def delete_agent_faculty_crawl_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.jobs.delete",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=CrawlJobSummaryRead,
            mutation=lambda: _delete_agent_faculty_crawl_job(session, job_id),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.post("/crawler/jobs/{job_id}/restore", response_model=CrawlJobSummaryRead)
async def restore_agent_faculty_crawl_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.jobs.restore",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=CrawlJobSummaryRead,
            mutation=lambda: _restore_agent_faculty_crawl_job(session, job_id),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


async def _create_agent_faculty_crawl_job(
    session: AsyncSession,
    payload: CrawlJobCreatePayload,
) -> CrawlJobSummaryRead:
    job = await create_faculty_crawl_job_record(
        session,
        payload,
        event_name="agent_cli.crawl_job.created",
        actor="agent_cli",
    )
    return await get_faculty_crawl_job_summary(session, job.id)


async def _create_many_agent_faculty_crawl_jobs(
    session: AsyncSession,
    raw_items: list[dict[str, object]],
) -> AgentCrawlJobBatchCreateRead:
    created_job_ids: list[int] = []
    failures: list[dict[str, object]] = []
    for index, raw_item in enumerate(raw_items):
        try:
            payload = CrawlJobCreatePayload.model_validate(raw_item)
        except ValidationError as exc:
            failures.append(
                {
                    "index": index,
                    "code": "INVALID_BATCH_ITEM",
                    "message": _batch_validation_message(exc),
                    "retryable": False,
                },
            )
            continue
        try:
            async with session.begin_nested():
                job = await create_faculty_crawl_job_record(
                    session,
                    payload,
                    event_name="agent_cli.crawl_job.created",
                    actor="agent_cli",
                )
                created_job_ids.append(job.id)
        except CrawlJobRecordError as exc:
            failures.append(
                {
                    "index": index,
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.status_code >= 500,
                },
            )
    return AgentCrawlJobBatchCreateRead(
        requested_count=len(raw_items),
        created_count=len(created_job_ids),
        failed_count=len(failures),
        created_job_ids=created_job_ids,
        failures=failures,
    )


async def _enrich_many_agent_crawl_candidates(
    session: AsyncSession,
    raw_items: list[dict[str, object]],
) -> AgentCrawlJobBatchEnrichRead:
    items: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for index, raw_item in enumerate(raw_items):
        resource_id = raw_item.get("job_id")
        normalized_resource_id = (
            resource_id
            if isinstance(resource_id, int)
            and not isinstance(resource_id, bool)
            and resource_id > 0
            else None
        )
        try:
            payload = AgentCrawlJobBatchEnrichItem.model_validate(raw_item)
        except ValidationError as exc:
            failures.append(
                {
                    "index": index,
                    "resource_id": normalized_resource_id,
                    "code": "INVALID_BATCH_ITEM",
                    "message": _batch_validation_message(exc),
                    "retryable": False,
                },
            )
            continue
        try:
            async with session.begin_nested():
                result = await enqueue_faculty_crawl_candidate_enrichment_records(
                    session,
                    payload.job_id,
                    payload.selection,
                    llm_profile_id=payload.llm_profile_id,
                    event_name="agent_cli.crawl_candidate_enrichment.queued",
                    actor="agent_cli",
                )
        except CrawlJobRecordError as exc:
            failures.append(
                {
                    "index": index,
                    "resource_id": payload.job_id,
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.code == "CRAWL_CANDIDATE_ENRICHMENT_RUNNING",
                },
            )
            continue
        submission = result.submission
        observation = result.observation
        items.append(
            {
                "job_id": payload.job_id,
                "queued_count": submission.queued_count
                if submission is not None
                else 0,
                "already_active_count": (
                    submission.already_active_count if submission is not None else 0
                ),
                "already_completed_count": (
                    submission.already_completed_count if submission is not None else 0
                ),
                "skipped_count": result.skipped_count,
                "status": observation.status if observation is not None else "unknown",
            },
        )
    return AgentCrawlJobBatchEnrichRead(
        requested_count=len(raw_items),
        accepted_count=len(items),
        failed_count=len(failures),
        queued_count=sum(int(item["queued_count"]) for item in items),
        skipped_count=sum(int(item["skipped_count"]) for item in items),
        items=items,
        failures=failures,
    )


def _batch_validation_message(error: ValidationError) -> str:
    first_error = error.errors(include_url=False)[0]
    location = ".".join(str(item) for item in first_error.get("loc", ()))
    message = str(first_error.get("msg") or "批量项格式无效")
    return f"{location}: {message}" if location else message


async def _update_agent_faculty_crawl_candidate(
    session: AsyncSession,
    candidate_id: int,
    payload: AgentCrawlCandidateUpdateRequest,
    if_revision: str | None = None,
) -> AgentCrawlCandidateRead:
    candidate = await get_faculty_crawl_candidate_or_raise(session, candidate_id)
    current_read = _serialize_crawl_candidate(candidate)
    ensure_revision(
        if_revision,
        current_read.revision,
        resource="crawler.candidates",
        resource_id=candidate_id,
        latest=current_read.model_dump(mode="json"),
    )
    current = {
        "name": candidate.name,
        "email": candidate.email,
        "title": candidate.title,
        "university": candidate.university,
        "school": candidate.school,
        "department": candidate.department,
        "research_direction": candidate.research_direction,
        "recent_papers": candidate.recent_papers or [],
        "profile_url": candidate.profile_url,
        "source_url": candidate.source_url,
        "review_status": candidate.review_status,
    }
    current.update(payload.model_dump(exclude_unset=True))
    updated = await update_faculty_crawl_candidate_record(
        session,
        candidate_id,
        CrawlCandidateUpdatePayload.model_validate(current),
        event_name="agent_cli.crawl_candidate.updated",
        actor="agent_cli",
    )
    return _serialize_crawl_candidate(updated)


async def _pause_agent_faculty_crawl_job(
    session: AsyncSession,
    job_id: int,
) -> CrawlJobSummaryRead:
    job = await pause_faculty_crawl_job_record(
        session,
        job_id,
        event_name="agent_cli.crawl_job.paused",
        actor="agent_cli",
    )
    return await get_faculty_crawl_job_summary(session, job.id)


async def _resume_agent_faculty_crawl_job(
    session: AsyncSession,
    job_id: int,
    payload: CrawlJobResumePayload | None,
) -> CrawlJobSummaryRead:
    job = await resume_faculty_crawl_job_record(
        session,
        job_id,
        payload,
        event_name="agent_cli.crawl_job.resumed",
        actor="agent_cli",
    )
    return await get_faculty_crawl_job_summary(session, job.id)


async def _cancel_agent_faculty_crawl_job(
    session: AsyncSession,
    job_id: int,
) -> CrawlJobSummaryRead:
    job = await cancel_faculty_crawl_job_record(
        session,
        job_id,
        event_name="agent_cli.crawl_job.canceled",
        actor="agent_cli",
    )
    return await get_faculty_crawl_job_summary(session, job.id)


async def _resume_agent_faculty_crawl_job_review(
    session: AsyncSession,
    job_id: int,
) -> CrawlJobSummaryRead:
    job = await resume_faculty_crawl_job_review_record(
        session,
        job_id,
        event_name="agent_cli.crawl_job.review_resumed",
        actor="agent_cli",
    )
    return await get_faculty_crawl_job_summary(session, job.id)


async def _delete_agent_faculty_crawl_job(
    session: AsyncSession,
    job_id: int,
) -> CrawlJobSummaryRead:
    job = await delete_faculty_crawl_job_record(
        session,
        job_id,
        event_name="agent_cli.crawl_job.deleted",
        actor="agent_cli",
    )
    return await get_faculty_crawl_job_summary(session, job.id)


async def _restore_agent_faculty_crawl_job(
    session: AsyncSession,
    job_id: int,
) -> CrawlJobSummaryRead:
    job = await restore_faculty_crawl_job_record(
        session,
        job_id,
        event_name="agent_cli.crawl_job.restored",
        actor="agent_cli",
    )
    return await get_faculty_crawl_job_summary(session, job.id)


def _agent_crawl_job_error(error: CrawlJobRecordError) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )


def _serialize_crawl_candidate(
    candidate: CrawlCandidate | object,
) -> AgentCrawlCandidateRead:
    result = AgentCrawlCandidateRead.model_validate(candidate)
    return result.model_copy(update={"revision": revision_for(result)})
