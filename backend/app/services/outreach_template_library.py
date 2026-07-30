from __future__ import annotations

from app.core.time import utc_now
from app.models import IdentityProfile, OutreachTemplate
from app.schemas.outreach_template import OutreachTemplateRead
from app.services.outreach_templates import (
    OUTREACH_GENERATION_MODE_LLM,
    OUTREACH_GENERATION_MODE_TEMPLATE,
)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


VALID_GENERATION_MODES = {
    OUTREACH_GENERATION_MODE_LLM,
    OUTREACH_GENERATION_MODE_TEMPLATE,
}


def serialize_outreach_template(template: OutreachTemplate) -> OutreachTemplateRead:
    return OutreachTemplateRead(
        id=template.id,
        name=template.name,
        recommended_generation_mode=template.recommended_generation_mode,
        subject=template.subject,
        body_text=template.body_text,
        body_html=template.body_html,
        is_ready=bool(
            (template.subject or "").strip()
            and (template.body_text or "").strip()
        ),
        is_default=template.is_default,
        archived_at=template.archived_at,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def normalize_template_name(value: object) -> str:
    name = str(value or "").strip()
    if not name:
        raise ValueError("请填写模板名称")
    if len(name) > 120:
        raise ValueError("模板名称不能超过 120 个字符")
    return name


def normalize_generation_mode(value: object) -> str:
    mode = str(value or OUTREACH_GENERATION_MODE_LLM).strip().lower()
    if mode not in VALID_GENERATION_MODES:
        raise ValueError("不支持的发信模式")
    return mode


def normalize_nullable_template_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


async def get_outreach_template(
    session: AsyncSession,
    template_id: int,
    *,
    include_archived: bool = False,
) -> OutreachTemplate:
    template = await session.get(OutreachTemplate, template_id)
    if template is None or (template.archived_at is not None and not include_archived):
        raise ValueError("未找到可用模板")
    return template


async def get_default_outreach_template_for_identity(
    session: AsyncSession,
    identity: IdentityProfile,
) -> OutreachTemplate | None:
    if identity.default_outreach_template_id is not None:
        template = await session.get(
            OutreachTemplate,
            identity.default_outreach_template_id,
        )
        if template is not None and template.archived_at is None:
            return template
    return await session.scalar(
        select(OutreachTemplate)
        .where(
            OutreachTemplate.is_default.is_(True),
            OutreachTemplate.archived_at.is_(None),
        )
        .order_by(OutreachTemplate.id.asc())
        .limit(1),
    )


async def clear_global_default_template(
    session: AsyncSession,
    *,
    exclude_id: int | None = None,
) -> None:
    statement = select(OutreachTemplate).where(OutreachTemplate.is_default.is_(True))
    if exclude_id is not None:
        statement = statement.where(OutreachTemplate.id != exclude_id)
    for template in (await session.scalars(statement)).all():
        template.is_default = False
        template.updated_at = utc_now()


def apply_template_to_identity_legacy_fields(
    identity: IdentityProfile,
    template: OutreachTemplate,
) -> None:
    identity.default_outreach_template_id = template.id
    identity.default_outreach_template = template
    identity.outreach_generation_mode = template.recommended_generation_mode
    identity.outreach_template_subject = template.subject
    identity.outreach_template_body_text = template.body_text
    identity.outreach_template_body_html = template.body_html
    identity.updated_at = utc_now()


def clear_identity_default_template(identity: IdentityProfile) -> None:
    identity.default_outreach_template_id = None
    identity.default_outreach_template = None
    identity.outreach_generation_mode = OUTREACH_GENERATION_MODE_LLM
    identity.outreach_template_subject = None
    identity.outreach_template_body_text = None
    identity.outreach_template_body_html = None
    identity.updated_at = utc_now()


async def sync_template_to_default_identities(
    session: AsyncSession,
    template: OutreachTemplate,
) -> None:
    identities = (
        await session.scalars(
            select(IdentityProfile).where(
                IdentityProfile.default_outreach_template_id == template.id,
            )
        )
    ).unique().all()
    for identity in identities:
        apply_template_to_identity_legacy_fields(identity, template)


async def unlink_template_from_identities(
    session: AsyncSession,
    template: OutreachTemplate,
) -> None:
    identities = (
        await session.scalars(
            select(IdentityProfile).where(
                IdentityProfile.default_outreach_template_id == template.id,
            )
        )
    ).unique().all()
    for identity in identities:
        clear_identity_default_template(identity)


def identity_has_legacy_template(identity: IdentityProfile) -> bool:
    return bool(
        identity.outreach_template_subject is not None
        or identity.outreach_template_body_text is not None
        or identity.outreach_template_body_html is not None
        or identity.outreach_generation_mode != OUTREACH_GENERATION_MODE_LLM
    )


async def create_template_from_legacy_identity(
    session: AsyncSession,
    identity: IdentityProfile,
) -> OutreachTemplate | None:
    if (
        identity.default_outreach_template_id is not None
        or not identity_has_legacy_template(identity)
    ):
        return identity.default_outreach_template

    template = OutreachTemplate(
        name=f"{identity.profile_name or identity.name} · 默认模板",
        recommended_generation_mode=normalize_generation_mode(
            identity.outreach_generation_mode,
        ),
        subject=identity.outreach_template_subject,
        body_text=identity.outreach_template_body_text,
        body_html=identity.outreach_template_body_html,
        is_default=False,
    )
    session.add(template)
    await session.flush()
    apply_template_to_identity_legacy_fields(identity, template)
    if identity.is_default:
        current_default = await session.scalar(
            select(OutreachTemplate.id)
            .where(OutreachTemplate.is_default.is_(True))
            .limit(1)
        )
        if current_default is None:
            template.is_default = True
    return template
