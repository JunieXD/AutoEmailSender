from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_async_session, get_session_factory
from app.models import (
    EmailTask,
    MatchAnalysisJob,
    MatchAnalysisJobItem,
    MatchAnalysisRun,
    Professor,
)
from .schemas import (
    CreateMatchAnalysisJobRequest,
    MatchAnalysisJobActionResponse,
    MatchAnalysisJobItemsPageRead,
    MatchAnalysisJobRead,
    MatchAnalysisSelectionSummaryRead,
    MatchAnalysisSelectionSummaryRequest,
)
from .job_runtime import (
    create_match_analysis_job,
    delete_match_analysis_job_record,
    request_match_analysis_job_cancel,
    restore_match_analysis_job_record,
    retry_failed_match_analysis_job,
    serialize_match_analysis_job,
    serialize_match_analysis_job_item,
    summarize_match_analysis_selection,
)
router = APIRouter(prefix="/api/match-analysis-jobs", tags=["match-analysis-jobs"])


@router.get("", response_model=list[MatchAnalysisJobRead])
async def list_match_analysis_jobs(
    identity_id: int | None = Query(default=None),
    llm_profile_id: int | None = Query(default=None),
    view: str = Query(default="current"),
    session: AsyncSession = Depends(get_async_session),
) -> list[MatchAnalysisJobRead]:
    statement = select(MatchAnalysisJob).order_by(
        MatchAnalysisJob.created_at.desc(),
        MatchAnalysisJob.id.desc(),
    )
    if identity_id is not None:
        statement = statement.where(MatchAnalysisJob.identity_id == identity_id)
    if view == "trash":
        statement = statement.where(MatchAnalysisJob.deleted_at.is_not(None))
    elif view == "current":
        statement = statement.where(MatchAnalysisJob.deleted_at.is_(None))
    else:
        raise HTTPException(status_code=400, detail="未知任务视图")
    jobs = list(await session.scalars(statement))
    return [serialize_match_analysis_job(job) for job in jobs]


@router.post("", response_model=MatchAnalysisJobRead, status_code=status.HTTP_201_CREATED)
async def create_job(payload: CreateMatchAnalysisJobRequest) -> MatchAnalysisJobRead:
    try:
        job = await create_match_analysis_job(
            get_session_factory(),
            identity_id=payload.identity_id,
            llm_profile_id=payload.llm_profile_id,
            professor_ids=payload.professor_ids,
            name=payload.name,
            skip_existing=payload.skip_existing,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return serialize_match_analysis_job(job)


@router.post(
    "/selection-summary",
    response_model=MatchAnalysisSelectionSummaryRead,
)
async def get_match_analysis_selection_summary(
    payload: MatchAnalysisSelectionSummaryRequest,
    session: AsyncSession = Depends(get_async_session),
) -> MatchAnalysisSelectionSummaryRead:
    try:
        return await summarize_match_analysis_selection(
            session,
            identity_id=payload.identity_id,
            professor_ids=payload.professor_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{job_id}", response_model=MatchAnalysisJobRead)
async def get_match_analysis_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> MatchAnalysisJobRead:
    job = await session.get(MatchAnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="匹配分析任务不存在")
    return serialize_match_analysis_job(job)


@router.get("/{job_id}/items", response_model=MatchAnalysisJobItemsPageRead)
async def list_match_analysis_job_items(
    job_id: int,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_async_session),
) -> MatchAnalysisJobItemsPageRead:
    job_exists = await session.scalar(select(MatchAnalysisJob.id).where(MatchAnalysisJob.id == job_id))
    if job_exists is None:
        raise HTTPException(status_code=404, detail="匹配分析任务不存在")
    filters = [MatchAnalysisJobItem.job_id == job_id]
    if status_filter is not None:
        filters.append(MatchAnalysisJobItem.status == status_filter)
    total_count = await session.scalar(
        select(func.count()).select_from(MatchAnalysisJobItem).where(*filters)
    )
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
            .where(*filters)
            .order_by(MatchAnalysisJobItem.id.asc())
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    has_more = len(items) > limit
    page = items[:limit]
    return MatchAnalysisJobItemsPageRead(
        items=[serialize_match_analysis_job_item(item) for item in page],
        total_count=total_count or 0,
        next_cursor=cursor + limit if has_more else None,
        has_more=has_more,
    )


@router.post("/{job_id}/cancel", response_model=MatchAnalysisJobActionResponse)
async def cancel_match_analysis_job(job_id: int) -> MatchAnalysisJobActionResponse:
    try:
        job = await request_match_analysis_job_cancel(get_session_factory(), job_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "不存在" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return MatchAnalysisJobActionResponse(ok=True, job=serialize_match_analysis_job(job))


@router.post("/{job_id}/retry-failed", response_model=MatchAnalysisJobRead, status_code=status.HTTP_201_CREATED)
async def retry_failed_match_analysis_job_api(job_id: int) -> MatchAnalysisJobRead:
    try:
        job = await retry_failed_match_analysis_job(get_session_factory(), job_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "不存在" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return serialize_match_analysis_job(job)


@router.post("/{job_id}/delete", response_model=MatchAnalysisJobActionResponse)
async def delete_match_analysis_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> MatchAnalysisJobActionResponse:
    try:
        job = await delete_match_analysis_job_record(session, job_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "不存在" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    await session.commit()
    await session.refresh(job)
    return MatchAnalysisJobActionResponse(ok=True, job=serialize_match_analysis_job(job))


@router.post("/{job_id}/restore", response_model=MatchAnalysisJobActionResponse)
async def restore_match_analysis_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> MatchAnalysisJobActionResponse:
    try:
        job = await restore_match_analysis_job_record(session, job_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "不存在" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    await session.commit()
    await session.refresh(job)
    return MatchAnalysisJobActionResponse(ok=True, job=serialize_match_analysis_job(job))
