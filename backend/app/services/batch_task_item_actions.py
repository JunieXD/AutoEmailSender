from __future__ import annotations

from sqlalchemy import or_

from app.models import EmailTask, EmailTaskStatus
from app.services.outreach_templates import (
    OUTREACH_GENERATION_MODE_LLM,
    OUTREACH_GENERATION_MODE_TEMPLATE,
)


def normalize_batch_item_generation_mode(email_task: EmailTask) -> str:
    mode = (email_task.outreach_generation_mode or "").strip().lower()
    if mode == OUTREACH_GENERATION_MODE_TEMPLATE:
        return OUTREACH_GENERATION_MODE_TEMPLATE
    return OUTREACH_GENERATION_MODE_LLM


def batch_item_uses_llm_generation_column(column):
    return or_(column.is_(None), column != OUTREACH_GENERATION_MODE_TEMPLATE)


def batch_item_uses_llm_generation(email_task: EmailTask) -> bool:
    return normalize_batch_item_generation_mode(email_task) == OUTREACH_GENERATION_MODE_LLM


def batch_item_is_ready_for_llm_generation(email_task: EmailTask) -> bool:
    return (
        batch_item_uses_llm_generation(email_task)
        and email_task.primary_material is not None
        and bool((email_task.professor.research_direction or "").strip())
    )


def resolve_batch_task_item_next_action(email_task: EmailTask) -> str | None:
    if email_task.status == EmailTaskStatus.REVIEW_REQUIRED.value:
        return "review_draft"
    if email_task.status == EmailTaskStatus.SCHEDULED.value:
        return "waiting_scheduled_send"
    if email_task.status == EmailTaskStatus.APPROVED.value:
        if (
            email_task.batch_task
            and email_task.batch_task.schedule_type == "scheduled"
            and email_task.scheduled_at is None
        ):
            return "missing_schedule"
        return "waiting_send"
    if email_task.status == EmailTaskStatus.SEND_FAILED.value:
        return "send_failed"
    if email_task.status == EmailTaskStatus.DRAFT_FAILED.value:
        if batch_item_uses_llm_generation(email_task):
            if email_task.primary_material is None:
                return "select_primary_material"
            if not (email_task.professor.research_direction or "").strip():
                return "complete_professor_profile"
            return "retry_draft_generation"
        return None
    if email_task.status in {
        EmailTaskStatus.DISCOVERED.value,
        EmailTaskStatus.MATCHED.value,
    }:
        if email_task.primary_material is None:
            return "select_primary_material"
        if batch_item_uses_llm_generation(email_task) and not (email_task.professor.research_direction or "").strip():
            return "complete_professor_profile"
        return "waiting_draft_generation"
    return None
