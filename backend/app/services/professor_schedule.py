from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.query_chunks import chunked_values, unique_positive_ids
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

    scheduled_professor_ids: set[int] = set()
    for professor_id_chunk in chunked_values(unique_positive_ids(professor_ids)):
        rows = await session.scalars(
            select(EmailTask.professor_id)
            .outerjoin(BatchTask, EmailTask.batch_task_id == BatchTask.id)
            .where(
                EmailTask.identity_id == identity_id,
                EmailTask.professor_id.in_(professor_id_chunk),
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
        scheduled_professor_ids.update(rows)
    return scheduled_professor_ids
