from __future__ import annotations

from pathlib import Path

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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.core.agent_revisions import ensure_revision, revision_for
from app.core.database import get_async_session, get_session_factory
from app.models import OutreachTemplate
from app.modules.campaigns.public import (
    OutreachTemplateCreate,
    OutreachTemplateMutationError,
    OutreachTemplateUpdate,
    create_outreach_template_record,
    duplicate_outreach_template_record,
    import_outreach_template_file,
    restore_outreach_template_record,
    set_default_outreach_template_record,
    update_outreach_template_record,
)
from app.schemas.agent import (
    AgentChangePlanRead,
    AgentPage,
    AgentTemplateCreateRequest,
    AgentTemplateImportRead,
    AgentTemplateRead,
    AgentTemplateUpdateRequest,
)
from app.services.agent_change_plans import create_template_archive_change_plan
from app.services.agent_mutations import execute_agent_mutation

from .support import (
    _project_agent_collection_response,
    _slice_page,
)

router = APIRouter()


@router.get("/templates", response_model=AgentPage[AgentTemplateRead])
async def list_agent_templates(
    include_archived: bool = Query(default=False),
    template_id: int | None = Query(default=None, ge=1),
    is_default: bool | None = Query(default=None),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentTemplateRead] | Response:
    statement = select(OutreachTemplate)
    if not include_archived:
        statement = statement.where(OutreachTemplate.archived_at.is_(None))
    if template_id is not None:
        statement = statement.where(OutreachTemplate.id == template_id)
    if is_default is not None:
        statement = statement.where(OutreachTemplate.is_default.is_(is_default))
    templates = list(
        await session.scalars(
            statement.order_by(
                OutreachTemplate.is_default.desc(),
                OutreachTemplate.updated_at.desc(),
                OutreachTemplate.id.desc(),
            )
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(templates, cursor=cursor, limit=limit)
    response = AgentPage(
        items=[_serialize_template(template) for template in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return _project_agent_collection_response(response, fields)


@router.get("/templates/{template_id}", response_model=AgentTemplateRead)
async def read_agent_template(
    template_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentTemplateRead:
    template = await session.get(OutreachTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="未找到邮件模板")
    return _serialize_template(template)


@router.post("/templates/import-file", response_model=AgentTemplateImportRead)
async def import_agent_template_file(
    file: UploadFile = File(...),
) -> AgentTemplateImportRead:
    if not file.filename:
        raise AgentApiError(
            status_code=400,
            code="TEMPLATE_IMPORT_FILE_REQUIRED",
            message="请选择要解析的模板文件。",
        )
    try:
        imported = import_outreach_template_file(
            Path(file.filename).name,
            await file.read(),
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=400,
            code="TEMPLATE_IMPORT_INVALID",
            message=str(exc),
        ) from exc
    return AgentTemplateImportRead(
        subject=imported.subject,
        body_text=imported.body_text,
        body_html=imported.body_html,
        format_name=imported.format_name,
    )


@router.post(
    "/templates",
    response_model=AgentTemplateRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_template(
    payload: AgentTemplateCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentTemplateRead:
    try:
        return await execute_agent_mutation(
            session,
            command="templates.create",
            request_data=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            response_type=AgentTemplateRead,
            mutation=lambda: _create_agent_template(session, payload),
        )
    except OutreachTemplateMutationError as exc:
        raise _agent_template_error(exc) from exc


@router.put("/templates/{template_id}", response_model=AgentTemplateRead)
async def update_agent_template(
    template_id: int,
    payload: AgentTemplateUpdateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentTemplateRead:
    if not payload.model_fields_set:
        raise AgentApiError(
            status_code=400,
            code="EMPTY_TEMPLATE_UPDATE",
            message="请至少提供一个需要修改的模板字段。",
        )
    try:
        return await execute_agent_mutation(
            session,
            command="templates.update",
            request_data={
                "template_id": template_id,
                "if_revision": if_revision,
                **payload.model_dump(mode="json", exclude_unset=True),
            },
            idempotency_key=idempotency_key,
            response_type=AgentTemplateRead,
            mutation=lambda: _update_agent_template_with_revision(
                session,
                template_id,
                payload,
                if_revision=if_revision,
            ),
        )
    except OutreachTemplateMutationError as exc:
        raise _agent_template_error(exc) from exc


@router.post(
    "/templates/{template_id}/duplicate",
    response_model=AgentTemplateRead,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_agent_template(
    template_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentTemplateRead:
    try:
        return await execute_agent_mutation(
            session,
            command="templates.duplicate",
            request_data={"template_id": template_id},
            idempotency_key=idempotency_key,
            response_type=AgentTemplateRead,
            mutation=lambda: _duplicate_agent_template(session, template_id),
        )
    except OutreachTemplateMutationError as exc:
        raise _agent_template_error(exc) from exc


@router.post("/templates/{template_id}/default", response_model=AgentTemplateRead)
async def set_agent_template_default(
    template_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentTemplateRead:
    try:
        return await execute_agent_mutation(
            session,
            command="templates.set-default",
            request_data={"template_id": template_id, "if_revision": if_revision},
            idempotency_key=idempotency_key,
            response_type=AgentTemplateRead,
            mutation=lambda: _set_agent_template_default_with_revision(
                session,
                template_id,
                if_revision=if_revision,
            ),
        )
    except OutreachTemplateMutationError as exc:
        raise _agent_template_error(exc) from exc


@router.post("/templates/{template_id}/restore", response_model=AgentTemplateRead)
async def restore_agent_template(
    template_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentTemplateRead:
    try:
        return await execute_agent_mutation(
            session,
            command="templates.restore",
            request_data={"template_id": template_id, "if_revision": if_revision},
            idempotency_key=idempotency_key,
            response_type=AgentTemplateRead,
            mutation=lambda: _restore_agent_template_with_revision(
                session,
                template_id,
                if_revision=if_revision,
            ),
        )
    except OutreachTemplateMutationError as exc:
        raise _agent_template_error(exc) from exc


@router.post(
    "/templates/{template_id}/prepare-archive",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_template_archive(
    template_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_template_archive_change_plan(
        get_session_factory(),
        template_id,
        idempotency_key=idempotency_key,
    )


async def _create_agent_template(
    session: AsyncSession,
    payload: AgentTemplateCreateRequest,
) -> AgentTemplateRead:
    template = await create_outreach_template_record(
        session,
        OutreachTemplateCreate.model_validate(payload.model_dump()),
        event_name="agent_cli.template.created",
        actor="agent_cli",
    )
    return _serialize_template(template)


async def _update_agent_template(
    session: AsyncSession,
    template_id: int,
    payload: AgentTemplateUpdateRequest,
) -> AgentTemplateRead:
    template = await update_outreach_template_record(
        session,
        template_id,
        OutreachTemplateUpdate.model_validate(payload.model_dump(exclude_unset=True)),
        event_name="agent_cli.template.updated",
        actor="agent_cli",
    )
    return _serialize_template(template)


async def _ensure_template_revision(
    session: AsyncSession,
    template_id: int,
    if_revision: str | None,
) -> None:
    if not if_revision:
        return
    template = await session.get(OutreachTemplate, template_id)
    if template is None:
        raise OutreachTemplateMutationError(404, "TEMPLATE_NOT_FOUND", "未找到邮件模板")
    current = _serialize_template(template)
    ensure_revision(
        if_revision,
        current.revision,
        resource="templates",
        resource_id=template_id,
        latest=current.model_dump(mode="json"),
    )


async def _update_agent_template_with_revision(
    session: AsyncSession,
    template_id: int,
    payload: AgentTemplateUpdateRequest,
    *,
    if_revision: str | None,
) -> AgentTemplateRead:
    await _ensure_template_revision(session, template_id, if_revision)
    return await _update_agent_template(session, template_id, payload)


async def _duplicate_agent_template(
    session: AsyncSession,
    template_id: int,
) -> AgentTemplateRead:
    template = await duplicate_outreach_template_record(
        session,
        template_id,
        event_name="agent_cli.template.duplicated",
        actor="agent_cli",
    )
    return _serialize_template(template)


async def _set_agent_template_default(
    session: AsyncSession,
    template_id: int,
) -> AgentTemplateRead:
    template = await set_default_outreach_template_record(
        session,
        template_id,
        event_name="agent_cli.template.default_set",
        actor="agent_cli",
    )
    return _serialize_template(template)


async def _set_agent_template_default_with_revision(
    session: AsyncSession,
    template_id: int,
    *,
    if_revision: str | None,
) -> AgentTemplateRead:
    await _ensure_template_revision(session, template_id, if_revision)
    return await _set_agent_template_default(session, template_id)


async def _restore_agent_template(
    session: AsyncSession,
    template_id: int,
) -> AgentTemplateRead:
    template = await restore_outreach_template_record(
        session,
        template_id,
        event_name="agent_cli.template.restored",
        actor="agent_cli",
    )
    return _serialize_template(template)


async def _restore_agent_template_with_revision(
    session: AsyncSession,
    template_id: int,
    *,
    if_revision: str | None,
) -> AgentTemplateRead:
    await _ensure_template_revision(session, template_id, if_revision)
    return await _restore_agent_template(session, template_id)


def _agent_template_error(error: OutreachTemplateMutationError) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )


def _serialize_template(template: OutreachTemplate) -> AgentTemplateRead:
    result = AgentTemplateRead(
        id=template.id,
        name=template.name,
        recommended_generation_mode=template.recommended_generation_mode,
        subject=template.subject,
        body_text=template.body_text,
        body_html=template.body_html,
        is_default=template.is_default,
        archived_at=template.archived_at,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )
    return result.model_copy(update={"revision": revision_for(result)})
