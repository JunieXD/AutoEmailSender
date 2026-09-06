from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.core.database import get_async_session
from app.models import CrawlJob, CrawlJobTriggerMode
from app.modules.professors.public import (
    CreateProfessorInformationEnrichmentJobRequest,
    ProfessorInformationEnrichmentItemRead,
    ProfessorInformationEnrichmentJobActionRead,
    ProfessorInformationEnrichmentJobRead,
    create_professor_information_enrichment_job_record,
    delete_professor_information_enrichment_job_record,
    get_professor_information_enrichment_job,
    list_professor_information_enrichment_items_page,
    list_professor_information_enrichment_jobs,
    request_professor_information_enrichment_cancel,
    restore_professor_information_enrichment_job_record,
    retry_failed_professor_information_enrichment_job_record,
)
from app.schemas.agent import AgentPage
from app.services.agent_mutations import execute_agent_mutation

from .support import (
    _slice_page,
)

router = APIRouter()


@router.get(
    "/enrichment/jobs",
    response_model=AgentPage[ProfessorInformationEnrichmentJobRead],
)
async def list_agent_professor_information_enrichment_jobs(
    view: Literal["current", "trash"] = Query(default="current"),
    status_filter: str | None = Query(default=None, alias="status"),
    llm_profile_id: int | None = Query(default=None, ge=1),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[ProfessorInformationEnrichmentJobRead]:
    try:
        jobs = await list_professor_information_enrichment_jobs(
            session,
            view=view,
            status=status_filter,
            llm_profile_id=llm_profile_id,
            offset=cursor,
            limit=limit + 1,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=422,
            code="INVALID_ENRICHMENT_JOB_VIEW",
            message=str(exc),
        ) from exc
    page, next_cursor, has_more = _slice_page(jobs, cursor=cursor, limit=limit)
    return AgentPage(items=list(page), next_cursor=next_cursor, has_more=has_more)


@router.post(
    "/enrichment/jobs",
    response_model=ProfessorInformationEnrichmentJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_professor_information_enrichment_job(
    payload: CreateProfessorInformationEnrichmentJobRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobRead:
    try:
        return await execute_agent_mutation(
            session,
            command="enrichment.jobs.create",
            request_data=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            response_type=ProfessorInformationEnrichmentJobRead,
            mutation=lambda: _create_agent_professor_information_enrichment_job(
                session, payload
            ),
        )
    except RuntimeError as exc:
        raise _agent_information_enrichment_error(exc, status_code=409) from exc
    except ValueError as exc:
        raise _agent_information_enrichment_error(exc) from exc


@router.get(
    "/enrichment/jobs/{job_id}",
    response_model=ProfessorInformationEnrichmentJobRead,
)
async def read_agent_professor_information_enrichment_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobRead:
    job = await get_professor_information_enrichment_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="信息补全任务不存在")
    return job


@router.get(
    "/enrichment/jobs/{job_id}/items",
    response_model=AgentPage[ProfessorInformationEnrichmentItemRead],
)
async def list_agent_professor_information_enrichment_job_items(
    job_id: int,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[ProfessorInformationEnrichmentItemRead]:
    page = await list_professor_information_enrichment_items_page(
        session,
        job_id,
        cursor=cursor,
        limit=limit,
    )
    if page is None:
        raise HTTPException(status_code=404, detail="信息补全任务不存在")
    return AgentPage(
        items=page.items,
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.post(
    "/enrichment/jobs/{job_id}/cancel",
    response_model=ProfessorInformationEnrichmentJobActionRead,
)
async def cancel_agent_professor_information_enrichment_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobActionRead:
    try:
        return await execute_agent_mutation(
            session,
            command="enrichment.jobs.cancel",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=ProfessorInformationEnrichmentJobActionRead,
            mutation=lambda: _cancel_agent_professor_information_enrichment_job(
                session, job_id
            ),
        )
    except ValueError as exc:
        raise _agent_information_enrichment_error(exc) from exc


@router.post(
    "/enrichment/jobs/{job_id}/retry-failed",
    response_model=ProfessorInformationEnrichmentJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def retry_agent_professor_information_enrichment_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobRead:
    try:
        return await execute_agent_mutation(
            session,
            command="enrichment.jobs.retry-failed",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=ProfessorInformationEnrichmentJobRead,
            mutation=lambda: _retry_agent_professor_information_enrichment_job(
                session, job_id
            ),
        )
    except ValueError as exc:
        raise _agent_information_enrichment_error(exc) from exc


@router.post(
    "/enrichment/jobs/{job_id}/delete",
    response_model=ProfessorInformationEnrichmentJobActionRead,
)
async def delete_agent_professor_information_enrichment_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobActionRead:
    try:
        return await execute_agent_mutation(
            session,
            command="enrichment.jobs.delete",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=ProfessorInformationEnrichmentJobActionRead,
            mutation=lambda: _delete_agent_professor_information_enrichment_job(
                session, job_id
            ),
        )
    except ValueError as exc:
        raise _agent_information_enrichment_error(exc) from exc


@router.post(
    "/enrichment/jobs/{job_id}/restore",
    response_model=ProfessorInformationEnrichmentJobActionRead,
)
async def restore_agent_professor_information_enrichment_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobActionRead:
    try:
        return await execute_agent_mutation(
            session,
            command="enrichment.jobs.restore",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=ProfessorInformationEnrichmentJobActionRead,
            mutation=lambda: _restore_agent_professor_information_enrichment_job(
                session, job_id
            ),
        )
    except ValueError as exc:
        raise _agent_information_enrichment_error(exc) from exc


async def _create_agent_professor_information_enrichment_job(
    session: AsyncSession,
    payload: CreateProfessorInformationEnrichmentJobRequest,
) -> ProfessorInformationEnrichmentJobRead:
    job = await create_professor_information_enrichment_job_record(
        session,
        professor_ids=payload.professor_ids,
        llm_profile_id=payload.llm_profile_id,
        trigger_mode=CrawlJobTriggerMode.BATCH.value,
        name=payload.name,
        event_name="agent_cli.professor_information_enrichment_job.created",
        actor="agent_cli",
    )
    result = await get_professor_information_enrichment_job(session, job.id)
    if (
        result is None
    ):  # pragma: no cover - the record was just created in this transaction
        raise ValueError("信息补全任务不存在")
    return result


async def _cancel_agent_professor_information_enrichment_job(
    session: AsyncSession,
    job_id: int,
) -> ProfessorInformationEnrichmentJobActionRead:
    existing = await get_professor_information_enrichment_job(session, job_id)
    if existing is None:
        raise ValueError("信息补全任务不存在")
    job = await session.get(CrawlJob, job_id)
    if job is None:  # pragma: no cover - checked immediately above
        raise ValueError("信息补全任务不存在")
    await request_professor_information_enrichment_cancel(
        session,
        job,
        event_name="agent_cli.professor_information_enrichment_job.cancel_requested",
        actor="agent_cli",
    )
    result = await get_professor_information_enrichment_job(session, job_id)
    if (
        result is None
    ):  # pragma: no cover - the record cannot disappear in this transaction
        raise ValueError("信息补全任务不存在")
    return ProfessorInformationEnrichmentJobActionRead(ok=True, job=result)


async def _retry_agent_professor_information_enrichment_job(
    session: AsyncSession,
    job_id: int,
) -> ProfessorInformationEnrichmentJobRead:
    job = await retry_failed_professor_information_enrichment_job_record(
        session,
        job_id,
        actor="agent_cli",
    )
    result = await get_professor_information_enrichment_job(session, job.id)
    if (
        result is None
    ):  # pragma: no cover - the record was just created in this transaction
        raise ValueError("信息补全任务不存在")
    return result


async def _delete_agent_professor_information_enrichment_job(
    session: AsyncSession,
    job_id: int,
) -> ProfessorInformationEnrichmentJobActionRead:
    job = await delete_professor_information_enrichment_job_record(
        session,
        job_id,
        event_name="agent_cli.professor_information_enrichment_job.deleted",
        actor="agent_cli",
    )
    result = await get_professor_information_enrichment_job(session, job.id)
    if (
        result is None
    ):  # pragma: no cover - the record cannot disappear in this transaction
        raise ValueError("信息补全任务不存在")
    return ProfessorInformationEnrichmentJobActionRead(ok=True, job=result)


async def _restore_agent_professor_information_enrichment_job(
    session: AsyncSession,
    job_id: int,
) -> ProfessorInformationEnrichmentJobActionRead:
    job = await restore_professor_information_enrichment_job_record(
        session,
        job_id,
        event_name="agent_cli.professor_information_enrichment_job.restored",
        actor="agent_cli",
    )
    result = await get_professor_information_enrichment_job(session, job.id)
    if (
        result is None
    ):  # pragma: no cover - the record cannot disappear in this transaction
        raise ValueError("信息补全任务不存在")
    return ProfessorInformationEnrichmentJobActionRead(ok=True, job=result)


def _agent_information_enrichment_error(
    error: ValueError | RuntimeError,
    *,
    status_code: int | None = None,
) -> AgentApiError:
    message = str(error)
    if status_code is None:
        if "不存在" in message:
            status_code = 404
        elif "已有" in message or "请先取消" in message:
            status_code = 409
        else:
            status_code = 422
    return AgentApiError(
        status_code=status_code,
        code="INFORMATION_ENRICHMENT_OPERATION_REJECTED",
        message=message,
    )
