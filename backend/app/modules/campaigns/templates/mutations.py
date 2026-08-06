from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models import OutreachTemplate
from .schemas import OutreachTemplateCreate, OutreachTemplateUpdate
from app.services.operation_logs import record_operation_log
from .library import (
    clear_global_default_template,
    get_outreach_template,
    normalize_generation_mode,
    normalize_nullable_template_text,
    normalize_template_name,
    sync_template_to_default_identities,
    unlink_template_from_identities,
)


@dataclass(slots=True)
class OutreachTemplateMutationError(ValueError):
    status_code: int
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


async def create_outreach_template_record(
    session: AsyncSession,
    payload: OutreachTemplateCreate,
    *,
    event_name: str,
    actor: str,
) -> OutreachTemplate:
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
        raise OutreachTemplateMutationError(400, "TEMPLATE_INVALID_INPUT", str(exc)) from exc
    session.add(template)
    await session.flush()
    if requested_default:
        await clear_global_default_template(session)
        await session.flush()
        template.is_default = True
    await record_outreach_template_event(session, template, event_name, actor=actor)
    return template


async def update_outreach_template_record(
    session: AsyncSession,
    template_id: int,
    payload: OutreachTemplateUpdate,
    *,
    event_name: str,
    actor: str,
) -> OutreachTemplate:
    template = await get_outreach_template_or_raise(session, template_id, include_archived=True)
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
        raise OutreachTemplateMutationError(400, "TEMPLATE_INVALID_INPUT", str(exc)) from exc

    if "is_default" in fields and payload.is_default is not None:
        if payload.is_default:
            if template.archived_at is not None:
                raise OutreachTemplateMutationError(
                    400,
                    "ARCHIVED_TEMPLATE_NOT_DEFAULTABLE",
                    "已删除模板不能设为默认模板",
                )
            await clear_global_default_template(session, exclude_id=template.id)
            await session.flush()
        template.is_default = payload.is_default

    template.updated_at = utc_now()
    await sync_template_to_default_identities(session, template)
    await record_outreach_template_event(session, template, event_name, actor=actor)
    return template


async def duplicate_outreach_template_record(
    session: AsyncSession,
    template_id: int,
    *,
    event_name: str,
    actor: str,
) -> OutreachTemplate:
    source = await get_outreach_template_or_raise(session, template_id, include_archived=True)
    suffix = "（副本）"
    duplicate = OutreachTemplate(
        name=f"{source.name[: 120 - len(suffix)]}{suffix}",
        recommended_generation_mode=source.recommended_generation_mode,
        subject=source.subject,
        body_text=source.body_text,
        body_html=source.body_html,
        is_default=False,
    )
    session.add(duplicate)
    await session.flush()
    await record_outreach_template_event(session, duplicate, event_name, actor=actor)
    return duplicate


async def set_default_outreach_template_record(
    session: AsyncSession,
    template_id: int,
    *,
    event_name: str,
    actor: str,
) -> OutreachTemplate:
    template = await get_outreach_template_or_raise(session, template_id)
    await clear_global_default_template(session, exclude_id=template.id)
    await session.flush()
    template.is_default = True
    template.updated_at = utc_now()
    await record_outreach_template_event(session, template, event_name, actor=actor)
    return template


async def archive_outreach_template_record(
    session: AsyncSession,
    template_id: int,
    *,
    event_name: str,
    actor: str,
) -> OutreachTemplate:
    template = await get_outreach_template_or_raise(session, template_id, include_archived=True)
    if template.archived_at is None:
        template.archived_at = utc_now()
        template.is_default = False
        await unlink_template_from_identities(session, template)
        await record_outreach_template_event(session, template, event_name, actor=actor)
    return template


async def restore_outreach_template_record(
    session: AsyncSession,
    template_id: int,
    *,
    event_name: str,
    actor: str,
) -> OutreachTemplate:
    template = await get_outreach_template_or_raise(session, template_id, include_archived=True)
    template.archived_at = None
    template.updated_at = utc_now()
    await record_outreach_template_event(session, template, event_name, actor=actor)
    return template


async def get_outreach_template_or_raise(
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
        raise OutreachTemplateMutationError(404, "TEMPLATE_NOT_FOUND", str(exc)) from exc


async def record_outreach_template_event(
    session: AsyncSession,
    template: OutreachTemplate,
    event_name: str,
    *,
    actor: str,
) -> None:
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="outreach_template",
        entity_id=str(template.id),
        metadata={
            "actor": actor,
            "name": template.name,
            "recommended_generation_mode": template.recommended_generation_mode,
            "is_default": template.is_default,
            "is_archived": template.archived_at is not None,
        },
    )
