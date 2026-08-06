from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo

from app.core.time import as_utc_aware, local_now as get_local_now, utc_now

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.models import (
    BatchTask,
    BatchTaskStatus,
    EmailDirection,
    EmailLog,
    EmailTaskCancellationReason,
    EmailTaskSource,
    EmailTask,
    EmailTaskStatus,
    IdentityMaterial,
    IdentityProfile,
    MatchAnalysisRun,
    LLMProfile,
    OutreachTemplate,
    Professor,
)
from app.schemas.email_task import EmailTaskApprovalRequest, EmailTaskRewriteDraftRequest, EmailTaskScheduleRequest
from app.services import llm_runtime
from app.modules.communications.public import (
    EMAIL_TASK_RELATION_OPTIONS,
    RecentHistoryWindow as RecentHistoryWindow,
    build_recent_history_window as build_recent_history_window,
    extract_message_ids as extract_message_ids,
    get_cached_or_discover_sent_folder as get_cached_or_discover_sent_folder,
    is_imap_history_paused as is_imap_history_paused,
    is_imap_incremental_paused as is_imap_incremental_paused,
    load_email_task as _load_email_task,
    log_imap_history_progress as log_imap_history_progress,
    mark_imap_throttled as mark_imap_throttled,
    normalize_subject as normalize_subject,
    poll_for_replies_once as poll_for_replies_once,
    poll_identity_replies as poll_identity_replies,
    poll_imap_history_once as poll_imap_history_once,
    process_imap_fetched_messages as process_imap_fetched_messages,
    record_email_task_log as _record_email_task_log,
    repair_identity_replies as repair_identity_replies,
    sync_identity_history_once as sync_identity_history_once,
    sync_identity_history_poll_once as sync_identity_history_poll_once,
    sync_identity_imap_once as sync_identity_imap_once,
    sync_identity_incremental_once as sync_identity_incremental_once,
    sync_identity_incremental_poll_once as sync_identity_incremental_poll_once,
    sync_workspace_professor_replies as sync_workspace_professor_replies,
    transport as mail_runtime,
)
from app.modules.campaigns.public import (
    has_future_batch_window,
    is_batch_window_expired,
    is_datetime_in_batch_window,
    normalize_scheduled_dates,
)
from app.modules.campaigns.public import (
    DRAFT_GENERATION_SOURCE_LLM,
    DRAFT_GENERATION_SOURCE_TEMPLATE,
)
from app.modules.campaigns.public import sync_batch_task_completion
from app.modules.communications.public import MailAttachment
from app.services.match_results import (
    apply_match_result_snapshot_to_task,
    resolve_identity_match_scope,
    upsert_identity_professor_match_result,
)
from app.modules.identities.public import (
    build_material_download_name,
    ensure_material_extracted_text,
    material_can_be_primary,
)
from app.services.operation_logs import record_operation_log
from app.modules.campaigns.public import (
    get_default_outreach_template_for_identity,
    get_outreach_template,
)
from app.modules.campaigns.public import (
    OUTREACH_GENERATION_MODE_TEMPLATE,
    build_outreach_template_snapshot_config,
    build_send_template_context,
    get_outreach_template_defaults_validation_error,
    has_outreach_template_snapshot,
    render_outreach_template,
    render_template_with_context,
    resolve_outreach_template_config,
)
from app.services.rich_text import normalize_email_html, text_to_email_html
from app.modules.system.public import get_runtime_settings


TASK_RELATION_OPTIONS = EMAIL_TASK_RELATION_OPTIONS


class BatchDraftApprovalConflictError(ValueError):
    """Raised when the confirmed batch review snapshot is no longer current."""

DISPATCHABLE_EMAIL_TASK_STATUSES = (
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
)

STALE_SENDING_TASK_AFTER = timedelta(minutes=30)
SCHEDULED_BATCH_SEND_GRACE_PERIOD = timedelta(minutes=2)
DEFAULT_SEND_INTERVAL_MIN_SECONDS = 1
DEFAULT_SEND_INTERVAL_MAX_SECONDS = 5
INTERRUPTED_MATCH_ANALYSIS_RUN_ERROR = "匹配分析因桌面端进程中断而停止"
WORKSPACE_DRAFT_REWRITE_TIMEOUT = timedelta(minutes=5)
WORKSPACE_DRAFT_REWRITE_TIMEOUT_SECONDS = int(WORKSPACE_DRAFT_REWRITE_TIMEOUT.total_seconds())
WORKSPACE_DRAFT_REWRITE_TIMEOUT_MESSAGE = "AI 改写超时，请稍后重试"
WORKSPACE_DRAFT_REWRITE_INTERRUPTED_MESSAGE = "AI 改写已中断，请重试"

SAVE_DRAFT_ALLOWED_STATUSES = {
    EmailTaskStatus.DISCOVERED.value,
    EmailTaskStatus.MATCHED.value,
    EmailTaskStatus.DRAFT_FAILED.value,
    EmailTaskStatus.REVIEW_REQUIRED.value,
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
    EmailTaskStatus.SEND_FAILED.value,
}

MANUAL_DRAFT_CLAIMABLE_STATUSES = {
    EmailTaskStatus.DISCOVERED.value,
    EmailTaskStatus.MATCHED.value,
    EmailTaskStatus.DRAFT_FAILED.value,
    EmailTaskStatus.REVIEW_REQUIRED.value,
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
}


def _is_user_removed_batch_item(email_task: EmailTask) -> bool:
    return (
        email_task.status == EmailTaskStatus.CANCELED.value
        and email_task.cancellation_reason == EmailTaskCancellationReason.USER_REMOVED.value
    )


def _has_professor_match_evidence(professor: Professor) -> bool:
    return bool((professor.research_direction or "").strip()) or any(
        str(paper).strip() for paper in professor.recent_papers or []
    )


def _has_professor_research_direction(professor: Professor) -> bool:
    return bool((professor.research_direction or "").strip())


@dataclass(slots=True)
class MatchUsageSummary:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None


@dataclass(slots=True)
class MatchCalculationActionResult:
    professor_id: int
    identity_id: int
    match_source_identity_id: int
    llm_profile_id: int
    usage: MatchUsageSummary
    run_id: int | None = None


class MatchAnalysisAlreadyRunningError(RuntimeError):
    pass


class MatchCalculationCanceledError(RuntimeError):
    pass


async def process_pending_drafts_once(
    session_factory: async_sessionmaker[AsyncSession],
    limit: int = 5,
) -> int:
    return 0


async def dispatch_due_tasks_once(
    session_factory: async_sessionmaker[AsyncSession],
    limit: int = 10,
    *,
    now: datetime | None = None,
    local_timezone: tzinfo | None = None,
    count_identity_window_deferred: bool = False,
) -> int:
    now_utc, local_now = _resolve_dispatch_clocks(now, local_timezone)
    if limit <= 0:
        return 0

    await recover_stale_sending_tasks(session_factory, now=now_utc)

    async with session_factory() as session:
        await _expire_overdue_scheduled_batch_tasks(session, local_now)
        sent_counts: dict[int, int] = {}
        task_ids: list[int] = []
        selected_identity_ids: set[int] = set()
        deferred_identity_ids: set[int] = set()
        page_size = max(limit * 5, 10)
        offset = 0
        while len(task_ids) < limit:
            candidates = list(
                (
                    await session.execute(
                        select(EmailTask)
                        .options(
                            selectinload(EmailTask.batch_task),
                            selectinload(EmailTask.identity),
                        )
                        .join(BatchTask, EmailTask.batch_task_id == BatchTask.id, isouter=True)
                        .where(
                            or_(
                                EmailTask.status == EmailTaskStatus.APPROVED.value,
                                EmailTask.status == EmailTaskStatus.SCHEDULED.value,
                            ),
                            EmailTask.batch_send_canceled_at.is_(None),
                            or_(
                                EmailTask.scheduled_at.is_(None),
                                EmailTask.scheduled_at <= now_utc,
                            ),
                            or_(
                                BatchTask.id.is_(None),
                                and_(
                                    BatchTask.status == BatchTaskStatus.RUNNING.value,
                                    BatchTask.deleted_at.is_(None),
                                ),
                            ),
                        )
                        .order_by(
                            EmailTask.approved_at.asc(),
                            EmailTask.created_at.asc(),
                            EmailTask.id.asc(),
                        )
                        .offset(offset)
                        .limit(page_size),
                    )
                ).scalars()
            )
            if not candidates:
                break
            offset += len(candidates)

            for task in candidates:
                if len(task_ids) >= limit:
                    break
                batch_task = task.batch_task
                if not _batch_task_allows_dispatch(task, local_now):
                    continue
                if task.identity_id in selected_identity_ids:
                    continue
                if _is_identity_send_window_deferred(task.identity, now_utc):
                    deferred_identity_ids.add(task.identity_id)
                    continue
                if (
                    batch_task is not None
                    and batch_task.schedule_type == "scheduled"
                    and batch_task.emails_per_window is not None
                ):
                    count = sent_counts.get(batch_task.id)
                    if count is None:
                        count = await _batch_task_sent_count_on_date(session, batch_task.id, local_now)
                    if count >= batch_task.emails_per_window:
                        sent_counts[batch_task.id] = count
                        continue
                    sent_counts[batch_task.id] = count + 1
                task_ids.append(task.id)
                selected_identity_ids.add(task.identity_id)

            if len(candidates) < page_size:
                break

    processed = 0
    for task_id in task_ids:
        if await dispatch_email_task(session_factory, task_id, now=now_utc):
            processed += 1
    if count_identity_window_deferred:
        processed += len(deferred_identity_ids)
    return processed


async def _expire_overdue_scheduled_batch_tasks(
    session: AsyncSession,
    local_now: datetime,
) -> int:
    batch_tasks = list(
        (
            await session.execute(
                select(BatchTask)
                .options(selectinload(BatchTask.email_tasks))
                .where(
                    BatchTask.status == BatchTaskStatus.RUNNING.value,
                    BatchTask.schedule_type == "scheduled",
                    BatchTask.deleted_at.is_(None),
                ),
            )
        ).scalars().unique()
    )
    expired_count = 0
    completed_count = 0
    for batch_task in batch_tasks:
        if sync_batch_task_completion(batch_task, now=local_now.astimezone(UTC)):
            completed_count += 1
            continue
        if await expire_batch_task_if_needed(session, batch_task, local_now):
            expired_count += 1
    if expired_count > 0 or completed_count > 0:
        await session.commit()
    return expired_count


def _resolve_dispatch_clocks(
    now: datetime | None,
    local_timezone: tzinfo | None,
) -> tuple[datetime, datetime]:
    now_utc = as_utc_aware(now) if now is not None else utc_now()
    resolved_timezone = local_timezone or get_local_now().tzinfo or UTC
    return now_utc, now_utc.astimezone(resolved_timezone)


def _resolve_identity_send_interval_seconds(identity: IdentityProfile) -> tuple[int, int]:
    min_seconds = identity.send_interval_min or DEFAULT_SEND_INTERVAL_MIN_SECONDS
    max_seconds = identity.send_interval_max or DEFAULT_SEND_INTERVAL_MAX_SECONDS
    if min_seconds < 1:
        min_seconds = DEFAULT_SEND_INTERVAL_MIN_SECONDS
    if max_seconds < min_seconds:
        return DEFAULT_SEND_INTERVAL_MIN_SECONDS, DEFAULT_SEND_INTERVAL_MAX_SECONDS
    return min_seconds, max_seconds


def _is_identity_send_window_deferred(identity: IdentityProfile, now: datetime) -> bool:
    if identity.next_send_after is None:
        return False
    return as_utc_aware(identity.next_send_after) > as_utc_aware(now)


async def _reserve_identity_send_window(
    session: AsyncSession,
    identity: IdentityProfile,
    now: datetime,
    *,
    require_window_open: bool = True,
) -> bool:
    min_seconds, max_seconds = _resolve_identity_send_interval_seconds(identity)
    next_send_after = now + timedelta(
        seconds=random.uniform(min_seconds, max_seconds),
    )
    conditions = [IdentityProfile.id == identity.id]
    if require_window_open:
        conditions.append(
            or_(
                IdentityProfile.next_send_after.is_(None),
                IdentityProfile.next_send_after <= now,
            ),
        )
    result = await session.execute(
        update(IdentityProfile)
        .where(*conditions)
        .values(
            next_send_after=next_send_after,
            updated_at=now,
        )
        .execution_options(synchronize_session=False),
    )
    return result.rowcount == 1


def _batch_task_allows_dispatch(task: EmailTask, now: datetime) -> bool:
    if task.batch_send_canceled_at is not None:
        return False
    batch_task = task.batch_task
    if batch_task is None:
        return True
    if batch_task.status != BatchTaskStatus.RUNNING.value:
        return False
    if batch_task.schedule_type != "scheduled":
        return True
    if is_datetime_in_batch_window(
        now,
        scheduled_dates=batch_task.scheduled_dates,
        window_start_time=batch_task.window_start_time,
        window_end_time=batch_task.window_end_time,
    ):
        return True
    return _is_task_due_in_scheduled_batch_grace_period(task, now)


async def expire_batch_task_if_needed(
    session: AsyncSession,
    batch_task: BatchTask,
    local_now: datetime,
) -> bool:
    if batch_task.schedule_type != "scheduled":
        return False
    if batch_task.status != BatchTaskStatus.RUNNING.value:
        return False
    now_utc = local_now.astimezone(UTC)
    if not is_batch_window_expired(
        local_now,
        scheduled_dates=batch_task.scheduled_dates,
        window_end_time=batch_task.window_end_time,
    ):
        return False
    if any(
        _is_task_due_in_scheduled_batch_grace_period(email_task, local_now)
        for email_task in batch_task.email_tasks
        if not _is_user_removed_batch_item(email_task)
        and email_task.batch_send_canceled_at is None
    ):
        return False

    canceled_count = 0
    if any(
        _has_future_scheduled_at(
            email_task.scheduled_at,
            now_utc,
            scheduled_dates=batch_task.scheduled_dates,
            local_timezone=local_now.tzinfo,
        )
        for email_task in batch_task.email_tasks
        if not _is_user_removed_batch_item(email_task)
        if email_task.batch_send_canceled_at is None
        if email_task.status
        in {
            EmailTaskStatus.DISCOVERED.value,
            EmailTaskStatus.MATCHED.value,
            EmailTaskStatus.GENERATING_DRAFT.value,
            EmailTaskStatus.DRAFT_FAILED.value,
            EmailTaskStatus.REVIEW_REQUIRED.value,
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
        }
    ):
        return False

    for email_task in batch_task.email_tasks:
        if _is_user_removed_batch_item(email_task) or email_task.batch_send_canceled_at is not None:
            continue
        if email_task.status in {
            EmailTaskStatus.DISCOVERED.value,
            EmailTaskStatus.MATCHED.value,
            EmailTaskStatus.GENERATING_DRAFT.value,
            EmailTaskStatus.DRAFT_FAILED.value,
            EmailTaskStatus.REVIEW_REQUIRED.value,
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
        }:
            email_task.status = EmailTaskStatus.CANCELED.value
            email_task.cancellation_reason = EmailTaskCancellationReason.SCHEDULE_EXPIRED.value
            email_task.draft_generation_previous_status = None
            email_task.updated_at = now_utc
            canceled_count += 1

    if canceled_count == 0:
        return False

    batch_task.status = BatchTaskStatus.EXPIRED.value
    batch_task.updated_at = now_utc
    await record_operation_log(
        session,
        category="email",
        event_name="batch_task.expired",
        entity_type="batch_task",
        entity_id=str(batch_task.id),
        metadata={
            "canceled_count": canceled_count,
            "scheduled_dates": batch_task.scheduled_dates,
            "window_end_time": batch_task.window_end_time,
        },
    )
    return True


def _has_future_scheduled_at(
    scheduled_at: datetime | None,
    now_utc: datetime,
    *,
    scheduled_dates: list[str] | None,
    local_timezone: tzinfo | None,
) -> bool:
    if scheduled_at is None:
        return False
    scheduled_at_utc = as_utc_aware(scheduled_at)
    timezone = local_timezone or UTC
    scheduled_date = scheduled_at_utc.astimezone(timezone).date().isoformat()
    if scheduled_date not in set(normalize_scheduled_dates(scheduled_dates)):
        return False
    return scheduled_at_utc > now_utc


def _is_task_due_in_scheduled_batch_grace_period(
    task: EmailTask,
    local_now: datetime,
) -> bool:
    batch_task = task.batch_task
    if batch_task is None or batch_task.schedule_type != "scheduled":
        return False
    if task.status not in DISPATCHABLE_EMAIL_TASK_STATUSES:
        return False
    if task.scheduled_at is None:
        return False
    if not batch_task.window_end_time:
        return False

    timezone = local_now.tzinfo or UTC
    scheduled_at_utc = as_utc_aware(task.scheduled_at).astimezone(UTC)
    if scheduled_at_utc > local_now.astimezone(UTC):
        return False

    scheduled_local = scheduled_at_utc.astimezone(timezone)
    if not is_datetime_in_batch_window(
        scheduled_local,
        scheduled_dates=batch_task.scheduled_dates,
        window_start_time=batch_task.window_start_time,
        window_end_time=batch_task.window_end_time,
    ):
        return False

    end_clock = datetime.strptime(batch_task.window_end_time, "%H:%M").time()
    window_end = scheduled_local.replace(
        hour=end_clock.hour,
        minute=end_clock.minute,
        second=0,
        microsecond=0,
    )
    if scheduled_local < window_end - SCHEDULED_BATCH_SEND_GRACE_PERIOD:
        return False
    return window_end <= local_now <= window_end + SCHEDULED_BATCH_SEND_GRACE_PERIOD


async def _batch_task_sent_count_on_date(
    session: AsyncSession,
    batch_task_id: int,
    local_now: datetime,
) -> int:
    start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(UTC)
    end_utc = end_local.astimezone(UTC)
    return int(
        await session.scalar(
            select(func.count(EmailTask.id)).where(
                EmailTask.batch_task_id == batch_task_id,
                or_(
                    EmailTask.status == EmailTaskStatus.SENT.value,
                    EmailTask.status == EmailTaskStatus.REPLY_DETECTED.value,
                ),
                EmailTask.sent_at >= start_utc,
                EmailTask.sent_at < end_utc,
            ),
        )
        or 0
    )





async def recover_stale_sending_tasks(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    stale_after: timedelta = STALE_SENDING_TASK_AFTER,
    now: datetime | None = None,
) -> int:
    resolved_now = as_utc_aware(now) if now is not None else utc_now()
    cutoff = resolved_now - stale_after
    async with session_factory() as session:
        tasks = list(
            await session.scalars(
                select(EmailTask)
                .options(selectinload(EmailTask.batch_task))
                .where(
                    EmailTask.status == EmailTaskStatus.SENDING.value,
                    or_(
                        and_(
                            EmailTask.last_send_attempt_at.is_not(None),
                            EmailTask.last_send_attempt_at < cutoff,
                        ),
                        and_(
                            EmailTask.last_send_attempt_at.is_(None),
                            EmailTask.updated_at < cutoff,
                        ),
                    ),
                ),
            ),
        )
        for task in tasks:
            _restore_or_cancel_interrupted_send(task)
            task.updated_at = resolved_now
        await session.commit()
        return len(tasks)


async def recover_interrupted_match_analysis_runs(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> int:
    resolved_now = as_utc_aware(now) if now is not None else utc_now()
    async with session_factory() as session:
        runs = list(
            await session.scalars(
                select(MatchAnalysisRun).where(MatchAnalysisRun.status == "running"),
            ),
        )
        for run in runs:
            run.status = "failed"
            run.success = False
            run.error_kind = "interrupted"
            run.error_message = INTERRUPTED_MATCH_ANALYSIS_RUN_ERROR
            run.finished_at = resolved_now
        await session.commit()
        return len(runs)


async def generate_task_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    force: bool,
    ignore_batch_status: bool = False,
    automatic_batch: bool = False,
    require_running_batch: bool = False,
    llm_profile_id: int | None = None,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        task_identity = (task.professor_id, task.identity_id, task.llm_profile_id)
        runtime_llm_profile: LLMProfile | None = None
        if task.status == EmailTaskStatus.GENERATING_DRAFT.value and not automatic_batch:
            raise ValueError("草稿正在后台生成，请稍后刷新")
        if (
            task.batch_task
            and task.batch_task.status != BatchTaskStatus.RUNNING.value
            and not ignore_batch_status
        ):
            if automatic_batch or require_running_batch:
                _restore_or_cancel_interrupted_draft_generation(task)
                await session.commit()
            return task_identity

        if not automatic_batch:
            claim_result = await session.execute(
                update(EmailTask)
                .where(
                    EmailTask.id == task_id,
                    EmailTask.status.in_(MANUAL_DRAFT_CLAIMABLE_STATUSES),
                )
                .values(
                    status=EmailTaskStatus.GENERATING_DRAFT.value,
                    draft_generation_previous_status=task.status,
                    last_error=None,
                    updated_at=utc_now(),
                ),
            )
            if claim_result.rowcount != 1:
                await session.rollback()
                current_status = await session.scalar(
                    select(EmailTask.status).where(EmailTask.id == task_id),
                )
                if current_status == EmailTaskStatus.GENERATING_DRAFT.value:
                    raise ValueError("草稿正在后台生成，请稍后刷新")
                return task_identity
            await session.commit()
            task = await _load_email_task(session, task_id)
            if not task:
                raise ValueError(f"EmailTask {task_id} 不存在")
            task_identity = (task.professor_id, task.identity_id, task.llm_profile_id)

        batch_task = task.batch_task

        try:
            fallback_template = (
                None
                if _task_has_outreach_template_snapshot(task)
                else await get_default_outreach_template_for_identity(
                    session,
                    task.identity,
                )
            )
            outreach_config = _resolve_draft_generation_outreach_config(
                task,
                fallback_template=fallback_template,
            )
            if outreach_config.generation_mode == OUTREACH_GENERATION_MODE_TEMPLATE:
                template_subject = _normalize_nullable_text(outreach_config.subject_template)
                template_body = _normalize_nullable_text(outreach_config.body_text_template)
                detail = get_outreach_template_defaults_validation_error(
                    template_subject,
                    template_body,
                )
                if detail:
                    raise ValueError(detail)
                rendered = render_outreach_template(
                    task.identity,
                    task.professor,
                    subject_template=template_subject,
                    body_text_template=template_body,
                    body_html_template=outreach_config.body_html_template,
                )
                subject = rendered.subject
                body_text = rendered.body_text
                body_html = rendered.body_html
                usage = None
                provider_payload = {
                    "source": OUTREACH_GENERATION_MODE_TEMPLATE,
                    "placeholders": rendered.placeholders,
                    "usage": None,
                }
            else:
                if task.primary_material is None:
                    if force:
                        raise ValueError("请选择 AI 写信参考材料后再生成草稿")
                    return task.professor_id, task.identity_id, task.llm_profile_id
                if not _has_professor_research_direction(task.professor):
                    raise ValueError("请先补充导师研究方向，再使用 AI 生成草稿")
                ensure_material_extracted_text(task.primary_material)
                template_subject = _normalize_nullable_text(outreach_config.subject_template) or (
                    _normalize_nullable_text(batch_task.email_subject) if batch_task else None
                )
                template_body = _normalize_nullable_text(outreach_config.body_text_template) or (
                    _normalize_nullable_text(batch_task.email_body) if batch_task else None
                )
                template_body_html = _normalize_nullable_text(outreach_config.body_html_template)
                detail = get_outreach_template_defaults_validation_error(
                    template_subject,
                    template_body,
                )
                if detail:
                    raise ValueError(detail)

                runtime_llm_profile = await _resolve_runtime_llm_profile(session, task, llm_profile_id)
                task_identity = (task.professor_id, task.identity_id, runtime_llm_profile.id)
                runtime_settings = await get_runtime_settings(session)
                adaptation = await llm_runtime.ensure_llm_runtime_adaptation(session, runtime_llm_profile)
                rewrite_preferences = llm_runtime.DraftRewritePreferences(
                    draft_rewrite_intensity=runtime_settings.draft_rewrite_intensity,
                    draft_rewrite_tone=runtime_settings.draft_rewrite_tone,
                    draft_rewrite_formality=runtime_settings.draft_rewrite_formality,
                    draft_rewrite_length=runtime_settings.draft_rewrite_length,
                    draft_rewrite_specificity=runtime_settings.draft_rewrite_specificity,
                    draft_template_preservation=runtime_settings.draft_template_preservation,
                    draft_custom_instruction=runtime_settings.draft_custom_instruction,
                    intended_research_direction=runtime_settings.intended_research_direction,
                )
                generation = await llm_runtime.generate_draft_content(
                    identity=task.identity,
                    primary_material=task.primary_material,
                    llm_profile=runtime_llm_profile,
                    professor=task.professor,
                    available_materials=list(task.identity.materials),
                    custom_subject=template_subject,
                    custom_body=template_body,
                    custom_body_html=template_body_html,
                    max_tokens=runtime_settings.draft_max_tokens,
                    rewrite_preferences=rewrite_preferences,
                    session=session,
                    adaptation=adaptation,
                )
                subject = generation.result.subject
                body_text = generation.result.body_text
                body_html = generation.result.body_html
                usage = generation.usage
                provider_payload = {
                    "source": "llm",
                    "primary_material_id": task.primary_material_id,
                    "prompt_hash": generation.prompt_hash,
                    "stable_prefix_hash": generation.stable_prefix_hash,
                    "prompt_cache_key": generation.prompt_cache_key,
                    "usage": (
                        {
                            "prompt_tokens": usage.prompt_tokens,
                            "completion_tokens": usage.completion_tokens,
                            "cached_tokens": usage.cached_tokens,
                            "total_tokens": usage.total_tokens,
                        }
                        if usage is not None
                        else None
                    ),
                }
                if require_running_batch and task.batch_task_id is not None:
                    batch_status = await session.scalar(
                        select(BatchTask.status).where(BatchTask.id == task.batch_task_id),
                    )
                    if batch_status != BatchTaskStatus.RUNNING.value:
                        _restore_or_cancel_interrupted_draft_generation(task, batch_status=batch_status)
                        await session.commit()
                        return task.professor_id, task.identity_id, task.llm_profile_id
        except asyncio.CancelledError:
            await session.refresh(task)
            if _is_user_removed_batch_item(task):
                raise
            batch_status = (
                await session.scalar(select(BatchTask.status).where(BatchTask.id == task.batch_task_id))
                if task.batch_task_id is not None
                else None
            )
            _restore_or_cancel_interrupted_draft_generation(task, batch_status=batch_status)
            await session.commit()
            raise
        except llm_runtime.LLMRuntimeError as exc:
            await session.refresh(task)
            if _is_user_removed_batch_item(task):
                raise
            task.last_error = str(exc)
            if automatic_batch:
                task.status = EmailTaskStatus.DRAFT_FAILED.value
                task.draft_generation_previous_status = None
            else:
                task.status = task.draft_generation_previous_status or EmailTaskStatus.DISCOVERED.value
                task.draft_generation_previous_status = None
            task.updated_at = utc_now()
            await session.commit()
            if automatic_batch:
                return task.professor_id, task.identity_id, task.llm_profile_id
            raise
        except ValueError as exc:
            await session.refresh(task)
            if _is_user_removed_batch_item(task):
                raise
            task.last_error = str(exc)
            if automatic_batch:
                task.status = EmailTaskStatus.DRAFT_FAILED.value
                task.draft_generation_previous_status = None
            else:
                task.status = task.draft_generation_previous_status or EmailTaskStatus.DISCOVERED.value
                task.draft_generation_previous_status = None
            task.updated_at = utc_now()
            await session.commit()
            if automatic_batch:
                return task.professor_id, task.identity_id, task.llm_profile_id
            raise

        await session.refresh(task)
        if (
            task.status != EmailTaskStatus.GENERATING_DRAFT.value
            or task.cancellation_reason == EmailTaskCancellationReason.USER_REMOVED.value
        ):
            return task_identity

        if runtime_llm_profile is not None:
            task.llm_profile_id = runtime_llm_profile.id
        if not _task_has_outreach_template_snapshot(task):
            task.outreach_template_id = (
                fallback_template.id if fallback_template is not None else None
            )
            task.outreach_template_snapshot_version = 1
            task.outreach_generation_mode = outreach_config.generation_mode
            task.outreach_template_subject = _normalize_nullable_text(
                outreach_config.subject_template,
            )
            task.outreach_template_body_text = _normalize_nullable_text(
                outreach_config.body_text_template,
            )
            task.outreach_template_body_html = _normalize_nullable_text(
                outreach_config.body_html_template,
            )
        task.generated_subject = subject
        task.generated_content_text = body_text
        task.generated_content_html = body_html
        task.draft_generation_source = (
            DRAFT_GENERATION_SOURCE_TEMPLATE
            if outreach_config.generation_mode == OUTREACH_GENERATION_MODE_TEMPLATE
            else DRAFT_GENERATION_SOURCE_LLM
        )
        task.draft_fallback_reason = None
        task.status = EmailTaskStatus.REVIEW_REQUIRED.value
        task.draft_generation_previous_status = None
        task.updated_at = utc_now()
        task.last_error = None

        session.add(
            EmailLog(
                email_task_id=task.id,
                identity_id=task.identity_id,
                llm_profile_id=task.llm_profile_id,
                professor_id=task.professor_id,
                direction=EmailDirection.DRAFT.value,
                subject=subject,
                content=body_text,
                content_html=body_html,
                provider_payload=provider_payload,
            ),
        )
        await _record_email_task_log(
            session,
            task,
            "email_task.draft_generated",
            metadata={
                "generation_mode": outreach_config.generation_mode,
                "has_usage": usage is not None,
                "prompt_tokens": usage.prompt_tokens if usage is not None else None,
                "completion_tokens": usage.completion_tokens if usage is not None else None,
                "cached_tokens": usage.cached_tokens if usage is not None else None,
                "total_tokens": usage.total_tokens if usage is not None else None,
                "prompt_hash": provider_payload.get("prompt_hash"),
                "stable_prefix_hash": provider_payload.get("stable_prefix_hash"),
                "prompt_cache_key": provider_payload.get("prompt_cache_key"),
                "selected_material_ids": task.selected_material_ids,
            },
        )
        await session.commit()
        return task_identity


async def calculate_task_match(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    force: bool,
    ignore_batch_status: bool = False,
    cancel_requested: Callable[[], Awaitable[bool]] | None = None,
    llm_profile_id: int | None = None,
    match_source_identity_id: int | None = None,
) -> MatchCalculationActionResult:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        match_scope = await resolve_identity_match_scope(
            session,
            active_identity_id=task.identity_id,
            match_source_identity_id=match_source_identity_id,
        )
        runtime_llm_profile = await _resolve_runtime_llm_profile(session, task, llm_profile_id)
        if (
            task.batch_task
            and task.batch_task.status != BatchTaskStatus.RUNNING.value
            and not ignore_batch_status
        ):
            return _match_action_result(
                task,
                match_source_identity_id=match_scope.source_identity_id,
            )
        try:
            match_material = await _resolve_match_primary_material(
                session,
                match_scope.source_identity,
            )
        except ValueError:
            if force:
                raise
            return _match_action_result(
                task,
                match_source_identity_id=match_scope.source_identity_id,
            )
        ensure_material_extracted_text(match_material)
        if not _has_professor_match_evidence(task.professor):
            raise ValueError("缺少研究方向或近期论文，暂不能分析匹配度")
        if not force and task.status in {
            EmailTaskStatus.MATCHED.value,
            EmailTaskStatus.REVIEW_REQUIRED.value,
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            return _match_action_result(
                task,
                match_source_identity_id=match_scope.source_identity_id,
            )

        task.llm_profile_id = runtime_llm_profile.id
        runtime_settings = await get_runtime_settings(session)
        adaptation = await llm_runtime.ensure_llm_runtime_adaptation(session, runtime_llm_profile)
        run = await _create_running_match_analysis_run(
            session,
            task,
            match_identity=match_scope.source_identity,
            primary_material=match_material,
        )
        await session.commit()
        try:
            generation = await llm_runtime.generate_match_evaluation(
                identity=match_scope.source_identity,
                primary_material=match_material,
                llm_profile=runtime_llm_profile,
                professor=task.professor,
                available_materials=list(match_scope.source_identity.materials),
                intended_research_direction=runtime_settings.intended_research_direction,
                session=session,
                adaptation=adaptation,
            )
        except asyncio.CancelledError:
            _mark_match_analysis_run_failed(
                run,
                error_kind="canceled",
                error_message="匹配分析任务已取消",
            )
            task.updated_at = utc_now()
            await session.commit()
            raise
        except llm_runtime.LLMRuntimeError as exc:
            _mark_match_analysis_run_failed(
                run,
                error_kind="llm_runtime",
                error_message=str(exc),
                duration_ms=exc.duration_ms,
                endpoint_kind=exc.endpoint_kind,
                status_code=exc.status_code,
            )
            task.last_error = str(exc)
            task.updated_at = utc_now()
            await session.commit()
            return _match_action_result(
                task,
                match_source_identity_id=match_scope.source_identity_id,
                run_id=run.id,
            )
        except Exception as exc:
            _mark_match_analysis_run_failed(
                run,
                error_kind="unexpected",
                error_message=str(exc),
            )
            task.last_error = str(exc)
            task.updated_at = utc_now()
            await session.commit()
            raise

        if cancel_requested is not None and await cancel_requested():
            _mark_match_analysis_run_failed(
                run,
                error_kind="canceled",
                error_message="匹配分析任务已取消",
            )
            task.updated_at = utc_now()
            await session.commit()
            raise MatchCalculationCanceledError("匹配分析任务已取消")

        result = generation.result
        run.status = "succeeded"
        run.success = True
        run.match_score = result.match_score
        run.match_reason = result.match_reason
        run.fit_points = list(result.fit_points)
        run.risk_points = list(result.risk_points)
        run.match_keywords = list(result.keywords)
        run.prompt_tokens = generation.usage.prompt_tokens if generation.usage else None
        run.completion_tokens = generation.usage.completion_tokens if generation.usage else None
        run.total_tokens = generation.usage.total_tokens if generation.usage else None
        run.cached_tokens = generation.usage.cached_tokens if generation.usage else None
        run.duration_ms = generation.duration_ms
        run.endpoint_kind = generation.endpoint_kind
        run.status_code = generation.status_code
        run.prompt_hash = generation.prompt_hash
        run.stable_prefix_hash = generation.stable_prefix_hash
        run.error_kind = None
        run.error_message = None
        run.finished_at = utc_now()
        canonical_result = await upsert_identity_professor_match_result(
            session,
            identity_id=match_scope.source_identity_id,
            professor_id=task.professor_id,
            llm_profile_id=runtime_llm_profile.id,
            primary_material_id=match_material.id,
            source_email_task_id=task.id,
            analysis_run=run,
            match_score=result.match_score,
            match_reason=result.match_reason,
            fit_points=result.fit_points,
            risk_points=result.risk_points,
            match_keywords=result.keywords,
        )
        apply_match_result_snapshot_to_task(
            task,
            match_source_identity_id=match_scope.source_identity_id,
            match_score=result.match_score,
            match_reason=result.match_reason,
            fit_points=result.fit_points,
            risk_points=result.risk_points,
            match_keywords=result.keywords,
        )
        if task.status in {
            EmailTaskStatus.DISCOVERED.value,
            EmailTaskStatus.MATCHED.value,
            EmailTaskStatus.DRAFT_FAILED.value,
        }:
            task.status = EmailTaskStatus.MATCHED.value
        task.updated_at = utc_now()
        task.last_error = None
        await _record_email_task_log(
            session,
            task,
            "email_task.match_calculated",
            metadata={
                "match_analysis_run_id": run.id,
                "match_result_id": canonical_result.id,
                "match_source_identity_id": match_scope.source_identity_id,
                "match_score": result.match_score,
                "force": force,
            },
        )
        await session.commit()
        return _match_action_result(
            task,
            match_source_identity_id=match_scope.source_identity_id,
            usage=_match_usage_summary(generation.usage),
            run_id=run.id,
        )


async def regenerate_task_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    llm_profile_id: int | None = None,
) -> tuple[int, int, int]:
    return await generate_task_draft(
        session_factory,
        task_id,
        force=True,
        llm_profile_id=llm_profile_id,
    )


async def rewrite_task_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: EmailTaskRewriteDraftRequest,
) -> tuple[int, int, int]:
    source_subject = (payload.subject or "").strip() or None
    source_body_text = payload.body_text.strip()
    source_body_html = (payload.body_html or "").strip()
    if source_body_html:
        rendered_source_html = normalize_email_html(source_body_html)
        source_body_html = rendered_source_html.html
        if not source_body_text:
            source_body_text = rendered_source_html.text
    if not source_body_text and not source_body_html:
        raise ValueError("先写入正文或配置默认模板后再使用 AI 改写")

    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
            raise ValueError("AI 正在改写当前草稿，请稍后刷新")
        if task.primary_material is None:
            raise ValueError("请选择 AI 写信参考材料后再使用 AI 改写")
        if not _has_professor_research_direction(task.professor):
            raise ValueError("请先补充导师研究方向，再使用 AI 改写")
        await _validate_selected_material_ids(session, task.identity_id, payload.selected_material_ids)
        ensure_material_extracted_text(task.primary_material)

        runtime_llm_profile = await _resolve_runtime_llm_profile(session, task, payload.llm_profile_id)
        adaptation = await llm_runtime.ensure_llm_runtime_adaptation(session, runtime_llm_profile)
        runtime_settings = await get_runtime_settings(session)
        rewrite_preferences = llm_runtime.DraftRewritePreferences(
            draft_rewrite_intensity=runtime_settings.draft_rewrite_intensity,
            draft_rewrite_tone=runtime_settings.draft_rewrite_tone,
            draft_rewrite_formality=runtime_settings.draft_rewrite_formality,
            draft_rewrite_length=runtime_settings.draft_rewrite_length,
            draft_rewrite_specificity=runtime_settings.draft_rewrite_specificity,
            draft_template_preservation=runtime_settings.draft_template_preservation,
            draft_custom_instruction=runtime_settings.draft_custom_instruction,
            intended_research_direction=runtime_settings.intended_research_direction,
        )
        identity = task.identity
        primary_material = task.primary_material
        professor = task.professor
        available_materials = list(task.identity.materials)
        task_identity = (task.professor_id, task.identity_id, runtime_llm_profile.id)

        now = utc_now()
        previous_status = task.status or EmailTaskStatus.REVIEW_REQUIRED.value
        claim_result = await session.execute(
            update(EmailTask)
            .where(
                EmailTask.id == task.id,
                EmailTask.status != EmailTaskStatus.GENERATING_DRAFT.value,
            )
            .values(
                llm_profile_id=runtime_llm_profile.id,
                draft_generation_previous_status=previous_status,
                draft_generation_started_at=now,
                draft_rewrite_source_subject=source_subject,
                draft_rewrite_source_body_text=source_body_text,
                draft_rewrite_source_body_html=source_body_html or None,
                draft_rewrite_source_selected_material_ids=payload.selected_material_ids,
                selected_material_ids=payload.selected_material_ids,
                status=EmailTaskStatus.GENERATING_DRAFT.value,
                last_error=None,
                updated_at=now,
            )
        )
        if claim_result.rowcount != 1:
            await session.rollback()
            raise ValueError("AI 正在改写当前草稿，请稍后刷新")
        await session.commit()
        await session.refresh(task)

        try:
            generation = await asyncio.wait_for(
                llm_runtime.generate_draft_content(
                    identity=identity,
                    primary_material=primary_material,
                    llm_profile=runtime_llm_profile,
                    professor=professor,
                    available_materials=available_materials,
                    custom_subject=source_subject,
                    custom_body=source_body_text,
                    custom_body_html=source_body_html or None,
                    max_tokens=runtime_settings.draft_max_tokens,
                    rewrite_preferences=rewrite_preferences,
                    session=session,
                    adaptation=adaptation,
                ),
                timeout=WORKSPACE_DRAFT_REWRITE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            restore_workspace_rewrite_source(task, WORKSPACE_DRAFT_REWRITE_TIMEOUT_MESSAGE)
            await session.commit()
            raise ValueError(WORKSPACE_DRAFT_REWRITE_TIMEOUT_MESSAGE) from exc
        except llm_runtime.LLMRuntimeError as exc:
            await session.refresh(task)
            restore_workspace_rewrite_source(task, str(exc))
            await session.commit()
            raise
        except ValueError as exc:
            await session.refresh(task)
            restore_workspace_rewrite_source(task, str(exc))
            await session.commit()
            raise

        await session.refresh(task)
        if task.status != EmailTaskStatus.GENERATING_DRAFT.value:
            return task_identity

        result = generation.result
        usage = generation.usage
        task.generated_subject = result.subject
        task.generated_content_text = result.body_text
        task.generated_content_html = result.body_html
        task.draft_generation_source = DRAFT_GENERATION_SOURCE_LLM
        task.draft_fallback_reason = None
        task.approved_subject = None
        task.approved_body_text = None
        task.approved_body_html = None
        task.approved_at = None
        task.status = EmailTaskStatus.REVIEW_REQUIRED.value
        task.draft_generation_previous_status = None
        task.draft_generation_started_at = None
        task.updated_at = utc_now()
        task.last_error = None
        provider_payload = {
            "source": "workspace_rewrite",
            "primary_material_id": task.primary_material_id,
            "prompt_hash": generation.prompt_hash,
            "stable_prefix_hash": generation.stable_prefix_hash,
            "prompt_cache_key": generation.prompt_cache_key,
            "usage": (
                {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "cached_tokens": usage.cached_tokens,
                    "total_tokens": usage.total_tokens,
                }
                if usage is not None
                else None
            ),
        }
        session.add(
            EmailLog(
                email_task_id=task.id,
                identity_id=task.identity_id,
                llm_profile_id=task.llm_profile_id,
                professor_id=task.professor_id,
                direction=EmailDirection.DRAFT.value,
                subject=result.subject,
                content=result.body_text or "",
                content_html=result.body_html,
                provider_payload=provider_payload,
            ),
        )
        await _record_email_task_log(
            session,
            task,
            "email_task.draft_rewritten",
            metadata={
                "has_usage": usage is not None,
                "prompt_tokens": usage.prompt_tokens if usage is not None else None,
                "completion_tokens": usage.completion_tokens if usage is not None else None,
                "cached_tokens": usage.cached_tokens if usage is not None else None,
                "total_tokens": usage.total_tokens if usage is not None else None,
                "prompt_hash": generation.prompt_hash,
                "stable_prefix_hash": generation.stable_prefix_hash,
                "prompt_cache_key": generation.prompt_cache_key,
                "selected_material_ids": task.selected_material_ids,
            },
        )
        await session.commit()
        return task_identity


async def preview_task_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    llm_profile_id: int | None = None,
) -> llm_runtime.GeneratedDraftContent:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        runtime_llm_profile = await _resolve_runtime_llm_profile(session, task, llm_profile_id)

        fallback_template = (
            None
            if _task_has_outreach_template_snapshot(task)
            else await get_default_outreach_template_for_identity(
                session,
                task.identity,
            )
        )
        outreach_config = _resolve_draft_generation_outreach_config(
            task,
            fallback_template=fallback_template,
        )
        if outreach_config.generation_mode == OUTREACH_GENERATION_MODE_TEMPLATE:
            raise ValueError("模板模式不需要 AI 草稿预览")
        if task.primary_material is None:
            raise ValueError("请选择 AI 写信参考材料后再预览草稿")
        if not _has_professor_research_direction(task.professor):
            raise ValueError("请先补充导师研究方向，再使用 AI 生成草稿")

        ensure_material_extracted_text(task.primary_material)
        template_subject = _normalize_nullable_text(outreach_config.subject_template) or (
            _normalize_nullable_text(task.batch_task.email_subject) if task.batch_task else None
        )
        template_body = _normalize_nullable_text(outreach_config.body_text_template) or (
            _normalize_nullable_text(task.batch_task.email_body) if task.batch_task else None
        )
        template_body_html = _normalize_nullable_text(outreach_config.body_html_template)
        detail = get_outreach_template_defaults_validation_error(
            template_subject,
            template_body,
        )
        if detail:
            raise ValueError(detail)

        runtime_settings = await get_runtime_settings(session)
        adaptation = await llm_runtime.ensure_llm_runtime_adaptation(session, runtime_llm_profile)
        rewrite_preferences = llm_runtime.DraftRewritePreferences(
            draft_rewrite_intensity=runtime_settings.draft_rewrite_intensity,
            draft_rewrite_tone=runtime_settings.draft_rewrite_tone,
            draft_rewrite_formality=runtime_settings.draft_rewrite_formality,
            draft_rewrite_length=runtime_settings.draft_rewrite_length,
            draft_rewrite_specificity=runtime_settings.draft_rewrite_specificity,
            draft_template_preservation=runtime_settings.draft_template_preservation,
            draft_custom_instruction=runtime_settings.draft_custom_instruction,
            intended_research_direction=runtime_settings.intended_research_direction,
        )
        return await llm_runtime.generate_draft_content(
            identity=task.identity,
            primary_material=task.primary_material,
            llm_profile=runtime_llm_profile,
            professor=task.professor,
            available_materials=list(task.identity.materials),
            custom_subject=template_subject,
            custom_body=template_body,
            custom_body_html=template_body_html,
            rewrite_preferences=rewrite_preferences,
            session=session,
            adaptation=adaptation,
        )


def _match_usage_summary(
    usage: llm_runtime.ChatCompletionUsage | None,
) -> MatchUsageSummary:
    if usage is None:
        return MatchUsageSummary()
    return MatchUsageSummary(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        cached_tokens=usage.cached_tokens,
    )


def _match_action_result(
    task: EmailTask,
    *,
    match_source_identity_id: int | None = None,
    usage: MatchUsageSummary | None = None,
    run_id: int | None = None,
) -> MatchCalculationActionResult:
    return MatchCalculationActionResult(
        professor_id=task.professor_id,
        identity_id=task.identity_id,
        match_source_identity_id=match_source_identity_id or task.identity_id,
        llm_profile_id=task.llm_profile_id,
        usage=usage or MatchUsageSummary(),
        run_id=run_id,
    )


async def _create_running_match_analysis_run(
    session: AsyncSession,
    task: EmailTask,
    *,
    match_identity: IdentityProfile,
    primary_material: IdentityMaterial,
) -> MatchAnalysisRun:
    run = MatchAnalysisRun(
        email_task_id=task.id,
        professor_id=task.professor_id,
        identity_id=match_identity.id,
        llm_profile_id=task.llm_profile_id,
        primary_material_id=primary_material.id,
        status="running",
        success=False,
        started_at=utc_now(),
    )
    session.add(run)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise MatchAnalysisAlreadyRunningError("该任务正在分析中") from exc
    return run


async def _resolve_match_primary_material(
    session: AsyncSession,
    identity: IdentityProfile,
) -> IdentityMaterial:
    material = identity.current_primary_material
    if material is None:
        material_id = identity.current_primary_material_id
        if material_id is not None:
            material = await session.get(IdentityMaterial, material_id)
    if material is None:
        raise ValueError("请到个人页设置默认材料")
    if material.identity_id != identity.id:
        raise ValueError("匹配依据身份的默认材料归属不正确")
    if not material_can_be_primary(material):
        raise ValueError("个人页默认材料不支持匹配分析")
    return material


def _mark_match_analysis_run_failed(
    run: MatchAnalysisRun,
    *,
    error_kind: str,
    error_message: str,
    duration_ms: int | None = None,
    endpoint_kind: str | None = None,
    status_code: int | None = None,
) -> None:
    run.status = "failed"
    run.success = False
    run.error_kind = error_kind
    run.error_message = error_message
    run.duration_ms = duration_ms
    run.endpoint_kind = endpoint_kind
    run.status_code = status_code
    run.finished_at = utc_now()


async def calculate_task_match_once(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    llm_profile_id: int | None = None,
) -> MatchCalculationActionResult:
    return await calculate_task_match(
        session_factory,
        task_id,
        force=True,
        llm_profile_id=llm_profile_id,
    )


async def update_task_primary_material(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    primary_material_id: int,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        _ensure_task_not_generating_for_workspace_change(task)
        if task.status in {
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            raise ValueError("已发送或已回信任务不能再切换 AI 写信参考材料")

        material = await _validate_primary_material_id(session, task.identity_id, primary_material_id)
        task.primary_material_id = material.id
        task.approved_subject = None
        task.approved_body_text = None
        task.approved_body_html = None
        task.approved_at = None
        task.scheduled_at = None
        task.last_error = None
        task.updated_at = utc_now()
        await _record_email_task_log(
            session,
            task,
            "email_task.primary_material_updated",
            metadata={"primary_material_id": task.primary_material_id},
        )
        await session.commit()

    return await generate_task_draft(
        session_factory,
        task_id,
        force=True,
        ignore_batch_status=True,
    )


async def update_task_outreach_config(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    outreach_generation_mode: str,
    outreach_template_id: int | None = None,
    template_selection_explicit: bool = False,
    outreach_template_subject: str | None = None,
    outreach_template_body_text: str | None = None,
    outreach_template_body_html: str | None = None,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        _ensure_task_not_generating_for_workspace_change(task)
        if task.status in {
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            raise ValueError("正在发送、已发送或已回信任务不能再切换本次发信模式")

        if template_selection_explicit:
            selected_template = (
                await get_outreach_template(session, outreach_template_id)
                if outreach_template_id is not None
                else None
            )
        else:
            selected_template = await get_default_outreach_template_for_identity(
                session,
                task.identity,
            )
        previous_snapshot = (
            task.outreach_generation_mode,
            _normalize_nullable_text(task.outreach_template_subject),
            _normalize_nullable_text(task.outreach_template_body_text),
            _normalize_nullable_text(task.outreach_template_body_html),
        )
        if template_selection_explicit and selected_template is None:
            unlinked_snapshot = build_outreach_template_snapshot_config(
                generation_mode=outreach_generation_mode or task.outreach_generation_mode,
                subject_template=(
                    outreach_template_subject
                    if outreach_template_subject is not None
                    else task.outreach_template_subject
                ),
                body_text_template=(
                    outreach_template_body_text
                    if outreach_template_body_text is not None
                    else task.outreach_template_body_text
                ),
                body_html_template=(
                    outreach_template_body_html
                    if outreach_template_body_html is not None
                    else task.outreach_template_body_html
                ),
            )
            snapshot = {
                "outreach_generation_mode": unlinked_snapshot.generation_mode,
                "outreach_template_subject": _normalize_nullable_text(
                    unlinked_snapshot.subject_template,
                ),
                "outreach_template_body_text": _normalize_nullable_text(
                    unlinked_snapshot.body_text_template,
                ),
                "outreach_template_body_html": _normalize_nullable_text(
                    unlinked_snapshot.body_html_template,
                ),
            }
        else:
            snapshot = _build_task_outreach_snapshot(
                task.identity,
                template=selected_template,
                outreach_generation_mode=outreach_generation_mode,
                outreach_template_subject=outreach_template_subject,
                outreach_template_body_text=outreach_template_body_text,
                outreach_template_body_html=outreach_template_body_html,
                validate_ready=False,
            )
        next_snapshot = (
            snapshot["outreach_generation_mode"],
            snapshot["outreach_template_subject"],
            snapshot["outreach_template_body_text"],
            snapshot["outreach_template_body_html"],
        )
        provenance_only_unlink = bool(
            template_selection_explicit
            and selected_template is None
            and previous_snapshot == next_snapshot
        )
        task.outreach_generation_mode = next_snapshot[0]
        task.outreach_template_subject = next_snapshot[1]
        task.outreach_template_body_text = next_snapshot[2]
        task.outreach_template_body_html = next_snapshot[3]
        task.outreach_template_id = (
            selected_template.id if selected_template is not None else None
        )
        task.outreach_template_snapshot_version = 1
        if not provenance_only_unlink:
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
            task.draft_rewrite_source_subject = None
            task.draft_rewrite_source_body_text = None
            task.draft_rewrite_source_body_html = None
            task.draft_rewrite_source_selected_material_ids = None
            task.draft_generation_previous_status = None
            task.draft_generation_started_at = None
            if task.status != EmailTaskStatus.CANCELED.value:
                task.status = (
                    EmailTaskStatus.MATCHED.value
                    if _task_has_match_result(task)
                    else EmailTaskStatus.DISCOVERED.value
                )
            task.last_error = None
        task.updated_at = utc_now()
        await _record_email_task_log(
            session,
            task,
            "email_task.outreach_config_updated",
            metadata={"outreach_generation_mode": task.outreach_generation_mode},
        )
        await session.commit()
        return task.professor_id, task.identity_id, task.llm_profile_id


async def approve_and_send_task(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: EmailTaskApprovalRequest,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        _ensure_task_allows_approval(task)
        _ensure_batch_task_has_future_window(task)
        await _snapshot_approval(session, task, payload)
        task.status = EmailTaskStatus.APPROVED.value
        task.scheduled_at = None
        await _record_email_task_log(
            session,
            task,
            "email_task.approved",
            metadata={"selected_material_ids": task.selected_material_ids},
        )
        await session.commit()
        professor_id = task.professor_id
        identity_id = task.identity_id
        llm_profile_id = task.llm_profile_id

    await dispatch_email_task(
        session_factory,
        task_id,
        respect_identity_send_window=False,
    )
    return professor_id, identity_id, llm_profile_id


async def approve_generated_batch_drafts(
    session_factory: async_sessionmaker[AsyncSession],
    batch_task_id: int,
    item_ids: list[int],
) -> int:
    if not item_ids:
        raise BatchDraftApprovalConflictError("请至少选择一封待审核草稿。")

    async with session_factory() as session:
        tasks = list(
            (
                await session.execute(
                    select(EmailTask)
                    .options(selectinload(EmailTask.batch_task))
                    .where(
                        EmailTask.id.in_(item_ids),
                        EmailTask.batch_task_id == batch_task_id,
                        EmailTask.source == EmailTaskSource.BATCH.value,
                    )
                    .order_by(EmailTask.id.asc())
                    .with_for_update(),
                )
            ).scalars().unique()
        )
        if len(tasks) != len(item_ids):
            raise BatchDraftApprovalConflictError(
                "待审核草稿列表已发生变化，请刷新后重新确认。",
            )

        batch_task = tasks[0].batch_task
        if (
            batch_task is None
            or batch_task.deleted_at is not None
            or batch_task.status
            not in {
                BatchTaskStatus.RUNNING.value,
                BatchTaskStatus.PAUSED.value,
            }
        ):
            raise BatchDraftApprovalConflictError(
                "批量任务状态已发生变化，请刷新后重新确认。",
            )

        for task in tasks:
            if (
                task.status != EmailTaskStatus.REVIEW_REQUIRED.value
                or task.batch_send_canceled_at is not None
                or not (
                    (task.generated_content_text or "").strip()
                    or (task.generated_content_html or "").strip()
                )
            ):
                raise BatchDraftApprovalConflictError(
                    "待审核草稿列表已发生变化，请刷新后重新确认。",
                )
            _ensure_task_allows_legacy_manual_actions(task)
            _ensure_task_allows_approval(task)
            _ensure_batch_task_has_future_window(task)

        for task in tasks:
            await _snapshot_approval(
                session,
                task,
                EmailTaskApprovalRequest(
                    subject=task.generated_subject,
                    body_text=task.generated_content_text or "",
                    body_html=task.generated_content_html,
                    selected_material_ids=task.selected_material_ids,
                ),
            )
            if _is_scheduled_batch_task(task) and task.scheduled_at is not None:
                task.status = EmailTaskStatus.SCHEDULED.value
            else:
                task.status = EmailTaskStatus.APPROVED.value
                task.scheduled_at = None
            await _record_email_task_log(
                session,
                task,
                "email_task.approved",
                metadata={
                    "selected_material_ids": task.selected_material_ids,
                    "approval_method": "bulk",
                },
            )

        await record_operation_log(
            session,
            category="email",
            event_name="batch_task.drafts_bulk_approved",
            entity_type="batch_task",
            entity_id=str(batch_task_id),
            metadata={"approved_count": len(tasks)},
        )
        await session.commit()
        return len(tasks)


async def approve_draft_task(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: EmailTaskApprovalRequest,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        _ensure_task_allows_approval(task)
        _ensure_batch_task_has_future_window(task)
        await _snapshot_approval(session, task, payload)
        if _is_scheduled_batch_task(task) and task.scheduled_at is not None:
            task.status = EmailTaskStatus.SCHEDULED.value
        else:
            task.status = EmailTaskStatus.APPROVED.value
            task.scheduled_at = None
        await _record_email_task_log(
            session,
            task,
            "email_task.approved",
            metadata={"selected_material_ids": task.selected_material_ids},
        )
        await session.commit()
        return task.professor_id, task.identity_id, task.llm_profile_id


async def save_task_draft(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: EmailTaskApprovalRequest,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        _ensure_task_allows_draft_save(task)
        await _snapshot_saved_draft(session, task, payload)
        await _record_email_task_log(
            session,
            task,
            "email_task.draft_saved",
            metadata={"selected_material_ids": task.selected_material_ids},
        )
        await session.commit()
        return task.professor_id, task.identity_id, task.llm_profile_id


async def approve_and_schedule_task(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: EmailTaskScheduleRequest,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        _ensure_task_allows_approval(task)
        _ensure_batch_task_has_future_window(task)
        await _snapshot_approval(session, task, payload)
        task.status = EmailTaskStatus.SCHEDULED.value
        task.scheduled_at = payload.scheduled_at.astimezone(UTC)
        task.updated_at = utc_now()
        await _record_email_task_log(
            session,
            task,
            "email_task.approved_and_scheduled",
            metadata={
                "scheduled_at": task.scheduled_at.isoformat() if task.scheduled_at else None,
                "selected_material_ids": task.selected_material_ids,
            },
        )
        await session.commit()
        return task.professor_id, task.identity_id, task.llm_profile_id


async def cancel_scheduled_task(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        _ensure_task_allows_legacy_manual_actions(task)
        task.status = EmailTaskStatus.REVIEW_REQUIRED.value
        task.scheduled_at = None
        task.updated_at = utc_now()
        await _record_email_task_log(session, task, "email_task.schedule_canceled")
        await session.commit()
        return task.professor_id, task.identity_id, task.llm_profile_id


async def continue_task_manually(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        await _ensure_no_manual_child_exists(session, task.id)
        if (
            task.status != EmailTaskStatus.CANCELED.value
            or task.cancellation_reason != EmailTaskCancellationReason.BATCH_STOPPED.value
        ):
            raise ValueError("只有 canceled 且 cancellation_reason 为 batch_stopped 的任务支持继续联系")

        professor_id = task.professor_id
        identity_id = task.identity_id
        llm_profile_id = task.llm_profile_id
        parent_task_id = task.id
        fallback_template = (
            None
            if _task_has_outreach_template_snapshot(task)
            else await get_default_outreach_template_for_identity(
                session,
                task.identity,
            )
        )
        child_task = _create_manual_child_task(
            task,
            reuse_existing_draft=True,
            fallback_template=fallback_template,
        )
        session.add(child_task)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            existing_child_id = await _get_manual_child_task_id(session, parent_task_id)
            if existing_child_id is not None:
                return professor_id, identity_id, llm_profile_id
            raise
        await _record_email_task_log(
            session,
            child_task,
            "email_task.continued_manually",
            metadata={"parent_task_id": parent_task_id},
        )
        await _commit_manual_child_task(session)
        return professor_id, identity_id, llm_profile_id


async def start_follow_up_task(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
) -> tuple[int, int, int]:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        await _ensure_no_manual_child_exists(session, task.id)
        if task.status not in {
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            raise ValueError("只有 sent 或 reply_detected 的任务支持发起跟进")

        professor_id = task.professor_id
        identity_id = task.identity_id
        llm_profile_id = task.llm_profile_id
        parent_task_id = task.id
        fallback_template = (
            None
            if _task_has_outreach_template_snapshot(task)
            else await get_default_outreach_template_for_identity(
                session,
                task.identity,
            )
        )
        child_task = _create_manual_child_task(
            task,
            reuse_existing_draft=False,
            minimum_status=EmailTaskStatus.MATCHED.value,
            fallback_template=fallback_template,
        )
        session.add(child_task)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            existing_child_id = await _get_manual_child_task_id(session, parent_task_id)
            if existing_child_id is not None:
                return professor_id, identity_id, llm_profile_id
            raise
        await _record_email_task_log(
            session,
            child_task,
            "email_task.follow_up_started",
            metadata={"parent_task_id": parent_task_id},
        )
        await _commit_manual_child_task(session)
        return professor_id, identity_id, llm_profile_id


async def dispatch_email_task(

    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    now: datetime | None = None,
    respect_identity_send_window: bool = True,
) -> bool:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        if task.status not in DISPATCHABLE_EMAIL_TASK_STATUSES:
            return False
        if task.batch_send_canceled_at is not None:
            return False
        if task.batch_task and task.batch_task.status != BatchTaskStatus.RUNNING.value:
            return False

        claimed_at = as_utc_aware(now) if now is not None else utc_now()
        if _is_task_scheduled_for_future(task, claimed_at):
            return False
        if not await _reserve_identity_send_window(
            session,
            task.identity,
            claimed_at,
            require_window_open=respect_identity_send_window,
        ):
            await session.rollback()
            return False
        claim_result = await session.execute(
            update(EmailTask)
            .where(
                EmailTask.id == task_id,
                EmailTask.status.in_(DISPATCHABLE_EMAIL_TASK_STATUSES),
                EmailTask.batch_send_canceled_at.is_(None),
                or_(
                    EmailTask.scheduled_at.is_(None),
                    EmailTask.scheduled_at <= claimed_at,
                ),
            )
            .values(
                status=EmailTaskStatus.SENDING.value,
                last_send_attempt_at=claimed_at,
                retry_count=func.coalesce(EmailTask.retry_count, 0) + 1,
                updated_at=claimed_at,
            )
            .execution_options(synchronize_session=False),
        )
        if claim_result.rowcount != 1:
            await session.rollback()
            return False
        await session.commit()
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        if task.batch_task and task.batch_task.status != BatchTaskStatus.RUNNING.value:
            if task.batch_task.status == BatchTaskStatus.PAUSED.value:
                task.status = EmailTaskStatus.APPROVED.value
            elif task.batch_task.status == BatchTaskStatus.EXPIRED.value:
                task.status = EmailTaskStatus.CANCELED.value
                task.cancellation_reason = EmailTaskCancellationReason.SCHEDULE_EXPIRED.value
            else:
                task.status = EmailTaskStatus.CANCELED.value
                task.cancellation_reason = EmailTaskCancellationReason.BATCH_STOPPED.value
            task.updated_at = utc_now()
            await session.commit()
            return True

        subject_template = task.approved_subject or task.generated_subject
        body_text_template = task.approved_body_text or task.generated_content_text
        body_html_template = task.approved_body_html or task.generated_content_html
        context = build_send_template_context(
            task.identity,
            task.professor,
            local_timezone=get_local_now().tzinfo,
        )
        subject = render_template_with_context(subject_template, context).strip()
        body_text = render_template_with_context(body_text_template, context).strip()
        body_html = (
            render_template_with_context(body_html_template, context)
            if body_html_template
            else None
        )
        if not subject or not body_text:
            task.status = EmailTaskStatus.SEND_FAILED.value
            task.last_error = "任务缺少可发送的主题或正文"
            task.updated_at = utc_now()
            await session.commit()
            return True

        attachments = await _resolve_selected_materials(
            session,
            task.identity_id,
            task.selected_material_ids,
        )

        try:
            result = await mail_runtime.send_email(
                identity=task.identity,
                professor=task.professor,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                attachments=attachments,
            )
            rfc_message_id = result.message_id
            provider_payload = result.provider_payload

            task.status = EmailTaskStatus.SENT.value
            task.sent_at = utc_now()
            task.last_rfc_message_id = rfc_message_id
            task.last_error = None
            task.updated_at = utc_now()
            session.add(
                EmailLog(
                    email_task_id=task.id,
                    identity_id=task.identity_id,
                    llm_profile_id=task.llm_profile_id,
                    professor_id=task.professor_id,
                    direction=EmailDirection.SENT.value,
                    subject=subject,
                    content=body_text,
                    content_html=body_html,
                    rfc_message_id=rfc_message_id,
                    provider_payload=provider_payload,
                ),
            )
            await _record_email_task_log(
                session,
                task,
                "email_task.sent",
                metadata={
                    "rfc_message_id": rfc_message_id,
                    "retry_count": task.retry_count,
                    "attachment_count": len(attachments),
                },
            )
        except mail_runtime.MailRuntimeError as exc:
            task.status = EmailTaskStatus.SEND_FAILED.value
            task.last_error = str(exc)
            task.updated_at = utc_now()
            session.add(
                EmailLog(
                    email_task_id=task.id,
                    identity_id=task.identity_id,
                    llm_profile_id=task.llm_profile_id,
                    professor_id=task.professor_id,
                    direction=EmailDirection.SENT.value,
                    subject=subject,
                    content=body_text,
                    content_html=body_html,
                    failure_summary=str(exc),
                ),
            )
            await _record_email_task_log(
                session,
                task,
                "email_task.send_failed",
                level="warning",
                message=str(exc),
                metadata={
                    "retry_count": task.retry_count,
                    "attachment_count": len(attachments),
                },
            )

        await session.commit()
        return True
































































































































async def _snapshot_approval(
    session: AsyncSession,
    task: EmailTask,
    payload: EmailTaskApprovalRequest,
) -> None:
    await _validate_selected_material_ids(session, task.identity_id, payload.selected_material_ids)

    task.approved_subject = (payload.subject or task.generated_subject or "").strip()
    if payload.body_html:
        rendered = normalize_email_html(payload.body_html)
    else:
        rendered = text_to_email_html(payload.body_text)
    task.approved_body_text = rendered.text
    task.approved_body_html = rendered.html
    if payload.selected_material_ids is not None:
        task.selected_material_ids = payload.selected_material_ids
    task.approved_at = utc_now()
    task.updated_at = utc_now()
    task.last_error = None


async def _snapshot_saved_draft(
    session: AsyncSession,
    task: EmailTask,
    payload: EmailTaskApprovalRequest,
) -> None:
    await _validate_selected_material_ids(session, task.identity_id, payload.selected_material_ids)

    body_text = payload.body_text.strip()
    body_html = payload.body_html or ""
    if not body_text:
        normalized_body_html = ""
    elif body_html.strip():
        rendered = normalize_email_html(body_html)
        body_text = rendered.text
        normalized_body_html = rendered.html
    else:
        normalized_body_html = text_to_email_html(body_text).html
    task.approved_subject = (payload.subject or "").strip()
    task.approved_body_text = body_text
    task.approved_body_html = normalized_body_html
    if payload.selected_material_ids is not None:
        task.selected_material_ids = payload.selected_material_ids
    task.approved_at = utc_now()
    task.updated_at = utc_now()
    task.last_error = None


def restore_workspace_rewrite_source(
    task: EmailTask,
    error_message: str,
    *,
    now: datetime | None = None,
) -> None:
    source_body_text = task.draft_rewrite_source_body_text or ""
    source_body_html = task.draft_rewrite_source_body_html
    if source_body_text and not source_body_html:
        source_body_html = text_to_email_html(source_body_text).html
    task.approved_subject = task.draft_rewrite_source_subject
    task.approved_body_text = source_body_text
    task.approved_body_html = source_body_html
    task.selected_material_ids = task.draft_rewrite_source_selected_material_ids
    task.status = task.draft_generation_previous_status or EmailTaskStatus.REVIEW_REQUIRED.value
    task.draft_generation_previous_status = None
    task.draft_generation_started_at = None
    task.updated_at = now or utc_now()
    task.last_error = error_message


async def _validate_primary_material_id(
    session: AsyncSession,
    identity_id: int,
    primary_material_id: int,
) -> IdentityMaterial:
    material = await session.scalar(
        select(IdentityMaterial).where(
            IdentityMaterial.identity_id == identity_id,
            IdentityMaterial.id == primary_material_id,
        ),
    )
    if not material:
        raise ValueError("AI 写信参考材料不属于当前身份")
    if not material_can_be_primary(material):
        raise ValueError("当前材料不支持作为 AI 写信参考材料")
    return material


async def _validate_selected_material_ids(
    session: AsyncSession,
    identity_id: int,
    material_ids: list[int] | None,
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
    material_ids: list[int] | None,
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




async def _resolve_runtime_llm_profile(
    session: AsyncSession,
    task: EmailTask,
    llm_profile_id: int | None,
) -> LLMProfile:
    if llm_profile_id is None or llm_profile_id == task.llm_profile_id:
        return task.llm_profile
    profile = await session.get(LLMProfile, llm_profile_id)
    if profile is None:
        raise ValueError("未找到 LLM 配置")
    return profile



async def _ensure_no_manual_child_exists(session: AsyncSession, parent_task_id: int) -> None:
    existing_child_id = await session.scalar(
        select(EmailTask.id).where(EmailTask.parent_task_id == parent_task_id).limit(1),
    )
    if existing_child_id is not None:
        raise ValueError("该任务已创建过手动子任务，不能重复派生")


async def _get_manual_child_task_id(session: AsyncSession, parent_task_id: int) -> int | None:
    return await session.scalar(
        select(EmailTask.id).where(EmailTask.parent_task_id == parent_task_id).limit(1),
    )

async def _commit_manual_child_task(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ValueError("该任务已创建过手动子任务，不能重复派生") from exc




def _restore_or_cancel_interrupted_draft_generation(
    task: EmailTask,
    *,
    batch_status: str | None = None,
) -> None:
    resolved_batch_status = batch_status or (task.batch_task.status if task.batch_task else None)
    if task.batch_task is None:
        task.status = task.draft_generation_previous_status or EmailTaskStatus.DISCOVERED.value
        task.cancellation_reason = None
    elif resolved_batch_status == BatchTaskStatus.PAUSED.value:
        task.status = task.draft_generation_previous_status or EmailTaskStatus.DISCOVERED.value
    elif resolved_batch_status == BatchTaskStatus.EXPIRED.value:
        task.status = EmailTaskStatus.CANCELED.value
        task.cancellation_reason = EmailTaskCancellationReason.SCHEDULE_EXPIRED.value
    else:
        task.status = EmailTaskStatus.CANCELED.value
        task.cancellation_reason = EmailTaskCancellationReason.BATCH_STOPPED.value
    task.draft_generation_previous_status = None
    task.updated_at = utc_now()


def _restore_or_cancel_interrupted_send(task: EmailTask) -> None:
    batch_status = task.batch_task.status if task.batch_task else None
    if batch_status == BatchTaskStatus.EXPIRED.value:
        task.status = EmailTaskStatus.CANCELED.value
        task.cancellation_reason = EmailTaskCancellationReason.SCHEDULE_EXPIRED.value
    elif batch_status == BatchTaskStatus.STOPPED.value:
        task.status = EmailTaskStatus.CANCELED.value
        task.cancellation_reason = EmailTaskCancellationReason.BATCH_STOPPED.value
    elif batch_status == BatchTaskStatus.PAUSED.value:
        task.status = EmailTaskStatus.APPROVED.value
        task.cancellation_reason = None
    else:
        task.status = (
            EmailTaskStatus.SCHEDULED.value
            if task.scheduled_at is not None
            else EmailTaskStatus.APPROVED.value
        )
        task.cancellation_reason = None


def _ensure_batch_task_has_future_window(task: EmailTask) -> None:
    batch_task = task.batch_task
    if batch_task is None or batch_task.schedule_type != "scheduled":
        return

    local_now = get_local_now()
    if batch_task.status == BatchTaskStatus.EXPIRED.value or not has_future_batch_window(
        local_now,
        scheduled_dates=batch_task.scheduled_dates,
        window_end_time=batch_task.window_end_time,
    ):
        raise ValueError("当前批量任务的发送窗口已全部过期，请重新安排发送时间后再审核发送。")


def _is_scheduled_batch_task(task: EmailTask) -> bool:
    return task.batch_task is not None and task.batch_task.schedule_type == "scheduled"


def _is_task_scheduled_for_future(task: EmailTask, now: datetime) -> bool:
    if task.scheduled_at is None:
        return False
    scheduled_at = as_utc_aware(task.scheduled_at)
    return scheduled_at.astimezone(UTC) > now.astimezone(UTC)


def _resolve_task_outreach_config(task: EmailTask):
    return build_outreach_template_snapshot_config(
        generation_mode=task.outreach_generation_mode,
        subject_template=task.outreach_template_subject,
        body_text_template=task.outreach_template_body_text,
        body_html_template=task.outreach_template_body_html,
    )


def _task_has_outreach_template_snapshot(task: EmailTask) -> bool:
    return has_outreach_template_snapshot(
        snapshot_version=task.outreach_template_snapshot_version,
        template_id=task.outreach_template_id,
        subject_template=task.outreach_template_subject,
        body_text_template=task.outreach_template_body_text,
        body_html_template=task.outreach_template_body_html,
    )


def _resolve_draft_generation_outreach_config(
    task: EmailTask,
    *,
    fallback_template: OutreachTemplate | None = None,
):
    if _task_has_outreach_template_snapshot(task):
        return _resolve_task_outreach_config(task)

    return resolve_outreach_template_config(
        task.identity,
        template=fallback_template,
        generation_mode=task.outreach_generation_mode,
    )


def _build_task_outreach_snapshot(
    identity: IdentityProfile,
    *,
    template: OutreachTemplate | None = None,
    outreach_generation_mode: str | None = None,
    outreach_template_subject: str | None = None,
    outreach_template_body_text: str | None = None,
    outreach_template_body_html: str | None = None,
    fallback_task: EmailTask | None = None,
    validate_ready: bool = True,
) -> dict[str, str | None]:
    resolved = resolve_outreach_template_config(
        identity,
        template=template,
        generation_mode=(
            outreach_generation_mode
            if outreach_generation_mode is not None
            else fallback_task.outreach_generation_mode if fallback_task is not None else None
        ),
        subject_template=(
            outreach_template_subject
            if outreach_template_subject is not None
            else (
                fallback_task.outreach_template_subject
                if fallback_task is not None and template is None
                else None
            )
        ),
        body_text_template=(
            outreach_template_body_text
            if outreach_template_body_text is not None
            else (
                fallback_task.outreach_template_body_text
                if fallback_task is not None and template is None
                else None
            )
        ),
        body_html_template=(
            outreach_template_body_html
            if outreach_template_body_html is not None
            else (
                fallback_task.outreach_template_body_html
                if fallback_task is not None and template is None
                else None
            )
        ),
    )
    body_text = _normalize_nullable_text(resolved.body_text_template)
    body_html = _normalize_nullable_text(resolved.body_html_template)
    if validate_ready:
        detail = get_outreach_template_defaults_validation_error(
            resolved.subject_template,
            resolved.body_text_template,
        )
        if detail:
            raise ValueError(detail)
    return {
        "outreach_generation_mode": resolved.generation_mode,
        "outreach_template_subject": _normalize_nullable_text(resolved.subject_template),
        "outreach_template_body_text": body_text,
        "outreach_template_body_html": body_html,
    }


def _normalize_nullable_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _derive_manual_child_status(
    task: EmailTask,
    *,
    reuse_existing_draft: bool,
    minimum_status: str | None = None,
) -> str:
    if reuse_existing_draft and _task_has_reusable_draft(task):
        return EmailTaskStatus.REVIEW_REQUIRED.value

    status = EmailTaskStatus.MATCHED.value if _task_has_match_result(task) else EmailTaskStatus.DISCOVERED.value
    if minimum_status == EmailTaskStatus.MATCHED.value and status == EmailTaskStatus.DISCOVERED.value:
        return EmailTaskStatus.MATCHED.value
    return status


def _create_manual_child_task(
    task: EmailTask,
    *,
    reuse_existing_draft: bool,
    minimum_status: str | None = None,
    fallback_template: OutreachTemplate | None = None,
) -> EmailTask:
    now = utc_now()
    outreach_config = _resolve_draft_generation_outreach_config(
        task,
        fallback_template=fallback_template,
    )
    return EmailTask(
        source=EmailTaskSource.MANUAL.value,
        batch_task_id=None,
        parent_task_id=task.id,
        identity_id=task.identity_id,
        llm_profile_id=task.llm_profile_id,
        professor_id=task.professor_id,
        primary_material_id=task.primary_material_id,
        status=_derive_manual_child_status(
            task,
            reuse_existing_draft=reuse_existing_draft,
            minimum_status=minimum_status,
        ),
        cancellation_reason=None,
        match_source_identity_id=task.match_source_identity_id,
        match_score=task.match_score,
        match_reason=task.match_reason,
        generated_subject=task.generated_subject if reuse_existing_draft else None,
        generated_content_text=task.generated_content_text if reuse_existing_draft else None,
        generated_content_html=task.generated_content_html if reuse_existing_draft else None,
        outreach_generation_mode=outreach_config.generation_mode,
        outreach_template_subject=outreach_config.subject_template,
        outreach_template_body_text=outreach_config.body_text_template,
        outreach_template_body_html=outreach_config.body_html_template,
        outreach_template_id=(
            task.outreach_template_id
            if _task_has_outreach_template_snapshot(task)
            else fallback_template.id if fallback_template is not None else None
        ),
        outreach_template_snapshot_version=1,
        selected_material_ids=(
            list(task.selected_material_ids)
            if task.selected_material_ids is not None
            else None
        ),
        approved_at=None,
        fit_points=list(task.fit_points) if task.fit_points else [],
        risk_points=list(task.risk_points) if task.risk_points else [],
        match_keywords=list(task.match_keywords) if task.match_keywords else [],
        approved_subject=task.approved_subject if reuse_existing_draft else None,
        approved_body_text=task.approved_body_text if reuse_existing_draft else None,
        approved_body_html=task.approved_body_html if reuse_existing_draft else None,
        scheduled_at=None,
        last_send_attempt_at=None,
        sent_at=None,
        last_rfc_message_id=None,
        retry_count=0,
        is_read=False,
        is_replied=False,
        last_error=None,
        created_at=now,
        updated_at=now,
    )


def _task_has_reusable_draft(task: EmailTask) -> bool:
    return any(
        _normalize_nullable_text(value) is not None
        for value in [
            task.generated_subject,
            task.generated_content_text,
            task.generated_content_html,
            task.approved_subject,
            task.approved_body_text,
            task.approved_body_html,
        ]
    )


def _task_has_match_result(task: EmailTask) -> bool:
    return task.match_score is not None and bool(task.match_reason)


def _ensure_task_allows_legacy_manual_actions(task: EmailTask) -> None:
    if task.batch_send_canceled_at is not None:
        raise ValueError("该导师已取消发送，请先恢复发送")
    if (
        task.status == EmailTaskStatus.CANCELED.value
        and task.cancellation_reason == EmailTaskCancellationReason.BATCH_STOPPED.value
    ):
        raise ValueError("该任务已因批量任务停止而取消，请先“作为单独联系继续”后再执行此操作")


def _ensure_task_not_generating_for_workspace_change(task: EmailTask) -> None:
    if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
        raise ValueError("AI 正在改写当前草稿，请等待完成后再修改。")


def _ensure_task_allows_approval(task: EmailTask) -> None:
    if task.batch_send_canceled_at is not None:
        raise ValueError("该导师已取消发送，请先恢复发送")
    if (
        task.status == EmailTaskStatus.CANCELED.value
        and task.cancellation_reason == EmailTaskCancellationReason.USER_REMOVED.value
    ):
        raise ValueError("该草稿已从批量任务中移除，不能再审核或发送")
    if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
        raise ValueError("AI 正在改写当前草稿，请等待完成后再发送。")


def _ensure_task_allows_draft_save(task: EmailTask) -> None:
    if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
        raise ValueError("AI 正在改写当前草稿，请等待完成后再保存。")
    _ensure_task_allows_approval(task)
    if task.status not in SAVE_DRAFT_ALLOWED_STATUSES:
        raise ValueError("当前状态不能保存草稿")
