from __future__ import annotations

from datetime import datetime

from app.core.time import as_utc_aware
from app.models import (
    BatchTask,
    BatchTaskStatus,
    EmailTask,
    EmailTaskCancellationReason,
    EmailTaskStatus,
)


def _is_user_removed_batch_item(email_task: EmailTask) -> bool:
    return (
        email_task.status == EmailTaskStatus.CANCELED.value
        and email_task.cancellation_reason
        == EmailTaskCancellationReason.USER_REMOVED.value
    )


def _visible_batch_email_tasks(task: BatchTask) -> list[EmailTask]:
    return [
        email_task
        for email_task in task.email_tasks
        if not _is_user_removed_batch_item(email_task)
    ]


def _can_cancel_batch_task_item_send(email_task: EmailTask) -> bool:
    batch_task = email_task.batch_task
    return bool(
        batch_task is not None
        and _batch_task_allows_item_send_actions(batch_task)
        and email_task.scheduled_at is not None
        and email_task.batch_send_canceled_at is None
        and email_task.status in BATCH_TASK_ITEM_SEND_CANCELLABLE_STATUSES
    )


def _can_restore_batch_task_item_send(
    email_task: EmailTask,
    *,
    now: datetime,
) -> bool:
    batch_task = email_task.batch_task
    return bool(
        batch_task is not None
        and _batch_task_allows_item_send_actions(batch_task)
        and email_task.scheduled_at is not None
        and as_utc_aware(email_task.scheduled_at) > as_utc_aware(now)
        and email_task.batch_send_canceled_at is not None
        and email_task.status in BATCH_TASK_ITEM_SEND_CANCELLABLE_STATUSES
    )


BATCH_TASK_ITEM_DEFERRED_COLUMNS = (
    EmailTask.generated_content_text,
    EmailTask.generated_content_html,
    EmailTask.outreach_template_body_text,
    EmailTask.outreach_template_body_html,
    EmailTask.approved_body_text,
    EmailTask.approved_body_html,
    EmailTask.draft_rewrite_source_subject,
    EmailTask.draft_rewrite_source_body_text,
    EmailTask.draft_rewrite_source_body_html,
)


BATCH_TASK_ITEM_SEND_CANCELLABLE_STATUSES = {
    EmailTaskStatus.DISCOVERED.value,
    EmailTaskStatus.MATCHED.value,
    EmailTaskStatus.GENERATING_DRAFT.value,
    EmailTaskStatus.DRAFT_FAILED.value,
    EmailTaskStatus.REVIEW_REQUIRED.value,
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
}


def _batch_task_allows_item_send_actions(task: BatchTask) -> bool:
    return bool(
        task.deleted_at is None
        and task.schedule_type == "scheduled"
        and task.status in BATCH_TASK_ITEM_SEND_ACTION_BATCH_STATUSES
    )


BATCH_TASK_ITEM_SEND_ACTION_BATCH_STATUSES = {
    BatchTaskStatus.RUNNING.value,
    BatchTaskStatus.PAUSED.value,
}
