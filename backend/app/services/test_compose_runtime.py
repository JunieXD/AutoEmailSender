from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.identity_serializers import serialize_material
from app.models import (
    IdentityMaterial,
    IdentityProfile,
    LLMProfile,
    Professor,
    TestComposeMessage,
    TestComposeSession,
)
from app.schemas.test_compose import (
    TestComposeDraftRead,
    TestComposeDraftUpdateRequest,
    TestComposeIdentityRead,
    TestComposeLLMRead,
    TestComposeMessageRead,
    TestComposeMessageSendRequest,
    TestComposeStatusRead,
    TestComposeThreadRead,
)
from app.services import llm_runtime, mail_runtime
from app.services.mail_runtime import MailAttachment
from app.services.materials import build_material_download_name, ensure_material_extracted_text
from app.services.operation_logs import record_operation_log
from app.services.outreach_templates import (
    OUTREACH_GENERATION_MODE_TEMPLATE,
    TEST_RECIPIENT_NAME,
    build_test_compose_send_template_context,
    build_test_compose_template_context,
    get_identity_sender_name,
    get_outreach_template_defaults_validation_error,
    render_template_with_context,
    resolve_outreach_template_config,
)
from app.services.rich_text import normalize_email_html, text_to_email_html
from app.services.runtime_settings import get_runtime_settings


async def build_test_compose_thread(
    session: AsyncSession,
    *,
    identity_id: int,
    llm_profile_id: int,
) -> TestComposeThreadRead:
    identity = await _get_identity(session, identity_id)
    llm_profile = await _get_llm_profile(session, llm_profile_id)
    compose_session = await _get_or_create_test_compose_session(session, identity_id, llm_profile_id, identity)
    if _synchronize_selected_material_ids(compose_session, identity):
        await session.commit()
    history = await _list_test_compose_messages(session, compose_session.id)
    return _serialize_test_compose_thread(identity, llm_profile, compose_session, history)


async def get_test_compose_status(
    session: AsyncSession,
    *,
    identity_id: int,
) -> TestComposeStatusRead:
    await _get_identity(session, identity_id)
    sent_message_id = await session.scalar(
        select(TestComposeMessage.id)
        .where(
            TestComposeMessage.identity_id == identity_id,
            TestComposeMessage.status == "sent",
        )
        .limit(1),
    )
    return TestComposeStatusRead(completed=sent_message_id is not None)


async def generate_test_compose_draft(
    session: AsyncSession,
    *,
    identity_id: int,
    llm_profile_id: int,
) -> TestComposeThreadRead:
    identity = await _get_identity(session, identity_id)
    llm_profile = await _get_llm_profile(session, llm_profile_id)
    compose_session = await _get_or_create_test_compose_session(session, identity_id, llm_profile_id, identity)
    outreach_config = resolve_outreach_template_config(identity)

    template_subject = (outreach_config.subject_template or "").strip() or None
    template_body = (outreach_config.body_text_template or "").strip() or None
    template_body_html = (outreach_config.body_html_template or "").strip() or None
    detail = get_outreach_template_defaults_validation_error(template_subject, template_body)
    if detail:
        raise ValueError(detail)

    if outreach_config.generation_mode == OUTREACH_GENERATION_MODE_TEMPLATE:
        compose_session.subject = template_subject
        compose_session.body_text = template_body or ""
        compose_session.body_html = (outreach_config.body_html_template or "").strip() or None
    else:
        primary_material = identity.current_primary_material
        if primary_material is None:
            raise ValueError("请先选择用于匹配的默认材料")
        ensure_material_extracted_text(primary_material)
        pseudo_professor = _build_self_recipient_professor(identity)
        runtime_settings = await get_runtime_settings(session)
        rewrite_preferences = llm_runtime.DraftRewritePreferences(
            draft_rewrite_intensity=runtime_settings.draft_rewrite_intensity,
            draft_rewrite_tone=runtime_settings.draft_rewrite_tone,
            draft_rewrite_formality=runtime_settings.draft_rewrite_formality,
            draft_rewrite_length=runtime_settings.draft_rewrite_length,
            draft_rewrite_specificity=runtime_settings.draft_rewrite_specificity,
            draft_template_preservation=runtime_settings.draft_template_preservation,
            draft_custom_instruction=runtime_settings.draft_custom_instruction,
        )
        generation = await llm_runtime.generate_draft_content(
            identity=identity,
            primary_material=primary_material,
            llm_profile=llm_profile,
            professor=pseudo_professor,
            available_materials=list(identity.materials),
            custom_subject=template_subject,
            custom_body=template_body,
            custom_body_html=template_body_html,
            current_match=None,
            max_tokens=runtime_settings.draft_max_tokens,
            rewrite_preferences=rewrite_preferences,
        )
        compose_session.subject = generation.result.subject
        compose_session.body_text = generation.result.body_text
        compose_session.body_html = generation.result.body_html

    compose_session.updated_at = datetime.now(UTC)
    await _record_test_compose_log(
        session,
        compose_session,
        "test_compose.draft_generated",
        metadata={"generation_mode": outreach_config.generation_mode},
    )
    await session.commit()

    history = await _list_test_compose_messages(session, compose_session.id)
    return _serialize_test_compose_thread(identity, llm_profile, compose_session, history)


async def send_test_compose_message(
    session: AsyncSession,
    *,
    identity_id: int,
    llm_profile_id: int,
    payload: TestComposeMessageSendRequest,
) -> TestComposeThreadRead:
    identity = await _get_identity(session, identity_id)
    llm_profile = await _get_llm_profile(session, llm_profile_id)
    compose_session = await _get_or_create_test_compose_session(session, identity_id, llm_profile_id, identity)
    selected_material_ids = payload.selected_material_ids or []

    await _validate_selected_material_ids(session, identity_id, selected_material_ids)

    draft_subject = (payload.subject or "").strip() or None
    if payload.body_html:
        draft_rendered = normalize_email_html(payload.body_html)
    else:
        draft_rendered = text_to_email_html(payload.body_text)

    context = build_test_compose_send_template_context(identity)
    subject = render_template_with_context(payload.subject, context).strip()
    rendered_body_text = render_template_with_context(payload.body_text, context)
    rendered_body_html = render_template_with_context(payload.body_html, context)
    if rendered_body_html.strip():
        rendered = normalize_email_html(rendered_body_html)
    else:
        rendered = text_to_email_html(rendered_body_text)
    body_text = rendered.text
    body_html = rendered.html
    if not subject or not body_text:
        raise ValueError("测试邮件需要主题和正文")

    compose_session.subject = draft_subject
    compose_session.body_text = draft_rendered.text
    compose_session.body_html = draft_rendered.html
    compose_session.selected_material_ids = selected_material_ids
    compose_session.updated_at = datetime.now(UTC)

    attachments = await _resolve_selected_materials(session, identity_id, selected_material_ids)

    try:
        result = await mail_runtime.send_email_to_recipient(
            identity=identity,
            recipient_name=TEST_RECIPIENT_NAME,
            recipient_email=identity.email_address,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
        )
        message = TestComposeMessage(
            session_id=compose_session.id,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            recipient_email=identity.email_address,
            subject=subject,
            content=body_text,
            content_html=body_html,
            status="sent",
            rfc_message_id=result.message_id,
            provider_payload=result.provider_payload,
        )
    except mail_runtime.MailRuntimeError as exc:
        message = TestComposeMessage(
            session_id=compose_session.id,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            recipient_email=identity.email_address,
            subject=subject,
            content=body_text,
            content_html=body_html,
            status="send_failed",
            failure_summary=str(exc),
        )

    session.add(message)
    await _record_test_compose_log(
        session,
        compose_session,
        "test_compose.sent" if message.status == "sent" else "test_compose.send_failed",
        level="info" if message.status == "sent" else "warning",
        message=message.failure_summary,
        metadata={
            "status": message.status,
            "message_id": message.rfc_message_id,
            "attachment_count": len(attachments),
        },
    )
    await session.commit()

    history = await _list_test_compose_messages(session, compose_session.id)
    return _serialize_test_compose_thread(identity, llm_profile, compose_session, history)


async def save_test_compose_draft(
    session: AsyncSession,
    *,
    identity_id: int,
    llm_profile_id: int,
    payload: TestComposeDraftUpdateRequest,
) -> TestComposeThreadRead:
    identity = await _get_identity(session, identity_id)
    llm_profile = await _get_llm_profile(session, llm_profile_id)
    compose_session = await _get_or_create_test_compose_session(session, identity_id, llm_profile_id, identity)
    selected_material_ids = payload.selected_material_ids or []

    await _validate_selected_material_ids(session, identity_id, selected_material_ids)

    compose_session.subject = (payload.subject or "").strip() or None
    if payload.body_html:
        rendered = normalize_email_html(payload.body_html)
    else:
        rendered = text_to_email_html(payload.body_text)
    compose_session.body_text = rendered.text
    compose_session.body_html = rendered.html
    compose_session.selected_material_ids = selected_material_ids
    compose_session.updated_at = datetime.now(UTC)
    await _record_test_compose_log(
        session,
        compose_session,
        "test_compose.draft_saved",
        metadata={"selected_material_ids": selected_material_ids},
    )
    await session.commit()

    history = await _list_test_compose_messages(session, compose_session.id)
    return _serialize_test_compose_thread(identity, llm_profile, compose_session, history)


async def _get_identity(session: AsyncSession, identity_id: int) -> IdentityProfile:
    identity = await session.scalar(
        select(IdentityProfile)
        .options(
            selectinload(IdentityProfile.materials),
            selectinload(IdentityProfile.current_primary_material),
        )
        .where(IdentityProfile.id == identity_id),
    )
    if not identity:
        raise ValueError("未找到身份配置")
    return identity


async def _get_llm_profile(session: AsyncSession, llm_profile_id: int) -> LLMProfile:
    llm_profile = await session.get(LLMProfile, llm_profile_id)
    if not llm_profile:
        raise ValueError("未找到 LLM 配置")
    return llm_profile


async def _get_or_create_test_compose_session(
    session: AsyncSession,
    identity_id: int,
    llm_profile_id: int,
    identity: IdentityProfile,
) -> TestComposeSession:
    compose_session = await session.scalar(
        select(TestComposeSession)
        .where(
            TestComposeSession.identity_id == identity_id,
            TestComposeSession.llm_profile_id == llm_profile_id,
        )
        .order_by(TestComposeSession.updated_at.desc(), TestComposeSession.id.desc()),
    )
    if compose_session:
        return compose_session

    compose_session = TestComposeSession(
        identity_id=identity_id,
        llm_profile_id=llm_profile_id,
        subject=identity.outreach_template_subject,
        body_text=identity.outreach_template_body_text or "",
        body_html=identity.outreach_template_body_html,
        selected_material_ids=[],
    )
    session.add(compose_session)
    await session.commit()
    await session.refresh(compose_session)
    return compose_session


async def _list_test_compose_messages(
    session: AsyncSession,
    session_id: int,
) -> list[TestComposeMessage]:
    return list(
        (
            await session.execute(
                select(TestComposeMessage)
                .where(TestComposeMessage.session_id == session_id)
                .order_by(TestComposeMessage.created_at.desc(), TestComposeMessage.id.desc()),
            )
        ).scalars()
    )


async def _validate_selected_material_ids(
    session: AsyncSession,
    identity_id: int,
    material_ids: list[int],
) -> None:
    if not material_ids:
        return
    materials = list(
        (
            await session.execute(
                select(IdentityMaterial.id).where(
                    IdentityMaterial.identity_id == identity_id,
                    IdentityMaterial.id.in_(material_ids),
                ),
            )
        ).scalars()
    )
    if len(set(materials)) != len(set(material_ids)):
        raise ValueError("存在不属于当前身份的随信材料")


async def _resolve_selected_materials(
    session: AsyncSession,
    identity_id: int,
    material_ids: list[int],
) -> list[MailAttachment]:
    if not material_ids:
        return []

    result = await session.execute(
        select(IdentityMaterial).where(
            IdentityMaterial.identity_id == identity_id,
            IdentityMaterial.id.in_(material_ids),
        ),
    )
    materials = {material.id: material for material in result.scalars()}
    attachments: list[MailAttachment] = []
    for material_id in material_ids:
        material = materials.get(material_id)
        if material is None:
            continue
        attachments.append(
            MailAttachment(
                file_path=material.file_path,
                download_name=build_material_download_name(material),
            ),
        )
    return attachments


def _build_self_recipient_professor(identity: IdentityProfile) -> Professor:
    return Professor(
        name=TEST_RECIPIENT_NAME,
        email=identity.email_address,
        title=TEST_RECIPIENT_NAME,
        university="测试学校",
        school="测试学院",
        department="测试院系",
        research_direction="测试研究方向",
        recent_papers=[],
    )


async def _record_test_compose_log(
    session: AsyncSession,
    compose_session: TestComposeSession,
    event_name: str,
    *,
    level: str = "info",
    message: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    base_metadata: dict[str, object] = {
        "session_id": compose_session.id,
        "identity_id": compose_session.identity_id,
        "llm_profile_id": compose_session.llm_profile_id,
    }
    if metadata:
        base_metadata.update(metadata)
    await record_operation_log(
        session,
        category="email",
        event_name=event_name,
        level=level,
        message=message,
        entity_type="test_compose",
        entity_id=str(compose_session.identity_id),
        metadata=base_metadata,
    )


def _serialize_test_compose_thread(
    identity: IdentityProfile,
    llm_profile: LLMProfile,
    compose_session: TestComposeSession,
    history: list[TestComposeMessage],
) -> TestComposeThreadRead:
    return TestComposeThreadRead(
        identity=TestComposeIdentityRead(
            id=identity.id,
            name=identity.profile_name or identity.name,
            profile_name=identity.profile_name or identity.name,
            sender_name=get_identity_sender_name(identity),
            email_address=identity.email_address,
        ),
        llm_profile=TestComposeLLMRead(
            id=llm_profile.id,
            name=llm_profile.name,
            provider=llm_profile.provider,
            model_name=llm_profile.model_name,
        ),
        material_options=[
            serialize_material(material, identity.current_primary_material_id)
            for material in sorted(identity.materials, key=lambda item: item.created_at, reverse=True)
        ],
        draft=TestComposeDraftRead(
            subject=compose_session.subject,
            body_text=compose_session.body_text,
            body_html=compose_session.body_html,
            selected_material_ids=compose_session.selected_material_ids or [],
        ),
        history=[
            TestComposeMessageRead(
                id=message.id,
                recipient_email=message.recipient_email,
                subject=message.subject,
                content=message.content,
                content_html=message.content_html,
                status=message.status,
                rfc_message_id=message.rfc_message_id,
                failure_summary=message.failure_summary,
                created_at=message.created_at,
            )
            for message in history
        ],
    )


def _synchronize_selected_material_ids(
    compose_session: TestComposeSession,
    identity: IdentityProfile,
) -> bool:
    current_material_ids = {material.id for material in identity.materials}
    existing_ids = compose_session.selected_material_ids or []
    filtered_ids = [
        material_id for material_id in existing_ids if material_id in current_material_ids
    ]
    if filtered_ids == existing_ids:
        return False
    compose_session.selected_material_ids = filtered_ids
    compose_session.updated_at = datetime.now(UTC)
    return True

