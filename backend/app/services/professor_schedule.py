from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BatchTask, BatchTaskStatus, EmailTask, EmailTaskStatus


ACTIVE_SCHEDULE_TASK_STATUSES = {
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
}


async def load_active_scheduled_professor_ids(
    session: AsyncSession,
    *,
    identity_id: int,
    professor_ids: list[int],
) -> set[int]:
    """Return professors whose tasks are still eligible for scheduled dispatch."""
    if not professor_ids:
        return set()

    rows = await session.scalars(
        select(EmailTask.professor_id)
        .outerjoin(BatchTask, EmailTask.batch_task_id == BatchTask.id)
        .where(
            EmailTask.identity_id == identity_id,
            EmailTask.professor_id.in_(list(dict.fromkeys(professor_ids))),
            EmailTask.status.in_(ACTIVE_SCHEDULE_TASK_STATUSES),
            EmailTask.scheduled_at.is_not(None),
            EmailTask.batch_send_canceled_at.is_(None),
            or_(
                EmailTask.batch_task_id.is_(None),
                and_(
                    BatchTask.status == BatchTaskStatus.RUNNING.value,
                    BatchTask.deleted_at.is_(None),
                ),
            ),
        )
        .distinct(),
    )
    return set(rows)
