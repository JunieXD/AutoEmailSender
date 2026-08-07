from __future__ import annotations

import re
from datetime import UTC, datetime

from app.core.time import utc_now

from time import perf_counter

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_async_session
from app.models import (
    EmailTask,
    IdentityCommunicationGroup,
    IdentityProfessorMatchResult,
    IdentityProfile,
    MatchAnalysisJob,
    MatchAnalysisJobItem,
    MatchAnalysisJobItemStatus,
    MatchAnalysisJobStatus,
    MatchAnalysisRun,
)
from .schemas import (
    ConnectionTestResult,
    IdentityProfileCreate,
    IdentityProfileRead,
    IdentityProfileUpdate,
    IdentityTemplateImportResult,
)
from app.modules.campaigns.public import IdentityDefaultOutreachTemplateUpdate
from app.services.file_storage import delete_file
from ..communication_groups.public import (
    cleanup_communication_group_after_identity_delete,
)
from app.modules.communications.public import (
    clear_identity_sent_folder_discovery_cache_in_session,
    test_imap_connection,
    test_smtp_connection,
)
from app.services.operation_logs import record_operation_log
from app.modules.campaigns.public import (
    apply_template_to_identity_legacy_fields,
    clear_identity_default_template,
    create_template_from_legacy_identity,
    get_outreach_template,
    normalize_generation_mode,
    normalize_nullable_template_text,
    sync_template_to_default_identities,
)
from app.modules.campaigns.public import (
    OUTREACH_GENERATION_MODE_LLM,
    OUTREACH_GENERATION_MODE_TEMPLATE,
    import_outreach_template_file,
)
from app.modules.communications.public import explain_smtp_error

from .serializer import serialize_identity


router = APIRouter(prefix="/api/identities", tags=["identities"])
DUPLICATE_EMAIL_DETAIL = "该发件邮箱已存在，请改用编辑已有身份或更换邮箱"


@router.get("", response_model=list[IdentityProfileRead])
async def list_identities(
    session: AsyncSession = Depends(get_async_session),
) -> list[IdentityProfileRead]:
    result = await session.execute(
        _identity_query().order_by(
            IdentityProfile.is_default.desc(),
            IdentityProfile.created_at.desc(),
        ),
    )
    identities = list(result.scalars().unique())
    return [serialize_identity(identity) for identity in identities]


@router.post("", response_model=IdentityProfileRead, status_code=status.HTTP_201_CREATED)
async def create_identity(
    payload: IdentityProfileCreate,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityProfileRead:
    existing_count = await session.scalar(select(func.count(IdentityProfile.id)))
    data = _normalize_identity_payload(payload)
    requested_template_id = data.pop("default_outreach_template_id", None)
    template_was_explicit = "default_outreach_template_id" in payload.model_fields_set
    requested_template = None
    if requested_template_id is not None:
        requested_template = await _get_active_template_or_400(
            session,
            int(requested_template_id),
        )
    elif template_was_explicit:
        _clear_legacy_template_data(data)
    await _ensure_identity_email_available(session, str(data["email_address"]))
    identity = IdentityProfile(**data)
    if not existing_count:
        identity.is_default = True
    elif payload.is_default:
        await _clear_default_identities(session)

    try:
        session.add(identity)
        await session.flush()
        if requested_template is not None:
            apply_template_to_identity_legacy_fields(identity, requested_template)
        elif not template_was_explicit:
            await create_template_from_legacy_identity(session, identity)
        await _record_identity_log(session, identity, "identity.created")
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        _raise_email_conflict_if_needed(exc)
        raise
    saved = await _get_identity(session, identity.id)
    return serialize_identity(saved)


@router.put("/{identity_id}", response_model=IdentityProfileRead)
async def update_identity(
    identity_id: int,
    payload: IdentityProfileUpdate,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityProfileRead:
    identity = await _get_identity(session, identity_id)
    data = _normalize_identity_payload(payload)
    requested_template_id = data.pop("default_outreach_template_id", None)
    template_was_explicit = "default_outreach_template_id" in payload.model_fields_set
    requested_template = None
    if requested_template_id is not None:
        requested_template = await _get_active_template_or_400(
            session,
            int(requested_template_id),
        )
    elif template_was_explicit:
        _clear_legacy_template_data(data)
    old_imap_signature = _identity_imap_cache_signature(identity)
    await _ensure_identity_email_available(
        session,
        str(data["email_address"]),
        exclude_id=identity_id,
    )
    if data["is_default"]:
        await _clear_default_identities(session, exclude_id=identity_id)

    for key, value in data.items():
        setattr(identity, key, value)
    if requested_template is not None:
        apply_template_to_identity_legacy_fields(identity, requested_template)
    elif template_was_explicit:
        clear_identity_default_template(identity)
    elif identity.default_outreach_template is not None:
        identity.default_outreach_template.recommended_generation_mode = (
            normalize_generation_mode(identity.outreach_generation_mode)
        )
        identity.default_outreach_template.subject = normalize_nullable_template_text(
            identity.outreach_template_subject,
        )
        identity.default_outreach_template.body_text = normalize_nullable_template_text(
            identity.outreach_template_body_text,
        )
        identity.default_outreach_template.body_html = normalize_nullable_template_text(
            identity.outreach_template_body_html,
        )
        identity.default_outreach_template.updated_at = utc_now()
        await sync_template_to_default_identities(
            session,
            identity.default_outreach_template,
        )
    else:
        await create_template_from_legacy_identity(session, identity)
    if _imap_cache_signature_from_data(data) != old_imap_signature:
        await clear_identity_sent_folder_discovery_cache_in_session(session, identity_id)
    identity.updated_at = utc_now()

    try:
        await _record_identity_log(session, identity, "identity.updated")
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        _raise_email_conflict_if_needed(exc)
        raise
    saved = await _get_identity(session, identity_id)
    return serialize_identity(saved)


@router.put("/{identity_id}/default-template", response_model=IdentityProfileRead)
async def update_identity_default_template(
    identity_id: int,
    payload: IdentityDefaultOutreachTemplateUpdate,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityProfileRead:
    identity = await _get_identity(session, identity_id)
    if payload.template_id is None:
        clear_identity_default_template(identity)
    else:
        template = await _get_active_template_or_400(session, payload.template_id)
        apply_template_to_identity_legacy_fields(identity, template)
    await _record_identity_log(
        session,
        identity,
        "identity.default_outreach_template_updated",
        metadata={"default_outreach_template_id": identity.default_outreach_template_id},
    )
    await session.commit()
    saved = await _get_identity(session, identity_id)
    return serialize_identity(saved)


@router.delete("/{identity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_identity(
    identity_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    identity = await _get_identity(session, identity_id)
    was_default = identity.is_default
    communication_group_id = identity.communication_group_id
    cleared_match_source = False
    if communication_group_id is not None:
        communication_group = await session.get(
            IdentityCommunicationGroup,
            communication_group_id,
        )
        if (
            communication_group is not None
            and communication_group.match_source_identity_id == identity_id
        ):
            communication_group.match_source_identity_id = None
            communication_group.updated_at = utc_now()
            cleared_match_source = True

    for material in identity.materials:
        delete_file(material.file_path)

    await _record_identity_log(
        session,
        identity,
        "identity.deleted",
        metadata={
            "was_default": was_default,
            "cleared_group_match_source": cleared_match_source,
        },
    )
    await _delete_identity_match_artifacts(session, identity_id)
    await session.delete(identity)
    await session.flush()
    group_cleanup = None
    if communication_group_id is not None:
        group_cleanup = await cleanup_communication_group_after_identity_delete(
            session,
            group_id=communication_group_id,
            removed_identity_id=identity_id,
        )
    if group_cleanup is not None:
        await record_operation_log(
            session,
            category="identity",
            event_name=(
                "communication_group.deleted"
                if group_cleanup.dissolved
                else "communication_group.updated"
            ),
            entity_type="identity_communication_group",
            entity_id=str(group_cleanup.group_id),
            metadata={
                "before_member_ids": list(group_cleanup.previous_member_ids),
                "after_member_ids": (
                    []
                    if group_cleanup.dissolved
                    else list(group_cleanup.member_ids)
                ),
                "removed_identity_id": identity_id,
                "cleared_match_source": cleared_match_source,
            },
        )
    await session.commit()

    if was_default:
        remaining = await session.scalar(
            select(IdentityProfile)
            .order_by(IdentityProfile.created_at.asc())
            .limit(1),
        )
        if remaining:
            remaining.is_default = True
            remaining.updated_at = utc_now()
            await session.commit()


async def _delete_identity_match_artifacts(
    session: AsyncSession,
    identity_id: int,
) -> None:
    """Remove match records that would otherwise keep a deleted identity/task alive.

    SQLite does not enforce ``ON DELETE`` actions in the desktop runtime, so
    cross-identity analysis records must be cleaned explicitly. PostgreSQL does
    enforce the foreign keys, and the same cleanup is therefore required before
    SQLAlchemy cascades the identity's email tasks. Task snapshots intentionally
    retain ``match_source_identity_id`` as historical provenance.
    """

    now = utc_now()
    task_ids = select(EmailTask.id).where(EmailTask.identity_id == identity_id)
    active_source_job_ids = select(MatchAnalysisJob.id).where(
        MatchAnalysisJob.match_source_identity_id == identity_id,
        MatchAnalysisJob.status.in_(
            [
                MatchAnalysisJobStatus.QUEUED.value,
                MatchAnalysisJobStatus.RUNNING.value,
            ],
        ),
    )
    await session.execute(
        update(MatchAnalysisJobItem)
        .where(
            MatchAnalysisJobItem.job_id.in_(active_source_job_ids),
            MatchAnalysisJobItem.status.in_(
                [
                    MatchAnalysisJobItemStatus.QUEUED.value,
                    MatchAnalysisJobItemStatus.RUNNING.value,
                ],
            ),
        )
        .values(
            status=MatchAnalysisJobItemStatus.CANCELED.value,
            skip_reason="匹配依据身份已删除",
            finished_at=now,
            updated_at=now,
        ),
    )
    await session.execute(
        update(MatchAnalysisJob)
        .where(MatchAnalysisJob.id.in_(active_source_job_ids))
        .values(
            status=MatchAnalysisJobStatus.CANCELED.value,
            cancel_requested_at=now,
            finished_at=now,
            updated_at=now,
            last_error="匹配依据身份已删除，任务已取消",
        ),
    )
    run_ids = select(MatchAnalysisRun.id).where(
        or_(
            # A run normally belongs to the identity used as the match source.
            MatchAnalysisRun.identity_id == identity_id,
            # Legacy shared-group runs can reference an active identity's task
            # while belonging to a different source identity. Deleting the task
            # must still remove those historical linked runs.
            MatchAnalysisRun.email_task_id.in_(task_ids),
        ),
    )
    await session.execute(
        update(MatchAnalysisJobItem)
        .where(MatchAnalysisJobItem.match_analysis_run_id.in_(run_ids))
        .values(match_analysis_run_id=None, updated_at=now),
    )
    await session.execute(
        update(IdentityProfessorMatchResult)
        .where(IdentityProfessorMatchResult.latest_analysis_run_id.in_(run_ids))
        .values(latest_analysis_run_id=None, updated_at=now),
    )
    await session.execute(
        update(IdentityProfessorMatchResult)
        .where(IdentityProfessorMatchResult.source_email_task_id.in_(task_ids))
        .values(source_email_task_id=None, updated_at=now),
    )
    await session.execute(
        update(MatchAnalysisJobItem)
        .where(MatchAnalysisJobItem.email_task_id.in_(task_ids))
        .values(email_task_id=None, updated_at=now),
    )
    await session.execute(
        delete(IdentityProfessorMatchResult).where(
            IdentityProfessorMatchResult.identity_id == identity_id,
        ),
    )
    await session.execute(
        delete(MatchAnalysisRun).where(
            MatchAnalysisRun.id.in_(run_ids),
        ),
    )
    # Match-analysis jobs are identity-owned history, just like the email tasks
    # that feed them.  Delete their items first because the job identity FK is
    # non-null and has no database-level cascade in older installations.
    owned_job_ids = select(MatchAnalysisJob.id).where(
        MatchAnalysisJob.identity_id == identity_id,
    )
    await session.execute(
        delete(MatchAnalysisJobItem).where(
            MatchAnalysisJobItem.job_id.in_(owned_job_ids),
        ),
    )
    await session.execute(
        delete(MatchAnalysisJob).where(MatchAnalysisJob.id.in_(owned_job_ids)),
    )
    await session.execute(
        update(MatchAnalysisJob)
        .where(MatchAnalysisJob.match_source_identity_id == identity_id)
        .values(match_source_identity_id=None, updated_at=now),
    )


@router.post("/{identity_id}/default", response_model=IdentityProfileRead)
async def set_default_identity(
    identity_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityProfileRead:
    identity = await _get_identity(session, identity_id)
    await _clear_default_identities(session, exclude_id=identity_id)
    identity.is_default = True
    identity.updated_at = utc_now()
    await _record_identity_log(session, identity, "identity.default_set")
    await session.commit()
    saved = await _get_identity(session, identity_id)
    return serialize_identity(saved)


@router.post("/{identity_id}/smtp-test", response_model=ConnectionTestResult)
async def smtp_test(
    identity_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionTestResult:
    identity = await _get_identity(session, identity_id)
    started_at = perf_counter()
    ok, message = await test_smtp_connection(identity)
    duration_ms = int((perf_counter() - started_at) * 1000)
    await _record_identity_log(
        session,
        identity,
        "identity.smtp_tested",
        level="info" if ok else "warning",
        metadata={
            "ok": ok,
            "result": "ok" if ok else "failed",
            "duration_ms": duration_ms,
            "host": identity.smtp_host,
        },
    )
    await session.commit()
    return ConnectionTestResult(
        ok=ok,
        message=message,
        host=identity.smtp_host,
        possible_cause=(
            explain_smtp_error(message)
            if not ok
            else None
        ),
    )


@router.post("/{identity_id}/imap-test", response_model=ConnectionTestResult)
async def imap_test(
    identity_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionTestResult:
    identity = await _get_identity(session, identity_id)
    started_at = perf_counter()
    ok, message = await test_imap_connection(identity)
    duration_ms = int((perf_counter() - started_at) * 1000)
    await _record_identity_log(
        session,
        identity,
        "identity.imap_tested",
        level="info" if ok else "warning",
        metadata={
            "ok": ok,
            "result": "ok" if ok else "failed",
            "duration_ms": duration_ms,
            "host": identity.imap_host,
        },
    )
    await session.commit()
    return ConnectionTestResult(ok=ok, message=message, host=identity.imap_host)


@router.post("/{identity_id}/template-import", response_model=IdentityTemplateImportResult)
async def import_identity_template(
    identity_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_async_session),
) -> IdentityTemplateImportResult:
    await _get_identity(session, identity_id)
    return await _import_identity_template_from_upload(file)


@router.post("/template-import", response_model=IdentityTemplateImportResult)
async def import_unsaved_identity_template(
    file: UploadFile = File(...),
) -> IdentityTemplateImportResult:
    return await _import_identity_template_from_upload(file)


async def _import_identity_template_from_upload(
    file: UploadFile,
) -> IdentityTemplateImportResult:
    if not file.filename:
        raise HTTPException(status_code=400, detail="请选择模板文件")
    try:
        imported = import_outreach_template_file(file.filename, await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return IdentityTemplateImportResult(
        subject=imported.subject,
        body_text=imported.body_text,
        body_html=imported.body_html,
        format_name=imported.format_name,
    )


def _identity_query():
    return select(IdentityProfile).options(
        selectinload(IdentityProfile.materials),
        selectinload(IdentityProfile.current_primary_material),
    )


async def _get_active_template_or_400(
    session: AsyncSession,
    template_id: int,
):
    try:
        return await get_outreach_template(session, template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _get_identity(session: AsyncSession, identity_id: int) -> IdentityProfile:
    identity = await session.scalar(
        _identity_query().where(IdentityProfile.id == identity_id),
    )
    if not identity:
        raise HTTPException(status_code=404, detail="未找到身份配置")
    return identity


async def _clear_default_identities(
    session: AsyncSession,
    exclude_id: int | None = None,
) -> None:
    result = await session.execute(select(IdentityProfile))
    for identity in result.scalars():
        if exclude_id is not None and identity.id == exclude_id:
            continue
        identity.is_default = False
        identity.updated_at = utc_now()


async def _ensure_identity_email_available(
    session: AsyncSession,
    email_address: str,
    *,
    exclude_id: int | None = None,
) -> None:
    statement = select(IdentityProfile.id).where(
        IdentityProfile.email_address == email_address,
    )
    if exclude_id is not None:
        statement = statement.where(IdentityProfile.id != exclude_id)
    existing_id = await session.scalar(statement.limit(1))
    if existing_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=DUPLICATE_EMAIL_DETAIL,
        )


def _raise_email_conflict_if_needed(exc: IntegrityError) -> None:
    message = str(exc.orig or exc)
    if (
        "identity_profiles.email_address" in message
        or "uq_identity_profiles_email_address" in message
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=DUPLICATE_EMAIL_DETAIL,
        ) from exc


async def _record_identity_log(
    session: AsyncSession,
    identity: IdentityProfile,
    event_name: str,
    *,
    level: str = "info",
    metadata: dict[str, object] | None = None,
) -> None:
    base_metadata: dict[str, object] = {
        "id": identity.id,
        "name": identity.name,
        "profile_name": identity.profile_name,
        "sender_name": identity.sender_name,
        "email_address": identity.email_address,
        "smtp_host": identity.smtp_host,
        "imap_host": identity.imap_host,
        "is_default": identity.is_default,
    }
    if metadata:
        base_metadata.update(metadata)
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        level=level,
        entity_type="identity",
        entity_id=str(identity.id),
        metadata=base_metadata,
    )


def _normalize_identity_payload(
    payload: IdentityProfileCreate | IdentityProfileUpdate,
) -> dict[str, object]:
    data = payload.model_dump()
    smtp_host = str(data.get("smtp_host") or "").strip()
    email_address = str(data.get("email_address") or "").strip()
    smtp_password = str(data.get("smtp_password") or "")
    imap_host = str(data.get("imap_host") or "").strip()
    outreach_generation_mode = str(
        data.get("outreach_generation_mode") or OUTREACH_GENERATION_MODE_LLM,
    ).strip().lower()

    if outreach_generation_mode not in {
        OUTREACH_GENERATION_MODE_LLM,
        OUTREACH_GENERATION_MODE_TEMPLATE,
    }:
        outreach_generation_mode = OUTREACH_GENERATION_MODE_LLM

    profile_name = _clean_required_text(data.get("profile_name") or data.get("name"))
    sender_name = _clean_required_text(data.get("sender_name") or profile_name)
    data["name"] = profile_name
    data["profile_name"] = profile_name
    data["sender_name"] = sender_name
    data["email_address"] = email_address
    data["smtp_username"] = email_address
    data["imap_host"] = imap_host or _infer_imap_host(smtp_host)
    data["imap_port"] = data.get("imap_port") or 993
    data["imap_username"] = email_address
    data["imap_password"] = smtp_password
    data["outreach_generation_mode"] = outreach_generation_mode
    data["outreach_template_subject"] = _clean_nullable_text(
        data.get("outreach_template_subject"),
    )
    data["outreach_template_body_text"] = _clean_nullable_text(
        data.get("outreach_template_body_text"),
    )
    data["outreach_template_body_html"] = _clean_nullable_text(
        data.get("outreach_template_body_html"),
    )
    return data


def _clear_legacy_template_data(data: dict[str, object]) -> None:
    data["outreach_generation_mode"] = OUTREACH_GENERATION_MODE_LLM
    data["outreach_template_subject"] = None
    data["outreach_template_body_text"] = None
    data["outreach_template_body_html"] = None


def _identity_imap_cache_signature(identity: IdentityProfile) -> tuple[object, ...]:
    return (
        identity.email_address,
        identity.imap_host,
        identity.imap_port,
        identity.imap_username,
        identity.imap_password,
    )


def _imap_cache_signature_from_data(data: dict[str, object]) -> tuple[object, ...]:
    return (
        data.get("email_address"),
        data.get("imap_host"),
        data.get("imap_port"),
        data.get("imap_username"),
        data.get("imap_password"),
    )


def _infer_imap_host(smtp_host: str) -> str:
    if not smtp_host:
        return ""
    return re.sub(r"smtp", "imap", smtp_host, count=1, flags=re.IGNORECASE)


def _clean_nullable_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _clean_required_text(value: object) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="请填写配置名称和发件人姓名")
    return cleaned
