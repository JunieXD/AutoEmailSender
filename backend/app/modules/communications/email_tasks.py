"""Shared email-task persistence helpers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import EmailTask, IdentityProfile
from app.services.operation_logs import record_operation_log


EMAIL_TASK_RELATION_OPTIONS = (
    selectinload(EmailTask.batch_task),
    selectinload(EmailTask.identity),
    selectinload(EmailTask.identity).selectinload(
        IdentityProfile.current_primary_material
    ),
    selectinload(EmailTask.llm_profile),
    selectinload(EmailTask.professor),
    selectinload(EmailTask.primary_material),
)


async def load_email_task(
    session: AsyncSession,
    task_id: int,
) -> EmailTask | None:
    return await session.scalar(
        select(EmailTask)
        .options(*EMAIL_TASK_RELATION_OPTIONS)
        .where(EmailTask.id == task_id),
    )


async def record_email_task_log(
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
