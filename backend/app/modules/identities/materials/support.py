from __future__ import annotations

from pathlib import Path

from app.models import EmailTaskStatus, IdentityMaterial
from app.services.file_storage import (
    extract_text_from_document,
    material_supports_text_extraction,
)


MATERIAL_REFERENCE_BLOCKING_STATUSES = {
    EmailTaskStatus.GENERATING_DRAFT.value,
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
    EmailTaskStatus.SENDING.value,
}

MATERIAL_REFERENCE_DETACHABLE_STATUSES = {
    EmailTaskStatus.DISCOVERED.value,
    EmailTaskStatus.MATCHED.value,
    EmailTaskStatus.DRAFT_FAILED.value,
    EmailTaskStatus.REVIEW_REQUIRED.value,
    EmailTaskStatus.SENT.value,
    EmailTaskStatus.SEND_FAILED.value,
    EmailTaskStatus.REPLY_DETECTED.value,
    EmailTaskStatus.CANCELED.value,
}

MATERIAL_REFERENCE_RESET_DRAFT_STATUSES = {
    EmailTaskStatus.REVIEW_REQUIRED.value,
    EmailTaskStatus.SEND_FAILED.value,
}


def material_reference_fallback_status(task) -> str:
    if (
        task.match_score is not None
        or bool(task.match_reason)
        or bool(task.fit_points)
        or bool(task.risk_points)
        or bool(task.match_keywords)
    ):
        return EmailTaskStatus.MATCHED.value
    return EmailTaskStatus.DISCOVERED.value


def material_can_be_primary(material: IdentityMaterial) -> bool:
    return material_supports_text_extraction(material.original_filename)


def ensure_material_extracted_text(material: IdentityMaterial) -> str | None:
    if material.extracted_text:
        return material.extracted_text
    if not material_supports_text_extraction(material.original_filename):
        return None

    material.extracted_text = extract_text_from_document(material.file_path)
    if not material.extracted_text:
        raise ValueError("默认材料无法提取文本，请换用可复制文本的 PDF/DOCX/TXT/MD 材料后重试")
    return material.extracted_text


def build_material_download_name(material: IdentityMaterial) -> str:
    if material.original_filename:
        return material.original_filename

    suffix = Path(material.file_path).suffix
    base_name = material.display_name.strip() or "material"
    return f"{base_name}{suffix}"
