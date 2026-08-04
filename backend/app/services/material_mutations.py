from __future__ import annotations

from dataclasses import dataclass

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.time import serialize_api_datetime, utc_now
from app.models import (
    BatchTask,
    BatchTaskStatus,
    EmailTask,
    EmailTaskCancellationReason,
    EmailTaskStatus,
    IdentityMaterial,
    IdentityMaterialType,
    IdentityProfile,
    MatchAnalysisRun,
    TestComposeSession,
)
from app.services.batch_task_item_actions import (
    batch_item_uses_llm_generation_column,
    normalize_batch_item_generation_mode,
)
from app.services.batch_task_status import should_mark_batch_task_completed, sync_batch_task_completion
from app.services.file_storage import build_display_name, save_upload
from app.services.agent_mutations import fingerprint
from app.services.materials import (
    MATERIAL_REFERENCE_BLOCKING_STATUSES,
    MATERIAL_REFERENCE_DETACHABLE_STATUSES,
    MATERIAL_REFERENCE_RESET_DRAFT_STATUSES,
    material_can_be_primary,
    material_reference_fallback_status,
)
from app.services.operation_logs import record_operation_log


NON_CONTINUABLE_BATCH_TASK_STATUSES = {
    BatchTaskStatus.STOPPED.value,
    BatchTaskStatus.COMPLETED.value,
    BatchTaskStatus.EXPIRED.value,
}
IN_PROGRESS_MATERIAL_REFERENCE_STATUSES = {
    EmailTaskStatus.GENERATING_DRAFT.value,
    EmailTaskStatus.SENDING.value,
}
INACTIVE_BATCH_DETACHABLE_MATERIAL_REFERENCE_STATUSES = {
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
}


@dataclass(slots=True)
class MaterialMutationError(ValueError):
    status_code: int
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class MaterialDeletionResult:
    material_id: int
    identity_id: int
    display_name: str
    file_path: str | None
    was_primary: bool
    detached_primary_task_ids: list[int]
    removed_attachment_task_ids: list[int]
    reset_draft_task_ids: list[int]
    detached_test_compose_session_ids: list[int]
    detached_batch_task_ids: list[int]
    detached_match_analysis_run_count: int
    completed_batch_task_ids: list[int]

    def to_agent_result(self) -> dict[str, object]:
        return {
            "outcome": "deleted",
            "material_id": self.material_id,
            "material_name": self.display_name,
            "identity_id": self.identity_id,
            "was_primary": self.was_primary,
            "effects": {
                "detached_primary_task_ids": self.detached_primary_task_ids,
                "removed_attachment_task_ids": self.removed_attachment_task_ids,
                "reset_draft_task_ids": self.reset_draft_task_ids,
                "detached_test_compose_session_ids": self.detached_test_compose_session_ids,
                "detached_batch_task_ids": self.detached_batch_task_ids,
                "detached_match_analysis_run_count": self.detached_match_analysis_run_count,
                "completed_batch_task_ids": self.completed_batch_task_ids,
            },
        }


@dataclass(slots=True)
class _MaterialDeletionState:
    material: IdentityMaterial
    candidate_tasks: list[EmailTask]
    batch_tasks: list[BatchTask]
    test_compose_sessions: list[TestComposeSession]
    match_analysis_runs: list[MatchAnalysisRun]


async def upload_identity_material_record(
    session: AsyncSession,
    identity_id: int,
    file: UploadFile,
    material_type: str,
    display_name: str | None,
    *,
    event_name: str,
    actor: str,
) -> tuple[IdentityMaterial, int | None]:
    identity = await get_identity_for_materials_or_raise(session, identity_id)
    material_type_value = normalize_material_type(material_type)
    stored_upload = save_upload(file, "identities", str(identity_id), "materials")
    material = IdentityMaterial(
        identity_id=identity_id,
        identity=identity,
        display_name=(display_name or build_display_name(stored_upload.original_name)).strip()
        or build_display_name(stored_upload.original_name),
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
        identity.updated_at = utc_now()
        await _apply_primary_material_to_blocked_batch_tasks(session, material)

    await record_material_event(session, material, event_name, actor=actor)
    return material, identity.current_primary_material_id


async def set_primary_material_record(
    session: AsyncSession,
    material_id: int,
    *,
    event_name: str,
    actor: str,
) -> tuple[IdentityMaterial, int]:
    material = await get_material_with_identity_or_raise(session, material_id)
    if not material_can_be_primary(material):
        raise MaterialMutationError(
            400,
            "MATERIAL_NOT_PRIMARY_ELIGIBLE",
            "当前材料不支持作为默认材料",
        )

    identity = material.identity
    identity.current_primary_material_id = material.id
    identity.updated_at = utc_now()
    await _apply_primary_material_to_blocked_batch_tasks(session, material)
    await record_material_event(session, material, event_name, actor=actor)
    return material, material.id


async def get_identity_for_materials_or_raise(
    session: AsyncSession,
    identity_id: int,
) -> IdentityProfile:
    identity = await session.scalar(
        select(IdentityProfile)
        .options(
            selectinload(IdentityProfile.materials),
            selectinload(IdentityProfile.current_primary_material),
        )
        .where(IdentityProfile.id == identity_id),
    )
    if identity is None:
        raise MaterialMutationError(404, "IDENTITY_NOT_FOUND", "未找到身份配置")
    return identity


async def get_material_with_identity_or_raise(
    session: AsyncSession,
    material_id: int,
) -> IdentityMaterial:
    material = await session.scalar(
        select(IdentityMaterial)
        .options(selectinload(IdentityMaterial.identity))
        .where(IdentityMaterial.id == material_id),
    )
    if material is None:
        raise MaterialMutationError(404, "MATERIAL_NOT_FOUND", "未找到材料")
    return material


def normalize_material_type(material_type: str) -> str:
    normalized = material_type.strip().lower()
    if normalized not in {item.value for item in IdentityMaterialType}:
        raise MaterialMutationError(400, "MATERIAL_INVALID_TYPE", "不支持的材料标签")
    return normalized


async def record_material_event(
    session: AsyncSession,
    material: IdentityMaterial,
    event_name: str,
    *,
    actor: str,
    metadata: dict[str, object] | None = None,
) -> None:
    event_metadata: dict[str, object] = {
        "actor": actor,
        "identity_id": material.identity_id,
        "display_name": material.display_name,
        "original_filename": material.original_filename,
        "material_type": material.material_type,
        "mime_type": material.mime_type,
        "size_bytes": material.size_bytes,
    }
    if metadata:
        event_metadata.update(metadata)
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="identity_material",
        entity_id=str(material.id),
        metadata=event_metadata,
    )


async def prepare_material_deletion_snapshot(
    session: AsyncSession,
    material_id: int,
) -> dict[str, object]:
    """Return a non-mutating deletion preview with all affected records fingerprinted."""
    state = await _load_material_deletion_state(session, material_id)
    completed_batch_task_ids = _completed_batch_task_ids(state.batch_tasks)
    _ensure_material_deletion_allowed(state, completed_batch_task_ids)
    return _build_material_deletion_snapshot(state, completed_batch_task_ids)


async def delete_identity_material_record(
    session: AsyncSession,
    material_id: int,
    *,
    event_name: str,
    actor: str,
    expected_fingerprint: str | None = None,
) -> MaterialDeletionResult:
    """Delete one material and detach every safe stale reference within this transaction."""
    state = await _load_material_deletion_state(session, material_id)
    completed_batch_task_ids = _completed_batch_task_ids(state.batch_tasks)
    _ensure_material_deletion_allowed(state, completed_batch_task_ids)
    preview = _build_material_deletion_snapshot(state, completed_batch_task_ids)
    if expected_fingerprint is not None and preview["deletion_fingerprint"] != expected_fingerprint:
        raise MaterialMutationError(
            409,
            "MATERIAL_DELETION_STALE",
            "材料或其引用关系已发生变化，请重新生成删除预览。",
        )

    material = state.material
    identity = material.identity
    is_current_primary = identity.current_primary_material_id == material.id

    completed_batch_task_ids = []
    for batch_task in state.batch_tasks:
        if (
            batch_task.deleted_at is None
            and batch_task.status not in NON_CONTINUABLE_BATCH_TASK_STATUSES
            and sync_batch_task_completion(batch_task)
        ):
            completed_batch_task_ids.append(batch_task.id)

    detached_primary_task_ids: list[int] = []
    removed_attachment_task_ids: list[int] = []
    reset_draft_task_ids: list[int] = []
    for task in state.candidate_tasks:
        if not _material_reference_can_be_detached(task):
            continue
        detached_primary, removed_attachment, reset_draft = _detach_material_from_email_task(
            task,
            material.id,
        )
        if detached_primary:
            detached_primary_task_ids.append(task.id)
        if removed_attachment:
            removed_attachment_task_ids.append(task.id)
        if reset_draft:
            reset_draft_task_ids.append(task.id)

    detached_test_compose_session_ids: list[int] = []
    for compose_session in state.test_compose_sessions:
        if _detach_material_from_test_compose_session(compose_session, material.id):
            detached_test_compose_session_ids.append(compose_session.id)

    detached_batch_task_ids: list[int] = []
    for batch_task in state.batch_tasks:
        if not _batch_task_is_inactive(batch_task):
            continue
        if _detach_material_from_batch_task(batch_task, material.id):
            detached_batch_task_ids.append(batch_task.id)

    detached_match_analysis_run_count = 0
    for match_run in state.match_analysis_runs:
        match_run.primary_material_id = None
        detached_match_analysis_run_count += 1

    if is_current_primary:
        identity.current_primary_material_id = None
        identity.updated_at = utc_now()

    material_file_path = material.file_path
    result = MaterialDeletionResult(
        material_id=material.id,
        identity_id=material.identity_id,
        display_name=material.display_name,
        file_path=material_file_path,
        was_primary=is_current_primary,
        detached_primary_task_ids=detached_primary_task_ids,
        removed_attachment_task_ids=removed_attachment_task_ids,
        reset_draft_task_ids=reset_draft_task_ids,
        detached_test_compose_session_ids=detached_test_compose_session_ids,
        detached_batch_task_ids=detached_batch_task_ids,
        detached_match_analysis_run_count=detached_match_analysis_run_count,
        completed_batch_task_ids=completed_batch_task_ids,
    )
    await record_material_event(
        session,
        material,
        event_name,
        actor=actor,
        metadata={
            "was_primary": result.was_primary,
            "detached_primary_task_ids": result.detached_primary_task_ids,
            "removed_attachment_task_ids": result.removed_attachment_task_ids,
            "reset_draft_task_ids": result.reset_draft_task_ids,
            "detached_test_compose_session_ids": result.detached_test_compose_session_ids,
            "detached_batch_task_ids": result.detached_batch_task_ids,
            "detached_match_analysis_run_count": result.detached_match_analysis_run_count,
        },
    )
    await session.delete(material)
    return result


async def _load_material_deletion_state(
    session: AsyncSession,
    material_id: int,
) -> _MaterialDeletionState:
    material = await get_material_with_identity_or_raise(session, material_id)
    candidate_tasks = list(
        (
            await session.execute(
                select(EmailTask)
                .options(selectinload(EmailTask.batch_task))
                .where(EmailTask.identity_id == material.identity_id),
            )
        )
        .scalars()
        .unique()
    )
    batch_tasks = list(
        (
            await session.execute(
                select(BatchTask)
                .options(selectinload(BatchTask.email_tasks))
                .where(BatchTask.identity_id == material.identity_id),
            )
        )
        .scalars()
        .unique()
    )
    test_compose_sessions = list(
        await session.scalars(
            select(TestComposeSession).where(
                TestComposeSession.identity_id == material.identity_id,
            ),
        ),
    )
    match_analysis_runs = list(
        await session.scalars(
            select(MatchAnalysisRun).where(
                MatchAnalysisRun.identity_id == material.identity_id,
                MatchAnalysisRun.primary_material_id == material.id,
            ),
        ),
    )
    return _MaterialDeletionState(
        material=material,
        candidate_tasks=candidate_tasks,
        batch_tasks=batch_tasks,
        test_compose_sessions=test_compose_sessions,
        match_analysis_runs=match_analysis_runs,
    )


def _ensure_material_deletion_allowed(
    state: _MaterialDeletionState,
    completed_batch_task_ids: list[int],
) -> None:
    material_id = state.material.id
    blocking_tasks = [
        task
        for task in state.candidate_tasks
        if _task_references_material(task, material_id) and _material_reference_blocks_deletion(task)
    ]
    if blocking_tasks:
        raise MaterialMutationError(
            400,
            "MATERIAL_DELETION_BLOCKED",
            "当前材料仍被已批准、定时或发送中的任务使用",
        )

    unknown_referencing_tasks = [
        task
        for task in state.candidate_tasks
        if task.status not in MATERIAL_REFERENCE_BLOCKING_STATUSES
        and task.status not in MATERIAL_REFERENCE_DETACHABLE_STATUSES
        and _task_references_material(task, material_id)
    ]
    if unknown_referencing_tasks:
        raise MaterialMutationError(
            400,
            "MATERIAL_DELETION_BLOCKED",
            "当前材料仍被未完成任务使用",
        )

    completed_ids = set(completed_batch_task_ids)
    for batch_task in state.batch_tasks:
        if batch_task.deleted_at is not None:
            continue
        if batch_task.status in NON_CONTINUABLE_BATCH_TASK_STATUSES or batch_task.id in completed_ids:
            continue
        if _batch_task_references_material(batch_task, material_id):
            raise MaterialMutationError(
                400,
                "MATERIAL_DELETION_BLOCKED",
                "当前材料仍被可继续批量任务使用",
            )


def _completed_batch_task_ids(batch_tasks: list[BatchTask]) -> list[int]:
    return [
        batch_task.id
        for batch_task in batch_tasks
        if batch_task.deleted_at is None
        and batch_task.status not in NON_CONTINUABLE_BATCH_TASK_STATUSES
        and should_mark_batch_task_completed(batch_task)
    ]


def _build_material_deletion_snapshot(
    state: _MaterialDeletionState,
    completed_batch_task_ids: list[int],
) -> dict[str, object]:
    material = state.material
    material_id = material.id
    completed_ids = set(completed_batch_task_ids)
    referenced_tasks = [
        task for task in state.candidate_tasks if _task_references_material(task, material_id)
    ]
    referenced_batch_tasks = [
        batch_task
        for batch_task in state.batch_tasks
        if _batch_task_references_material(batch_task, material_id)
        or batch_task.id in completed_ids
    ]
    detached_primary_task_ids: list[int] = []
    removed_attachment_task_ids: list[int] = []
    reset_draft_task_ids: list[int] = []
    for task in referenced_tasks:
        if not _material_reference_can_be_detached_in_preview(task, completed_ids):
            continue
        detached_primary, removed_attachment, reset_draft = _describe_email_task_detachment(
            task,
            material_id,
            completed_ids,
        )
        if detached_primary:
            detached_primary_task_ids.append(task.id)
        if removed_attachment:
            removed_attachment_task_ids.append(task.id)
        if reset_draft:
            reset_draft_task_ids.append(task.id)

    detached_test_compose_session_ids = [
        compose_session.id
        for compose_session in state.test_compose_sessions
        if material_id in (compose_session.selected_material_ids or [])
    ]
    detached_batch_task_ids = [
        batch_task.id
        for batch_task in referenced_batch_tasks
        if _batch_task_is_inactive_in_preview(batch_task, completed_ids)
    ]
    is_primary = material.identity.current_primary_material_id == material.id
    effects = {
        "clears_default_reference_material": is_primary,
        "detached_primary_task_ids": detached_primary_task_ids,
        "removed_attachment_task_ids": removed_attachment_task_ids,
        "reset_draft_task_ids": reset_draft_task_ids,
        "detached_test_compose_session_ids": detached_test_compose_session_ids,
        "detached_batch_task_ids": detached_batch_task_ids,
        "detached_match_analysis_run_count": len(state.match_analysis_runs),
        "completed_batch_task_ids": completed_batch_task_ids,
    }
    fingerprint_payload = {
        "material": {
            "id": material.id,
            "identity_id": material.identity_id,
            "display_name": material.display_name,
            "original_filename": material.original_filename,
            "material_type": material.material_type,
            "mime_type": material.mime_type,
            "size_bytes": material.size_bytes,
            "sha256": material.sha256,
            "is_primary": is_primary,
        },
        "email_tasks": [
            _material_reference_task_fingerprint_data(task) for task in referenced_tasks
        ],
        "batch_tasks": [
            _material_reference_batch_fingerprint_data(batch_task)
            for batch_task in referenced_batch_tasks
        ],
        "test_compose_sessions": [
            {
                "id": compose_session.id,
                "selected_material_ids": compose_session.selected_material_ids or [],
            }
            for compose_session in state.test_compose_sessions
            if material_id in (compose_session.selected_material_ids or [])
        ],
        "match_analysis_runs": [
            {
                "id": run.id,
                "primary_material_id": run.primary_material_id,
                "status": run.status,
            }
            for run in state.match_analysis_runs
        ],
        "effects": effects,
    }
    warnings = ["确认后会永久删除该材料文件，无法从应用内恢复。"]
    if is_primary:
        warnings.append("该材料当前是默认 AI 参考材料，删除后会清除该默认设置。")
    if detached_primary_task_ids or removed_attachment_task_ids:
        warnings.append("引用该材料的可安全处理草稿会解除引用；部分草稿将回到需要重新审核的状态。")
    if detached_batch_task_ids:
        warnings.append("已停止、已完成或已删除的批量任务会解除该材料引用。")
    return {
        "snapshot_version": "1",
        "material_id": material.id,
        "deletion_fingerprint": fingerprint(fingerprint_payload),
        "summary": {
            "material": {
                "id": material.id,
                "name": material.display_name,
                "identity_id": material.identity_id,
                "is_primary": is_primary,
            },
            "effects": effects,
        },
        "warnings": warnings,
    }


def _material_reference_task_fingerprint_data(task: EmailTask) -> dict[str, object]:
    return {
        "id": task.id,
        "source": task.source,
        "batch_task_id": task.batch_task_id,
        "status": task.status,
        "primary_material_id": task.primary_material_id,
        "selected_material_ids": task.selected_material_ids or [],
        "match_score": task.match_score,
        "match_reason": task.match_reason,
        "fit_points": task.fit_points or [],
        "risk_points": task.risk_points or [],
        "match_keywords": task.match_keywords or [],
        "scheduled_at": _serialize_optional_datetime(task.scheduled_at),
        "batch_send_canceled_at": _serialize_optional_datetime(task.batch_send_canceled_at),
    }


def _material_reference_batch_fingerprint_data(batch_task: BatchTask) -> dict[str, object]:
    return {
        "id": batch_task.id,
        "status": batch_task.status,
        "deleted_at": _serialize_optional_datetime(batch_task.deleted_at),
        "primary_material_id": batch_task.primary_material_id,
        "selected_material_ids": batch_task.selected_material_ids or [],
        "target_count": batch_task.target_count,
        "email_tasks": [
            {
                "id": email_task.id,
                "status": email_task.status,
                "scheduled_at": _serialize_optional_datetime(email_task.scheduled_at),
                "batch_send_canceled_at": _serialize_optional_datetime(
                    email_task.batch_send_canceled_at,
                ),
            }
            for email_task in sorted(batch_task.email_tasks, key=lambda item: item.id)
        ],
    }


def _serialize_optional_datetime(value: object) -> str | None:
    return serialize_api_datetime(value) if value is not None else None


def _material_reference_can_be_detached_in_preview(
    task: EmailTask,
    completed_batch_task_ids: set[int],
) -> bool:
    if task.status in MATERIAL_REFERENCE_DETACHABLE_STATUSES:
        return True
    if task.status not in INACTIVE_BATCH_DETACHABLE_MATERIAL_REFERENCE_STATUSES:
        return False
    batch_task = task.batch_task
    return batch_task is not None and _batch_task_is_inactive_in_preview(
        batch_task,
        completed_batch_task_ids,
    )


def _describe_email_task_detachment(
    task: EmailTask,
    material_id: int,
    completed_batch_task_ids: set[int],
) -> tuple[bool, bool, bool]:
    detached_primary = task.primary_material_id == material_id
    removed_attachment = material_id in (task.selected_material_ids or [])
    reset_draft = False
    if detached_primary and task.status in MATERIAL_REFERENCE_RESET_DRAFT_STATUSES:
        reset_draft = True
    elif detached_primary and task.status == EmailTaskStatus.DRAFT_FAILED.value:
        reset_draft = False
    if removed_attachment and not detached_primary and task.status in MATERIAL_REFERENCE_RESET_DRAFT_STATUSES:
        reset_draft = True
    if (
        (detached_primary or removed_attachment)
        and task.status in INACTIVE_BATCH_DETACHABLE_MATERIAL_REFERENCE_STATUSES
        and task.batch_task is not None
        and _batch_task_is_inactive_in_preview(task.batch_task, completed_batch_task_ids)
    ):
        reset_draft = True
    return detached_primary, removed_attachment, reset_draft


def _batch_task_is_inactive_in_preview(
    batch_task: BatchTask,
    completed_batch_task_ids: set[int],
) -> bool:
    return (
        batch_task.deleted_at is not None
        or batch_task.status in NON_CONTINUABLE_BATCH_TASK_STATUSES
        or batch_task.id in completed_batch_task_ids
    )


async def _apply_primary_material_to_blocked_batch_tasks(
    session: AsyncSession,
    material: IdentityMaterial,
) -> int:
    tasks = list(
        (
            await session.execute(
                select(EmailTask)
                .options(selectinload(EmailTask.batch_task))
                .where(
                    EmailTask.identity_id == material.identity_id,
                    EmailTask.source == "batch",
                    batch_item_uses_llm_generation_column(EmailTask.outreach_generation_mode),
                    EmailTask.primary_material_id.is_(None),
                    EmailTask.status.in_(
                        [
                            EmailTaskStatus.DISCOVERED.value,
                            EmailTaskStatus.MATCHED.value,
                            EmailTaskStatus.DRAFT_FAILED.value,
                        ],
                    ),
                ),
            )
        )
        .scalars()
        .unique()
    )
    updated_count = 0
    now = utc_now()
    for task in tasks:
        batch_task = task.batch_task
        if batch_task is None or batch_task.status in NON_CONTINUABLE_BATCH_TASK_STATUSES:
            continue
        task.outreach_generation_mode = normalize_batch_item_generation_mode(task)
        task.primary_material_id = material.id
        task.last_error = None
        if task.status == EmailTaskStatus.DRAFT_FAILED.value:
            task.status = material_reference_fallback_status(task)
        task.updated_at = now
        if batch_task.primary_material_id is None:
            batch_task.primary_material_id = material.id
            batch_task.updated_at = now
        updated_count += 1
    return updated_count


def _task_references_material(task: EmailTask, material_id: int) -> bool:
    return task.primary_material_id == material_id or material_id in (task.selected_material_ids or [])


def _batch_task_references_material(task: BatchTask, material_id: int) -> bool:
    return task.primary_material_id == material_id or material_id in (task.selected_material_ids or [])


def _material_reference_blocks_deletion(task: EmailTask) -> bool:
    if task.status not in MATERIAL_REFERENCE_BLOCKING_STATUSES:
        return False
    if task.status in IN_PROGRESS_MATERIAL_REFERENCE_STATUSES:
        return True

    batch_task = task.batch_task
    if batch_task is None:
        return True
    return batch_task.deleted_at is None and batch_task.status not in NON_CONTINUABLE_BATCH_TASK_STATUSES


def _material_reference_can_be_detached(task: EmailTask) -> bool:
    if task.status in MATERIAL_REFERENCE_DETACHABLE_STATUSES:
        return True
    return _is_inactive_batch_approved_material_reference(task)


def _is_inactive_batch_approved_material_reference(task: EmailTask) -> bool:
    if task.status not in INACTIVE_BATCH_DETACHABLE_MATERIAL_REFERENCE_STATUSES:
        return False

    batch_task = task.batch_task
    return batch_task is not None and _batch_task_is_inactive(batch_task)


def _batch_task_is_inactive(batch_task: BatchTask) -> bool:
    return (
        batch_task.deleted_at is not None
        or batch_task.status in NON_CONTINUABLE_BATCH_TASK_STATUSES
    )


def _inactive_batch_cancellation_reason(task: EmailTask) -> str:
    if task.batch_task is not None and task.batch_task.status == BatchTaskStatus.EXPIRED.value:
        return EmailTaskCancellationReason.SCHEDULE_EXPIRED.value
    return EmailTaskCancellationReason.BATCH_STOPPED.value


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

    if (detached_primary or removed_attachment) and _is_inactive_batch_approved_material_reference(task):
        _clear_approved_draft(task)
        task.status = EmailTaskStatus.CANCELED.value
        task.cancellation_reason = _inactive_batch_cancellation_reason(task)
        task.draft_generation_previous_status = None
        task.last_error = None
        reset_draft = True

    if detached_primary or removed_attachment or reset_draft:
        task.updated_at = utc_now()

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
        task.updated_at = utc_now()
    return updated


def _detach_material_from_test_compose_session(
    compose_session: TestComposeSession,
    material_id: int,
) -> bool:
    if material_id not in (compose_session.selected_material_ids or []):
        return False

    compose_session.selected_material_ids = [
        selected_material_id
        for selected_material_id in compose_session.selected_material_ids or []
        if selected_material_id != material_id
    ]
    compose_session.updated_at = utc_now()
    return True
