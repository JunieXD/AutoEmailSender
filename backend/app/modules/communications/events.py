from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, selectinload

from app.core.query_chunks import chunked_values, unique_positive_ids
from app.models import EmailDirection, EmailLog, EmailLogRecordState, IdentityProfile


@dataclass(frozen=True)
class CommunicationEvent:
    id: int
    log: EmailLog
    logs: tuple[EmailLog, ...]
    source_identities: tuple[IdentityProfile, ...]
    created_at: datetime
    successful: bool


async def load_communication_events(
    session: AsyncSession,
    *,
    identity_ids: tuple[int, ...] | list[int],
    professor_ids: list[int] | tuple[int, ...] | None = None,
    include_message_content: bool = True,
    include_source_identities: bool = True,
    include_professors: bool = True,
) -> list[CommunicationEvent]:
    normalized_identity_ids = unique_positive_ids(identity_ids)
    if not normalized_identity_ids:
        return []

    load_options = []
    if not include_message_content:
        load_options.append(
            load_only(
                EmailLog.id,
                EmailLog.email_task_id,
                EmailLog.identity_id,
                EmailLog.professor_id,
                EmailLog.direction,
                EmailLog.rfc_message_id,
                EmailLog.normalized_message_id,
                EmailLog.message_fingerprint,
                EmailLog.delivery_attempt_id,
                EmailLog.record_state,
                EmailLog.failure_summary,
                EmailLog.created_at,
            ),
        )
    if include_source_identities:
        load_options.append(selectinload(EmailLog.identity))
    if include_professors:
        load_options.append(selectinload(EmailLog.professor))

    normalized_professor_ids: list[int] | None = None
    if professor_ids is not None:
        normalized_professor_ids = unique_positive_ids(professor_ids)
        if not normalized_professor_ids:
            return []

    logs: list[EmailLog] = []
    professor_id_chunks: list[tuple[int, ...] | None] = (
        list(chunked_values(normalized_professor_ids))
        if normalized_professor_ids is not None
        else [None]
    )
    for identity_id_chunk in chunked_values(normalized_identity_ids):
        for professor_id_chunk in professor_id_chunks:
            statement = (
                select(EmailLog)
                .options(*load_options)
                .where(
                    EmailLog.identity_id.in_(identity_id_chunk),
                    EmailLog.direction.in_(
                        [EmailDirection.SENT.value, EmailDirection.RECEIVED.value],
                    ),
                    EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
                )
            )
            if professor_id_chunk is not None:
                statement = statement.where(
                    EmailLog.professor_id.in_(professor_id_chunk),
                )
            logs.extend(await session.scalars(statement))
    return collapse_communication_logs(
        logs,
        prefer_message_content=include_message_content,
        include_source_identities=include_source_identities,
    )


def collapse_communication_logs(
    logs: list[EmailLog],
    *,
    prefer_message_content: bool = True,
    include_source_identities: bool = True,
) -> list[CommunicationEvent]:
    grouped: dict[tuple[object, ...], list[EmailLog]] = {}
    for log in logs:
        if log.record_state != EmailLogRecordState.CANONICAL.value:
            continue
        grouped.setdefault(_event_key(log), []).append(log)

    events = [
        _build_event(
            group_logs,
            prefer_message_content=prefer_message_content,
            include_source_identities=include_source_identities,
        )
        for group_logs in grouped.values()
    ]
    return sorted(events, key=lambda event: (event.created_at, event.id))


def _event_key(log: EmailLog) -> tuple[object, ...]:
    if log.delivery_attempt_id:
        return (log.professor_id, log.direction, "delivery", log.delivery_attempt_id)

    normalized_message_id = _normalize_message_id(log.normalized_message_id)
    if normalized_message_id:
        return (log.professor_id, log.direction, "message", normalized_message_id)

    rfc_message_id = _normalize_message_id(log.rfc_message_id)
    if rfc_message_id:
        return (log.professor_id, log.direction, "message", rfc_message_id)

    fingerprint = (log.message_fingerprint or "").strip().lower()
    if fingerprint:
        return (log.professor_id, log.direction, "fingerprint", fingerprint)

    return (log.professor_id, log.direction, "log", log.id)


def _normalize_message_id(value: str | None) -> str:
    return (value or "").strip().lower().removeprefix("<").removesuffix(">")


def _build_event(
    logs: list[EmailLog],
    *,
    prefer_message_content: bool,
    include_source_identities: bool,
) -> CommunicationEvent:
    ordered_logs = sorted(logs, key=lambda log: (log.created_at, log.id))
    successful = any(
        log.direction != EmailDirection.SENT.value
        or not (log.failure_summary or "").strip()
        for log in ordered_logs
    )
    candidate_logs = (
        [log for log in ordered_logs if not (log.failure_summary or "").strip()]
        if successful and ordered_logs[0].direction == EmailDirection.SENT.value
        else ordered_logs
    )
    if prefer_message_content:
        canonical_log = max(
            candidate_logs or ordered_logs,
            key=lambda log: (
                bool((log.content_html or "").strip()),
                len((log.content_html or "").strip()),
                len((log.content or "").strip()),
                -log.id,
            ),
        )
    else:
        canonical_log = (candidate_logs or ordered_logs)[0]
    identities_by_id = (
        {log.identity.id: log.identity for log in ordered_logs}
        if include_source_identities
        else {}
    )
    return CommunicationEvent(
        id=min(log.id for log in ordered_logs),
        log=canonical_log,
        logs=tuple(ordered_logs),
        source_identities=tuple(
            identities_by_id[identity_id] for identity_id in sorted(identities_by_id)
        ),
        created_at=min(log.created_at for log in ordered_logs),
        successful=successful,
    )
