from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, time

from app.core.time import as_utc_aware, local_now, utc_now

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, selectinload

from app.core.database import get_async_session, get_session_factory
from app.core.query_chunks import chunked_values, unique_positive_ids
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
from app.modules.campaigns.public import (
    BatchTaskActionResponse,
    BatchTaskBulkApproveDraftsRequest,
    BatchTaskBulkApproveDraftsResponse,
    BatchTaskCardRead,
    BatchTaskItemRead,
    BatchTaskResendContextRead,
    CreateBatchTaskRequest,
)
from app.modules.campaigns.public import (
    build_jittered_batch_schedule,
    has_future_batch_window,
    normalize_scheduled_dates,
)
from app.modules.campaigns.public import (
    DRAFT_GENERATION_SOURCE_TEMPLATE,
    build_initial_batch_draft,
)
from app.modules.campaigns.public import (
    batch_item_is_ready_for_llm_generation,
    batch_item_uses_llm_generation,
    batch_item_uses_llm_generation_column,
    normalize_batch_item_generation_mode,
    resolve_batch_task_item_next_action,
)
from app.modules.campaigns.public import (
    BatchTaskResendContextError,
    build_batch_task_resend_context,
    classify_resend_content,
    decide_resend_item,
    normalize_resend_body,
    reused_content_requires_review,
)
from app.modules.campaigns.public import (
    count_completed_batch_task_items,
    sync_batch_task_completion,
)
from app.modules.campaigns.status import email_task_is_not_user_removed_expression
from app.modules.identities.public import material_can_be_primary
from app.services.match_results import load_resolved_match_results
from app.services.operation_logs import record_operation_log
from app.modules.campaigns.public import (
    get_default_outreach_template_for_identity,
    get_outreach_template,
)
from app.modules.campaigns.public import (
    OUTREACH_GENERATION_MODE_TEMPLATE,
    get_outreach_template_defaults_validation_error,
    resolve_outreach_template_config,
)
from app.modules.communications.public import explain_smtp_error
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
import app.modules.llm.public as llm_runtime

from .review import apply_batch_review_outreach_template


router = APIRouter(prefix="/api/batch-tasks", tags=["batch-tasks"])


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


@router.get("", response_model=list[BatchTaskCardRead])
async def list_batch_tasks(
    identity_id: int | None = None,
    llm_profile_id: int | None = None,
    view: str = "current",
    session: AsyncSession = Depends(get_async_session),
) -> list[BatchTaskCardRead]:
    statement = (
        select(BatchTask)
        .order_by(BatchTask.created_at.desc())
    )
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


@router.post("", response_model=BatchTaskCardRead, status_code=status.HTTP_201_CREATED)
async def create_batch_task(
    payload: CreateBatchTaskRequest,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskCardRead:
    if not payload.professor_ids:
        raise HTTPException(status_code=400, detail="请至少选择一位导师")
    if (
        payload.resend_content_strategy is not None
        and payload.resend_source_batch_task_id is None
    ):
        raise HTTPException(status_code=400, detail="重发内容策略只能用于重新发起任务")

    resend_content_strategy = (
        payload.resend_content_strategy or "reuse"
        if payload.resend_source_batch_task_id is not None
        else None
    )

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
            local_now(),
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

    requested_professor_ids = unique_positive_ids(payload.professor_ids)
    professors: list[Professor] = []
    for professor_id_chunk in chunked_values(requested_professor_ids):
        professors.extend(
            await session.scalars(
                select(Professor).where(
                    Professor.id.in_(professor_id_chunk),
                    Professor.archived_at.is_(None),
                ),
            ),
        )
    professors.sort(key=lambda professor: professor.id)
    if len(professors) != len(set(payload.professor_ids)):
        raise HTTPException(status_code=404, detail="部分导师不存在或已被移入回收站")

    resend_source_items: dict[int, EmailTask] = {}
    if payload.resend_source_batch_task_id is not None:
        source_batch_task = await session.scalar(
            select(BatchTask)
            .options(
                selectinload(BatchTask.email_tasks).selectinload(EmailTask.professor),
            )
            .where(BatchTask.id == payload.resend_source_batch_task_id),
        )
        if source_batch_task is None:
            raise HTTPException(status_code=404, detail="未找到原批量任务")
        if source_batch_task.identity_id != payload.identity_id:
            raise HTTPException(status_code=400, detail="重新发起任务必须沿用原任务身份")
        if source_batch_task.status not in {
            BatchTaskStatus.STOPPED.value,
            BatchTaskStatus.COMPLETED.value,
            BatchTaskStatus.EXPIRED.value,
        }:
            raise HTTPException(status_code=400, detail="原批量任务尚未结束，不能重新发起")

        requested_professor_ids = set(payload.professor_ids)
        for source_item in source_batch_task.email_tasks:
            if source_item.professor_id not in requested_professor_ids:
                continue
            decision = decide_resend_item(source_item)
            if not decision.selectable:
                raise HTTPException(
                    status_code=400,
                    detail=f"{source_item.professor.name}不属于可重新发起项：{decision.reason_label}",
                )
            resend_source_items[source_item.professor_id] = source_item
        if set(resend_source_items) != requested_professor_ids:
            raise HTTPException(status_code=400, detail="部分导师不属于原任务的可重新发起项")
    resend_requires_generation = (
        not resend_source_items
        or resend_content_strategy in {"template", "llm"}
        or any(
            classify_resend_content(item) == "regenerate"
            for item in resend_source_items.values()
        )
    )

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
                    now=local_now(),
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    material_map = {material.id: material for material in identity.materials}
    primary_material_id = payload.primary_material_id or identity.current_primary_material_id
    if primary_material_id is not None:
        primary_material = material_map.get(primary_material_id)
        if primary_material is None:
            raise HTTPException(status_code=400, detail="AI 写信参考材料不属于当前身份")
        if not material_can_be_primary(primary_material):
            raise HTTPException(status_code=400, detail="当前材料不支持作为 AI 写信参考材料")

    selected_material_ids = payload.selected_material_ids or None
    if selected_material_ids:
        if len(set(selected_material_ids)) != len(set(material_map) & set(selected_material_ids)):
            raise HTTPException(status_code=400, detail="存在不属于当前身份的随信材料")

    selected_template = None
    if payload.outreach_template_id is not None:
        allow_archived_provenance = not resend_requires_generation or bool(
            {
                "outreach_template_subject",
                "outreach_template_body_text",
            }.issubset(payload.model_fields_set)
            and _normalize_nullable_text(payload.outreach_template_subject)
            and _normalize_nullable_text(payload.outreach_template_body_text)
        )
        try:
            selected_template = await get_outreach_template(
                session,
                payload.outreach_template_id,
                include_archived=allow_archived_provenance,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif "outreach_template_id" not in payload.model_fields_set:
        selected_template = await get_default_outreach_template_for_identity(
            session,
            identity,
        )
    outreach_template_name_snapshot = (
        selected_template.name if selected_template is not None else None
    )

    requested_subject = _normalize_nullable_text(payload.outreach_template_subject) or _normalize_nullable_text(
        payload.email_subject,
    )
    requested_body_text = _normalize_nullable_text(
        payload.outreach_template_body_text,
    ) or _normalize_nullable_text(payload.email_body)
    requested_generation_mode = (
        str(
            payload.outreach_generation_mode
            or (
                selected_template.recommended_generation_mode
                if selected_template is not None
                else None
            )
            or identity.outreach_generation_mode
            or "llm"
        ).strip().lower()
    )
    if resend_content_strategy in {"template", "llm"}:
        requested_generation_mode = resend_content_strategy
    outreach_config = resolve_outreach_template_config(
        identity,
        template=selected_template,
        generation_mode=requested_generation_mode,
        subject_template=requested_subject,
        body_text_template=requested_body_text,
        body_html_template=payload.outreach_template_body_html,
    )
    detail = get_outreach_template_defaults_validation_error(
        outreach_config.subject_template,
        outreach_config.body_text_template,
    )
    if detail and resend_requires_generation:
        raise HTTPException(status_code=400, detail=detail)
    if (
        resend_content_strategy == "llm"
        and primary_material_id is None
    ):
        raise HTTPException(status_code=400, detail="AI 写信参考材料为必选项")

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
        outreach_template_id=(
            selected_template.id if selected_template is not None else None
        ),
        outreach_template_name_snapshot=outreach_template_name_snapshot,
        outreach_template_snapshot_version=1,
        outreach_generation_mode=outreach_config.generation_mode,
        outreach_template_subject=_normalize_nullable_text(
            outreach_config.subject_template,
        ),
        outreach_template_body_text=_normalize_nullable_text(
            outreach_config.body_text_template,
        ),
        outreach_template_body_html=_normalize_nullable_text(
            outreach_config.body_html_template,
        ),
        email_subject=_normalize_nullable_text(outreach_config.subject_template),
        email_body=_normalize_nullable_text(outreach_config.body_text_template),
        selected_material_ids=selected_material_ids,
        target_count=len(professors),
    )
    session.add(batch_task)
    await session.flush()

    for index, professor in enumerate(professors):
        generated_subject = None
        generated_body_text = None
        generated_body_html = None
        draft_generation_source = None
        draft_fallback_reason = None
        task_status = EmailTaskStatus.DISCOVERED.value
        approved_at = None
        approved_subject = None
        approved_body_text = None
        approved_body_html = None
        item_primary_material_id = primary_material_id
        item_selected_material_ids = selected_material_ids
        item_outreach_template_id = selected_template.id if selected_template is not None else None
        item_outreach_generation_mode = outreach_config.generation_mode
        item_outreach_template_subject = _normalize_nullable_text(outreach_config.subject_template)
        item_outreach_template_body_text = _normalize_nullable_text(outreach_config.body_text_template)
        item_outreach_template_body_html = _normalize_nullable_text(outreach_config.body_html_template)
        source_item = resend_source_items.get(professor.id)
        reuse_kind = classify_resend_content(source_item) if source_item is not None else "regenerate"

        if (
            resend_content_strategy == "reuse"
            and source_item is not None
            and reuse_kind != "regenerate"
        ):
            if source_item.outreach_template_snapshot_version is not None:
                item_outreach_template_id = source_item.outreach_template_id
                item_outreach_generation_mode = source_item.outreach_generation_mode
                item_outreach_template_subject = source_item.outreach_template_subject
                item_outreach_template_body_text = source_item.outreach_template_body_text
                item_outreach_template_body_html = source_item.outreach_template_body_html

            if reuse_kind == "rewrite_source":
                generated_subject = source_item.draft_rewrite_source_subject
                generated_body_text = source_item.draft_rewrite_source_body_text
                generated_body_html = source_item.draft_rewrite_source_body_html
                generated_body_text, generated_body_html = normalize_resend_body(
                    generated_body_text,
                    generated_body_html,
                )
                draft_generation_source = source_item.draft_generation_source
                task_status = EmailTaskStatus.REVIEW_REQUIRED.value
            else:
                generated_subject = source_item.generated_subject
                generated_body_text = source_item.generated_content_text
                generated_body_html = source_item.generated_content_html
                generated_body_text, generated_body_html = normalize_resend_body(
                    generated_body_text,
                    generated_body_html,
                )
                draft_generation_source = source_item.draft_generation_source
                draft_fallback_reason = source_item.draft_fallback_reason
                if reuse_kind == "approved":
                    approved_subject = source_item.approved_subject
                    approved_body_text = source_item.approved_body_text
                    approved_body_html = source_item.approved_body_html
                    approved_body_text, approved_body_html = normalize_resend_body(
                        approved_body_text,
                        approved_body_html,
                    )
                    approved_at = utc_now()
                    if reused_content_requires_review(source_item):
                        generated_subject = generated_subject or approved_subject
                        generated_body_text = generated_body_text or approved_body_text
                        generated_body_html = generated_body_html or approved_body_html
                        task_status = EmailTaskStatus.REVIEW_REQUIRED.value
                    else:
                        task_status = (
                            EmailTaskStatus.SCHEDULED.value
                            if payload.schedule_type == "scheduled"
                            else EmailTaskStatus.APPROVED.value
                        )
                else:
                    task_status = EmailTaskStatus.REVIEW_REQUIRED.value
        else:
            try:
                initial_draft = build_initial_batch_draft(
                    identity,
                    professor,
                    outreach_config,
                    primary_material_available=primary_material_id is not None,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if initial_draft is not None:
                generated_subject = initial_draft.subject
                generated_body_text = initial_draft.body_text
                generated_body_html = initial_draft.body_html
                draft_generation_source = initial_draft.generation_source
                draft_fallback_reason = initial_draft.fallback_reason
                if initial_draft.generation_source == DRAFT_GENERATION_SOURCE_TEMPLATE:
                    task_status = EmailTaskStatus.APPROVED.value
                    approved_subject = generated_subject
                    approved_body_text = generated_body_text
                    approved_body_html = generated_body_html
                    approved_at = utc_now()
                else:
                    task_status = EmailTaskStatus.REVIEW_REQUIRED.value

        email_task = EmailTask(
            source=EmailTaskSource.BATCH.value,
            batch_task_id=batch_task.id,
            identity_id=payload.identity_id,
            llm_profile_id=payload.llm_profile_id,
            professor_id=professor.id,
            primary_material_id=item_primary_material_id,
            outreach_template_id=item_outreach_template_id,
            outreach_template_snapshot_version=1,
            outreach_generation_mode=item_outreach_generation_mode,
            outreach_template_subject=item_outreach_template_subject,
            outreach_template_body_text=item_outreach_template_body_text,
            outreach_template_body_html=item_outreach_template_body_html,
            status=task_status,
            generated_subject=generated_subject,
            generated_content_text=generated_body_text,
            generated_content_html=generated_body_html,
            draft_generation_source=draft_generation_source,
            draft_fallback_reason=draft_fallback_reason,
            approved_subject=approved_subject,
            approved_body_text=approved_body_text,
            approved_body_html=approved_body_html,
            approved_at=approved_at,
            match_source_identity_id=source_item.match_source_identity_id if source_item else None,
            match_score=source_item.match_score if source_item else None,
            match_reason=source_item.match_reason if source_item else None,
            fit_points=list(source_item.fit_points) if source_item and source_item.fit_points else None,
            risk_points=list(source_item.risk_points) if source_item and source_item.risk_points else None,
            match_keywords=list(source_item.match_keywords) if source_item and source_item.match_keywords else None,
            scheduled_at=scheduled_at_values[index],
            selected_material_ids=item_selected_material_ids,
        )
        session.add(email_task)

    await session.flush()
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
            "outreach_template_id": batch_task.outreach_template_id,
            "outreach_template_name_snapshot": batch_task.outreach_template_name_snapshot,
            "outreach_generation_mode": batch_task.outreach_generation_mode,
            "resend_source_batch_task_id": payload.resend_source_batch_task_id,
            "resend_content_strategy": resend_content_strategy,
            "reused_approved_count": sum(
                1
                for item in resend_source_items.values()
                if resend_content_strategy == "reuse"
                and classify_resend_content(item) == "approved"
            ),
            "reused_generated_count": sum(
                1
                for item in resend_source_items.values()
                if resend_content_strategy == "reuse"
                and classify_resend_content(item) in {"generated", "rewrite_source"}
            ),
            "regenerated_count": sum(
                1
                for item in resend_source_items.values()
                if resend_content_strategy != "reuse"
                or classify_resend_content(item) == "regenerate"
            ),
        },
    )
    await session.commit()
    refreshed_batch_task = await _load_batch_task_for_serialization(session, batch_task.id)
    if refreshed_batch_task is not None and sync_batch_task_completion(refreshed_batch_task):
        await session.commit()
    return _serialize_batch_task(refreshed_batch_task)


@router.get("/{task_id}/resend-context", response_model=BatchTaskResendContextRead)
async def get_batch_task_resend_context(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskResendContextRead:
    try:
        return await build_batch_task_resend_context(session, task_id)
    except BatchTaskResendContextError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
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
                            IdentityMaterial.identity_id == identity_id,
                            IdentityMaterial.id.in_(material_id_chunk),
                        ),
                    )
                ).all(),
            )
        material_sizes = {
            material_id: max(0, size_bytes)
            for material_id, size_bytes in rows
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
    return await build_workspace_thread_for_task(session, task_id=item_id)


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


@router.post("/{task_id}/items/{item_id}/regenerate-draft", response_model=WorkspaceThreadRead)
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
    return await build_workspace_thread_for_task(session, task_id=item_id)


@router.post("/{task_id}/items/{item_id}/rewrite-draft", response_model=WorkspaceThreadRead)
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
    return await build_workspace_thread_for_task(session, task_id=item_id)


@router.post("/{task_id}/items/{item_id}/outreach-config", response_model=WorkspaceThreadRead)
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
    return await build_workspace_thread_for_task(session, task_id=item_id)


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
    return await build_workspace_thread_for_task(session, task_id=item_id)


@router.post("/{task_id}/items/{item_id}/approve-and-send", response_model=WorkspaceThreadRead)
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
    return await build_workspace_thread_for_task(session, task_id=item_id)


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
            email_task.status = email_task.draft_generation_previous_status or EmailTaskStatus.DISCOVERED.value
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
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
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
            email_task.cancellation_reason = EmailTaskCancellationReason.BATCH_STOPPED.value
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
        now = utc_now()
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
        task.updated_at = utc_now()
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
            raise HTTPException(status_code=400, detail="邮件已进入发送流程，不能取消发送")
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
        if current_item.scheduled_at is None or as_utc_aware(current_item.scheduled_at) <= now:
            raise HTTPException(status_code=400, detail="原定发送时间已过，无法恢复发送")
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
            "scheduled_at": item.scheduled_at.isoformat() if item.scheduled_at else None,
        },
    )
    await session.commit()
    await session.refresh(task, attribute_names=["email_tasks"])
    return BatchTaskActionResponse(ok=True, task=_serialize_batch_task(task))


@router.post("/{task_id}/items/{item_id}/delete", response_model=BatchTaskActionResponse)
async def delete_batch_task_item(
    task_id: int,
    item_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
    item = next((email_task for email_task in task.email_tasks if email_task.id == item_id), None)
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
            EmailTask.batch_send_canceled_at.is_(None),
            EmailTask.status.in_(BATCH_TASK_ITEM_REMOVABLE_STATUSES),
        )
        .values(
            status=EmailTaskStatus.CANCELED.value,
            cancellation_reason=EmailTaskCancellationReason.USER_REMOVED.value,
            scheduled_at=None,
            draft_generation_previous_status=None,
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
        if current_item.batch_send_canceled_at is not None:
            raise HTTPException(status_code=400, detail="该导师已取消发送，请先恢复发送")
        if current_item.status in {
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            raise HTTPException(status_code=400, detail="已发送或正在发送的邮件不能从批量任务中移除")
        raise HTTPException(status_code=400, detail="已批准、已排程或正在处理的邮件不能从批量任务中移除")

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


@router.post("/{task_id}/items/{item_id}/retry-draft", response_model=BatchTaskActionResponse)
async def retry_batch_task_item_draft(
    task_id: int,
    item_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskActionResponse:
    task = await _get_batch_task(session, task_id)
    if task.status != BatchTaskStatus.RUNNING.value:
        raise HTTPException(status_code=400, detail="批量任务未运行，不能重新生成草稿")
    item = next((email_task for email_task in task.email_tasks if email_task.id == item_id), None)
    if item is None or _is_user_removed_batch_item(item):
        raise HTTPException(status_code=404, detail="未找到批量任务项")
    if item.batch_send_canceled_at is not None:
        raise HTTPException(status_code=400, detail="该导师已取消发送，请先恢复发送")
    if item.status != EmailTaskStatus.DRAFT_FAILED.value:
        raise HTTPException(status_code=400, detail="只有草稿生成失败的任务项可以重新生成")

    generation_mode = normalize_batch_item_generation_mode(item)
    if generation_mode == OUTREACH_GENERATION_MODE_TEMPLATE:
        raise HTTPException(status_code=400, detail="模板模式草稿失败不能加入 AI 生成队列")
    if batch_item_uses_llm_generation(item):
        await session.refresh(item, attribute_names=["professor", "primary_material"])
        if item.primary_material is None:
            raise HTTPException(status_code=400, detail="请选择 AI 写信参考材料后再重新生成草稿")
        if not (item.professor.research_direction or "").strip():
            raise HTTPException(status_code=400, detail="请先补充导师研究方向，再使用 AI 生成草稿")

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
    EmailTaskStatus.DRAFT_FAILED.value,
    EmailTaskStatus.REVIEW_REQUIRED.value,
}

BATCH_TASK_ITEM_SEND_CANCELLABLE_STATUSES = {
    EmailTaskStatus.DISCOVERED.value,
    EmailTaskStatus.MATCHED.value,
    EmailTaskStatus.GENERATING_DRAFT.value,
    EmailTaskStatus.DRAFT_FAILED.value,
    EmailTaskStatus.REVIEW_REQUIRED.value,
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
}

BATCH_TASK_ITEM_SEND_ACTION_BATCH_STATUSES = {
    BatchTaskStatus.RUNNING.value,
    BatchTaskStatus.PAUSED.value,
}


def _is_user_removed_batch_item(email_task: EmailTask) -> bool:
    return (
        email_task.status == EmailTaskStatus.CANCELED.value
        and email_task.cancellation_reason == EmailTaskCancellationReason.USER_REMOVED.value
    )


def _visible_batch_email_tasks(task: BatchTask) -> list[EmailTask]:
    return [email_task for email_task in task.email_tasks if not _is_user_removed_batch_item(email_task)]


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


def _batch_task_allows_item_send_actions(task: BatchTask) -> bool:
    return bool(
        task.deleted_at is None
        and task.schedule_type == "scheduled"
        and task.status in BATCH_TASK_ITEM_SEND_ACTION_BATCH_STATUSES
    )


def _validate_batch_item_send_action_context(task: BatchTask, item: EmailTask) -> None:
    if not _batch_task_allows_item_send_actions(task):
        raise HTTPException(status_code=400, detail="当前批量任务状态不支持修改导师发送计划")
    if item.scheduled_at is None:
        raise HTTPException(status_code=400, detail="该导师缺少原定发送时间，不能修改发送计划")


def _can_cancel_batch_task_item_send(email_task: EmailTask) -> bool:
    batch_task = email_task.batch_task
    return bool(
        batch_task is not None
        and _batch_task_allows_item_send_actions(batch_task)
        and email_task.scheduled_at is not None
        and email_task.batch_send_canceled_at is None
        and email_task.status in BATCH_TASK_ITEM_SEND_CANCELLABLE_STATUSES
    )


def _can_restore_batch_task_item_send(
    email_task: EmailTask,
    *,
    now: datetime,
) -> bool:
    batch_task = email_task.batch_task
    return bool(
        batch_task is not None
        and _batch_task_allows_item_send_actions(batch_task)
        and email_task.scheduled_at is not None
        and as_utc_aware(email_task.scheduled_at) > as_utc_aware(now)
        and email_task.batch_send_canceled_at is not None
        and email_task.status in BATCH_TASK_ITEM_SEND_CANCELLABLE_STATUSES
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


async def _load_batch_task_for_serialization(session: AsyncSession, task_id: int) -> BatchTask | None:
    return await session.scalar(
        select(BatchTask)
        .options(
            selectinload(BatchTask.email_tasks).selectinload(EmailTask.professor),
            selectinload(BatchTask.email_tasks).selectinload(
                EmailTask.primary_material
            ),
        )
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


async def _sanitize_batch_task_material_references_before_restore(session: AsyncSession, task: BatchTask) -> None:
    material_ids = set(task.selected_material_ids or [])
    if task.primary_material_id is not None:
        material_ids.add(task.primary_material_id)
    if not material_ids:
        return

    existing_material_ids: set[int] = set()
    for material_id_chunk in chunked_values(material_ids):
        existing_material_ids.update(
            await session.scalars(
                select(IdentityMaterial.id).where(
                    IdentityMaterial.identity_id == task.identity_id,
                    IdentityMaterial.id.in_(material_id_chunk),
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
        task.updated_at = utc_now()


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
                is_active & (EmailTask.status == EmailTaskStatus.GENERATING_DRAFT.value),
                "generating_draft_count",
            ),
            count_when(
                is_active & (EmailTask.status == EmailTaskStatus.DRAFT_FAILED.value),
                "draft_failed_count",
            ),
            count_when(
                is_active & (EmailTask.status == EmailTaskStatus.REVIEW_REQUIRED.value),
                "review_required_count",
            ),
            count_when(
                is_active & (EmailTask.status == EmailTaskStatus.APPROVED.value),
                "approved_count",
            ),
            count_when(
                is_active & (EmailTask.status == EmailTaskStatus.SCHEDULED.value),
                "scheduled_count",
            ),
            count_when(
                is_active & (EmailTask.status == EmailTaskStatus.SENT.value),
                "sent_count",
            ),
            count_when(
                is_active & (EmailTask.status == EmailTaskStatus.SEND_FAILED.value),
                "failed_count",
            ),
            count_when(
                is_active & (EmailTask.status == EmailTaskStatus.REPLY_DETECTED.value),
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
        generating_draft_count=status_counter.get(EmailTaskStatus.GENERATING_DRAFT.value, 0),
        draft_failed_count=status_counter.get(EmailTaskStatus.DRAFT_FAILED.value, 0),
        review_required_count=status_counter.get(EmailTaskStatus.REVIEW_REQUIRED.value, 0),
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
