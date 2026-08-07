from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from app.core.time import as_utc_aware, utc_now
from app.models import (
    BatchTask,
    BatchTaskStatus,
    EmailTask,
    EmailTaskCancellationReason,
    EmailTaskSource,
    EmailTaskStatus,
    IdentityMaterial,
    Professor,
)
from app.modules.communications.public import record_email_task_log
from app.modules.workspace.tasks.delivery import dispatch_email_task

from .schemas import (
    EmailDeliveryActionRead,
    EmailDeliveryItemRead,
    EmailDeliveryListRead,
    EmailDeliveryViewCountsRead,
)


DELIVERY_VIEWS = {"upcoming", "attention", "history"}
DELIVERY_SOURCES = {"all", "manual", "batch"}
DELIVERY_SEARCH_FIELDS = {
    "recipient_name",
    "recipient_email",
    "subject",
    "batch_name",
}
DELIVERY_STATUS_FILTERS = {
    "waiting_scheduled",
    "send_asap",
    "batch_paused",
    "sending",
    "send_failed",
    "schedule_missed",
    "batch_stopped",
    "sent",
    "replied",
    "canceled_schedule",
    "canceled_send",
}
DEFAULT_DELIVERY_SORTS = {
    "upcoming": "scheduled_asc",
    "attention": "updated_desc",
    "history": "event_desc",
}
DELIVERY_SORTS_BY_VIEW = {
    "upcoming": {"scheduled_asc", "scheduled_desc", "updated_desc"},
    "attention": {"updated_desc", "updated_asc", "scheduled_asc"},
    "history": {"event_desc", "event_asc"},
}
MINIMUM_RESCHEDULE_DELAY = timedelta(minutes=1)


def _upcoming_condition():
    return and_(
        EmailTask.status.in_(
            {
                EmailTaskStatus.APPROVED.value,
                EmailTaskStatus.SCHEDULED.value,
                EmailTaskStatus.SENDING.value,
            },
        ),
        EmailTask.schedule_canceled_at.is_(None),
        EmailTask.batch_send_canceled_at.is_(None),
    )


def _attention_condition():
    return and_(
        EmailTask.schedule_canceled_at.is_(None),
        EmailTask.batch_send_canceled_at.is_(None),
        or_(
            EmailTask.status.in_(
                {
                    EmailTaskStatus.SEND_FAILED.value,
                    EmailTaskStatus.SCHEDULE_MISSED.value,
                },
            ),
            and_(
                EmailTask.status == EmailTaskStatus.CANCELED.value,
                EmailTask.cancellation_reason.in_(
                    {
                        EmailTaskCancellationReason.BATCH_STOPPED.value,
                        EmailTaskCancellationReason.SCHEDULE_EXPIRED.value,
                    },
                ),
            ),
        ),
    )


def _history_condition():
    return or_(
        EmailTask.status.in_(
            {
                EmailTaskStatus.SENT.value,
                EmailTaskStatus.REPLY_DETECTED.value,
            },
        ),
        EmailTask.schedule_canceled_at.is_not(None),
        EmailTask.batch_send_canceled_at.is_not(None),
        and_(
            EmailTask.status == EmailTaskStatus.CANCELED.value,
            EmailTask.cancellation_reason == EmailTaskCancellationReason.USER_REMOVED.value,
        ),
    )


def _view_condition(view: str):
    if view == "upcoming":
        return _upcoming_condition()
    if view == "attention":
        return _attention_condition()
    if view == "history":
        return _history_condition()
    raise ValueError("未知发送计划视图")


def _status_condition(status: str):
    if status == "waiting_scheduled":
        return and_(
            EmailTask.status == EmailTaskStatus.SCHEDULED.value,
            or_(BatchTask.id.is_(None), BatchTask.status != BatchTaskStatus.PAUSED.value),
        )
    if status == "send_asap":
        return and_(
            EmailTask.status == EmailTaskStatus.APPROVED.value,
            or_(BatchTask.id.is_(None), BatchTask.status != BatchTaskStatus.PAUSED.value),
        )
    if status == "batch_paused":
        return and_(
            BatchTask.status == BatchTaskStatus.PAUSED.value,
            EmailTask.status.in_(
                {EmailTaskStatus.APPROVED.value, EmailTaskStatus.SCHEDULED.value},
            ),
        )
    if status == "sending":
        return EmailTask.status == EmailTaskStatus.SENDING.value
    if status == "send_failed":
        return EmailTask.status == EmailTaskStatus.SEND_FAILED.value
    if status == "schedule_missed":
        return EmailTask.status == EmailTaskStatus.SCHEDULE_MISSED.value
    if status == "batch_stopped":
        return and_(
            EmailTask.status == EmailTaskStatus.CANCELED.value,
            EmailTask.cancellation_reason.in_(
                {
                    EmailTaskCancellationReason.BATCH_STOPPED.value,
                    EmailTaskCancellationReason.SCHEDULE_EXPIRED.value,
                },
            ),
        )
    if status == "sent":
        return EmailTask.status == EmailTaskStatus.SENT.value
    if status == "replied":
        return EmailTask.status == EmailTaskStatus.REPLY_DETECTED.value
    if status == "canceled_schedule":
        return EmailTask.schedule_canceled_at.is_not(None)
    if status == "canceled_send":
        return EmailTask.batch_send_canceled_at.is_not(None)
    raise ValueError("未知发送状态筛选")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _base_filter_conditions(
    *,
    identity_id: int | None,
    source: str,
    query: str | None,
    search_fields: set[str],
) -> list[object]:
    conditions: list[object] = []
    if identity_id is not None:
        conditions.append(EmailTask.identity_id == identity_id)
    if source == "manual":
        conditions.append(EmailTask.source == EmailTaskSource.MANUAL.value)
    elif source == "batch":
        conditions.append(EmailTask.source == EmailTaskSource.BATCH.value)
    elif source != "all":
        raise ValueError("未知发送来源筛选")

    normalized_query = (query or "").strip().lower()
    if normalized_query:
        pattern = f"%{_escape_like(normalized_query)}%"
        field_conditions = []
        if "recipient_name" in search_fields:
            field_conditions.append(func.lower(Professor.name).like(pattern, escape="\\"))
        if "recipient_email" in search_fields:
            field_conditions.append(
                func.lower(func.coalesce(Professor.email, "")).like(pattern, escape="\\"),
            )
        if "subject" in search_fields:
            field_conditions.extend(
                [
                    func.lower(func.coalesce(EmailTask.approved_subject, "")).like(
                        pattern,
                        escape="\\",
                    ),
                    func.lower(func.coalesce(EmailTask.generated_subject, "")).like(
                        pattern,
                        escape="\\",
                    ),
                ],
            )
        if "batch_name" in search_fields:
            field_conditions.append(
                func.lower(func.coalesce(BatchTask.name, "")).like(pattern, escape="\\"),
            )
        conditions.append(or_(*field_conditions))
    return conditions


def _joined_from(
    statement,
    *,
    join_professor: bool,
    join_batch: bool,
):
    if join_professor:
        statement = statement.join(Professor, Professor.id == EmailTask.professor_id)
    if join_batch:
        statement = statement.outerjoin(BatchTask, BatchTask.id == EmailTask.batch_task_id)
    return statement


def _sort_expressions(view: str, sort: str | None):
    resolved_sort = sort or DEFAULT_DELIVERY_SORTS[view]
    if resolved_sort not in DELIVERY_SORTS_BY_VIEW[view]:
        raise ValueError("当前视图不支持该排序方式")
    if resolved_sort in {"scheduled_asc", "scheduled_desc"} and view == "upcoming":
        scheduled_order = (
            EmailTask.scheduled_at.asc().nulls_first()
            if resolved_sort == "scheduled_asc"
            else EmailTask.scheduled_at.desc().nulls_last()
        )
        return (
            case(
                (EmailTask.status == EmailTaskStatus.SENDING.value, 0),
                (EmailTask.status == EmailTaskStatus.APPROVED.value, 1),
                else_=2,
            ).asc(),
            scheduled_order,
            EmailTask.id.asc(),
        )
    if resolved_sort == "scheduled_asc":
        return (EmailTask.scheduled_at.asc().nulls_last(), EmailTask.id.asc())
    if resolved_sort == "updated_desc":
        return (EmailTask.updated_at.desc(), EmailTask.id.desc())
    if resolved_sort == "updated_asc":
        return (EmailTask.updated_at.asc(), EmailTask.id.asc())
    event_time = func.coalesce(
        EmailTask.sent_at,
        EmailTask.schedule_canceled_at,
        EmailTask.batch_send_canceled_at,
        EmailTask.updated_at,
    )
    if resolved_sort == "event_asc":
        return (event_time.asc(), EmailTask.id.asc())
    return (event_time.desc(), EmailTask.id.desc())


async def list_email_deliveries(
    session: AsyncSession,
    *,
    view: str,
    page: int,
    page_size: int,
    identity_id: int | None,
    source: str,
    status: str | None,
    query: str | None,
    task_id: int | None,
    sort: str | None = None,
    search_fields: tuple[str, ...] | None = None,
) -> EmailDeliveryListRead:
    if view not in DELIVERY_VIEWS:
        raise ValueError("未知发送计划视图")
    if source not in DELIVERY_SOURCES:
        raise ValueError("未知发送来源筛选")
    if status is not None and status not in DELIVERY_STATUS_FILTERS:
        raise ValueError("未知发送状态筛选")
    if sort is not None and sort not in DELIVERY_SORTS_BY_VIEW[view]:
        raise ValueError("当前视图不支持该排序方式")
    resolved_search_fields = (
        set(search_fields) if search_fields is not None else set(DELIVERY_SEARCH_FIELDS)
    )
    if not resolved_search_fields or resolved_search_fields - DELIVERY_SEARCH_FIELDS:
        raise ValueError("未知关键词搜索字段")

    filters = _base_filter_conditions(
        identity_id=identity_id,
        source=source,
        query=query,
        search_fields=resolved_search_fields,
    )
    has_query = bool((query or "").strip())
    search_joins_professor = has_query and bool(
        resolved_search_fields & {"recipient_name", "recipient_email"},
    )
    search_joins_batch = has_query and "batch_name" in resolved_search_fields
    all_delivery_condition = or_(
        _upcoming_condition(),
        _attention_condition(),
        _history_condition(),
    )
    counts_statement = _joined_from(
        select(
            func.sum(case((_upcoming_condition(), 1), else_=0)).label("upcoming"),
            func.sum(case((_attention_condition(), 1), else_=0)).label("attention"),
            func.sum(case((_history_condition(), 1), else_=0)).label("history"),
        ).select_from(EmailTask),
        join_professor=search_joins_professor,
        join_batch=search_joins_batch,
    ).where(all_delivery_condition, *filters)
    counts_row = (await session.execute(counts_statement)).one()
    counts = EmailDeliveryViewCountsRead(
        upcoming=int(counts_row.upcoming or 0),
        attention=int(counts_row.attention or 0),
        history=int(counts_row.history or 0),
    )

    item_conditions = [all_delivery_condition, *filters]
    if task_id is None:
        item_conditions.append(_view_condition(view))
        if status is not None:
            item_conditions.append(_status_condition(status))
    if task_id is not None:
        item_conditions.append(EmailTask.id == task_id)

    status_joins_batch = status in {"waiting_scheduled", "send_asap", "batch_paused"}
    total_statement = _joined_from(
        select(func.count(EmailTask.id)).select_from(EmailTask),
        join_professor=search_joins_professor,
        join_batch=search_joins_batch or status_joins_batch,
    ).where(*item_conditions)
    total_count = int((await session.scalar(total_statement)) or 0)
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    safe_page = min(page, total_pages)

    items_statement = (
        _joined_from(
            select(EmailTask)
            .options(
                joinedload(EmailTask.professor),
                joinedload(EmailTask.identity),
                joinedload(EmailTask.batch_task),
            )
            .select_from(EmailTask),
            join_professor=search_joins_professor,
            join_batch=search_joins_batch or status_joins_batch,
        )
        .where(*item_conditions)
        .order_by(*_sort_expressions(view, sort))
        .offset((safe_page - 1) * page_size)
        .limit(page_size)
    )
    tasks = list((await session.execute(items_statement)).scalars().unique())
    material_ids = {
        material_id
        for task in tasks
        for material_id in (task.selected_material_ids or [])
    }
    material_sizes: dict[int, int] = {}
    if material_ids:
        rows = (
            await session.execute(
                select(IdentityMaterial.id, IdentityMaterial.size_bytes).where(
                    IdentityMaterial.id.in_(material_ids),
                ),
            )
        ).all()
        material_sizes = {material_id: max(0, size_bytes) for material_id, size_bytes in rows}

    return EmailDeliveryListRead(
        items=[_serialize_delivery(task, material_sizes=material_sizes) for task in tasks],
        counts=counts,
        page=safe_page,
        page_size=page_size,
        total_count=total_count,
        total_pages=total_pages,
    )


def _delivery_status(task: EmailTask) -> tuple[str, str, str]:
    if task.status == EmailTaskStatus.REPLY_DETECTED.value:
        return "replied", "已回复", "已检测到导师回复"
    if task.status == EmailTaskStatus.SENT.value:
        return "sent", "已发送", "邮件已成功交给发件服务器"
    if task.batch_send_canceled_at is not None:
        return "canceled_send", "已取消发送", "该导师的批量发送已取消"
    if task.schedule_canceled_at is not None:
        return "canceled_schedule", "已取消定时", "草稿仍保留在原工作区"
    if task.status == EmailTaskStatus.SENDING.value:
        return "sending", "正在发送", "邮件已进入发送流程，暂时不能修改"
    if task.status == EmailTaskStatus.SEND_FAILED.value:
        return "send_failed", "发送失败", "请检查失败原因后重试或重新排期"
    if task.status == EmailTaskStatus.SCHEDULE_MISSED.value:
        return "schedule_missed", "错过计划", "应用未在计划时间运行，请重新决定发送时间"
    if (
        task.status == EmailTaskStatus.CANCELED.value
        and task.cancellation_reason
        in {
            EmailTaskCancellationReason.BATCH_STOPPED.value,
            EmailTaskCancellationReason.SCHEDULE_EXPIRED.value,
        }
    ):
        return "batch_stopped", "批次已结束", "可前往所属批次查看后续处理方式"
    if task.batch_task is not None and task.batch_task.status == BatchTaskStatus.PAUSED.value:
        return "batch_paused", "批次已暂停", "恢复所属批次后继续执行"
    if task.status == EmailTaskStatus.APPROVED.value:
        return "send_asap", "尽快发送", "正在等待发件间隔或发送窗口"
    return "waiting_scheduled", "等待发送", "将在计划时间进入发送流程"


def _serialize_delivery(
    task: EmailTask,
    *,
    material_sizes: dict[int, int],
) -> EmailDeliveryItemRead:
    status, status_label, status_description = _delivery_status(task)
    selected_material_ids = task.selected_material_ids or []
    batch_task = task.batch_task
    is_manual = task.source == EmailTaskSource.MANUAL.value and batch_task is None
    manual_mutable_statuses = {
        EmailTaskStatus.SCHEDULED.value,
        EmailTaskStatus.SCHEDULE_MISSED.value,
        EmailTaskStatus.SEND_FAILED.value,
    }
    batch_mutable = bool(
        batch_task is not None
        and batch_task.deleted_at is None
        and batch_task.status
        in {BatchTaskStatus.RUNNING.value, BatchTaskStatus.PAUSED.value}
        and task.scheduled_at is not None
        and task.status
        in {
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
        }
    )
    return EmailDeliveryItemRead(
        id=task.id,
        source=task.source,
        batch_task_id=task.batch_task_id,
        batch_task_name=batch_task.name if batch_task else None,
        batch_task_status=batch_task.status if batch_task else None,
        professor_id=task.professor_id,
        professor_name=task.professor.name,
        professor_email=task.professor.email,
        identity_id=task.identity_id,
        identity_name=task.identity.profile_name,
        sender_email=task.identity.email_address,
        subject=task.approved_subject or task.generated_subject or task.outreach_template_subject,
        attachment_count=len(selected_material_ids),
        attachment_size_bytes=sum(
            material_sizes.get(material_id, 0) for material_id in selected_material_ids
        ),
        status=status,
        status_label=status_label,
        status_description=status_description,
        scheduled_at=task.scheduled_at,
        last_scheduled_at=task.last_scheduled_at,
        schedule_canceled_at=task.schedule_canceled_at,
        batch_send_canceled_at=task.batch_send_canceled_at,
        approved_at=task.approved_at,
        last_send_attempt_at=task.last_send_attempt_at,
        sent_at=task.sent_at,
        last_error=task.last_error,
        retry_count=task.retry_count,
        created_at=task.created_at,
        updated_at=task.updated_at,
        can_reschedule=is_manual and task.status in manual_mutable_statuses,
        can_cancel=(
            (is_manual and task.status in manual_mutable_statuses)
            or (batch_mutable and task.batch_send_canceled_at is None)
        ),
        can_send_now=is_manual and task.status in manual_mutable_statuses,
        can_restore=batch_mutable and task.batch_send_canceled_at is not None,
        can_edit=is_manual
        and task.status
        not in {
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        },
    )


def _timestamps_match(current: datetime, expected: datetime) -> bool:
    return as_utc_aware(current) == as_utc_aware(expected)


async def _load_manual_delivery_for_update(
    session: AsyncSession,
    task_id: int,
) -> EmailTask:
    task = await session.scalar(
        select(EmailTask)
        .options(joinedload(EmailTask.batch_task))
        .where(EmailTask.id == task_id)
        .with_for_update(),
    )
    if task is None:
        raise HTTPException(status_code=404, detail="未找到发送项")
    if task.source != EmailTaskSource.MANUAL.value or task.batch_task_id is not None:
        raise HTTPException(status_code=400, detail="批量邮件需要在所属批次中修改")
    return task


async def reschedule_email_delivery(
    session: AsyncSession,
    *,
    task_id: int,
    scheduled_at: datetime,
    expected_updated_at: datetime,
) -> EmailDeliveryActionRead:
    task = await _load_manual_delivery_for_update(session, task_id)
    if not _timestamps_match(task.updated_at, expected_updated_at):
        raise HTTPException(status_code=409, detail="邮件状态已变化，请刷新后重试")
    if task.status not in {
        EmailTaskStatus.SCHEDULED.value,
        EmailTaskStatus.SCHEDULE_MISSED.value,
        EmailTaskStatus.SEND_FAILED.value,
    }:
        raise HTTPException(status_code=409, detail="邮件已进入其他状态，不能修改时间")
    next_scheduled_at = scheduled_at.astimezone(UTC)
    now = utc_now()
    if next_scheduled_at < now + MINIMUM_RESCHEDULE_DELAY:
        raise HTTPException(status_code=422, detail="新的发送时间必须晚于当前时间至少 1 分钟")

    if task.scheduled_at is not None:
        task.last_scheduled_at = task.scheduled_at
    task.scheduled_at = next_scheduled_at
    task.schedule_canceled_at = None
    task.cancellation_reason = None
    task.status = EmailTaskStatus.SCHEDULED.value
    task.last_error = None
    task.updated_at = now
    await record_email_task_log(
        session,
        task,
        "email_task.schedule_rescheduled",
        metadata={"scheduled_at": next_scheduled_at.isoformat()},
    )
    await session.commit()
    return EmailDeliveryActionRead(ok=True, task_id=task.id, message="发送时间已更新")


async def cancel_email_delivery(
    session: AsyncSession,
    *,
    task_id: int,
    expected_updated_at: datetime,
) -> EmailDeliveryActionRead:
    task = await _load_manual_delivery_for_update(session, task_id)
    if not _timestamps_match(task.updated_at, expected_updated_at):
        raise HTTPException(status_code=409, detail="邮件状态已变化，请刷新后重试")
    if task.status not in {
        EmailTaskStatus.SCHEDULED.value,
        EmailTaskStatus.SCHEDULE_MISSED.value,
        EmailTaskStatus.SEND_FAILED.value,
    }:
        raise HTTPException(status_code=409, detail="邮件已进入其他状态，不能取消定时")
    now = utc_now()
    task.last_scheduled_at = task.scheduled_at or task.last_scheduled_at
    task.scheduled_at = None
    task.schedule_canceled_at = now
    task.cancellation_reason = None
    task.status = EmailTaskStatus.REVIEW_REQUIRED.value
    task.updated_at = now
    await record_email_task_log(session, task, "email_task.schedule_canceled")
    await session.commit()
    return EmailDeliveryActionRead(
        ok=True,
        task_id=task.id,
        message="已取消定时，草稿仍保留在工作区",
    )


async def send_email_delivery_now(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    expected_updated_at: datetime,
) -> EmailDeliveryActionRead:
    async with session_factory() as session:
        task = await _load_manual_delivery_for_update(session, task_id)
        if not _timestamps_match(task.updated_at, expected_updated_at):
            raise HTTPException(status_code=409, detail="邮件状态已变化，请刷新后重试")
        if task.status not in {
            EmailTaskStatus.SCHEDULED.value,
            EmailTaskStatus.SCHEDULE_MISSED.value,
            EmailTaskStatus.SEND_FAILED.value,
        }:
            raise HTTPException(status_code=409, detail="邮件已进入其他状态，不能立即发送")
        task.last_scheduled_at = task.scheduled_at or task.last_scheduled_at
        task.scheduled_at = None
        task.schedule_canceled_at = None
        task.cancellation_reason = None
        task.status = EmailTaskStatus.APPROVED.value
        task.last_error = None
        task.updated_at = utc_now()
        await record_email_task_log(session, task, "email_task.send_now_requested")
        await session.commit()

    sent = await dispatch_email_task(
        session_factory,
        task_id,
        respect_identity_send_window=False,
    )
    return EmailDeliveryActionRead(
        ok=sent,
        task_id=task_id,
        message="邮件已发送" if sent else "邮件未能发送，请查看失败原因",
    )
