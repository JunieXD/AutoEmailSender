from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import as_utc_aware, utc_now
from app.models import (
    EmailDeliveryAttempt,
    EmailDeliveryAttemptStatus,
    EmailDirection,
    EmailLog,
    EmailLogRecordState,
    EmailObservation,
    EmailObservationResolution,
    EmailTask,
)
from .addresses import normalize_email_address, normalize_email_list


AUTOMATIC_FOLD_WINDOW = timedelta(minutes=30)
AUTOMATIC_FOLD_BODY_SIMILARITY = 0.9
AUTOMATIC_FOLD_CONTAINMENT_MIN_CHARS = 12
AUTOMATIC_FOLD_CONTAINMENT_MIN_RATIO = 0.3


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
    delivery_key: str | None = None


@dataclass(frozen=True)
class SentObservationIngestResult:
    observation: EmailObservation
    email_log: EmailLog | None
    email_task_id: int | None
    resolution: str
    match_method: str | None


@dataclass(frozen=True)
class _AutomaticFoldCandidate:
    email_log: EmailLog | None
    delivery_attempt: EmailDeliveryAttempt | None
    body_similarity: float
    time_delta_seconds: float


def normalize_message_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def normalize_reconciliation_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(
        " ".join(line.split())
        for line in normalized.split("\n")
        if line.strip()
    ).strip()


def build_reconciliation_fingerprint(value: str | None) -> str:
    digest = hashlib.sha256(normalize_reconciliation_text(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def normalize_delivery_key(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    try:
        return str(uuid.UUID(normalized))
    except ValueError:
        return None


def build_message_fingerprint(record: EmailLogIngestRecord) -> str:
    normalized_from = normalize_email_address(record.from_email)
    recipients = {
        "to": normalize_email_list(record.to_emails),
        "cc": normalize_email_list(record.cc_emails),
        "bcc": normalize_email_list(record.bcc_emails),
    }
    content = record.content or ""
    content_html = record.content_html or ""
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    content_html_hash = hashlib.sha256(content_html.encode("utf-8")).hexdigest()
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
        "content_html_hash": content_html_hash,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()
    return f"sha256:{digest}"


async def upsert_email_log(session: AsyncSession, record: EmailLogIngestRecord) -> EmailLog:
    normalized_message_id = normalize_message_id(record.message_id)
    message_fingerprint = _message_fingerprint_or_none(record, normalized_message_id)
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


async def ensure_delivery_email_log(
    session: AsyncSession,
    *,
    delivery_attempt_id: str,
    record: EmailLogIngestRecord,
    failure_summary: str | None = None,
) -> tuple[EmailLog, bool]:
    existing = await session.scalar(
        select(EmailLog).where(
            EmailLog.delivery_attempt_id == delivery_attempt_id,
            EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
        ),
    )
    if existing is not None:
        _merge_email_log(
            existing,
            record,
            normalize_message_id(record.message_id),
            None,
        )
        return existing, False

    values = {
        "delivery_attempt_id": delivery_attempt_id,
        "record_state": EmailLogRecordState.CANONICAL.value,
        "reconciliation_version": 1,
        "email_task_id": record.email_task_id,
        "identity_id": record.identity_id,
        "llm_profile_id": record.llm_profile_id,
        "professor_id": record.professor_id,
        "direction": str(record.direction),
        "subject": record.subject,
        "content": record.content or "",
        "content_html": record.content_html,
        "rfc_message_id": record.message_id,
        "ingest_source": record.ingest_source,
        "folder_role": record.folder_role,
        "folder": record.folder,
        "uidvalidity": record.uidvalidity,
        "imap_uid": record.imap_uid,
        "normalized_message_id": normalize_message_id(record.message_id),
        "message_fingerprint": None,
        "from_email": _normalized_address_or_none(record.from_email),
        "to_emails": _normalized_list_or_none(record.to_emails),
        "cc_emails": _normalized_list_or_none(record.cc_emails),
        "bcc_emails": _normalized_list_or_none(record.bcc_emails),
        "synced_at": utc_now(),
        "provider_payload": record.provider_payload,
        "failure_summary": failure_summary,
        "reply_headers": record.reply_headers,
        "created_at": record.created_at,
    }
    dialect_name = session.get_bind().dialect.name
    if dialect_name == "sqlite":
        statement = sqlite_insert(EmailLog.__table__).values(**values)
    elif dialect_name == "postgresql":
        statement = postgresql_insert(EmailLog.__table__).values(**values)
    else:
        raise RuntimeError(f"Unsupported email delivery database dialect: {dialect_name}")
    inserted_id = await session.scalar(
        statement.on_conflict_do_nothing().returning(EmailLog.id),
    )
    email_log = await session.scalar(
        select(EmailLog).where(
            EmailLog.delivery_attempt_id == delivery_attempt_id,
            EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
        ),
    )
    if email_log is None:
        email_log = await _find_canonical_log_by_message_id(
            session,
            record,
            normalize_message_id(record.message_id),
        )
        if email_log is None:
            raise RuntimeError("Delivery log insert was ignored without a matching canonical log")
        email_log.delivery_attempt_id = delivery_attempt_id
    _merge_email_log(
        email_log,
        record,
        normalize_message_id(record.message_id),
        None,
    )
    await session.flush()
    return email_log, inserted_id is not None


async def ingest_sent_email_observation(
    session: AsyncSession,
    record: EmailLogIngestRecord,
) -> SentObservationIngestResult:
    if str(record.direction) != EmailDirection.SENT.value:
        raise ValueError("sent email observation requires direction='sent'")

    existing_observation = await _find_existing_observation(session, record)
    if existing_observation is not None:
        existing_log = (
            await session.get(EmailLog, existing_observation.email_log_id)
            if existing_observation.email_log_id is not None
            else None
        )
        return SentObservationIngestResult(
            observation=existing_observation,
            email_log=existing_log,
            email_task_id=(
                existing_log.email_task_id
                if existing_log is not None
                else await _observation_attempt_task_id(session, existing_observation)
                if existing_observation.resolution
                == EmailObservationResolution.MATCHED.value
                else None
            ),
            resolution=existing_observation.resolution,
            match_method=existing_observation.match_method,
        )

    delivery_key = normalize_delivery_key(record.delivery_key) or _delivery_key_from_headers(
        record.reply_headers,
    )
    normalized_message_id = normalize_message_id(record.message_id)
    observed_at = utc_now()
    observation = EmailObservation(
        identity_id=record.identity_id,
        professor_id=record.professor_id,
        direction=EmailDirection.SENT.value,
        source=record.ingest_source,
        resolution=EmailObservationResolution.PENDING.value,
        delivery_key=delivery_key,
        message_id=record.message_id,
        normalized_message_id=normalized_message_id,
        folder_role=record.folder_role,
        folder=record.folder,
        uidvalidity=record.uidvalidity,
        imap_uid=record.imap_uid,
        from_email=_normalized_address_or_none(record.from_email),
        to_emails=_normalized_list_or_none(record.to_emails),
        cc_emails=_normalized_list_or_none(record.cc_emails),
        bcc_emails=_normalized_list_or_none(record.bcc_emails),
        subject=record.subject,
        content=record.content or "",
        content_html=record.content_html,
        subject_fingerprint=build_reconciliation_fingerprint(record.subject),
        content_fingerprint=build_reconciliation_fingerprint(record.content),
        message_sent_at=record.created_at,
        observed_at=observed_at,
        headers=record.reply_headers,
        provider_payload=record.provider_payload,
    )
    session.add(observation)
    await session.flush()

    attempt = await _find_attempt_by_delivery_key(
        session,
        delivery_key=delivery_key,
        identity_id=record.identity_id,
        professor_id=record.professor_id,
    )
    if attempt is not None:
        observation.delivery_attempt_id = attempt.id
        matched_log = await session.scalar(
            select(EmailLog).where(
                EmailLog.delivery_attempt_id == attempt.id,
                EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
            ),
        )
        if matched_log is None:
            task = (
                await session.get(EmailTask, attempt.email_task_id)
                if attempt.email_task_id is not None
                else None
            )
            recovered_record = replace(
                record,
                email_task_id=attempt.email_task_id,
                llm_profile_id=task.llm_profile_id if task is not None else None,
            )
            matched_log, _ = await ensure_delivery_email_log(
                session,
                delivery_attempt_id=attempt.id,
                record=recovered_record,
            )
        matched_log.failure_summary = None
        attempt.status = EmailDeliveryAttemptStatus.ACCEPTED.value
        attempt.completed_at = record.created_at
        return await _match_observation(
            session,
            observation,
            matched_log,
            record,
            match_method="delivery_key",
        )

    if delivery_key is not None:
        observation.match_method = "unknown_delivery_key"
        await session.flush()
        return SentObservationIngestResult(
            observation=observation,
            email_log=None,
            email_task_id=None,
            resolution=EmailObservationResolution.PENDING.value,
            match_method=observation.match_method,
        )

    exact_log = await _find_canonical_log_by_message_id(
        session,
        record,
        normalized_message_id,
    )
    if exact_log is not None:
        return await _match_observation(
            session,
            observation,
            exact_log,
            record,
            match_method="message_id",
        )

    aliased_log = await _find_log_by_observed_message_id(
        session,
        record,
        normalized_message_id,
    )
    if aliased_log is not None:
        return await _match_observation(
            session,
            observation,
            aliased_log,
            record,
            match_method="observed_message_id",
        )

    pending_copy = await _find_pending_fold_by_observed_message_id(
        session,
        record,
        normalized_message_id,
    )
    if pending_copy is not None:
        observation.match_method = "automatic_fold_same_message_copy"
        await session.flush()
        return SentObservationIngestResult(
            observation=observation,
            email_log=None,
            email_task_id=None,
            resolution=EmailObservationResolution.PENDING.value,
            match_method=observation.match_method,
        )

    exact_task = await _find_task_by_message_id(session, record, normalized_message_id)
    if exact_task is not None:
        task_record = replace(
            record,
            email_task_id=exact_task.id,
            llm_profile_id=exact_task.llm_profile_id,
        )
        task_log = await upsert_email_log(session, task_record)
        return await _match_observation(
            session,
            observation,
            task_log,
            task_record,
            match_method="task_message_id",
        )

    automatic_fold = await _find_automatic_fold_candidate(
        session,
        record,
        observation,
    )
    if automatic_fold is not None:
        if automatic_fold.email_log is not None:
            observation.candidate_email_log_id = automatic_fold.email_log.id
        if automatic_fold.delivery_attempt is not None:
            observation.delivery_attempt_id = automatic_fold.delivery_attempt.id
        observation.match_method = (
            "automatic_fold_exact_body"
            if automatic_fold.body_similarity == 1.0
            else "automatic_fold_similar_body"
        )
        await session.flush()
        return SentObservationIngestResult(
            observation=observation,
            email_log=None,
            email_task_id=None,
            resolution=EmailObservationResolution.PENDING.value,
            match_method=observation.match_method,
        )

    external_log = await upsert_email_log(session, record)
    observation.email_log_id = external_log.id
    observation.resolution = EmailObservationResolution.EXTERNAL.value
    observation.match_method = "no_app_send_candidate"
    await session.flush()
    return SentObservationIngestResult(
        observation=observation,
        email_log=external_log,
        email_task_id=external_log.email_task_id,
        resolution=observation.resolution,
        match_method=observation.match_method,
    )


async def attach_delivery_observations(
    session: AsyncSession,
    *,
    delivery_attempt_id: str,
    email_log: EmailLog,
) -> None:
    observations = list(
        await session.scalars(
            select(EmailObservation).where(
                EmailObservation.delivery_attempt_id == delivery_attempt_id,
                EmailObservation.resolution == EmailObservationResolution.PENDING.value,
            ),
        ),
    )
    for observation in observations:
        observation.email_log_id = email_log.id
        observation.candidate_email_log_id = None
        observation.resolution = EmailObservationResolution.MATCHED.value
        observation.match_method = "delivery_key"


async def release_delivery_observation_candidates(
    session: AsyncSession,
    *,
    delivery_attempt_id: str,
) -> None:
    observations = list(
        await session.scalars(
            select(EmailObservation).where(
                EmailObservation.delivery_attempt_id == delivery_attempt_id,
                EmailObservation.resolution == EmailObservationResolution.PENDING.value,
            ),
        ),
    )
    await _externalize_observation_group(session, observations)


async def _find_existing_observation(
    session: AsyncSession,
    record: EmailLogIngestRecord,
) -> EmailObservation | None:
    if not _has_imap_location(record):
        return None
    return await session.scalar(
        select(EmailObservation).where(
            EmailObservation.identity_id == record.identity_id,
            EmailObservation.professor_id == record.professor_id,
            EmailObservation.folder_role == record.folder_role,
            EmailObservation.folder == record.folder,
            EmailObservation.uidvalidity == record.uidvalidity,
            EmailObservation.imap_uid == record.imap_uid,
        ),
    )


async def _observation_attempt_task_id(
    session: AsyncSession,
    observation: EmailObservation,
) -> int | None:
    if observation.delivery_attempt_id is None:
        return None
    attempt = await session.get(EmailDeliveryAttempt, observation.delivery_attempt_id)
    return attempt.email_task_id if attempt is not None else None


def _delivery_key_from_headers(headers: dict[str, Any] | None) -> str | None:
    if not headers:
        return None
    for key, value in headers.items():
        normalized_key = str(key).strip().lower().replace("_", "-")
        if normalized_key == "x-autoemailsender-delivery-id":
            return normalize_delivery_key(str(value))
    return None


async def _find_attempt_by_delivery_key(
    session: AsyncSession,
    *,
    delivery_key: str | None,
    identity_id: int,
    professor_id: int,
) -> EmailDeliveryAttempt | None:
    if delivery_key is None:
        return None
    return await session.scalar(
        select(EmailDeliveryAttempt).where(
            EmailDeliveryAttempt.id == delivery_key,
            EmailDeliveryAttempt.identity_id == identity_id,
            EmailDeliveryAttempt.professor_id == professor_id,
        ),
    )


async def _find_canonical_log_by_message_id(
    session: AsyncSession,
    record: EmailLogIngestRecord,
    normalized_message_id: str | None,
) -> EmailLog | None:
    if normalized_message_id is None:
        return None
    return await session.scalar(
        select(EmailLog).where(
            EmailLog.identity_id == record.identity_id,
            EmailLog.professor_id == record.professor_id,
            EmailLog.direction == EmailDirection.SENT.value,
            EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
            or_(
                EmailLog.normalized_message_id == normalized_message_id,
                func.lower(func.trim(EmailLog.rfc_message_id)) == normalized_message_id,
            ),
        ),
    )


async def _find_log_by_observed_message_id(
    session: AsyncSession,
    record: EmailLogIngestRecord,
    normalized_message_id: str | None,
) -> EmailLog | None:
    if normalized_message_id is None:
        return None
    observation = await session.scalar(
        select(EmailObservation).where(
            EmailObservation.identity_id == record.identity_id,
            EmailObservation.professor_id == record.professor_id,
            EmailObservation.direction == EmailDirection.SENT.value,
            EmailObservation.normalized_message_id == normalized_message_id,
            EmailObservation.email_log_id.is_not(None),
            EmailObservation.resolution.in_(
                [
                    EmailObservationResolution.MATCHED.value,
                    EmailObservationResolution.EXTERNAL.value,
                ],
            ),
        ),
    )
    if observation is None or observation.email_log_id is None:
        return None
    email_log = await session.get(EmailLog, observation.email_log_id)
    if email_log is None or email_log.record_state != EmailLogRecordState.CANONICAL.value:
        return None
    return email_log


async def _find_pending_fold_by_observed_message_id(
    session: AsyncSession,
    record: EmailLogIngestRecord,
    normalized_message_id: str | None,
) -> EmailObservation | None:
    if normalized_message_id is None:
        return None
    return await session.scalar(
        select(EmailObservation)
        .where(
            EmailObservation.identity_id == record.identity_id,
            EmailObservation.professor_id == record.professor_id,
            EmailObservation.direction == EmailDirection.SENT.value,
            EmailObservation.normalized_message_id == normalized_message_id,
            EmailObservation.resolution == EmailObservationResolution.PENDING.value,
            or_(
                EmailObservation.candidate_email_log_id.is_not(None),
                EmailObservation.delivery_attempt_id.is_not(None),
            ),
        )
        .order_by(EmailObservation.id),
    )


async def _find_task_by_message_id(
    session: AsyncSession,
    record: EmailLogIngestRecord,
    normalized_message_id: str | None,
) -> EmailTask | None:
    if normalized_message_id is None:
        return None
    return await session.scalar(
        select(EmailTask)
        .where(
            EmailTask.identity_id == record.identity_id,
            EmailTask.professor_id == record.professor_id,
            func.lower(func.trim(EmailTask.last_rfc_message_id)) == normalized_message_id,
        )
        .order_by(EmailTask.updated_at.desc(), EmailTask.id.desc()),
    )


async def _find_automatic_fold_candidate(
    session: AsyncSession,
    record: EmailLogIngestRecord,
    observation: EmailObservation,
) -> _AutomaticFoldCandidate | None:
    candidates = await _load_automatic_fold_candidates(session, record, observation)
    for candidate in candidates:
        if await _reserve_automatic_fold_candidate(session, observation, candidate):
            return candidate
    return None


async def _load_automatic_fold_candidates(
    session: AsyncSession,
    record: EmailLogIngestRecord,
    observation: EmailObservation,
) -> list[_AutomaticFoldCandidate]:
    sent_at = as_utc_aware(record.created_at)
    window_start = sent_at - AUTOMATIC_FOLD_WINDOW
    window_end = sent_at + AUTOMATIC_FOLD_WINDOW
    recipient_set = _record_recipient_set(record)
    attempts = list(
        await session.scalars(
            select(EmailDeliveryAttempt).where(
                EmailDeliveryAttempt.identity_id == record.identity_id,
                EmailDeliveryAttempt.professor_id == record.professor_id,
                EmailDeliveryAttempt.started_at >= window_start,
                EmailDeliveryAttempt.started_at <= window_end,
                EmailDeliveryAttempt.subject_fingerprint == observation.subject_fingerprint,
                EmailDeliveryAttempt.status.in_(
                    [
                        EmailDeliveryAttemptStatus.PREPARED.value,
                        EmailDeliveryAttemptStatus.ACCEPTED.value,
                        EmailDeliveryAttemptStatus.UNKNOWN.value,
                    ],
                ),
            ),
        ),
    )
    if recipient_set:
        attempts = [
            attempt
            for attempt in attempts
            if normalize_email_address(attempt.recipient_email) in recipient_set
        ]

    logs_by_attempt_id: dict[str, EmailLog] = {}
    attempt_ids = [attempt.id for attempt in attempts]
    if attempt_ids:
        linked_logs = await session.scalars(
            select(EmailLog).where(
                EmailLog.delivery_attempt_id.in_(attempt_ids),
                EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
            ),
        )
        logs_by_attempt_id = {
            log.delivery_attempt_id: log
            for log in linked_logs
            if log.delivery_attempt_id is not None
        }

    candidates: list[_AutomaticFoldCandidate] = []
    included_log_ids: set[int] = set()
    for attempt in attempts:
        email_log = logs_by_attempt_id.get(attempt.id)
        if email_log is not None:
            if (email_log.failure_summary or "").strip():
                continue
            if not _log_addresses_match(email_log, record):
                continue
            body_similarity = _reconciliation_body_similarity(
                email_log.content,
                observation.content,
            )
            candidate_time = email_log.created_at
            included_log_ids.add(email_log.id)
        else:
            body_similarity = (
                1.0
                if attempt.content_fingerprint == observation.content_fingerprint
                and bool(normalize_reconciliation_text(observation.content))
                else 0.0
            )
            candidate_time = attempt.started_at
        if body_similarity < AUTOMATIC_FOLD_BODY_SIMILARITY:
            continue
        candidates.append(
            _AutomaticFoldCandidate(
                email_log=email_log,
                delivery_attempt=attempt,
                body_similarity=body_similarity,
                time_delta_seconds=abs(
                    (as_utc_aware(candidate_time) - sent_at).total_seconds(),
                ),
            ),
        )

    standalone_logs = list(
        await session.scalars(
            select(EmailLog).where(
                EmailLog.identity_id == record.identity_id,
                EmailLog.professor_id == record.professor_id,
                EmailLog.direction == EmailDirection.SENT.value,
                EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
                EmailLog.ingest_source != "imap",
                func.trim(func.coalesce(EmailLog.failure_summary, "")) == "",
                EmailLog.created_at >= window_start,
                EmailLog.created_at <= window_end,
            ),
        ),
    )
    for email_log in standalone_logs:
        if email_log.id in included_log_ids:
            continue
        if (
            build_reconciliation_fingerprint(email_log.subject)
            != observation.subject_fingerprint
            or not _log_addresses_match(email_log, record)
        ):
            continue
        body_similarity = _reconciliation_body_similarity(
            email_log.content,
            observation.content,
        )
        if body_similarity < AUTOMATIC_FOLD_BODY_SIMILARITY:
            continue
        linked_attempt = (
            await session.get(EmailDeliveryAttempt, email_log.delivery_attempt_id)
            if email_log.delivery_attempt_id is not None
            else None
        )
        candidates.append(
            _AutomaticFoldCandidate(
                email_log=email_log,
                delivery_attempt=linked_attempt,
                body_similarity=body_similarity,
                time_delta_seconds=abs(
                    (as_utc_aware(email_log.created_at) - sent_at).total_seconds(),
                ),
            ),
        )

    return sorted(candidates, key=_automatic_fold_candidate_rank)


def _automatic_fold_candidate_rank(
    candidate: _AutomaticFoldCandidate,
) -> tuple[float, float, str]:
    stable_id = (
        f"log:{candidate.email_log.id}"
        if candidate.email_log is not None
        else f"attempt:{candidate.delivery_attempt.id}"
        if candidate.delivery_attempt is not None
        else ""
    )
    return (-candidate.body_similarity, candidate.time_delta_seconds, stable_id)


async def _reserve_automatic_fold_candidate(
    session: AsyncSession,
    observation: EmailObservation,
    candidate: _AutomaticFoldCandidate,
) -> bool:
    occupancy_conditions = []
    if candidate.email_log is not None:
        occupancy_conditions.extend(
            [
                EmailObservation.candidate_email_log_id == candidate.email_log.id,
                EmailObservation.email_log_id == candidate.email_log.id,
            ],
        )
    if candidate.delivery_attempt is not None:
        occupancy_conditions.append(
            EmailObservation.delivery_attempt_id == candidate.delivery_attempt.id,
        )
    if not occupancy_conditions:
        return False

    occupants = list(
        await session.scalars(
            select(EmailObservation).where(
                EmailObservation.id != observation.id,
                EmailObservation.direction == EmailDirection.SENT.value,
                EmailObservation.resolution.in_(
                    [
                        EmailObservationResolution.MATCHED.value,
                        EmailObservationResolution.PENDING.value,
                    ],
                ),
                or_(*occupancy_conditions),
            ),
        ),
    )
    observation_group = _observation_group_key(observation)
    if any(
        occupant.resolution == EmailObservationResolution.MATCHED.value
        and _observation_group_key(occupant) != observation_group
        for occupant in occupants
    ):
        return False

    weak_groups: dict[tuple[str, object], list[EmailObservation]] = {}
    for occupant in occupants:
        if occupant.resolution != EmailObservationResolution.PENDING.value:
            continue
        weak_groups.setdefault(_observation_group_key(occupant), []).append(occupant)
    if observation_group in weak_groups:
        return True

    ranked_groups: list[
        tuple[tuple[float, float, int], tuple[str, object], list[EmailObservation]]
    ] = []
    for group_key, group in weak_groups.items():
        representative = min(group, key=lambda item: item.id)
        rank = _automatic_fold_observation_rank(candidate, representative)
        if rank is None:
            await _externalize_observation_group(session, group)
            continue
        ranked_groups.append((rank, group_key, group))
    ranked_groups.sort(key=lambda item: item[0])
    for _, _, extra_group in ranked_groups[1:]:
        await _externalize_observation_group(session, extra_group)

    new_rank = _automatic_fold_observation_rank(candidate, observation)
    if new_rank is None:
        return False
    if not ranked_groups:
        return True
    existing_rank, _, existing_group = ranked_groups[0]
    if new_rank >= existing_rank:
        return False
    await _externalize_observation_group(session, existing_group)
    return True


def _automatic_fold_observation_rank(
    candidate: _AutomaticFoldCandidate,
    observation: EmailObservation,
) -> tuple[float, float, int] | None:
    if candidate.email_log is not None:
        body_similarity = _reconciliation_body_similarity(
            candidate.email_log.content,
            observation.content,
        )
        candidate_time = candidate.email_log.created_at
    elif candidate.delivery_attempt is not None:
        body_similarity = (
            1.0
            if candidate.delivery_attempt.content_fingerprint
            == observation.content_fingerprint
            and bool(normalize_reconciliation_text(observation.content))
            else 0.0
        )
        candidate_time = candidate.delivery_attempt.started_at
    else:
        return None
    if body_similarity < AUTOMATIC_FOLD_BODY_SIMILARITY:
        return None
    time_delta = abs(
        (
            as_utc_aware(candidate_time)
            - as_utc_aware(observation.message_sent_at)
        ).total_seconds(),
    )
    if time_delta > AUTOMATIC_FOLD_WINDOW.total_seconds():
        return None
    return (-body_similarity, time_delta, observation.id)


def _reconciliation_body_similarity(left: str | None, right: str | None) -> float:
    normalized_left = normalize_reconciliation_text(left).casefold()
    normalized_right = normalize_reconciliation_text(right).casefold()
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0
    shorter, longer = sorted(
        (normalized_left, normalized_right),
        key=len,
    )
    if (
        len(shorter) >= AUTOMATIC_FOLD_CONTAINMENT_MIN_CHARS
        and len(shorter) / len(longer) >= AUTOMATIC_FOLD_CONTAINMENT_MIN_RATIO
        and shorter in longer
    ):
        return 0.97
    return SequenceMatcher(None, normalized_left, normalized_right, autojunk=False).ratio()


def _log_addresses_match(email_log: EmailLog, record: EmailLogIngestRecord) -> bool:
    record_sender = normalize_email_address(record.from_email)
    log_sender = normalize_email_address(email_log.from_email)
    if record_sender and log_sender and record_sender != log_sender:
        return False
    record_recipients = _record_recipient_set(record)
    log_recipients = set(
        normalize_email_list(
            [
                *(email_log.to_emails or []),
                *(email_log.cc_emails or []),
                *(email_log.bcc_emails or []),
            ],
        ),
    )
    return not record_recipients or not log_recipients or record_recipients == log_recipients


def _observation_group_key(observation: EmailObservation) -> tuple[str, object]:
    if observation.normalized_message_id:
        return "message_id", observation.normalized_message_id
    return "observation", observation.id


async def _externalize_observation_group(
    session: AsyncSession,
    observations: list[EmailObservation],
) -> None:
    expanded: dict[int, EmailObservation] = {item.id: item for item in observations}
    for observation in observations:
        if observation.normalized_message_id is None:
            continue
        copies = await session.scalars(
            select(EmailObservation).where(
                EmailObservation.identity_id == observation.identity_id,
                EmailObservation.professor_id == observation.professor_id,
                EmailObservation.direction == observation.direction,
                EmailObservation.normalized_message_id
                == observation.normalized_message_id,
                EmailObservation.resolution == EmailObservationResolution.PENDING.value,
            ),
        )
        expanded.update({copy.id: copy for copy in copies})
    for observation in expanded.values():
        await _externalize_observation(session, observation)


async def _externalize_observation(
    session: AsyncSession,
    observation: EmailObservation,
) -> EmailLog:
    legacy_log = (
        await session.get(EmailLog, observation.legacy_email_log_id)
        if observation.legacy_email_log_id is not None
        else None
    )
    if legacy_log is not None:
        legacy_log.record_state = EmailLogRecordState.CANONICAL.value
        legacy_log.merged_into_id = None
        email_log = legacy_log
    else:
        if observation.professor_id is None:
            raise RuntimeError("Sent observation cannot become external without a professor")
        email_log = await upsert_email_log(
            session,
            EmailLogIngestRecord(
                identity_id=observation.identity_id,
                professor_id=observation.professor_id,
                direction=observation.direction,
                subject=observation.subject,
                content=observation.content,
                content_html=observation.content_html,
                message_id=observation.message_id,
                from_email=observation.from_email,
                to_emails=observation.to_emails,
                cc_emails=observation.cc_emails,
                bcc_emails=observation.bcc_emails,
                created_at=observation.message_sent_at,
                ingest_source=observation.source,
                folder_role=observation.folder_role,
                folder=observation.folder,
                uidvalidity=observation.uidvalidity,
                imap_uid=observation.imap_uid,
                email_task_id=None,
                llm_profile_id=None,
                provider_payload=observation.provider_payload,
                reply_headers=observation.headers,
            ),
        )
    observation.email_log_id = email_log.id
    observation.candidate_email_log_id = None
    observation.delivery_attempt_id = None
    observation.resolution = EmailObservationResolution.EXTERNAL.value
    observation.match_method = "automatic_fold_released_external"
    return email_log


def _record_recipient_set(record: EmailLogIngestRecord) -> set[str]:
    return set(
        normalize_email_list(
            [
                *(record.to_emails or []),
                *(record.cc_emails or []),
                *(record.bcc_emails or []),
            ],
        ),
    )


async def _match_observation(
    session: AsyncSession,
    observation: EmailObservation,
    email_log: EmailLog,
    record: EmailLogIngestRecord,
    *,
    match_method: str,
) -> SentObservationIngestResult:
    await _release_conflicting_automatic_folds(session, observation, email_log)
    observation.email_log_id = email_log.id
    observation.candidate_email_log_id = None
    observation.delivery_attempt_id = email_log.delivery_attempt_id
    observation.resolution = EmailObservationResolution.MATCHED.value
    observation.match_method = match_method
    _merge_email_log(
        email_log,
        record,
        normalize_message_id(record.message_id),
        None,
    )
    await session.flush()
    return SentObservationIngestResult(
        observation=observation,
        email_log=email_log,
        email_task_id=email_log.email_task_id,
        resolution=observation.resolution,
        match_method=match_method,
    )


async def _release_conflicting_automatic_folds(
    session: AsyncSession,
    matched_observation: EmailObservation,
    email_log: EmailLog,
) -> None:
    conditions = [EmailObservation.candidate_email_log_id == email_log.id]
    if email_log.delivery_attempt_id is not None:
        conditions.append(
            EmailObservation.delivery_attempt_id == email_log.delivery_attempt_id,
        )
    pending = list(
        await session.scalars(
            select(EmailObservation).where(
                EmailObservation.id != matched_observation.id,
                EmailObservation.direction == EmailDirection.SENT.value,
                EmailObservation.resolution == EmailObservationResolution.PENDING.value,
                or_(*conditions),
            ),
        ),
    )
    matched_group = _observation_group_key(matched_observation)
    conflicting = [
        observation
        for observation in pending
        if _observation_group_key(observation) != matched_group
    ]
    await _externalize_observation_group(session, conflicting)


async def _find_existing(
    session: AsyncSession,
    record: EmailLogIngestRecord,
    normalized_message_id: str | None,
    message_fingerprint: str | None,
) -> EmailLog | None:
    if normalized_message_id:
        by_message_id = await session.scalar(
            select(EmailLog).where(
                EmailLog.identity_id == record.identity_id,
                EmailLog.professor_id == record.professor_id,
                EmailLog.direction == str(record.direction),
                EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
                EmailLog.normalized_message_id == normalized_message_id,
            ),
        )
        if by_message_id is not None:
            return by_message_id

        by_rfc_message_id = await _find_by_normalized_rfc_message_id(session, record, normalized_message_id)
        if by_rfc_message_id is not None:
            return by_rfc_message_id

    if _has_imap_location(record):
        by_imap_location = await session.scalar(
            select(EmailLog).where(
                EmailLog.identity_id == record.identity_id,
                EmailLog.professor_id == record.professor_id,
                EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
                EmailLog.folder_role == record.folder_role,
                EmailLog.folder == record.folder,
                EmailLog.uidvalidity == record.uidvalidity,
                EmailLog.imap_uid == record.imap_uid,
            ),
        )
        if by_imap_location is not None:
            return by_imap_location

    if message_fingerprint is None:
        return None

    return await session.scalar(
        select(EmailLog).where(
            EmailLog.identity_id == record.identity_id,
            EmailLog.professor_id == record.professor_id,
            EmailLog.direction == str(record.direction),
            EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
            EmailLog.message_fingerprint == message_fingerprint,
        ),
    )


def _merge_email_log(
    existing: EmailLog,
    record: EmailLogIngestRecord,
    normalized_message_id: str | None,
    message_fingerprint: str | None,
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
    _merge_dict_attr(existing, "provider_payload", record.provider_payload)
    _merge_dict_attr(existing, "reply_headers", record.reply_headers)
    existing.synced_at = utc_now()


async def _find_by_normalized_rfc_message_id(
    session: AsyncSession,
    record: EmailLogIngestRecord,
    normalized_message_id: str,
) -> EmailLog | None:
    candidates = await session.scalars(
        select(EmailLog).where(
            EmailLog.identity_id == record.identity_id,
            EmailLog.professor_id == record.professor_id,
            EmailLog.direction == str(record.direction),
            EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
            EmailLog.rfc_message_id.is_not(None),
            EmailLog.normalized_message_id.is_(None),
        ),
    )
    for candidate in candidates:
        if normalize_message_id(candidate.rfc_message_id) == normalized_message_id:
            return candidate
    return None


def _fill_attr(existing: EmailLog, attr_name: str, value: object | None) -> None:
    if value is None:
        return
    current = getattr(existing, attr_name)
    if current is None or current == "" or current == []:
        setattr(existing, attr_name, value)


def _merge_dict_attr(existing: EmailLog, attr_name: str, value: dict[str, Any] | None) -> None:
    if value is None:
        return

    current = getattr(existing, attr_name)
    if current is None or current == {}:
        setattr(existing, attr_name, value)
        return

    merged = dict(value)
    merged.update(current)
    setattr(existing, attr_name, merged)


def _message_fingerprint_or_none(record: EmailLogIngestRecord, normalized_message_id: str | None) -> str | None:
    if normalized_message_id is not None or _has_imap_location(record):
        return None
    return build_message_fingerprint(record)


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
