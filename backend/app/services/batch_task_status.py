from __future__ import annotations

from datetime import UTC, datetime

from app.models import BatchTask, BatchTaskStatus, EmailTaskStatus


BATCH_TASK_COMPLETED_ITEM_STATUSES = {
    EmailTaskStatus.SENT.value,
    EmailTaskStatus.REPLY_DETECTED.value,
}

BATCH_TASK_COMPLETION_EXCLUDED_STATUSES = {
    BatchTaskStatus.STOPPED.value,
    BatchTaskStatus.EXPIRED.value,
}


def count_completed_batch_task_items(task: BatchTask) -> int:
    return sum(
        1
        for email_task in task.email_tasks
        if email_task.status in BATCH_TASK_COMPLETED_ITEM_STATUSES
    )


def should_mark_batch_task_completed(task: BatchTask) -> bool:
    return (
        task.target_count > 0
        and count_completed_batch_task_items(task) >= task.target_count
        and task.status not in BATCH_TASK_COMPLETION_EXCLUDED_STATUSES
    )


def sync_batch_task_completion(task: BatchTask, *, now: datetime | None = None) -> bool:
    if not should_mark_batch_task_completed(task):
        return False
    if task.status == BatchTaskStatus.COMPLETED.value:
        return False
    task.status = BatchTaskStatus.COMPLETED.value
    task.updated_at = now or datetime.now(UTC)
    return True
