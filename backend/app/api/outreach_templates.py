from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.time import utc_now
from app.models import OutreachTemplate
from app.schemas.outreach_template import (
    OutreachTemplateCreate,
    OutreachTemplateRead,
    OutreachTemplateUpdate,
)
from app.services.operation_logs import record_operation_log
from app.services.outreach_template_library import (
    clear_global_default_template,
    get_outreach_template,
    normalize_generation_mode,
    normalize_nullable_template_text,
    normalize_template_name,
    serialize_outreach_template,
    sync_template_to_default_identities,
    unlink_template_from_identities,
)


router = APIRouter(prefix="/api/outreach-templates", tags=["outreach-templates"])


@router.get("", response_model=list[OutreachTemplateRead])
async def list_outreach_templates(
    include_archived: bool = Query(default=False),
    session: AsyncSession = Depends(get_async_session),
) -> list[OutreachTemplateRead]:
    statement = select(OutreachTemplate)
    if not include_archived:
        statement = statement.where(OutreachTemplate.archived_at.is_(None))
    statement = statement.order_by(
        OutreachTemplate.is_default.desc(),
        OutreachTemplate.updated_at.desc(),
        OutreachTemplate.id.desc(),
    )
    templates = (await session.scalars(statement)).all()
    return [serialize_outreach_template(template) for template in templates]


@router.get("/{template_id}", response_model=OutreachTemplateRead)
async def read_outreach_template(
    template_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> OutreachTemplateRead:
    return serialize_outreach_template(
        await _get_template_or_404(session, template_id, include_archived=True),
    )


@router.post("", response_model=OutreachTemplateRead, status_code=status.HTTP_201_CREATED)
async def create_outreach_template(
    payload: OutreachTemplateCreate,
    session: AsyncSession = Depends(get_async_session),
) -> OutreachTemplateRead:
    try:
        requested_default = payload.is_default
        template = OutreachTemplate(
            name=normalize_template_name(payload.name),
            recommended_generation_mode=normalize_generation_mode(
                payload.recommended_generation_mode,
            ),
            subject=normalize_nullable_template_text(payload.subject),
            body_text=normalize_nullable_template_text(payload.body_text),
            body_html=normalize_nullable_template_text(payload.body_html),
            is_default=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.add(template)
    await session.flush()
    if requested_default:
        await clear_global_default_template(session)
        await session.flush()
        template.is_default = True
    await _record_template_log(session, template, "outreach_template.created")
    await session.commit()
    await session.refresh(template)
    return serialize_outreach_template(template)


@router.put("/{template_id}", response_model=OutreachTemplateRead)
async def update_outreach_template(
    template_id: int,
    payload: OutreachTemplateUpdate,
    session: AsyncSession = Depends(get_async_session),
) -> OutreachTemplateRead:
    template = await _get_template_or_404(session, template_id, include_archived=True)
    fields = payload.model_fields_set
    try:
        if "name" in fields:
            template.name = normalize_template_name(payload.name)
        if "recommended_generation_mode" in fields:
            template.recommended_generation_mode = normalize_generation_mode(
                payload.recommended_generation_mode,
            )
        if "subject" in fields:
            template.subject = normalize_nullable_template_text(payload.subject)
        if "body_text" in fields:
            template.body_text = normalize_nullable_template_text(payload.body_text)
        if "body_html" in fields:
            template.body_html = normalize_nullable_template_text(payload.body_html)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if "is_default" in fields and payload.is_default is not None:
        if payload.is_default:
            if template.archived_at is not None:
                raise HTTPException(status_code=400, detail="归档模板不能设为默认模板")
            await clear_global_default_template(session, exclude_id=template.id)
            await session.flush()
        template.is_default = payload.is_default

    template.updated_at = utc_now()
    await sync_template_to_default_identities(session, template)
    await _record_template_log(session, template, "outreach_template.updated")
    await session.commit()
    await session.refresh(template)
    return serialize_outreach_template(template)


@router.post("/{template_id}/duplicate", response_model=OutreachTemplateRead, status_code=201)
async def duplicate_outreach_template(
    template_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> OutreachTemplateRead:
    source = await _get_template_or_404(session, template_id, include_archived=True)
    duplicate_suffix = "（副本）"
    duplicate = OutreachTemplate(
        name=f"{source.name[: 120 - len(duplicate_suffix)]}{duplicate_suffix}",
        recommended_generation_mode=source.recommended_generation_mode,
        subject=source.subject,
        body_text=source.body_text,
        body_html=source.body_html,
        is_default=False,
    )
    session.add(duplicate)
    await session.flush()
    await _record_template_log(session, duplicate, "outreach_template.duplicated")
    await session.commit()
    await session.refresh(duplicate)
    return serialize_outreach_template(duplicate)


@router.post("/{template_id}/default", response_model=OutreachTemplateRead)
async def set_global_default_outreach_template(
    template_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> OutreachTemplateRead:
    template = await _get_template_or_404(session, template_id)
    await clear_global_default_template(session, exclude_id=template.id)
    await session.flush()
    template.is_default = True
    template.updated_at = utc_now()
    await _record_template_log(session, template, "outreach_template.default_set")
    await session.commit()
    await session.refresh(template)
    return serialize_outreach_template(template)


@router.delete("/{template_id}", response_model=OutreachTemplateRead)
async def archive_outreach_template(
    template_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> OutreachTemplateRead:
    template = await _get_template_or_404(session, template_id, include_archived=True)
    if template.archived_at is None:
        template.archived_at = utc_now()
        template.is_default = False
        await unlink_template_from_identities(session, template)
        await _record_template_log(session, template, "outreach_template.archived")
        await session.commit()
        await session.refresh(template)
    return serialize_outreach_template(template)


@router.post("/{template_id}/restore", response_model=OutreachTemplateRead)
async def restore_outreach_template(
    template_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> OutreachTemplateRead:
    template = await _get_template_or_404(session, template_id, include_archived=True)
    template.archived_at = None
    template.updated_at = utc_now()
    await _record_template_log(session, template, "outreach_template.restored")
    await session.commit()
    await session.refresh(template)
    return serialize_outreach_template(template)


async def _get_template_or_404(
    session: AsyncSession,
    template_id: int,
    *,
    include_archived: bool = False,
) -> OutreachTemplate:
    try:
        return await get_outreach_template(
            session,
            template_id,
            include_archived=include_archived,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _record_template_log(
    session: AsyncSession,
    template: OutreachTemplate,
    event_name: str,
) -> None:
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="outreach_template",
        entity_id=str(template.id),
        metadata={
            "name": template.name,
            "recommended_generation_mode": template.recommended_generation_mode,
            "is_default": template.is_default,
            "is_archived": template.archived_at is not None,
        },
    )
