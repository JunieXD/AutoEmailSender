from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.core.database import get_async_session, get_session_factory
from app.core.query_chunks import chunked_values
from app.models import Professor
from app.modules.community.public import (
    CommunityCatalogRead,
    CommunityDataError,
    CommunityImportPayload,
    CommunityMentorDataService,
    CommunityPreviewPayload,
    CommunityRecordSelectionPayload,
    CommunityRecordsRead,
    build_community_comparisons,
    build_community_records_response,
    build_community_share_package,
    sync_community_link_lifecycle,
)
from app.schemas.agent import AgentChangePlanRead
from app.services.agent_change_plans import create_community_mentor_import_change_plan

from .support import (
    get_agent_community_mentor_data_service,
)

router = APIRouter()


@router.get("/community-mentors/catalog", response_model=CommunityCatalogRead)
async def get_agent_community_catalog(
    refresh: bool = Query(default=False),
    session: AsyncSession = Depends(get_async_session),
    service: CommunityMentorDataService = Depends(
        get_agent_community_mentor_data_service
    ),
) -> CommunityCatalogRead:
    try:
        bundle = await service.get_catalog(force_refresh=refresh)
        lifecycle_warnings = await sync_community_link_lifecycle(session, bundle)
        await session.commit()
    except CommunityDataError as exc:
        await session.rollback()
        raise _agent_community_mentor_error(exc) from exc
    return CommunityCatalogRead(
        schema_version=bundle.catalog.schema_version,
        dataset_version=bundle.catalog.dataset_version,
        generated_at=bundle.catalog.generated_at,
        record_count=bundle.catalog.record_count,
        universities=bundle.catalog.universities,
        source=bundle.source,
        stale=bundle.stale,
        warning=bundle.warning,
        verified_at=bundle.verified_at,
        lifecycle_warnings=lifecycle_warnings,
    )


@router.post("/community-mentors/records", response_model=CommunityRecordsRead)
async def list_agent_community_records(
    payload: CommunityRecordSelectionPayload,
    session: AsyncSession = Depends(get_async_session),
    service: CommunityMentorDataService = Depends(
        get_agent_community_mentor_data_service
    ),
) -> CommunityRecordsRead:
    try:
        record_bundle = await service.load_records(
            dataset_version=payload.dataset_version,
            unit_paths=payload.unit_paths,
        )
        lifecycle_warnings = await sync_community_link_lifecycle(
            session,
            record_bundle.catalog_bundle,
        )
        comparisons = await build_community_comparisons(session, record_bundle.records)
        await session.commit()
    except CommunityDataError as exc:
        await session.rollback()
        raise _agent_community_mentor_error(exc) from exc
    return build_community_records_response(
        bundle=record_bundle,
        comparisons=comparisons,
        lifecycle_warnings=lifecycle_warnings,
    )


@router.post("/community-mentors/preview", response_model=CommunityRecordsRead)
async def preview_agent_community_import(
    payload: CommunityPreviewPayload,
    session: AsyncSession = Depends(get_async_session),
    service: CommunityMentorDataService = Depends(
        get_agent_community_mentor_data_service
    ),
) -> CommunityRecordsRead:
    try:
        record_bundle = await service.load_records(
            dataset_version=payload.dataset_version,
            unit_paths=payload.unit_paths,
        )
        records_by_id = {record.id: record for record in record_bundle.records}
        missing_ids = [
            record_id
            for record_id in payload.record_ids
            if record_id not in records_by_id
        ]
        if missing_ids:
            raise CommunityDataError(
                f"所选导师不属于当前学院数据：{', '.join(missing_ids[:3])}",
                code="COMMUNITY_DATA_SELECTION_INVALID",
            )
        selected_records = [
            records_by_id[record_id] for record_id in payload.record_ids
        ]
        lifecycle_warnings = await sync_community_link_lifecycle(
            session,
            record_bundle.catalog_bundle,
        )
        comparisons = await build_community_comparisons(session, selected_records)
        await session.commit()
    except CommunityDataError as exc:
        await session.rollback()
        raise _agent_community_mentor_error(exc) from exc
    return build_community_records_response(
        bundle=record_bundle,
        comparisons=comparisons,
        lifecycle_warnings=lifecycle_warnings,
    )


@router.post(
    "/community-mentors/prepare-import",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_community_mentor_import(
    payload: CommunityImportPayload,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    service: CommunityMentorDataService = Depends(
        get_agent_community_mentor_data_service
    ),
) -> AgentChangePlanRead:
    return await create_community_mentor_import_change_plan(
        get_session_factory(),
        payload,
        service,
        idempotency_key=idempotency_key,
    )


@router.get("/community-mentors/share-package")
async def export_agent_community_share_package(
    professor_ids: str = Query(min_length=1, max_length=4_000),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    try:
        ids = _parse_agent_professor_ids(professor_ids)
    except ValueError as exc:
        raise AgentApiError(
            status_code=400,
            code="PROFESSOR_IDS_INVALID",
            message=str(exc),
        ) from exc
    professors: list[Professor] = []
    for professor_id_chunk in chunked_values(ids):
        professors.extend(
            await session.scalars(
                select(Professor).where(Professor.id.in_(professor_id_chunk)),
            ),
        )
    professors_by_id = {professor.id: professor for professor in professors}
    missing_ids = [
        professor_id for professor_id in ids if professor_id not in professors_by_id
    ]
    if missing_ids:
        raise AgentApiError(
            status_code=404,
            code="PROFESSOR_NOT_FOUND",
            message=f"未找到导师：{missing_ids[0]}",
        )
    try:
        content = build_community_share_package(
            [professors_by_id[professor_id] for professor_id in ids],
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=400,
            code="COMMUNITY_SHARE_PACKAGE_INVALID",
            message=str(exc),
        ) from exc
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="community-share.xlsx"'},
    )


def _parse_agent_professor_ids(value: str) -> list[int]:
    parts = [part.strip() for part in value.split(",")]
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError("导师 ID 列表无效")
    ids = [int(part) for part in parts]
    if any(professor_id <= 0 for professor_id in ids):
        raise ValueError("导师 ID 必须为正整数")
    if len(ids) > 500:
        raise ValueError("一次最多导出 500 位导师")
    if len(ids) != len(set(ids)):
        raise ValueError("导师 ID 不能重复")
    return ids


def _agent_community_mentor_error(error: CommunityDataError) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=str(error),
    )
