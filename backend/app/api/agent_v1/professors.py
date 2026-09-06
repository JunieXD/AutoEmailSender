from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.agent_api_errors import AgentApiError
from app.core.agent_revisions import ensure_revision, revision_for
from app.core.database import get_async_session, get_session_factory
from app.models import Professor, ProfessorTag
from app.modules.professors.public import (
    ProfessorBulkTagsPayload,
    ProfessorMutationError,
    ProfessorTagPayload,
    ProfessorTagUpdatePayload,
    ProfessorUpsertPayload,
    archive_professor_record,
    build_professor_export,
    build_professor_template,
    create_professor_record,
    create_professor_tag_record,
    get_professor_tag_usage_snapshot,
    get_professor_with_tags_or_raise,
    professor_name_script_clause,
    restore_professor_record,
    set_professor_tags_record,
    update_professor_record,
)
from app.schemas.agent import (
    AgentChangePlanRead,
    AgentPage,
    AgentProfessorBulkArchiveRequest,
    AgentProfessorBulkTagsRequest,
    AgentProfessorPresentSelectionRequest,
    AgentProfessorRead,
    AgentProfessorTagCreateRequest,
    AgentProfessorTagRead,
    AgentProfessorTagSetRequest,
    AgentProfessorTagUsageRead,
    AgentProfessorUpdateRequest,
    AgentProfessorUpsertRequest,
    AgentUiHandoffRead,
)
from app.services.agent_change_plans import (
    create_professor_bulk_archive_change_plan,
    create_professor_bulk_tags_change_plan,
    create_professor_import_change_plan,
    create_professor_tag_delete_change_plan,
)
from app.services.agent_mutations import execute_agent_mutation
from app.services.agent_ui_handoffs import create_professor_selection_ui_handoff

from .support import (
    _project_agent_collection_response,
    _slice_page,
)

router = APIRouter()


@router.get("/professors", response_model=AgentPage[AgentProfessorRead])
async def list_agent_professors(
    q: str | None = Query(default=None),
    name_script: Literal["latin", "han", "cyrillic", "arabic", "digit"] | None = Query(
        default=None
    ),
    archived: Literal["active", "archived", "all"] = Query(default="active"),
    tag_id: int | None = Query(default=None, ge=1),
    professor_id: int | None = Query(default=None, ge=1),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentProfessorRead] | Response:
    statement = select(Professor).options(selectinload(Professor.tags))
    if professor_id is not None:
        statement = statement.where(Professor.id == professor_id)
    if name_script is not None:
        statement = statement.where(professor_name_script_clause(name_script))
    if archived == "active":
        statement = statement.where(Professor.archived_at.is_(None))
    elif archived == "archived":
        statement = statement.where(Professor.archived_at.is_not(None))
    if tag_id is not None:
        statement = statement.where(Professor.tags.any(ProfessorTag.id == tag_id))
    normalized_query = (q or "").strip()
    if normalized_query:
        search = f"%{normalized_query}%"
        statement = statement.where(
            or_(
                Professor.name.ilike(search),
                Professor.email.ilike(search),
                Professor.university.ilike(search),
                Professor.school.ilike(search),
                Professor.department.ilike(search),
                Professor.research_direction.ilike(search),
                Professor.personal_note.ilike(search),
            ),
        )
    professors = list(
        (
            await session.scalars(
                statement.order_by(Professor.id.asc()).offset(cursor).limit(limit + 1),
            )
        ).unique(),
    )
    page, next_cursor, has_more = _slice_page(professors, cursor=cursor, limit=limit)
    response = AgentPage(
        items=[_serialize_professor(professor) for professor in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return _project_agent_collection_response(response, fields)


@router.get("/professors/export")
async def export_agent_professors(
    format: Literal["xlsx", "csv"] = Query(default="xlsx"),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    professors = list(
        await session.scalars(
            select(Professor)
            .where(Professor.archived_at.is_(None))
            .order_by(Professor.updated_at.desc(), Professor.created_at.desc()),
        ),
    )
    try:
        content, media_type, filename = build_professor_export(professors, format)
    except ValueError as exc:
        raise AgentApiError(
            status_code=400,
            code="INVALID_EXPORT_FORMAT",
            message=str(exc),
        ) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/professors/import-template")
async def download_agent_professor_import_template(
    format: Literal["xlsx", "csv"] = Query(default="xlsx"),
) -> Response:
    try:
        content, media_type, filename = build_professor_template(format)
    except ValueError as exc:
        raise AgentApiError(
            status_code=400,
            code="INVALID_TEMPLATE_FORMAT",
            message=str(exc),
        ) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/professors/{professor_id}", response_model=AgentProfessorRead)
async def read_agent_professor(
    professor_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorRead:
    professor = await session.scalar(
        select(Professor)
        .options(selectinload(Professor.tags))
        .where(Professor.id == professor_id),
    )
    if professor is None:
        raise HTTPException(status_code=404, detail="未找到导师")
    return _serialize_professor(professor)


@router.post(
    "/professors",
    response_model=AgentProfessorRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_professor(
    payload: AgentProfessorUpsertRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorRead:
    request_data = payload.model_dump(mode="json")
    try:
        return await execute_agent_mutation(
            session,
            command="professors.create",
            request_data=request_data,
            idempotency_key=idempotency_key,
            response_type=AgentProfessorRead,
            mutation=lambda: _create_agent_professor(session, payload),
        )
    except ProfessorMutationError as exc:
        raise _agent_professor_error(exc) from exc


@router.put("/professors/{professor_id}", response_model=AgentProfessorRead)
async def update_agent_professor(
    professor_id: int,
    payload: AgentProfessorUpdateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorRead:
    if not payload.model_fields_set:
        raise AgentApiError(
            status_code=400,
            code="EMPTY_PROFESSOR_UPDATE",
            message="请至少提供一个需要修改的导师字段。",
        )
    request_data = {
        "professor_id": professor_id,
        "if_revision": if_revision,
        **payload.model_dump(mode="json", exclude_unset=True),
    }
    try:
        return await execute_agent_mutation(
            session,
            command="professors.update",
            request_data=request_data,
            idempotency_key=idempotency_key,
            response_type=AgentProfessorRead,
            mutation=lambda: _update_agent_professor_with_revision(
                session,
                professor_id,
                payload,
                if_revision=if_revision,
            ),
        )
    except ProfessorMutationError as exc:
        raise _agent_professor_error(exc) from exc


@router.post("/professors/{professor_id}/archive", response_model=AgentProfessorRead)
async def archive_agent_professor(
    professor_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorRead:
    try:
        return await execute_agent_mutation(
            session,
            command="professors.archive",
            request_data={"professor_id": professor_id, "if_revision": if_revision},
            idempotency_key=idempotency_key,
            response_type=AgentProfessorRead,
            mutation=lambda: _archive_agent_professor_with_revision(
                session,
                professor_id,
                if_revision=if_revision,
            ),
        )
    except ProfessorMutationError as exc:
        raise _agent_professor_error(exc) from exc


@router.post("/professors/{professor_id}/restore", response_model=AgentProfessorRead)
async def restore_agent_professor(
    professor_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorRead:
    try:
        return await execute_agent_mutation(
            session,
            command="professors.restore",
            request_data={"professor_id": professor_id, "if_revision": if_revision},
            idempotency_key=idempotency_key,
            response_type=AgentProfessorRead,
            mutation=lambda: _restore_agent_professor_with_revision(
                session,
                professor_id,
                if_revision=if_revision,
            ),
        )
    except ProfessorMutationError as exc:
        raise _agent_professor_error(exc) from exc


@router.put("/professors/{professor_id}/tags", response_model=AgentProfessorRead)
async def set_agent_professor_tags(
    professor_id: int,
    payload: AgentProfessorTagSetRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorRead:
    request_data = {
        "professor_id": professor_id,
        "if_revision": if_revision,
        **payload.model_dump(mode="json"),
    }
    try:
        return await execute_agent_mutation(
            session,
            command="professors.tags.set",
            request_data=request_data,
            idempotency_key=idempotency_key,
            response_type=AgentProfessorRead,
            mutation=lambda: _set_agent_professor_tags_with_revision(
                session,
                professor_id,
                payload,
                if_revision=if_revision,
            ),
        )
    except ProfessorMutationError as exc:
        raise _agent_professor_error(exc) from exc


@router.post(
    "/professors/prepare-bulk-tags",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_professor_bulk_tags(
    payload: AgentProfessorBulkTagsRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_professor_bulk_tags_change_plan(
        get_session_factory(),
        ProfessorBulkTagsPayload.model_validate(payload.model_dump()),
        idempotency_key=idempotency_key,
    )


@router.post(
    "/professors/prepare-bulk-archive",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_professor_bulk_archive(
    payload: AgentProfessorBulkArchiveRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_professor_bulk_archive_change_plan(
        get_session_factory(),
        payload.resolved_selection(),
        idempotency_key=idempotency_key,
    )


@router.post(
    "/professors/present-selection",
    response_model=AgentUiHandoffRead,
    status_code=status.HTTP_201_CREATED,
)
async def present_agent_professor_selection(
    payload: AgentProfessorPresentSelectionRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentUiHandoffRead:
    return await create_professor_selection_ui_handoff(
        get_session_factory(),
        payload,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/professors/prepare-import",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_professor_import(
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    if not file.filename:
        raise AgentApiError(
            status_code=400,
            code="PROFESSOR_IMPORT_FILE_REQUIRED",
            message="请选择要导入的文件。",
        )
    return await create_professor_import_change_plan(
        get_session_factory(),
        Path(file.filename).name,
        await file.read(),
        idempotency_key=idempotency_key,
    )


@router.get("/professor-tags", response_model=AgentPage[AgentProfessorTagRead])
async def list_agent_professor_tags(
    tag_id: int | None = Query(default=None, ge=1),
    name: str | None = Query(default=None, max_length=200),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentProfessorTagRead] | Response:
    statement = select(ProfessorTag)
    if tag_id is not None:
        statement = statement.where(ProfessorTag.id == tag_id)
    if name is not None:
        statement = statement.where(ProfessorTag.name == name)
    tags = list(
        await session.scalars(
            statement.order_by(ProfessorTag.id.asc()).offset(cursor).limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(tags, cursor=cursor, limit=limit)
    response = AgentPage(
        items=[_serialize_tag(tag) for tag in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return _project_agent_collection_response(response, fields)


@router.post(
    "/professor-tags",
    response_model=AgentProfessorTagRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_professor_tag(
    payload: AgentProfessorTagCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorTagRead:
    try:
        return await execute_agent_mutation(
            session,
            command="professors.tags.create",
            request_data=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            response_type=AgentProfessorTagRead,
            mutation=lambda: _create_agent_professor_tag(session, payload),
        )
    except ProfessorMutationError as exc:
        raise _agent_professor_error(exc) from exc


@router.get(
    "/professor-tags/{tag_id}/usage",
    response_model=AgentProfessorTagUsageRead,
)
async def read_agent_professor_tag_usage(
    tag_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorTagUsageRead:
    try:
        return AgentProfessorTagUsageRead.model_validate(
            await get_professor_tag_usage_snapshot(session, tag_id),
        )
    except ProfessorMutationError as exc:
        raise _agent_professor_error(exc) from exc


@router.post(
    "/professor-tags/{tag_id}/prepare-delete",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_professor_tag_delete(
    tag_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_professor_tag_delete_change_plan(
        get_session_factory(),
        tag_id,
        idempotency_key=idempotency_key,
    )


async def _create_agent_professor(
    session: AsyncSession,
    payload: AgentProfessorUpsertRequest,
) -> AgentProfessorRead:
    professor = await create_professor_record(
        session,
        ProfessorUpsertPayload.model_validate(payload.model_dump()),
        event_name="agent_cli.professor.created",
        actor="agent_cli",
    )
    return _serialize_professor(professor)


async def _update_agent_professor(
    session: AsyncSession,
    professor_id: int,
    payload: AgentProfessorUpdateRequest,
) -> AgentProfessorRead:
    existing = await get_professor_with_tags_or_raise(session, professor_id)
    merged_payload = {
        "name": existing.name,
        "email": existing.email,
        "title": existing.title,
        "university": existing.university,
        "school": existing.school,
        "department": existing.department,
        "research_direction": existing.research_direction,
        "recent_papers": existing.recent_papers or [],
        "profile_url": existing.profile_url,
        "source_url": existing.source_url,
        "personal_note": existing.personal_note,
        "tag_ids": [tag.id for tag in existing.tags],
    }
    merged_payload.update(payload.model_dump(exclude_unset=True))
    professor = await update_professor_record(
        session,
        professor_id,
        ProfessorUpsertPayload.model_validate(merged_payload),
        event_name="agent_cli.professor.updated",
        actor="agent_cli",
    )
    return _serialize_professor(professor)


async def _ensure_professor_revision(
    session: AsyncSession,
    professor_id: int,
    if_revision: str | None,
) -> None:
    if not if_revision:
        return
    current = await get_professor_with_tags_or_raise(session, professor_id)
    current_read = _serialize_professor(current)
    ensure_revision(
        if_revision,
        current_read.revision,
        resource="professors",
        resource_id=professor_id,
        latest=current_read.model_dump(mode="json"),
    )


async def _update_agent_professor_with_revision(
    session: AsyncSession,
    professor_id: int,
    payload: AgentProfessorUpdateRequest,
    *,
    if_revision: str | None,
) -> AgentProfessorRead:
    await _ensure_professor_revision(session, professor_id, if_revision)
    return await _update_agent_professor(session, professor_id, payload)


async def _archive_agent_professor(
    session: AsyncSession,
    professor_id: int,
) -> AgentProfessorRead:
    professor, _, _, _, _ = await archive_professor_record(
        session,
        professor_id,
        event_name="agent_cli.professor.archived",
        actor="agent_cli",
    )
    return _serialize_professor(professor)


async def _archive_agent_professor_with_revision(
    session: AsyncSession,
    professor_id: int,
    *,
    if_revision: str | None,
) -> AgentProfessorRead:
    await _ensure_professor_revision(session, professor_id, if_revision)
    return await _archive_agent_professor(session, professor_id)


async def _restore_agent_professor(
    session: AsyncSession,
    professor_id: int,
) -> AgentProfessorRead:
    professor, _ = await restore_professor_record(
        session,
        professor_id,
        event_name="agent_cli.professor.restored",
        actor="agent_cli",
    )
    return _serialize_professor(professor)


async def _restore_agent_professor_with_revision(
    session: AsyncSession,
    professor_id: int,
    *,
    if_revision: str | None,
) -> AgentProfessorRead:
    await _ensure_professor_revision(session, professor_id, if_revision)
    return await _restore_agent_professor(session, professor_id)


async def _set_agent_professor_tags(
    session: AsyncSession,
    professor_id: int,
    payload: AgentProfessorTagSetRequest,
) -> AgentProfessorRead:
    professor = await set_professor_tags_record(
        session,
        professor_id,
        ProfessorTagUpdatePayload.model_validate(payload.model_dump()),
        event_name="agent_cli.professor.tags_set",
        actor="agent_cli",
    )
    return _serialize_professor(professor)


async def _set_agent_professor_tags_with_revision(
    session: AsyncSession,
    professor_id: int,
    payload: AgentProfessorTagSetRequest,
    *,
    if_revision: str | None,
) -> AgentProfessorRead:
    await _ensure_professor_revision(session, professor_id, if_revision)
    return await _set_agent_professor_tags(session, professor_id, payload)


async def _create_agent_professor_tag(
    session: AsyncSession,
    payload: AgentProfessorTagCreateRequest,
) -> AgentProfessorTagRead:
    tag = await create_professor_tag_record(
        session,
        ProfessorTagPayload.model_validate(payload.model_dump()),
        event_name="agent_cli.professor.tag_created",
        actor="agent_cli",
    )
    return _serialize_tag(tag)


def _agent_professor_error(error: ProfessorMutationError) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )


def _serialize_tag(tag: ProfessorTag) -> AgentProfessorTagRead:
    return AgentProfessorTagRead(
        id=tag.id,
        name=tag.name,
        text_color=tag.text_color,
        background_color=tag.background_color,
    )


def _serialize_professor(professor: Professor) -> AgentProfessorRead:
    result = AgentProfessorRead(
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
        tags=[_serialize_tag(tag) for tag in professor.tags],
    )
    return result.model_copy(update={"revision": revision_for(result)})
