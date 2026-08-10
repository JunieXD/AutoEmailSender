from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from app.core.agent_runtime_descriptor import get_runtime_id
from app.core.backend_role import get_backend_role
from app.core.config import get_settings
from app.core.fault_injection import wait_at_fault_point
from app.core.process_liveness import process_is_running
from app.core.runtime_group import read_runtime_process_status
from app.core.time import as_utc_aware, local_now as get_local_now, utc_now
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.models import (
    BatchTask,
    BatchTaskStatus,
    EmailDirection,
    EmailDeliveryAttempt,
    EmailDeliveryOutcome,
    EmailLog,
    EmailTaskCancellationReason,
    EmailTask,
    EmailTaskStatus,
    EmailTaskSource,
    IdentityMaterial,
    IdentityProfile,
    Professor,
)
from app.modules.campaigns.public import (
    build_send_template_context,
    has_future_batch_window,
    is_batch_window_expired,
    is_datetime_in_batch_window,
    normalize_scheduled_dates,
    render_template_with_context,
    sync_batch_task_completion,
)
from app.modules.communications.public import (
    MailAttachment,
    load_email_task as _load_email_task,
    record_email_task_log as _record_email_task_log,
    transport as mail_runtime,
)
from app.modules.identities.public import build_material_download_name
from app.services.operation_logs import (
    record_operation_log,
    sanitize_user_visible_error,
)

__all__ = [
    "DEFAULT_SEND_INTERVAL_MAX_SECONDS",
    "DEFAULT_SEND_INTERVAL_MIN_SECONDS",
    "dispatch_due_tasks_once",
    "dispatch_email_task",
    "expire_batch_task_if_needed",
    "mark_overdue_manual_schedules_missed",
    "process_pending_drafts_once",
    "recover_stale_sending_tasks",
]

DISPATCHABLE_EMAIL_TASK_STATUSES = (
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
)

STALE_SENDING_TASK_AFTER = timedelta(minutes=30)
DELIVERY_ABANDONED_MARKER_DIRECTORY = "abandoned-delivery-attempts"

SCHEDULED_BATCH_SEND_GRACE_PERIOD = timedelta(minutes=2)

STARTUP_MANUAL_SCHEDULE_GRACE_PERIOD = timedelta(minutes=2)

DEFAULT_SEND_INTERVAL_MIN_SECONDS = 1

DEFAULT_SEND_INTERVAL_MAX_SECONDS = 5
TIMESTAMP_CAS_EPSILON = timedelta(microseconds=1)

_DELIVERY_PROCESS_GENERATION = uuid.uuid4().hex
_ABANDONED_ATTEMPTS_IN_PROCESS: set[str] = set()
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeliveryOwnerIdentity:
    role: str
    runtime_id: str
    generation: str
    pid: int


@dataclass(frozen=True, slots=True)
class DeliveryPreparationSnapshot:
    task_id: int
    source: str
    batch_task_id: int | None
    parent_task_id: int | None
    identity_id: int
    identity_expected_updated_at: datetime
    llm_profile_id: int
    professor_id: int
    expected_updated_at: datetime
    batch_expected_updated_at: datetime | None
    subject: str
    body_text: str
    body_html: str | None
    attachment_count: int
    identity: IdentityProfile
    professor: Professor
    attachments: tuple[MailAttachment, ...]


@dataclass(frozen=True, slots=True)
class PreparedDeliverySnapshot:
    task_id: int
    source: str
    batch_task_id: int | None
    parent_task_id: int | None
    identity_id: int
    identity_expected_updated_at: datetime
    llm_profile_id: int
    professor_id: int
    expected_updated_at: datetime
    batch_expected_updated_at: datetime | None
    subject: str
    body_text: str
    body_html: str | None
    attachment_count: int
    identity: IdentityProfile
    prepared_email: mail_runtime.PreparedEmail


@dataclass(frozen=True, slots=True)
class DeliveryFinalizationSnapshot:
    task_id: int
    attempt_id: str
    source: str
    batch_task_id: int | None
    parent_task_id: int | None
    identity_id: int
    llm_profile_id: int
    professor_id: int
    subject: str
    body_text: str
    body_html: str | None
    attachment_count: int


async def mark_overdue_manual_schedules_missed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> int:
    """Prevent a restarted desktop app from silently sending stale manual schedules."""
    now_utc = as_utc_aware(now or utc_now())
    cutoff = now_utc - STARTUP_MANUAL_SCHEDULE_GRACE_PERIOD
    async with session_factory() as session:
        tasks = list(
            (
                await session.execute(
                    select(EmailTask).where(
                        EmailTask.source == EmailTaskSource.MANUAL.value,
                        EmailTask.batch_task_id.is_(None),
                        EmailTask.status == EmailTaskStatus.SCHEDULED.value,
                        EmailTask.scheduled_at.is_not(None),
                        EmailTask.scheduled_at < cutoff,
                    ),
                )
            ).scalars()
        )
        for task in tasks:
            task.status = EmailTaskStatus.SCHEDULE_MISSED.value
            task.updated_at = now_utc
            await _record_email_task_log(
                session,
                task,
                "email_task.schedule_missed",
                metadata={
                    "scheduled_at": task.scheduled_at.isoformat()
                    if task.scheduled_at
                    else None,
                },
            )
        if tasks:
            await session.commit()
        return len(tasks)


def _is_user_removed_batch_item(email_task: EmailTask) -> bool:
    return (
        email_task.status == EmailTaskStatus.CANCELED.value
        and email_task.cancellation_reason
        == EmailTaskCancellationReason.USER_REMOVED.value
    )


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
                        .join(
                            BatchTask,
                            EmailTask.batch_task_id == BatchTask.id,
                            isouter=True,
                        )
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
                        count = await _batch_task_sent_count_on_date(
                            session, batch_task.id, local_now
                        )
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
        )
        .scalars()
        .unique()
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


def _resolve_identity_send_interval_seconds(
    identity: IdentityProfile,
) -> tuple[int, int]:
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
    expected_updated_at: datetime | None = None,
) -> bool:
    min_seconds, max_seconds = _resolve_identity_send_interval_seconds(identity)
    next_send_after = now + timedelta(
        seconds=random.uniform(min_seconds, max_seconds),
    )
    conditions = [IdentityProfile.id == identity.id]
    if expected_updated_at is not None:
        conditions.extend(
            _timestamp_cas_conditions(
                IdentityProfile.updated_at,
                expected_updated_at,
            )
        )
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
        if (
            _is_user_removed_batch_item(email_task)
            or email_task.batch_send_canceled_at is not None
        ):
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
            email_task.cancellation_reason = (
                EmailTaskCancellationReason.SCHEDULE_EXPIRED.value
            )
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
    """Conservatively finalize abandoned delivery claims without redispatching.

    A delivery claim is the irreversible boundary.  Wall-clock age is only used
    for legacy/corrupt rows that do not have an attempt owner; a live owner is
    never fenced merely because SMTP is taking a long time.
    """

    resolved_now = as_utc_aware(now) if now is not None else utc_now()
    cutoff = resolved_now - stale_after
    async with session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(EmailTask, EmailDeliveryAttempt)
                    .join(
                        EmailDeliveryAttempt,
                        EmailDeliveryAttempt.attempt_id
                        == EmailTask.delivery_attempt_id,
                        isouter=True,
                    )
                    .where(EmailTask.status == EmailTaskStatus.SENDING.value),
                )
            ).all()
        )

    recovered = 0
    for task, attempt in rows:
        if attempt is None:
            last_activity_at = task.last_send_attempt_at or task.updated_at
            if as_utc_aware(last_activity_at) >= cutoff:
                continue
            attempt = await _claim_legacy_sending_for_recovery(
                session_factory,
                task,
                resolved_now,
            )
            if attempt is None:
                continue

        explicitly_abandoned = _delivery_attempt_is_explicitly_abandoned(
            attempt.attempt_id
        )
        if not explicitly_abandoned and _delivery_owner_is_active(attempt):
            continue

        finalization = _build_recovery_finalization_snapshot(task, attempt)
        finalized = await _finalize_delivery_attempt(
            session_factory,
            finalization,
            outcome=EmailDeliveryOutcome.ASSUMED_SENT_AFTER_INTERRUPTION,
            finalized_at=resolved_now,
            rfc_message_id=None,
            provider_payload={
                "delivery_outcome": (
                    EmailDeliveryOutcome.ASSUMED_SENT_AFTER_INTERRUPTION.value
                ),
                "recovery_reason": (
                    "attempt_explicitly_abandoned"
                    if explicitly_abandoned
                    else "delivery_owner_no_longer_active"
                ),
            },
            error_summary=(
                attempt.error_summary
                or "发送所有者已失效；为防止重复发送，保守视为已发送"
            ),
            inject_faults=False,
        )
        if finalized:
            recovered += 1
            _clear_delivery_abandoned_marker(attempt.attempt_id)
    return recovered


async def dispatch_email_task(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    now: datetime | None = None,
    respect_identity_send_window: bool = True,
) -> bool:
    prepared_at = as_utc_aware(now) if now is not None else utc_now()
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
        if _is_task_scheduled_for_future(task, prepared_at):
            return False

        subject_template = task.approved_subject or task.generated_subject
        body_text_template = task.approved_body_text or task.generated_content_text
        body_html_template = task.approved_body_html or task.generated_content_html
        context = build_send_template_context(
            task.identity,
            task.professor,
            local_timezone=get_local_now().tzinfo,
        )
        subject = render_template_with_context(subject_template or "", context).strip()
        body_text = render_template_with_context(body_text_template or "", context).strip()
        body_html = (
            render_template_with_context(body_html_template, context)
            if body_html_template
            else None
        )
        attachments = await _resolve_selected_materials(
            session,
            task.identity_id,
            task.selected_material_ids,
        )
        preparation = DeliveryPreparationSnapshot(
            task_id=task.id,
            source=task.source,
            batch_task_id=task.batch_task_id,
            parent_task_id=task.parent_task_id,
            identity_id=task.identity_id,
            identity_expected_updated_at=task.identity.updated_at,
            llm_profile_id=task.llm_profile_id,
            professor_id=task.professor_id,
            expected_updated_at=task.updated_at,
            batch_expected_updated_at=(
                task.batch_task.updated_at if task.batch_task is not None else None
            ),
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            attachment_count=len(attachments),
            identity=task.identity,
            professor=task.professor,
            attachments=tuple(attachments),
        )

    if not preparation.subject or not preparation.body_text:
        return await _record_pre_claim_delivery_failure(
            session_factory,
            preparation,
            "任务缺少可发送的主题或正文",
        )

    try:
        prepared_email = await mail_runtime.prepare_email(
            identity=preparation.identity,
            professor=preparation.professor,
            subject=preparation.subject,
            body_text=preparation.body_text,
            body_html=preparation.body_html,
            attachments=list(preparation.attachments),
        )
    except mail_runtime.MailRuntimeError as exc:
        return await _record_pre_claim_delivery_failure(
            session_factory,
            preparation,
            sanitize_user_visible_error(exc),
        )

    prepared = PreparedDeliverySnapshot(
        task_id=preparation.task_id,
        source=preparation.source,
        batch_task_id=preparation.batch_task_id,
        parent_task_id=preparation.parent_task_id,
        identity_id=preparation.identity_id,
        identity_expected_updated_at=preparation.identity_expected_updated_at,
        llm_profile_id=preparation.llm_profile_id,
        professor_id=preparation.professor_id,
        expected_updated_at=preparation.expected_updated_at,
        batch_expected_updated_at=preparation.batch_expected_updated_at,
        subject=preparation.subject,
        body_text=preparation.body_text,
        body_html=preparation.body_html,
        attachment_count=preparation.attachment_count,
        identity=preparation.identity,
        prepared_email=prepared_email,
    )

    await wait_at_fault_point("delivery.before_claim")
    owner = _resolve_delivery_owner_identity()
    claimed_at = as_utc_aware(now) if now is not None else utc_now()
    attempt_id = await _claim_prepared_delivery(
        session_factory,
        prepared,
        owner,
        claimed_at=claimed_at,
        respect_identity_send_window=respect_identity_send_window,
    )
    if attempt_id is None:
        return False

    finalization = _build_finalization_snapshot(prepared, attempt_id)
    try:
        await wait_at_fault_point("delivery.claim_committed")
        await wait_at_fault_point("delivery.before_smtp")
        result = await mail_runtime.send_prepared_email(
            identity=prepared.identity,
            prepared=prepared.prepared_email,
        )
        await wait_at_fault_point("delivery.smtp_accepted")
    except asyncio.CancelledError:
        await _finalize_canceled_delivery(
            session_factory,
            finalization,
            "发送进程在结果确认前被取消",
        )
        raise
    except mail_runtime.MailDeliveryError as exc:
        if exc.safe_to_retry:
            return await _finalize_claimed_delivery(
                session_factory,
                finalization,
                outcome=EmailDeliveryOutcome.PRE_SUBMISSION_FAILED,
                rfc_message_id=None,
                provider_payload={
                    "delivery_outcome": EmailDeliveryOutcome.PRE_SUBMISSION_FAILED.value,
                    "failure_kind": exc.failure_kind.value,
                },
                error_summary=sanitize_user_visible_error(exc),
            )
        return await _finalize_claimed_delivery(
            session_factory,
            finalization,
            outcome=EmailDeliveryOutcome.ASSUMED_SENT_AFTER_INTERRUPTION,
            rfc_message_id=None,
            provider_payload={
                "delivery_outcome": (
                    EmailDeliveryOutcome.ASSUMED_SENT_AFTER_INTERRUPTION.value
                ),
                "failure_kind": exc.failure_kind.value,
            },
            error_summary=sanitize_user_visible_error(exc),
        )
    except Exception as exc:
        return await _finalize_claimed_delivery(
            session_factory,
            finalization,
            outcome=EmailDeliveryOutcome.ASSUMED_SENT_AFTER_INTERRUPTION,
            rfc_message_id=None,
            provider_payload={
                "delivery_outcome": (
                    EmailDeliveryOutcome.ASSUMED_SENT_AFTER_INTERRUPTION.value
                ),
                "failure_kind": "unclassified_after_claim",
            },
            error_summary=sanitize_user_visible_error(exc),
        )

    return await _finalize_claimed_delivery(
        session_factory,
        finalization,
        outcome=EmailDeliveryOutcome.SMTP_ACCEPTED,
        rfc_message_id=result.message_id,
        provider_payload=result.provider_payload,
        error_summary=None,
    )


async def _record_pre_claim_delivery_failure(
    session_factory: async_sessionmaker[AsyncSession],
    snapshot: DeliveryPreparationSnapshot,
    error_summary: str,
) -> bool:
    failed_at = utc_now()
    conditions = _delivery_snapshot_conditions(snapshot, failed_at)
    async with session_factory() as session:
        result = await session.execute(
            update(EmailTask)
            .where(*conditions)
            .values(
                status=EmailTaskStatus.SEND_FAILED.value,
                delivery_attempt_id=None,
                delivery_outcome=None,
                delivery_outcome_at=None,
                last_error=error_summary,
                retry_count=func.coalesce(EmailTask.retry_count, 0) + 1,
                updated_at=failed_at,
            )
            .execution_options(synchronize_session=False),
        )
        if result.rowcount != 1:
            await session.rollback()
            return False
        session.add(
            EmailLog(
                email_task_id=snapshot.task_id,
                identity_id=snapshot.identity_id,
                llm_profile_id=snapshot.llm_profile_id,
                professor_id=snapshot.professor_id,
                direction=EmailDirection.SENT.value,
                subject=snapshot.subject,
                content=snapshot.body_text,
                content_html=snapshot.body_html,
                failure_summary=error_summary,
            )
        )
        await record_operation_log(
            session,
            category="email",
            event_name="email_task.send_preparation_failed",
            level="warning",
            message=error_summary,
            entity_type="email_task",
            entity_id=str(snapshot.task_id),
            metadata={
                "task_id": snapshot.task_id,
                "source": snapshot.source,
                "batch_task_id": snapshot.batch_task_id,
                "parent_task_id": snapshot.parent_task_id,
                "identity_id": snapshot.identity_id,
                "llm_profile_id": snapshot.llm_profile_id,
                "professor_id": snapshot.professor_id,
                "attachment_count": snapshot.attachment_count,
                "pre_claim": True,
            },
        )
        await session.commit()
        return True


def _delivery_snapshot_conditions(
    snapshot: DeliveryPreparationSnapshot | PreparedDeliverySnapshot,
    attempted_at: datetime,
) -> list[object]:
    conditions: list[object] = [
        EmailTask.id == snapshot.task_id,
        EmailTask.status.in_(DISPATCHABLE_EMAIL_TASK_STATUSES),
        *_timestamp_cas_conditions(
            EmailTask.updated_at,
            snapshot.expected_updated_at,
        ),
        EmailTask.batch_send_canceled_at.is_(None),
        or_(
            EmailTask.scheduled_at.is_(None),
            EmailTask.scheduled_at <= attempted_at,
        ),
    ]
    if snapshot.batch_task_id is None:
        conditions.append(EmailTask.batch_task_id.is_(None))
    else:
        conditions.extend(
            [
                EmailTask.batch_task_id == snapshot.batch_task_id,
                EmailTask.batch_task_id.in_(
                    select(BatchTask.id).where(
                        BatchTask.id == snapshot.batch_task_id,
                        BatchTask.status == BatchTaskStatus.RUNNING.value,
                        BatchTask.deleted_at.is_(None),
                        *_timestamp_cas_conditions(
                            BatchTask.updated_at,
                            snapshot.batch_expected_updated_at,
                        ),
                    )
                ),
            ]
        )
    return conditions


def _timestamp_cas_conditions(
    column: Any,
    expected: datetime | None,
) -> tuple[object, object]:
    if expected is None:
        return column.is_(None), column.is_(None)
    return (
        column >= expected - TIMESTAMP_CAS_EPSILON,
        column <= expected + TIMESTAMP_CAS_EPSILON,
    )


async def _claim_prepared_delivery(
    session_factory: async_sessionmaker[AsyncSession],
    snapshot: PreparedDeliverySnapshot,
    owner: DeliveryOwnerIdentity,
    *,
    claimed_at: datetime,
    respect_identity_send_window: bool,
) -> str | None:
    attempt_id = str(uuid.uuid4())
    async with session_factory() as session:
        if not await _reserve_identity_send_window(
            session,
            snapshot.identity,
            claimed_at,
            require_window_open=respect_identity_send_window,
            expected_updated_at=snapshot.identity_expected_updated_at,
        ):
            await session.rollback()
            return None

        claim_result = await session.execute(
            update(EmailTask)
            .where(*_delivery_snapshot_conditions(snapshot, claimed_at))
            .values(
                status=EmailTaskStatus.SENDING.value,
                last_send_attempt_at=claimed_at,
                delivery_attempt_id=attempt_id,
                delivery_outcome=EmailDeliveryOutcome.CLAIMED.value,
                delivery_outcome_at=claimed_at,
                last_error=None,
                retry_count=func.coalesce(EmailTask.retry_count, 0) + 1,
                updated_at=claimed_at,
            )
            .execution_options(synchronize_session=False),
        )
        if claim_result.rowcount != 1:
            await session.rollback()
            return None

        session.add(
            EmailDeliveryAttempt(
                attempt_id=attempt_id,
                email_task_id=snapshot.task_id,
                owner_role=owner.role,
                runtime_id=owner.runtime_id,
                owner_generation=owner.generation,
                owner_pid=owner.pid,
                outcome=EmailDeliveryOutcome.CLAIMED.value,
                claimed_at=claimed_at,
                prepared_rfc_message_id=snapshot.prepared_email.message_id,
                subject=snapshot.subject,
                content=snapshot.body_text,
                content_html=snapshot.body_html,
                attachment_count=snapshot.attachment_count,
            )
        )
        await session.commit()
    return attempt_id


def _build_finalization_snapshot(
    snapshot: PreparedDeliverySnapshot,
    attempt_id: str,
) -> DeliveryFinalizationSnapshot:
    return DeliveryFinalizationSnapshot(
        task_id=snapshot.task_id,
        attempt_id=attempt_id,
        source=snapshot.source,
        batch_task_id=snapshot.batch_task_id,
        parent_task_id=snapshot.parent_task_id,
        identity_id=snapshot.identity_id,
        llm_profile_id=snapshot.llm_profile_id,
        professor_id=snapshot.professor_id,
        subject=snapshot.subject,
        body_text=snapshot.body_text,
        body_html=snapshot.body_html,
        attachment_count=snapshot.attachment_count,
    )


async def _finalize_claimed_delivery(
    session_factory: async_sessionmaker[AsyncSession],
    snapshot: DeliveryFinalizationSnapshot,
    *,
    outcome: EmailDeliveryOutcome,
    rfc_message_id: str | None,
    provider_payload: dict[str, object] | None,
    error_summary: str | None,
) -> bool:
    try:
        await _finalize_delivery_attempt(
            session_factory,
            snapshot,
            outcome=outcome,
            finalized_at=utc_now(),
            rfc_message_id=rfc_message_id,
            provider_payload=provider_payload,
            error_summary=error_summary,
            inject_faults=True,
        )
    except BaseException as exc:
        _record_delivery_abandoned_marker(
            snapshot.attempt_id,
            sanitize_user_visible_error(exc),
        )
        raise
    _clear_delivery_abandoned_marker(snapshot.attempt_id)
    await wait_at_fault_point("delivery.after_final_commit")
    return True


async def _finalize_canceled_delivery(
    session_factory: async_sessionmaker[AsyncSession],
    snapshot: DeliveryFinalizationSnapshot,
    error_summary: str,
) -> None:
    finalize_task = asyncio.create_task(
        _finalize_delivery_attempt(
            session_factory,
            snapshot,
            outcome=EmailDeliveryOutcome.ASSUMED_SENT_AFTER_INTERRUPTION,
            finalized_at=utc_now(),
            rfc_message_id=None,
            provider_payload={
                "delivery_outcome": (
                    EmailDeliveryOutcome.ASSUMED_SENT_AFTER_INTERRUPTION.value
                ),
                "failure_kind": "delivery_coroutine_canceled_after_claim",
            },
            error_summary=error_summary,
            inject_faults=False,
        )
    )
    try:
        await asyncio.shield(finalize_task)
    except BaseException as exc:
        _record_delivery_abandoned_marker(
            snapshot.attempt_id,
            sanitize_user_visible_error(exc),
        )
    else:
        _clear_delivery_abandoned_marker(snapshot.attempt_id)


async def _finalize_delivery_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    snapshot: DeliveryFinalizationSnapshot,
    *,
    outcome: EmailDeliveryOutcome,
    finalized_at: datetime,
    rfc_message_id: str | None,
    provider_payload: dict[str, object] | None,
    error_summary: str | None,
    inject_faults: bool,
) -> bool:
    if outcome == EmailDeliveryOutcome.PRE_SUBMISSION_FAILED:
        task_status = EmailTaskStatus.SEND_FAILED.value
        event_name = "email_task.send_failed"
        event_level = "warning"
    elif outcome == EmailDeliveryOutcome.SMTP_ACCEPTED:
        task_status = EmailTaskStatus.SENT.value
        event_name = "email_task.sent"
        event_level = "info"
    else:
        task_status = EmailTaskStatus.SENT.value
        event_name = "email_task.assumed_sent_after_interruption"
        event_level = "warning"

    task_values: dict[str, object | None] = {
        "status": task_status,
        "delivery_outcome": outcome.value,
        "delivery_outcome_at": finalized_at,
        "last_error": (
            error_summary
            if outcome == EmailDeliveryOutcome.PRE_SUBMISSION_FAILED
            else None
        ),
        "updated_at": finalized_at,
    }
    if task_status == EmailTaskStatus.SENT.value:
        task_values["sent_at"] = finalized_at
    if outcome == EmailDeliveryOutcome.SMTP_ACCEPTED:
        task_values["last_rfc_message_id"] = rfc_message_id

    async with session_factory() as session:
        attempt_result = await session.execute(
            update(EmailDeliveryAttempt)
            .where(
                EmailDeliveryAttempt.attempt_id == snapshot.attempt_id,
                EmailDeliveryAttempt.email_task_id == snapshot.task_id,
                EmailDeliveryAttempt.outcome == EmailDeliveryOutcome.CLAIMED.value,
            )
            .values(
                outcome=outcome.value,
                finalized_at=finalized_at,
                smtp_accepted_at=(
                    finalized_at
                    if outcome == EmailDeliveryOutcome.SMTP_ACCEPTED
                    else None
                ),
                provider_payload=provider_payload,
                error_summary=error_summary,
            )
            .execution_options(synchronize_session=False),
        )
        if attempt_result.rowcount != 1:
            await session.rollback()
            return False

        task_result = await session.execute(
            update(EmailTask)
            .where(
                EmailTask.id == snapshot.task_id,
                EmailTask.status == EmailTaskStatus.SENDING.value,
                EmailTask.delivery_attempt_id == snapshot.attempt_id,
            )
            .values(**task_values)
            .execution_options(synchronize_session=False),
        )
        if task_result.rowcount != 1:
            await session.rollback()
            return False

        session.add(
            EmailLog(
                email_task_id=snapshot.task_id,
                delivery_attempt_id=snapshot.attempt_id,
                identity_id=snapshot.identity_id,
                llm_profile_id=snapshot.llm_profile_id,
                professor_id=snapshot.professor_id,
                direction=EmailDirection.SENT.value,
                subject=snapshot.subject,
                content=snapshot.body_text,
                content_html=snapshot.body_html,
                rfc_message_id=(
                    rfc_message_id
                    if outcome == EmailDeliveryOutcome.SMTP_ACCEPTED
                    else None
                ),
                provider_payload=provider_payload,
                failure_summary=error_summary,
            )
        )
        await record_operation_log(
            session,
            category="email",
            event_name=event_name,
            level=event_level,
            message=error_summary,
            entity_type="email_task",
            entity_id=str(snapshot.task_id),
            metadata={
                "task_id": snapshot.task_id,
                "source": snapshot.source,
                "batch_task_id": snapshot.batch_task_id,
                "parent_task_id": snapshot.parent_task_id,
                "identity_id": snapshot.identity_id,
                "llm_profile_id": snapshot.llm_profile_id,
                "professor_id": snapshot.professor_id,
                "attempt_id": snapshot.attempt_id,
                "delivery_outcome": outcome.value,
                "rfc_message_id": rfc_message_id,
                "attachment_count": snapshot.attachment_count,
            },
        )
        if inject_faults:
            await wait_at_fault_point("delivery.before_final_commit")
        await session.commit()

    return True


def _resolve_delivery_owner_identity() -> DeliveryOwnerIdentity:
    role = get_backend_role()
    runtime_id = get_runtime_id()
    pid = os.getpid()
    if role == "combined":
        return DeliveryOwnerIdentity(
            role=role,
            runtime_id=runtime_id,
            generation=_DELIVERY_PROCESS_GENERATION,
            pid=pid,
        )

    status = read_runtime_process_status(get_settings().data_dir, role)
    if (
        status is None
        or status.get("role") != role
        or status.get("runtime_id") != runtime_id
        or status.get("pid") != pid
        or not isinstance(status.get("generation"), str)
        or not status["generation"]
        or not process_is_running(pid)
    ):
        raise RuntimeError(
            f"Cannot claim email delivery without a current {role} runtime identity"
        )
    return DeliveryOwnerIdentity(
        role=role,
        runtime_id=runtime_id,
        generation=status["generation"],
        pid=pid,
    )


def _delivery_owner_is_active(attempt: EmailDeliveryAttempt) -> bool:
    if attempt.owner_role == "combined":
        return (
            attempt.owner_pid == os.getpid()
            and attempt.runtime_id == get_runtime_id()
            and attempt.owner_generation == _DELIVERY_PROCESS_GENERATION
            and process_is_running(attempt.owner_pid)
        )
    if attempt.owner_role not in {"api", "worker"}:
        return False
    status = read_runtime_process_status(
        get_settings().data_dir,
        attempt.owner_role,
    )
    return bool(
        status is not None
        and status.get("role") == attempt.owner_role
        and status.get("runtime_id") == attempt.runtime_id
        and status.get("generation") == attempt.owner_generation
        and status.get("pid") == attempt.owner_pid
        and process_is_running(attempt.owner_pid)
    )


async def _claim_legacy_sending_for_recovery(
    session_factory: async_sessionmaker[AsyncSession],
    task: EmailTask,
    recovered_at: datetime,
) -> EmailDeliveryAttempt | None:
    attempt_id = str(uuid.uuid4())
    claimed_at = task.last_send_attempt_at or task.updated_at
    attempt = EmailDeliveryAttempt(
        attempt_id=attempt_id,
        email_task_id=task.id,
        owner_role="legacy",
        runtime_id="legacy",
        owner_generation="missing-attempt-owner",
        owner_pid=0,
        outcome=EmailDeliveryOutcome.CLAIMED.value,
        claimed_at=claimed_at,
        prepared_rfc_message_id=task.last_rfc_message_id,
        subject=task.approved_subject or task.generated_subject or "",
        content=task.approved_body_text or task.generated_content_text or "",
        content_html=task.approved_body_html or task.generated_content_html,
        attachment_count=0,
        error_summary="Recovered a legacy sending row without delivery owner metadata",
    )
    async with session_factory() as session:
        result = await session.execute(
            update(EmailTask)
            .where(
                EmailTask.id == task.id,
                EmailTask.status == EmailTaskStatus.SENDING.value,
                EmailTask.delivery_attempt_id.is_(None),
            )
            .values(
                delivery_attempt_id=attempt_id,
                delivery_outcome=EmailDeliveryOutcome.CLAIMED.value,
                delivery_outcome_at=recovered_at,
                updated_at=recovered_at,
            )
            .execution_options(synchronize_session=False),
        )
        if result.rowcount != 1:
            await session.rollback()
            return None
        session.add(attempt)
        await session.commit()
    return attempt


def _build_recovery_finalization_snapshot(
    task: EmailTask,
    attempt: EmailDeliveryAttempt,
) -> DeliveryFinalizationSnapshot:
    return DeliveryFinalizationSnapshot(
        task_id=task.id,
        attempt_id=attempt.attempt_id,
        source=task.source,
        batch_task_id=task.batch_task_id,
        parent_task_id=task.parent_task_id,
        identity_id=task.identity_id,
        llm_profile_id=task.llm_profile_id,
        professor_id=task.professor_id,
        subject=attempt.subject,
        body_text=attempt.content,
        body_html=attempt.content_html,
        attachment_count=attempt.attachment_count,
    )


def _delivery_abandoned_marker_path(attempt_id: str) -> Path:
    marker_name = uuid.uuid5(uuid.NAMESPACE_URL, attempt_id).hex
    return (
        get_settings().data_dir
        / DELIVERY_ABANDONED_MARKER_DIRECTORY
        / f"{marker_name}.json"
    )


def _record_delivery_abandoned_marker(attempt_id: str, error_summary: str) -> None:
    _ABANDONED_ATTEMPTS_IN_PROCESS.add(attempt_id)
    marker_path = _delivery_abandoned_marker_path(attempt_id)
    temporary_path = marker_path.parent / f".{uuid.uuid4().hex}.tmp"
    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary_path.write_text(
            json.dumps(
                {
                    "attempt_id": attempt_id,
                    "recorded_at": utc_now().isoformat(),
                    "error_summary": error_summary,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            temporary_path.chmod(0o600)
        except OSError:
            pass
        temporary_path.replace(marker_path)
    except OSError:
        logger.warning(
            "Unable to persist abandoned email delivery marker for attempt_id=%s",
            attempt_id,
            exc_info=True,
        )
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def _delivery_attempt_is_explicitly_abandoned(attempt_id: str) -> bool:
    if attempt_id in _ABANDONED_ATTEMPTS_IN_PROCESS:
        return True
    marker_path = _delivery_abandoned_marker_path(attempt_id)
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("attempt_id") == attempt_id


def _clear_delivery_abandoned_marker(attempt_id: str) -> None:
    _ABANDONED_ATTEMPTS_IN_PROCESS.discard(attempt_id)
    try:
        _delivery_abandoned_marker_path(attempt_id).unlink(missing_ok=True)
    except OSError:
        pass


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


def _ensure_batch_task_has_future_window(task: EmailTask) -> None:
    batch_task = task.batch_task
    if batch_task is None or batch_task.schedule_type != "scheduled":
        return

    local_now = get_local_now()
    if (
        batch_task.status == BatchTaskStatus.EXPIRED.value
        or not has_future_batch_window(
            local_now,
            scheduled_dates=batch_task.scheduled_dates,
            window_end_time=batch_task.window_end_time,
        )
    ):
        raise ValueError(
            "当前批量任务的发送窗口已全部过期，请重新安排发送时间后再审核发送。"
        )


def _is_scheduled_batch_task(task: EmailTask) -> bool:
    return task.batch_task is not None and task.batch_task.schedule_type == "scheduled"


def _is_task_scheduled_for_future(task: EmailTask, now: datetime) -> bool:
    if task.scheduled_at is None:
        return False
    scheduled_at = as_utc_aware(task.scheduled_at)
    return scheduled_at.astimezone(UTC) > now.astimezone(UTC)
