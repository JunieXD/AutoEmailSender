from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo

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
    ImapMailboxHistoricalScanStatus,
    ImapMailboxSyncState,
    ImapProfessorSyncState,
    MatchAnalysisRun,
    LLMProfile,
    Professor,
)
from app.core.config import get_settings
from app.schemas.email_task import EmailTaskApprovalRequest, EmailTaskRewriteDraftRequest, EmailTaskScheduleRequest
from app.services import llm_runtime, mail_runtime
from app.services.email_addresses import normalize_email_address, normalize_email_list
from app.services.email_log_ingestion import EmailLogIngestRecord, upsert_email_log
from app.services.imap_errors import is_account_level_throttle_error as _is_account_level_throttle_error
from app.services.imap_errors import is_provider_throttle_error
from app.services.imap_message_fetcher import ImapFetchedMessage
from app.services.imap_sync_state import (
    claim_next_mailbox_history_scans,
    claim_next_professor_scans,
    clear_identity_sent_folder_discovery_cache,
    ensure_professor_scan_states_if_needed,
    ensure_recent_history_professor_scan_states,
    mark_mailbox_history_scan_failed,
    mark_mailbox_history_scan_progress,
    mark_professor_scan_completed,
    mark_professor_scan_failed,
    reset_mailbox_history_scans_to_pending,
    reset_professor_scans_to_pending,
)
from app.services.batch_schedule import (
    has_future_batch_window,
    is_batch_window_expired,
    is_datetime_in_batch_window,
    normalize_scheduled_dates,
)
from app.services.mail_runtime import MailAttachment, ReceivedEmail
from app.services.materials import (
    build_material_download_name,
    ensure_material_extracted_text,
    material_can_be_primary,
)
from app.services.operation_logs import record_operation_log
from app.services.outreach_templates import (
    OUTREACH_GENERATION_MODE_TEMPLATE,
    build_send_template_context,
    build_template_context,
    get_outreach_template_defaults_validation_error,
    render_outreach_template,
    render_template_with_context,
    resolve_outreach_template_config,
)
from app.services.rich_text import normalize_email_html, text_to_email_html
from app.services.runtime_settings import get_runtime_settings
from app.services.thinking_adaptation import ensure_thinking_adaptation


TASK_RELATION_OPTIONS = (
    selectinload(EmailTask.batch_task),
    selectinload(EmailTask.identity).selectinload(IdentityProfile.materials),
    selectinload(EmailTask.identity).selectinload(IdentityProfile.current_primary_material),
    selectinload(EmailTask.llm_profile),
    selectinload(EmailTask.professor),
    selectinload(EmailTask.primary_material),
)
_IMAP_IDENTITY_LOCKS: dict[int, asyncio.Lock] = {}
_IMAP_INCREMENTAL_LOCKS: dict[int, asyncio.Lock] = {}
_IMAP_HISTORY_LOCKS: dict[int, asyncio.Lock] = {}
_IMAP_IDENTITY_LOCKS_GUARD = asyncio.Lock()
VALID_IMAP_FOLDER_ROLES = {"inbox", "sent"}
IMAP_HISTORY_THROTTLE_PREFIX = "history:"
IMAP_ACCOUNT_THROTTLE_PREFIX = "account:"
IMAP_HISTORY_BODY_FETCH_COMMANDS_PER_MESSAGE = 6
RECENT_HISTORY_STRATEGY_NAME = "recent-v1"
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RecentHistoryWindow:
    start_date: date
    strategy_version: str


def build_recent_history_window(now: datetime | None = None) -> RecentHistoryWindow:
    current = now or utc_now()
    start_year = current.year - 1
    return RecentHistoryWindow(
        start_date=date(start_year, 1, 1),
        strategy_version=f"{RECENT_HISTORY_STRATEGY_NAME}-{start_year}",
    )


@dataclass(frozen=True, slots=True)
class _HistoryBodyFetchResult:
    messages: list[ImapFetchedMessage]
    command_count: int
    highest_scanned_uid: int | None
    covered_all_headers: bool


@dataclass(frozen=True, slots=True)
class _MailboxHistoryBodyFetchResult:
    messages: list[ImapFetchedMessage]
    command_count: int
    matched_header_count: int
    covered_all_headers: bool
    highest_scanned_uid: int | None = None
    safe_match_uids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class _MailboxHistoryHeaderMatch:
    message: ImapFetchedMessage
    professor_ids: tuple[int, ...]


@dataclass(slots=True)
class _RecentSentDiscoveryResult:
    detected: int
    professor_candidates: set[tuple[int, str]]
    command_count: int


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
                            selectinload(EmailTask.batch_task).selectinload(BatchTask.email_tasks),
                            selectinload(EmailTask.identity),
                        )
                        .join(BatchTask, EmailTask.batch_task_id == BatchTask.id, isouter=True)
                        .where(
                            EmailTask.status.in_(
                                [
                                    EmailTaskStatus.APPROVED.value,
                                    EmailTaskStatus.SCHEDULED.value,
                                ],
                            ),
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
    for batch_task in batch_tasks:
        if await expire_batch_task_if_needed(session, batch_task, local_now):
            expired_count += 1
    if expired_count > 0:
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
    ):
        return False

    canceled_count = 0
    now_utc = local_now.astimezone(UTC)
    if any(
        _has_future_scheduled_at(
            email_task.scheduled_at,
            now_utc,
            scheduled_dates=batch_task.scheduled_dates,
            local_timezone=local_now.tzinfo,
        )
        for email_task in batch_task.email_tasks
        if not _is_user_removed_batch_item(email_task)
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
        if _is_user_removed_batch_item(email_task):
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
                EmailTask.status.in_(
                    [
                        EmailTaskStatus.SENT.value,
                        EmailTaskStatus.REPLY_DETECTED.value,
                    ],
                ),
                EmailTask.sent_at >= start_utc,
                EmailTask.sent_at < end_utc,
            ),
        )
        or 0
    )


async def poll_for_replies_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session:
        identity_ids = list(
            (
                await session.execute(
                    select(IdentityProfile.id).where(
                        IdentityProfile.imap_host.is_not(None),
                        IdentityProfile.imap_port.is_not(None),
                        IdentityProfile.imap_username.is_not(None),
                        IdentityProfile.imap_password.is_not(None),
                        func.trim(IdentityProfile.imap_host) != "",
                        func.trim(IdentityProfile.imap_username) != "",
                        func.trim(IdentityProfile.imap_password) != "",
                    ),
                )
            ).scalars()
        )

    detected = 0
    for identity_id in identity_ids:
        detected += await sync_identity_incremental_poll_once(session_factory, identity_id)
    return detected


async def poll_imap_history_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session:
        identity_ids = list(
            (
                await session.execute(
                    select(IdentityProfile.id).where(
                        IdentityProfile.imap_host.is_not(None),
                        IdentityProfile.imap_port.is_not(None),
                        IdentityProfile.imap_username.is_not(None),
                        IdentityProfile.imap_password.is_not(None),
                        func.trim(IdentityProfile.imap_host) != "",
                        func.trim(IdentityProfile.imap_username) != "",
                        func.trim(IdentityProfile.imap_password) != "",
                    ),
                )
            ).scalars()
        )

    detected = 0
    for identity_id in identity_ids:
        detected += await sync_identity_history_poll_once(session_factory, identity_id)
    return detected

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
            outreach_config = _resolve_draft_generation_outreach_config(task)
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
                thinking_extra_body = await ensure_thinking_adaptation(session, runtime_llm_profile)
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
                    thinking_extra_body=thinking_extra_body,
                )
                subject = generation.result.subject
                body_text = generation.result.body_text
                body_html = generation.result.body_html
                usage = generation.usage
                provider_payload = {
                    "source": "llm",
                    "primary_material_id": task.primary_material_id,
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
        task.generated_subject = subject
        task.generated_content_text = body_text
        task.generated_content_html = body_html
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
) -> MatchCalculationActionResult:
    async with session_factory() as session:
        task = await _load_email_task(session, task_id)
        if not task:
            raise ValueError(f"EmailTask {task_id} 不存在")
        runtime_llm_profile = await _resolve_runtime_llm_profile(session, task, llm_profile_id)
        if (
            task.batch_task
            and task.batch_task.status != BatchTaskStatus.RUNNING.value
            and not ignore_batch_status
        ):
            return _match_action_result(task)
        try:
            match_material = await _resolve_match_primary_material(session, task)
        except ValueError:
            if force:
                raise
            return _match_action_result(task)
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
            return _match_action_result(task)

        task.llm_profile_id = runtime_llm_profile.id
        thinking_extra_body = await ensure_thinking_adaptation(session, runtime_llm_profile)
        run = await _create_running_match_analysis_run(session, task, match_material)
        await session.commit()
        try:
            generation = await llm_runtime.generate_match_evaluation(
                identity=task.identity,
                primary_material=match_material,
                llm_profile=runtime_llm_profile,
                professor=task.professor,
                available_materials=list(task.identity.materials),
                thinking_extra_body=thinking_extra_body,
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
            return _match_action_result(task, run_id=run.id)
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
        task.match_score = result.match_score
        task.match_reason = result.match_reason
        task.fit_points = result.fit_points
        task.risk_points = result.risk_points
        task.match_keywords = result.keywords
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
                "match_score": task.match_score,
                "force": force,
            },
        )
        await session.commit()
        return _match_action_result(
            task,
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
        thinking_extra_body = await ensure_thinking_adaptation(session, runtime_llm_profile)
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
                    thinking_extra_body=thinking_extra_body,
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

        outreach_config = _resolve_draft_generation_outreach_config(task)
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
        thinking_extra_body = await ensure_thinking_adaptation(session, runtime_llm_profile)
        rewrite_preferences = llm_runtime.DraftRewritePreferences(
            draft_rewrite_intensity=runtime_settings.draft_rewrite_intensity,
            draft_rewrite_tone=runtime_settings.draft_rewrite_tone,
            draft_rewrite_formality=runtime_settings.draft_rewrite_formality,
            draft_rewrite_length=runtime_settings.draft_rewrite_length,
            draft_rewrite_specificity=runtime_settings.draft_rewrite_specificity,
            draft_template_preservation=runtime_settings.draft_template_preservation,
            draft_custom_instruction=runtime_settings.draft_custom_instruction,
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
            thinking_extra_body=thinking_extra_body,
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
    usage: MatchUsageSummary | None = None,
    run_id: int | None = None,
) -> MatchCalculationActionResult:
    return MatchCalculationActionResult(
        professor_id=task.professor_id,
        identity_id=task.identity_id,
        llm_profile_id=task.llm_profile_id,
        usage=usage or MatchUsageSummary(),
        run_id=run_id,
    )


async def _create_running_match_analysis_run(
    session: AsyncSession,
    task: EmailTask,
    primary_material: IdentityMaterial,
) -> MatchAnalysisRun:
    run = MatchAnalysisRun(
        email_task_id=task.id,
        professor_id=task.professor_id,
        identity_id=task.identity_id,
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
    task: EmailTask,
) -> IdentityMaterial:
    material = task.identity.current_primary_material
    if material is None:
        material_id = task.identity.current_primary_material_id
        if material_id is not None:
            material = await session.get(IdentityMaterial, material_id)
    if material is None:
        raise ValueError("请到个人页设置默认材料")
    if material.identity_id != task.identity_id:
        raise ValueError("个人页默认材料不属于当前身份")
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
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            raise ValueError("已发送或已回信任务不能再切换本次发信模式")

        snapshot = _build_task_outreach_snapshot(
            task.identity,
            outreach_generation_mode=outreach_generation_mode,
            outreach_template_subject=outreach_template_subject,
            outreach_template_body_text=outreach_template_body_text,
            outreach_template_body_html=outreach_template_body_html,
            fallback_task=task,
        )
        task.outreach_generation_mode = snapshot["outreach_generation_mode"]
        task.outreach_template_subject = snapshot["outreach_template_subject"]
        task.outreach_template_body_text = snapshot["outreach_template_body_text"]
        task.outreach_template_body_html = snapshot["outreach_template_body_html"]
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
        child_task = _create_manual_child_task(task, reuse_existing_draft=True)
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
        child_task = _create_manual_child_task(
            task,
            reuse_existing_draft=False,
            minimum_status=EmailTaskStatus.MATCHED.value,
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


async def poll_identity_replies(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> int:
    return await sync_identity_imap_once(session_factory, identity_id)


async def sync_identity_imap_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> int:
    lock = await _get_imap_identity_lock(identity_id)
    incremental_lock = await _get_imap_incremental_lock(identity_id)
    history_lock = await _get_imap_history_lock(identity_id)
    if lock.locked() or incremental_lock.locked() or history_lock.locked():
        return 0
    async with lock:
        if incremental_lock.locked() or history_lock.locked():
            return 0
        return await _sync_identity_imap_once_unlocked(session_factory, identity_id)


async def sync_identity_incremental_poll_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> int:
    identity_lock = await _get_imap_identity_lock(identity_id)
    if identity_lock.locked():
        return 0
    history_lock = await _get_imap_history_lock(identity_id)
    if history_lock.locked():
        return 0
    lock = await _get_imap_incremental_lock(identity_id)
    if lock.locked():
        return 0
    async with lock:
        if identity_lock.locked() or history_lock.locked():
            return 0
        return await _sync_identity_incremental_once_unlocked(session_factory, identity_id)


async def sync_identity_history_poll_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> int:
    if await _is_imap_history_work_locked(identity_id):
        return 0
    lock = await _get_imap_history_lock(identity_id)
    async with lock:
        if await _is_imap_identity_locked(identity_id):
            return 0
        if (await _get_imap_incremental_lock(identity_id)).locked():
            return 0
        if await is_imap_history_paused(session_factory, identity_id):
            return 0
        return await sync_identity_history_once(session_factory, identity_id)


async def get_cached_or_discover_sent_folder(
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityProfile,
) -> str | None:
    settings = get_settings()
    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity.id,
            folder_role="sent",
            folder="Sent",
        )
        if state.discovered_sent_folder:
            return state.discovered_sent_folder
        if state.sent_folder_discovery_failed_at is not None:
            retry_at = state.sent_folder_discovery_failed_at + timedelta(
                seconds=settings.imap_sent_folder_failure_ttl_seconds,
            )
            if utc_now() < retry_at:
                return None

    try:
        sent_folder = await mail_runtime.discover_sent_folder(identity)
    except Exception as exc:
        await _record_sent_folder_discovery_failure(session_factory, identity.id, str(exc))
        if is_provider_throttle_error(exc):
            await mark_imap_throttled(
                session_factory,
                identity.id,
                reason=str(exc),
                account_level=_is_account_level_throttle_error(exc),
            )
        return None

    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity.id,
            folder_role="sent",
            folder="Sent",
        )
        if sent_folder:
            state.discovered_sent_folder = sent_folder
            state.sent_folder_discovered_at = utc_now()
            state.sent_folder_discovery_failed_at = None
            state.sent_folder_discovery_error = None
        else:
            state.sent_folder_discovery_failed_at = utc_now()
            state.sent_folder_discovery_error = "Sent folder not found"
        await session.commit()
    return sent_folder


async def _record_sent_folder_discovery_failure(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    error: str,
) -> None:
    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role="sent",
            folder="Sent",
        )
        state.sent_folder_discovery_failed_at = utc_now()
        state.sent_folder_discovery_error = error
        await session.commit()


async def mark_imap_throttled(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    reason: str,
    account_level: bool,
) -> None:
    settings = get_settings()
    prefix = IMAP_ACCOUNT_THROTTLE_PREFIX if account_level else IMAP_HISTORY_THROTTLE_PREFIX
    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role="inbox",
            folder="INBOX",
        )
        state.throttle_paused_until = utc_now() + timedelta(seconds=settings.imap_throttle_backoff_seconds)
        state.throttle_reason = f"{prefix}{reason}"
        await session.commit()


async def is_imap_history_paused(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> bool:
    reason = await _get_active_imap_throttle_reason(session_factory, identity_id)
    return reason is not None


async def is_imap_incremental_paused(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> bool:
    reason = await _get_active_imap_throttle_reason(session_factory, identity_id)
    return bool(reason and reason.startswith(IMAP_ACCOUNT_THROTTLE_PREFIX))


async def _get_active_imap_throttle_reason(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> str | None:
    async with session_factory() as session:
        state = await session.scalar(
            select(ImapMailboxSyncState).where(
                ImapMailboxSyncState.identity_id == identity_id,
                ImapMailboxSyncState.folder_role == "inbox",
                ImapMailboxSyncState.folder == "INBOX",
            ),
        )
        if state is None or state.throttle_paused_until is None:
            return None
        if state.throttle_paused_until <= utc_now():
            state.throttle_paused_until = None
            state.throttle_reason = None
            await session.commit()
            return None
        return state.throttle_reason or ""


async def log_imap_history_progress(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    folders: list[tuple[str, str]] | None = None,
) -> None:
    folder_conditions = [
        and_(
            ImapMailboxSyncState.folder_role == folder_role,
            ImapMailboxSyncState.folder == folder,
        )
        for folder_role, folder in (folders or [])
    ]
    mailbox_where = [ImapMailboxSyncState.identity_id == identity_id]
    if folders is not None:
        mailbox_where.append(or_(*folder_conditions) if folder_conditions else False)
    async with session_factory() as session:
        mailbox_rows = (
            await session.execute(
                select(
                    ImapMailboxSyncState.history_scan_status,
                    func.count(ImapMailboxSyncState.id),
                )
                .where(*mailbox_where)
                .group_by(ImapMailboxSyncState.history_scan_status),
            )
        ).all()
        professor_rows = (
            await session.execute(
                select(
                    ImapProfessorSyncState.historical_scan_status,
                    func.count(ImapProfessorSyncState.id),
                )
                .where(ImapProfessorSyncState.identity_id == identity_id)
                .group_by(ImapProfessorSyncState.historical_scan_status),
            )
        ).all()
        progress = await session.execute(
            select(
                func.coalesce(func.sum(ImapMailboxSyncState.history_scanned_count), 0),
                func.coalesce(func.sum(ImapMailboxSyncState.history_matched_count), 0),
            ).where(*mailbox_where),
        )
        scanned_count, matched_count = progress.one()
        mailbox_counts = {status: count for status, count in mailbox_rows}
        professor_counts = {status: count for status, count in professor_rows}
    logger.info(
        (
            "imap_history_progress identity_id=%s mailbox_pending=%s mailbox_completed=%s "
            "mailbox_failed=%s mailbox_running=%s scanned=%s matched=%s "
            "targeted_pending=%s targeted_completed=%s targeted_failed=%s targeted_running=%s"
        ),
        identity_id,
        mailbox_counts.get("pending", 0),
        mailbox_counts.get("completed", 0),
        mailbox_counts.get("failed", 0),
        mailbox_counts.get("running", 0),
        scanned_count,
        matched_count,
        professor_counts.get("pending", 0),
        professor_counts.get("completed", 0),
        professor_counts.get("failed", 0),
        professor_counts.get("running", 0),
    )


async def _sync_identity_imap_once_unlocked(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> int:
    incremental_detected = await _sync_identity_incremental_once_unlocked(
        session_factory,
        identity_id,
    )
    history_detected = 0
    if not await is_imap_history_paused(session_factory, identity_id):
        history_detected = await sync_identity_history_once(session_factory, identity_id)
    return incremental_detected + history_detected


async def _sync_identity_incremental_once_unlocked(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> int:
    sent_folder = None
    incremental_paused = await is_imap_incremental_paused(session_factory, identity_id)
    async with session_factory() as session:
        identity = await session.get(IdentityProfile, identity_id)
    if identity is not None and not incremental_paused:
        sent_folder = await get_cached_or_discover_sent_folder(session_factory, identity)

    if await is_imap_incremental_paused(session_factory, identity_id):
        return 0

    await ensure_professor_scan_states_if_needed(
        session_factory,
        identity_id=identity_id,
        sent_folder=sent_folder,
    )
    inbox_detected = 0
    if not incremental_paused:
        inbox_detected = await sync_identity_incremental_once(
            session_factory,
            identity_id,
            folder_role="inbox",
            folder="INBOX",
        )
    sent_detected = 0
    if sent_folder and not await is_imap_incremental_paused(session_factory, identity_id):
        sent_detected = await sync_identity_incremental_once(
            session_factory,
            identity_id,
            folder_role="sent",
            folder=sent_folder,
        )
    return inbox_detected + sent_detected


async def sync_identity_history_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> int:
    settings = get_settings()
    if await is_imap_history_paused(session_factory, identity_id):
        return 0
    if settings.imap_history_batch_size <= 0 or settings.imap_history_command_budget_per_minute <= 0:
        return 0

    async with session_factory() as session:
        identity = await session.get(IdentityProfile, identity_id)
    if identity is None:
        return 0
    if not _identity_has_imap_config(identity):
        return 0

    sent_folder = await get_cached_or_discover_sent_folder(session_factory, identity)
    if await is_imap_history_paused(session_factory, identity_id):
        return 0

    window = build_recent_history_window()
    command_budget = settings.imap_history_command_budget_per_minute
    sent_discovery = _RecentSentDiscoveryResult(
        detected=0,
        professor_candidates=set(),
        command_count=0,
    )
    if sent_folder and command_budget > 0:
        try:
            sent_discovery = await _sync_recent_sent_history_once(
                session_factory,
                identity,
                identity_id=identity_id,
                sent_folder=sent_folder,
                window=window,
                command_budget=command_budget,
            )
        except Exception as exc:
            if is_provider_throttle_error(exc):
                await mark_imap_throttled(
                    session_factory,
                    identity_id,
                    reason=str(exc),
                    account_level=_is_account_level_throttle_error(exc),
                )
            await _mark_recent_sent_history_failed(
                session_factory,
                identity_id=identity_id,
                sent_folder=sent_folder,
                error=exc,
            )
            await log_imap_history_progress(session_factory, identity_id, folders=[("inbox", "INBOX")])
            return 0
        command_budget = max(0, command_budget - sent_discovery.command_count)

    inbox_candidates = await _load_recent_history_inbox_candidates(
        session_factory,
        identity_id=identity_id,
        sent_candidates=sent_discovery.professor_candidates,
    )
    await ensure_recent_history_professor_scan_states(
        session_factory,
        identity_id=identity_id,
        candidates=inbox_candidates,
        strategy_version=window.strategy_version,
        folder="INBOX",
    )
    inbox_detected = await _sync_identity_targeted_history_once(
        session_factory,
        identity_id,
        mailbox_folders=[("inbox", "INBOX")],
        since_date=window.start_date,
        strategy_version=window.strategy_version,
        command_budget=command_budget,
    )
    await log_imap_history_progress(session_factory, identity_id, folders=[("inbox", "INBOX")])
    return sent_discovery.detected + inbox_detected


async def _sync_recent_sent_history_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityProfile,
    *,
    identity_id: int,
    sent_folder: str,
    window: RecentHistoryWindow,
    command_budget: int,
) -> _RecentSentDiscoveryResult:
    if command_budget <= 0:
        return _RecentSentDiscoveryResult(
            detected=0,
            professor_candidates=set(),
            command_count=0,
        )

    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role="sent",
            folder=sent_folder,
        )
        if state.history_strategy_version != window.strategy_version:
            state.history_strategy_version = window.strategy_version
            state.history_high_water_uid = None
            state.history_next_before_uid = None
            state.history_scan_status = "sent_recent_discovery_pending"
            state.history_scanned_count = 0
            state.history_matched_count = 0
            state.history_last_error = None
        min_uid = state.history_high_water_uid
        expected_uidvalidity = state.uidvalidity
        await session.commit()

    header_result = await mail_runtime.fetch_recent_mailbox_message_headers_since(
        identity,
        sent_folder,
        window.start_date,
        min_uid=min_uid,
        max_fetch_batches=max(0, command_budget - 1),
        expected_uidvalidity=expected_uidvalidity,
    )
    if header_result.command_count > command_budget:
        raise RuntimeError("IMAP history command budget exhausted during recent sent header fetch")
    remaining_command_budget = command_budget - header_result.command_count
    matched_headers, professor_candidates = await _match_recent_sent_headers(
        session_factory,
        header_result.messages,
    )
    body_result = await _fetch_recent_sent_message_bodies(
        session_factory,
        identity,
        identity_id=identity_id,
        folder=sent_folder,
        matched_headers=matched_headers,
        remaining_command_budget=remaining_command_budget,
    )
    detected = await process_imap_fetched_messages(
        session_factory,
        identity_id,
        body_result.messages,
        folder_role="sent",
        folder=sent_folder,
    )
    covered_recent_headers = not header_result.exhausted and body_result.covered_all_headers
    high_water_uid, safe_scanned_count, safe_matched_count = _recent_sent_safe_scan_progress(
        None if header_result.uidvalidity_changed else min_uid,
        header_result.messages,
        matched_headers,
        body_result.safe_match_uids,
    )
    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role="sent",
            folder=sent_folder,
        )
        if header_result.uidvalidity is not None:
            state.uidvalidity = header_result.uidvalidity
        state.history_high_water_uid = high_water_uid
        state.history_next_before_uid = None
        state.history_scan_status = (
            "inbox_recent_replies_pending"
            if covered_recent_headers
            else "sent_recent_discovery_running"
        )
        state.history_scanned_count = (state.history_scanned_count or 0) + safe_scanned_count
        state.history_matched_count = (state.history_matched_count or 0) + safe_matched_count
        state.history_last_error = None
        await session.commit()

    return _RecentSentDiscoveryResult(
        detected=detected,
        professor_candidates=professor_candidates,
        command_count=header_result.command_count + body_result.command_count,
    )


async def _mark_recent_sent_history_failed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    sent_folder: str,
    error: Exception,
) -> None:
    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role="sent",
            folder=sent_folder,
        )
        state.history_scan_status = "sent_recent_discovery_failed"
        state.history_last_error = str(error)
        await session.commit()


async def _match_recent_sent_headers(
    session_factory: async_sessionmaker[AsyncSession],
    header_messages: list[ImapFetchedMessage],
) -> tuple[list[_MailboxHistoryHeaderMatch], set[tuple[int, str]]]:
    if not header_messages:
        return [], set()
    professor_ids_by_email = await _load_active_professor_ids_by_email(session_factory)
    if not professor_ids_by_email:
        return [], set()

    matches: list[_MailboxHistoryHeaderMatch] = []
    professor_candidates: set[tuple[int, str]] = set()
    for message in header_messages:
        candidate_emails = normalize_email_list(
            [*message.to_emails, *message.cc_emails, *message.bcc_emails],
        )
        professor_ids = tuple(
            dict.fromkeys(
                professor_id
                for email in candidate_emails
                if email
                for professor_id in professor_ids_by_email.get(email, [])
            ),
        )
        for email in candidate_emails:
            if not email:
                continue
            for professor_id in professor_ids_by_email.get(email, []):
                professor_candidates.add((professor_id, email))
        if professor_ids:
            matches.append(_MailboxHistoryHeaderMatch(message=message, professor_ids=professor_ids))
    return matches, professor_candidates


async def _fetch_recent_sent_message_bodies(
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityProfile,
    *,
    identity_id: int,
    folder: str,
    matched_headers: list[_MailboxHistoryHeaderMatch],
    remaining_command_budget: int,
) -> _MailboxHistoryBodyFetchResult:
    if not matched_headers:
        return _MailboxHistoryBodyFetchResult(
            messages=[],
            command_count=0,
            matched_header_count=0,
            covered_all_headers=True,
        )

    state = ImapMailboxSyncState(
        identity_id=identity_id,
        folder_role="sent",
        folder=folder,
    )
    missing_uids: list[int] = []
    covered_all_headers = True
    allowed_uid_count = None
    highest_scanned_uid: int | None = None
    safe_match_uids: list[int] = []
    for match in sorted(matched_headers, key=lambda item: item.message.uid):
        if await _history_mailbox_header_already_ingested(
            session_factory,
            identity_id=identity_id,
            state=state,
            message=match.message,
            professor_ids=match.professor_ids,
        ):
            highest_scanned_uid = _max_optional_uid(highest_scanned_uid, match.message.uid)
            safe_match_uids.append(match.message.uid)
            continue
        if allowed_uid_count is None:
            allowed_uid_count = _history_body_fetch_uid_limit(
                remaining_command_budget,
                get_settings().imap_fetch_batch_size,
            )
            if allowed_uid_count <= 0:
                return _MailboxHistoryBodyFetchResult(
                    messages=[],
                    command_count=0,
                    matched_header_count=len(matched_headers),
                    covered_all_headers=False,
                    highest_scanned_uid=None,
                    safe_match_uids=tuple(safe_match_uids),
                )
        if len(missing_uids) >= allowed_uid_count:
            covered_all_headers = False
            break
        missing_uids.append(match.message.uid)
        highest_scanned_uid = _max_optional_uid(highest_scanned_uid, match.message.uid)

    if not missing_uids:
        return _MailboxHistoryBodyFetchResult(
            messages=[],
            command_count=0,
            matched_header_count=len(matched_headers),
            covered_all_headers=covered_all_headers,
            highest_scanned_uid=highest_scanned_uid,
            safe_match_uids=tuple(safe_match_uids),
        )

    body_fetch_command_count = _history_body_fetch_command_count(
        len(missing_uids),
        get_settings().imap_fetch_batch_size,
    )
    messages = await mail_runtime.fetch_professor_history_mailbox_messages_by_uid(
        identity,
        folder,
        missing_uids,
    )
    fetched_uids = {message.uid for message in messages}
    missing_after_fetch = [uid for uid in missing_uids if uid not in fetched_uids]
    if missing_after_fetch:
        raise RuntimeError(f"IMAP history body fetch incomplete for UIDs: {missing_after_fetch}")
    safe_match_uids.extend(missing_uids)
    return _MailboxHistoryBodyFetchResult(
        messages=messages,
        command_count=body_fetch_command_count,
        matched_header_count=len(matched_headers),
        covered_all_headers=covered_all_headers,
        highest_scanned_uid=highest_scanned_uid,
        safe_match_uids=tuple(safe_match_uids),
    )


def _recent_sent_safe_scan_progress(
    min_uid: int | None,
    header_messages: list[ImapFetchedMessage],
    matched_headers: list[_MailboxHistoryHeaderMatch],
    safe_match_uids: tuple[int, ...],
) -> tuple[int | None, int, int]:
    matched_uids = {match.message.uid for match in matched_headers}
    safe_matched_uids = set(safe_match_uids)
    high_water_uid = min_uid
    scanned_count = 0
    matched_count = 0
    for message in sorted(header_messages, key=lambda item: item.uid):
        if message.uid in matched_uids and message.uid not in safe_matched_uids:
            break
        high_water_uid = _max_optional_uid(high_water_uid, message.uid)
        scanned_count += 1
        if message.uid in matched_uids:
            matched_count += 1
    return high_water_uid, scanned_count, matched_count


async def _load_recent_history_inbox_candidates(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    sent_candidates: set[tuple[int, str]],
) -> set[tuple[int, str]]:
    candidates = {
        (professor_id, normalized_email)
        for professor_id, email in sent_candidates
        if professor_id is not None
        and professor_id > 0
        and (normalized_email := normalize_email_address(email))
    }
    async with session_factory() as session:
        log_rows = (
            await session.execute(
                select(Professor.id, Professor.email)
                .join(EmailLog, EmailLog.professor_id == Professor.id)
                .where(
                    EmailLog.identity_id == identity_id,
                    Professor.archived_at.is_(None),
                    Professor.email.is_not(None),
                    EmailLog.direction.in_(
                        [
                            EmailDirection.SENT.value,
                            EmailDirection.RECEIVED.value,
                        ],
                    ),
                    or_(
                        EmailLog.direction == EmailDirection.RECEIVED.value,
                        EmailLog.failure_summary.is_(None),
                    ),
                )
                .distinct(),
            )
        ).all()
        task_rows = (
            await session.execute(
                select(Professor.id, Professor.email)
                .join(EmailTask, EmailTask.professor_id == Professor.id)
                .where(
                    EmailTask.identity_id == identity_id,
                    Professor.archived_at.is_(None),
                    Professor.email.is_not(None),
                    or_(
                        EmailTask.status.in_(
                            [
                                EmailTaskStatus.SENT.value,
                                EmailTaskStatus.REPLY_DETECTED.value,
                            ],
                        ),
                        EmailTask.sent_at.is_not(None),
                        EmailTask.is_replied.is_(True),
                        EmailTask.last_rfc_message_id.is_not(None),
                    ),
                )
                .distinct(),
            )
        ).all()
    for professor_id, email in [*log_rows, *task_rows]:
        normalized_email = normalize_email_address(email)
        if professor_id and normalized_email:
            candidates.add((professor_id, normalized_email))
    return candidates


async def _ensure_mailbox_history_scan_states(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    folder_specs: list[tuple[str, str]],
) -> None:
    async with session_factory() as session:
        for folder_role, folder in folder_specs:
            await _get_or_create_mailbox_state(
                session,
                identity_id,
                folder_role=folder_role,
                folder=folder,
            )
        await session.commit()


def _mailbox_history_folder_specs(sent_folder: str | None) -> list[tuple[str, str]]:
    specs = [("inbox", "INBOX")]
    if sent_folder:
        specs.append(("sent", sent_folder))
    return specs


async def _mailbox_history_scans_completed(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    folder_specs: list[tuple[str, str]],
) -> bool:
    if not folder_specs:
        return False
    async with session_factory() as session:
        for folder_role, folder in folder_specs:
            state = await session.scalar(
                select(ImapMailboxSyncState).where(
                    ImapMailboxSyncState.identity_id == identity_id,
                    ImapMailboxSyncState.folder_role == folder_role,
                    ImapMailboxSyncState.folder == folder,
                ),
            )
            if state is None:
                return False
            if state.history_scan_status != ImapMailboxHistoricalScanStatus.COMPLETED.value:
                return False
    return True


async def _load_mailbox_uidvalidity(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    folder_role: str,
    folder: str,
) -> int | None:
    async with session_factory() as session:
        return await session.scalar(
            select(ImapMailboxSyncState.uidvalidity).where(
                ImapMailboxSyncState.identity_id == identity_id,
                ImapMailboxSyncState.folder_role == folder_role,
                ImapMailboxSyncState.folder == folder,
            ),
        )


async def _record_targeted_mailbox_uidvalidity(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    folder_role: str,
    folder: str,
    uidvalidity: int,
) -> None:
    async with session_factory() as session:
        mailbox_state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role=folder_role,
            folder=folder,
        )
        mailbox_state.uidvalidity = uidvalidity
        await session.commit()


async def _reset_other_targeted_professor_cursors_for_uidvalidity_change(
    session_factory: async_sessionmaker[AsyncSession],
    current_state: ImapProfessorSyncState,
) -> None:
    async with session_factory() as session:
        sibling_states = list(
            (
                await session.execute(
                    select(ImapProfessorSyncState).where(
                        ImapProfessorSyncState.identity_id == current_state.identity_id,
                        ImapProfessorSyncState.folder_role == current_state.folder_role,
                        ImapProfessorSyncState.folder == current_state.folder,
                        ImapProfessorSyncState.history_strategy_version
                        == current_state.history_strategy_version,
                        ImapProfessorSyncState.id != current_state.id,
                    ),
                )
            ).scalars(),
        )
        for sibling in sibling_states:
            sibling.last_scanned_uid = None
            sibling.historical_scan_status = "pending"
            sibling.historical_scan_started_at = None
            sibling.historical_scan_completed_at = None
            sibling.last_error = None
        await session.commit()


async def _sync_identity_targeted_history_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    command_budget: int | None = None,
    mailbox_folders: list[tuple[str, str]] | None = None,
    since_date: date | None = None,
    strategy_version: str | None = None,
) -> int:
    settings = get_settings()
    if await is_imap_history_paused(session_factory, identity_id):
        return 0
    effective_command_budget = (
        settings.imap_history_command_budget_per_minute
        if command_budget is None
        else command_budget
    )
    claim_limit = min(
        settings.imap_history_batch_size,
        effective_command_budget,
    )
    if claim_limit <= 0:
        return 0
    states = await claim_next_professor_scans(
        session_factory,
        identity_id,
        limit=claim_limit,
        strategy_version=strategy_version,
    )
    if not states:
        return 0
    if mailbox_folders is not None:
        allowed_folders = set(mailbox_folders)
        allowed_states = [
            state
            for state in states
            if (state.folder_role, state.folder) in allowed_folders
        ]
        disallowed_state_ids = [
            state.id
            for state in states
            if (state.folder_role, state.folder) not in allowed_folders
        ]
        if disallowed_state_ids:
            await reset_professor_scans_to_pending(session_factory, disallowed_state_ids)
        states = allowed_states
        if not states:
            return 0
    detected_total = 0
    command_budget = effective_command_budget
    for index, state in enumerate(states):
        try:
            async with session_factory() as session:
                identity = await session.get(IdentityProfile, identity_id)
            if identity is None:
                await mark_professor_scan_completed(session_factory, state.id, state.last_scanned_uid)
                continue
            if command_budget <= 0:
                raise RuntimeError("IMAP history command budget exhausted")
            header_search_reserve = 4 if state.folder_role == "sent" else 1
            header_fetch_budget = max(
                0,
                command_budget
                - header_search_reserve
                - _history_body_fetch_command_count(1, settings.imap_fetch_batch_size),
            )
            expected_uidvalidity: int | None | object = mail_runtime._UIDVALIDITY_UNSET
            if state.folder_role == "inbox":
                expected_uidvalidity = await _load_mailbox_uidvalidity(
                    session_factory,
                    identity_id=identity_id,
                    folder_role=state.folder_role,
                    folder=state.folder,
                )
            header_result = await mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count(
                identity,
                state.folder,
                state.professor_email,
                folder_role=state.folder_role,
                min_uid=state.last_scanned_uid,
                max_fetch_batches=header_fetch_budget,
                since_date=since_date if state.folder_role == "inbox" else None,
                expected_uidvalidity=expected_uidvalidity,
            )
            if header_result.command_count > command_budget:
                raise RuntimeError("IMAP history command budget exhausted during header fetch")
            inbox_uidvalidity_changed = state.folder_role == "inbox" and header_result.uidvalidity_changed
            if state.folder_role == "inbox" and header_result.uidvalidity is not None:
                await _record_targeted_mailbox_uidvalidity(
                    session_factory,
                    identity_id=identity_id,
                    folder_role=state.folder_role,
                    folder=state.folder,
                    uidvalidity=header_result.uidvalidity,
                )
            if inbox_uidvalidity_changed:
                await _reset_other_targeted_professor_cursors_for_uidvalidity_change(
                    session_factory,
                    state,
                )
                state.last_scanned_uid = None
            command_budget -= header_result.command_count
            body_result = await _fetch_missing_history_message_bodies(
                session_factory,
                identity,
                identity_id=identity_id,
                state=state,
                header_messages=header_result.messages,
                remaining_command_budget=command_budget,
            )
            messages = body_result.messages
            command_budget -= body_result.command_count
            detected = await process_imap_fetched_messages(
                session_factory,
                identity_id,
                messages,
                folder_role=state.folder_role,
                folder=state.folder,
            )
            max_uid = body_result.highest_scanned_uid
            if header_result.exhausted or not body_result.covered_all_headers:
                await reset_professor_scans_to_pending(session_factory, [state.id])
                async with session_factory() as session:
                    pending_state = await session.get(ImapProfessorSyncState, state.id)
                    if pending_state is not None:
                        pending_state.last_scanned_uid = max_uid
                        pending_state.last_error = None
                        await session.commit()
                await reset_professor_scans_to_pending(
                    session_factory,
                    [pending_state.id for pending_state in states[index + 1 :]],
                )
                detected_total += detected
                break
            await mark_professor_scan_completed(session_factory, state.id, max_uid)
            detected_total += detected
            if inbox_uidvalidity_changed:
                break
        except Exception as exc:
            if _is_history_command_budget_error(exc):
                await reset_professor_scans_to_pending(
                    session_factory,
                    [pending_state.id for pending_state in states[index:]],
                )
                break
            if is_provider_throttle_error(exc):
                await mark_imap_throttled(
                    session_factory,
                    identity_id,
                    reason=str(exc),
                    account_level=_is_account_level_throttle_error(exc),
                )
                await reset_professor_scans_to_pending(
                    session_factory,
                    [pending_state.id for pending_state in states[index + 1 :]],
                )
            await mark_professor_scan_failed(session_factory, state.id, str(exc))
            if is_provider_throttle_error(exc):
                break
    await log_imap_history_progress(session_factory, identity_id, folders=mailbox_folders)
    return detected_total


async def _fetch_missing_history_mailbox_message_bodies(
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityProfile,
    *,
    identity_id: int,
    state: ImapMailboxSyncState,
    header_messages: list[ImapFetchedMessage],
    remaining_command_budget: int,
) -> _MailboxHistoryBodyFetchResult:
    matched_headers = await _match_history_mailbox_headers(
        session_factory,
        folder_role=state.folder_role,
        header_messages=header_messages,
    )
    if not matched_headers:
        return _MailboxHistoryBodyFetchResult(
            messages=[],
            command_count=0,
            matched_header_count=0,
            covered_all_headers=True,
        )

    missing_uids: list[int] = []
    covered_all_headers = True
    allowed_uid_count = None
    for match in sorted(matched_headers, key=lambda item: item.message.uid, reverse=True):
        if await _history_mailbox_header_already_ingested(
            session_factory,
            identity_id=identity_id,
            state=state,
            message=match.message,
            professor_ids=match.professor_ids,
        ):
            continue
        if allowed_uid_count is None:
            allowed_uid_count = _history_body_fetch_uid_limit(
                remaining_command_budget,
                get_settings().imap_fetch_batch_size,
            )
            if allowed_uid_count <= 0:
                raise RuntimeError("IMAP history command budget exhausted before body fetch")
        if len(missing_uids) >= allowed_uid_count:
            covered_all_headers = False
            break
        missing_uids.append(match.message.uid)

    if not missing_uids:
        return _MailboxHistoryBodyFetchResult(
            messages=[],
            command_count=0,
            matched_header_count=len(matched_headers),
            covered_all_headers=covered_all_headers,
        )

    body_fetch_command_count = _history_body_fetch_command_count(
        len(missing_uids),
        get_settings().imap_fetch_batch_size,
    )
    messages = await mail_runtime.fetch_professor_history_mailbox_messages_by_uid(
        identity,
        state.folder,
        missing_uids,
    )
    fetched_uids = {message.uid for message in messages}
    missing_after_fetch = [uid for uid in missing_uids if uid not in fetched_uids]
    if missing_after_fetch:
        raise RuntimeError(f"IMAP history body fetch incomplete for UIDs: {missing_after_fetch}")
    return _MailboxHistoryBodyFetchResult(
        messages=messages,
        command_count=body_fetch_command_count,
        matched_header_count=len(matched_headers),
        covered_all_headers=covered_all_headers,
    )


async def _match_history_mailbox_headers(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    folder_role: str,
    header_messages: list[ImapFetchedMessage],
) -> list[_MailboxHistoryHeaderMatch]:
    if not header_messages:
        return []
    professor_ids_by_email = await _load_active_professor_ids_by_email(session_factory)
    if not professor_ids_by_email:
        return []

    matches: list[_MailboxHistoryHeaderMatch] = []
    for message in header_messages:
        if folder_role == "inbox":
            candidate_emails = [normalize_email_address(message.from_email)]
        elif folder_role == "sent":
            candidate_emails = normalize_email_list(
                [*message.to_emails, *message.cc_emails, *message.bcc_emails],
            )
        else:
            _validate_imap_folder_role(folder_role)
            candidate_emails = []

        professor_ids = tuple(
            dict.fromkeys(
                professor_id
                for email in candidate_emails
                if email
                for professor_id in professor_ids_by_email.get(email, [])
            ),
        )
        if professor_ids:
            matches.append(_MailboxHistoryHeaderMatch(message=message, professor_ids=professor_ids))
    return matches


async def _load_active_professor_ids_by_email(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, list[int]]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Professor.id, Professor.email).where(
                    Professor.archived_at.is_(None),
                    Professor.email.is_not(None),
                ),
            )
        ).all()
    professor_ids_by_email: dict[str, list[int]] = {}
    for professor_id, email in rows:
        normalized_email = normalize_email_address(email)
        if not normalized_email:
            continue
        professor_ids_by_email.setdefault(normalized_email, []).append(professor_id)
    return professor_ids_by_email


async def _history_mailbox_header_already_ingested(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    state: ImapMailboxSyncState,
    message: ImapFetchedMessage,
    professor_ids: tuple[int, ...],
) -> bool:
    if not professor_ids:
        return True
    direction = EmailDirection.RECEIVED.value if state.folder_role == "inbox" else EmailDirection.SENT.value
    expected_professor_ids = set(professor_ids)
    normalized_message_id = (message.message_id or "").strip().lower()
    async with session_factory() as session:
        if normalized_message_id:
            rows = (
                await session.execute(
                    select(EmailLog.professor_id).where(
                        EmailLog.identity_id == identity_id,
                        EmailLog.professor_id.in_(expected_professor_ids),
                        EmailLog.direction == direction,
                        or_(
                            EmailLog.normalized_message_id == normalized_message_id,
                            func.lower(EmailLog.rfc_message_id) == normalized_message_id,
                        ),
                    ),
                )
            ).all()
            if {professor_id for (professor_id,) in rows} >= expected_professor_ids:
                return True
        if message.uidvalidity is None:
            return False
        rows = (
            await session.execute(
                select(EmailLog.professor_id).where(
                    EmailLog.identity_id == identity_id,
                    EmailLog.professor_id.in_(expected_professor_ids),
                    EmailLog.folder_role == state.folder_role,
                    EmailLog.folder == state.folder,
                    EmailLog.uidvalidity == message.uidvalidity,
                    EmailLog.imap_uid == message.uid,
                ),
            )
        ).all()
    return {professor_id for (professor_id,) in rows} >= expected_professor_ids


async def _fetch_missing_history_message_bodies(
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityProfile,
    *,
    identity_id: int,
    state: ImapProfessorSyncState,
    header_messages: list[ImapFetchedMessage],
    remaining_command_budget: int,
) -> _HistoryBodyFetchResult:
    if not header_messages:
        return _HistoryBodyFetchResult(
            messages=[],
            command_count=0,
            highest_scanned_uid=state.last_scanned_uid,
            covered_all_headers=True,
        )
    sorted_headers = sorted(header_messages, key=lambda message: message.uid)
    missing_uids: list[int] = []
    highest_scanned_uid = state.last_scanned_uid
    covered_all_headers = True
    allowed_uid_count = None
    for message in sorted_headers:
        if await _history_header_already_ingested(
            session_factory,
            identity_id=identity_id,
            state=state,
            message=message,
        ):
            highest_scanned_uid = _max_optional_uid(highest_scanned_uid, message.uid)
            continue
        if allowed_uid_count is None:
            allowed_uid_count = _history_body_fetch_uid_limit(
                remaining_command_budget,
                get_settings().imap_fetch_batch_size,
            )
            if allowed_uid_count <= 0:
                raise RuntimeError("IMAP history command budget exhausted before body fetch")
        if len(missing_uids) >= allowed_uid_count:
            covered_all_headers = False
            break
        missing_uids.append(message.uid)
        highest_scanned_uid = _max_optional_uid(highest_scanned_uid, message.uid)
    if not missing_uids:
        return _HistoryBodyFetchResult(
            messages=[],
            command_count=0,
            highest_scanned_uid=highest_scanned_uid,
            covered_all_headers=covered_all_headers,
        )
    body_fetch_command_count = _history_body_fetch_command_count(
        len(missing_uids),
        get_settings().imap_fetch_batch_size,
    )
    messages = await mail_runtime.fetch_professor_history_mailbox_messages_by_uid(
        identity,
        state.folder,
        missing_uids,
    )
    fetched_uids = {message.uid for message in messages}
    missing_after_fetch = [uid for uid in missing_uids if uid not in fetched_uids]
    if missing_after_fetch:
        raise RuntimeError(f"IMAP history body fetch incomplete for UIDs: {missing_after_fetch}")
    return _HistoryBodyFetchResult(
        messages=messages,
        command_count=body_fetch_command_count,
        highest_scanned_uid=highest_scanned_uid,
        covered_all_headers=covered_all_headers,
    )


def _history_body_fetch_command_count(message_count: int, batch_size: int) -> int:
    if message_count <= 0:
        return 0
    effective_batch_size = max(1, batch_size)
    return (
        (message_count + effective_batch_size - 1) // effective_batch_size
        + message_count * IMAP_HISTORY_BODY_FETCH_COMMANDS_PER_MESSAGE
    )


def _history_body_fetch_uid_limit(remaining_command_budget: int, batch_size: int) -> int:
    if remaining_command_budget <= 0:
        return 0
    effective_batch_size = max(1, batch_size)
    allowed = 0
    while True:
        candidate = allowed + 1
        if _history_body_fetch_command_count(candidate, effective_batch_size) > remaining_command_budget:
            return allowed
        allowed = candidate


def _max_optional_uid(current: int | None, uid: int) -> int:
    return max(current or 0, uid)


async def _history_header_already_ingested(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    state: ImapProfessorSyncState,
    message: ImapFetchedMessage,
) -> bool:
    async with session_factory() as session:
        professor = await session.get(Professor, state.professor_id)
        if professor is None:
            return False
        direction = EmailDirection.RECEIVED.value if state.folder_role == "inbox" else EmailDirection.SENT.value
        normalized_message_id = (message.message_id or "").strip().lower()
        if normalized_message_id:
            existing_by_message = await session.scalar(
                select(EmailLog.id).where(
                    EmailLog.identity_id == identity_id,
                    EmailLog.professor_id == professor.id,
                    EmailLog.direction == direction,
                    or_(
                        EmailLog.normalized_message_id == normalized_message_id,
                        func.lower(EmailLog.rfc_message_id) == normalized_message_id,
                    ),
                ),
            )
            if existing_by_message is not None:
                return True
        if message.uidvalidity is None:
            return False
        existing_by_uid = await session.scalar(
            select(EmailLog.id).where(
                EmailLog.identity_id == identity_id,
                EmailLog.professor_id == professor.id,
                EmailLog.folder_role == state.folder_role,
                EmailLog.folder == state.folder,
                EmailLog.uidvalidity == message.uidvalidity,
                EmailLog.imap_uid == message.uid,
            ),
        )
        return existing_by_uid is not None


async def sync_identity_incremental_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    folder_role: str = "inbox",
    folder: str = "INBOX",
) -> int:
    _validate_imap_folder_role(folder_role)
    async with session_factory() as session:
        identity = await session.get(IdentityProfile, identity_id)
        if identity is None:
            return 0
        state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role=folder_role,
            folder=folder,
        )
        if not _identity_has_imap_config(identity):
            state.last_error = None
            await session.commit()
            return 0
        last_seen_uid = state.last_seen_uid
        expected_uidvalidity = state.uidvalidity
        should_bootstrap_history_cursor = (
            last_seen_uid is None
            and state.history_high_water_uid is None
            and state.history_scan_status != ImapMailboxHistoricalScanStatus.COMPLETED.value
        )
        await session.commit()
    try:
        if should_bootstrap_history_cursor:
            bootstrap_result = await mail_runtime.fetch_history_mailbox_message_headers_before_uid(
                identity,
                folder,
                before_uid=None,
                limit=0,
                max_fetch_batches=0,
                expected_uidvalidity=expected_uidvalidity,
            )
            if bootstrap_result.high_water_uid is not None:
                async with session_factory() as session:
                    state = await _get_or_create_mailbox_state(
                        session,
                        identity_id,
                        folder_role=folder_role,
                        folder=folder,
                    )
                    if bootstrap_result.uidvalidity is not None:
                        state.uidvalidity = bootstrap_result.uidvalidity
                    state.history_high_water_uid = bootstrap_result.high_water_uid
                    state.history_next_before_uid = bootstrap_result.next_before_uid
                    state.last_seen_uid = max(state.last_seen_uid or 0, bootstrap_result.high_water_uid)
                    state.last_sync_at = utc_now()
                    state.last_error = None
                    await session.commit()
                return 0
            async with session_factory() as session:
                state = await _get_or_create_mailbox_state(
                    session,
                    identity_id,
                    folder_role=folder_role,
                    folder=folder,
                )
                if bootstrap_result.uidvalidity is not None:
                    state.uidvalidity = bootstrap_result.uidvalidity
                state.last_sync_at = utc_now()
                state.last_error = "IMAP high-water bootstrap failed; incremental sync skipped to avoid full mailbox fetch"
                await session.commit()
            return 0
        fetch_with_uidvalidity = getattr(
            mail_runtime,
            "fetch_incremental_mailbox_messages_with_uidvalidity",
            None,
        )
        used_uidvalidity_aware_fetch = callable(fetch_with_uidvalidity)
        if used_uidvalidity_aware_fetch:
            max_seen_uid, messages, current_uidvalidity = await fetch_with_uidvalidity(
                identity,
                folder,
                last_seen_uid,
                expected_uidvalidity=expected_uidvalidity,
            )
        else:
            max_seen_uid, messages = await mail_runtime.fetch_incremental_mailbox_messages(
                identity,
                folder,
                last_seen_uid,
            )
            current_uidvalidity = _resolve_messages_uidvalidity(messages)
    except Exception as exc:
        async with session_factory() as session:
            state = await _get_or_create_mailbox_state(
                session,
                identity_id,
                folder_role=folder_role,
                folder=folder,
            )
            state.last_error = str(exc)
            await session.commit()
        if is_provider_throttle_error(exc):
            await mark_imap_throttled(
                session_factory,
                identity_id,
                reason=str(exc),
                account_level=True,
            )
        if folder_role == "sent" and _is_imap_mailbox_selection_error(exc):
            await clear_identity_sent_folder_discovery_cache(session_factory, identity_id)
        return 0
    detected = await process_imap_fetched_messages(
        session_factory,
        identity_id,
        messages,
        folder_role=folder_role,
        folder=folder,
    )
    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role=folder_role,
            folder=folder,
        )
        uidvalidity_changed = (
            current_uidvalidity is not None
            and current_uidvalidity != state.uidvalidity
        )
        if uidvalidity_changed:
            state.last_seen_uid = None
        if current_uidvalidity is not None:
            state.uidvalidity = current_uidvalidity
        should_apply_max_seen_uid = not (uidvalidity_changed and not used_uidvalidity_aware_fetch)
        if max_seen_uid is not None and should_apply_max_seen_uid:
            state.last_seen_uid = max(state.last_seen_uid or 0, max_seen_uid)
        state.last_sync_at = utc_now()
        state.last_error = None
        await session.commit()
    return detected


def _resolve_messages_uidvalidity(messages: list[ImapFetchedMessage]) -> int | None:
    for message in messages:
        if message.uidvalidity is not None:
            return message.uidvalidity
    return None


def _is_imap_mailbox_selection_error(exc: object) -> bool:
    text = str(exc).lower()
    return (
        "imap 选择邮箱文件夹失败" in text
        or "select" in text and "mailbox" in text
        or "no such mailbox" in text
    )


def _is_history_command_budget_error(exc: object) -> bool:
    return "imap history command budget" in str(exc).lower()


def _identity_has_imap_config(identity: IdentityProfile) -> bool:
    return bool(
        identity.imap_host
        and str(identity.imap_host).strip()
        and identity.imap_port
        and identity.imap_username
        and str(identity.imap_username).strip()
        and identity.imap_password
        and str(identity.imap_password).strip()
    )


async def sync_workspace_professor_replies(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    professor_id: int,
) -> int:
    if await _is_imap_history_work_locked(identity_id):
        return 0
    lock = await _get_imap_history_lock(identity_id)
    async with lock:
        if await _is_imap_identity_locked(identity_id):
            return 0
        async with session_factory() as session:
            identity = await session.get(IdentityProfile, identity_id)
            professor = await session.get(Professor, professor_id)
        if identity is None or professor is None or not professor.email:
            return 0
        messages = await mail_runtime.fetch_professor_history_inbox_messages(
            identity,
            professor.email,
        )
        return await process_imap_fetched_messages(session_factory, identity_id, messages)


async def _get_or_create_mailbox_state(
    session: AsyncSession,
    identity_id: int,
    *,
    folder_role: str = "inbox",
    folder: str = "INBOX",
) -> ImapMailboxSyncState:
    state = await session.scalar(
        select(ImapMailboxSyncState).where(
            ImapMailboxSyncState.identity_id == identity_id,
            ImapMailboxSyncState.folder_role == folder_role,
            ImapMailboxSyncState.folder == folder,
        ),
    )
    if state is not None:
        return state
    state = ImapMailboxSyncState(
        identity_id=identity_id,
        folder_role=folder_role,
        folder=folder,
    )
    session.add(state)
    await session.flush()
    return state


async def _get_imap_identity_lock(identity_id: int) -> asyncio.Lock:
    return await _get_imap_lock(_IMAP_IDENTITY_LOCKS, identity_id)


async def _get_imap_incremental_lock(identity_id: int) -> asyncio.Lock:
    return await _get_imap_lock(_IMAP_INCREMENTAL_LOCKS, identity_id)


async def _get_imap_history_lock(identity_id: int) -> asyncio.Lock:
    return await _get_imap_lock(_IMAP_HISTORY_LOCKS, identity_id)


async def _is_imap_identity_locked(identity_id: int) -> bool:
    return (await _get_imap_identity_lock(identity_id)).locked()


async def _is_imap_history_work_locked(identity_id: int) -> bool:
    return (
        await _is_imap_identity_locked(identity_id)
        or (await _get_imap_incremental_lock(identity_id)).locked()
        or (await _get_imap_history_lock(identity_id)).locked()
    )


async def _get_imap_lock(
    locks: dict[int, asyncio.Lock],
    identity_id: int,
) -> asyncio.Lock:
    async with _IMAP_IDENTITY_LOCKS_GUARD:
        lock = locks.get(identity_id)
        if lock is None:
            lock = asyncio.Lock()
            locks[identity_id] = lock
        return lock


async def repair_identity_replies(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    professor_email: str | None = None,
) -> int:
    async with session_factory() as session:
        identity = await session.get(IdentityProfile, identity_id)
    if not identity:
        return 0

    if professor_email and professor_email.strip():
        if await _is_imap_history_work_locked(identity_id):
            return 0
        lock = await _get_imap_history_lock(identity_id)
        async with lock:
            if await _is_imap_identity_locked(identity_id):
                return 0
            messages = await mail_runtime.fetch_professor_history_inbox_messages(identity, professor_email)
            return await process_imap_fetched_messages(session_factory, identity_id, messages)
    return await sync_identity_imap_once(session_factory, identity_id)


async def _process_incoming_reply_messages(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    messages: list[ReceivedEmail],
    *,
    fetched_messages: list[ImapFetchedMessage] | None = None,
    folder_role: str = "inbox",
    folder: str = "INBOX",
) -> int:
    detected = 0
    fetched_by_message_id = {
        fetched.message_id: fetched for fetched in fetched_messages or [] if fetched.message_id
    }
    fetched_by_uid = {fetched.uid: fetched for fetched in fetched_messages or []}
    for index, message in enumerate(messages):
        async with session_factory() as session:
            reply_created_at = _get_reply_created_at(message)
            fetched = None
            if message.message_id:
                fetched = fetched_by_message_id.get(message.message_id)
            if fetched is None and fetched_messages and index < len(fetched_messages):
                candidate = fetched_messages[index]
                if candidate.uid in fetched_by_uid:
                    fetched = candidate
            task = await _find_reply_target(session, identity_id, message)
            if not task:
                professor = await _find_existing_professor_for_incoming_message(session, message)
                if professor is not None:
                    await _upsert_unbound_received_log(
                        session,
                        identity_id=identity_id,
                        professor=professor,
                        message=message,
                        fetched=fetched,
                        reply_created_at=reply_created_at,
                        folder_role=folder_role,
                        folder=folder,
                    )
                    await session.commit()
                    detected += 1
                continue

            existing = await _find_existing_received_log_for_reply(
                session,
                task,
                message.message_id,
            )
            if existing is not None:
                was_already_replied = task.is_replied and task.status == EmailTaskStatus.REPLY_DETECTED.value
                _mark_task_reply_detected(task)
                changed = _backfill_existing_reply(existing, message, reply_created_at)
                if changed:
                    session.add(existing)
                if changed or not was_already_replied:
                    if not was_already_replied:
                        await _record_email_task_log(
                            session,
                            task,
                            "email_task.reply_detected",
                            metadata={"message_id": message.message_id},
                        )
                    await session.commit()
                if not was_already_replied:
                    detected += 1
                continue

            _mark_task_reply_detected(task)
            try:
                await upsert_email_log(
                    session,
                    EmailLogIngestRecord(
                        email_task_id=task.id,
                        identity_id=task.identity_id,
                        llm_profile_id=task.llm_profile_id,
                        professor_id=task.professor_id,
                        direction=EmailDirection.RECEIVED.value,
                        subject=message.subject,
                        content=message.content,
                        content_html=message.content_html,
                        message_id=message.message_id,
                        from_email=fetched.from_email if fetched is not None else message.from_email,
                        to_emails=fetched.to_emails if fetched is not None else None,
                        cc_emails=fetched.cc_emails if fetched is not None else None,
                        bcc_emails=fetched.bcc_emails if fetched is not None else None,
                        created_at=reply_created_at,
                        ingest_source="imap",
                        folder_role=folder_role,
                        folder=folder,
                        uidvalidity=fetched.uidvalidity if fetched is not None else None,
                        imap_uid=fetched.uid if fetched is not None else None,
                        provider_payload=None,
                        reply_headers=message.headers,
                    ),
                )
                await _record_email_task_log(
                    session,
                    task,
                    "email_task.reply_detected",
                    metadata={"message_id": message.message_id},
                )
                await session.commit()
            except IntegrityError:
                await session.rollback()
                continue
            detected += 1
    return detected


async def _find_existing_professor_for_incoming_message(
    session: AsyncSession,
    message: ReceivedEmail,
) -> Professor | None:
    normalized_from_email = normalize_email_address(message.from_email)
    if not normalized_from_email:
        return None
    return await session.scalar(
        select(Professor)
        .where(
            Professor.archived_at.is_(None),
            func.lower(Professor.email) == normalized_from_email,
        )
        .order_by(Professor.updated_at.desc(), Professor.id.desc()),
    )


async def _upsert_unbound_received_log(
    session: AsyncSession,
    *,
    identity_id: int,
    professor: Professor,
    message: ReceivedEmail,
    fetched: ImapFetchedMessage | None,
    reply_created_at: datetime,
    folder_role: str,
    folder: str,
) -> None:
    await upsert_email_log(
        session,
        EmailLogIngestRecord(
            email_task_id=None,
            identity_id=identity_id,
            llm_profile_id=None,
            professor_id=professor.id,
            direction=EmailDirection.RECEIVED.value,
            subject=message.subject,
            content=message.content,
            content_html=message.content_html,
            message_id=message.message_id,
            from_email=fetched.from_email if fetched is not None else message.from_email,
            to_emails=fetched.to_emails if fetched is not None else None,
            cc_emails=fetched.cc_emails if fetched is not None else None,
            bcc_emails=fetched.bcc_emails if fetched is not None else None,
            created_at=reply_created_at,
            ingest_source="imap",
            folder_role=folder_role,
            folder=folder,
            uidvalidity=fetched.uidvalidity if fetched is not None else None,
            imap_uid=fetched.uid if fetched is not None else None,
            provider_payload=None,
            reply_headers=message.headers,
        ),
    )


def _mark_task_reply_detected(task: EmailTask) -> None:
    task.is_replied = True
    task.status = EmailTaskStatus.REPLY_DETECTED.value
    task.updated_at = utc_now()


async def _find_existing_received_log_for_reply(
    session: AsyncSession,
    task: EmailTask,
    message_id: str | None,
) -> EmailLog | None:
    normalized_message_id = (message_id or "").strip().lower()
    if not normalized_message_id:
        return None
    return await session.scalar(
        select(EmailLog).where(
            EmailLog.identity_id == task.identity_id,
            EmailLog.professor_id == task.professor_id,
            EmailLog.direction == EmailDirection.RECEIVED.value,
            or_(
                EmailLog.normalized_message_id == normalized_message_id,
                func.lower(EmailLog.rfc_message_id) == normalized_message_id,
            ),
        ),
    )


async def process_imap_fetched_messages(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    messages: list[ImapFetchedMessage],
    *,
    folder_role: str = "inbox",
    folder: str = "INBOX",
) -> int:
    _validate_imap_folder_role(folder_role)
    if folder_role == "sent":
        return await _process_sent_imap_fetched_messages(
            session_factory,
            identity_id,
            messages,
            folder_role=folder_role,
            folder=folder,
        )

    received_messages = [
        ReceivedEmail(
            from_email=message.from_email,
            subject=message.subject,
            content=message.body_text,
            content_html=message.body_html,
            message_id=message.message_id,
            in_reply_to=message.in_reply_to,
            references=message.references,
            sent_at=message.sent_at,
            received_at=message.received_at,
            headers=message.headers,
        )
        for message in messages
    ]
    return await _process_incoming_reply_messages(
        session_factory,
        identity_id,
        received_messages,
        fetched_messages=messages,
        folder_role=folder_role,
        folder=folder,
    )


def _validate_imap_folder_role(folder_role: str) -> None:
    if folder_role not in VALID_IMAP_FOLDER_ROLES:
        raise ValueError(f"Unsupported IMAP folder_role: {folder_role}")


async def _process_sent_imap_fetched_messages(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    messages: list[ImapFetchedMessage],
    *,
    folder_role: str,
    folder: str,
) -> int:
    detected = 0
    recipients_by_message = {
        id(message): normalize_email_list([*message.to_emails, *message.cc_emails, *message.bcc_emails])
        for message in messages
    }
    all_recipient_emails = sorted(
        {
            email
            for recipient_emails in recipients_by_message.values()
            for email in recipient_emails
        },
    )
    if not all_recipient_emails:
        return 0

    async with session_factory() as session:
        professors = list(
            (
                await session.execute(
                    select(Professor).where(
                        Professor.archived_at.is_(None),
                        func.lower(Professor.email).in_(all_recipient_emails),
                    ),
                )
            ).scalars(),
        )
        professors_by_email: dict[str, list[Professor]] = {}
        for professor in professors:
            normalized_email = normalize_email_address(professor.email)
            if not normalized_email:
                continue
            professors_by_email.setdefault(normalized_email, []).append(professor)
        for message in messages:
            matched_professors_by_id: dict[int, Professor] = {}
            for email in recipients_by_message[id(message)]:
                for professor in professors_by_email.get(email, []):
                    matched_professors_by_id.setdefault(professor.id, professor)
            matched_professors = list(matched_professors_by_id.values())
            for professor in matched_professors:
                task = await _find_sent_message_task_match(
                    session,
                    identity_id=identity_id,
                    professor_id=professor.id,
                    message_id=message.message_id,
                )
                if task is not None:
                    if task.status != EmailTaskStatus.REPLY_DETECTED.value:
                        task.status = EmailTaskStatus.SENT.value
                    task.sent_at = message.sent_at
                    if message.message_id:
                        task.last_rfc_message_id = message.message_id
                    task.updated_at = utc_now()

                await upsert_email_log(
                    session,
                    EmailLogIngestRecord(
                        email_task_id=task.id if task is not None else None,
                        identity_id=identity_id,
                        llm_profile_id=task.llm_profile_id if task is not None else None,
                        professor_id=professor.id,
                        direction=EmailDirection.SENT.value,
                        subject=message.subject,
                        content=message.body_text,
                        content_html=message.body_html,
                        message_id=message.message_id,
                        from_email=message.from_email,
                        to_emails=message.to_emails,
                        cc_emails=message.cc_emails,
                        bcc_emails=message.bcc_emails,
                        created_at=message.sent_at,
                        ingest_source="imap",
                        folder_role=folder_role,
                        folder=folder,
                        uidvalidity=message.uidvalidity,
                        imap_uid=message.uid,
                        provider_payload=None,
                        reply_headers=message.headers,
                    ),
                )
                detected += 1
        await session.commit()
    return detected


async def _find_sent_message_task_match(
    session: AsyncSession,
    *,
    identity_id: int,
    professor_id: int,
    message_id: str | None,
) -> EmailTask | None:
    normalized_message_id = (message_id or "").strip().lower()
    if not normalized_message_id:
        return None

    task = await session.scalar(
        select(EmailTask)
        .where(
            EmailTask.identity_id == identity_id,
            EmailTask.professor_id == professor_id,
            func.lower(EmailTask.last_rfc_message_id) == normalized_message_id,
        )
        .order_by(EmailTask.updated_at.desc(), EmailTask.id.desc()),
    )
    if task is not None:
        return task

    sent_log = await session.scalar(
        select(EmailLog)
        .where(
            EmailLog.identity_id == identity_id,
            EmailLog.professor_id == professor_id,
            EmailLog.direction == EmailDirection.SENT.value,
            EmailLog.email_task_id.is_not(None),
            or_(
                func.lower(EmailLog.rfc_message_id) == normalized_message_id,
                EmailLog.normalized_message_id == normalized_message_id,
            ),
        )
        .order_by(EmailLog.created_at.desc(), EmailLog.id.desc()),
    )
    if sent_log is None:
        return None
    return await session.get(EmailTask, sent_log.email_task_id)


def _backfill_existing_reply(
    existing: EmailLog,
    message: ReceivedEmail,
    reply_created_at: datetime,
) -> bool:
    changed = False
    if (not existing.content or _looks_like_raw_mime_content(existing.content)) and message.content:
        existing.content = message.content
        changed = True
    if (
        (not existing.content_html or _looks_like_raw_mime_content(existing.content_html))
        and message.content_html
    ):
        existing.content_html = message.content_html
        changed = True
    if not existing.reply_headers and message.headers:
        existing.reply_headers = message.headers
        changed = True
    if not _datetimes_match(existing.created_at, reply_created_at):
        existing.created_at = reply_created_at
        changed = True
    return changed


def _looks_like_raw_mime_content(content: str | None) -> bool:
    if not content:
        return False
    normalized = content[:2000].lower()
    return (
        "content-transfer-encoding:" in normalized
        or "content-type:" in normalized and "---=" in normalized
        or "body[section" in normalized
    )


def _get_reply_created_at(message: mail_runtime.ReceivedEmail) -> datetime:
    return message.received_at or message.sent_at


def _datetimes_match(left: datetime, right: datetime) -> bool:
    def normalize(value: datetime) -> datetime:
        if value.tzinfo is None:
            return as_utc_aware(value)
        return value.astimezone(UTC)

    return normalize(left) == normalize(right)


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


async def _find_reply_target(
    session: AsyncSession,
    identity_id: int,
    message: ReceivedEmail,
) -> EmailTask | None:
    normalized_from_email = normalize_email_address(message.from_email)
    reference_ids = extract_message_ids(message.in_reply_to, message.references)
    if reference_ids:
        matched_log = await session.scalar(
            select(EmailLog)
            .join(Professor, EmailLog.professor_id == Professor.id)
            .where(
                EmailLog.identity_id == identity_id,
                EmailLog.direction == EmailDirection.SENT.value,
                Professor.archived_at.is_(None),
                func.lower(Professor.email) == normalized_from_email,
                or_(
                    func.lower(EmailLog.rfc_message_id).in_(reference_ids),
                    EmailLog.normalized_message_id.in_(reference_ids),
                ),
            )
            .order_by(EmailLog.created_at.desc()),
        )
        if matched_log and matched_log.email_task_id:
            return await _load_email_task(session, matched_log.email_task_id)

    if not normalized_from_email:
        return None

    candidate_tasks = list(
        (
            await session.execute(
                select(EmailTask)
                .options(*TASK_RELATION_OPTIONS)
                .join(Professor, EmailTask.professor_id == Professor.id)
                .where(
                    EmailTask.identity_id == identity_id,
                    func.lower(Professor.email) == normalized_from_email,
                    EmailTask.status.in_(
                        [
                            EmailTaskStatus.SENT.value,
                            EmailTaskStatus.REPLY_DETECTED.value,
                            EmailTaskStatus.CANCELED.value,
                        ],
                    ),
                )
                .order_by(EmailTask.sent_at.desc(), EmailTask.updated_at.desc()),
            )
        ).scalars()
    )
    if not candidate_tasks:
        return None

    normalized_incoming_subject = normalize_subject(message.subject)
    if normalized_incoming_subject:
        for task in candidate_tasks:
            if normalize_subject(task.approved_subject or task.generated_subject) == normalized_incoming_subject:
                return task
    return candidate_tasks[0]


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

async def _load_email_task(session: AsyncSession, task_id: int) -> EmailTask | None:
    return await session.scalar(
        select(EmailTask)
        .options(*TASK_RELATION_OPTIONS)
        .where(EmailTask.id == task_id),
    )


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


async def _record_email_task_log(
    session: AsyncSession,
    task: EmailTask,
    event_name: str,
    *,
    level: str = "info",
    message: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    base_metadata: dict[str, object] = {
        "task_id": task.id,
        "source": task.source,
        "status": task.status,
        "batch_task_id": task.batch_task_id,
        "parent_task_id": task.parent_task_id,
        "professor_id": task.professor_id,
        "identity_id": task.identity_id,
        "llm_profile_id": task.llm_profile_id,
    }
    if metadata:
        base_metadata.update(metadata)
    await record_operation_log(
        session,
        category="email",
        event_name=event_name,
        level=level,
        message=message,
        entity_type="email_task",
        entity_id=str(task.id),
        metadata=base_metadata,
    )


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
    return resolve_outreach_template_config(
        task.identity,
        generation_mode=task.outreach_generation_mode,
        subject_template=task.outreach_template_subject,
        body_text_template=task.outreach_template_body_text,
        body_html_template=task.outreach_template_body_html,
    )


def _resolve_draft_generation_outreach_config(task: EmailTask):
    if task.batch_task_id is not None:
        return _resolve_task_outreach_config(task)

    return resolve_outreach_template_config(
        task.identity,
        generation_mode=task.outreach_generation_mode,
    )


def _build_task_outreach_snapshot(
    identity: IdentityProfile,
    *,
    outreach_generation_mode: str | None = None,
    outreach_template_subject: str | None = None,
    outreach_template_body_text: str | None = None,
    outreach_template_body_html: str | None = None,
    fallback_task: EmailTask | None = None,
) -> dict[str, str | None]:
    resolved = resolve_outreach_template_config(
        identity,
        generation_mode=(
            outreach_generation_mode
            if outreach_generation_mode is not None
            else fallback_task.outreach_generation_mode if fallback_task is not None else None
        ),
        subject_template=(
            outreach_template_subject
            if outreach_template_subject is not None
            else fallback_task.outreach_template_subject if fallback_task is not None else None
        ),
        body_text_template=(
            outreach_template_body_text
            if outreach_template_body_text is not None
            else fallback_task.outreach_template_body_text if fallback_task is not None else None
        ),
        body_html_template=(
            outreach_template_body_html
            if outreach_template_body_html is not None
            else fallback_task.outreach_template_body_html if fallback_task is not None else None
        ),
    )
    body_text = _normalize_nullable_text(resolved.body_text_template)
    body_html = _normalize_nullable_text(resolved.body_html_template)
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
) -> EmailTask:
    now = utc_now()
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
        match_score=task.match_score,
        match_reason=task.match_reason,
        generated_subject=task.generated_subject if reuse_existing_draft else None,
        generated_content_text=task.generated_content_text if reuse_existing_draft else None,
        generated_content_html=task.generated_content_html if reuse_existing_draft else None,
        outreach_generation_mode=task.outreach_generation_mode,
        outreach_template_subject=task.outreach_template_subject,
        outreach_template_body_text=task.outreach_template_body_text,
        outreach_template_body_html=task.outreach_template_body_html,
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
    if (
        task.status == EmailTaskStatus.CANCELED.value
        and task.cancellation_reason == EmailTaskCancellationReason.BATCH_STOPPED.value
    ):
        raise ValueError("该任务已因批量任务停止而取消，请先“作为单独联系继续”后再执行此操作")


def _ensure_task_not_generating_for_workspace_change(task: EmailTask) -> None:
    if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
        raise ValueError("AI 正在改写当前草稿，请等待完成后再修改。")


def _ensure_task_allows_approval(task: EmailTask) -> None:
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


def extract_message_ids(*headers: str | None) -> set[str]:
    values: set[str] = set()
    for header in headers:
        if not header:
            continue
        values.update(value.lower() for value in re.findall(r"<[^>]+>", header))
    return values


def normalize_subject(subject: str | None) -> str:
    if not subject:
        return ""
    normalized = subject.strip().lower()
    normalized = re.sub(r"^(re|fw|fwd)\s*:\s*", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized
