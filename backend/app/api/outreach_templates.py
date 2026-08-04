from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.models import OutreachTemplate
from app.schemas.outreach_template import (
    OutreachTemplateCreate,
    OutreachTemplateRead,
    OutreachTemplateUpdate,
)
from app.services.outreach_template_library import (
    serialize_outreach_template,
)
from app.services.outreach_template_mutations import (
    OutreachTemplateMutationError,
    archive_outreach_template_record,
    create_outreach_template_record,
    duplicate_outreach_template_record,
    get_outreach_template_or_raise,
    restore_outreach_template_record,
    set_default_outreach_template_record,
    update_outreach_template_record,
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
        template = await create_outreach_template_record(
            session,
            payload,
            event_name="outreach_template.created",
            actor="desktop_ui",
        )
    except OutreachTemplateMutationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    await session.refresh(template)
    return serialize_outreach_template(template)


@router.put("/{template_id}", response_model=OutreachTemplateRead)
async def update_outreach_template(
    template_id: int,
    payload: OutreachTemplateUpdate,
    session: AsyncSession = Depends(get_async_session),
) -> OutreachTemplateRead:
    try:
        template = await update_outreach_template_record(
            session,
            template_id,
            payload,
            event_name="outreach_template.updated",
            actor="desktop_ui",
        )
    except OutreachTemplateMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()
    await session.refresh(template)
    return serialize_outreach_template(template)


@router.post("/{template_id}/duplicate", response_model=OutreachTemplateRead, status_code=201)
async def duplicate_outreach_template(
    template_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> OutreachTemplateRead:
    try:
        duplicate = await duplicate_outreach_template_record(
            session,
            template_id,
            event_name="outreach_template.duplicated",
            actor="desktop_ui",
        )
    except OutreachTemplateMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()
    await session.refresh(duplicate)
    return serialize_outreach_template(duplicate)


@router.post("/{template_id}/default", response_model=OutreachTemplateRead)
async def set_global_default_outreach_template(
    template_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> OutreachTemplateRead:
    try:
        template = await set_default_outreach_template_record(
            session,
            template_id,
            event_name="outreach_template.default_set",
            actor="desktop_ui",
        )
    except OutreachTemplateMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()
    await session.refresh(template)
    return serialize_outreach_template(template)


@router.delete("/{template_id}", response_model=OutreachTemplateRead)
async def archive_outreach_template(
    template_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> OutreachTemplateRead:
    try:
        template = await archive_outreach_template_record(
            session,
            template_id,
            event_name="outreach_template.archived",
            actor="desktop_ui",
        )
    except OutreachTemplateMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()
    await session.refresh(template)
    return serialize_outreach_template(template)


@router.post("/{template_id}/restore", response_model=OutreachTemplateRead)
async def restore_outreach_template(
    template_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> OutreachTemplateRead:
    try:
        template = await restore_outreach_template_record(
            session,
            template_id,
            event_name="outreach_template.restored",
            actor="desktop_ui",
        )
    except OutreachTemplateMutationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
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
        return await get_outreach_template_or_raise(
            session,
            template_id,
            include_archived=include_archived,
        )
    except OutreachTemplateMutationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
