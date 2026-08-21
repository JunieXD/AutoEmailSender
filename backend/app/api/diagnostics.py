from __future__ import annotations

from datetime import datetime
from typing import Annotated

from app.core.time import utc_now

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_async_session
from app.models import OperationLog
from app.schemas.diagnostics import (
    DiagnosticFileRead,
    OperationLogExportResponse,
    OperationLogListResponse,
    OperationLogRead,
)
from app.modules.crawler.public import crawler_debug_file_path


router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

OperationLogLimit = Annotated[int, Query(ge=1, le=500)]
OperationLogOffset = Annotated[int, Query(ge=0)]


@router.get("/operation-logs", response_model=OperationLogListResponse)
async def list_operation_logs(
    limit: OperationLogLimit = 100,
    offset: OperationLogOffset = 0,
    level: str | None = None,
    category: str | None = None,
    event_name: str | None = None,
    request_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    session: AsyncSession = Depends(get_async_session),
) -> OperationLogListResponse:
    filters = _build_operation_log_filters(
        level=level,
        category=category,
        event_name=event_name,
        request_id=request_id,
        entity_type=entity_type,
        entity_id=entity_id,
        start_at=start_at,
        end_at=end_at,
    )
    total = await _count_operation_logs(session, filters)
    logs = list(
        (
            await session.execute(
                select(OperationLog)
                .where(*filters)
                .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
                .limit(limit)
                .offset(offset),
            )
        ).scalars(),
    )
    return OperationLogListResponse(
        items=[_to_operation_log_read(log) for log in logs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/export", response_model=OperationLogExportResponse)
async def export_operation_logs(
    level: str | None = None,
    category: str | None = None,
    event_name: str | None = None,
    request_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    session: AsyncSession = Depends(get_async_session),
) -> OperationLogExportResponse:
    filter_values = {
        "level": level,
        "category": category,
        "event_name": event_name,
        "request_id": request_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "start_at": start_at.isoformat() if start_at else None,
        "end_at": end_at.isoformat() if end_at else None,
    }
    filters = _build_operation_log_filters(
        level=level,
        category=category,
        event_name=event_name,
        request_id=request_id,
        entity_type=entity_type,
        entity_id=entity_id,
        start_at=start_at,
        end_at=end_at,
    )
    total = await _count_operation_logs(session, filters)
    logs = list(
        (
            await session.execute(
                select(OperationLog)
                .where(*filters)
                .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
                .limit(500),
            )
        ).scalars(),
    )
    return OperationLogExportResponse(
        exported_at=utc_now(),
        total=total,
        items=[_to_operation_log_read(log) for log in logs],
        filters=filter_values,
        startup_logs=read_startup_logs(),
    )


@router.get("/crawler-debug/{job_id}/export", response_class=FileResponse)
async def export_crawler_debug_jsonl(job_id: int) -> FileResponse:
    debug_file = crawler_debug_file_path(job_id)
    if not debug_file.is_file():
        raise HTTPException(status_code=404, detail="未找到该抓取任务的调试日志")
    return FileResponse(
        debug_file,
        media_type="application/jsonl; charset=utf-8",
        filename=debug_file.name,
    )


def _build_operation_log_filters(
    *,
    level: str | None,
    category: str | None,
    event_name: str | None,
    request_id: str | None,
    entity_type: str | None,
    entity_id: str | None,
    start_at: datetime | None,
    end_at: datetime | None,
) -> list[object]:
    filters: list[object] = []
    if level is not None:
        filters.append(OperationLog.level == level)
    if category is not None:
        filters.append(OperationLog.category == category)
    if event_name is not None:
        filters.append(OperationLog.event_name == event_name)
    if request_id is not None:
        filters.append(OperationLog.request_id == request_id)
    if entity_type is not None:
        filters.append(OperationLog.entity_type == entity_type)
    if entity_id is not None:
        filters.append(OperationLog.entity_id == entity_id)
    if start_at is not None:
        filters.append(OperationLog.created_at >= start_at)
    if end_at is not None:
        filters.append(OperationLog.created_at < end_at)
    return filters


def read_startup_logs() -> list[DiagnosticFileRead]:
    log_dir = get_settings().data_dir / "logs"
    diagnostic_files: list[DiagnosticFileRead] = []
    for log_name in ["startup.log", "backend-errors.log"]:
        log_path = log_dir / log_name
        if not log_path.is_file():
            continue
        diagnostic_files.append(
            DiagnosticFileRead(
                name=log_path.name,
                relative_path=f"logs/{log_path.name}",
                content=log_path.read_text(encoding="utf-8", errors="replace"),
            ),
        )
    return diagnostic_files


async def _count_operation_logs(session: AsyncSession, filters: list[object]) -> int:
    statement: Select[tuple[int]] = (
        select(func.count()).select_from(OperationLog).where(*filters)
    )
    return int(await session.scalar(statement) or 0)


def _to_operation_log_read(log: OperationLog) -> OperationLogRead:
    return OperationLogRead(
        id=log.id,
        request_id=log.request_id,
        category=log.category,
        event_name=log.event_name,
        level=log.level,
        message=log.message,
        entity_type=log.entity_type,
        entity_id=log.entity_id,
        metadata=log.event_metadata,
        created_at=log.created_at,
    )
