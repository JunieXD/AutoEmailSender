from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_async_session, get_session_factory
from app.models import CrawlJob, CrawlJobKind, CrawlJobTriggerMode
from .schemas import (
    CreateProfessorInformationEnrichmentJobRequest,
    CreateProfessorInformationEnrichmentRequest,
    ProfessorInformationEnrichmentActiveRead,
    ProfessorInformationEnrichmentItemRead,
    ProfessorInformationEnrichmentItemsPageRead,
    ProfessorInformationEnrichmentJobActionRead,
    ProfessorInformationEnrichmentJobRead,
)
from .service import (
    create_professor_information_enrichment_job,
    delete_professor_information_enrichment_job_record,
    get_active_professor_information_enrichment_job,
    get_professor_information_enrichment_job,
    list_professor_information_enrichment_items,
    list_professor_information_enrichment_items_page,
    list_professor_information_enrichment_jobs,
    request_professor_information_enrichment_cancel,
    restore_professor_information_enrichment_job_record,
    retry_failed_professor_information_enrichment_job,
    serialize_professor_information_enrichment_job,
)


router = APIRouter(
    prefix="/api/professor-information-enrichment-jobs",
    tags=["professor-information-enrichment-jobs"],
)
professor_router = APIRouter(prefix="/api/professors", tags=["professors"])
JobListLimit = Annotated[int, Query(ge=1, le=100)]


@professor_router.post(
    "/{professor_id}/information-enrichment",
    response_model=ProfessorInformationEnrichmentJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_single_professor_information_enrichment(
    professor_id: int,
    payload: CreateProfessorInformationEnrichmentRequest,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobRead:
    try:
        job_id = await create_professor_information_enrichment_job(
            get_session_factory(),
            professor_ids=[professor_id],
            llm_profile_id=payload.llm_profile_id,
            trigger_mode=CrawlJobTriggerMode.SINGLE.value,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="该导师已有信息补全正在进行") from exc
    except ValueError as exc:
        detail = str(exc)
        status_code_value = 404 if detail == "导师不存在" else 422
        raise HTTPException(status_code=status_code_value, detail=detail) from exc
    result = await get_professor_information_enrichment_job(session, job_id)
    if result is None:  # pragma: no cover - committed job cannot disappear in normal execution
        raise HTTPException(status_code=404, detail="信息补全任务不存在")
    return result


@professor_router.get(
    "/{professor_id}/information-enrichment/active",
    response_model=ProfessorInformationEnrichmentActiveRead,
)
async def get_single_professor_information_enrichment_active(
    professor_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentActiveRead:
    job = await get_active_professor_information_enrichment_job(session, professor_id)
    return ProfessorInformationEnrichmentActiveRead(active=job is not None, job=job)


@router.get("", response_model=list[ProfessorInformationEnrichmentJobRead])
async def list_information_enrichment_jobs(
    view: str = Query(default="current"),
    limit: JobListLimit = 50,
    session: AsyncSession = Depends(get_async_session),
) -> list[ProfessorInformationEnrichmentJobRead]:
    try:
        return await list_professor_information_enrichment_jobs(
            session,
            view=view,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "",
    response_model=ProfessorInformationEnrichmentJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_batch_information_enrichment_job(
    payload: CreateProfessorInformationEnrichmentJobRequest,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobRead:
    try:
        job_id = await create_professor_information_enrichment_job(
            get_session_factory(),
            professor_ids=payload.professor_ids,
            llm_profile_id=payload.llm_profile_id,
            trigger_mode=CrawlJobTriggerMode.BATCH.value,
            name=payload.name,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="部分导师已有信息补全正在进行，请重试") from exc
    except ValueError as exc:
        detail = str(exc)
        status_code_value = 404 if detail == "导师不存在" else 422
        raise HTTPException(status_code=status_code_value, detail=detail) from exc
    result = await get_professor_information_enrichment_job(session, job_id)
    if result is None:  # pragma: no cover
        raise HTTPException(status_code=404, detail="信息补全任务不存在")
    return result


@router.get("/{job_id}", response_model=ProfessorInformationEnrichmentJobRead)
async def get_information_enrichment_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobRead:
    job = await get_professor_information_enrichment_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="信息补全任务不存在")
    return job


@router.get(
    "/{job_id}/items",
    response_model=list[ProfessorInformationEnrichmentItemRead],
)
async def list_information_enrichment_job_items(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> list[ProfessorInformationEnrichmentItemRead]:
    items = await list_professor_information_enrichment_items(session, job_id)
    if items is None:
        raise HTTPException(status_code=404, detail="信息补全任务不存在")
    return items


@router.get(
    "/{job_id}/items/page",
    response_model=ProfessorInformationEnrichmentItemsPageRead,
)
async def list_information_enrichment_job_items_page(
    job_id: int,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentItemsPageRead:
    try:
        page = await list_professor_information_enrichment_items_page(
            session,
            job_id,
            cursor=cursor,
            limit=limit,
            status_filter=status_filter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if page is None:
        raise HTTPException(status_code=404, detail="信息补全任务不存在")
    return page


@router.post(
    "/{job_id}/cancel",
    response_model=ProfessorInformationEnrichmentJobActionRead,
)
async def cancel_information_enrichment_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobActionRead:
    job = await _get_visible_job_or_404(session, job_id)
    await request_professor_information_enrichment_cancel(session, job)
    await session.commit()
    refreshed = await _get_visible_job_or_404(session, job_id)
    return ProfessorInformationEnrichmentJobActionRead(
        ok=True,
        job=await serialize_professor_information_enrichment_job(session, refreshed),
    )


@router.post(
    "/{job_id}/retry-failed",
    response_model=ProfessorInformationEnrichmentJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def retry_failed_information_enrichment_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobRead:
    try:
        new_job_id = await retry_failed_professor_information_enrichment_job(
            get_session_factory(),
            job_id,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code_value = 404 if "不存在" in detail else 400
        raise HTTPException(status_code=status_code_value, detail=detail) from exc
    result = await get_professor_information_enrichment_job(session, new_job_id)
    if result is None:  # pragma: no cover
        raise HTTPException(status_code=404, detail="信息补全任务不存在")
    return result


@router.delete(
    "/{job_id}",
    response_model=ProfessorInformationEnrichmentJobActionRead,
)
async def delete_information_enrichment_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobActionRead:
    await _get_visible_job_or_404(session, job_id)
    try:
        await delete_professor_information_enrichment_job_record(session, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    refreshed = await _get_visible_job_or_404(session, job_id)
    return ProfessorInformationEnrichmentJobActionRead(
        ok=True,
        job=await serialize_professor_information_enrichment_job(session, refreshed),
    )


@router.post(
    "/{job_id}/restore",
    response_model=ProfessorInformationEnrichmentJobActionRead,
)
async def restore_information_enrichment_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobActionRead:
    await _get_visible_job_or_404(session, job_id)
    try:
        await restore_professor_information_enrichment_job_record(session, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    refreshed = await _get_visible_job_or_404(session, job_id)
    return ProfessorInformationEnrichmentJobActionRead(
        ok=True,
        job=await serialize_professor_information_enrichment_job(session, refreshed),
    )


async def _get_visible_job_or_404(session: AsyncSession, job_id: int) -> CrawlJob:
    job = await session.scalar(
        select(CrawlJob)
        .options(selectinload(CrawlJob.current_run))
        .where(
            CrawlJob.id == job_id,
            CrawlJob.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value,
            CrawlJob.task_center_visible.is_(True),
        )
    )
    if job is None:
        raise HTTPException(status_code=404, detail="信息补全任务不存在")
    return job
