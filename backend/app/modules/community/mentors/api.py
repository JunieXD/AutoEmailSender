from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.query_chunks import chunked_values
from app.models import Professor
from .schemas import (
    CommunityCatalogRead,
    CommunityImportPayload,
    CommunityImportResultRead,
    CommunityPreviewPayload,
    CommunityRecordSelectionPayload,
    CommunityRecordsRead,
    CommunitySharePackagePayload,
    MAX_COMMUNITY_SHARE_PROFESSORS,
)
from .service import (
    CommunityDataError,
    CommunityMentorDataService,
    build_community_comparisons,
    build_community_records_response,
    build_community_share_package,
    import_community_records,
    sync_community_link_lifecycle,
)


router = APIRouter(prefix="/api/community-mentors", tags=["community-mentors"])


def get_community_mentor_data_service() -> CommunityMentorDataService:
    return CommunityMentorDataService()


@router.get("/catalog", response_model=CommunityCatalogRead)
async def get_community_catalog(
    refresh: bool = Query(default=False),
    session: AsyncSession = Depends(get_async_session),
    service: CommunityMentorDataService = Depends(get_community_mentor_data_service),
) -> CommunityCatalogRead:
    try:
        bundle = await service.get_catalog(force_refresh=refresh)
        lifecycle_warnings = await sync_community_link_lifecycle(session, bundle)
        await session.commit()
    except CommunityDataError as exc:
        await session.rollback()
        raise _community_http_error(exc) from exc
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


@router.post("/records", response_model=CommunityRecordsRead)
async def list_community_records(
    payload: CommunityRecordSelectionPayload,
    session: AsyncSession = Depends(get_async_session),
    service: CommunityMentorDataService = Depends(get_community_mentor_data_service),
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
        raise _community_http_error(exc) from exc
    return build_community_records_response(
        bundle=record_bundle,
        comparisons=comparisons,
        lifecycle_warnings=lifecycle_warnings,
    )


@router.post("/preview", response_model=CommunityRecordsRead)
async def preview_community_import(
    payload: CommunityPreviewPayload,
    session: AsyncSession = Depends(get_async_session),
    service: CommunityMentorDataService = Depends(get_community_mentor_data_service),
) -> CommunityRecordsRead:
    try:
        record_bundle = await service.load_records(
            dataset_version=payload.dataset_version,
            unit_paths=payload.unit_paths,
        )
        records_by_id = {record.id: record for record in record_bundle.records}
        missing_ids = [record_id for record_id in payload.record_ids if record_id not in records_by_id]
        if missing_ids:
            raise CommunityDataError(
                f"所选导师不属于当前学院数据：{', '.join(missing_ids[:3])}",
                code="COMMUNITY_DATA_SELECTION_INVALID",
            )
        selected_records = [records_by_id[record_id] for record_id in payload.record_ids]
        lifecycle_warnings = await sync_community_link_lifecycle(
            session,
            record_bundle.catalog_bundle,
        )
        comparisons = await build_community_comparisons(session, selected_records)
        await session.commit()
    except CommunityDataError as exc:
        await session.rollback()
        raise _community_http_error(exc) from exc
    return build_community_records_response(
        bundle=record_bundle,
        comparisons=comparisons,
        lifecycle_warnings=lifecycle_warnings,
    )


@router.post("/import", response_model=CommunityImportResultRead)
async def import_from_community(
    payload: CommunityImportPayload,
    session: AsyncSession = Depends(get_async_session),
    service: CommunityMentorDataService = Depends(get_community_mentor_data_service),
) -> CommunityImportResultRead:
    try:
        record_bundle = await service.load_records(
            dataset_version=payload.dataset_version,
            unit_paths=payload.unit_paths,
        )
        records_by_id = {record.id: record for record in record_bundle.records}
        missing_ids = [
            item.community_record_id
            for item in payload.items
            if item.community_record_id not in records_by_id
        ]
        if missing_ids:
            raise CommunityDataError(
                f"所选导师不属于当前学院数据：{', '.join(missing_ids[:3])}",
                code="COMMUNITY_DATA_SELECTION_INVALID",
            )
        selected_records = [
            records_by_id[item.community_record_id]
            for item in payload.items
        ]
        await sync_community_link_lifecycle(session, record_bundle.catalog_bundle)
        comparisons = await build_community_comparisons(session, selected_records)
        summary = await import_community_records(
            session,
            dataset_version=payload.dataset_version,
            comparisons=comparisons,
            items=payload.items,
        )
        await session.commit()
    except CommunityDataError as exc:
        await session.rollback()
        raise _community_http_error(exc) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="导入与现有导师邮箱或社区关联冲突，请刷新预览后处理",
        ) from exc

    return CommunityImportResultRead(
        inserted_count=summary.inserted_count,
        updated_count=summary.updated_count,
        linked_count=summary.linked_count,
        skipped_count=summary.skipped_count,
        message=(
            f"社区导入完成：新增 {summary.inserted_count} 条，"
            f"更新 {summary.updated_count} 条，建立关联 {summary.linked_count} 条。"
        ),
        professors=list(summary.professors),
    )


@router.get("/share-package")
async def export_community_share_package(
    professor_ids: str = Query(min_length=1, max_length=4_000),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    try:
        ids = _parse_professor_ids(professor_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _build_community_share_package_response(ids, session)


@router.post("/share-package")
async def export_community_share_package_from_selection(
    payload: CommunitySharePackagePayload,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    return await _build_community_share_package_response(payload.professor_ids, session)


async def _build_community_share_package_response(
    ids: list[int],
    session: AsyncSession,
) -> Response:
    professors: list[Professor] = []
    for professor_id_chunk in chunked_values(ids):
        professors.extend(
            await session.scalars(
                select(Professor).where(Professor.id.in_(professor_id_chunk)),
            ),
        )
    professors_by_id = {professor.id: professor for professor in professors}
    missing_ids = [professor_id for professor_id in ids if professor_id not in professors_by_id]
    if missing_ids:
        raise HTTPException(status_code=404, detail=f"未找到导师：{missing_ids[0]}")
    try:
        content = build_community_share_package(
            [professors_by_id[professor_id] for professor_id in ids],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="community-share.xlsx"',
        },
    )


def _parse_professor_ids(value: str) -> list[int]:
    parts = [part.strip() for part in value.split(",")]
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError("导师 ID 列表无效")
    ids = [int(part) for part in parts]
    if any(professor_id <= 0 for professor_id in ids):
        raise ValueError("导师 ID 必须为正整数")
    if len(ids) > MAX_COMMUNITY_SHARE_PROFESSORS:
        raise ValueError(
            f"一次最多导出 {MAX_COMMUNITY_SHARE_PROFESSORS} 位导师",
        )
    if len(ids) != len(set(ids)):
        raise ValueError("导师 ID 不能重复")
    return ids


def _community_http_error(exc: CommunityDataError) -> HTTPException:
    if exc.code in {
        "COMMUNITY_DATA_VERSION_CHANGED",
        "COMMUNITY_DATA_REQUIRES_NEWER_APP",
        "COMMUNITY_DATA_IDENTITY_CONFLICT",
        "COMMUNITY_DATA_LIFECYCLE_BLOCKED",
        "COMMUNITY_DATA_PREVIEW_STALE",
    }:
        status_code = 409
    elif exc.code in {
        "COMMUNITY_DATA_SELECTION_INVALID",
        "COMMUNITY_DATA_PATH_INVALID",
        "COMMUNITY_DATA_CONFIG_INVALID",
        "COMMUNITY_DATA_FIELD_CHOICE_INVALID",
        "COMMUNITY_DATA_TOO_LARGE",
    }:
        status_code = 400
    elif exc.code == "COMMUNITY_DATA_UNAVAILABLE":
        status_code = 503
    else:
        status_code = 502
    return HTTPException(status_code=status_code, detail=str(exc))
