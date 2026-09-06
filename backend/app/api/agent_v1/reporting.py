from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.core.config import get_settings
from app.core.database import get_async_session
from app.core.time import utc_now
from app.models import OperationLog
from app.modules.crawler.public import crawler_debug_file_path
from app.schemas.dashboard import DashboardOverviewRead
from app.schemas.diagnostics import (
    DiagnosticFileRead,
    OperationLogExportResponse,
    OperationLogListResponse,
    OperationLogRead,
)
from app.schemas.token_usage import (
    TokenUsageChartPreset,
    TokenUsageChartRead,
    TokenUsageFeatureFilter,
    TokenUsageRecordListRead,
    TokenUsageVisualizationRead,
)
from app.services.dashboard_stats import build_dashboard_overview
from app.services.operation_logs import (
    build_operation_log_filters,
    sanitize_diagnostic_metadata,
    sanitize_diagnostic_text,
)
from app.services.token_usage_records import (
    build_token_usage_chart,
    build_token_usage_visualization,
    list_token_usage_records,
)

from .support import (
    _project_agent_collection_response,
)

router = APIRouter()


@router.get(
    "/diagnostics/operation-logs",
    response_model=OperationLogListResponse,
)
async def list_agent_operation_logs(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    level: str | None = Query(default=None),
    category: str | None = Query(default=None),
    event_name: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    fields: str | None = Query(default=None, max_length=4_000),
    session: AsyncSession = Depends(get_async_session),
) -> OperationLogListResponse | Response:
    filters = build_operation_log_filters(
        level=level,
        category=category,
        event_name=event_name,
        request_id=request_id,
        entity_type=entity_type,
        entity_id=entity_id,
        start_at=start_at,
        end_at=end_at,
    )
    total = int(
        await session.scalar(
            select(func.count()).select_from(OperationLog).where(*filters),
        )
        or 0,
    )
    logs = list(
        (
            await session.scalars(
                select(OperationLog)
                .where(*filters)
                .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
                .limit(limit)
                .offset(offset),
            )
        ).all(),
    )
    response = OperationLogListResponse(
        items=[_serialize_agent_operation_log(log) for log in logs],
        total=total,
        limit=limit,
        offset=offset,
    )
    return _project_agent_collection_response(response, fields)


@router.get("/diagnostics/export", response_model=OperationLogExportResponse)
async def export_agent_operation_logs(
    level: str | None = Query(default=None),
    category: str | None = Query(default=None),
    event_name: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> OperationLogExportResponse:
    filters = build_operation_log_filters(
        level=level,
        category=category,
        event_name=event_name,
        request_id=request_id,
        entity_type=entity_type,
        entity_id=entity_id,
        start_at=start_at,
        end_at=end_at,
    )
    total = int(
        await session.scalar(
            select(func.count()).select_from(OperationLog).where(*filters),
        )
        or 0,
    )
    logs = list(
        (
            await session.scalars(
                select(OperationLog)
                .where(*filters)
                .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
                .limit(500),
            )
        ).all(),
    )
    return OperationLogExportResponse(
        exported_at=utc_now(),
        total=total,
        items=[_serialize_agent_operation_log(log) for log in logs],
        filters={
            "level": level,
            "category": category,
            "event_name": event_name,
            "request_id": request_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "start_at": start_at.isoformat() if start_at else None,
            "end_at": end_at.isoformat() if end_at else None,
        },
        startup_logs=_read_agent_startup_logs(),
    )


@router.get("/diagnostics/crawler-debug/{job_id}/export")
async def export_agent_crawler_debug_log(job_id: int) -> Response:
    debug_file = crawler_debug_file_path(job_id)
    if not debug_file.is_file():
        raise AgentApiError(
            status_code=404,
            code="CRAWLER_DEBUG_LOG_NOT_FOUND",
            message="未找到该抓取任务的调试日志。",
        )
    try:
        content = sanitize_diagnostic_text(
            debug_file.read_text(encoding="utf-8", errors="replace"),
        )
    except OSError as exc:
        raise AgentApiError(
            status_code=500,
            code="CRAWLER_DEBUG_LOG_READ_FAILED",
            message="无法读取该抓取任务的调试日志。",
        ) from exc
    return Response(
        content=content,
        media_type="application/jsonl; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{debug_file.name}"',
        },
    )


@router.get("/dashboard/overview", response_model=DashboardOverviewRead)
async def read_agent_dashboard_overview(
    identity_id: int = Query(..., ge=1),
    llm_profile_id: int | None = Query(default=None, ge=1),
    university: str | None = Query(default=None),
    school: str | None = Query(default=None),
    email_university: str | None = Query(default=None),
    email_school: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> DashboardOverviewRead:
    try:
        return await build_dashboard_overview(
            session,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            university=university,
            school=school,
            email_university=email_university,
            email_school=email_school,
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=422,
            code="INVALID_DASHBOARD_FILTER",
            message=str(exc),
        ) from exc


@router.get("/usage/records", response_model=TokenUsageRecordListRead)
async def list_agent_token_usage_records(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=100),
    feature_type: TokenUsageFeatureFilter = Query(default="all"),
    model_name: str | None = Query(default=None),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    fields: str | None = Query(default=None, max_length=4_000),
    session: AsyncSession = Depends(get_async_session),
) -> TokenUsageRecordListRead | Response:
    try:
        response = await list_token_usage_records(
            session,
            page=page,
            page_size=page_size,
            feature_type=feature_type,
            model_name=model_name,
            start_at=start_at,
            end_at=end_at,
        )
        return _project_agent_collection_response(response, fields)
    except ValueError as exc:
        raise AgentApiError(
            status_code=422,
            code="INVALID_TOKEN_USAGE_FILTER",
            message=str(exc),
        ) from exc


@router.get("/usage/chart", response_model=TokenUsageChartRead)
async def read_agent_token_usage_chart(
    preset: TokenUsageChartPreset = Query(default="last_24_hours"),
    feature_type: TokenUsageFeatureFilter = Query(default="all"),
    model_name: str | None = Query(default=None),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> TokenUsageChartRead:
    try:
        return await build_token_usage_chart(
            session,
            preset=preset,
            feature_type=feature_type,
            model_name=model_name,
            start_at=start_at,
            end_at=end_at,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=422,
            code="INVALID_TOKEN_USAGE_FILTER",
            message=str(exc),
        ) from exc


@router.get("/usage/visualization", response_model=TokenUsageVisualizationRead)
async def read_agent_token_usage_visualization(
    preset: TokenUsageChartPreset = Query(default="last_24_hours"),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> TokenUsageVisualizationRead:
    try:
        return await build_token_usage_visualization(
            session,
            preset=preset,
            start_at=start_at,
            end_at=end_at,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=422,
            code="INVALID_TOKEN_USAGE_FILTER",
            message=str(exc),
        ) from exc


def _serialize_agent_operation_log(log: OperationLog) -> OperationLogRead:
    metadata = sanitize_diagnostic_metadata(log.event_metadata)
    return OperationLogRead(
        id=log.id,
        request_id=log.request_id,
        category=log.category,
        event_name=log.event_name,
        level=log.level,
        message=sanitize_diagnostic_text(log.message) or None,
        entity_type=log.entity_type,
        entity_id=log.entity_id,
        metadata=metadata if isinstance(metadata, dict | list) else None,
        created_at=log.created_at,
    )


def _read_agent_startup_logs() -> list[DiagnosticFileRead]:
    log_dir = get_settings().data_dir / "logs"
    diagnostic_files: list[DiagnosticFileRead] = []
    for log_name in ("startup.log", "backend-errors.log"):
        log_path = log_dir / log_name
        if not log_path.is_file():
            continue
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        diagnostic_files.append(
            DiagnosticFileRead(
                name=log_path.name,
                relative_path=f"logs/{log_path.name}",
                content=sanitize_diagnostic_text(content),
            ),
        )
    return diagnostic_files
