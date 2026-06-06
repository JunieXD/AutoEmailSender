from __future__ import annotations

from dataclasses import dataclass

from app.models import EmailTask, EmailTaskCancellationReason, EmailTaskStatus, IdentityMaterial
from app.services.materials import material_can_be_primary

SUCCESS_STATUSES = {EmailTaskStatus.SENT.value, EmailTaskStatus.REPLY_DETECTED.value}
EXCLUDED_RUNNING_STATUSES = {EmailTaskStatus.SENDING.value}

REASON_LABELS: dict[tuple[str, str | None], str] = {
    (EmailTaskStatus.CANCELED.value, EmailTaskCancellationReason.SCHEDULE_EXPIRED.value): "发送窗口已过期",
    (EmailTaskStatus.CANCELED.value, EmailTaskCancellationReason.BATCH_STOPPED.value): "任务中止后未发送",
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


def decide_resend_item(email_task: EmailTask) -> ResendItemDecision:
    professor = email_task.professor
    if professor is None:
        return ResendItemDecision(False, False, "导师不存在", "导师已不存在，未带入新任务")
    if professor.archived_at is not None:
        return ResendItemDecision(False, False, "导师已归档", "导师已归档，未带入新任务")
    if email_task.status in SUCCESS_STATUSES:
        return ResendItemDecision(False, False, "已成功触达", "已成功触达，未带入新任务")
    if (
        email_task.status == EmailTaskStatus.CANCELED.value
        and email_task.cancellation_reason == EmailTaskCancellationReason.USER_REMOVED.value
    ):
        return ResendItemDecision(False, False, "用户已移除", "已从原任务单独移除，未带入新任务")
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
    next_selected_ids = [material_id for material_id in (selected_material_ids or []) if material_id in material_by_id]
    if selected_material_ids and len(next_selected_ids) != len(selected_material_ids):
        warnings.append("部分原随信附件已不存在，未带入新任务")
    return next_primary_id, next_selected_ids, list(dict.fromkeys(warnings))