from __future__ import annotations

import re
from datetime import UTC, datetime

from app.core.time import utc_now

from time import perf_counter

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.identity_serializers import serialize_identity
from app.core.database import get_async_session
from app.models import IdentityProfile
from app.schemas.identity import (
    ConnectionTestResult,
    IdentityProfileCreate,
    IdentityProfileRead,
    IdentityProfileUpdate,
    IdentityTemplateImportResult,
)
from app.services.file_storage import delete_file
from app.services.mail_runtime import test_imap_connection, test_smtp_connection
from app.services.operation_logs import record_operation_log
from app.services.outreach_templates import (
    OUTREACH_GENERATION_MODE_LLM,
    OUTREACH_GENERATION_MODE_TEMPLATE,
    import_outreach_template_file,
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
    return [serialize_identity(identity) for identity in identities]


@router.post("", response_model=IdentityProfileRead, status_code=status.HTTP_201_CREATED)
async def create_identity(
    payload: IdentityProfileCreate,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityProfileRead:
    existing_count = await session.scalar(select(func.count(IdentityProfile.id)))
    data = _normalize_identity_payload(payload)
    await _ensure_identity_email_available(session, str(data["email_address"]))
    identity = IdentityProfile(**data)
    if not existing_count:
        identity.is_default = True
    elif payload.is_default:
        await _clear_default_identities(session)

    try:
        session.add(identity)
        await session.flush()
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
    await _ensure_identity_email_available(
        session,
        str(data["email_address"]),
        exclude_id=identity_id,
    )
    if data["is_default"]:
        await _clear_default_identities(session, exclude_id=identity_id)

    for key, value in data.items():
        setattr(identity, key, value)
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


@router.delete("/{identity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_identity(
    identity_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    identity = await _get_identity(session, identity_id)
    was_default = identity.is_default

    for material in identity.materials:
        delete_file(material.file_path)

    await _record_identity_log(
        session,
        identity,
        "identity.deleted",
        metadata={"was_default": was_default},
    )
    await session.delete(identity)
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
    return ConnectionTestResult(ok=ok, message=message, host=identity.smtp_host)


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


