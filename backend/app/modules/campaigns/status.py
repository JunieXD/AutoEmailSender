from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

from app.core.time import as_utc_aware, utc_now

from app.models import (
    BatchTask,
    BatchTaskStatus,
    EmailTask,
    EmailTaskCancellationReason,
    EmailTaskStatus,
)


BATCH_TASK_COMPLETED_ITEM_STATUSES = {
    EmailTaskStatus.SENT.value,
    EmailTaskStatus.REPLY_DETECTED.value,
}

BATCH_TASK_COMPLETION_EXCLUDED_STATUSES = {
    BatchTaskStatus.STOPPED.value,
    BatchTaskStatus.EXPIRED.value,
}


def email_task_is_not_user_removed_expression() -> ColumnElement[bool]:
    """Keep legacy canceled rows with no recorded cancellation reason visible."""

    return or_(
        EmailTask.status != EmailTaskStatus.CANCELED.value,
        EmailTask.cancellation_reason.is_(None),
        EmailTask.cancellation_reason
        != EmailTaskCancellationReason.USER_REMOVED.value,
    )


def batch_item_counts_as_completed(
    email_task: EmailTask,
    *,
    now: datetime,
) -> bool:
    if email_task.status in BATCH_TASK_COMPLETED_ITEM_STATUSES:
        return True
    return bool(
        email_task.batch_send_canceled_at is not None
        and email_task.scheduled_at is not None
        and as_utc_aware(email_task.scheduled_at) <= as_utc_aware(now)
    )


def count_completed_batch_task_items(
    task: BatchTask,
    *,
    now: datetime | None = None,
) -> int:
    resolved_now = now or utc_now()
    return sum(
        1
        for email_task in task.email_tasks
        if batch_item_counts_as_completed(email_task, now=resolved_now)
    )


def should_mark_batch_task_completed(
    task: BatchTask,
    *,
    now: datetime | None = None,
) -> bool:
    resolved_now = now or utc_now()
    return (
        task.target_count > 0
        and count_completed_batch_task_items(task, now=resolved_now) >= task.target_count
        and task.status not in BATCH_TASK_COMPLETION_EXCLUDED_STATUSES
    )


def sync_batch_task_completion(task: BatchTask, *, now: datetime | None = None) -> bool:
    resolved_now = now or utc_now()
    if not should_mark_batch_task_completed(task, now=resolved_now):
        return False
    if task.status == BatchTaskStatus.COMPLETED.value:
        return False
    task.status = BatchTaskStatus.COMPLETED.value
    task.updated_at = resolved_now
    return True
