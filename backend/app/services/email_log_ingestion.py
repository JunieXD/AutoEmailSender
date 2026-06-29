from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import as_utc_aware, utc_now
from app.models import EmailLog
from app.services.email_addresses import normalize_email_address, normalize_email_list


@dataclass(frozen=True)
class EmailLogIngestRecord:
    identity_id: int
    professor_id: int
    direction: str
    subject: str | None
    content: str | None
    content_html: str | None
    message_id: str | None
    from_email: str | None
    to_emails: list[str] | tuple[str, ...] | None
    cc_emails: list[str] | tuple[str, ...] | None
    bcc_emails: list[str] | tuple[str, ...] | None
    created_at: datetime
    ingest_source: str
    folder_role: str | None
    folder: str | None
    uidvalidity: int | None
    imap_uid: int | None
    email_task_id: int | None
    llm_profile_id: int | None
    provider_payload: dict[str, Any] | None
    reply_headers: dict[str, Any] | None


def normalize_message_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def build_message_fingerprint(record: EmailLogIngestRecord) -> str:
    normalized_from = normalize_email_address(record.from_email)
    recipients = {
        "to": normalize_email_list(record.to_emails),
        "cc": normalize_email_list(record.cc_emails),
        "bcc": normalize_email_list(record.bcc_emails),
    }
    content = record.content or ""
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    created_at_minute = as_utc_aware(record.created_at).replace(second=0, microsecond=0).isoformat()
    payload = {
        "identity_id": record.identity_id,
        "professor_id": record.professor_id,
        "direction": str(record.direction),
        "from_email": normalized_from,
        "recipients": recipients,
        "created_at_minute": created_at_minute,
        "subject": record.subject or "",
        "content_hash": content_hash,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()
    return f"sha256:{digest}"


async def upsert_email_log(session: AsyncSession, record: EmailLogIngestRecord) -> EmailLog:
    normalized_message_id = normalize_message_id(record.message_id)
    message_fingerprint = build_message_fingerprint(record)
    existing = await _find_existing(session, record, normalized_message_id, message_fingerprint)

    if existing is None:
        email_log = EmailLog(
            email_task_id=record.email_task_id,
            identity_id=record.identity_id,
            llm_profile_id=record.llm_profile_id,
            professor_id=record.professor_id,
            direction=str(record.direction),
            subject=record.subject,
            content=record.content or "",
            content_html=record.content_html,
            rfc_message_id=record.message_id,
            ingest_source=record.ingest_source,
            folder_role=record.folder_role,
            folder=record.folder,
            uidvalidity=record.uidvalidity,
            imap_uid=record.imap_uid,
            normalized_message_id=normalized_message_id,
            message_fingerprint=message_fingerprint,
            from_email=_normalized_address_or_none(record.from_email),
            to_emails=_normalized_list_or_none(record.to_emails),
            cc_emails=_normalized_list_or_none(record.cc_emails),
            bcc_emails=_normalized_list_or_none(record.bcc_emails),
            synced_at=utc_now(),
            provider_payload=record.provider_payload,
            reply_headers=record.reply_headers,
            created_at=record.created_at,
        )
        session.add(email_log)
        await session.flush()
        return email_log

    _merge_email_log(existing, record, normalized_message_id, message_fingerprint)
    await session.flush()
    return existing


async def _find_existing(
    session: AsyncSession,
    record: EmailLogIngestRecord,
    normalized_message_id: str | None,
    message_fingerprint: str,
) -> EmailLog | None:
    if normalized_message_id:
        by_message_id = await session.scalar(
            select(EmailLog).where(
                EmailLog.identity_id == record.identity_id,
                EmailLog.professor_id == record.professor_id,
                EmailLog.direction == str(record.direction),
                EmailLog.normalized_message_id == normalized_message_id,
            ),
        )
        if by_message_id is not None:
            return by_message_id

    if _has_imap_location(record):
        by_imap_location = await session.scalar(
            select(EmailLog).where(
                EmailLog.identity_id == record.identity_id,
                EmailLog.professor_id == record.professor_id,
                EmailLog.folder_role == record.folder_role,
                EmailLog.folder == record.folder,
                EmailLog.uidvalidity == record.uidvalidity,
                EmailLog.imap_uid == record.imap_uid,
            ),
        )
        if by_imap_location is not None:
            return by_imap_location

    return await session.scalar(
        select(EmailLog).where(
            EmailLog.identity_id == record.identity_id,
            EmailLog.professor_id == record.professor_id,
            EmailLog.direction == str(record.direction),
            EmailLog.message_fingerprint == message_fingerprint,
        ),
    )


def _merge_email_log(
    existing: EmailLog,
    record: EmailLogIngestRecord,
    normalized_message_id: str | None,
    message_fingerprint: str,
) -> None:
    _fill_attr(existing, "email_task_id", record.email_task_id)
    _fill_attr(existing, "llm_profile_id", record.llm_profile_id)
    _fill_attr(existing, "subject", record.subject)
    _fill_attr(existing, "content", record.content)
    _fill_attr(existing, "content_html", record.content_html)
    _fill_attr(existing, "rfc_message_id", record.message_id)
    _fill_attr(existing, "normalized_message_id", normalized_message_id)
    _fill_attr(existing, "message_fingerprint", message_fingerprint)
    _fill_attr(existing, "from_email", _normalized_address_or_none(record.from_email))
    _fill_attr(existing, "to_emails", _normalized_list_or_none(record.to_emails))
    _fill_attr(existing, "cc_emails", _normalized_list_or_none(record.cc_emails))
    _fill_attr(existing, "bcc_emails", _normalized_list_or_none(record.bcc_emails))
    _fill_attr(existing, "folder_role", record.folder_role)
    _fill_attr(existing, "folder", record.folder)
    _fill_attr(existing, "uidvalidity", record.uidvalidity)
    _fill_attr(existing, "imap_uid", record.imap_uid)
    _fill_attr(existing, "provider_payload", record.provider_payload)
    _fill_attr(existing, "reply_headers", record.reply_headers)
    existing.synced_at = utc_now()


def _fill_attr(existing: EmailLog, attr_name: str, value: object | None) -> None:
    if value is None:
        return
    current = getattr(existing, attr_name)
    if current is None or current == "" or current == []:
        setattr(existing, attr_name, value)


def _has_imap_location(record: EmailLogIngestRecord) -> bool:
    return all(
        value is not None
        for value in (record.folder_role, record.folder, record.uidvalidity, record.imap_uid)
    )


def _normalized_address_or_none(value: str | None) -> str | None:
    normalized = normalize_email_address(value)
    return normalized or None


def _normalized_list_or_none(values: list[str] | tuple[str, ...] | None) -> list[str] | None:
    normalized = normalize_email_list(values)
    return normalized or None
