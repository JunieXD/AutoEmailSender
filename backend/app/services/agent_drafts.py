from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.api.workspace_support import ensure_workspace_task
from app.core.time import utc_now
from app.models import (
    EmailTask,
    EmailTaskStatus,
    IdentityMaterial,
    LLMProfile,
)
from app.schemas.agent import (
    AgentDraftGenerateRequest,
    AgentDraftRegenerateRequest,
    AgentDraftRewriteRequest,
    AgentDraftSaveRequest,
)
from app.schemas.email_task import EmailTaskApprovalRequest, EmailTaskRewriteDraftRequest
from app.modules.identities.public import material_can_be_primary
from app.services.match_results import load_resolved_match_result
from app.services.operation_logs import record_operation_log
from app.modules.campaigns.public import (
    get_default_outreach_template_for_identity,
    get_outreach_template,
)
from app.modules.campaigns.public import (
    OUTREACH_GENERATION_MODE_LLM,
    OUTREACH_GENERATION_MODE_TEMPLATE,
    get_outreach_template_defaults_validation_error,
    resolve_outreach_template_config,
)
from app.services.rich_text import normalize_email_html
from app.services.task_runtime import (
    regenerate_task_draft,
    save_task_draft,
    start_follow_up_task,
)


async def generate_agent_draft(
    session_factory: async_sessionmaker[AsyncSession],
    payload: AgentDraftGenerateRequest,
) -> EmailTask:
    task = await _ensure_editable_workspace_task(session_factory, payload)
    await _configure_agent_draft(session_factory, task.id, payload)

    if payload.generation_mode == "manual":
        body_text, body_html = _normalize_manual_body(
            payload.body_text or "",
            payload.body_html,
        )
        await save_task_draft(
            session_factory,
            task.id,
            EmailTaskApprovalRequest(
                subject=payload.subject,
                body_text=body_text,
                body_html=body_html,
                selected_material_ids=payload.attachment_material_ids,
            ),
        )
    else:
        await regenerate_task_draft(
            session_factory,
            task.id,
            llm_profile_id=payload.llm_profile_id,
        )
    return await load_agent_draft_task(session_factory, task.id)


async def save_agent_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: AgentDraftSaveRequest,
) -> EmailTask:
    async with session_factory() as session:
        task = await _load_task(session, task_id)
        if task is None:
            raise ValueError("未找到邮件任务")
        _ensure_draft_only_state(task)
        await _validate_attachment_material_ids(
            session,
            task.identity_id,
            payload.attachment_material_ids,
        )
        task.status = EmailTaskStatus.REVIEW_REQUIRED.value
        task.scheduled_at = None
        task.approved_at = None
        task.updated_at = utc_now()
        await session.commit()

    body_text, body_html = _normalize_manual_body(payload.body_text, payload.body_html)
    await save_task_draft(
        session_factory,
        task_id,
        EmailTaskApprovalRequest(
            subject=payload.subject,
            body_text=body_text,
            body_html=body_html,
            selected_material_ids=payload.attachment_material_ids,
        ),
    )
    return await load_agent_draft_task(session_factory, task_id)


async def regenerate_agent_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: AgentDraftRegenerateRequest,
) -> EmailTask:
    async with session_factory() as session:
        task = await _load_task(session, task_id)
        if task is None:
            raise ValueError("未找到邮件任务")
        _ensure_draft_only_state(task)
        _, match_result = await load_resolved_match_result(
            session,
            active_identity_id=task.identity_id,
            professor_id=task.professor_id,
        )
        task.status = (
            EmailTaskStatus.MATCHED.value
            if match_result is not None
            else EmailTaskStatus.DISCOVERED.value
        )
        task.approved_subject = None
        task.approved_body_text = None
        task.approved_body_html = None
        task.approved_at = None
        task.scheduled_at = None
        task.updated_at = utc_now()
        await session.commit()
    await regenerate_task_draft(
        session_factory,
        task_id,
        llm_profile_id=payload.llm_profile_id,
    )
    return await load_agent_draft_task(session_factory, task_id)


async def rewrite_agent_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: AgentDraftRewriteRequest,
) -> EmailTask:
    await rewrite_task_draft(
        session_factory,
        task_id,
        EmailTaskRewriteDraftRequest(
            subject=payload.subject,
            body_text=payload.body_text,
            body_html=payload.body_html,
            selected_material_ids=payload.attachment_material_ids,
            llm_profile_id=payload.llm_profile_id,
        ),
    )
    return await load_agent_draft_task(session_factory, task_id)


async def load_agent_draft_task(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
) -> EmailTask:
    async with session_factory() as session:
        task = await _load_task(session, task_id)
        if task is None:
            raise ValueError("未找到邮件任务")
        return task


async def _ensure_editable_workspace_task(
    session_factory: async_sessionmaker[AsyncSession],
    payload: AgentDraftGenerateRequest,
) -> EmailTask:
    async with session_factory() as session:
        task = await ensure_workspace_task(
            session,
            professor_id=payload.professor_id,
            identity_id=payload.identity_id,
            llm_profile_id=payload.llm_profile_id,
        )
        task_id = task.id
        status = task.status

    if status in {
        EmailTaskStatus.SENT.value,
        EmailTaskStatus.REPLY_DETECTED.value,
    }:
        await start_follow_up_task(session_factory, task_id)
        async with session_factory() as session:
            task = await ensure_workspace_task(
                session,
                professor_id=payload.professor_id,
                identity_id=payload.identity_id,
                llm_profile_id=payload.llm_profile_id,
            )
            task_id = task.id
    return await load_agent_draft_task(session_factory, task_id)


async def _configure_agent_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: AgentDraftGenerateRequest,
) -> None:
    async with session_factory() as session:
        task = await _load_task(session, task_id)
        if task is None:
            raise ValueError("未找到邮件任务")
        _ensure_draft_only_state(task)
        _, match_result = await load_resolved_match_result(
            session,
            active_identity_id=task.identity_id,
            professor_id=task.professor_id,
        )

        llm_profile = await session.get(LLMProfile, payload.llm_profile_id)
        if llm_profile is None:
            raise ValueError("未找到 LLM 配置")
        selected_template = (
            await get_outreach_template(session, payload.template_id)
            if payload.template_id is not None
            else await get_default_outreach_template_for_identity(session, task.identity)
        )
        internal_mode = (
            OUTREACH_GENERATION_MODE_TEMPLATE
            if payload.generation_mode == "template"
            else OUTREACH_GENERATION_MODE_LLM
        )
        resolved = resolve_outreach_template_config(
            task.identity,
            template=selected_template,
            generation_mode=internal_mode,
            subject_template=payload.subject,
            body_text_template=payload.body_text,
            body_html_template=payload.body_html,
        )
        if payload.generation_mode != "manual":
            validation_error = get_outreach_template_defaults_validation_error(
                resolved.subject_template,
                resolved.body_text_template,
            )
            if validation_error:
                raise ValueError(validation_error)

        reference_material = None
        if payload.reference_material_id is not None:
            reference_material = await _validate_reference_material_id(
                session,
                task.identity_id,
                payload.reference_material_id,
            )
        if payload.generation_mode == "ai_rewrite" and reference_material is None:
            raise ValueError("AI 改写必须明确指定 AI 写信参考材料")
        await _validate_attachment_material_ids(
            session,
            task.identity_id,
            payload.attachment_material_ids,
        )

        task.llm_profile_id = llm_profile.id
        task.primary_material_id = reference_material.id if reference_material else None
        task.selected_material_ids = list(payload.attachment_material_ids)
        task.outreach_template_id = selected_template.id if selected_template else None
        task.outreach_template_snapshot_version = 1
        task.outreach_generation_mode = (
            "manual" if payload.generation_mode == "manual" else resolved.generation_mode
        )
        task.outreach_template_subject = _normalize_nullable_text(resolved.subject_template)
        task.outreach_template_body_text = _normalize_nullable_text(resolved.body_text_template)
        task.outreach_template_body_html = _normalize_nullable_text(resolved.body_html_template)
        task.generated_subject = None
        task.generated_content_text = None
        task.generated_content_html = None
        task.draft_generation_source = None
        task.draft_fallback_reason = None
        task.approved_subject = None
        task.approved_body_text = None
        task.approved_body_html = None
        task.approved_at = None
        task.scheduled_at = None
        task.last_error = None
        task.status = (
            EmailTaskStatus.REVIEW_REQUIRED.value
            if payload.generation_mode == "manual"
            else (
                EmailTaskStatus.MATCHED.value
                if match_result is not None
                else EmailTaskStatus.DISCOVERED.value
            )
        )
        task.updated_at = utc_now()
        await record_operation_log(
            session,
            category="email",
            event_name="agent_cli.draft_configured",
            entity_type="email_task",
            entity_id=str(task.id),
            metadata={
                "actor": "agent_cli",
                "generation_mode": payload.generation_mode,
                "template_id": task.outreach_template_id,
                "reference_material_id": task.primary_material_id,
                "attachment_material_ids": task.selected_material_ids,
            },
        )
        await session.commit()


def _ensure_draft_only_state(task: EmailTask) -> None:
    if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
        raise ValueError("草稿正在生成，请等待完成后再修改")
    if task.status in {
        EmailTaskStatus.SENDING.value,
        EmailTaskStatus.SENT.value,
        EmailTaskStatus.REPLY_DETECTED.value,
        EmailTaskStatus.CANCELED.value,
    } or task.sent_at is not None or task.is_replied:
        raise ValueError("当前任务不能作为 draft_only 草稿修改")


async def _validate_reference_material_id(
    session: AsyncSession,
    identity_id: int,
    material_id: int,
) -> IdentityMaterial:
    material = await session.scalar(
        select(IdentityMaterial).where(
            IdentityMaterial.id == material_id,
            IdentityMaterial.identity_id == identity_id,
        ),
    )
    if material is None:
        raise ValueError("AI 写信参考材料不属于当前身份")
    if not material_can_be_primary(material):
        raise ValueError("当前材料不支持作为 AI 写信参考材料")
    return material


async def _validate_attachment_material_ids(
    session: AsyncSession,
    identity_id: int,
    material_ids: list[int],
) -> None:
    if not material_ids:
        return
    unique_ids = set(material_ids)
    found = set(
        await session.scalars(
            select(IdentityMaterial.id).where(
                IdentityMaterial.identity_id == identity_id,
                IdentityMaterial.id.in_(unique_ids),
            ),
        ),
    )
    if found != unique_ids:
        raise ValueError("存在不属于当前身份的随信附件")


def _normalize_manual_body(body_text: str, body_html: str | None) -> tuple[str, str | None]:
    normalized_text = body_text.strip()
    normalized_html = (body_html or "").strip()
    if normalized_html:
        rendered = normalize_email_html(normalized_html)
        normalized_html = rendered.html
        if not normalized_text:
            normalized_text = rendered.text
    if not normalized_text:
        raise ValueError("草稿正文不能为空")
    return normalized_text, normalized_html or None


def _normalize_nullable_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


async def _load_task(session: AsyncSession, task_id: int) -> EmailTask | None:
    return await session.scalar(
        select(EmailTask)
        .options(
            selectinload(EmailTask.professor),
            selectinload(EmailTask.identity),
            selectinload(EmailTask.primary_material),
            selectinload(EmailTask.outreach_template),
        )
        .where(EmailTask.id == task_id),
    )
