from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.identity_serializers import serialize_material
from app.core.database import get_async_session
from app.models import (
    BatchTask,
    BatchTaskStatus,
    EmailTask,
    EmailTaskStatus,
    IdentityMaterial,
    IdentityMaterialType,
    IdentityProfile,
)
from app.schemas.identity import IdentityMaterialRead
from app.services.file_storage import (
    build_display_name,
    delete_file,
    save_upload,
)
from app.services.materials import (
    MATERIAL_REFERENCE_BLOCKING_STATUSES,
    MATERIAL_REFERENCE_DETACHABLE_STATUSES,
    MATERIAL_REFERENCE_RESET_DRAFT_STATUSES,
    build_material_download_name,
    material_can_be_primary,
    material_reference_fallback_status,
)
from app.services.operation_logs import record_operation_log


router = APIRouter(prefix="/api", tags=["materials"])

NON_CONTINUABLE_BATCH_TASK_STATUSES = {
    BatchTaskStatus.STOPPED.value,
    BatchTaskStatus.COMPLETED.value,
    BatchTaskStatus.EXPIRED.value,
}


@router.post(
    "/identities/{identity_id}/materials",
    response_model=IdentityMaterialRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_identity_material(
    identity_id: int,
    file: UploadFile = File(...),
    material_type: str = Form(default=IdentityMaterialType.OTHER.value),
    display_name: str | None = Form(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> IdentityMaterialRead:
    identity = await _get_identity(session, identity_id)
    material_type_value = _normalize_material_type(material_type)

    stored_upload = save_upload(file, "identities", str(identity_id), "materials")
    material = IdentityMaterial(
        identity_id=identity_id,
        display_name=(display_name or build_display_name(stored_upload.original_name)).strip() or build_display_name(stored_upload.original_name),
        original_filename=stored_upload.original_name,
        file_path=stored_upload.file_path,
        mime_type=file.content_type,
        size_bytes=stored_upload.size_bytes,
        sha256=stored_upload.sha256,
        extracted_text=None,
        material_type=material_type_value,
    )
    session.add(material)
    await session.flush()

    if identity.current_primary_material_id is None and material_can_be_primary(material):
        identity.current_primary_material_id = material.id
        identity.updated_at = datetime.now(UTC)

    await _record_material_log(session, material, "identity_material.uploaded")
    await session.commit()
    await session.refresh(identity)
    await session.refresh(material)
    return serialize_material(material, identity.current_primary_material_id)


@router.post("/materials/{material_id}/set-primary", response_model=IdentityMaterialRead)
async def set_primary_material(
    material_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityMaterialRead:
    material = await _get_material(session, material_id)
    if not material_can_be_primary(material):
        raise HTTPException(status_code=400, detail="当前材料不支持作为默认材料")

    identity = material.identity
    identity.current_primary_material_id = material.id
    identity.updated_at = datetime.now(UTC)
    await _record_material_log(session, material, "identity_material.primary_set")
    await session.commit()
    await session.refresh(identity)
    return serialize_material(material, identity.current_primary_material_id)


@router.delete("/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material(
    material_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    material = await _get_material(session, material_id)
    identity = material.identity
    is_current_primary = identity.current_primary_material_id == material.id

    candidate_tasks = list(
        (
            await session.execute(
                select(EmailTask).where(
                    EmailTask.identity_id == material.identity_id,
                ),
            )
        ).scalars()
    )
    blocking_tasks = [
        task
        for task in candidate_tasks
        if task.status in MATERIAL_REFERENCE_BLOCKING_STATUSES and _task_references_material(task, material.id)
    ]
    if blocking_tasks:
        raise HTTPException(status_code=400, detail="当前材料仍被已批准、定时或发送中的任务使用")

    unknown_referencing_tasks = [
        task
        for task in candidate_tasks
        if task.status not in MATERIAL_REFERENCE_BLOCKING_STATUSES
        and task.status not in MATERIAL_REFERENCE_DETACHABLE_STATUSES
        and _task_references_material(task, material.id)
    ]
    if unknown_referencing_tasks:
        raise HTTPException(status_code=400, detail="当前材料仍被未完成任务使用")

    continuable_batch_tasks = list(
        (
            await session.execute(
                select(BatchTask).where(
                    BatchTask.identity_id == material.identity_id,
                    BatchTask.deleted_at.is_(None),
                    BatchTask.status.not_in(NON_CONTINUABLE_BATCH_TASK_STATUSES),
                ),
            )
        ).scalars()
    )
    for batch_task in continuable_batch_tasks:
        if _batch_task_references_material(batch_task, material.id):
            raise HTTPException(status_code=400, detail="当前材料仍被可继续批量任务使用")

    detached_primary_task_ids: list[int] = []
    removed_attachment_task_ids: list[int] = []
    reset_draft_task_ids: list[int] = []
    for task in candidate_tasks:
        if task.status not in MATERIAL_REFERENCE_DETACHABLE_STATUSES:
            continue
        detached_primary, removed_attachment, reset_draft = _detach_material_from_email_task(task, material.id)
        if detached_primary:
            detached_primary_task_ids.append(task.id)
        if removed_attachment:
            removed_attachment_task_ids.append(task.id)
        if reset_draft:
            reset_draft_task_ids.append(task.id)

    referencing_batch_tasks = list(
        (
            await session.execute(
                select(BatchTask).where(
                    BatchTask.identity_id == material.identity_id,
                    or_(
                        BatchTask.status.in_(NON_CONTINUABLE_BATCH_TASK_STATUSES),
                        BatchTask.deleted_at.is_not(None),
                    ),
                ),
            )
        ).scalars()
    )
    detached_batch_task_ids: list[int] = []
    for batch_task in referencing_batch_tasks:
        if _detach_material_from_batch_task(batch_task, material.id):
            detached_batch_task_ids.append(batch_task.id)

    if is_current_primary:
        identity.current_primary_material_id = None
        identity.updated_at = datetime.now(UTC)

    material_file_path = material.file_path
    await _record_material_log(
        session,
        material,
        "identity_material.deleted",
        metadata={
            "was_primary": is_current_primary,
            "detached_primary_task_ids": detached_primary_task_ids,
            "removed_attachment_task_ids": removed_attachment_task_ids,
            "reset_draft_task_ids": reset_draft_task_ids,
            "detached_batch_task_ids": detached_batch_task_ids,
        },
    )
    await session.delete(material)
    await session.commit()
    delete_file(material_file_path)


@router.get("/materials/{material_id}/open")
async def open_material(
    material_id: int,
    session: AsyncSession = Depends(get_async_session),
):
    material = await _get_material(session, material_id)
    file_path = Path(material.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="材料文件不存在")

    response = FileResponse(file_path, media_type=material.mime_type)
    response.headers["Content-Disposition"] = "inline"
    return response


@router.get("/materials/{material_id}/download")
async def download_material(
    material_id: int,
    session: AsyncSession = Depends(get_async_session),
):
    material = await _get_material(session, material_id)
    file_path = Path(material.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="材料文件不存在")

    return FileResponse(
        file_path,
        media_type=material.mime_type,
        filename=build_material_download_name(material),
    )


async def _get_identity(session: AsyncSession, identity_id: int) -> IdentityProfile:
    identity = await session.scalar(
        select(IdentityProfile)
        .options(
            selectinload(IdentityProfile.materials),
            selectinload(IdentityProfile.current_primary_material),
        )
        .where(IdentityProfile.id == identity_id),
    )
    if not identity:
        raise HTTPException(status_code=404, detail="未找到身份配置")
    return identity


async def _get_material(session: AsyncSession, material_id: int) -> IdentityMaterial:
    material = await session.scalar(
        select(IdentityMaterial)
        .options(selectinload(IdentityMaterial.identity))
        .where(IdentityMaterial.id == material_id),
    )
    if not material:
        raise HTTPException(status_code=404, detail="未找到材料")
    return material


def _task_references_material(task: EmailTask, material_id: int) -> bool:
    return task.primary_material_id == material_id or material_id in (task.selected_material_ids or [])


def _batch_task_references_material(task: BatchTask, material_id: int) -> bool:
    return task.primary_material_id == material_id or material_id in (task.selected_material_ids or [])


def _clear_generated_draft(task: EmailTask) -> None:
    task.generated_subject = None
    task.generated_content_text = None
    task.generated_content_html = None


def _clear_approved_draft(task: EmailTask) -> None:
    task.approved_subject = None
    task.approved_body_text = None
    task.approved_body_html = None
    task.approved_at = None
    task.scheduled_at = None


def _detach_material_from_email_task(task: EmailTask, material_id: int) -> tuple[bool, bool, bool]:
    detached_primary = False
    removed_attachment = False
    reset_draft = False

    if task.primary_material_id == material_id:
        task.primary_material_id = None
        detached_primary = True
        if task.status in MATERIAL_REFERENCE_RESET_DRAFT_STATUSES:
            _clear_generated_draft(task)
            _clear_approved_draft(task)
            task.status = material_reference_fallback_status(task)
            task.last_error = None
            reset_draft = True
        elif task.status == EmailTaskStatus.DRAFT_FAILED.value:
            task.status = material_reference_fallback_status(task)
            task.last_error = None

    if material_id in (task.selected_material_ids or []):
        task.selected_material_ids = [
            selected_material_id
            for selected_material_id in task.selected_material_ids or []
            if selected_material_id != material_id
        ]
        removed_attachment = True
        if not detached_primary and task.status in MATERIAL_REFERENCE_RESET_DRAFT_STATUSES:
            _clear_approved_draft(task)
            task.status = EmailTaskStatus.REVIEW_REQUIRED.value
            task.last_error = None
            reset_draft = True

    if detached_primary or removed_attachment or reset_draft:
        task.updated_at = datetime.now(UTC)

    return detached_primary, removed_attachment, reset_draft


def _detach_material_from_batch_task(task: BatchTask, material_id: int) -> bool:
    updated = False
    detached_primary = False
    if task.primary_material_id == material_id:
        task.primary_material_id = None
        detached_primary = True
        updated = True
    if material_id in (task.selected_material_ids or []):
        task.selected_material_ids = [
            selected_material_id
            for selected_material_id in task.selected_material_ids or []
            if selected_material_id != material_id
        ]
        updated = True
    if (
        detached_primary
        and task.deleted_at is not None
        and task.status not in NON_CONTINUABLE_BATCH_TASK_STATUSES
    ):
        task.status = BatchTaskStatus.STOPPED.value
        updated = True
    if updated:
        task.updated_at = datetime.now(UTC)
    return updated


def _normalize_material_type(material_type: str) -> str:
    normalized = material_type.strip().lower()
    if normalized not in {item.value for item in IdentityMaterialType}:
        raise HTTPException(status_code=400, detail="不支持的材料标签")
    return normalized


async def _record_material_log(
    session: AsyncSession,
    material: IdentityMaterial,
    event_name: str,
    *,
    metadata: dict[str, object] | None = None,
) -> None:
    base_metadata: dict[str, object] = {
        "identity_id": material.identity_id,
        "display_name": material.display_name,
        "original_filename": material.original_filename,
        "material_type": material.material_type,
        "mime_type": material.mime_type,
        "size_bytes": material.size_bytes,
    }
    if metadata:
        base_metadata.update(metadata)
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="identity_material",
        entity_id=str(material.id),
        metadata=base_metadata,
    )
