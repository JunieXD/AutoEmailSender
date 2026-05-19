from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_async_session, get_session_factory
from app.models import (
    BatchTask,
    BatchTaskStatus,
    EmailTask,
    EmailTaskCancellationReason,
    EmailTaskSource,
    EmailTaskStatus,
    IdentityMaterial,
    IdentityProfile,
    LLMProfile,
    Professor,
)
from app.schemas.batch_task import (
    BatchTaskActionResponse,
    BatchTaskCardRead,
    BatchTaskItemRead,
    CreateBatchTaskRequest,
)
from app.services.batch_schedule import (
    build_jittered_batch_schedule,
    has_future_batch_window,
    normalize_scheduled_dates,
)
from app.services.batch_task_status import count_completed_batch_task_items, sync_batch_task_completion
from app.services.materials import material_can_be_primary
from app.services.operation_logs import record_operation_log
from app.services.outreach_templates import (
    OUTREACH_GENERATION_MODE_TEMPLATE,
    get_outreach_template_defaults_validation_error,
    render_outreach_template,
    resolve_outreach_template_config,
)
from app.services.task_runtime import dispatch_email_task, expire_batch_task_if_needed


router = APIRouter(prefix="/api/batch-tasks", tags=["batch-tasks"])


@router.get("", response_model=list[BatchTaskCardRead])
async def list_batch_tasks(
    identity_id: int | None = None,
    llm_profile_id: int | None = None,
    view: str = "current",
    session: AsyncSession = Depends(get_async_session),
) -> list[BatchTaskCardRead]:
    statement = (
        select(BatchTask)
        .options(selectinload(BatchTask.email_tasks))
        .order_by(BatchTask.created_at.desc())
    )
    if identity_id is not None:
        statement = statement.where(BatchTask.identity_id == identity_id)
    if llm_profile_id is not None:
        statement = statement.where(BatchTask.llm_profile_id == llm_profile_id)
    if view == "trash":
        statement = statement.where(BatchTask.deleted_at.is_not(None))
    elif view == "current":
        statement = statement.where(BatchTask.deleted_at.is_(None))
    else:
        raise HTTPException(status_code=400, detail="未知任务视图")

    tasks = list((await session.execute(statement)).scalars().unique())
    completed_batch_task_updated = False
    for task in tasks:
        completed_batch_task_updated = sync_batch_task_completion(task) or completed_batch_task_updated
    if completed_batch_task_updated:
        await session.commit()
    return [_serialize_batch_task(task) for task in tasks]


@router.post("", response_model=BatchTaskCardRead, status_code=status.HTTP_201_CREATED)
async def create_batch_task(
    payload: CreateBatchTaskRequest,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskCardRead:
    if not payload.professor_ids:
        raise HTTPException(status_code=400, detail="请至少选择一位导师")

    try:
        scheduled_dates = normalize_scheduled_dates(payload.scheduled_dates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.schedule_type == "scheduled":
        if not scheduled_dates:
            raise HTTPException(status_code=400, detail="请至少选择一个发送日期")
        _validate_time_window(payload.window_start_time, payload.window_end_time)
        if not payload.emails_per_window or payload.emails_per_window <= 0:
            raise HTTPException(status_code=400, detail="请输入每天发送数量")
        if not has_future_batch_window(
            datetime.now().astimezone(),
            scheduled_dates=scheduled_dates,
            window_end_time=payload.window_end_time,
        ):
            raise HTTPException(status_code=400, detail="当前定时发送窗口已全部过期，请重新选择发送日期或结束时间。")

    identity = await session.scalar(
        select(IdentityProfile)
        .options(
            selectinload(IdentityProfile.materials),
            selectinload(IdentityProfile.current_primary_material),
        )
        .where(IdentityProfile.id == payload.identity_id),
    )
    if not identity:
        raise HTTPException(status_code=404, detail="未找到身份配置")

    llm_profile = await session.get(LLMProfile, payload.llm_profile_id)
    if not llm_profile:
        raise HTTPException(status_code=404, detail="未找到 LLM 配置")

    professors = list(
        (
            await session.execute(
                select(Professor).where(
                    Professor.id.in_(payload.professor_ids),
                    Professor.archived_at.is_(None),
                ),
            )
        ).scalars()
    )
    if len(professors) != len(set(payload.professor_ids)):
        raise HTTPException(status_code=404, detail="部分导师不存在或已被移入回收站")

    scheduled_at_values: list[datetime | None] = [None] * len(professors)
    if payload.schedule_type == "scheduled":
        try:
            scheduled_at_values = list(
                build_jittered_batch_schedule(
                    task_count=len(professors),
                    scheduled_dates=scheduled_dates,
                    window_start_time=payload.window_start_time or "",
                    window_end_time=payload.window_end_time or "",
                    emails_per_window=payload.emails_per_window or 0,
                    now=datetime.now().astimezone(),
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    material_map = {material.id: material for material in identity.materials}
    primary_material_id = payload.primary_material_id or identity.current_primary_material_id
    if primary_material_id is not None:
        primary_material = material_map.get(primary_material_id)
        if primary_material is None:
            raise HTTPException(status_code=400, detail="默认材料不属于当前身份")
        if not material_can_be_primary(primary_material):
            raise HTTPException(status_code=400, detail="当前材料不支持作为默认材料")

    selected_material_ids = payload.selected_material_ids or None
    if selected_material_ids:
        if len(set(selected_material_ids)) != len(set(material_map) & set(selected_material_ids)):
            raise HTTPException(status_code=400, detail="存在不属于当前身份的随信材料")

    requested_subject = _normalize_nullable_text(payload.outreach_template_subject) or _normalize_nullable_text(
        payload.email_subject,
    )
    requested_body_text = _normalize_nullable_text(
        payload.outreach_template_body_text,
    ) or _normalize_nullable_text(payload.email_body)
    requested_generation_mode = (
        str(payload.outreach_generation_mode or identity.outreach_generation_mode or "llm").strip().lower()
    )
    outreach_config = resolve_outreach_template_config(
        identity,
        generation_mode=requested_generation_mode,
        subject_template=requested_subject,
        body_text_template=requested_body_text,
        body_html_template=payload.outreach_template_body_html,
    )
    detail = get_outreach_template_defaults_validation_error(
        outreach_config.subject_template,
        outreach_config.body_text_template,
    )
    if detail:
        raise HTTPException(status_code=400, detail=detail)

    batch_task = BatchTask(
        identity_id=payload.identity_id,
        llm_profile_id=payload.llm_profile_id,
        name=payload.name,
        schedule_type=payload.schedule_type,
        window_start_time=payload.window_start_time,
        window_end_time=payload.window_end_time,
        emails_per_window=payload.emails_per_window,
        scheduled_dates=scheduled_dates or None,
        status=BatchTaskStatus.RUNNING.value,
        primary_material_id=primary_material_id,
        email_subject=_normalize_nullable_text(outreach_config.subject_template),
        email_body=_normalize_nullable_text(outreach_config.body_text_template),
        selected_material_ids=selected_material_ids,
        target_count=len(professors),
    )
    session.add(batch_task)
    await session.flush()

    created_email_tasks: list[EmailTask] = []
    for index, professor in enumerate(professors):
        generated_subject = None
        generated_body_text = None
        generated_body_html = None
        task_status = EmailTaskStatus.DISCOVERED.value
        approved_at = None
        if outreach_config.generation_mode == OUTREACH_GENERATION_MODE_TEMPLATE:
            try:
                rendered = render_outreach_template(
                    identity,
                    professor,
                    subject_template=outreach_config.subject_template,
                    body_text_template=outreach_config.body_text_template,
                    body_html_template=outreach_config.body_html_template,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            generated_subject = rendered.subject
            generated_body_text = rendered.body_text
            generated_body_html = rendered.body_html
            task_status = EmailTaskStatus.APPROVED.value
            approved_at = datetime.now(UTC)

        email_task = EmailTask(
            source=EmailTaskSource.BATCH.value,
            batch_task_id=batch_task.id,
            identity_id=payload.identity_id,
            llm_profile_id=payload.llm_profile_id,
            professor_id=professor.id,
            primary_material_id=primary_material_id,
            outreach_generation_mode=outreach_config.generation_mode,
            outreach_template_subject=_normalize_nullable_text(outreach_config.subject_template),
            outreach_template_body_text=_normalize_nullable_text(outreach_config.body_text_template),
            outreach_template_body_html=_normalize_nullable_text(outreach_config.body_html_template),
            status=task_status,
            generated_subject=generated_subject,
            generated_content_text=generated_body_text,
            generated_content_html=generated_body_html,
            approved_subject=generated_subject,
            approved_body_text=generated_body_text,
            approved_body_html=generated_body_html,
            approved_at=approved_at,
            scheduled_at=scheduled_at_values[index],
            selected_material_ids=selected_material_ids,
        )
        session.add(email_task)
        created_email_tasks.append(email_task)

    await session.flush()
    created_email_task_ids = [email_task.id for email_task in created_email_tasks]
    await record_operation_log(
        session,
        category="email",
        event_name="batch_task.created",
        entity_type="batch_task",
        entity_id=str(batch_task.id),
        metadata={
            "target_count": batch_task.target_count,
            "identity_id": batch_task.identity_id,
            "llm_profile_id": batch_task.llm_profile_id,
            "schedule_type": batch_task.schedule_type,
        },
    )
    await session.commit()
    if (
        outreach_config.generation_mode == OUTREACH_GENERATION_MODE_TEMPLATE
        and payload.schedule_type == "immediate"
    ):
        session_factory = get_session_factory()
        for email_task_id in created_email_task_ids:
            await dispatch_email_task(session_factory, email_task_id)
    refreshed_batch_task = await _load_batch_task_for_serialization(session, batch_task.id)
    if refreshed_batch_task is not None and sync_batch_task_completion(refreshed_batch_task):
        await session.commit()
    return _serialize_batch_task(refreshed_batch_task)


@router.get("/{task_id}/items", response_model=list[BatchTaskItemRead])
async def list_batch_task_items(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> list[BatchTaskItemRead]:
    exists = await session.scalar(select(BatchTask.id).where(BatchTask.id == task_id))
    if exists is None:
        raise HTTPException(status_code=404, detail="未找到批量任务")

    statement = (
        select(EmailTask)
        .options(selectinload(EmailTask.professor))
        .where(EmailTask.batch_task_id == task_id)
        .order_by(EmailTask.created_at.asc(), EmailTask.id.asc())
    )
    email_tasks = list((await session.execute(statement)).scalars().unique())
    return [_serialize_batch_task_item(email_task) for email_task in email_tasks]


@router.post("/{task_id}/pause", response_model=BatchTaskActionResponse)
async def pause_batch_task(
    task_id: int,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
    task.status = BatchTaskStatus.PAUSED.value
    task.updated_at = datetime.now(UTC)
    for email_task in task.email_tasks:
        if email_task.status == EmailTaskStatus.GENERATING_DRAFT.value:
            email_task.status = email_task.draft_generation_previous_status or EmailTaskStatus.DISCOVERED.value
            email_task.draft_generation_previous_status = None
            email_task.updated_at = datetime.now(UTC)
    await _record_batch_task_action(session, task, "batch_task.paused")
    await session.commit()
    _cancel_running_batch_drafts(request, task_id)
    await session.refresh(task, attribute_names=["email_tasks"])
    return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))


@router.post("/{task_id}/resume", response_model=BatchTaskActionResponse)
async def resume_batch_task(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
    task.status = BatchTaskStatus.RUNNING.value
    task.updated_at = datetime.now(UTC)
    expired = await expire_batch_task_if_needed(session, task, datetime.now().astimezone())
    if not expired:
        await _record_batch_task_action(session, task, "batch_task.resumed")
    await session.commit()
    await session.refresh(task, attribute_names=["email_tasks"])
    return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))


@router.post("/{task_id}/stop", response_model=BatchTaskActionResponse)
async def stop_batch_task(
    task_id: int,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
    task.status = BatchTaskStatus.STOPPED.value
    task.updated_at = datetime.now(UTC)
    for email_task in task.email_tasks:
        if email_task.status not in {
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
            EmailTaskStatus.SEND_FAILED.value,
        }:
            email_task.status = EmailTaskStatus.CANCELED.value
            email_task.cancellation_reason = EmailTaskCancellationReason.BATCH_STOPPED.value
            email_task.draft_generation_previous_status = None
            email_task.updated_at = datetime.now(UTC)
    await _record_batch_task_action(session, task, "batch_task.stopped")
    await session.commit()
    _cancel_running_batch_drafts(request, task_id)
    await session.refresh(task, attribute_names=["email_tasks"])
    return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))


BATCH_TASK_DELETABLE_STATUSES = {
    BatchTaskStatus.STOPPED.value,
    BatchTaskStatus.COMPLETED.value,
    BatchTaskStatus.EXPIRED.value,
}


@router.post("/{task_id}/delete", response_model=BatchTaskActionResponse)
async def delete_batch_task(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
    sync_batch_task_completion(task)
    serialized = _serialize_batch_task(task)
    if serialized.status not in BATCH_TASK_DELETABLE_STATUSES:
        raise HTTPException(status_code=400, detail="请先中止/取消任务后再删除")
    previous_deleted_at = task.deleted_at
    if task.deleted_at is None:
        now = datetime.now(UTC)
        task.deleted_at = now
        task.updated_at = now
    await _record_batch_task_action(
        session,
        task,
        "batch_task.deleted",
        extra_metadata={
            "previous_deleted_at": previous_deleted_at.isoformat() if previous_deleted_at else None,
        },
    )
    await session.commit()
    await session.refresh(task, attribute_names=["email_tasks"])
    return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))


@router.post("/{task_id}/restore", response_model=BatchTaskActionResponse)
async def restore_batch_task(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
    previous_deleted_at = task.deleted_at
    if task.deleted_at is not None:
        await _sanitize_batch_task_material_references_before_restore(session, task)
        task.deleted_at = None
        task.updated_at = datetime.now(UTC)
    await _record_batch_task_action(
        session,
        task,
        "batch_task.restored",
        extra_metadata={
            "previous_deleted_at": previous_deleted_at.isoformat() if previous_deleted_at else None,
        },
    )
    await session.commit()
    await session.refresh(task, attribute_names=["email_tasks"])
    return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))


async def _get_batch_task(session: AsyncSession, task_id: int) -> BatchTask:
    task = await _load_batch_task_for_serialization(session, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="未找到批量任务")
    return task


async def _load_batch_task_for_serialization(session: AsyncSession, task_id: int) -> BatchTask | None:
    return await session.scalar(
        select(BatchTask)
        .options(selectinload(BatchTask.email_tasks))
        .where(BatchTask.id == task_id)
        .execution_options(populate_existing=True),
    )


async def _record_batch_task_action(
    session: AsyncSession,
    task: BatchTask,
    event_name: str,
    extra_metadata: dict[str, object] | None = None,
) -> None:
    metadata = {
        "status": task.status,
        "target_count": task.target_count,
        "identity_id": task.identity_id,
        "llm_profile_id": task.llm_profile_id,
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    await record_operation_log(
        session,
        category="email",
        event_name=event_name,
        entity_type="batch_task",
        entity_id=str(task.id),
        metadata=metadata,
    )


def _cancel_running_batch_drafts(request: Request, task_id: int) -> None:
    runtime_manager = getattr(request.app.state, "runtime_manager", None)
    if runtime_manager is not None:
        runtime_manager.cancel_batch_draft_generation(task_id)


def _serialize_batch_task_item(email_task: EmailTask) -> BatchTaskItemRead:
    professor = email_task.professor
    return BatchTaskItemRead(
        id=email_task.id,
        professor_id=professor.id,
        professor_name=professor.name,
        professor_email=professor.email,
        professor_title=professor.title,
        professor_school=professor.school,
        status=email_task.status,
        cancellation_reason=email_task.cancellation_reason,
        match_score=email_task.match_score,
        scheduled_at=email_task.scheduled_at,
        sent_at=email_task.sent_at,
        last_send_attempt_at=email_task.last_send_attempt_at,
        last_error=email_task.last_error,
        is_replied=email_task.is_replied,
        updated_at=email_task.updated_at,
    )


async def _sanitize_batch_task_material_references_before_restore(session: AsyncSession, task: BatchTask) -> None:
    material_ids = set(task.selected_material_ids or [])
    if task.primary_material_id is not None:
        material_ids.add(task.primary_material_id)
    if not material_ids:
        return

    existing_material_ids = set(
        await session.scalars(
            select(IdentityMaterial.id).where(
                IdentityMaterial.identity_id == task.identity_id,
                IdentityMaterial.id.in_(material_ids),
            ),
        ),
    )
    removed_primary = task.primary_material_id is not None and task.primary_material_id not in existing_material_ids
    updated = False
    if removed_primary:
        task.primary_material_id = None
        if task.status not in BATCH_TASK_DELETABLE_STATUSES:
            task.status = BatchTaskStatus.STOPPED.value
        updated = True
    if task.selected_material_ids is not None:
        filtered_material_ids = [
            material_id
            for material_id in task.selected_material_ids
            if material_id in existing_material_ids
        ]
        if filtered_material_ids != task.selected_material_ids:
            task.selected_material_ids = filtered_material_ids
            updated = True
    if updated:
        task.updated_at = datetime.now(UTC)


def _serialize_batch_task(task: BatchTask) -> BatchTaskCardRead:
    status_counter = Counter(email_task.status for email_task in task.email_tasks)

    completed_count = count_completed_batch_task_items(task)
    status = task.status

    pending_generation_count = sum(
        status_counter.get(item, 0)
        for item in [
            EmailTaskStatus.DISCOVERED.value,
            EmailTaskStatus.MATCHED.value,
        ]
    )

    return BatchTaskCardRead(
        id=task.id,
        name=task.name,
        status=status,
        schedule_type=task.schedule_type,
        window_start_time=task.window_start_time,
        window_end_time=task.window_end_time,
        emails_per_window=task.emails_per_window,
        scheduled_dates=task.scheduled_dates,
        email_subject=task.email_subject,
        target_count=task.target_count,
        completed_count=completed_count,
        identity_id=task.identity_id,
        llm_profile_id=task.llm_profile_id,
        pending_generation_count=pending_generation_count,
        generating_draft_count=status_counter.get(EmailTaskStatus.GENERATING_DRAFT.value, 0),
        draft_failed_count=status_counter.get(EmailTaskStatus.DRAFT_FAILED.value, 0),
        review_required_count=status_counter.get(EmailTaskStatus.REVIEW_REQUIRED.value, 0),
        approved_count=status_counter.get(EmailTaskStatus.APPROVED.value, 0),
        scheduled_count=status_counter.get(EmailTaskStatus.SCHEDULED.value, 0),
        sent_count=status_counter.get(EmailTaskStatus.SENT.value, 0),
        failed_count=status_counter.get(EmailTaskStatus.SEND_FAILED.value, 0),
        replied_count=status_counter.get(EmailTaskStatus.REPLY_DETECTED.value, 0),
        created_at=task.created_at,
        updated_at=task.updated_at,
        deleted_at=task.deleted_at,
    )


def _validate_time_window(start_time: str | None, end_time: str | None) -> None:
    if not start_time or not end_time:
        raise HTTPException(status_code=400, detail="请填写发送时间窗口")
    if not re.fullmatch(r"\d{2}:\d{2}", start_time) or not re.fullmatch(r"\d{2}:\d{2}", end_time):
        raise HTTPException(status_code=400, detail="发送时间必须使用 HH:mm 格式")
    try:
        start = time.fromisoformat(start_time)
        end = time.fromisoformat(end_time)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="发送时间必须使用 HH:mm 格式") from exc
    if end <= start:
        raise HTTPException(status_code=400, detail="结束时间必须晚于开始时间")


def _normalize_nullable_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
