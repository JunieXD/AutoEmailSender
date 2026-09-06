from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, selectinload

import app.modules.llm.public as llm_runtime
from app.core.database import get_async_session, get_session_factory
from app.core.query_chunks import chunked_values
from app.core.time import as_utc_aware, local_now, utc_now
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
from app.modules.campaigns.public import (
    OUTREACH_GENERATION_MODE_TEMPLATE,
    BatchTaskActionResponse,
    BatchTaskAttachmentDefaultsRead,
    BatchTaskBulkApproveDraftsRequest,
    BatchTaskBulkApproveDraftsResponse,
    BatchTaskCardRead,
    BatchTaskItemRead,
    BatchTaskResendContextError,
    BatchTaskResendContextRead,
    CreateBatchTaskRequest,
    batch_item_uses_llm_generation,
    build_batch_task_resend_context,
    normalize_batch_item_generation_mode,
    sync_batch_task_completion,
)
from app.modules.campaigns.status import (
    BATCH_TASK_DELETABLE_STATUSES,
    email_task_is_not_user_removed_expression,
    sanitize_batch_task_material_references_before_restore,
)
from app.modules.identities.public import (
    get_active_identity_profile,
)
from app.modules.llm.public import get_active_llm_profile
from app.modules.workspace.public import (
    BatchDraftApprovalConflictError,
    EmailTaskApprovalRequest,
    EmailTaskOutreachConfigRequest,
    EmailTaskRewriteDraftRequest,
    WorkspaceThreadRead,
    approve_and_send_task,
    approve_draft_task,
    approve_generated_batch_drafts,
    build_workspace_thread_for_task,
    expire_batch_task_if_needed,
    regenerate_task_draft,
    rewrite_task_draft,
)
from app.services.match_results import load_resolved_match_results
from app.services.operation_logs import record_operation_log

from .creation import (
    BatchTaskCreationError,
    _normalize_nullable_text as _normalize_nullable_text,
    _validate_time_window as _validate_time_window,
    create_batch_task_record as create_batch_task_record,
)
from .item_policy import (
    BATCH_TASK_ITEM_DEFERRED_COLUMNS as BATCH_TASK_ITEM_DEFERRED_COLUMNS,
    BATCH_TASK_ITEM_SEND_ACTION_BATCH_STATUSES as BATCH_TASK_ITEM_SEND_ACTION_BATCH_STATUSES,
    BATCH_TASK_ITEM_SEND_CANCELLABLE_STATUSES as BATCH_TASK_ITEM_SEND_CANCELLABLE_STATUSES,
    _batch_task_allows_item_send_actions as _batch_task_allows_item_send_actions,
    _can_cancel_batch_task_item_send as _can_cancel_batch_task_item_send,
    _can_restore_batch_task_item_send as _can_restore_batch_task_item_send,
    _is_user_removed_batch_item as _is_user_removed_batch_item,
    _visible_batch_email_tasks as _visible_batch_email_tasks,
)
from .projections import (
    BatchTaskCardMetrics as BatchTaskCardMetrics,
    _batch_task_card_metrics_from_email_tasks as _batch_task_card_metrics_from_email_tasks,
    _load_batch_task_card_metrics as _load_batch_task_card_metrics,
    _load_batch_task_for_serialization as _load_batch_task_for_serialization,
    _serialize_batch_task as _serialize_batch_task,
    _serialize_batch_task_item as _serialize_batch_task_item,
    _should_sync_batch_task_completion as _should_sync_batch_task_completion,
)
from .review import apply_batch_review_outreach_template

router = APIRouter(prefix="/api/batch-tasks", tags=["batch-tasks"])


@router.get(
    "/attachment-defaults",
    response_model=BatchTaskAttachmentDefaultsRead,
)
async def get_batch_task_attachment_defaults(
    identity_id: int = Query(..., ge=1),
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskAttachmentDefaultsRead:
    identity = await get_active_identity_profile(session, identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="未找到身份配置")

    stored_ids = await session.scalar(
        select(BatchTask.selected_material_ids)
        .where(
            BatchTask.identity_id == identity.id,
            BatchTask.deleted_at.is_(None),
        )
        .order_by(BatchTask.created_at.desc(), BatchTask.id.desc())
        .limit(1),
    )
    if not isinstance(stored_ids, list):
        stored_ids = []

    available_material_ids = set(await session.scalars(select(IdentityMaterial.id)))
    selected_material_ids: list[int] = []
    seen_ids: set[int] = set()
    for material_id in stored_ids:
        if (
            type(material_id) is not int
            or material_id not in available_material_ids
            or material_id in seen_ids
        ):
            continue
        selected_material_ids.append(material_id)
        seen_ids.add(material_id)

    return BatchTaskAttachmentDefaultsRead(
        identity_id=identity.id,
        selected_material_ids=selected_material_ids,
    )


@router.get("", response_model=list[BatchTaskCardRead])
async def list_batch_tasks(
    identity_id: int | None = None,
    llm_profile_id: int | None = None,
    view: str = "current",
    session: AsyncSession = Depends(get_async_session),
) -> list[BatchTaskCardRead]:
    statement = select(BatchTask).order_by(BatchTask.created_at.desc())
    if identity_id is not None:
        statement = statement.where(BatchTask.identity_id == identity_id)
    if view == "trash":
        statement = statement.where(BatchTask.deleted_at.is_not(None))
    elif view == "current":
        statement = statement.where(BatchTask.deleted_at.is_(None))
    else:
        raise HTTPException(status_code=400, detail="未知任务视图")

    tasks = list((await session.execute(statement)).scalars().unique())
    now = utc_now()
    metrics_by_task_id = await _load_batch_task_card_metrics(
        session,
        [task.id for task in tasks],
        now=now,
    )
    completed_task_ids = [
        task.id
        for task in tasks
        if _should_sync_batch_task_completion(
            task,
            metrics_by_task_id.get(task.id, BatchTaskCardMetrics()),
        )
    ]
    if completed_task_ids:
        completed_task_id_set = set(completed_task_ids)
        for task_id_chunk in chunked_values(completed_task_ids):
            await session.execute(
                update(BatchTask)
                .where(BatchTask.id.in_(task_id_chunk))
                .values(
                    status=BatchTaskStatus.COMPLETED.value,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
        for task in tasks:
            if task.id in completed_task_id_set:
                task.status = BatchTaskStatus.COMPLETED.value
                task.updated_at = now
        await session.commit()
    return [
        _serialize_batch_task(
            task,
            metrics=metrics_by_task_id.get(task.id, BatchTaskCardMetrics()),
        )
        for task in tasks
    ]


@router.get("/{task_id}/resend-context", response_model=BatchTaskResendContextRead)
async def get_batch_task_resend_context(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskResendContextRead:
    try:
        return await build_batch_task_resend_context(session, task_id)
    except BatchTaskResendContextError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/{task_id}/summary", response_model=BatchTaskCardRead)
async def get_batch_task_summary(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskCardRead:
    """Cheap polling payload: task card with status counters, no item rows.

    The desktop UI polls this while a running task is open and only refetches
    the full item list when the counters change.
    """
    task = await session.scalar(select(BatchTask).where(BatchTask.id == task_id))
    if task is None:
        raise HTTPException(status_code=404, detail="未找到批量任务")
    metrics_by_task_id = await _load_batch_task_card_metrics(
        session,
        [task.id],
        now=utc_now(),
    )
    return _serialize_batch_task(
        task,
        metrics=metrics_by_task_id.get(task.id, BatchTaskCardMetrics()),
    )


@router.get("/{task_id}/items", response_model=list[BatchTaskItemRead])
async def list_batch_task_items(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> list[BatchTaskItemRead]:
    identity_id = await session.scalar(
        select(BatchTask.identity_id).where(BatchTask.id == task_id),
    )
    if identity_id is None:
        raise HTTPException(status_code=404, detail="未找到批量任务")

    statement = (
        select(EmailTask)
        .options(
            load_only(
                EmailTask.id,
                EmailTask.batch_task_id,
                EmailTask.professor_id,
                EmailTask.primary_material_id,
                EmailTask.selected_material_ids,
                EmailTask.status,
                EmailTask.cancellation_reason,
                EmailTask.batch_send_canceled_at,
                EmailTask.outreach_generation_mode,
                EmailTask.draft_generation_source,
                EmailTask.draft_fallback_reason,
                EmailTask.scheduled_at,
                EmailTask.last_send_attempt_at,
                EmailTask.sent_at,
                EmailTask.is_replied,
                EmailTask.last_error,
                EmailTask.created_at,
                EmailTask.updated_at,
            ),
            selectinload(EmailTask.batch_task).load_only(
                BatchTask.id,
                BatchTask.schedule_type,
                BatchTask.status,
                BatchTask.deleted_at,
            ),
            selectinload(EmailTask.professor)
            .load_only(
                Professor.id,
                Professor.name,
                Professor.email,
                Professor.title,
                Professor.school,
                Professor.research_direction,
            )
            .lazyload(Professor.tags),
            selectinload(EmailTask.primary_material).load_only(
                IdentityMaterial.id,
            ),
        )
        .where(
            EmailTask.batch_task_id == task_id,
            email_task_is_not_user_removed_expression(),
        )
        .order_by(EmailTask.created_at.asc(), EmailTask.id.asc())
    )
    email_tasks = list((await session.execute(statement)).scalars().unique())
    resolved_matches = await load_resolved_match_results(
        session,
        active_identity_id=identity_id,
        professor_ids=[email_task.professor_id for email_task in email_tasks],
    )
    selected_material_ids = {
        material_id
        for email_task in email_tasks
        for material_id in (email_task.selected_material_ids or [])
    }
    material_sizes: dict[int, int] = {}
    if selected_material_ids:
        rows = []
        for material_id_chunk in chunked_values(selected_material_ids):
            rows.extend(
                (
                    await session.execute(
                        select(IdentityMaterial.id, IdentityMaterial.size_bytes).where(
                            IdentityMaterial.id.in_(material_id_chunk),
                        ),
                    )
                ).all(),
            )
        material_sizes = {
            material_id: max(0, size_bytes) for material_id, size_bytes in rows
        }
    return [
        _serialize_batch_task_item(
            email_task,
            material_sizes=material_sizes,
            match_score=(
                resolved_matches.get(email_task.professor_id).match_score
                if resolved_matches.get(email_task.professor_id) is not None
                else None
            ),
        )
        for email_task in email_tasks
    ]


@router.get("/{task_id}/items/{item_id}/thread", response_model=WorkspaceThreadRead)
async def get_batch_task_item_thread(
    task_id: int,
    item_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    await _get_batch_task_item(session, task_id, item_id)
    return await build_workspace_thread_for_task(
        session,
        task_id=item_id,
        include_communication_events=False,
    )


@router.post(
    "/{task_id}/approve-all-drafts",
    response_model=BatchTaskBulkApproveDraftsResponse,
)
async def approve_all_batch_task_drafts(
    task_id: int,
    payload: BatchTaskBulkApproveDraftsRequest,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskBulkApproveDraftsResponse:
    await _get_batch_task(session, task_id)
    try:
        approved_count = await approve_generated_batch_drafts(
            get_session_factory(),
            task_id,
            payload.item_ids,
        )
    except BatchDraftApprovalConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.expire_all()
    task = await _get_batch_task(session, task_id)
    return BatchTaskBulkApproveDraftsResponse(
        ok=True,
        approved_count=approved_count,
        task=_serialize_batch_task(task),
    )


@router.post(
    "/{task_id}/items/{item_id}/regenerate-draft", response_model=WorkspaceThreadRead
)
async def regenerate_batch_task_item_draft(
    task_id: int,
    item_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    await _run_batch_task_item_workspace_action(
        session,
        task_id,
        item_id,
        lambda: regenerate_task_draft(get_session_factory(), item_id),
    )
    return await build_workspace_thread_for_task(
        session,
        task_id=item_id,
        include_communication_events=False,
    )


@router.post(
    "/{task_id}/items/{item_id}/rewrite-draft", response_model=WorkspaceThreadRead
)
async def rewrite_batch_task_item_draft(
    task_id: int,
    item_id: int,
    payload: EmailTaskRewriteDraftRequest,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    await _run_batch_task_item_workspace_action(
        session,
        task_id,
        item_id,
        lambda: rewrite_task_draft(get_session_factory(), item_id, payload),
    )
    return await build_workspace_thread_for_task(
        session,
        task_id=item_id,
        include_communication_events=False,
    )


@router.post(
    "/{task_id}/items/{item_id}/outreach-config", response_model=WorkspaceThreadRead
)
async def update_batch_task_item_outreach_config(
    task_id: int,
    item_id: int,
    payload: EmailTaskOutreachConfigRequest,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    await _run_batch_task_item_workspace_action(
        session,
        task_id,
        item_id,
        lambda: apply_batch_review_outreach_template(
            get_session_factory(),
            task_id,
            item_id,
            outreach_template_id=payload.outreach_template_id,
        ),
    )
    return await build_workspace_thread_for_task(
        session,
        task_id=item_id,
        include_communication_events=False,
    )


@router.post("/{task_id}/items/{item_id}/approve", response_model=WorkspaceThreadRead)
async def approve_batch_task_item_draft(
    task_id: int,
    item_id: int,
    payload: EmailTaskApprovalRequest,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    await _run_batch_task_item_workspace_action(
        session,
        task_id,
        item_id,
        lambda: approve_draft_task(get_session_factory(), item_id, payload),
    )
    return await build_workspace_thread_for_task(
        session,
        task_id=item_id,
        include_communication_events=False,
    )


@router.post(
    "/{task_id}/items/{item_id}/approve-and-send", response_model=WorkspaceThreadRead
)
async def approve_and_send_batch_task_item_draft(
    task_id: int,
    item_id: int,
    payload: EmailTaskApprovalRequest,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    await _run_batch_task_item_workspace_action(
        session,
        task_id,
        item_id,
        lambda: approve_and_send_task(get_session_factory(), item_id, payload),
    )
    return await build_workspace_thread_for_task(
        session,
        task_id=item_id,
        include_communication_events=False,
    )


@router.post("/{task_id}/pause", response_model=BatchTaskActionResponse)
async def pause_batch_task(
    task_id: int,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
    task.status = BatchTaskStatus.PAUSED.value
    task.updated_at = utc_now()
    for email_task in task.email_tasks:
        if _is_user_removed_batch_item(email_task):
            continue
        if email_task.status == EmailTaskStatus.GENERATING_DRAFT.value:
            email_task.status = (
                email_task.draft_generation_previous_status
                or EmailTaskStatus.DISCOVERED.value
            )
            email_task.draft_generation_previous_status = None
            email_task.draft_generation_started_at = None
            email_task.draft_claim_id = None
            email_task.draft_claimed_at = None
            email_task.draft_lease_expires_at = None
            email_task.updated_at = utc_now()
    await _record_batch_task_action(session, task, "batch_task.paused")
    await session.commit()
    _cancel_running_batch_drafts(request, task_id)
    await session.refresh(task, attribute_names=["email_tasks"])
    return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))


@router.post("/{task_id}/resume", response_model=BatchTaskActionResponse)
async def resume_batch_task(
    task_id: int,
    replacement_llm_profile_id: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
    if await get_active_identity_profile(session, task.identity_id) is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CAMPAIGN_IDENTITY_RETIRED",
                "message": (
                    f"批量任务 #{task.id} 使用的发件身份 #{task.identity_id} 已删除，"
                    "不能继续执行。历史记录会保留；如需再次联系，请选择其他发件身份新建任务。"
                ),
                "batch_task_id": task.id,
                "identity_id": task.identity_id,
            },
        )
    pending_llm_tasks = [
        email_task
        for email_task in task.email_tasks
        if email_task.batch_send_canceled_at is None
        and email_task.status
        in {
            EmailTaskStatus.DISCOVERED.value,
            EmailTaskStatus.MATCHED.value,
            EmailTaskStatus.DRAFT_FAILED.value,
        }
        and normalize_batch_item_generation_mode(email_task) == "llm"
    ]
    if replacement_llm_profile_id is not None:
        replacement = await get_active_llm_profile(
            session,
            replacement_llm_profile_id,
        )
        if replacement is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "CAMPAIGN_LLM_PROFILE_REPLACEMENT_INVALID",
                    "message": "用于继续活动的模型配置不存在或已删除。",
                },
            )
        task.llm_profile_id = replacement.id
        for email_task in pending_llm_tasks:
            email_task.llm_profile_id = replacement.id
            email_task.updated_at = utc_now()
    elif (
        pending_llm_tasks
        and await get_active_llm_profile(
            session,
            task.llm_profile_id,
        )
        is None
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CAMPAIGN_LLM_PROFILE_REPLACEMENT_REQUIRED",
                "message": "原模型配置已删除，请选择新的模型后再继续活动。",
                "campaign_id": task.id,
                "llm_profile_id": task.llm_profile_id,
                "pending_llm_draft_count": len(pending_llm_tasks),
            },
        )
    task.status = BatchTaskStatus.RUNNING.value
    task.updated_at = utc_now()
    expired = await expire_batch_task_if_needed(session, task, local_now())
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
    task.updated_at = utc_now()
    for email_task in task.email_tasks:
        if _is_user_removed_batch_item(email_task):
            continue
        if email_task.status not in {
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
            EmailTaskStatus.SEND_FAILED.value,
        }:
            email_task.status = EmailTaskStatus.CANCELED.value
            email_task.cancellation_reason = (
                EmailTaskCancellationReason.BATCH_STOPPED.value
            )
            email_task.draft_generation_previous_status = None
            email_task.draft_generation_started_at = None
            email_task.draft_claim_id = None
            email_task.draft_claimed_at = None
            email_task.draft_lease_expires_at = None
            email_task.updated_at = utc_now()
    await _record_batch_task_action(session, task, "batch_task.stopped")
    await session.commit()
    _cancel_running_batch_drafts(request, task_id)
    await session.refresh(task, attribute_names=["email_tasks"])
    return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))


@router.post("/{task_id}/delete", response_model=BatchTaskActionResponse)
async def delete_batch_task(
    task_id: int,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
    await session.execute(
        update(BatchTask)
        .where(BatchTask.id == task_id)
        .values(updated_at=BatchTask.updated_at)
        .execution_options(synchronize_session=False)
    )
    await session.refresh(task, attribute_names=["status", "deleted_at", "email_tasks"])
    sync_batch_task_completion(task)
    sending_item_ids = sorted(
        email_task.id
        for email_task in task.email_tasks
        if email_task.status == EmailTaskStatus.SENDING.value
    )
    if sending_item_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "BATCH_TASK_TRASH_SENDING",
                "message": (
                    f"批量任务 #{task.id} 暂时无法移入回收站：邮件任务 "
                    f"#{'、#'.join(str(item_id) for item_id in sending_item_ids[:10])}"
                    f"{' 等' if len(sending_item_ids) > 10 else ''} 正在发送。"
                    "请等待发送结果确认后再试。"
                ),
                "details": {
                    "batch_task_id": task.id,
                    "email_task_ids": sending_item_ids,
                    "status": EmailTaskStatus.SENDING.value,
                },
            },
        )
    serialized = _serialize_batch_task(task)
    now = utc_now()
    if serialized.status not in BATCH_TASK_DELETABLE_STATUSES:
        task.status = BatchTaskStatus.STOPPED.value
    for email_task in task.email_tasks:
        if _is_user_removed_batch_item(email_task):
            continue
        if email_task.status not in {
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
            EmailTaskStatus.SEND_FAILED.value,
        }:
            email_task.status = EmailTaskStatus.CANCELED.value
            email_task.cancellation_reason = (
                EmailTaskCancellationReason.BATCH_STOPPED.value
            )
            email_task.draft_generation_previous_status = None
            email_task.draft_generation_started_at = None
            email_task.draft_claim_id = None
            email_task.draft_claimed_at = None
            email_task.draft_lease_expires_at = None
            email_task.updated_at = now
    previous_deleted_at = task.deleted_at
    if task.deleted_at is None:
        task.deleted_at = now
        task.updated_at = now
    await _record_batch_task_action(
        session,
        task,
        "batch_task.deleted",
        extra_metadata={
            "previous_deleted_at": previous_deleted_at.isoformat()
            if previous_deleted_at
            else None,
        },
    )
    await session.commit()
    _cancel_running_batch_drafts(request, task_id)
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
        await sanitize_batch_task_material_references_before_restore(session, task)
        task.deleted_at = None
        task.updated_at = utc_now()
    await _record_batch_task_action(
        session,
        task,
        "batch_task.restored",
        extra_metadata={
            "previous_deleted_at": previous_deleted_at.isoformat()
            if previous_deleted_at
            else None,
        },
    )
    await session.commit()
    await session.refresh(task, attribute_names=["email_tasks"])
    return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))


@router.post(
    "/{task_id}/items/{item_id}/cancel-send",
    response_model=BatchTaskActionResponse,
)
async def cancel_batch_task_item_send(
    task_id: int,
    item_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
    item = _find_visible_batch_task_item(task, item_id)
    _validate_batch_item_send_action_context(task, item)
    if item.batch_send_canceled_at is not None:
        return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))

    previous_status = item.status
    scheduled_at = item.scheduled_at
    now = utc_now()
    cancel_result = await session.execute(
        update(EmailTask)
        .where(
            EmailTask.id == item_id,
            EmailTask.batch_task_id == task_id,
            EmailTask.source == EmailTaskSource.BATCH.value,
            EmailTask.batch_send_canceled_at.is_(None),
            EmailTask.status.in_(BATCH_TASK_ITEM_SEND_CANCELLABLE_STATUSES),
        )
        .values(
            batch_send_canceled_at=now,
            updated_at=now,
        )
        .execution_options(synchronize_session=False),
    )
    if cancel_result.rowcount != 1:
        await session.rollback()
        current_item = await session.scalar(
            select(EmailTask).where(
                EmailTask.id == item_id,
                EmailTask.batch_task_id == task_id,
            ),
        )
        if current_item is None or _is_user_removed_batch_item(current_item):
            raise HTTPException(status_code=404, detail="未找到批量任务项")
        if current_item.batch_send_canceled_at is not None:
            task = await _get_batch_task(session, task_id)
            return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))
        if current_item.status in {
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            raise HTTPException(
                status_code=400, detail="邮件已进入发送流程，不能取消发送"
            )
        raise HTTPException(status_code=400, detail="当前邮件状态不能取消发送")

    await session.execute(
        update(BatchTask)
        .where(BatchTask.id == task_id)
        .values(updated_at=now)
        .execution_options(synchronize_session=False),
    )
    task = await _load_batch_task_for_serialization(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="未找到批量任务")
    sync_batch_task_completion(task, now=now)
    await _record_batch_task_action(
        session,
        task,
        "batch_task.item_send_canceled",
        extra_metadata={
            "email_task_id": item_id,
            "previous_status": previous_status,
            "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
        },
    )
    await session.commit()
    await session.refresh(task, attribute_names=["email_tasks"])
    return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))


@router.post(
    "/{task_id}/items/{item_id}/restore-send",
    response_model=BatchTaskActionResponse,
)
async def restore_batch_task_item_send(
    task_id: int,
    item_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
    item = _find_visible_batch_task_item(task, item_id)
    _validate_batch_item_send_action_context(task, item)
    if item.batch_send_canceled_at is None:
        return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))

    now = utc_now()
    if item.scheduled_at is None or as_utc_aware(item.scheduled_at) <= now:
        raise HTTPException(status_code=400, detail="原定发送时间已过，无法恢复发送")

    restore_result = await session.execute(
        update(EmailTask)
        .where(
            EmailTask.id == item_id,
            EmailTask.batch_task_id == task_id,
            EmailTask.source == EmailTaskSource.BATCH.value,
            EmailTask.batch_send_canceled_at.is_not(None),
            EmailTask.status.in_(BATCH_TASK_ITEM_SEND_CANCELLABLE_STATUSES),
            EmailTask.scheduled_at > now,
        )
        .values(
            batch_send_canceled_at=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False),
    )
    if restore_result.rowcount != 1:
        await session.rollback()
        current_item = await session.scalar(
            select(EmailTask).where(
                EmailTask.id == item_id,
                EmailTask.batch_task_id == task_id,
            ),
        )
        if current_item is None or _is_user_removed_batch_item(current_item):
            raise HTTPException(status_code=404, detail="未找到批量任务项")
        if current_item.batch_send_canceled_at is None:
            task = await _get_batch_task(session, task_id)
            return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))
        if (
            current_item.scheduled_at is None
            or as_utc_aware(current_item.scheduled_at) <= now
        ):
            raise HTTPException(
                status_code=400, detail="原定发送时间已过，无法恢复发送"
            )
        raise HTTPException(status_code=400, detail="当前邮件状态不能恢复发送")

    await session.execute(
        update(BatchTask)
        .where(BatchTask.id == task_id)
        .values(updated_at=now)
        .execution_options(synchronize_session=False),
    )
    task = await _load_batch_task_for_serialization(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="未找到批量任务")
    await _record_batch_task_action(
        session,
        task,
        "batch_task.item_send_restored",
        extra_metadata={
            "email_task_id": item_id,
            "item_status": item.status,
            "scheduled_at": item.scheduled_at.isoformat()
            if item.scheduled_at
            else None,
        },
    )
    await session.commit()
    await session.refresh(task, attribute_names=["email_tasks"])
    return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))


@router.post(
    "/{task_id}/items/{item_id}/delete", response_model=BatchTaskActionResponse
)
async def delete_batch_task_item(
    task_id: int,
    item_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
    item = next(
        (email_task for email_task in task.email_tasks if email_task.id == item_id),
        None,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="未找到批量任务项")
    previous_status = item.status
    previous_cancellation_reason = item.cancellation_reason
    now = utc_now()

    delete_result = await session.execute(
        update(EmailTask)
        .where(
            EmailTask.id == item_id,
            EmailTask.batch_task_id == task_id,
            EmailTask.status.in_(BATCH_TASK_ITEM_REMOVABLE_STATUSES),
        )
        .values(
            status=EmailTaskStatus.CANCELED.value,
            cancellation_reason=EmailTaskCancellationReason.USER_REMOVED.value,
            scheduled_at=None,
            draft_generation_previous_status=None,
            draft_generation_started_at=None,
            draft_claim_id=None,
            draft_claimed_at=None,
            draft_lease_expires_at=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False),
    )
    if delete_result.rowcount != 1:
        await session.rollback()
        current_item = await session.scalar(
            select(EmailTask).where(
                EmailTask.id == item_id,
                EmailTask.batch_task_id == task_id,
            ),
        )
        if current_item is None or _is_user_removed_batch_item(current_item):
            raise HTTPException(status_code=404, detail="未找到批量任务项")
        if current_item.status in {
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.SEND_FAILED.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "BATCH_TASK_ITEM_REMOVE_BLOCKED",
                    "message": (
                        f"批量任务 #{task_id} 的邮件任务 #{item_id} 当前状态为 "
                        f"{current_item.status}，已进入发送流程或已有发送结果，"
                        "不能从任务中移除。请在任务详情中查看该邮件。"
                    ),
                    "details": {
                        "batch_task_id": task_id,
                        "email_task_id": item_id,
                        "status": current_item.status,
                        "surface": "任务中心 > 批量任务详情",
                    },
                },
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "BATCH_TASK_ITEM_REMOVE_BLOCKED",
                "message": (
                    f"批量任务 #{task_id} 的邮件任务 #{item_id} 状态已变为 "
                    f"{current_item.status}，暂时无法移除。请刷新任务详情后重试。"
                ),
                "details": {
                    "batch_task_id": task_id,
                    "email_task_id": item_id,
                    "status": current_item.status,
                    "surface": "任务中心 > 批量任务详情",
                },
            },
        )

    await session.execute(
        update(BatchTask)
        .where(BatchTask.id == task_id)
        .values(
            target_count=case(
                (BatchTask.target_count > 0, BatchTask.target_count - 1),
                else_=0,
            ),
            status=case(
                (BatchTask.target_count <= 1, BatchTaskStatus.COMPLETED.value),
                else_=BatchTask.status,
            ),
            updated_at=now,
        )
        .execution_options(synchronize_session=False),
    )
    task = await _load_batch_task_for_serialization(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="未找到批量任务")
    await _record_batch_task_action(
        session,
        task,
        "batch_task.item_deleted",
        extra_metadata={
            "email_task_id": item_id,
            "previous_status": previous_status,
            "previous_cancellation_reason": previous_cancellation_reason,
            "target_count": task.target_count,
        },
    )
    sync_batch_task_completion(task)
    await session.commit()
    await session.refresh(task, attribute_names=["email_tasks"])
    return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))


@router.post(
    "/{task_id}/items/{item_id}/retry-draft", response_model=BatchTaskActionResponse
)
async def retry_batch_task_item_draft(
    task_id: int,
    item_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
    if task.status != BatchTaskStatus.RUNNING.value:
        raise HTTPException(status_code=400, detail="批量任务未运行，不能重新生成草稿")
    item = next(
        (email_task for email_task in task.email_tasks if email_task.id == item_id),
        None,
    )
    if item is None or _is_user_removed_batch_item(item):
        raise HTTPException(status_code=404, detail="未找到批量任务项")
    if item.batch_send_canceled_at is not None:
        raise HTTPException(status_code=400, detail="该导师已取消发送，请先恢复发送")
    if item.status != EmailTaskStatus.DRAFT_FAILED.value:
        raise HTTPException(
            status_code=400, detail="只有草稿生成失败的任务项可以重新生成"
        )

    generation_mode = normalize_batch_item_generation_mode(item)
    if generation_mode == OUTREACH_GENERATION_MODE_TEMPLATE:
        raise HTTPException(
            status_code=400, detail="模板模式草稿失败不能加入 AI 生成队列"
        )
    if batch_item_uses_llm_generation(item):
        await session.refresh(item, attribute_names=["professor", "primary_material"])
        if item.primary_material is None:
            raise HTTPException(
                status_code=400, detail="请选择 AI 写信参考材料后再重新生成草稿"
            )
        if not (item.professor.research_direction or "").strip():
            raise HTTPException(
                status_code=400, detail="请先补充导师研究方向，再使用 AI 生成草稿"
            )

    item.outreach_generation_mode = generation_mode
    item.status = EmailTaskStatus.DISCOVERED.value
    item.last_error = None
    item.draft_generation_previous_status = None
    item.draft_generation_started_at = None
    item.draft_claim_id = None
    item.draft_claimed_at = None
    item.draft_lease_expires_at = None
    item.updated_at = utc_now()
    await _record_batch_task_action(
        session,
        task,
        "batch_task.item_draft_retry_requested",
        extra_metadata={"email_task_id": item_id},
    )
    await session.commit()
    task = await _get_batch_task(session, task_id)
    return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))


BATCH_TASK_ITEM_REMOVABLE_STATUSES = {
    EmailTaskStatus.DISCOVERED.value,
    EmailTaskStatus.MATCHED.value,
    EmailTaskStatus.GENERATING_DRAFT.value,
    EmailTaskStatus.DRAFT_FAILED.value,
    EmailTaskStatus.REVIEW_REQUIRED.value,
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
    EmailTaskStatus.SCHEDULE_MISSED.value,
    EmailTaskStatus.CANCELED.value,
}


def _find_visible_batch_task_item(task: BatchTask, item_id: int) -> EmailTask:
    item = next(
        (
            email_task
            for email_task in task.email_tasks
            if email_task.id == item_id and not _is_user_removed_batch_item(email_task)
        ),
        None,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="未找到批量任务项")
    return item


def _validate_batch_item_send_action_context(task: BatchTask, item: EmailTask) -> None:
    if not _batch_task_allows_item_send_actions(task):
        raise HTTPException(
            status_code=400, detail="当前批量任务状态不支持修改导师发送计划"
        )
    if item.scheduled_at is None:
        raise HTTPException(
            status_code=400, detail="该导师缺少原定发送时间，不能修改发送计划"
        )


async def _get_batch_task(session: AsyncSession, task_id: int) -> BatchTask:
    task = await _load_batch_task_for_serialization(session, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="未找到批量任务")
    return task


async def _get_batch_task_item(
    session: AsyncSession,
    task_id: int,
    item_id: int,
) -> EmailTask:
    item = await session.scalar(
        select(EmailTask).where(
            EmailTask.id == item_id,
            EmailTask.batch_task_id == task_id,
            EmailTask.source == EmailTaskSource.BATCH.value,
        ),
    )
    if item is None or _is_user_removed_batch_item(item):
        raise HTTPException(status_code=404, detail="未找到批量任务项")
    if item.batch_send_canceled_at is not None:
        raise HTTPException(status_code=400, detail="该导师已取消发送，请先恢复发送")
    return item


async def _run_batch_task_item_workspace_action(
    session: AsyncSession,
    task_id: int,
    item_id: int,
    action,
) -> None:
    await _get_batch_task_item(session, task_id, item_id)
    try:
        await action()
    except llm_runtime.LLMRuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "不存在" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    session.expire_all()
    await _get_batch_task_item(session, task_id, item_id)


# Per-item draft/approval body snapshots can be tens of KB each; card metrics
# and item lifecycle actions never read them, so skip them for card payloads.


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


@router.post("", response_model=BatchTaskCardRead, status_code=status.HTTP_201_CREATED)
async def create_batch_task(
    payload: CreateBatchTaskRequest,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskCardRead:
    try:
        batch_task = await create_batch_task_record(payload, session)
    except BatchTaskCreationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await session.commit()
    refreshed_batch_task = await _load_batch_task_for_serialization(
        session, batch_task.id
    )
    if refreshed_batch_task is not None and sync_batch_task_completion(
        refreshed_batch_task
    ):
        await session.commit()
    return _serialize_batch_task(refreshed_batch_task)
