from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, load_only, selectinload

from app.core.query_chunks import chunked_values, unique_positive_ids
from app.core.time import as_utc_aware, utc_now
from app.models import (
    BatchTask,
    BatchTaskStatus,
    EmailTask,
    EmailTaskStatus,
    IdentityMaterial,
    Professor,
)
from app.modules.campaigns.public import (
    BatchTaskCardRead,
    BatchTaskItemRead,
    batch_item_is_ready_for_llm_generation,
    batch_item_uses_llm_generation_column,
    count_completed_batch_task_items,
    resolve_batch_task_item_next_action,
)
from app.modules.campaigns.status import email_task_is_not_user_removed_expression
from app.modules.communications.public import explain_smtp_error

from .item_policy import (
    BATCH_TASK_ITEM_DEFERRED_COLUMNS,
    _can_cancel_batch_task_item_send,
    _can_restore_batch_task_item_send,
    _visible_batch_email_tasks,
)


@dataclass(frozen=True)
class BatchTaskCardMetrics:
    completed_count: int = 0
    pending_generation_count: int = 0
    queued_generation_count: int = 0
    blocked_generation_count: int = 0
    generating_draft_count: int = 0
    draft_failed_count: int = 0
    review_required_count: int = 0
    approved_count: int = 0
    scheduled_count: int = 0
    sent_count: int = 0
    failed_count: int = 0
    replied_count: int = 0
    canceled_send_count: int = 0


async def _load_batch_task_for_serialization(
    session: AsyncSession, task_id: int
) -> BatchTask | None:
    return await session.scalar(
        select(BatchTask)
        .options(
            selectinload(BatchTask.email_tasks).options(
                *[
                    defer(deferred_column)
                    for deferred_column in BATCH_TASK_ITEM_DEFERRED_COLUMNS
                ],
                selectinload(EmailTask.professor).options(
                    defer(Professor.recent_papers),
                ),
                selectinload(EmailTask.primary_material).options(
                    load_only(IdentityMaterial.id),
                ),
            ),
        )
        .where(BatchTask.id == task_id)
        .execution_options(populate_existing=True),
    )


def _serialize_batch_task_item(
    email_task: EmailTask,
    *,
    material_sizes: dict[int, int] | None = None,
    match_score: int | None = None,
) -> BatchTaskItemRead:
    professor = email_task.professor
    now = utc_now()
    selected_material_ids = dict.fromkeys(email_task.selected_material_ids or [])
    selected_attachment_size_bytes = sum(
        (material_sizes or {}).get(material_id, 0)
        for material_id in selected_material_ids
    )
    return BatchTaskItemRead(
        id=email_task.id,
        professor_id=professor.id,
        professor_name=professor.name,
        professor_email=professor.email,
        professor_title=professor.title,
        professor_school=professor.school,
        professor_research_direction=professor.research_direction,
        status=email_task.status,
        cancellation_reason=email_task.cancellation_reason,
        batch_send_canceled_at=email_task.batch_send_canceled_at,
        can_cancel_send=_can_cancel_batch_task_item_send(email_task),
        can_restore_send=_can_restore_batch_task_item_send(email_task, now=now),
        match_score=match_score,
        scheduled_at=email_task.scheduled_at,
        sent_at=email_task.sent_at,
        last_send_attempt_at=email_task.last_send_attempt_at,
        last_error=email_task.last_error,
        possible_cause=(
            explain_smtp_error(email_task.last_error)
            if email_task.status == EmailTaskStatus.SEND_FAILED.value
            else None
        ),
        draft_generation_source=email_task.draft_generation_source,
        draft_fallback_reason=email_task.draft_fallback_reason,
        is_replied=email_task.is_replied,
        updated_at=email_task.updated_at,
        next_action=(
            None
            if email_task.batch_send_canceled_at is not None
            else resolve_batch_task_item_next_action(email_task)
        ),
        selected_attachment_size_bytes=selected_attachment_size_bytes,
    )


async def _load_batch_task_card_metrics(
    session: AsyncSession,
    task_ids: list[int],
    *,
    now: datetime,
) -> dict[int, BatchTaskCardMetrics]:
    if not task_ids:
        return {}

    is_visible = email_task_is_not_user_removed_expression()
    is_active = is_visible & EmailTask.batch_send_canceled_at.is_(None)
    is_completed = EmailTask.status.in_(
        {
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }
    )
    is_pending_generation = is_active & EmailTask.status.in_(
        {
            EmailTaskStatus.DISCOVERED.value,
            EmailTaskStatus.MATCHED.value,
        }
    )
    is_queued_generation = (
        is_pending_generation
        & batch_item_uses_llm_generation_column(EmailTask.outreach_generation_mode)
        & EmailTask.primary_material_id.is_not(None)
        & Professor.research_direction.is_not(None)
        & (func.trim(Professor.research_direction) != "")
    )

    def count_when(condition: object, name: str):
        return func.coalesce(func.sum(case((condition, 1), else_=0)), 0).label(name)

    metric_rows = []
    canceled_scheduled_rows = []
    for task_id_chunk in chunked_values(unique_positive_ids(task_ids)):
        metric_rows.extend(
            (
                await session.execute(
                    select(
                        EmailTask.batch_task_id,
                        count_when(is_completed, "completed_count"),
                        count_when(
                            is_pending_generation,
                            "pending_generation_count",
                        ),
                        count_when(is_queued_generation, "queued_generation_count"),
                        count_when(
                            is_pending_generation & ~is_queued_generation,
                            "blocked_generation_count",
                        ),
                        count_when(
                            is_active
                            & (
                                EmailTask.status
                                == EmailTaskStatus.GENERATING_DRAFT.value
                            ),
                            "generating_draft_count",
                        ),
                        count_when(
                            is_active
                            & (EmailTask.status == EmailTaskStatus.DRAFT_FAILED.value),
                            "draft_failed_count",
                        ),
                        count_when(
                            is_active
                            & (
                                EmailTask.status
                                == EmailTaskStatus.REVIEW_REQUIRED.value
                            ),
                            "review_required_count",
                        ),
                        count_when(
                            is_active
                            & (EmailTask.status == EmailTaskStatus.APPROVED.value),
                            "approved_count",
                        ),
                        count_when(
                            is_active
                            & (EmailTask.status == EmailTaskStatus.SCHEDULED.value),
                            "scheduled_count",
                        ),
                        count_when(
                            is_active
                            & (EmailTask.status == EmailTaskStatus.SENT.value),
                            "sent_count",
                        ),
                        count_when(
                            is_active
                            & (EmailTask.status == EmailTaskStatus.SEND_FAILED.value),
                            "failed_count",
                        ),
                        count_when(
                            is_active
                            & (
                                EmailTask.status == EmailTaskStatus.REPLY_DETECTED.value
                            ),
                            "replied_count",
                        ),
                        count_when(
                            is_visible & EmailTask.batch_send_canceled_at.is_not(None),
                            "canceled_send_count",
                        ),
                    )
                    .join(Professor, EmailTask.professor_id == Professor.id)
                    .where(EmailTask.batch_task_id.in_(task_id_chunk))
                    .group_by(EmailTask.batch_task_id)
                )
            ).all(),
        )
        canceled_scheduled_rows.extend(
            (
                await session.execute(
                    select(EmailTask.batch_task_id, EmailTask.scheduled_at).where(
                        EmailTask.batch_task_id.in_(task_id_chunk),
                        EmailTask.batch_send_canceled_at.is_not(None),
                        EmailTask.scheduled_at.is_not(None),
                    )
                )
            ).all(),
        )
    completed_canceled_counts: Counter[int] = Counter()
    for task_id, scheduled_at in canceled_scheduled_rows:
        if scheduled_at is not None and as_utc_aware(scheduled_at) <= as_utc_aware(now):
            completed_canceled_counts[int(task_id)] += 1
    return {
        int(row.batch_task_id): BatchTaskCardMetrics(
            completed_count=(
                int(row.completed_count)
                + completed_canceled_counts[int(row.batch_task_id)]
            ),
            pending_generation_count=int(row.pending_generation_count),
            queued_generation_count=int(row.queued_generation_count),
            blocked_generation_count=int(row.blocked_generation_count),
            generating_draft_count=int(row.generating_draft_count),
            draft_failed_count=int(row.draft_failed_count),
            review_required_count=int(row.review_required_count),
            approved_count=int(row.approved_count),
            scheduled_count=int(row.scheduled_count),
            sent_count=int(row.sent_count),
            failed_count=int(row.failed_count),
            replied_count=int(row.replied_count),
            canceled_send_count=int(row.canceled_send_count),
        )
        for row in metric_rows
    }


def _should_sync_batch_task_completion(
    task: BatchTask,
    metrics: BatchTaskCardMetrics,
) -> bool:
    return (
        task.target_count > 0
        and metrics.completed_count >= task.target_count
        and task.status
        not in {
            BatchTaskStatus.STOPPED.value,
            BatchTaskStatus.EXPIRED.value,
            BatchTaskStatus.COMPLETED.value,
        }
    )


def _batch_task_card_metrics_from_email_tasks(task: BatchTask) -> BatchTaskCardMetrics:
    visible_email_tasks = _visible_batch_email_tasks(task)
    active_email_tasks = [
        email_task
        for email_task in visible_email_tasks
        if email_task.batch_send_canceled_at is None
    ]
    status_counter = Counter(email_task.status for email_task in active_email_tasks)
    pending_generation_tasks = [
        email_task
        for email_task in active_email_tasks
        if email_task.status
        in {
            EmailTaskStatus.DISCOVERED.value,
            EmailTaskStatus.MATCHED.value,
        }
    ]
    queued_generation_count = sum(
        1
        for email_task in pending_generation_tasks
        if batch_item_is_ready_for_llm_generation(email_task)
    )
    canceled_send_count = sum(
        1
        for email_task in visible_email_tasks
        if email_task.batch_send_canceled_at is not None
    )
    completed_count = count_completed_batch_task_items(task)
    return BatchTaskCardMetrics(
        completed_count=completed_count,
        pending_generation_count=(
            status_counter.get(EmailTaskStatus.DISCOVERED.value, 0)
            + status_counter.get(EmailTaskStatus.MATCHED.value, 0)
        ),
        queued_generation_count=queued_generation_count,
        blocked_generation_count=(
            len(pending_generation_tasks) - queued_generation_count
        ),
        generating_draft_count=status_counter.get(
            EmailTaskStatus.GENERATING_DRAFT.value, 0
        ),
        draft_failed_count=status_counter.get(EmailTaskStatus.DRAFT_FAILED.value, 0),
        review_required_count=status_counter.get(
            EmailTaskStatus.REVIEW_REQUIRED.value, 0
        ),
        approved_count=status_counter.get(EmailTaskStatus.APPROVED.value, 0),
        scheduled_count=status_counter.get(EmailTaskStatus.SCHEDULED.value, 0),
        sent_count=status_counter.get(EmailTaskStatus.SENT.value, 0),
        failed_count=status_counter.get(EmailTaskStatus.SEND_FAILED.value, 0),
        replied_count=status_counter.get(EmailTaskStatus.REPLY_DETECTED.value, 0),
        canceled_send_count=canceled_send_count,
    )


def _serialize_batch_task(
    task: BatchTask,
    *,
    metrics: BatchTaskCardMetrics | None = None,
) -> BatchTaskCardRead:
    resolved_metrics = metrics or _batch_task_card_metrics_from_email_tasks(task)
    return BatchTaskCardRead(
        id=task.id,
        name=task.name,
        status=task.status,
        schedule_type=task.schedule_type,
        window_start_time=task.window_start_time,
        window_end_time=task.window_end_time,
        emails_per_window=task.emails_per_window,
        scheduled_dates=task.scheduled_dates,
        email_subject=task.email_subject,
        outreach_template_id=task.outreach_template_id,
        outreach_template_name_snapshot=task.outreach_template_name_snapshot,
        outreach_template_snapshot_version=task.outreach_template_snapshot_version,
        outreach_generation_mode=task.outreach_generation_mode,
        target_count=task.target_count,
        completed_count=resolved_metrics.completed_count,
        identity_id=task.identity_id,
        llm_profile_id=task.llm_profile_id,
        pending_generation_count=resolved_metrics.pending_generation_count,
        queued_generation_count=resolved_metrics.queued_generation_count,
        blocked_generation_count=resolved_metrics.blocked_generation_count,
        generating_draft_count=resolved_metrics.generating_draft_count,
        draft_failed_count=resolved_metrics.draft_failed_count,
        review_required_count=resolved_metrics.review_required_count,
        approved_count=resolved_metrics.approved_count,
        scheduled_count=resolved_metrics.scheduled_count,
        sent_count=resolved_metrics.sent_count,
        failed_count=resolved_metrics.failed_count,
        replied_count=resolved_metrics.replied_count,
        canceled_send_count=resolved_metrics.canceled_send_count,
        created_at=task.created_at,
        updated_at=task.updated_at,
        deleted_at=task.deleted_at,
    )
