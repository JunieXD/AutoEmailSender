from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.identity_serializers import serialize_material
from app.models import (
    EmailDirection,
    EmailLog,
    EmailTask,
    EmailTaskCancellationReason,
    EmailTaskStatus,
    IdentityProfile,
    LLMProfile,
    Professor,
)
from app.schemas.workspace import (
    WorkspaceIdentityRead,
    WorkspaceLLMRead,
    WorkspaceMessageRead,
    WorkspaceProfessorRead,
    WorkspaceTaskSummaryRead,
    WorkspaceThreadRead,
)
from app.services import llm_runtime
from app.services.mail_runtime import strip_quoted_reply_html, strip_quoted_reply_text
from app.services.operation_logs import record_operation_log
from app.services.outreach_templates import (
    OUTREACH_GENERATION_MODE_TEMPLATE,
    RenderedOutreachTemplate,
    get_identity_sender_name,
    render_outreach_template,
    resolve_outreach_template_config,
)
from app.services.task_runtime import _create_manual_child_task


async def build_workspace_thread(
    session: AsyncSession,
    *,
    professor_id: int,
    identity_id: int,
    llm_profile_id: int,
) -> WorkspaceThreadRead:
    professor = await _get_professor(session, professor_id)
    identity = await _get_identity(session, identity_id)
    llm_profile = await _get_llm_profile(session, llm_profile_id)
    current_task = await _get_latest_email_task(session, professor_id, identity_id, llm_profile_id)
    if _recover_legacy_sent_task_status(current_task):
        await session.commit()
        await session.refresh(current_task)
    match_task = (
        current_task
        if _task_has_match_result(current_task)
        else await _get_latest_identity_match_task(session, professor_id, identity_id)
    )
    current_task_outreach = (
        _resolve_task_outreach_config(identity, current_task)
        if current_task is not None
        else resolve_outreach_template_config(identity)
    )
    rendered_template = _render_workspace_template_summary(
        identity,
        professor,
        current_task_outreach,
    )

    message_filters = [
        EmailLog.professor_id == professor_id,
        EmailLog.identity_id == identity_id,
    ]
    if current_task is not None:
        message_filters.append(
            or_(
                EmailLog.direction != EmailDirection.DRAFT.value,
                EmailLog.email_task_id == current_task.id,
            ),
        )
    else:
        message_filters.append(EmailLog.direction != EmailDirection.DRAFT.value)

    logs = list(
        (
            await session.execute(
                select(EmailLog)
                .where(*message_filters)
                .order_by(EmailLog.created_at.asc(), EmailLog.id.asc()),
            )
        ).scalars()
    )
    latest_draft_log = next(
        (
            log
            for log in reversed(logs)
            if log.direction == "draft" and current_task and log.email_task_id == current_task.id
        ),
        None,
    )
    latest_draft_usage = _extract_usage(latest_draft_log.provider_payload if latest_draft_log else None)

    token_estimate = None
    if (
        current_task is not None
        and current_task.primary_material is not None
        and current_task_outreach.generation_mode == "llm"
        and llm_runtime.resolve_template_text(
            current_task_outreach.body_text_template,
            current_task_outreach.body_html_template,
        )
    ):
        resolved_body_template = llm_runtime.resolve_template_text(
            current_task_outreach.body_text_template,
            current_task_outreach.body_html_template,
        )
        token_estimate = llm_runtime.estimate_draft_content_tokens(
            identity=identity,
            primary_material=current_task.primary_material,
            llm_profile=llm_profile,
            professor=professor,
            available_materials=list(identity.materials),
            custom_subject=current_task_outreach.subject_template,
            custom_body=resolved_body_template,
            custom_body_html=current_task_outreach.body_html_template,
        )

    return WorkspaceThreadRead(
        professor=WorkspaceProfessorRead(
            id=professor.id,
            name=professor.name,
            email=professor.email,
            title=professor.title,
            university=professor.university,
            school=professor.school,
            research_direction=professor.research_direction,
            recent_papers=professor.recent_papers or [],
        ),
        identity=WorkspaceIdentityRead(
            id=identity.id,
            name=identity.profile_name or identity.name,
            profile_name=identity.profile_name or identity.name,
            sender_name=get_identity_sender_name(identity),
            email_address=identity.email_address,
        ),
        llm_profile=WorkspaceLLMRead(
            id=llm_profile.id,
            name=llm_profile.name,
            provider=llm_profile.provider,
            model_name=llm_profile.model_name,
        ),
        material_options=[
            serialize_material(material, identity.current_primary_material_id)
            for material in sorted(identity.materials, key=lambda item: item.created_at, reverse=True)
        ],
        current_task=WorkspaceTaskSummaryRead(
            id=current_task.id if current_task else None,
            source=current_task.source if current_task else None,
            batch_task_id=current_task.batch_task_id if current_task else None,
            parent_task_id=current_task.parent_task_id if current_task else None,
            status=current_task.status if current_task else None,
            cancellation_reason=current_task.cancellation_reason if current_task else None,
            can_continue_manually=_can_continue_manually(current_task),
            can_write_follow_up=_can_write_follow_up(current_task),
            outreach_generation_mode=current_task_outreach.generation_mode,
            outreach_template_subject=current_task_outreach.subject_template,
            outreach_template_body_text=current_task_outreach.body_text_template,
            outreach_template_body_html=current_task_outreach.body_html_template,
            rendered_template_subject=rendered_template.subject if rendered_template else None,
            rendered_template_body_text=rendered_template.body_text if rendered_template else None,
            rendered_template_body_html=rendered_template.body_html if rendered_template else None,
            match_score=match_task.match_score if match_task else None,
            match_reason=match_task.match_reason if match_task else None,
            fit_points=(match_task.fit_points or []) if match_task else [],
            risk_points=(match_task.risk_points or []) if match_task else [],
            match_keywords=(match_task.match_keywords or []) if match_task else [],
            generated_subject=current_task.generated_subject if current_task else None,
            generated_content_text=current_task.generated_content_text if current_task else None,
            generated_content_html=current_task.generated_content_html if current_task else None,
            approved_subject=current_task.approved_subject if current_task else None,
            approved_body_text=current_task.approved_body_text if current_task else None,
            approved_body_html=current_task.approved_body_html if current_task else None,
            primary_material_id=current_task.primary_material_id if current_task else None,
            primary_material=(
                serialize_material(current_task.primary_material, identity.current_primary_material_id)
                if current_task and current_task.primary_material is not None
                else None
            ),
            selected_material_ids=current_task.selected_material_ids if current_task else None,
            approved_at=current_task.approved_at if current_task else None,
            scheduled_at=current_task.scheduled_at if current_task else None,
            last_send_attempt_at=current_task.last_send_attempt_at if current_task else None,
            sent_at=current_task.sent_at if current_task else None,
            last_rfc_message_id=current_task.last_rfc_message_id if current_task else None,
            retry_count=current_task.retry_count if current_task else 0,
            last_error=current_task.last_error if current_task else None,
            is_replied=current_task.is_replied if current_task else False,
            estimated_prompt_tokens=(
                token_estimate.estimated_prompt_tokens if token_estimate is not None else None
            ),
            estimated_completion_tokens_upper_bound=(
                token_estimate.estimated_completion_tokens_upper_bound if token_estimate is not None else None
            ),
            estimated_total_tokens_upper_bound=(
                token_estimate.estimated_total_tokens_upper_bound if token_estimate is not None else None
            ),
            last_draft_prompt_tokens=latest_draft_usage.get("prompt_tokens"),
            last_draft_completion_tokens=latest_draft_usage.get("completion_tokens"),
            last_draft_total_tokens=latest_draft_usage.get("total_tokens"),
        ),
        messages=[_serialize_workspace_message(log) for log in logs],
    )


async def ensure_workspace_task(
    session: AsyncSession,
    *,
    professor_id: int,
    identity_id: int,
    llm_profile_id: int,
) -> EmailTask:
    professor = await _get_professor(session, professor_id)
    identity = await _get_identity(session, identity_id)
    await _get_llm_profile(session, llm_profile_id)
    professor_pk = professor.id
    identity_pk = identity.id

    current_task = await _get_latest_email_task(session, professor_pk, identity_pk, llm_profile_id)
    if current_task is not None:
        if _should_resume_workspace_task(current_task):
            return await _create_workspace_resume_task(session, current_task)
        if not _task_has_match_result(current_task):
            match_task = await _get_latest_identity_match_task(
                session,
                professor_pk,
                identity_pk,
                exclude_task_id=current_task.id,
            )
            if match_task is not None:
                _copy_match_snapshot(current_task, match_task)
                current_task.updated_at = datetime.now(UTC)
                await session.commit()
                await session.refresh(current_task)
        return current_task

    snapshot = resolve_outreach_template_config(identity)
    match_task = await _get_latest_identity_match_task(session, professor_pk, identity_pk)
    task = EmailTask(
        source="manual",
        batch_task_id=None,
        identity_id=identity_pk,
        llm_profile_id=llm_profile_id,
        professor_id=professor_pk,
        primary_material_id=identity.current_primary_material_id,
        outreach_generation_mode=snapshot.generation_mode,
        outreach_template_subject=_normalize_nullable_text(snapshot.subject_template),
        outreach_template_body_text=_normalize_nullable_text(snapshot.body_text_template),
        outreach_template_body_html=_normalize_nullable_text(snapshot.body_html_template),
        status=(
            EmailTaskStatus.MATCHED.value
            if match_task is not None
            else EmailTaskStatus.DISCOVERED.value
        ),
        match_score=match_task.match_score if match_task else None,
        match_reason=match_task.match_reason if match_task else None,
        fit_points=list(match_task.fit_points or []) if match_task else None,
        risk_points=list(match_task.risk_points or []) if match_task else None,
        match_keywords=list(match_task.match_keywords or []) if match_task else None,
        selected_material_ids=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(task)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing_task = await _get_latest_email_task(session, professor_pk, identity_pk, llm_profile_id)
        if existing_task is not None:
            return existing_task
        raise
    await record_operation_log(
        session,
        category="email",
        event_name="email_task.created",
        entity_type="email_task",
        entity_id=str(task.id),
        metadata={
            "source": task.source,
            "professor_id": task.professor_id,
            "identity_id": task.identity_id,
            "llm_profile_id": task.llm_profile_id,
            "primary_material_id": task.primary_material_id,
        },
    )
    await session.commit()
    await session.refresh(task)
    return task

async def _create_workspace_resume_task(
    session: AsyncSession,
    task: EmailTask,
) -> EmailTask:
    professor_id = task.professor_id
    identity_id = task.identity_id
    llm_profile_id = task.llm_profile_id
    resumed_task = _create_manual_child_task(task, reuse_existing_draft=True)
    session.add(resumed_task)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        existing_task = await _get_latest_email_task(session, professor_id, identity_id, llm_profile_id)
        if existing_task is not None:
            return existing_task
        raise ValueError("该工作区已经存在可继续编辑的手动任务") from exc

    await record_operation_log(
        session,
        category="email",
        event_name="email_task.continued_manually",
        entity_type="email_task",
        entity_id=str(resumed_task.id),
        metadata={
            "parent_task_id": task.id,
            "workspace_resume_reason": task.cancellation_reason,
        },
    )
    await session.commit()
    await session.refresh(resumed_task)
    return resumed_task


async def _get_professor(session: AsyncSession, professor_id: int) -> Professor:
    professor = await session.scalar(
        select(Professor).where(
            Professor.id == professor_id,
            Professor.archived_at.is_(None),
        ),
    )
    if not professor:
        raise HTTPException(status_code=404, detail="未找到导师或该导师已被移入回收站")
    return professor


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
        raise HTTPException(status_code=404, detail="未找到身份配置")
    return identity


async def _get_llm_profile(session: AsyncSession, llm_profile_id: int) -> LLMProfile:
    profile = await session.get(LLMProfile, llm_profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="未找到 LLM 配置")
    return profile


async def _get_latest_email_task(
    session: AsyncSession,
    professor_id: int,
    identity_id: int,
    llm_profile_id: int,
) -> EmailTask | None:
    return await session.scalar(
        select(EmailTask)
        .options(
            selectinload(EmailTask.primary_material),
            selectinload(EmailTask.batch_task),
        )
        .where(
            EmailTask.professor_id == professor_id,
            EmailTask.identity_id == identity_id,
            EmailTask.llm_profile_id == llm_profile_id,
        )
        .order_by(EmailTask.created_at.desc(), EmailTask.id.desc()),
    )


async def _get_latest_identity_match_task(
    session: AsyncSession,
    professor_id: int,
    identity_id: int,
    *,
    exclude_task_id: int | None = None,
) -> EmailTask | None:
    statement = (
        select(EmailTask)
        .where(
            EmailTask.professor_id == professor_id,
            EmailTask.identity_id == identity_id,
            EmailTask.match_score.is_not(None),
        )
        .order_by(EmailTask.updated_at.desc(), EmailTask.created_at.desc(), EmailTask.id.desc())
    )
    if exclude_task_id is not None:
        statement = statement.where(EmailTask.id != exclude_task_id)
    return await session.scalar(statement)


def _task_has_match_result(task: EmailTask | None) -> bool:
    return task is not None and task.match_score is not None


def _copy_match_snapshot(target: EmailTask, source: EmailTask) -> None:
    target.match_score = source.match_score
    target.match_reason = source.match_reason
    target.fit_points = list(source.fit_points or [])
    target.risk_points = list(source.risk_points or [])
    target.match_keywords = list(source.match_keywords or [])
    if target.status == EmailTaskStatus.DISCOVERED.value:
        target.status = EmailTaskStatus.MATCHED.value


def _recover_legacy_sent_task_status(task: EmailTask | None) -> bool:
    if (
        task is None
        or task.sent_at is None
        or task.status
        not in {
            EmailTaskStatus.DISCOVERED.value,
            EmailTaskStatus.MATCHED.value,
            EmailTaskStatus.DRAFT_FAILED.value,
            EmailTaskStatus.REVIEW_REQUIRED.value,
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
        }
    ):
        return False

    task.status = EmailTaskStatus.SENT.value
    task.last_error = None
    task.updated_at = datetime.now(UTC)
    return True


def _extract_usage(provider_payload: dict[str, object] | None) -> dict[str, int | None]:
    if not provider_payload:
        return {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }

    raw_usage = provider_payload.get("usage")
    if not isinstance(raw_usage, dict):
        return {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }

    return {
        "prompt_tokens": raw_usage.get("prompt_tokens") if isinstance(raw_usage.get("prompt_tokens"), int) else None,
        "completion_tokens": (
            raw_usage.get("completion_tokens")
            if isinstance(raw_usage.get("completion_tokens"), int)
            else None
        ),
        "total_tokens": raw_usage.get("total_tokens") if isinstance(raw_usage.get("total_tokens"), int) else None,
    }


def _serialize_workspace_message(log: EmailLog) -> WorkspaceMessageRead:
    usage = _extract_usage(log.provider_payload)
    is_received = log.direction == "received"
    return WorkspaceMessageRead(
        id=log.id,
        direction=log.direction,
        subject=log.subject,
        content=strip_quoted_reply_text(log.content) if is_received else log.content,
        content_html=(
            strip_quoted_reply_html(log.content_html)
            if is_received and log.content_html
            else log.content_html
        ),
        rfc_message_id=log.rfc_message_id,
        failure_summary=log.failure_summary,
        reply_headers=log.reply_headers,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
        created_at=log.created_at,
    )


def _render_workspace_template_summary(
    identity: IdentityProfile,
    professor: Professor,
    outreach_config,
) -> RenderedOutreachTemplate | None:
    if outreach_config.generation_mode != OUTREACH_GENERATION_MODE_TEMPLATE:
        return None

    try:
        return render_outreach_template(
            identity,
            professor,
            subject_template=outreach_config.subject_template,
            body_text_template=outreach_config.body_text_template,
            body_html_template=outreach_config.body_html_template,
        )
    except ValueError:
        return None


def _resolve_task_outreach_config(identity: IdentityProfile, task: EmailTask):
    if task.batch_task_id is None:
        return resolve_outreach_template_config(
            identity,
            generation_mode=task.outreach_generation_mode,
        )

    return resolve_outreach_template_config(
        identity,
        generation_mode=task.outreach_generation_mode,
        subject_template=task.outreach_template_subject,
        body_text_template=task.outreach_template_body_text,
        body_html_template=task.outreach_template_body_html,
    )


def _normalize_nullable_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _can_continue_manually(task: EmailTask | None) -> bool:
    return bool(
        task is not None
        and task.status == EmailTaskStatus.CANCELED.value
        and task.cancellation_reason == EmailTaskCancellationReason.BATCH_STOPPED.value
    )


def _can_write_follow_up(task: EmailTask | None) -> bool:
    return bool(
        task is not None
        and task.status in {
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }
    )

def _should_resume_workspace_task(task: EmailTask | None) -> bool:
    return bool(
        task is not None
        and task.status == EmailTaskStatus.CANCELED.value
        and task.cancellation_reason == EmailTaskCancellationReason.SCHEDULE_EXPIRED.value
    )
