from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.agent_api_errors import AgentApiError
from app.core.database import get_async_session
from app.models import (
    EmailTask,
    MatchAnalysisJob,
    MatchAnalysisJobItem,
    MatchAnalysisRun,
    Professor,
)
from app.modules.matching.public import (
    create_match_analysis_job_record,
    delete_match_analysis_job_record,
    match_analysis_job_item_score,
    request_match_analysis_job_cancel_record,
    restore_match_analysis_job_record,
    retry_failed_match_analysis_job_record,
)
from app.schemas.agent import (
    AgentMatchAnalysisJobActionRead,
    AgentMatchAnalysisJobCreateRequest,
    AgentMatchAnalysisJobItemRead,
    AgentMatchAnalysisJobRead,
    AgentPage,
)
from app.services.agent_mutations import execute_agent_mutation

from .support import (
    _slice_page,
)

router = APIRouter()


@router.get("/matching/jobs", response_model=AgentPage[AgentMatchAnalysisJobRead])
async def list_agent_match_analysis_jobs(
    identity_id: int | None = Query(default=None, ge=1),
    llm_profile_id: int | None = Query(default=None, ge=1),
    status_filter: str | None = Query(default=None, alias="status"),
    view: Literal["current", "trash"] = Query(default="current"),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentMatchAnalysisJobRead]:
    statement = select(MatchAnalysisJob)
    if identity_id is not None:
        statement = statement.where(MatchAnalysisJob.identity_id == identity_id)
    if llm_profile_id is not None:
        statement = statement.where(MatchAnalysisJob.llm_profile_id == llm_profile_id)
    if status_filter is not None:
        statement = statement.where(MatchAnalysisJob.status == status_filter)
    if view == "trash":
        statement = statement.where(MatchAnalysisJob.deleted_at.is_not(None))
    else:
        statement = statement.where(MatchAnalysisJob.deleted_at.is_(None))
    jobs = list(
        await session.scalars(
            statement.order_by(
                MatchAnalysisJob.created_at.desc(), MatchAnalysisJob.id.desc()
            )
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(jobs, cursor=cursor, limit=limit)
    return AgentPage(
        items=[_serialize_match_analysis_job(job) for job in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post(
    "/matching/jobs",
    response_model=AgentMatchAnalysisJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_match_analysis_job(
    payload: AgentMatchAnalysisJobCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMatchAnalysisJobRead:
    try:
        return await execute_agent_mutation(
            session,
            command="matching.jobs.create",
            request_data=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            response_type=AgentMatchAnalysisJobRead,
            mutation=lambda: _create_agent_match_analysis_job(session, payload),
        )
    except ValueError as exc:
        raise _agent_match_analysis_error(exc) from exc


@router.get("/matching/jobs/{job_id}", response_model=AgentMatchAnalysisJobRead)
async def read_agent_match_analysis_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentMatchAnalysisJobRead:
    job = await session.get(MatchAnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="匹配分析任务不存在")
    return _serialize_match_analysis_job(job)


@router.get(
    "/matching/jobs/{job_id}/items",
    response_model=AgentPage[AgentMatchAnalysisJobItemRead],
)
async def list_agent_match_analysis_job_items(
    job_id: int,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentMatchAnalysisJobItemRead]:
    if await session.get(MatchAnalysisJob, job_id) is None:
        raise HTTPException(status_code=404, detail="匹配分析任务不存在")
    items = list(
        await session.scalars(
            select(MatchAnalysisJobItem)
            .options(
                selectinload(MatchAnalysisJobItem.professor)
                .load_only(
                    Professor.id,
                    Professor.name,
                    Professor.email,
                    Professor.title,
                    Professor.university,
                    Professor.school,
                )
                .lazyload(Professor.tags),
                selectinload(MatchAnalysisJobItem.email_task).load_only(
                    EmailTask.id,
                    EmailTask.match_score,
                ),
                selectinload(MatchAnalysisJobItem.match_analysis_run).load_only(
                    MatchAnalysisRun.id,
                    MatchAnalysisRun.match_score,
                ),
            )
            .where(MatchAnalysisJobItem.job_id == job_id)
            .order_by(MatchAnalysisJobItem.id.asc())
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(items, cursor=cursor, limit=limit)
    return AgentPage(
        items=[_serialize_match_analysis_job_item(item) for item in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post(
    "/matching/jobs/{job_id}/cancel",
    response_model=AgentMatchAnalysisJobActionRead,
)
async def cancel_agent_match_analysis_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMatchAnalysisJobActionRead:
    try:
        return await execute_agent_mutation(
            session,
            command="matching.jobs.cancel",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=AgentMatchAnalysisJobActionRead,
            mutation=lambda: _cancel_agent_match_analysis_job(session, job_id),
        )
    except ValueError as exc:
        raise _agent_match_analysis_error(exc) from exc


@router.post(
    "/matching/jobs/{job_id}/retry-failed",
    response_model=AgentMatchAnalysisJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def retry_agent_match_analysis_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMatchAnalysisJobRead:
    try:
        return await execute_agent_mutation(
            session,
            command="matching.jobs.retry-failed",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=AgentMatchAnalysisJobRead,
            mutation=lambda: _retry_agent_match_analysis_job(session, job_id),
        )
    except ValueError as exc:
        raise _agent_match_analysis_error(exc) from exc


@router.post(
    "/matching/jobs/{job_id}/delete",
    response_model=AgentMatchAnalysisJobActionRead,
)
async def delete_agent_match_analysis_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMatchAnalysisJobActionRead:
    try:
        return await execute_agent_mutation(
            session,
            command="matching.jobs.delete",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=AgentMatchAnalysisJobActionRead,
            mutation=lambda: _delete_agent_match_analysis_job(session, job_id),
        )
    except ValueError as exc:
        raise _agent_match_analysis_error(exc) from exc


@router.post(
    "/matching/jobs/{job_id}/restore",
    response_model=AgentMatchAnalysisJobActionRead,
)
async def restore_agent_match_analysis_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMatchAnalysisJobActionRead:
    try:
        return await execute_agent_mutation(
            session,
            command="matching.jobs.restore",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=AgentMatchAnalysisJobActionRead,
            mutation=lambda: _restore_agent_match_analysis_job(session, job_id),
        )
    except ValueError as exc:
        raise _agent_match_analysis_error(exc) from exc


async def _create_agent_match_analysis_job(
    session: AsyncSession,
    payload: AgentMatchAnalysisJobCreateRequest,
) -> AgentMatchAnalysisJobRead:
    job = await create_match_analysis_job_record(
        session,
        identity_id=payload.identity_id,
        llm_profile_id=payload.llm_profile_id,
        professor_ids=payload.professor_ids,
        name=payload.name,
        event_name="agent_cli.match_analysis_job.created",
        actor="agent_cli",
    )
    return _serialize_match_analysis_job(job)


async def _cancel_agent_match_analysis_job(
    session: AsyncSession,
    job_id: int,
) -> AgentMatchAnalysisJobActionRead:
    job = await request_match_analysis_job_cancel_record(
        session,
        job_id,
        event_name="agent_cli.match_analysis_job.cancel_requested",
        actor="agent_cli",
    )
    return AgentMatchAnalysisJobActionRead(
        ok=True,
        job=_serialize_match_analysis_job(job),
    )


async def _retry_agent_match_analysis_job(
    session: AsyncSession,
    job_id: int,
) -> AgentMatchAnalysisJobRead:
    job = await retry_failed_match_analysis_job_record(
        session,
        job_id,
        event_name="agent_cli.match_analysis_job.retry_created",
        actor="agent_cli",
    )
    return _serialize_match_analysis_job(job)


async def _delete_agent_match_analysis_job(
    session: AsyncSession,
    job_id: int,
) -> AgentMatchAnalysisJobActionRead:
    job = await delete_match_analysis_job_record(
        session,
        job_id,
        event_name="agent_cli.match_analysis_job.deleted",
        actor="agent_cli",
    )
    return AgentMatchAnalysisJobActionRead(
        ok=True,
        job=_serialize_match_analysis_job(job),
    )


async def _restore_agent_match_analysis_job(
    session: AsyncSession,
    job_id: int,
) -> AgentMatchAnalysisJobActionRead:
    job = await restore_match_analysis_job_record(
        session,
        job_id,
        event_name="agent_cli.match_analysis_job.restored",
        actor="agent_cli",
    )
    return AgentMatchAnalysisJobActionRead(
        ok=True,
        job=_serialize_match_analysis_job(job),
    )


def _agent_match_analysis_error(error: ValueError) -> AgentApiError:
    message = str(error)
    return AgentApiError(
        status_code=404 if "不存在" in message else 409,
        code="MATCH_ANALYSIS_OPERATION_REJECTED",
        message=message,
    )


def _serialize_match_analysis_job(job: MatchAnalysisJob) -> AgentMatchAnalysisJobRead:
    return AgentMatchAnalysisJobRead(
        id=job.id,
        name=job.name,
        status=job.status,
        target_count=job.target_count,
        succeeded_count=job.succeeded_count,
        failed_count=job.failed_count,
        skipped_count=job.skipped_count,
        total_prompt_tokens=job.total_prompt_tokens,
        total_completion_tokens=job.total_completion_tokens,
        total_cached_tokens=job.total_cached_tokens,
        total_tokens=job.total_tokens,
        identity_id=job.identity_id,
        match_source_identity_id=job.match_source_identity_id,
        llm_profile_id=job.llm_profile_id,
        cancel_requested_at=job.cancel_requested_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        deleted_at=job.deleted_at,
        last_error=job.last_error,
    )


def _serialize_match_analysis_job_item(
    item: MatchAnalysisJobItem,
) -> AgentMatchAnalysisJobItemRead:
    return AgentMatchAnalysisJobItemRead(
        id=item.id,
        job_id=item.job_id,
        professor_id=item.professor_id,
        professor_name=item.professor.name,
        professor_email=item.professor.email,
        professor_title=item.professor.title,
        professor_university=item.professor.university,
        professor_school=item.professor.school,
        email_task_id=item.email_task_id,
        status=item.status,
        match_score=match_analysis_job_item_score(item),
        match_analysis_run_id=item.match_analysis_run_id,
        error_message=item.error_message,
        skip_reason=item.skip_reason,
        prompt_tokens=item.prompt_tokens,
        completion_tokens=item.completion_tokens,
        cached_tokens=item.cached_tokens,
        total_tokens=item.total_tokens,
        started_at=item.started_at,
        finished_at=item.finished_at,
        updated_at=item.updated_at,
    )
