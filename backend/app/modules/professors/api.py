from __future__ import annotations

from collections import defaultdict

from app.core.time import utc_now

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, selectinload

from app.core.database import get_async_session
from app.core.agent_revisions import revision_for
from app.core.query_chunks import chunked_values, unique_positive_ids
from app.models import (
    EmailTask,
    Professor,
    ProfessorTag,
    ProfessorTagLink,
)
from app.modules.campaigns.public import email_task_is_not_user_removed_expression
from .schemas import (
    ProfessorActionResult,
    ProfessorBulkArchivePayload,
    ProfessorBulkTagsPayload,
    ProfessorBulkTagsResult,
    ProfessorDashboardItemRead,
    ProfessorDashboardPageRead,
    ProfessorDashboardPageRequest,
    ProfessorFetchByIdsPayload,
    ProfessorFetchByIdsRead,
    ProfessorImportFileResult,
    ProfessorImportResult,
    ProfessorIdSelectionRead,
    ProfessorManagementItemRead,
    ProfessorManagementPageRead,
    ProfessorManagementPageRequest,
    ProfessorNoteUpdatePayload,
    ProfessorNoteUpdateRead,
    ProfessorRead,
    ProfessorTagPayload,
    ProfessorTagRead,
    ProfessorTagUpdatePayload,
    ProfessorTagUsageProfessorRead,
    ProfessorTagUsageRead,
    ProfessorUpsertPayload,
)
from .query import (
    list_dashboard_professor_ids,
    list_dashboard_professor_page,
    list_management_professor_ids,
    list_management_professor_page,
)
from app.services.contact_status import build_contact_status_by_professor
from app.modules.identities.public import resolve_identity_communication_scope
from app.services.match_results import (
    ResolvedMatchResults,
    load_resolved_match_results,
    match_result_is_stale,
)
from app.services.operation_logs import record_operation_log
from app.services.professor_schedule import load_active_scheduled_professor_ids
from .management import (
    build_professor_export,
    build_professor_template,
    is_valid_professor_email,
    parse_professor_import_file,
)
from .mutations import (
    ProfessorMutationError,
    archive_professor_record,
    bulk_archive_professor_records,
    bulk_update_professor_tags_record,
    create_professor_record,
    create_professor_tag_record,
    delete_professor_tag_record,
    lock_professor_tag_for_delete,
    import_professor_records,
    restore_professor_record,
    set_professor_tags_record,
    update_professor_record,
)
from .samples import SAMPLE_PROFESSORS


router = APIRouter(prefix="/api/professors", tags=["professors"])


@router.get("", response_model=list[ProfessorDashboardItemRead])
async def list_professors(
    identity_id: int | None = None,
    llm_profile_id: int | None = None,
    ids: str | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> list[ProfessorDashboardItemRead]:
    requested_professor_ids = (
        unique_positive_ids(int(item) for item in ids.split(",") if item.strip())
        if ids
        else []
    )
    if ids and not requested_professor_ids:
        return []
    if requested_professor_ids:
        professors = await _load_active_professors_by_ids(
            session, requested_professor_ids
        )
    else:
        statement = (
            select(Professor)
            .options(selectinload(Professor.tags))
            .where(Professor.archived_at.is_(None))
            .order_by(Professor.created_at.desc(), Professor.id.asc())
        )
        professors = list((await session.execute(statement)).scalars())
    if not professors:
        return []

    return await _build_dashboard_professor_items(
        session,
        identity_id=identity_id,
        professors=professors,
    )


@router.post("/fetch-by-ids", response_model=ProfessorFetchByIdsRead)
async def fetch_professors_by_ids(
    payload: ProfessorFetchByIdsPayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorFetchByIdsRead:
    # POST body variant of the ``ids`` query on GET ``/api/professors``: the
    # create-task flow can select thousands of professors, which overflows
    # practical URL length limits as a comma-joined query string.
    requested_professor_ids = unique_positive_ids(payload.ids)
    if not requested_professor_ids:
        return ProfessorFetchByIdsRead(
            items=[],
            total_count=0,
            page=1,
            page_size=payload.page_size or 0,
            total_pages=1,
        )
    professors = await _load_active_professors_by_ids(session, requested_professor_ids)
    total_count = len(professors)
    if payload.page_size is None:
        page_professors = professors
        page = 1
        page_size = total_count
        total_pages = 1
    else:
        page_size = payload.page_size
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        page = min(payload.page, total_pages)
        page_professors = professors[(page - 1) * page_size : page * page_size]
    items = await _build_dashboard_professor_items(
        session,
        identity_id=payload.identity_id,
        professors=page_professors,
    )
    return ProfessorFetchByIdsRead(
        items=items,
        total_count=total_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


async def _load_active_professors_by_ids(
    session: AsyncSession,
    requested_professor_ids: list[int],
) -> list[Professor]:
    statement = (
        select(Professor)
        .options(selectinload(Professor.tags))
        .where(Professor.archived_at.is_(None))
        .order_by(Professor.created_at.desc(), Professor.id.asc())
    )
    professors: list[Professor] = []
    for professor_id_chunk in chunked_values(requested_professor_ids):
        professors.extend(
            (
                await session.scalars(
                    statement.where(Professor.id.in_(professor_id_chunk)),
                )
            ).unique(),
        )
    professors.sort(key=lambda item: (-item.created_at.timestamp(), item.id))
    return professors


async def _build_dashboard_professor_items(
    session: AsyncSession,
    *,
    identity_id: int | None,
    professors: list[Professor],
) -> list[ProfessorDashboardItemRead]:

    professor_ids = [professor.id for professor in professors]
    tasks_by_professor: dict[int, list[EmailTask]] = defaultdict(list)
    contact_status_by_professor = {}
    active_scheduled_professor_ids: set[int] = set()
    resolved_matches: ResolvedMatchResults | None = None

    if identity_id is not None:
        try:
            communication_scope = await resolve_identity_communication_scope(
                session,
                active_identity_id=identity_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        for professor_id_chunk in chunked_values(professor_ids):
            task_result = await session.execute(
                select(EmailTask)
                .options(
                    load_only(
                        EmailTask.professor_id,
                        EmailTask.status,
                        EmailTask.match_score,
                        EmailTask.created_at,
                        EmailTask.sent_at,
                        EmailTask.is_replied,
                        EmailTask.updated_at,
                    ),
                )
                .where(
                    EmailTask.identity_id == identity_id,
                    EmailTask.professor_id.in_(professor_id_chunk),
                    EmailTask.batch_send_canceled_at.is_(None),
                    email_task_is_not_user_removed_expression(),
                )
                .order_by(
                    EmailTask.professor_id.asc(),
                    EmailTask.created_at.desc(),
                    EmailTask.id.desc(),
                ),
            )
            for task in task_result.scalars():
                tasks_by_professor[task.professor_id].append(task)
        contact_status_by_professor = await build_contact_status_by_professor(
            session,
            identity_id=identity_id,
            professor_ids=professor_ids,
            tasks_by_professor=tasks_by_professor,
            communication_identity_ids=communication_scope.identity_ids,
        )
        active_scheduled_professor_ids = await load_active_scheduled_professor_ids(
            session,
            identity_id=identity_id,
            professor_ids=professor_ids,
        )
        resolved_matches = await load_resolved_match_results(
            session,
            active_identity_id=identity_id,
            professor_ids=professor_ids,
        )

    items: list[ProfessorDashboardItemRead] = []
    for professor in professors:
        contact_status = contact_status_by_professor.get(professor.id)
        match_result = (
            resolved_matches.get(professor.id) if resolved_matches is not None else None
        )
        match_scope = resolved_matches.scope if resolved_matches is not None else None
        items.append(
            ProfessorDashboardItemRead(
                id=professor.id,
                name=professor.name,
                email=professor.email,
                title=professor.title,
                university=professor.university,
                school=professor.school,
                department=professor.department,
                research_direction=professor.research_direction,
                recent_papers=professor.recent_papers or [],
                match_score=match_result.match_score if match_result else None,
                match_source_identity_id=(
                    match_scope.source_identity_id if match_scope is not None else None
                ),
                match_source_identity_name=(
                    match_scope.source_identity.profile_name
                    if match_scope is not None
                    else None
                ),
                match_is_shared=(
                    match_scope.uses_group_match_source
                    if match_scope is not None
                    else False
                ),
                match_is_stale=(
                    match_result_is_stale(match_result, match_scope.source_identity)
                    if match_scope is not None
                    else False
                ),
                match_analyzed_at=(
                    match_result.analyzed_at if match_result is not None else None
                ),
                sent_count=contact_status.sent_count if contact_status else 0,
                status=contact_status.status if contact_status else "not_contacted",
                has_active_schedule=professor.id in active_scheduled_professor_ids,
                last_sent_at=contact_status.last_sent_at if contact_status else None,
                last_replied_at=contact_status.last_replied_at
                if contact_status
                else None,
                updated_at=professor.updated_at,
                personal_note=professor.personal_note,
                tags=_serialize_professor_tags(professor),
            )
        )
    return items


@router.post("/search/dashboard", response_model=ProfessorDashboardPageRead)
async def search_dashboard_professors(
    payload: ProfessorDashboardPageRequest,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorDashboardPageRead:
    try:
        return await list_dashboard_professor_page(session, payload)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "身份" in detail and "未找到" in detail else 422
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/search/management", response_model=ProfessorManagementPageRead)
async def search_management_professors(
    payload: ProfessorManagementPageRequest,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorManagementPageRead:
    try:
        return await list_management_professor_page(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/search/dashboard/ids", response_model=ProfessorIdSelectionRead)
async def search_dashboard_professor_ids(
    payload: ProfessorDashboardPageRequest,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorIdSelectionRead:
    try:
        return await list_dashboard_professor_ids(session, payload)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "身份" in detail and "未找到" in detail else 422
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/search/management/ids", response_model=ProfessorIdSelectionRead)
async def search_management_professor_ids(
    payload: ProfessorManagementPageRequest,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorIdSelectionRead:
    try:
        return await list_management_professor_ids(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/template")
async def download_professor_template(
    format: str = Query(default="xlsx"),
) -> Response:
    try:
        content, media_type, filename = build_professor_template(format)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/export")
async def export_professors(
    format: str = Query(default="xlsx"),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    professors = list(
        (
            await session.execute(
                select(Professor)
                .where(Professor.archived_at.is_(None))
                .order_by(Professor.updated_at.desc(), Professor.created_at.desc()),
            )
        ).scalars(),
    )
    try:
        content, media_type, filename = build_professor_export(professors, format)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.post("/import-file", response_model=ProfessorImportFileResult)
async def import_professors_from_file(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorImportFileResult:
    if not file.filename:
        raise HTTPException(status_code=400, detail="请选择要导入的文件")

    try:
        parsed = parse_professor_import_file(file.filename, await file.read())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    result = await import_professor_records(
        session,
        parsed,
        filename=file.filename,
        event_name="professor.import_file",
        actor="desktop_ui",
    )
    await session.commit()

    return ProfessorImportFileResult(
        inserted_count=result.inserted_count,
        updated_count=result.updated_count,
        failed_count=result.failed_count,
        message=(
            f"导入完成：新增 {result.inserted_count} 条，更新 {result.updated_count} 条，"
            f"创建标签 {result.created_tag_count} 个，失败 {result.failed_count} 条。"
        ),
    )


@router.get("/tags", response_model=list[ProfessorTagRead])
async def list_professor_tags(
    session: AsyncSession = Depends(get_async_session),
) -> list[ProfessorTagRead]:
    tags = list(
        (
            await session.execute(
                select(ProfessorTag).order_by(ProfessorTag.id.asc()),
            )
        ).scalars(),
    )
    return [_serialize_tag(tag) for tag in tags]


@router.get("/tags/{tag_id}/usage", response_model=ProfessorTagUsageRead)
async def get_professor_tag_usage(
    tag_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorTagUsageRead:
    tag = await session.get(ProfessorTag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="未找到标签")

    professors = list(
        (
            await session.execute(
                select(Professor)
                .join(
                    ProfessorTagLink,
                    ProfessorTagLink.professor_id == Professor.id,
                )
                .where(ProfessorTagLink.tag_id == tag_id)
                .order_by(Professor.name.asc(), Professor.id.asc()),
            )
        ).scalars(),
    )
    result = ProfessorTagUsageRead(
        tag=_serialize_tag(tag),
        professors=[
            ProfessorTagUsageProfessorRead(
                id=professor.id,
                name=professor.name,
                email=professor.email,
                university=professor.university,
                school=professor.school,
            )
            for professor in professors
        ],
        revision="",
    )
    return result.model_copy(
        update={
            "revision": revision_for(
                {
                    "tag": result.tag.model_dump(mode="json"),
                    "professor_ids": [professor.id for professor in result.professors],
                }
            )
        }
    )


@router.post(
    "/tags",
    response_model=ProfessorTagRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_professor_tag(
    payload: ProfessorTagPayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorTagRead:
    try:
        tag = await create_professor_tag_record(
            session,
            payload,
            event_name="professor.tag_created",
            actor="desktop_ui",
        )
        await session.commit()
    except ProfessorMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="标签已存在") from exc
    await session.refresh(tag)
    return _serialize_tag(tag)


@router.delete("/tags/{tag_id}", response_model=ProfessorActionResult)
async def delete_professor_tag(
    tag_id: int,
    impact_revision: str = Query(..., min_length=20, max_length=64),
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorActionResult:
    try:
        await lock_professor_tag_for_delete(session, tag_id)
    except ProfessorMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    usage = await get_professor_tag_usage(tag_id, session)
    if usage.revision != impact_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PROFESSOR_TAG_DELETE_PLAN_STALE",
                "message": "标签关联的导师已发生变化，请重新确认删除影响。",
                "usage": usage.model_dump(mode="json"),
            },
        )
    try:
        result = await delete_professor_tag_record(
            session,
            tag_id,
            event_name="professor.tag_deleted",
            actor="desktop_ui",
        )
    except ProfessorMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()
    return ProfessorActionResult(
        ok=True,
        affected_count=1,
        message=(
            f"标签已删除，并从 {result['affected_professor_count']} 位导师中移除"
            if result["affected_professor_count"]
            else "标签已删除"
        ),
    )


@router.patch("/{professor_id}/tags", response_model=ProfessorManagementItemRead)
async def update_professor_tags(
    professor_id: int,
    payload: ProfessorTagUpdatePayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorManagementItemRead:
    try:
        professor = await set_professor_tags_record(
            session,
            professor_id,
            payload,
            event_name="professor.tags_updated",
            actor="desktop_ui",
        )
    except ProfessorMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()
    return _serialize_management_professor(professor)


@router.post(
    "", response_model=ProfessorManagementItemRead, status_code=status.HTTP_201_CREATED
)
async def create_professor(
    payload: ProfessorUpsertPayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorManagementItemRead:
    try:
        professor = await create_professor_record(
            session,
            payload,
            event_name="professor.created",
            actor="desktop_ui",
        )
    except ProfessorMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()
    return _serialize_management_professor(professor)


@router.get("/{professor_id}", response_model=ProfessorRead)
async def get_professor(
    professor_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorRead:
    professor = await session.scalar(
        select(Professor)
        .options(selectinload(Professor.tags))
        .where(Professor.id == professor_id)
        .execution_options(populate_existing=True),
    )
    if not professor:
        raise HTTPException(status_code=404, detail="未找到导师")
    return ProfessorRead(
        id=professor.id,
        name=professor.name,
        email=professor.email,
        title=professor.title,
        university=professor.university,
        school=professor.school,
        department=professor.department,
        research_direction=professor.research_direction,
        recent_papers=professor.recent_papers,
        profile_url=professor.profile_url,
        source_url=professor.source_url,
        crawl_status=professor.crawl_status,
        skip_reason=professor.skip_reason,
        personal_note=professor.personal_note,
        archived_at=professor.archived_at,
        created_at=professor.created_at,
        updated_at=professor.updated_at,
        tags=_serialize_professor_tags(professor),
    )


@router.patch("/{professor_id}", response_model=ProfessorManagementItemRead)
async def update_professor(
    professor_id: int,
    payload: ProfessorUpsertPayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorManagementItemRead:
    try:
        professor = await update_professor_record(
            session,
            professor_id,
            payload,
            event_name="professor.updated",
            actor="desktop_ui",
        )
    except ProfessorMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()
    return _serialize_management_professor(professor)


@router.patch("/{professor_id}/note", response_model=ProfessorNoteUpdateRead)
async def update_professor_personal_note(
    professor_id: int,
    payload: ProfessorNoteUpdatePayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorNoteUpdateRead:
    professor = await session.get(Professor, professor_id)
    if not professor:
        raise HTTPException(status_code=404, detail="未找到导师")

    professor.personal_note = payload.personal_note
    professor.updated_at = utc_now()
    await _record_professor_log(
        session,
        professor,
        "professor.personal_note_updated",
        metadata=_build_personal_note_log_metadata(professor.personal_note),
    )
    await session.commit()
    await session.refresh(professor)
    return ProfessorNoteUpdateRead(
        id=professor.id,
        personal_note=professor.personal_note,
        updated_at=professor.updated_at,
    )


@router.post("/{professor_id}/archive", response_model=ProfessorActionResult)
async def archive_professor(
    professor_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorActionResult:
    try:
        (
            _,
            affected_count,
            canceled_email_task_ids,
            canceled_match_analysis_item_ids,
            canceled_information_enrichment_task_ids,
        ) = await archive_professor_record(
            session,
            professor_id,
            event_name="professor.archived",
            actor="desktop_ui",
        )
    except ProfessorMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()

    return ProfessorActionResult(
        ok=True,
        affected_count=affected_count,
        message="导师已移入回收站",
        canceled_email_task_ids=canceled_email_task_ids,
        canceled_match_analysis_item_ids=canceled_match_analysis_item_ids,
        canceled_information_enrichment_task_ids=(
            canceled_information_enrichment_task_ids
        ),
    )


@router.post("/bulk-archive", response_model=ProfessorActionResult)
async def bulk_archive_professors(
    payload: ProfessorBulkArchivePayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorActionResult:
    try:
        result = await bulk_archive_professor_records(
            session,
            payload.ids,
            event_name="professor.bulk_archived",
            actor="desktop_ui",
        )
    except ProfessorMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()
    affected_count = int(result["affected_count"])
    return ProfessorActionResult(
        ok=True,
        affected_count=affected_count,
        message=f"已将 {affected_count} 位导师移入回收站",
        canceled_email_task_ids=list(result["canceled_email_task_ids"]),
        canceled_match_analysis_item_ids=list(
            result["canceled_match_analysis_item_ids"]
        ),
        canceled_information_enrichment_task_ids=list(
            result["canceled_information_enrichment_task_ids"]
        ),
    )


@router.post("/bulk-tags", response_model=ProfessorBulkTagsResult)
async def bulk_update_professor_tags(
    payload: ProfessorBulkTagsPayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorBulkTagsResult:
    try:
        result = await bulk_update_professor_tags_record(
            session,
            payload,
            event_name="professor.bulk_tags_updated",
            actor="desktop_ui",
        )
    except ProfessorMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()
    return ProfessorBulkTagsResult(
        ok=True,
        affected_count=result.affected_count,
        message=f"已更新 {result.affected_count} 位导师的标签",
    )


@router.post("/{professor_id}/restore", response_model=ProfessorActionResult)
async def restore_professor(
    professor_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorActionResult:
    try:
        _, affected_count = await restore_professor_record(
            session,
            professor_id,
            event_name="professor.restored",
            actor="desktop_ui",
        )
    except ProfessorMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()

    return ProfessorActionResult(
        ok=True,
        affected_count=affected_count,
        message="导师已恢复到正常列表",
    )


@router.post("/import-sample", response_model=ProfessorImportResult)
async def import_sample_professors(
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorImportResult:
    existing_emails = {
        email
        for email in (
            await session.execute(
                select(Professor.email).where(Professor.email.is_not(None)),
            )
        ).scalars()
    }

    inserted_count = 0
    for item in SAMPLE_PROFESSORS:
        email = item["email"]
        if isinstance(email, str) and email in existing_emails:
            continue
        professor = Professor(**item)
        session.add(professor)
        if isinstance(email, str):
            existing_emails.add(email)
        inserted_count += 1

    await record_operation_log(
        session,
        category="user_action",
        event_name="professor.import_sample",
        entity_type="professor",
        metadata={
            "inserted_count": inserted_count,
            "sample_count": len(SAMPLE_PROFESSORS),
        },
    )
    await session.commit()
    total_count = await session.scalar(select(func.count(Professor.id)))
    return ProfessorImportResult(
        inserted_count=inserted_count,
        total_count=total_count or 0,
        message="样例导师数据已导入",
    )


@router.post("/trigger-crawler")
async def trigger_crawler(
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    await record_operation_log(
        session,
        category="crawler",
        event_name="crawler.trigger_requested",
        entity_type="crawler",
        metadata={"source": "professors.trigger_crawler"},
    )
    await session.commit()
    return {
        "status": "accepted",
        "message": "已接收智能抓取请求，当前版本先返回占位结果，后续可接入真实 crawler。",
    }


async def _record_professor_log(
    session: AsyncSession,
    professor: Professor,
    event_name: str,
    *,
    metadata: dict[str, object] | None = None,
) -> None:
    base_metadata: dict[str, object] = {
        "name": professor.name,
        "email": professor.email,
        "university": professor.university,
        "school": professor.school,
        "archived": professor.archived_at is not None,
    }
    if metadata:
        base_metadata.update(metadata)
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="professor",
        entity_id=str(professor.id),
        metadata=base_metadata,
    )


def _serialize_management_professor(
    professor: Professor,
) -> ProfessorManagementItemRead:
    return ProfessorManagementItemRead(
        id=professor.id,
        name=professor.name,
        email=professor.email,
        title=professor.title,
        university=professor.university,
        school=professor.school,
        department=professor.department,
        research_direction=professor.research_direction,
        recent_papers=professor.recent_papers or [],
        profile_url=professor.profile_url,
        source_url=professor.source_url,
        crawl_status=professor.crawl_status,
        skip_reason=professor.skip_reason,
        personal_note=professor.personal_note,
        archived_at=professor.archived_at,
        created_at=professor.created_at,
        updated_at=professor.updated_at,
        tags=_serialize_professor_tags(professor),
    )


def _serialize_tag(tag: ProfessorTag) -> ProfessorTagRead:
    return ProfessorTagRead(
        id=tag.id,
        name=tag.name,
        text_color=tag.text_color,
        background_color=tag.background_color,
    )


def _serialize_professor_tags(professor: Professor) -> list[ProfessorTagRead]:
    return [_serialize_tag(tag) for tag in professor.tags]


def _build_personal_note_log_metadata(personal_note: str | None) -> dict[str, object]:
    return {
        "has_personal_note": personal_note is not None,
        "personal_note_length": len(personal_note or ""),
    }
