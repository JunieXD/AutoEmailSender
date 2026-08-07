from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta, tzinfo

from app.core.time import as_utc_aware, local_now as get_local_now, utc_now
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.models import (
    BatchTask,
    BatchTaskStatus,
    EmailDirection,
    EmailLog,
    EmailTaskCancellationReason,
    EmailTask,
    EmailTaskStatus,
    EmailTaskSource,
    IdentityMaterial,
    IdentityProfile,
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
from app.services.operation_logs import record_operation_log

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

SCHEDULED_BATCH_SEND_GRACE_PERIOD = timedelta(minutes=2)

STARTUP_MANUAL_SCHEDULE_GRACE_PERIOD = timedelta(minutes=2)

DEFAULT_SEND_INTERVAL_MIN_SECONDS = 1

DEFAULT_SEND_INTERVAL_MAX_SECONDS = 5


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
                task.cancellation_reason = (
                    EmailTaskCancellationReason.SCHEDULE_EXPIRED.value
                )
            else:
                task.status = EmailTaskStatus.CANCELED.value
                task.cancellation_reason = (
                    EmailTaskCancellationReason.BATCH_STOPPED.value
                )
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
