from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.core.query_chunks import chunked_values, unique_positive_ids
from app.models import EmailDirection, EmailLog, EmailTask, EmailTaskCancellationReason, EmailTaskStatus
from app.modules.campaigns.public import email_task_is_not_user_removed_expression
from app.modules.communications.public import load_communication_events


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
    communication_identity_ids: tuple[int, ...] | list[int] | None = None,
) -> dict[int, ProfessorContactStatus]:
    if not professor_ids:
        return {}

    unique_professor_ids = unique_positive_ids(professor_ids)
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

    resolved_identity_ids = tuple(
        dict.fromkeys(communication_identity_ids or (identity_id,)),
    )
    if len(resolved_identity_ids) == 1:
        successful_sent_log = (
            (EmailLog.direction == EmailDirection.SENT.value)
            & (func.trim(func.coalesce(EmailLog.failure_summary, "")) == "")
        )
        for professor_id_chunk in chunked_values(unique_professor_ids):
            log_rows = await session.execute(
                select(
                    EmailLog.professor_id,
                    func.sum(case((successful_sent_log, 1), else_=0)).label("sent_count"),
                    func.max(
                        case((successful_sent_log, EmailLog.created_at), else_=None),
                    ).label("last_sent_at"),
                    func.max(
                        case(
                            (EmailLog.direction == EmailDirection.RECEIVED.value, EmailLog.created_at),
                            else_=None,
                        ),
                    ).label("last_replied_at"),
                )
                .where(
                    EmailLog.identity_id == resolved_identity_ids[0],
                    EmailLog.professor_id.in_(professor_id_chunk),
                    EmailLog.direction.in_([EmailDirection.SENT.value, EmailDirection.RECEIVED.value]),
                )
                .group_by(EmailLog.professor_id),
            )
            for professor_id, sent_count, last_sent_at, last_replied_at in log_rows:
                sent_count_by_professor[professor_id] = int(sent_count or 0)
                _keep_latest_timestamp(last_sent_at_by_professor, professor_id, last_sent_at)
                _keep_latest_timestamp(last_replied_at_by_professor, professor_id, last_replied_at)
    else:
        communication_events = await load_communication_events(
            session,
            identity_ids=resolved_identity_ids,
            professor_ids=unique_professor_ids,
            include_message_content=False,
            include_source_identities=False,
            include_professors=False,
        )
        for event in communication_events:
            professor_id = event.log.professor_id
            if event.log.direction == EmailDirection.SENT.value and event.successful:
                sent_count_by_professor[professor_id] += 1
                _keep_latest_timestamp(
                    last_sent_at_by_professor,
                    professor_id,
                    event.created_at,
                )
            elif event.log.direction == EmailDirection.RECEIVED.value:
                _keep_latest_timestamp(
                    last_replied_at_by_professor,
                    professor_id,
                    event.created_at,
                )
    professors_with_sent_logs = set(last_sent_at_by_professor)
    professors_with_reply_logs = set(last_replied_at_by_professor)

    statuses: dict[int, ProfessorContactStatus] = {}
    for professor_id in unique_professor_ids:
        tasks = resolved_tasks_by_professor.get(professor_id, [])
        for task in tasks:
            if professor_id not in professors_with_sent_logs:
                _keep_latest_timestamp(last_sent_at_by_professor, professor_id, task.sent_at)
            if (
                professor_id not in professors_with_reply_logs
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
    tasks_by_professor: dict[int, list[EmailTask]] = defaultdict(list)
    for professor_id_chunk in chunked_values(unique_positive_ids(professor_ids)):
        rows = await session.scalars(
            select(EmailTask)
            .options(
                load_only(
                    EmailTask.professor_id,
                    EmailTask.status,
                    EmailTask.created_at,
                    EmailTask.sent_at,
                    EmailTask.is_replied,
                    EmailTask.updated_at,
                ),
            )
            .where(
                EmailTask.identity_id == identity_id,
                EmailTask.professor_id.in_(professor_id_chunk),
                EmailTask.batch_send_canceled_at.is_(None),
                email_task_is_not_user_removed_expression(),
            )
            .order_by(EmailTask.professor_id.asc(), EmailTask.created_at.desc(), EmailTask.id.desc()),
        )
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
