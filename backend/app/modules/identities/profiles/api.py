from __future__ import annotations

import re
from time import perf_counter

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_async_session
from app.core.time import utc_now
from app.models import (
    IdentityProfile,
    OutreachTemplate,
)
from app.modules.campaigns.public import (
    OUTREACH_GENERATION_MODE_LLM,
    OUTREACH_GENERATION_MODE_TEMPLATE,
    IdentityDefaultOutreachTemplateUpdate,
    apply_template_to_identity_legacy_fields,
    clear_identity_default_template,
    create_template_from_legacy_identity,
    get_outreach_template,
    import_outreach_template_file,
    normalize_generation_mode,
    normalize_nullable_template_text,
    sync_template_to_default_identities,
)
from app.modules.communications.public import (
    clear_identity_sent_folder_discovery_cache_in_session,
    explain_smtp_error,
    test_imap_connection,
    test_smtp_connection,
)
from app.services.material_catalog import list_global_material_metadata
from app.services.operation_logs import record_operation_log

from .defaults import set_default_identity_record
from .deletion import (
    IdentityDeletionError,
    build_identity_deletion_impact,
    retire_identity_profile,
)
from .schemas import (
    ConnectionTestResult,
    IdentityDeletionImpact,
    IdentityProfileCreate,
    IdentityProfileRead,
    IdentityProfileUpdate,
    IdentityTemplateImportResult,
)
from .serializer import serialize_identity
from .usage import (
    IdentityProfileRetiringError,
    end_identity_profile_retirement,
    track_identity_profile_usage,
)

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
    materials = await list_global_material_metadata(session)
    global_default_template = await _get_global_default_outreach_template(session)
    return [
        serialize_identity(identity, materials, global_default_template)
        for identity in identities
    ]


@router.post(
    "", response_model=IdentityProfileRead, status_code=status.HTTP_201_CREATED
)
async def create_identity(
    payload: IdentityProfileCreate,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityProfileRead:
    existing_count = await session.scalar(
        select(func.count(IdentityProfile.id)).where(
            IdentityProfile.deleted_at.is_(None)
        )
    )
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
    return await _serialize_identity_with_global_materials(session, saved)


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
        await clear_identity_sent_folder_discovery_cache_in_session(
            session, identity_id
        )
    identity.updated_at = utc_now()

    try:
        await _record_identity_log(session, identity, "identity.updated")
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        _raise_email_conflict_if_needed(exc)
        raise
    saved = await _get_identity(session, identity_id)
    return await _serialize_identity_with_global_materials(session, saved)


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
        metadata={
            "default_outreach_template_id": identity.default_outreach_template_id
        },
    )
    await session.commit()
    saved = await _get_identity(session, identity_id)
    return await _serialize_identity_with_global_materials(session, saved)


@router.get(
    "/{identity_id}/deletion-impact",
    response_model=IdentityDeletionImpact,
)
async def get_identity_deletion_impact(
    identity_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityDeletionImpact:
    identity = await _get_identity(session, identity_id)
    return await build_identity_deletion_impact(session, identity)


@router.delete("/{identity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_identity(
    identity_id: int,
    request: Request,
    impact_revision: str = Query(..., min_length=64, max_length=64),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    identity = await _get_identity(session, identity_id)
    # Acquire the database writer/row lock before the final impact check. SQLite
    # serializes the following writes; PostgreSQL also protects this row until commit.
    await session.execute(
        update(IdentityProfile)
        .where(
            IdentityProfile.id == identity_id,
            IdentityProfile.deleted_at.is_(None),
        )
        .values(updated_at=IdentityProfile.updated_at),
    )
    retirement_acquired = False
    try:
        result = await retire_identity_profile(
            session,
            identity,
            expected_revision=impact_revision,
        )
        retirement_acquired = True
        await session.commit()
    except IdentityDeletionError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": exc.code,
                "message": exc.message,
                "impact": exc.impact.model_dump(mode="json"),
            },
        ) from exc
    finally:
        if retirement_acquired:
            end_identity_profile_retirement(identity_id)

    runtime_manager = getattr(request.app.state, "runtime_manager", None)
    if runtime_manager is not None:
        for batch_task_id in result.stopped_batch_task_ids:
            runtime_manager.cancel_batch_draft_generation(batch_task_id)


@router.post("/{identity_id}/default", response_model=IdentityProfileRead)
async def set_default_identity(
    identity_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityProfileRead:
    identity = await _get_identity(session, identity_id)
    await set_default_identity_record(session, identity, refresh_timestamps=True)
    await _record_identity_log(session, identity, "identity.default_set")
    await session.commit()
    saved = await _get_identity(session, identity_id)
    return await _serialize_identity_with_global_materials(session, saved)


@router.post("/{identity_id}/smtp-test", response_model=ConnectionTestResult)
async def smtp_test(
    identity_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionTestResult:
    identity = await _get_identity(session, identity_id)
    started_at = perf_counter()
    try:
        with track_identity_profile_usage(identity.id, "smtp_test"):
            ok, message = await test_smtp_connection(identity)
    except IdentityProfileRetiringError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
        possible_cause=(explain_smtp_error(message) if not ok else None),
    )


@router.post("/{identity_id}/imap-test", response_model=ConnectionTestResult)
async def imap_test(
    identity_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionTestResult:
    identity = await _get_identity(session, identity_id)
    started_at = perf_counter()
    try:
        with track_identity_profile_usage(identity.id, "imap_test"):
            ok, message = await test_imap_connection(identity)
    except IdentityProfileRetiringError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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


@router.post(
    "/{identity_id}/template-import", response_model=IdentityTemplateImportResult
)
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
    return (
        select(IdentityProfile)
        .options(selectinload(IdentityProfile.current_primary_material))
        .where(IdentityProfile.deleted_at.is_(None))
    )


async def _serialize_identity_with_global_materials(
    session: AsyncSession,
    identity: IdentityProfile,
) -> IdentityProfileRead:
    return serialize_identity(
        identity,
        await list_global_material_metadata(session),
        await _get_global_default_outreach_template(session),
    )


async def _get_global_default_outreach_template(
    session: AsyncSession,
) -> OutreachTemplate | None:
    return await session.scalar(
        select(OutreachTemplate)
        .where(
            OutreachTemplate.is_default.is_(True),
            OutreachTemplate.archived_at.is_(None),
        )
        .order_by(OutreachTemplate.id.asc())
        .limit(1),
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
    result = await session.execute(
        select(IdentityProfile).where(IdentityProfile.deleted_at.is_(None))
    )
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
        IdentityProfile.deleted_at.is_(None),
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
        or "uq_identity_profiles_active_email_address" in message
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
    outreach_generation_mode = (
        str(
            data.get("outreach_generation_mode") or OUTREACH_GENERATION_MODE_LLM,
        )
        .strip()
        .lower()
    )

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
