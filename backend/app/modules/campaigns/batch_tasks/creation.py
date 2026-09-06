from __future__ import annotations

import re
from datetime import datetime, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.time import local_now, utc_now
from app.models import (
    BatchTask,
    BatchTaskStatus,
    EmailTask,
    EmailTaskSource,
    EmailTaskStatus,
    IdentityProfile,
    OutreachTemplate,
    Professor,
)
from app.modules.campaigns.create_inputs import (
    load_campaign_identity,
    load_campaign_professors,
)
from app.modules.campaigns.public import (
    DRAFT_GENERATION_SOURCE_TEMPLATE,
    CreateBatchTaskRequest,
    build_initial_batch_draft,
    build_jittered_batch_schedule,
    classify_resend_content,
    decide_resend_item,
    get_default_outreach_template_for_identity,
    get_outreach_template,
    get_outreach_template_defaults_validation_error,
    has_future_batch_window,
    normalize_resend_body,
    normalize_scheduled_dates,
    resolve_outreach_template_config,
    reused_content_requires_review,
)
from app.modules.campaigns.templates.rendering import OutreachTemplateConfig
from app.modules.identities.public import material_can_be_primary
from app.modules.llm.public import get_active_llm_profile
from app.services.material_catalog import list_global_material_metadata
from app.services.operation_logs import record_operation_log


class BatchTaskCreationError(ValueError):
    def __init__(self, *, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def create_batch_task_record(
    payload: CreateBatchTaskRequest, session: AsyncSession
) -> BatchTask:
    if not payload.professor_ids:
        raise BatchTaskCreationError(status_code=400, detail="请至少选择一位导师")
    if (
        payload.resend_content_strategy is not None
        and payload.resend_source_batch_task_id is None
    ):
        raise BatchTaskCreationError(
            status_code=400, detail="重发内容策略只能用于重新发起任务"
        )

    resend_content_strategy = (
        payload.resend_content_strategy or "reuse"
        if payload.resend_source_batch_task_id is not None
        else None
    )

    try:
        scheduled_dates = normalize_scheduled_dates(payload.scheduled_dates)
    except ValueError as exc:
        raise BatchTaskCreationError(status_code=400, detail=str(exc)) from exc
    if payload.schedule_type == "scheduled":
        if not scheduled_dates:
            raise BatchTaskCreationError(
                status_code=400, detail="请至少选择一个发送日期"
            )
        _validate_time_window(payload.window_start_time, payload.window_end_time)
        if not payload.emails_per_window or payload.emails_per_window <= 0:
            raise BatchTaskCreationError(status_code=400, detail="请输入每天发送数量")
        if not has_future_batch_window(
            local_now(),
            scheduled_dates=scheduled_dates,
            window_end_time=payload.window_end_time,
        ):
            raise BatchTaskCreationError(
                status_code=400,
                detail="当前定时发送窗口已全部过期，请重新选择发送日期或结束时间。",
            )

    identity = await load_campaign_identity(session, payload.identity_id)
    if not identity:
        raise BatchTaskCreationError(status_code=404, detail="未找到身份配置")

    llm_profile = await get_active_llm_profile(session, payload.llm_profile_id)
    if not llm_profile:
        raise BatchTaskCreationError(status_code=404, detail="模型配置不存在或已删除")

    professors = await load_campaign_professors(session, payload.professor_ids)
    if len(professors) != len(set(payload.professor_ids)):
        raise BatchTaskCreationError(
            status_code=404, detail="部分导师不存在或已被移入回收站"
        )

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
            raise BatchTaskCreationError(status_code=404, detail="未找到原批量任务")
        if source_batch_task.identity_id != payload.identity_id:
            raise BatchTaskCreationError(
                status_code=400, detail="重新发起任务必须沿用原任务身份"
            )
        if source_batch_task.status not in {
            BatchTaskStatus.STOPPED.value,
            BatchTaskStatus.COMPLETED.value,
            BatchTaskStatus.EXPIRED.value,
        }:
            raise BatchTaskCreationError(
                status_code=400, detail="原批量任务尚未结束，不能重新发起"
            )

        requested_professor_ids = set(payload.professor_ids)
        for source_item in source_batch_task.email_tasks:
            if source_item.professor_id not in requested_professor_ids:
                continue
            decision = decide_resend_item(source_item)
            if not decision.selectable:
                raise BatchTaskCreationError(
                    status_code=400,
                    detail=f"{source_item.professor.name}不属于可重新发起项：{decision.reason_label}",
                )
            resend_source_items[source_item.professor_id] = source_item
        if set(resend_source_items) != requested_professor_ids:
            raise BatchTaskCreationError(
                status_code=400, detail="部分导师不属于原任务的可重新发起项"
            )
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
            raise BatchTaskCreationError(status_code=400, detail=str(exc)) from exc

    material_map = {
        material.id: material
        for material in await list_global_material_metadata(session)
    }
    primary_material_id = (
        payload.primary_material_id or identity.current_primary_material_id
    )
    if primary_material_id is not None:
        primary_material = material_map.get(primary_material_id)
        if primary_material is None:
            raise BatchTaskCreationError(
                status_code=400, detail="未找到 AI 写信参考材料"
            )
        if not material_can_be_primary(primary_material):
            raise BatchTaskCreationError(
                status_code=400, detail="当前材料不支持作为 AI 写信参考材料"
            )

    selected_material_ids = payload.selected_material_ids or None
    if selected_material_ids:
        if len(set(selected_material_ids)) != len(
            set(material_map) & set(selected_material_ids)
        ):
            raise BatchTaskCreationError(
                status_code=400, detail="存在已删除或不存在的随信材料"
            )

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
            raise BatchTaskCreationError(status_code=400, detail=str(exc)) from exc
    elif "outreach_template_id" not in payload.model_fields_set:
        selected_template = await get_default_outreach_template_for_identity(
            session,
            identity,
        )
    outreach_template_name_snapshot = (
        selected_template.name if selected_template is not None else None
    )

    requested_subject = _normalize_nullable_text(
        payload.outreach_template_subject
    ) or _normalize_nullable_text(
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
        )
        .strip()
        .lower()
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
        raise BatchTaskCreationError(status_code=400, detail=detail)
    if resend_content_strategy == "llm" and primary_material_id is None:
        raise BatchTaskCreationError(status_code=400, detail="AI 写信参考材料为必选项")

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
        email_task = _build_batch_email_task(
            payload=payload,
            batch_task=batch_task,
            professor=professor,
            identity=identity,
            outreach_config=outreach_config,
            selected_template=selected_template,
            primary_material_id=primary_material_id,
            selected_material_ids=selected_material_ids,
            resend_content_strategy=resend_content_strategy,
            source_item=resend_source_items.get(professor.id),
            scheduled_at=scheduled_at_values[index],
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
    return batch_task


def _validate_time_window(start_time: str | None, end_time: str | None) -> None:
    if not start_time or not end_time:
        raise BatchTaskCreationError(status_code=400, detail="请填写发送时间窗口")
    if not re.fullmatch(r"\d{2}:\d{2}", start_time) or not re.fullmatch(
        r"\d{2}:\d{2}", end_time
    ):
        raise BatchTaskCreationError(
            status_code=400, detail="发送时间必须使用 HH:mm 格式"
        )
    try:
        start = time.fromisoformat(start_time)
        end = time.fromisoformat(end_time)
    except ValueError as exc:
        raise BatchTaskCreationError(
            status_code=400, detail="发送时间必须使用 HH:mm 格式"
        ) from exc
    if end <= start:
        raise BatchTaskCreationError(status_code=400, detail="结束时间必须晚于开始时间")


def _normalize_nullable_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _build_batch_email_task(
    *,
    payload: CreateBatchTaskRequest,
    batch_task: BatchTask,
    professor: Professor,
    identity: IdentityProfile,
    outreach_config: OutreachTemplateConfig,
    selected_template: OutreachTemplate | None,
    primary_material_id: int | None,
    selected_material_ids: list[int],
    resend_content_strategy: str | None,
    source_item: EmailTask | None,
    scheduled_at: datetime | None,
) -> EmailTask:
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
    item_outreach_template_id = (
        selected_template.id if selected_template is not None else None
    )
    item_outreach_generation_mode = outreach_config.generation_mode
    item_outreach_template_subject = _normalize_nullable_text(
        outreach_config.subject_template
    )
    item_outreach_template_body_text = _normalize_nullable_text(
        outreach_config.body_text_template
    )
    item_outreach_template_body_html = _normalize_nullable_text(
        outreach_config.body_html_template
    )
    reuse_kind = (
        classify_resend_content(source_item)
        if source_item is not None
        else "regenerate"
    )

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
            raise BatchTaskCreationError(status_code=400, detail=str(exc)) from exc
        if initial_draft is not None:
            generated_subject = initial_draft.subject
            generated_body_text = initial_draft.body_text
            generated_body_html = initial_draft.body_html
            draft_generation_source = initial_draft.generation_source
            draft_fallback_reason = initial_draft.fallback_reason
            if initial_draft.generation_source == DRAFT_GENERATION_SOURCE_TEMPLATE:
                task_status = (
                    EmailTaskStatus.SCHEDULED.value
                    if payload.schedule_type == "scheduled"
                    else EmailTaskStatus.APPROVED.value
                )
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
        match_source_identity_id=source_item.match_source_identity_id
        if source_item
        else None,
        match_score=source_item.match_score if source_item else None,
        match_reason=source_item.match_reason if source_item else None,
        fit_points=list(source_item.fit_points)
        if source_item and source_item.fit_points
        else None,
        risk_points=list(source_item.risk_points)
        if source_item and source_item.risk_points
        else None,
        match_keywords=list(source_item.match_keywords)
        if source_item and source_item.match_keywords
        else None,
        scheduled_at=scheduled_at,
        selected_material_ids=item_selected_material_ids,
    )
    return email_task
