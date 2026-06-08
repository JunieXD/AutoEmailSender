from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmailDirection, EmailLog, EmailTask, EmailTaskCancellationReason, EmailTaskStatus


@dataclass(frozen=True)
class ProfessorContactStatus:
    professor_id: int
    status: str
    sent_count: int = 0
    last_sent_at: datetime | None = None
    last_replied_at: datetime | None = None


async def build_contact_status_by_professor(
    session: AsyncSession,
    *,
    identity_id: int,
    professor_ids: list[int],
    tasks_by_professor: dict[int, list[EmailTask]] | None = None,
) -> dict[int, ProfessorContactStatus]:
    if not professor_ids:
        return {}

    unique_professor_ids = list(dict.fromkeys(professor_ids))
    resolved_tasks_by_professor = (
        tasks_by_professor
        if tasks_by_professor is not None
        else await _load_tasks_by_professor(
            session,
            identity_id=identity_id,
            professor_ids=unique_professor_ids,
        )
    )
    sent_count_by_professor: dict[int, int] = defaultdict(int)
    last_sent_at_by_professor: dict[int, datetime] = {}
    last_replied_at_by_professor: dict[int, datetime] = {}

    logs = await session.scalars(
        select(EmailLog)
        .where(
            EmailLog.identity_id == identity_id,
            EmailLog.professor_id.in_(unique_professor_ids),
            EmailLog.direction.in_([EmailDirection.SENT.value, EmailDirection.RECEIVED.value]),
        )
        .order_by(EmailLog.created_at.asc(), EmailLog.id.asc()),
    )
    for log in logs:
        if log.direction == EmailDirection.SENT.value and not log.failure_summary:
            sent_count_by_professor[log.professor_id] += 1
            _keep_latest_timestamp(last_sent_at_by_professor, log.professor_id, log.created_at)
        elif log.direction == EmailDirection.RECEIVED.value:
            _keep_latest_timestamp(last_replied_at_by_professor, log.professor_id, log.created_at)

    statuses: dict[int, ProfessorContactStatus] = {}
    for professor_id in unique_professor_ids:
        tasks = resolved_tasks_by_professor.get(professor_id, [])
        for task in tasks:
            if professor_id not in last_sent_at_by_professor:
                _keep_latest_timestamp(last_sent_at_by_professor, professor_id, task.sent_at)
            if (
                professor_id not in last_replied_at_by_professor
                and (task.is_replied or task.status == EmailTaskStatus.REPLY_DETECTED.value)
            ):
                _keep_latest_timestamp(last_replied_at_by_professor, professor_id, task.updated_at)

        statuses[professor_id] = ProfessorContactStatus(
            professor_id=professor_id,
            status=resolve_professor_contact_status(
                tasks,
                sent_count=sent_count_by_professor.get(professor_id, 0),
                has_reply=professor_id in last_replied_at_by_professor,
            ),
            sent_count=sent_count_by_professor.get(professor_id, 0),
            last_sent_at=last_sent_at_by_professor.get(professor_id),
            last_replied_at=last_replied_at_by_professor.get(professor_id),
        )
    return statuses


def resolve_professor_contact_status(
    tasks: list[EmailTask],
    *,
    sent_count: int = 0,
    has_reply: bool = False,
) -> str:
    if has_reply or any(task.is_replied or task.status == EmailTaskStatus.REPLY_DETECTED.value for task in tasks):
        return "replied"
    if sent_count > 0 or any(task.status == EmailTaskStatus.SENT.value or task.sent_at for task in tasks):
        return "contacted"
    if not tasks:
        return "not_contacted"

    latest_task = tasks[0]
    if latest_task.status in {
        EmailTaskStatus.DRAFT_FAILED.value,
        EmailTaskStatus.SEND_FAILED.value,
    }:
        return "failed"
    if latest_task.status in {
        EmailTaskStatus.APPROVED.value,
        EmailTaskStatus.SCHEDULED.value,
        EmailTaskStatus.SENDING.value,
    }:
        return "ready_to_send"
    if latest_task.status in {
        EmailTaskStatus.DISCOVERED.value,
        EmailTaskStatus.MATCHED.value,
        EmailTaskStatus.GENERATING_DRAFT.value,
        EmailTaskStatus.REVIEW_REQUIRED.value,
    }:
        return "preparing"
    return "not_contacted"


async def _load_tasks_by_professor(
    session: AsyncSession,
    *,
    identity_id: int,
    professor_ids: list[int],
) -> dict[int, list[EmailTask]]:
    rows = await session.scalars(
        select(EmailTask)
        .where(
            EmailTask.identity_id == identity_id,
            EmailTask.professor_id.in_(professor_ids),
            ~(
                (EmailTask.status == EmailTaskStatus.CANCELED.value)
                & (EmailTask.cancellation_reason == EmailTaskCancellationReason.USER_REMOVED.value)
            ),
        )
        .order_by(EmailTask.created_at.desc(), EmailTask.id.desc()),
    )
    tasks_by_professor: dict[int, list[EmailTask]] = defaultdict(list)
    for task in rows:
        tasks_by_professor[task.professor_id].append(task)
    return tasks_by_professor


def _keep_latest_timestamp(
    values: dict[int, datetime],
    professor_id: int,
    timestamp: datetime | None,
) -> None:
    if timestamp is None:
        return
    current = values.get(professor_id)
    if current is None or timestamp > current:
        values[professor_id] = timestamp
