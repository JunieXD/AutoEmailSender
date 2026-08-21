from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    BatchTask,
    EmailTask,
    EmailTaskCancellationReason,
    EmailTaskStatus,
    IdentityMaterial,
)
from app.modules.identities.public import material_can_be_primary
from app.services.rich_text import normalize_email_html
from app.services.material_catalog import list_global_materials

from .drafts.fallback import DRAFT_GENERATION_SOURCE_TEMPLATE
from .schemas import (
    BatchTaskResendContextRead,
    BatchTaskResendContextTaskRead,
    BatchTaskResendDefaultsRead,
    BatchTaskResendItemRead,
    BatchTaskResendSummaryRead,
)

SUCCESS_STATUSES = {EmailTaskStatus.SENT.value, EmailTaskStatus.REPLY_DETECTED.value}
EXCLUDED_RUNNING_STATUSES = {EmailTaskStatus.SENDING.value}

RESEND_CONTENT_APPROVED = "approved"
RESEND_CONTENT_GENERATED = "generated"
RESEND_CONTENT_REWRITE_SOURCE = "rewrite_source"
RESEND_CONTENT_REGENERATE = "regenerate"

REASON_LABELS: dict[tuple[str, str | None], str] = {
    (
        EmailTaskStatus.CANCELED.value,
        EmailTaskCancellationReason.SCHEDULE_EXPIRED.value,
    ): "发送窗口已过期",
    (
        EmailTaskStatus.CANCELED.value,
        EmailTaskCancellationReason.BATCH_STOPPED.value,
    ): "任务中止后未发送",
    (EmailTaskStatus.SEND_FAILED.value, None): "发送失败",
    (EmailTaskStatus.DRAFT_FAILED.value, None): "草稿生成失败",
    (EmailTaskStatus.REVIEW_REQUIRED.value, None): "待审核未发送",
    (EmailTaskStatus.APPROVED.value, None): "已批准未发送",
    (EmailTaskStatus.SCHEDULED.value, None): "已排程未发送",
    (EmailTaskStatus.DISCOVERED.value, None): "尚未完成发信准备",
    (EmailTaskStatus.MATCHED.value, None): "尚未完成发信准备",
    (EmailTaskStatus.GENERATING_DRAFT.value, None): "尚未完成发信准备",
}


@dataclass(frozen=True)
class ResendItemDecision:
    selectable: bool
    default_selected: bool
    reason_label: str
    unavailable_reason: str | None


class BatchTaskResendContextError(ValueError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def classify_resend_content(email_task: EmailTask) -> str:
    has_approved_snapshot = any(
        (value or "").strip()
        for value in (
            email_task.approved_subject,
            email_task.approved_body_text,
            email_task.approved_body_html,
        )
    )
    if has_approved_snapshot and _has_sendable_content(
        email_task.approved_subject or email_task.generated_subject,
        email_task.approved_body_text or email_task.generated_content_text,
        email_task.approved_body_html or email_task.generated_content_html,
    ):
        return RESEND_CONTENT_APPROVED
    if _has_sendable_content(
        email_task.generated_subject,
        email_task.generated_content_text,
        email_task.generated_content_html,
    ):
        return RESEND_CONTENT_GENERATED
    if _has_sendable_content(
        email_task.draft_rewrite_source_subject,
        email_task.draft_rewrite_source_body_text,
        email_task.draft_rewrite_source_body_html,
    ):
        return RESEND_CONTENT_REWRITE_SOURCE
    return RESEND_CONTENT_REGENERATE


def _has_sendable_content(
    subject: str | None,
    body_text: str | None,
    body_html: str | None,
) -> bool:
    normalized_body_text, _ = normalize_resend_body(body_text, body_html)
    return bool((subject or "").strip() and (normalized_body_text or "").strip())


def normalize_resend_body(
    body_text: str | None,
    body_html: str | None,
) -> tuple[str | None, str | None]:
    if (body_text or "").strip():
        return body_text, body_html
    if not (body_html or "").strip():
        return body_text, body_html
    try:
        rendered = normalize_email_html(body_html or "")
    except ValueError:
        return body_text, body_html
    return rendered.text, rendered.html


def reused_content_requires_review(email_task: EmailTask) -> bool:
    if classify_resend_content(email_task) != RESEND_CONTENT_APPROVED:
        return True
    if email_task.status in {
        EmailTaskStatus.APPROVED.value,
        EmailTaskStatus.SCHEDULED.value,
        EmailTaskStatus.SEND_FAILED.value,
    }:
        return False
    if (
        email_task.approved_at is not None
        and email_task.draft_generation_source == DRAFT_GENERATION_SOURCE_TEMPLATE
    ):
        return False
    return True


def decide_resend_item(email_task: EmailTask) -> ResendItemDecision:
    professor = email_task.professor
    if professor is None:
        return ResendItemDecision(
            False, False, "导师不存在", "导师已不存在，未带入新任务"
        )
    if professor.archived_at is not None:
        return ResendItemDecision(
            False, False, "导师已归档", "导师已归档，未带入新任务"
        )
    if email_task.batch_send_canceled_at is not None:
        return ResendItemDecision(
            False,
            False,
            "用户已取消发送",
            "已在原任务中主动取消发送，未带入新任务",
        )
    if email_task.status in SUCCESS_STATUSES:
        return ResendItemDecision(
            False, False, "已成功触达", "已成功触达，未带入新任务"
        )
    if (
        email_task.status == EmailTaskStatus.CANCELED.value
        and email_task.cancellation_reason
        == EmailTaskCancellationReason.USER_REMOVED.value
    ):
        return ResendItemDecision(
            False, False, "用户已移除", "已从原任务单独移除，未带入新任务"
        )
    if email_task.status in EXCLUDED_RUNNING_STATUSES:
        return ResendItemDecision(False, False, "发送中", "正在发送的邮件未带入新任务")
    reason_label = REASON_LABELS.get(
        (email_task.status, email_task.cancellation_reason),
        REASON_LABELS.get((email_task.status, None), "未成功触达"),
    )
    return ResendItemDecision(True, True, reason_label, None)


def filter_available_material_defaults(
    *,
    materials: list[IdentityMaterial],
    primary_material_id: int | None,
    selected_material_ids: list[int] | None,
) -> tuple[int | None, list[int], list[str]]:
    material_by_id = {material.id: material for material in materials}
    warnings: list[str] = []
    next_primary_id = None
    if primary_material_id is not None:
        material = material_by_id.get(primary_material_id)
        if material is not None and material_can_be_primary(material):
            next_primary_id = primary_material_id
        else:
            warnings.append("部分原材料已不存在或不再支持分析，未带入新任务")
    next_selected_ids = [
        material_id
        for material_id in (selected_material_ids or [])
        if material_id in material_by_id
    ]
    if selected_material_ids and len(next_selected_ids) != len(selected_material_ids):
        warnings.append("部分原随信附件已不存在，未带入新任务")
    return next_primary_id, next_selected_ids, list(dict.fromkeys(warnings))


async def build_batch_task_resend_context(
    session: AsyncSession,
    task_id: int,
) -> BatchTaskResendContextRead:
    task = await session.scalar(
        select(BatchTask)
        .options(
            selectinload(BatchTask.email_tasks).selectinload(EmailTask.professor),
            selectinload(BatchTask.email_tasks).selectinload(
                EmailTask.outreach_template
            ),
            selectinload(BatchTask.identity),
        )
        .where(BatchTask.id == task_id)
        .execution_options(populate_existing=True),
    )
    if task is None:
        raise BatchTaskResendContextError(404, "未找到批量任务")
    if task.identity is None:
        raise BatchTaskResendContextError(400, "原任务身份已不存在，无法直接重新发起。")

    primary_material_id, selected_material_ids, warnings = (
        filter_available_material_defaults(
            materials=await list_global_materials(session),
            primary_material_id=task.primary_material_id,
            selected_material_ids=task.selected_material_ids,
        )
    )
    sorted_email_tasks = sorted(
        task.email_tasks, key=lambda item: (item.created_at, item.id)
    )
    snapshot_task = sorted_email_tasks[0] if sorted_email_tasks else None
    has_batch_outreach_snapshot = task.outreach_template_snapshot_version is not None
    outreach_template_id = (
        task.outreach_template_id
        if has_batch_outreach_snapshot
        else snapshot_task.outreach_template_id
        if snapshot_task
        else None
    )
    outreach_template_name_snapshot = (
        task.outreach_template_name_snapshot
        if has_batch_outreach_snapshot
        else (
            snapshot_task.outreach_template.name
            if snapshot_task and snapshot_task.outreach_template
            else None
        )
    )
    outreach_generation_mode = (
        task.outreach_generation_mode
        if has_batch_outreach_snapshot
        else (
            snapshot_task.outreach_generation_mode
            if snapshot_task and snapshot_task.outreach_generation_mode
            else task.identity.outreach_generation_mode
        )
    )
    outreach_template_subject = (
        task.outreach_template_subject
        if has_batch_outreach_snapshot
        else (
            snapshot_task.outreach_template_subject
            if snapshot_task and snapshot_task.outreach_template_subject is not None
            else task.email_subject
        )
    )
    outreach_template_body_text = (
        task.outreach_template_body_text
        if has_batch_outreach_snapshot
        else (
            snapshot_task.outreach_template_body_text
            if snapshot_task and snapshot_task.outreach_template_body_text is not None
            else task.email_body
        )
    )
    outreach_template_body_html = (
        task.outreach_template_body_html
        if has_batch_outreach_snapshot
        else (
            snapshot_task.outreach_template_body_html
            if snapshot_task and snapshot_task.outreach_template_body_html is not None
            else None
        )
    )
    items: list[BatchTaskResendItemRead] = []
    for email_task in sorted_email_tasks:
        decision = decide_resend_item(email_task)
        professor = email_task.professor
        items.append(
            BatchTaskResendItemRead(
                email_task_id=email_task.id,
                professor_id=professor.id if professor else None,
                professor_name=professor.name if professor else "已删除导师",
                professor_email=professor.email if professor else None,
                status=email_task.status,
                cancellation_reason=email_task.cancellation_reason,
                reason_label=decision.reason_label,
                default_selected=decision.default_selected,
                selectable=decision.selectable,
                unavailable_reason=decision.unavailable_reason,
                content_reuse_kind=classify_resend_content(email_task),
                content_requires_review=reused_content_requires_review(email_task),
                updated_at=email_task.updated_at,
            ),
        )

    return BatchTaskResendContextRead(
        task=BatchTaskResendContextTaskRead(
            id=task.id,
            name=task.name,
            identity_id=task.identity_id,
            schedule_type=task.schedule_type,
        ),
        defaults=BatchTaskResendDefaultsRead(
            identity_id=task.identity_id,
            outreach_template_id=outreach_template_id,
            outreach_template_name_snapshot=outreach_template_name_snapshot,
            outreach_generation_mode=outreach_generation_mode,
            outreach_template_subject=outreach_template_subject,
            outreach_template_body_text=outreach_template_body_text,
            outreach_template_body_html=outreach_template_body_html,
            primary_material_id=primary_material_id,
            selected_material_ids=selected_material_ids,
        ),
        items=items,
        summary=BatchTaskResendSummaryRead(
            candidate_count=sum(1 for item in items if item.selectable),
            default_selected_count=sum(1 for item in items if item.default_selected),
            unavailable_count=sum(1 for item in items if not item.selectable),
        ),
        warnings=list(dict.fromkeys(warnings)),
    )
