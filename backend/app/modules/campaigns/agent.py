from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.agent_api_errors import AgentApiError
from app.core.query_chunks import chunked_values, unique_positive_ids
from app.core.time import as_utc_aware, local_now, serialize_api_datetime, utc_now
from app.models import (
    BatchTask,
    BatchTaskStatus,
    EmailTask,
    EmailTaskCancellationReason,
    EmailTaskSource,
    EmailTaskStatus,
    IdentityMaterial,
    IdentityProfile,
    LLMProfile,
    OutreachTemplate,
    Professor,
)
from app.schemas.agent import (
    AgentCampaignCreateRequest,
    AgentCampaignItemRead,
    AgentCampaignRead,
    AgentCampaignSendRequest,
)
from .scheduling import (
    build_jittered_batch_schedule,
    has_future_batch_window,
    normalize_scheduled_dates,
)
from .drafts.fallback import (
    build_initial_batch_draft,
    professor_has_research_direction,
)
from .status import sync_batch_task_completion
from app.modules.identities.public import material_can_be_primary
from app.services.operation_logs import record_operation_log
from .templates.library import (
    get_default_outreach_template_for_identity,
    get_outreach_template,
)
from .templates.rendering import (
    OUTREACH_GENERATION_MODE_LLM,
    OUTREACH_GENERATION_MODE_TEMPLATE,
    OutreachTemplateConfig,
    get_outreach_template_defaults_validation_error,
    resolve_outreach_template_config,
)
from app.services.rich_text import normalize_email_html, text_to_email_html
from app.services.agent_mutations import fingerprint
from app.modules.professors.public import is_valid_professor_email


CAMPAIGN_CREATE_SNAPSHOT_VERSION = "1"
CAMPAIGN_SEND_SNAPSHOT_VERSION = "1"
CAMPAIGN_RESUME_SNAPSHOT_VERSION = "1"
CAMPAIGN_RESTORE_SEND_SNAPSHOT_VERSION = "1"
CAMPAIGN_ALLOWED_ACTIVE_STATUSES = {
    BatchTaskStatus.PAUSED.value,
    BatchTaskStatus.RUNNING.value,
}
CAMPAIGN_DISPATCHABLE_STATUSES = {
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
    EmailTaskStatus.SENDING.value,
}
CAMPAIGN_DELETABLE_STATUSES = {
    BatchTaskStatus.STOPPED.value,
    BatchTaskStatus.COMPLETED.value,
    BatchTaskStatus.EXPIRED.value,
}
CAMPAIGN_ITEM_REMOVABLE_STATUSES = {
    EmailTaskStatus.DISCOVERED.value,
    EmailTaskStatus.MATCHED.value,
    EmailTaskStatus.DRAFT_FAILED.value,
    EmailTaskStatus.REVIEW_REQUIRED.value,
}
CAMPAIGN_ITEM_SEND_CANCELLABLE_STATUSES = {
    EmailTaskStatus.DISCOVERED.value,
    EmailTaskStatus.MATCHED.value,
    EmailTaskStatus.GENERATING_DRAFT.value,
    EmailTaskStatus.DRAFT_FAILED.value,
    EmailTaskStatus.REVIEW_REQUIRED.value,
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
}


@dataclass(slots=True)
class CampaignCreateContext:
    payload: AgentCampaignCreateRequest
    identity: IdentityProfile
    llm_profile: LLMProfile
    professors: list[Professor]
    selected_template: OutreachTemplate | None
    primary_material: IdentityMaterial | None
    attachment_materials: list[IdentityMaterial]
    outreach_config: OutreachTemplateConfig
    scheduled_dates: list[str]


@dataclass(slots=True)
class FinalCampaignDraft:
    task: EmailTask
    subject: str
    body_text: str
    body_html: str | None
    attachment_material_ids: list[int]


@dataclass(slots=True)
class CampaignSendContext:
    campaign: BatchTask
    selected_tasks: list[EmailTask]
    final_drafts: list[FinalCampaignDraft]
    scheduled_at_values: list[datetime | None]


@dataclass(slots=True)
class CampaignResumeContext:
    campaign: BatchTask
    delivery_drafts: list[FinalCampaignDraft]


@dataclass(slots=True)
class CampaignRestoreSendContext:
    campaign: BatchTask
    final_draft: FinalCampaignDraft


@dataclass(frozen=True, slots=True)
class CampaignTaskSummary:
    counts: Counter[str]
    canceled_send_count: int
    has_dispatchable: bool
    has_ai_pending: bool


async def list_agent_campaigns(
    session: AsyncSession,
    *,
    view: str,
    identity_id: int | None,
    status: str | None,
    cursor: int,
    limit: int,
) -> tuple[list[AgentCampaignRead], str | None, bool]:
    statement = (
        select(BatchTask)
        .options(
            selectinload(BatchTask.identity),
            selectinload(BatchTask.llm_profile),
            selectinload(BatchTask.primary_material),
        )
        .order_by(BatchTask.created_at.desc(), BatchTask.id.desc())
    )
    if view == "current":
        statement = statement.where(BatchTask.deleted_at.is_(None))
    elif view == "trash":
        statement = statement.where(BatchTask.deleted_at.is_not(None))
    else:
        raise AgentApiError(
            status_code=400,
            code="INVALID_CAMPAIGN_VIEW",
            message="活动视图只能是 current 或 trash。",
        )
    if identity_id is not None:
        statement = statement.where(BatchTask.identity_id == identity_id)
    if status is not None:
        statement = statement.where(BatchTask.status == status)
    campaigns = list(
        (await session.scalars(statement.offset(cursor).limit(limit + 1))).unique()
    )
    has_more = len(campaigns) > limit
    page = campaigns[:limit]
    summaries = await _campaign_task_summaries(
        session,
        [campaign.id for campaign in page],
    )
    next_cursor = str(cursor + len(page)) if has_more else None
    return (
        [
            _serialize_campaign(campaign, task_summary=summaries[campaign.id])
            for campaign in page
        ],
        next_cursor,
        has_more,
    )


async def get_agent_campaign(
    session: AsyncSession,
    campaign_id: int,
) -> AgentCampaignRead:
    campaign = await _load_campaign_or_raise(session, campaign_id)
    return _serialize_campaign(campaign)


async def list_agent_campaign_items(
    session: AsyncSession,
    campaign_id: int,
    *,
    cursor: int,
    limit: int,
) -> tuple[list[AgentCampaignItemRead], str | None, bool]:
    campaign = await _load_campaign_or_raise(session, campaign_id)
    items = list(
        await session.scalars(
            select(EmailTask)
            .options(
                selectinload(EmailTask.professor),
                selectinload(EmailTask.primary_material),
            )
            .where(
                EmailTask.batch_task_id == campaign_id,
                EmailTask.source == EmailTaskSource.BATCH.value,
                ~(
                    (EmailTask.status == EmailTaskStatus.CANCELED.value)
                    & (
                        EmailTask.cancellation_reason
                        == EmailTaskCancellationReason.USER_REMOVED.value
                    )
                ),
            )
            .order_by(EmailTask.id.asc())
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    has_more = len(items) > limit
    page = items[:limit]
    next_cursor = str(cursor + len(page)) if has_more else None
    return (
        [_serialize_campaign_item(item, campaign=campaign) for item in page],
        next_cursor,
        has_more,
    )


async def start_agent_campaign_draft_generation(
    session: AsyncSession,
    campaign_id: int,
) -> AgentCampaignRead:
    campaign = await _load_campaign_or_raise(session, campaign_id)
    _ensure_campaign_active_for_agent(campaign)
    dispatchable = [
        task.id
        for task in campaign.email_tasks
        if task.status in CAMPAIGN_DISPATCHABLE_STATUSES
        and task.batch_send_canceled_at is None
    ]
    if dispatchable:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_START_UNSAFE",
            message="活动含有已获准或正在发送的邮件，不能通过 Agent 恢复草稿生成。",
            details={"dispatchable_item_ids": dispatchable},
        )
    pending_llm_items = [
        task
        for task in campaign.email_tasks
        if _task_uses_ai_rewrite(task)
        and task.status
        in {
            EmailTaskStatus.DISCOVERED.value,
            EmailTaskStatus.MATCHED.value,
            EmailTaskStatus.DRAFT_FAILED.value,
        }
        and task.batch_send_canceled_at is None
    ]
    if not pending_llm_items:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_NO_PENDING_DRAFTS",
            message="该活动没有可启动的 AI 草稿任务。",
        )
    campaign.status = BatchTaskStatus.RUNNING.value
    campaign.updated_at = utc_now()
    await record_operation_log(
        session,
        category="email",
        event_name="agent_cli.campaign.draft_generation_started",
        entity_type="batch_task",
        entity_id=str(campaign.id),
        metadata={
            "actor": "agent_cli",
            "pending_llm_draft_count": len(pending_llm_items),
            "never_sends": True,
        },
    )
    return _serialize_campaign(campaign)


async def pause_agent_campaign(
    session: AsyncSession,
    campaign_id: int,
) -> AgentCampaignRead:
    campaign = await _load_campaign_or_raise(session, campaign_id)
    _ensure_campaign_active_for_agent(campaign)
    now = utc_now()
    if campaign.status != BatchTaskStatus.PAUSED.value:
        campaign.status = BatchTaskStatus.PAUSED.value
        campaign.updated_at = now
    for task in campaign.email_tasks:
        if _is_user_removed_campaign_item(task):
            continue
        if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
            task.status = (
                task.draft_generation_previous_status
                or EmailTaskStatus.DISCOVERED.value
            )
            task.draft_generation_previous_status = None
            task.draft_generation_started_at = None
            task.draft_claim_id = None
            task.draft_claimed_at = None
            task.draft_lease_expires_at = None
            task.updated_at = now
    if campaign.status == BatchTaskStatus.PAUSED.value:
        await record_operation_log(
            session,
            category="email",
            event_name="agent_cli.campaign.paused",
            entity_type="batch_task",
            entity_id=str(campaign.id),
            metadata={"actor": "agent_cli"},
        )
    return _serialize_campaign(campaign)


async def stop_agent_campaign(
    session: AsyncSession,
    campaign_id: int,
) -> AgentCampaignRead:
    """Stop a campaign and cancel every delivery that has not started yet."""

    campaign = await _load_campaign_or_raise(session, campaign_id)
    _ensure_campaign_not_in_trash(campaign)
    now = utc_now()
    campaign.status = BatchTaskStatus.STOPPED.value
    campaign.updated_at = now
    for task in campaign.email_tasks:
        if _is_user_removed_campaign_item(task):
            continue
        if task.status in {
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
            EmailTaskStatus.SEND_FAILED.value,
        }:
            continue
        task.status = EmailTaskStatus.CANCELED.value
        task.cancellation_reason = EmailTaskCancellationReason.BATCH_STOPPED.value
        task.draft_generation_previous_status = None
        task.draft_generation_started_at = None
        task.draft_claim_id = None
        task.draft_claimed_at = None
        task.draft_lease_expires_at = None
        task.updated_at = now
    await _record_campaign_action(
        session,
        campaign,
        "agent_cli.campaign.stopped",
    )
    return _serialize_campaign(campaign)


async def archive_agent_campaign(
    session: AsyncSession,
    campaign_id: int,
) -> AgentCampaignRead:
    campaign = await _load_campaign_or_raise(session, campaign_id)
    if campaign.deleted_at is not None:
        return _serialize_campaign(campaign)
    sync_batch_task_completion(campaign)
    if campaign.status not in CAMPAIGN_DELETABLE_STATUSES:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_NOT_ARCHIVABLE",
            message="请先停止或完成批量活动后再移入回收站。",
            details={"campaign_id": campaign.id, "status": campaign.status},
        )
    now = utc_now()
    campaign.deleted_at = now
    campaign.updated_at = now
    await _record_campaign_action(
        session,
        campaign,
        "agent_cli.campaign.archived",
    )
    return _serialize_campaign(campaign)


async def restore_agent_campaign(
    session: AsyncSession,
    campaign_id: int,
) -> AgentCampaignRead:
    campaign = await _load_campaign_or_raise(session, campaign_id)
    if campaign.deleted_at is None:
        return _serialize_campaign(campaign)
    await _sanitize_campaign_material_references_before_restore(session, campaign)
    campaign.deleted_at = None
    campaign.updated_at = utc_now()
    await _record_campaign_action(
        session,
        campaign,
        "agent_cli.campaign.restored",
    )
    return _serialize_campaign(campaign)


async def remove_agent_campaign_item(
    session: AsyncSession,
    campaign_id: int,
    item_id: int,
) -> AgentCampaignRead:
    campaign = await _load_campaign_or_raise(session, campaign_id)
    _ensure_campaign_not_in_trash(campaign)
    item = _find_visible_campaign_item(campaign, item_id)
    resolved_campaign_id = campaign.id
    resolved_item_id = item.id
    now = utc_now()
    result = await session.execute(
        update(EmailTask)
        .where(
            EmailTask.id == item.id,
            EmailTask.batch_task_id == campaign.id,
            EmailTask.source == EmailTaskSource.BATCH.value,
            EmailTask.batch_send_canceled_at.is_(None),
            EmailTask.status.in_(CAMPAIGN_ITEM_REMOVABLE_STATUSES),
        )
        .values(
            status=EmailTaskStatus.CANCELED.value,
            cancellation_reason=EmailTaskCancellationReason.USER_REMOVED.value,
            scheduled_at=None,
            draft_generation_previous_status=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False),
    )
    if result.rowcount != 1:
        await session.rollback()
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_ITEM_NOT_REMOVABLE",
            message="活动项状态已发生变化，不能从批量活动中移除。",
            details={"campaign_id": campaign.id, "item_id": item.id},
        )
    await session.execute(
        update(BatchTask)
        .where(BatchTask.id == campaign.id)
        .values(
            target_count=case(
                (BatchTask.target_count > 0, BatchTask.target_count - 1),
                else_=0,
            ),
            status=case(
                (BatchTask.target_count <= 1, BatchTaskStatus.COMPLETED.value),
                else_=BatchTask.status,
            ),
            updated_at=now,
        )
        .execution_options(synchronize_session=False),
    )
    session.expire_all()
    refreshed = await _load_campaign_or_raise(session, resolved_campaign_id)
    sync_batch_task_completion(refreshed, now=now)
    await _record_campaign_action(
        session,
        refreshed,
        "agent_cli.campaign.item_removed",
        metadata={"item_id": resolved_item_id},
    )
    return _serialize_campaign(refreshed)


async def cancel_agent_campaign_item_send(
    session: AsyncSession,
    campaign_id: int,
    item_id: int,
) -> AgentCampaignRead:
    campaign = await _load_campaign_or_raise(session, campaign_id)
    _ensure_campaign_not_in_trash(campaign)
    item = _find_visible_campaign_item(campaign, item_id)
    resolved_campaign_id = campaign.id
    resolved_item_id = item.id
    _ensure_campaign_item_send_action_allowed(campaign, item)
    if item.batch_send_canceled_at is not None:
        return _serialize_campaign(campaign)
    now = utc_now()
    result = await session.execute(
        update(EmailTask)
        .where(
            EmailTask.id == item.id,
            EmailTask.batch_task_id == campaign.id,
            EmailTask.source == EmailTaskSource.BATCH.value,
            EmailTask.batch_send_canceled_at.is_(None),
            EmailTask.status.in_(CAMPAIGN_ITEM_SEND_CANCELLABLE_STATUSES),
        )
        .values(batch_send_canceled_at=now, updated_at=now)
        .execution_options(synchronize_session=False),
    )
    if result.rowcount != 1:
        await session.rollback()
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_ITEM_SEND_NOT_CANCELLABLE",
            message="活动项已进入发送流程或状态已变化，不能取消发送。",
            details={"campaign_id": campaign.id, "item_id": item.id},
        )
    await session.execute(
        update(BatchTask)
        .where(BatchTask.id == campaign.id)
        .values(updated_at=now)
        .execution_options(synchronize_session=False),
    )
    session.expire_all()
    refreshed = await _load_campaign_or_raise(session, resolved_campaign_id)
    sync_batch_task_completion(refreshed, now=now)
    await _record_campaign_action(
        session,
        refreshed,
        "agent_cli.campaign.item_send_canceled",
        metadata={"item_id": resolved_item_id},
    )
    return _serialize_campaign(refreshed)


async def retry_agent_campaign_item_draft(
    session: AsyncSession,
    campaign_id: int,
    item_id: int,
) -> AgentCampaignRead:
    campaign = await _load_campaign_or_raise(session, campaign_id)
    _ensure_campaign_active_for_agent(campaign)
    if campaign.status != BatchTaskStatus.RUNNING.value:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_NOT_RUNNING",
            message="批量活动未运行，不能重新生成失败草稿。",
        )
    item = _find_visible_campaign_item(campaign, item_id)
    if item.batch_send_canceled_at is not None:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_ITEM_SEND_CANCELED",
            message="该活动项已取消发送，请先重新确认其发送计划。",
        )
    if item.status != EmailTaskStatus.DRAFT_FAILED.value:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_ITEM_NOT_DRAFT_FAILED",
            message="只有草稿生成失败的活动项可以重试。",
            details={"item_id": item.id, "status": item.status},
        )
    if not _task_uses_ai_rewrite(item):
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_ITEM_RETRY_UNSUPPORTED",
            message="模板模式的草稿失败不能加入 AI 重试队列。",
        )
    if item.primary_material is None:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_ITEM_REFERENCE_MATERIAL_REQUIRED",
            message="请先为该活动项选择 AI 写信参考材料，再重试草稿。",
        )
    if item.professor is None or not (item.professor.research_direction or "").strip():
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_ITEM_RESEARCH_DIRECTION_REQUIRED",
            message="请先补充导师研究方向，再使用 AI 重新生成草稿。",
        )
    item.status = EmailTaskStatus.DISCOVERED.value
    item.last_error = None
    item.draft_generation_previous_status = None
    item.draft_generation_started_at = None
    item.draft_claim_id = None
    item.draft_claimed_at = None
    item.draft_lease_expires_at = None
    item.updated_at = utc_now()
    await _record_campaign_action(
        session,
        campaign,
        "agent_cli.campaign.item_draft_retry_requested",
        metadata={"item_id": item.id},
    )
    return _serialize_campaign(campaign)


async def prepare_campaign_resume_snapshot(
    session: AsyncSession,
    campaign_id: int,
) -> dict[str, object]:
    context = await _resolve_campaign_resume_context(session, campaign_id)
    return _build_campaign_resume_snapshot(context)


async def execute_campaign_resume_snapshot(
    session: AsyncSession,
    snapshot: dict[str, object],
) -> dict[str, object]:
    campaign_id = _campaign_resume_request_from_snapshot(snapshot)
    expected_fingerprint = snapshot.get("campaign_resume_fingerprint")
    if not isinstance(expected_fingerprint, str):
        raise _invalid_campaign_snapshot_error()
    context = await _resolve_campaign_resume_context(session, campaign_id)
    current_snapshot = _build_campaign_resume_snapshot(context)
    if expected_fingerprint != _campaign_resume_snapshot_fingerprint(current_snapshot):
        raise _campaign_resume_plan_stale_error(campaign_id)

    now = utc_now()
    context.campaign.status = BatchTaskStatus.RUNNING.value
    context.campaign.updated_at = now
    await _record_campaign_action(
        session,
        context.campaign,
        "agent_cli.campaign.resumed",
        metadata={
            "delivery_item_ids": [draft.task.id for draft in context.delivery_drafts],
            "recipient_count": len(context.delivery_drafts),
            "risk_level": "L3",
        },
    )
    return {
        "outcome": "campaign_resumed",
        "campaign_id": context.campaign.id,
        "campaign_status": context.campaign.status,
        "delivery_item_ids": [draft.task.id for draft in context.delivery_drafts],
        "recipient_count": len(context.delivery_drafts),
    }


async def prepare_campaign_restore_send_snapshot(
    session: AsyncSession,
    campaign_id: int,
    item_id: int,
) -> dict[str, object]:
    context = await _resolve_campaign_restore_send_context(
        session,
        campaign_id,
        item_id,
    )
    return _build_campaign_restore_send_snapshot(context)


async def execute_campaign_restore_send_snapshot(
    session: AsyncSession,
    snapshot: dict[str, object],
) -> dict[str, object]:
    campaign_id, item_id = _campaign_restore_send_request_from_snapshot(snapshot)
    expected_fingerprint = snapshot.get("campaign_restore_send_fingerprint")
    if not isinstance(expected_fingerprint, str):
        raise _invalid_campaign_snapshot_error()
    context = await _resolve_campaign_restore_send_context(
        session,
        campaign_id,
        item_id,
    )
    current_snapshot = _build_campaign_restore_send_snapshot(context)
    if expected_fingerprint != _campaign_restore_send_snapshot_fingerprint(current_snapshot):
        raise _campaign_restore_send_plan_stale_error(campaign_id, item_id)

    now = utc_now()
    context.final_draft.task.batch_send_canceled_at = None
    context.final_draft.task.updated_at = now
    context.campaign.updated_at = now
    await _record_campaign_action(
        session,
        context.campaign,
        "agent_cli.campaign.item_send_restored",
        metadata={
            "item_id": context.final_draft.task.id,
            "scheduled_at": _serialize_optional_datetime(
                context.final_draft.task.scheduled_at,
            ),
            "risk_level": "L3",
        },
    )
    return {
        "outcome": "campaign_item_send_restored",
        "campaign_id": context.campaign.id,
        "item_id": context.final_draft.task.id,
        "scheduled_at": _serialize_optional_datetime(
            context.final_draft.task.scheduled_at,
        ),
        "campaign_status": context.campaign.status,
    }


async def prepare_campaign_create_snapshot(
    session: AsyncSession,
    payload: AgentCampaignCreateRequest,
) -> dict[str, object]:
    context = await _resolve_campaign_create_context(session, payload)
    return _build_campaign_create_snapshot(context)


async def execute_campaign_create_snapshot(
    session: AsyncSession,
    snapshot: dict[str, object],
) -> dict[str, object]:
    payload = _campaign_create_request_from_snapshot(snapshot)
    expected_fingerprint = snapshot.get("campaign_create_fingerprint")
    if not isinstance(expected_fingerprint, str):
        raise _invalid_campaign_snapshot_error()
    context = await _resolve_campaign_create_context(session, payload)
    current_snapshot = _build_campaign_create_snapshot(context)
    if expected_fingerprint != _campaign_create_snapshot_fingerprint(current_snapshot):
        raise _campaign_create_plan_stale_error()

    batch_task = BatchTask(
        identity_id=context.identity.id,
        llm_profile_id=context.llm_profile.id,
        name=payload.name.strip(),
        schedule_type=payload.schedule_type,
        window_start_time=payload.window_start_time,
        window_end_time=payload.window_end_time,
        emails_per_window=payload.emails_per_window,
        scheduled_dates=context.scheduled_dates or None,
        status=BatchTaskStatus.PAUSED.value,
        primary_material_id=(
            context.primary_material.id if context.primary_material is not None else None
        ),
        outreach_template_id=(
            context.selected_template.id if context.selected_template is not None else None
        ),
        outreach_template_name_snapshot=(
            context.selected_template.name if context.selected_template is not None else None
        ),
        outreach_template_snapshot_version=1,
        outreach_generation_mode=context.outreach_config.generation_mode,
        outreach_template_subject=_normalize_nullable_text(
            context.outreach_config.subject_template,
        ),
        outreach_template_body_text=_normalize_nullable_text(
            context.outreach_config.body_text_template,
        ),
        outreach_template_body_html=_normalize_nullable_text(
            context.outreach_config.body_html_template,
        ),
        email_subject=_normalize_nullable_text(context.outreach_config.subject_template),
        email_body=_normalize_nullable_text(context.outreach_config.body_text_template),
        selected_material_ids=[material.id for material in context.attachment_materials] or None,
        target_count=len(context.professors),
    )
    session.add(batch_task)
    await session.flush()

    pending_generation_count = 0
    review_required_count = 0
    for professor in context.professors:
        generated_subject = None
        generated_body_text = None
        generated_body_html = None
        draft_generation_source = None
        draft_fallback_reason = None
        task_status = EmailTaskStatus.DISCOVERED.value
        initial_draft = build_initial_batch_draft(
            context.identity,
            professor,
            context.outreach_config,
            primary_material_available=context.primary_material is not None,
        )
        if initial_draft is not None:
            generated_subject = initial_draft.subject
            generated_body_text = initial_draft.body_text
            generated_body_html = initial_draft.body_html
            draft_generation_source = initial_draft.generation_source
            draft_fallback_reason = initial_draft.fallback_reason
            task_status = EmailTaskStatus.REVIEW_REQUIRED.value
            review_required_count += 1
        else:
            pending_generation_count += 1

        session.add(
            EmailTask(
                source=EmailTaskSource.BATCH.value,
                batch_task_id=batch_task.id,
                identity_id=context.identity.id,
                llm_profile_id=context.llm_profile.id,
                professor_id=professor.id,
                primary_material_id=(
                    context.primary_material.id
                    if context.primary_material is not None
                    else None
                ),
                outreach_template_id=(
                    context.selected_template.id
                    if context.selected_template is not None
                    else None
                ),
                outreach_template_snapshot_version=1,
                outreach_generation_mode=context.outreach_config.generation_mode,
                outreach_template_subject=_normalize_nullable_text(
                    context.outreach_config.subject_template,
                ),
                outreach_template_body_text=_normalize_nullable_text(
                    context.outreach_config.body_text_template,
                ),
                outreach_template_body_html=_normalize_nullable_text(
                    context.outreach_config.body_html_template,
                ),
                status=task_status,
                generated_subject=generated_subject,
                generated_content_text=generated_body_text,
                generated_content_html=generated_body_html,
                draft_generation_source=draft_generation_source,
                draft_fallback_reason=draft_fallback_reason,
                selected_material_ids=[material.id for material in context.attachment_materials]
                or None,
            ),
        )

    await session.flush()
    await record_operation_log(
        session,
        category="email",
        event_name="agent_cli.campaign.created",
        entity_type="batch_task",
        entity_id=str(batch_task.id),
        metadata={
            "actor": "agent_cli",
            "target_count": batch_task.target_count,
            "identity_id": batch_task.identity_id,
            "llm_profile_id": batch_task.llm_profile_id,
            "generation_mode": _agent_generation_mode(batch_task.outreach_generation_mode),
            "status": batch_task.status,
            "never_sends": True,
        },
    )
    return {
        "outcome": "campaign_created",
        "campaign_id": batch_task.id,
        "status": batch_task.status,
        "target_count": batch_task.target_count,
        "pending_generation_count": pending_generation_count,
        "review_required_count": review_required_count,
    }


async def prepare_campaign_send_snapshot(
    session: AsyncSession,
    campaign_id: int,
    payload: AgentCampaignSendRequest,
) -> dict[str, object]:
    context = await _resolve_campaign_send_context(session, campaign_id, payload)
    return _build_campaign_send_snapshot(context, payload)


async def execute_campaign_send_snapshot(
    session: AsyncSession,
    snapshot: dict[str, object],
) -> dict[str, object]:
    campaign_id, payload, scheduled_at_values = _campaign_send_request_from_snapshot(snapshot)
    expected_fingerprint = snapshot.get("campaign_send_fingerprint")
    if not isinstance(expected_fingerprint, str):
        raise _invalid_campaign_snapshot_error()
    context = await _resolve_campaign_send_context(
        session,
        campaign_id,
        payload,
        scheduled_at_values=scheduled_at_values,
    )
    current_snapshot = _build_campaign_send_snapshot(context, payload)
    if expected_fingerprint != _campaign_send_snapshot_fingerprint(current_snapshot):
        raise _campaign_send_plan_stale_error(campaign_id)

    now = utc_now()
    context.campaign.status = BatchTaskStatus.RUNNING.value
    context.campaign.updated_at = now
    for final_draft, scheduled_at in zip(
        context.final_drafts,
        context.scheduled_at_values,
        strict=True,
    ):
        task = final_draft.task
        task.approved_subject = final_draft.subject
        task.approved_body_text = final_draft.body_text
        task.approved_body_html = final_draft.body_html
        task.selected_material_ids = final_draft.attachment_material_ids or None
        task.approved_at = now
        task.scheduled_at = scheduled_at
        task.status = (
            EmailTaskStatus.SCHEDULED.value
            if scheduled_at is not None
            else EmailTaskStatus.APPROVED.value
        )
        task.updated_at = now
        task.last_error = None

    await record_operation_log(
        session,
        category="email",
        event_name="agent_cli.campaign.send_authorized",
        entity_type="batch_task",
        entity_id=str(context.campaign.id),
        metadata={
            "actor": "agent_cli",
            "item_ids": [task.id for task in context.selected_tasks],
            "recipient_count": len(context.selected_tasks),
            "schedule_type": context.campaign.schedule_type,
            "risk_level": "L3",
        },
    )
    return {
        "outcome": (
            "campaign_scheduled"
            if context.campaign.schedule_type == "scheduled"
            else "campaign_queued_for_dispatch"
        ),
        "campaign_id": context.campaign.id,
        "campaign_status": context.campaign.status,
        "recipient_count": len(context.selected_tasks),
        "item_ids": [task.id for task in context.selected_tasks],
        "scheduled_at": [
            serialize_api_datetime(value) if value is not None else None
            for value in context.scheduled_at_values
        ],
    }


async def _resolve_campaign_create_context(
    session: AsyncSession,
    payload: AgentCampaignCreateRequest,
) -> CampaignCreateContext:
    name = payload.name.strip()
    if not name:
        raise AgentApiError(
            status_code=422,
            code="CAMPAIGN_NAME_REQUIRED",
            message="活动名称不能为空。",
        )
    scheduled_dates = _validate_campaign_schedule(payload)
    identity = await session.scalar(
        select(IdentityProfile)
        .options(
            selectinload(IdentityProfile.materials),
            selectinload(IdentityProfile.current_primary_material),
        )
        .where(IdentityProfile.id == payload.identity_id),
    )
    if identity is None:
        raise AgentApiError(
            status_code=404,
            code="CAMPAIGN_IDENTITY_NOT_FOUND",
            message="未找到发件身份。",
        )
    llm_profile = await session.get(LLMProfile, payload.llm_profile_id)
    if llm_profile is None:
        raise AgentApiError(
            status_code=404,
            code="CAMPAIGN_LLM_PROFILE_NOT_FOUND",
            message="未找到模型配置。",
        )
    professor_ids = unique_positive_ids(payload.professor_ids)
    professors: list[Professor] = []
    for professor_id_chunk in chunked_values(professor_ids):
        professors.extend(
            await session.scalars(
                select(Professor).where(
                    Professor.id.in_(professor_id_chunk),
                    Professor.archived_at.is_(None),
                ),
            ),
        )
    professors.sort(key=lambda professor: professor.id)
    if len(professors) != len(payload.professor_ids):
        raise AgentApiError(
            status_code=404,
            code="CAMPAIGN_PROFESSORS_NOT_FOUND",
            message="部分导师不存在或已被移入回收站。",
        )

    material_map = {material.id: material for material in identity.materials}
    primary_material: IdentityMaterial | None = None
    if payload.reference_material_id is not None:
        primary_material = material_map.get(payload.reference_material_id)
        if primary_material is None:
            raise AgentApiError(
                status_code=422,
                code="CAMPAIGN_REFERENCE_MATERIAL_INVALID",
                message="AI 写信参考材料不属于当前发件身份。",
            )
        if not material_can_be_primary(primary_material):
            raise AgentApiError(
                status_code=422,
                code="CAMPAIGN_REFERENCE_MATERIAL_INVALID",
                message="当前材料不支持作为 AI 写信参考材料。",
            )
    attachment_materials = [
        material_map[material_id]
        for material_id in payload.attachment_material_ids
        if material_id in material_map
    ]
    if len(attachment_materials) != len(payload.attachment_material_ids):
        raise AgentApiError(
            status_code=422,
            code="CAMPAIGN_ATTACHMENT_MATERIAL_INVALID",
            message="存在不属于当前发件身份的随信附件。",
        )

    selected_template = None
    if payload.template_id is not None:
        try:
            selected_template = await get_outreach_template(session, payload.template_id)
        except ValueError as exc:
            raise AgentApiError(
                status_code=422,
                code="CAMPAIGN_TEMPLATE_INVALID",
                message=str(exc),
            ) from exc
    else:
        selected_template = await get_default_outreach_template_for_identity(session, identity)
    internal_generation_mode = (
        OUTREACH_GENERATION_MODE_TEMPLATE
        if payload.generation_mode == "template"
        else OUTREACH_GENERATION_MODE_LLM
    )
    outreach_config = resolve_outreach_template_config(
        identity,
        template=selected_template,
        generation_mode=internal_generation_mode,
        subject_template=payload.subject,
        body_text_template=payload.body_text,
        body_html_template=payload.body_html,
    )
    validation_error = get_outreach_template_defaults_validation_error(
        outreach_config.subject_template,
        outreach_config.body_text_template,
    )
    if validation_error:
        raise AgentApiError(
            status_code=422,
            code="CAMPAIGN_TEMPLATE_CONTENT_INVALID",
            message=validation_error,
        )
    for professor in professors:
        try:
            build_initial_batch_draft(
                identity,
                professor,
                outreach_config,
                primary_material_available=primary_material is not None,
            )
        except ValueError as exc:
            raise AgentApiError(
                status_code=422,
                code="CAMPAIGN_TEMPLATE_RENDER_FAILED",
                message=f"无法为导师 {professor.id} 渲染模板草稿：{exc}",
                details={"professor_id": professor.id},
            ) from exc
    return CampaignCreateContext(
        payload=payload,
        identity=identity,
        llm_profile=llm_profile,
        professors=professors,
        selected_template=selected_template,
        primary_material=primary_material,
        attachment_materials=attachment_materials,
        outreach_config=outreach_config,
        scheduled_dates=scheduled_dates,
    )


def _validate_campaign_schedule(payload: AgentCampaignCreateRequest) -> list[str]:
    if payload.schedule_type == "immediate":
        return []
    try:
        scheduled_dates = normalize_scheduled_dates(payload.scheduled_dates)
    except ValueError as exc:
        raise AgentApiError(
            status_code=422,
            code="CAMPAIGN_SCHEDULE_INVALID",
            message=str(exc),
        ) from exc
    if not scheduled_dates or not payload.window_start_time or not payload.window_end_time:
        raise AgentApiError(
            status_code=422,
            code="CAMPAIGN_SCHEDULE_INVALID",
            message="定时发送必须指定日期和发送时间窗口。",
        )
    if not payload.emails_per_window or payload.emails_per_window <= 0:
        raise AgentApiError(
            status_code=422,
            code="CAMPAIGN_SCHEDULE_INVALID",
            message="每天发送数量必须大于 0。",
        )
    _validate_time_window(payload.window_start_time, payload.window_end_time)
    if not has_future_batch_window(
        local_now(),
        scheduled_dates=scheduled_dates,
        window_end_time=payload.window_end_time,
    ):
        raise AgentApiError(
            status_code=422,
            code="CAMPAIGN_SCHEDULE_EXPIRED",
            message="当前定时发送窗口已全部过期，请重新选择日期或结束时间。",
        )
    return scheduled_dates


def _build_campaign_create_snapshot(context: CampaignCreateContext) -> dict[str, object]:
    payload = context.payload
    fallback_count = sum(
        1
        for professor in context.professors
        if (
            payload.generation_mode == "ai_rewrite"
            and context.primary_material is not None
            and not professor_has_research_direction(professor)
        )
    )
    state = {
        "identity": _identity_state(context.identity),
        "llm_profile": _llm_profile_state(context.llm_profile),
        "professors": [_professor_state(professor) for professor in context.professors],
        "template": _template_state(context.selected_template),
        "reference_material": _material_state(context.primary_material),
        "attachments": [_material_state(material) for material in context.attachment_materials],
        "outreach_config": _outreach_config_state(context.outreach_config),
        "schedule": _schedule_state(
            schedule_type=payload.schedule_type,
            window_start_time=payload.window_start_time,
            window_end_time=payload.window_end_time,
            emails_per_window=payload.emails_per_window,
            scheduled_dates=context.scheduled_dates,
        ),
    }
    summary = {
        "campaign": {
            "name": payload.name.strip(),
            "status_after_execution": BatchTaskStatus.PAUSED.value,
            "schedule": state["schedule"],
        },
        "recipient_count": len(context.professors),
        "recipients": [
            {"id": professor.id, "name": professor.name, "email": professor.email}
            for professor in context.professors
        ],
        "identity": _named_identity(context.identity),
        "llm_profile": _named_llm_profile(context.llm_profile),
        "generation_mode": payload.generation_mode,
        "template_fallback_count": fallback_count,
        "template": _named_template(context.selected_template),
        "reference_material": _named_material(context.primary_material),
        "attachments": [_named_material(material) for material in context.attachment_materials],
    }
    warnings = ["确认后只会创建暂停的草稿活动，不会发送或排程任何邮件。"]
    if payload.generation_mode == "ai_rewrite":
        warnings.append(
            "AI 草稿尚未开始生成。需在创建后明确运行 campaigns start-drafts，才会调用模型。",
        )
        if fallback_count:
            warnings.append(
                f"其中 {fallback_count} 位导师缺少研究方向；这些邮件会直接使用模板生成到待审核状态，不会调用模型。",
            )
    else:
        warnings.append("固定模板草稿会生成到待审核状态；发送前仍需单独创建并确认批量发送计划。")
    snapshot = {
        "snapshot_version": CAMPAIGN_CREATE_SNAPSHOT_VERSION,
        "request": payload.model_dump(mode="json"),
        "state": state,
        "summary": summary,
        "warnings": warnings,
    }
    snapshot["campaign_create_fingerprint"] = _campaign_create_snapshot_fingerprint(snapshot)
    return snapshot


def _campaign_create_snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    return fingerprint(
        {
            "request": snapshot.get("request"),
            "state": snapshot.get("state"),
        },
    )


def _campaign_create_request_from_snapshot(
    snapshot: dict[str, object],
) -> AgentCampaignCreateRequest:
    request = snapshot.get("request")
    if not isinstance(request, dict):
        raise _invalid_campaign_snapshot_error()
    try:
        return AgentCampaignCreateRequest.model_validate(request)
    except ValueError as exc:
        raise _invalid_campaign_snapshot_error() from exc


async def _resolve_campaign_send_context(
    session: AsyncSession,
    campaign_id: int,
    payload: AgentCampaignSendRequest,
    *,
    scheduled_at_values: list[datetime | None] | None = None,
) -> CampaignSendContext:
    campaign = await _load_campaign_for_send_or_raise(session, campaign_id)
    _ensure_campaign_active_for_agent(campaign)
    selected_by_id = {task.id: task for task in campaign.email_tasks}
    missing_ids = sorted(set(payload.item_ids) - set(selected_by_id))
    if missing_ids:
        raise AgentApiError(
            status_code=404,
            code="CAMPAIGN_ITEMS_NOT_FOUND",
            message="部分活动项不存在或不属于该活动。",
            details={"item_ids": missing_ids},
        )
    selected_tasks = [selected_by_id[item_id] for item_id in sorted(payload.item_ids)]
    final_drafts = [_final_campaign_draft_or_raise(campaign, task) for task in selected_tasks]
    resolved_scheduled_at_values = _resolve_campaign_send_schedule(
        campaign,
        len(selected_tasks),
        scheduled_at_values=scheduled_at_values,
    )
    return CampaignSendContext(
        campaign=campaign,
        selected_tasks=selected_tasks,
        final_drafts=final_drafts,
        scheduled_at_values=resolved_scheduled_at_values,
    )


async def _resolve_campaign_resume_context(
    session: AsyncSession,
    campaign_id: int,
) -> CampaignResumeContext:
    campaign = await _load_campaign_for_send_or_raise(session, campaign_id)
    _ensure_campaign_not_in_trash(campaign)
    if campaign.status != BatchTaskStatus.PAUSED.value:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_NOT_PAUSED",
            message="只有暂停中的批量活动可以恢复运行。",
            details={"campaign_id": campaign.id, "status": campaign.status},
        )
    if campaign.schedule_type == "scheduled" and not has_future_batch_window(
        local_now(),
        scheduled_dates=campaign.scheduled_dates,
        window_end_time=campaign.window_end_time,
    ):
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_SCHEDULE_EXPIRED",
            message="该活动的定时发送窗口已全部过期，不能恢复运行。",
        )
    delivery_drafts = [
        _current_campaign_delivery_draft_or_raise(campaign, task)
        for task in campaign.email_tasks
        if task.batch_send_canceled_at is None
        and task.status
        in {
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
        }
    ]
    return CampaignResumeContext(
        campaign=campaign,
        delivery_drafts=delivery_drafts,
    )


async def _resolve_campaign_restore_send_context(
    session: AsyncSession,
    campaign_id: int,
    item_id: int,
) -> CampaignRestoreSendContext:
    campaign = await _load_campaign_for_send_or_raise(session, campaign_id)
    _ensure_campaign_not_in_trash(campaign)
    item = _find_visible_campaign_item(campaign, item_id)
    _ensure_campaign_item_send_action_allowed(campaign, item)
    if item.batch_send_canceled_at is None:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_ITEM_SEND_NOT_CANCELED",
            message="该活动项当前没有被取消的定时发送。",
            details={"campaign_id": campaign.id, "item_id": item.id},
        )
    if item.scheduled_at is None or as_utc_aware(item.scheduled_at) <= utc_now():
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_ITEM_SCHEDULE_EXPIRED",
            message="原定发送时间已经过去，不能恢复发送。",
            details={"campaign_id": campaign.id, "item_id": item.id},
        )
    if item.status not in CAMPAIGN_ITEM_SEND_CANCELLABLE_STATUSES:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_ITEM_SEND_NOT_RESTORABLE",
            message="活动项当前状态不能恢复发送。",
            details={"campaign_id": campaign.id, "item_id": item.id, "status": item.status},
        )
    return CampaignRestoreSendContext(
        campaign=campaign,
        final_draft=_current_campaign_delivery_draft_or_raise(campaign, item),
    )


def _final_campaign_draft_or_raise(
    campaign: BatchTask,
    task: EmailTask,
) -> FinalCampaignDraft:
    if task.status != EmailTaskStatus.REVIEW_REQUIRED.value:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_ITEM_NOT_REVIEWABLE",
            message=f"活动项 {task.id} 当前状态为 {task.status}，不能创建发送计划。",
            details={"item_id": task.id, "status": task.status},
        )
    if task.batch_send_canceled_at is not None:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_ITEM_SEND_CANCELED",
            message=f"活动项 {task.id} 已取消发送。",
            details={"item_id": task.id},
        )
    return _current_campaign_delivery_draft_or_raise(campaign, task)


def _current_campaign_delivery_draft_or_raise(
    campaign: BatchTask,
    task: EmailTask,
) -> FinalCampaignDraft:
    if task.professor is None or not _has_valid_recipient_email(task.professor.email):
        raise AgentApiError(
            status_code=422,
            code="CAMPAIGN_RECIPIENT_EMAIL_INVALID",
            message=f"活动项 {task.id} 的导师没有可用邮箱地址。",
            details={"item_id": task.id},
        )
    identity = campaign.identity
    if identity is None or not (
        identity.smtp_host and identity.smtp_username and identity.smtp_password
    ):
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_SMTP_NOT_CONFIGURED",
            message="发件身份尚未配置 SMTP，不能创建批量发送计划。",
        )
    has_saved_snapshot = any(
        value is not None
        for value in (
            task.approved_subject,
            task.approved_body_text,
            task.approved_body_html,
        )
    )
    subject = (
        task.approved_subject if has_saved_snapshot else task.generated_subject
    ) or ""
    body_text = (
        task.approved_body_text if has_saved_snapshot else task.generated_content_text
    ) or ""
    body_html = (
        task.approved_body_html if has_saved_snapshot else task.generated_content_html
    )
    subject = subject.strip()
    body_text = body_text.strip()
    if not subject or not body_text:
        raise AgentApiError(
            status_code=422,
            code="CAMPAIGN_DRAFT_CONTENT_MISSING",
            message=f"活动项 {task.id} 缺少可发送的主题或正文。",
            details={"item_id": task.id},
        )
    if body_html:
        rendered = normalize_email_html(body_html)
    else:
        rendered = text_to_email_html(body_text)
    material_by_id = {material.id: material for material in identity.materials}
    attachment_material_ids = list(dict.fromkeys(task.selected_material_ids or []))
    missing_attachment_ids = [
        material_id
        for material_id in attachment_material_ids
        if material_id not in material_by_id
    ]
    if missing_attachment_ids:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_ATTACHMENT_MATERIAL_STALE",
            message=f"活动项 {task.id} 包含不存在或不属于发件身份的附件。",
            details={"item_id": task.id, "attachment_material_ids": missing_attachment_ids},
        )
    return FinalCampaignDraft(
        task=task,
        subject=subject,
        body_text=rendered.text,
        body_html=rendered.html,
        attachment_material_ids=attachment_material_ids,
    )


def _resolve_campaign_send_schedule(
    campaign: BatchTask,
    task_count: int,
    *,
    scheduled_at_values: list[datetime | None] | None,
) -> list[datetime | None]:
    if campaign.schedule_type == "immediate":
        if scheduled_at_values is not None and (
            len(scheduled_at_values) != task_count
            or any(value is not None for value in scheduled_at_values)
        ):
            raise _invalid_campaign_snapshot_error()
        return [None] * task_count
    if campaign.schedule_type != "scheduled":
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_SCHEDULE_INVALID",
            message="活动的发送方式无效，不能创建发送计划。",
        )
    if not has_future_batch_window(
        local_now(),
        scheduled_dates=campaign.scheduled_dates,
        window_end_time=campaign.window_end_time,
    ):
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_SCHEDULE_EXPIRED",
            message="活动的定时发送窗口已全部过期，请重新创建活动或在桌面端调整计划。",
        )
    if scheduled_at_values is None:
        try:
            return list(
                build_jittered_batch_schedule(
                    task_count=task_count,
                    scheduled_dates=normalize_scheduled_dates(campaign.scheduled_dates),
                    window_start_time=campaign.window_start_time or "",
                    window_end_time=campaign.window_end_time or "",
                    emails_per_window=campaign.emails_per_window or 0,
                    now=local_now(),
                ),
            )
        except ValueError as exc:
            raise AgentApiError(
                status_code=422,
                code="CAMPAIGN_SCHEDULE_INVALID",
                message=str(exc),
            ) from exc
    if len(scheduled_at_values) != task_count or any(
        value is None for value in scheduled_at_values
    ):
        raise _invalid_campaign_snapshot_error()
    now = utc_now()
    if any(as_utc_aware(value) <= now for value in scheduled_at_values if value is not None):
        raise AgentApiError(
            status_code=409,
            code="PLAN_STALE",
            message="批量发送计划中的排程时间已经过去，请重新生成计划。",
            details={"changed_fields": ["scheduled_at"]},
            suggested_command=f"auto-email-sender campaigns prepare-send {campaign.id}",
        )
    return scheduled_at_values


def _build_campaign_send_snapshot(
    context: CampaignSendContext,
    payload: AgentCampaignSendRequest,
) -> dict[str, object]:
    state = {
        "campaign": _campaign_send_state(context.campaign),
        "identity": _identity_state(context.campaign.identity),
        "items": [
            _campaign_send_item_state(final_draft)
            for final_draft in context.final_drafts
        ],
    }
    items_summary = [
        _campaign_send_item_summary(final_draft, scheduled_at)
        for final_draft, scheduled_at in zip(
            context.final_drafts,
            context.scheduled_at_values,
            strict=True,
        )
    ]
    snapshot = {
        "snapshot_version": CAMPAIGN_SEND_SNAPSHOT_VERSION,
        "request": {
            "campaign_id": context.campaign.id,
            "item_ids": [task.id for task in context.selected_tasks],
        },
        "state": state,
        "scheduled_at": [
            serialize_api_datetime(value) if value is not None else None
            for value in context.scheduled_at_values
        ],
        "summary": {
            "campaign": {
                "id": context.campaign.id,
                "name": context.campaign.name,
                "schedule_type": context.campaign.schedule_type,
            },
            "recipient_count": len(context.final_drafts),
            "delivery": context.campaign.schedule_type,
            "items": items_summary,
        },
        "warnings": [
            "尚未发送。确认后这些邮件会进入发送队列或按活动时间窗口排程。",
            "执行后无法通过同一计划再次创建重复发送。",
        ],
    }
    snapshot["campaign_send_fingerprint"] = _campaign_send_snapshot_fingerprint(snapshot)
    return snapshot


def _campaign_send_snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    return fingerprint(
        {
            "request": snapshot.get("request"),
            "state": snapshot.get("state"),
        },
    )


def _build_campaign_resume_snapshot(
    context: CampaignResumeContext,
) -> dict[str, object]:
    campaign = context.campaign
    items = [
        _campaign_send_item_summary(draft, draft.task.scheduled_at)
        for draft in context.delivery_drafts
    ]
    snapshot = {
        "snapshot_version": CAMPAIGN_RESUME_SNAPSHOT_VERSION,
        "request": {"campaign_id": campaign.id},
        "state": {
            "campaign": _campaign_send_state(campaign),
            "identity": _identity_state(campaign.identity),
            "items": [
                _campaign_send_item_state(draft) for draft in context.delivery_drafts
            ],
        },
        "summary": {
            "campaign": {
                "id": campaign.id,
                "name": campaign.name,
                "schedule_type": campaign.schedule_type,
            },
            "recipient_count": len(items),
            "delivery": campaign.schedule_type,
            "items": items,
        },
        "warnings": [
            "尚未恢复活动。确认后活动会恢复运行，下面列出的已获准或已排程邮件可能重新进入发送调度。",
            "已取消发送的活动项不会因恢复活动而自动恢复；需要单独创建恢复发送计划。",
        ],
    }
    snapshot["campaign_resume_fingerprint"] = _campaign_resume_snapshot_fingerprint(
        snapshot,
    )
    return snapshot


def _campaign_resume_snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    return fingerprint(
        {
            "request": snapshot.get("request"),
            "state": snapshot.get("state"),
        },
    )


def _campaign_resume_request_from_snapshot(snapshot: dict[str, object]) -> int:
    request = snapshot.get("request")
    if not isinstance(request, dict):
        raise _invalid_campaign_snapshot_error()
    campaign_id = request.get("campaign_id")
    if not isinstance(campaign_id, int) or isinstance(campaign_id, bool) or campaign_id < 1:
        raise _invalid_campaign_snapshot_error()
    return campaign_id


def _build_campaign_restore_send_snapshot(
    context: CampaignRestoreSendContext,
) -> dict[str, object]:
    campaign = context.campaign
    final_draft = context.final_draft
    item = _campaign_send_item_summary(final_draft, final_draft.task.scheduled_at)
    snapshot = {
        "snapshot_version": CAMPAIGN_RESTORE_SEND_SNAPSHOT_VERSION,
        "request": {
            "campaign_id": campaign.id,
            "item_id": final_draft.task.id,
        },
        "state": {
            "campaign": _campaign_send_state(campaign),
            "identity": _identity_state(campaign.identity),
            "item": _campaign_send_item_state(final_draft),
        },
        "summary": {
            "campaign": {
                "id": campaign.id,
                "name": campaign.name,
                "schedule_type": campaign.schedule_type,
            },
            "recipient_count": 1,
            "delivery": "scheduled",
            "items": [item],
        },
        "warnings": [
            "尚未恢复发送。确认后这封邮件会恢复到原定的未来发送时间。",
            "若活动当前正在运行，到达原定时间后会按已有身份和 SMTP 配置进入发送流程。",
        ],
    }
    snapshot["campaign_restore_send_fingerprint"] = (
        _campaign_restore_send_snapshot_fingerprint(snapshot)
    )
    return snapshot


def _campaign_restore_send_snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    return fingerprint(
        {
            "request": snapshot.get("request"),
            "state": snapshot.get("state"),
        },
    )


def _campaign_restore_send_request_from_snapshot(
    snapshot: dict[str, object],
) -> tuple[int, int]:
    request = snapshot.get("request")
    if not isinstance(request, dict):
        raise _invalid_campaign_snapshot_error()
    campaign_id = request.get("campaign_id")
    item_id = request.get("item_id")
    if (
        not isinstance(campaign_id, int)
        or isinstance(campaign_id, bool)
        or campaign_id < 1
        or not isinstance(item_id, int)
        or isinstance(item_id, bool)
        or item_id < 1
    ):
        raise _invalid_campaign_snapshot_error()
    return campaign_id, item_id


def _campaign_send_request_from_snapshot(
    snapshot: dict[str, object],
) -> tuple[int, AgentCampaignSendRequest, list[datetime | None]]:
    request = snapshot.get("request")
    scheduled_at = snapshot.get("scheduled_at")
    if not isinstance(request, dict) or not isinstance(scheduled_at, list):
        raise _invalid_campaign_snapshot_error()
    campaign_id = request.get("campaign_id")
    item_ids = request.get("item_ids")
    if not isinstance(campaign_id, int) or isinstance(campaign_id, bool) or campaign_id < 1:
        raise _invalid_campaign_snapshot_error()
    try:
        payload = AgentCampaignSendRequest.model_validate({"item_ids": item_ids})
    except ValueError as exc:
        raise _invalid_campaign_snapshot_error() from exc
    values: list[datetime | None] = []
    for value in scheduled_at:
        if value is None:
            values.append(None)
            continue
        if not isinstance(value, str):
            raise _invalid_campaign_snapshot_error()
        try:
            from app.core.time import parse_api_datetime

            values.append(parse_api_datetime(value))
        except ValueError as exc:
            raise _invalid_campaign_snapshot_error() from exc
    return campaign_id, payload, values


def _campaign_send_state(campaign: BatchTask) -> dict[str, object]:
    return {
        "id": campaign.id,
        "status": campaign.status,
        "deleted_at": _serialize_optional_datetime(campaign.deleted_at),
        "schedule": _schedule_state(
            schedule_type=campaign.schedule_type,
            window_start_time=campaign.window_start_time,
            window_end_time=campaign.window_end_time,
            emails_per_window=campaign.emails_per_window,
            scheduled_dates=campaign.scheduled_dates or [],
        ),
        "updated_at": _serialize_optional_datetime(campaign.updated_at),
    }


def _campaign_send_item_state(final_draft: FinalCampaignDraft) -> dict[str, object]:
    task = final_draft.task
    return {
        "task": {
            "id": task.id,
            "status": task.status,
            "batch_send_canceled_at": _serialize_optional_datetime(task.batch_send_canceled_at),
            "updated_at": _serialize_optional_datetime(task.updated_at),
            "generation_mode": _agent_generation_mode(task.outreach_generation_mode),
            "template_id": task.outreach_template_id,
            "reference_material_id": task.primary_material_id,
            "attachment_material_ids": final_draft.attachment_material_ids,
            "subject": final_draft.subject,
            "body_text": final_draft.body_text,
            "body_html": final_draft.body_html,
        },
        "professor": _professor_state(task.professor),
        "template": _template_state(task.outreach_template),
        "reference_material": _material_state(task.primary_material),
        "attachments": [
            _material_state(material)
            for material in _selected_attachment_materials(task)
        ],
    }


def _campaign_send_item_summary(
    final_draft: FinalCampaignDraft,
    scheduled_at: datetime | None,
) -> dict[str, object]:
    task = final_draft.task
    identity = task.batch_task.identity if task.batch_task is not None else None
    return {
        "item_id": task.id,
        "recipient": {
            "id": task.professor.id,
            "name": task.professor.name,
            "email": task.professor.email,
        },
        "identity": _named_identity(identity),
        "generation_mode": _agent_generation_mode(task.outreach_generation_mode),
        "template": _named_template(task.outreach_template),
        "reference_material": _named_material(task.primary_material),
        "attachments": [
            _named_material(material) for material in _selected_attachment_materials(task)
        ],
        "scheduled_at": _serialize_optional_datetime(scheduled_at),
        "subject": final_draft.subject,
        "body_text": final_draft.body_text,
        "body_html": final_draft.body_html,
    }


def _selected_attachment_materials(task: EmailTask) -> list[IdentityMaterial]:
    if task.batch_task is None or task.batch_task.identity is None:
        return []
    material_by_id = {material.id: material for material in task.batch_task.identity.materials}
    return [
        material_by_id[material_id]
        for material_id in task.selected_material_ids or []
        if material_id in material_by_id
    ]


def _campaign_load_statement():
    return select(BatchTask).options(
        selectinload(BatchTask.email_tasks).selectinload(EmailTask.professor),
        selectinload(BatchTask.email_tasks).selectinload(EmailTask.primary_material),
        selectinload(BatchTask.identity).selectinload(IdentityProfile.materials),
        selectinload(BatchTask.llm_profile),
        selectinload(BatchTask.primary_material),
    )


async def _campaign_task_summaries(
    session: AsyncSession,
    campaign_ids: list[int],
) -> dict[int, CampaignTaskSummary]:
    summaries = {
        campaign_id: CampaignTaskSummary(
            counts=Counter(),
            canceled_send_count=0,
            has_dispatchable=False,
            has_ai_pending=False,
        )
        for campaign_id in campaign_ids
    }
    if not campaign_ids:
        return summaries

    visible = or_(
        EmailTask.status != EmailTaskStatus.CANCELED.value,
        EmailTask.cancellation_reason.is_(None),
        EmailTask.cancellation_reason
        != EmailTaskCancellationReason.USER_REMOVED.value,
    )
    active = and_(visible, EmailTask.batch_send_canceled_at.is_(None))
    statuses = [status.value for status in EmailTaskStatus]
    status_columns = [
        func.sum(
            case(
                (and_(active, EmailTask.status == task_status), 1),
                else_=0,
            ),
        ).label(f"status_{task_status}")
        for task_status in statuses
    ]
    rows = []
    for campaign_id_chunk in chunked_values(unique_positive_ids(campaign_ids)):
        rows.extend(
            (
                await session.execute(
                    select(
            EmailTask.batch_task_id,
            *status_columns,
            func.sum(
                case(
                    (
                        and_(visible, EmailTask.batch_send_canceled_at.is_not(None)),
                        1,
                    ),
                    else_=0,
                ),
            ).label("canceled_send_count"),
            func.max(
                case(
                    (
                        and_(active, EmailTask.status.in_(CAMPAIGN_DISPATCHABLE_STATUSES)),
                        1,
                    ),
                    else_=0,
                ),
            ).label("has_dispatchable"),
            func.max(
                case(
                    (
                        and_(
                            active,
                            or_(
                                EmailTask.outreach_generation_mode.is_(None),
                                func.lower(EmailTask.outreach_generation_mode)
                                != OUTREACH_GENERATION_MODE_TEMPLATE,
                            ),
                            EmailTask.status.in_(
                                {
                                    EmailTaskStatus.DISCOVERED.value,
                                    EmailTaskStatus.MATCHED.value,
                                    EmailTaskStatus.DRAFT_FAILED.value,
                                },
                            ),
                        ),
                        1,
                    ),
                    else_=0,
                ),
            ).label("has_ai_pending"),
        )
                    .where(EmailTask.batch_task_id.in_(campaign_id_chunk))
                    .group_by(EmailTask.batch_task_id),
                )
            ).mappings(),
        )
    for row in rows:
        campaign_id = int(row["batch_task_id"])
        summaries[campaign_id] = CampaignTaskSummary(
            counts=Counter(
                {
                    task_status: int(row[f"status_{task_status}"] or 0)
                    for task_status in statuses
                },
            ),
            canceled_send_count=int(row["canceled_send_count"] or 0),
            has_dispatchable=bool(row["has_dispatchable"]),
            has_ai_pending=bool(row["has_ai_pending"]),
        )
    return summaries


async def _load_campaign_or_raise(session: AsyncSession, campaign_id: int) -> BatchTask:
    campaign = await session.scalar(
        _campaign_load_statement().where(BatchTask.id == campaign_id),
    )
    if campaign is None:
        raise AgentApiError(
            status_code=404,
            code="CAMPAIGN_NOT_FOUND",
            message="未找到批量活动。",
        )
    return campaign


async def _load_campaign_for_send_or_raise(
    session: AsyncSession,
    campaign_id: int,
) -> BatchTask:
    campaign = await session.scalar(
        select(BatchTask)
        .options(
            selectinload(BatchTask.identity).selectinload(IdentityProfile.materials),
            selectinload(BatchTask.email_tasks).selectinload(EmailTask.professor),
            selectinload(BatchTask.email_tasks).selectinload(EmailTask.primary_material),
            selectinload(BatchTask.email_tasks).selectinload(EmailTask.outreach_template),
        )
        .where(BatchTask.id == campaign_id),
    )
    if campaign is None:
        raise AgentApiError(
            status_code=404,
            code="CAMPAIGN_NOT_FOUND",
            message="未找到批量活动。",
        )
    return campaign


def _ensure_campaign_active_for_agent(campaign: BatchTask) -> None:
    if campaign.deleted_at is not None:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_IN_TRASH",
            message="该批量活动已在回收站中，不能由 Agent 操作。",
        )
    if campaign.status not in CAMPAIGN_ALLOWED_ACTIVE_STATUSES:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_NOT_ACTIVE",
            message=f"批量活动当前状态为 {campaign.status}，不能执行该操作。",
        )


def _ensure_campaign_not_in_trash(campaign: BatchTask) -> None:
    if campaign.deleted_at is not None:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_IN_TRASH",
            message="该批量活动已在回收站中，不能执行此操作。",
        )


def _is_user_removed_campaign_item(task: EmailTask) -> bool:
    return (
        task.status == EmailTaskStatus.CANCELED.value
        and task.cancellation_reason == EmailTaskCancellationReason.USER_REMOVED.value
    )


def _find_visible_campaign_item(campaign: BatchTask, item_id: int) -> EmailTask:
    item = next(
        (
            task
            for task in campaign.email_tasks
            if task.id == item_id and not _is_user_removed_campaign_item(task)
        ),
        None,
    )
    if item is None:
        raise AgentApiError(
            status_code=404,
            code="CAMPAIGN_ITEM_NOT_FOUND",
            message="未找到该批量活动项。",
            details={"campaign_id": campaign.id, "item_id": item_id},
        )
    return item


def _campaign_allows_item_send_actions(campaign: BatchTask) -> bool:
    return bool(
        campaign.deleted_at is None
        and campaign.schedule_type == "scheduled"
        and campaign.status in CAMPAIGN_ALLOWED_ACTIVE_STATUSES
    )


def _ensure_campaign_item_send_action_allowed(
    campaign: BatchTask,
    item: EmailTask,
) -> None:
    if not _campaign_allows_item_send_actions(campaign):
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_ITEM_SEND_ACTION_UNAVAILABLE",
            message="当前批量活动状态不支持修改该导师的发送计划。",
        )
    if item.scheduled_at is None:
        raise AgentApiError(
            status_code=409,
            code="CAMPAIGN_ITEM_SCHEDULE_MISSING",
            message="该活动项缺少原定发送时间，不能修改发送计划。",
        )


async def _sanitize_campaign_material_references_before_restore(
    session: AsyncSession,
    campaign: BatchTask,
) -> None:
    material_ids = set(campaign.selected_material_ids or [])
    if campaign.primary_material_id is not None:
        material_ids.add(campaign.primary_material_id)
    if not material_ids:
        return
    existing_material_ids: set[int] = set()
    for material_id_chunk in chunked_values(material_ids):
        existing_material_ids.update(
            await session.scalars(
                select(IdentityMaterial.id).where(
                    IdentityMaterial.identity_id == campaign.identity_id,
                    IdentityMaterial.id.in_(material_id_chunk),
                ),
            ),
        )
    updated = False
    if (
        campaign.primary_material_id is not None
        and campaign.primary_material_id not in existing_material_ids
    ):
        campaign.primary_material_id = None
        if campaign.status not in CAMPAIGN_DELETABLE_STATUSES:
            campaign.status = BatchTaskStatus.STOPPED.value
        updated = True
    if campaign.selected_material_ids is not None:
        filtered_ids = [
            material_id
            for material_id in campaign.selected_material_ids
            if material_id in existing_material_ids
        ]
        if filtered_ids != campaign.selected_material_ids:
            campaign.selected_material_ids = filtered_ids
            updated = True
    if updated:
        campaign.updated_at = utc_now()


async def _record_campaign_action(
    session: AsyncSession,
    campaign: BatchTask,
    event_name: str,
    *,
    metadata: dict[str, object] | None = None,
) -> None:
    event_metadata: dict[str, object] = {
        "actor": "agent_cli",
        "status": campaign.status,
        "target_count": campaign.target_count,
        "identity_id": campaign.identity_id,
        "llm_profile_id": campaign.llm_profile_id,
    }
    if metadata:
        event_metadata.update(metadata)
    await record_operation_log(
        session,
        category="email",
        event_name=event_name,
        entity_type="batch_task",
        entity_id=str(campaign.id),
        metadata=event_metadata,
    )


def _serialize_campaign(
    campaign: BatchTask,
    *,
    task_summary: CampaignTaskSummary | None = None,
) -> AgentCampaignRead:
    visible_tasks: list[EmailTask] | None = None
    if task_summary is None:
        visible_tasks = [
            task for task in campaign.email_tasks if not _is_user_removed_campaign_item(task)
        ]
        active_tasks = [
            task for task in visible_tasks if task.batch_send_canceled_at is None
        ]
        counts = Counter(task.status for task in active_tasks)
        canceled_send_count = sum(
            1 for task in visible_tasks if task.batch_send_canceled_at is not None
        )
        can_start_draft_generation = _campaign_can_start_draft_generation(campaign)
    else:
        counts = task_summary.counts
        canceled_send_count = task_summary.canceled_send_count
        can_start_draft_generation = (
            campaign.status in CAMPAIGN_ALLOWED_ACTIVE_STATUSES
            and campaign.deleted_at is None
            and not task_summary.has_dispatchable
            and task_summary.has_ai_pending
        )
    identity = campaign.identity
    llm_profile = campaign.llm_profile
    if identity is None or llm_profile is None:  # pragma: no cover - database foreign keys
        raise AgentApiError(
            status_code=500,
            code="CAMPAIGN_RELATION_MISSING",
            message="批量活动关联的数据不完整。",
        )
    return AgentCampaignRead(
        id=campaign.id,
        name=campaign.name,
        status=campaign.status,
        identity={"id": identity.id, "name": _identity_name(identity)},
        llm_profile={"id": llm_profile.id, "name": llm_profile.name},
        generation_mode=_agent_generation_mode(campaign.outreach_generation_mode),
        template=(
            {"id": campaign.outreach_template_id, "name": campaign.outreach_template_name_snapshot}
            if campaign.outreach_template_id is not None
            and campaign.outreach_template_name_snapshot is not None
            else None
        ),
        reference_material=_named_material(campaign.primary_material),
        attachment_material_ids=list(campaign.selected_material_ids or []),
        schedule_type=(
            "scheduled" if campaign.schedule_type == "scheduled" else "immediate"
        ),
        window_start_time=campaign.window_start_time,
        window_end_time=campaign.window_end_time,
        emails_per_window=campaign.emails_per_window,
        scheduled_dates=list(campaign.scheduled_dates or []),
        target_count=campaign.target_count,
        pending_generation_count=(
            counts[EmailTaskStatus.DISCOVERED.value] + counts[EmailTaskStatus.MATCHED.value]
        ),
        generating_draft_count=counts[EmailTaskStatus.GENERATING_DRAFT.value],
        draft_failed_count=counts[EmailTaskStatus.DRAFT_FAILED.value],
        review_required_count=counts[EmailTaskStatus.REVIEW_REQUIRED.value],
        approved_count=counts[EmailTaskStatus.APPROVED.value],
        scheduled_count=counts[EmailTaskStatus.SCHEDULED.value],
        sending_count=counts[EmailTaskStatus.SENDING.value],
        sent_count=(
            counts[EmailTaskStatus.SENT.value] + counts[EmailTaskStatus.REPLY_DETECTED.value]
        ),
        failed_count=counts[EmailTaskStatus.SEND_FAILED.value],
        canceled_count=counts[EmailTaskStatus.CANCELED.value],
        canceled_send_count=canceled_send_count,
        can_start_draft_generation=can_start_draft_generation,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


def _serialize_campaign_item(
    task: EmailTask,
    *,
    campaign: BatchTask | None = None,
) -> AgentCampaignItemRead:
    subject = (
        task.approved_subject
        if any(
            value is not None
            for value in (
                task.approved_subject,
                task.approved_body_text,
                task.approved_body_html,
            )
        )
        else task.generated_subject
    )
    has_final_content = bool(
        (task.approved_body_text or task.generated_content_text or "").strip()
        or (task.approved_body_html or task.generated_content_html or "").strip()
    )
    if task.professor is None or task.batch_task_id is None:  # pragma: no cover - database foreign keys
        raise AgentApiError(
            status_code=500,
            code="CAMPAIGN_RELATION_MISSING",
            message="批量活动项关联的数据不完整。",
        )
    resolved_campaign = campaign
    can_remove = bool(
        resolved_campaign is not None
        and resolved_campaign.deleted_at is None
        and task.batch_send_canceled_at is None
        and task.status in CAMPAIGN_ITEM_REMOVABLE_STATUSES
    )
    can_cancel_send = bool(
        resolved_campaign is not None
        and _campaign_allows_item_send_actions(resolved_campaign)
        and task.scheduled_at is not None
        and task.batch_send_canceled_at is None
        and task.status in CAMPAIGN_ITEM_SEND_CANCELLABLE_STATUSES
    )
    can_restore_send = bool(
        resolved_campaign is not None
        and _campaign_allows_item_send_actions(resolved_campaign)
        and task.scheduled_at is not None
        and as_utc_aware(task.scheduled_at) > utc_now()
        and task.batch_send_canceled_at is not None
        and task.status in CAMPAIGN_ITEM_SEND_CANCELLABLE_STATUSES
    )
    can_retry_draft = bool(
        resolved_campaign is not None
        and resolved_campaign.status == BatchTaskStatus.RUNNING.value
        and resolved_campaign.deleted_at is None
        and task.batch_send_canceled_at is None
        and task.status == EmailTaskStatus.DRAFT_FAILED.value
        and _task_uses_ai_rewrite(task)
        and task.primary_material is not None
        and task.professor is not None
        and bool((task.professor.research_direction or "").strip())
    )
    return AgentCampaignItemRead(
        id=task.id,
        campaign_id=task.batch_task_id,
        professor_id=task.professor.id,
        professor_name=task.professor.name,
        professor_email=task.professor.email,
        status=task.status,
        generation_mode=_agent_generation_mode(task.outreach_generation_mode),
        draft_generation_source=task.draft_generation_source,
        draft_fallback_reason=task.draft_fallback_reason,
        subject=subject,
        has_final_content=has_final_content,
        attachment_material_ids=list(task.selected_material_ids or []),
        scheduled_at=task.scheduled_at,
        send_canceled_at=task.batch_send_canceled_at,
        sent_at=task.sent_at,
        last_error=task.last_error,
        can_remove=can_remove,
        can_cancel_send=can_cancel_send,
        can_restore_send=can_restore_send,
        can_retry_draft=can_retry_draft,
        updated_at=task.updated_at,
    )


def _campaign_can_start_draft_generation(campaign: BatchTask) -> bool:
    if campaign.status not in CAMPAIGN_ALLOWED_ACTIVE_STATUSES or campaign.deleted_at is not None:
        return False
    if any(
        task.status in CAMPAIGN_DISPATCHABLE_STATUSES
        and task.batch_send_canceled_at is None
        for task in campaign.email_tasks
    ):
        return False
    return any(
        _task_uses_ai_rewrite(task)
        and task.status
        in {
            EmailTaskStatus.DISCOVERED.value,
            EmailTaskStatus.MATCHED.value,
            EmailTaskStatus.DRAFT_FAILED.value,
        }
        and task.batch_send_canceled_at is None
        for task in campaign.email_tasks
    )


def _task_uses_ai_rewrite(task: EmailTask) -> bool:
    return (task.outreach_generation_mode or "").strip().lower() != OUTREACH_GENERATION_MODE_TEMPLATE


def _agent_generation_mode(value: str | None) -> str:
    return "template" if (value or "").strip().lower() == "template" else "ai_rewrite"


def _identity_state(identity: IdentityProfile | None) -> dict[str, object] | None:
    if identity is None:
        return None
    return {
        "id": identity.id,
        "name": identity.name,
        "profile_name": identity.profile_name,
        "sender_name": identity.sender_name,
        "email_address": identity.email_address,
        "outreach_generation_mode": identity.outreach_generation_mode,
        "outreach_template_subject": identity.outreach_template_subject,
        "outreach_template_body_text": identity.outreach_template_body_text,
        "outreach_template_body_html": identity.outreach_template_body_html,
        "default_outreach_template_id": identity.default_outreach_template_id,
        "smtp_configured": bool(
            identity.smtp_host and identity.smtp_username and identity.smtp_password
        ),
        "updated_at": _serialize_optional_datetime(identity.updated_at),
    }


def _llm_profile_state(profile: LLMProfile) -> dict[str, object]:
    return {
        "id": profile.id,
        "name": profile.name,
        "provider": profile.provider,
        "model_name": profile.model_name,
        "updated_at": _serialize_optional_datetime(profile.updated_at),
    }


def _professor_state(professor: Professor | None) -> dict[str, object] | None:
    if professor is None:
        return None
    return {
        "id": professor.id,
        "name": professor.name,
        "email": professor.email,
        "title": professor.title,
        "university": professor.university,
        "school": professor.school,
        "department": professor.department,
        "research_direction": professor.research_direction,
        "archived_at": _serialize_optional_datetime(professor.archived_at),
        "updated_at": _serialize_optional_datetime(professor.updated_at),
    }


def _template_state(template: OutreachTemplate | None) -> dict[str, object] | None:
    if template is None:
        return None
    return {
        "id": template.id,
        "name": template.name,
        "recommended_generation_mode": template.recommended_generation_mode,
        "subject": template.subject,
        "body_text": template.body_text,
        "body_html": template.body_html,
        "archived_at": _serialize_optional_datetime(template.archived_at),
        "updated_at": _serialize_optional_datetime(template.updated_at),
    }


def _material_state(material: IdentityMaterial | None) -> dict[str, object] | None:
    if material is None:
        return None
    return {
        "id": material.id,
        "identity_id": material.identity_id,
        "display_name": material.display_name,
        "material_type": material.material_type,
        "mime_type": material.mime_type,
        "size_bytes": material.size_bytes,
        "created_at": _serialize_optional_datetime(material.created_at),
    }


def _outreach_config_state(config: OutreachTemplateConfig) -> dict[str, object]:
    return {
        "generation_mode": config.generation_mode,
        "subject_template": config.subject_template,
        "body_text_template": config.body_text_template,
        "body_html_template": config.body_html_template,
    }


def _schedule_state(
    *,
    schedule_type: str,
    window_start_time: str | None,
    window_end_time: str | None,
    emails_per_window: int | None,
    scheduled_dates: list[str] | None,
) -> dict[str, object]:
    return {
        "schedule_type": schedule_type,
        "window_start_time": window_start_time,
        "window_end_time": window_end_time,
        "emails_per_window": emails_per_window,
        "scheduled_dates": list(scheduled_dates or []),
    }


def _named_identity(identity: IdentityProfile | None) -> dict[str, object] | None:
    if identity is None:
        return None
    return {
        "id": identity.id,
        "name": _identity_name(identity),
        "email_address": identity.email_address,
    }


def _named_llm_profile(profile: LLMProfile) -> dict[str, object]:
    return {"id": profile.id, "name": profile.name, "model_name": profile.model_name}


def _named_template(template: OutreachTemplate | None) -> dict[str, object] | None:
    if template is None:
        return None
    return {"id": template.id, "name": template.name}


def _named_material(material: IdentityMaterial | None) -> dict[str, object] | None:
    if material is None:
        return None
    return {"id": material.id, "name": material.display_name}


def _identity_name(identity: IdentityProfile) -> str:
    return identity.profile_name or identity.name


def _serialize_optional_datetime(value: datetime | None) -> str | None:
    return serialize_api_datetime(value) if value is not None else None


def _normalize_nullable_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _has_valid_recipient_email(value: str | None) -> bool:
    return bool(value and is_valid_professor_email(value.strip()))


def _validate_time_window(start_time: str | None, end_time: str | None) -> None:
    if not start_time or not end_time:
        raise AgentApiError(
            status_code=422,
            code="CAMPAIGN_SCHEDULE_INVALID",
            message="定时发送必须提供发送时间窗口。",
        )
    try:
        start = datetime.strptime(start_time, "%H:%M").time()
        end = datetime.strptime(end_time, "%H:%M").time()
    except ValueError as exc:
        raise AgentApiError(
            status_code=422,
            code="CAMPAIGN_SCHEDULE_INVALID",
            message="发送时间必须使用 HH:MM 格式。",
        ) from exc
    if end <= start:
        raise AgentApiError(
            status_code=422,
            code="CAMPAIGN_SCHEDULE_INVALID",
            message="结束时间必须晚于开始时间。",
        )


def _invalid_campaign_snapshot_error() -> AgentApiError:
    return AgentApiError(
        status_code=500,
        code="INVALID_CHANGE_PLAN_SNAPSHOT",
        message="批量活动计划快照无效，请重新生成计划。",
    )


def _campaign_create_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="活动所用的导师、身份、模板、材料、模型或排程已发生变化，请重新生成创建预览。",
        details={
            "changed_fields": [
                "professors",
                "identity",
                "template",
                "materials",
                "llm_profile",
                "schedule",
            ],
        },
    )


def _campaign_send_plan_stale_error(campaign_id: int) -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="活动状态、收件人、草稿、附件或排程已发生变化，请重新生成并展示批量发送计划。",
        details={"changed_fields": ["campaign", "recipients", "drafts", "attachments"]},
        suggested_command=f"auto-email-sender campaigns prepare-send {campaign_id}",
    )


def _campaign_resume_plan_stale_error(campaign_id: int) -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="活动状态、已授权邮件、草稿、附件或排程已发生变化，请重新生成恢复活动计划。",
        details={"changed_fields": ["campaign", "deliveries", "drafts", "attachments"]},
        suggested_command=f"auto-email-sender campaigns prepare-resume {campaign_id}",
    )


def _campaign_restore_send_plan_stale_error(
    campaign_id: int,
    item_id: int,
) -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="活动项的发送状态、草稿、附件或原定时间已发生变化，请重新生成恢复发送计划。",
        details={"changed_fields": ["campaign", "item", "draft", "attachments", "schedule"]},
        suggested_command=(
            "auto-email-sender campaigns prepare-restore-item-send "
            f"{campaign_id} {item_id}"
        ),
    )
