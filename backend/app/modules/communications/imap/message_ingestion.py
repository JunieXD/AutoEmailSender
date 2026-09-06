from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.query_chunks import chunked_values
from app.core.time import as_utc_aware, utc_now
from app.models import (
    EmailDirection,
    EmailLog,
    EmailLogRecordState,
    EmailObservation,
    EmailObservationResolution,
    EmailTask,
    EmailTaskStatus,
    Professor,
)

from .. import transport as mail_runtime
from ..addresses import normalize_email_address, normalize_email_list
from ..email_tasks import (
    EMAIL_TASK_RELATION_OPTIONS as TASK_RELATION_OPTIONS,
    load_email_task as _load_email_task,
    record_email_task_log as _record_email_task_log,
)
from ..ingestion import (
    EmailLogIngestRecord,
    ingest_sent_email_observation,
    upsert_email_log,
)
from ..transport import ReceivedEmail
from .fetcher import ImapFetchedMessage
from .state import commit_imap_identity_sync_session


async def _process_incoming_reply_messages(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    messages: list[ReceivedEmail],
    *,
    fetched_messages: list[ImapFetchedMessage] | None = None,
    folder_role: str = "inbox",
    folder: str = "INBOX",
) -> int:
    detected = 0
    fetched_by_message_id = {
        fetched.message_id: fetched
        for fetched in fetched_messages or []
        if fetched.message_id
    }
    fetched_by_uid = {fetched.uid: fetched for fetched in fetched_messages or []}
    for index, message in enumerate(messages):
        async with session_factory() as session:
            reply_created_at = _get_reply_created_at(message)
            fetched = None
            if message.message_id:
                fetched = fetched_by_message_id.get(message.message_id)
            if fetched is None and fetched_messages and index < len(fetched_messages):
                candidate = fetched_messages[index]
                if candidate.uid in fetched_by_uid:
                    fetched = candidate
            task = await _find_reply_target(session, identity_id, message)
            if not task:
                professor = await _find_existing_professor_for_incoming_message(
                    session, message
                )
                if professor is not None:
                    await _upsert_unbound_received_log(
                        session,
                        identity_id=identity_id,
                        professor=professor,
                        message=message,
                        fetched=fetched,
                        reply_created_at=reply_created_at,
                        folder_role=folder_role,
                        folder=folder,
                    )
                    await commit_imap_identity_sync_session(session)
                    detected += 1
                continue

            existing = await _find_existing_received_log_for_reply(
                session,
                task,
                message.message_id,
            )
            if existing is not None:
                was_already_replied = (
                    task.is_replied
                    and task.status == EmailTaskStatus.REPLY_DETECTED.value
                )
                _mark_task_reply_detected(task)
                changed = _backfill_existing_reply(existing, message, reply_created_at)
                if changed:
                    session.add(existing)
                if changed or not was_already_replied:
                    if not was_already_replied:
                        await _record_email_task_log(
                            session,
                            task,
                            "email_task.reply_detected",
                            metadata={"message_id": message.message_id},
                        )
                    await commit_imap_identity_sync_session(session)
                if not was_already_replied:
                    detected += 1
                continue

            _mark_task_reply_detected(task)
            try:
                await upsert_email_log(
                    session,
                    EmailLogIngestRecord(
                        email_task_id=task.id,
                        identity_id=task.identity_id,
                        llm_profile_id=task.llm_profile_id,
                        professor_id=task.professor_id,
                        direction=EmailDirection.RECEIVED.value,
                        subject=message.subject,
                        content=message.content,
                        content_html=message.content_html,
                        message_id=message.message_id,
                        from_email=fetched.from_email
                        if fetched is not None
                        else message.from_email,
                        to_emails=fetched.to_emails if fetched is not None else None,
                        cc_emails=fetched.cc_emails if fetched is not None else None,
                        bcc_emails=fetched.bcc_emails if fetched is not None else None,
                        created_at=reply_created_at,
                        ingest_source="imap",
                        folder_role=folder_role,
                        folder=folder,
                        uidvalidity=fetched.uidvalidity
                        if fetched is not None
                        else None,
                        imap_uid=fetched.uid if fetched is not None else None,
                        provider_payload=None,
                        reply_headers=message.headers,
                    ),
                )
                await _record_email_task_log(
                    session,
                    task,
                    "email_task.reply_detected",
                    metadata={"message_id": message.message_id},
                )
                await commit_imap_identity_sync_session(session)
            except IntegrityError:
                await session.rollback()
                continue
            detected += 1
    return detected


async def _find_existing_professor_for_incoming_message(
    session: AsyncSession,
    message: ReceivedEmail,
) -> Professor | None:
    normalized_from_email = normalize_email_address(message.from_email)
    if not normalized_from_email:
        return None
    return await session.scalar(
        select(Professor)
        .where(
            Professor.archived_at.is_(None),
            func.lower(Professor.email) == normalized_from_email,
        )
        .order_by(Professor.updated_at.desc(), Professor.id.desc()),
    )


async def _upsert_unbound_received_log(
    session: AsyncSession,
    *,
    identity_id: int,
    professor: Professor,
    message: ReceivedEmail,
    fetched: ImapFetchedMessage | None,
    reply_created_at: datetime,
    folder_role: str,
    folder: str,
) -> None:
    await upsert_email_log(
        session,
        EmailLogIngestRecord(
            email_task_id=None,
            identity_id=identity_id,
            llm_profile_id=None,
            professor_id=professor.id,
            direction=EmailDirection.RECEIVED.value,
            subject=message.subject,
            content=message.content,
            content_html=message.content_html,
            message_id=message.message_id,
            from_email=fetched.from_email
            if fetched is not None
            else message.from_email,
            to_emails=fetched.to_emails if fetched is not None else None,
            cc_emails=fetched.cc_emails if fetched is not None else None,
            bcc_emails=fetched.bcc_emails if fetched is not None else None,
            created_at=reply_created_at,
            ingest_source="imap",
            folder_role=folder_role,
            folder=folder,
            uidvalidity=fetched.uidvalidity if fetched is not None else None,
            imap_uid=fetched.uid if fetched is not None else None,
            provider_payload=None,
            reply_headers=message.headers,
        ),
    )


def _mark_task_reply_detected(task: EmailTask) -> None:
    task.is_replied = True
    task.status = EmailTaskStatus.REPLY_DETECTED.value
    task.updated_at = utc_now()


async def _find_existing_received_log_for_reply(
    session: AsyncSession,
    task: EmailTask,
    message_id: str | None,
) -> EmailLog | None:
    normalized_message_id = (message_id or "").strip().lower()
    if not normalized_message_id:
        return None
    return await session.scalar(
        select(EmailLog).where(
            EmailLog.identity_id == task.identity_id,
            EmailLog.professor_id == task.professor_id,
            EmailLog.direction == EmailDirection.RECEIVED.value,
            EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
            or_(
                EmailLog.normalized_message_id == normalized_message_id,
                func.lower(EmailLog.rfc_message_id) == normalized_message_id,
            ),
        ),
    )


async def process_imap_fetched_messages(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    messages: list[ImapFetchedMessage],
    *,
    folder_role: str = "inbox",
    folder: str = "INBOX",
) -> int:
    _validate_imap_folder_role(folder_role)
    if folder_role == "sent":
        return await _process_sent_imap_fetched_messages(
            session_factory,
            identity_id,
            messages,
            folder_role=folder_role,
            folder=folder,
        )

    received_messages = [
        ReceivedEmail(
            from_email=message.from_email,
            subject=message.subject,
            content=message.body_text,
            content_html=message.body_html,
            message_id=message.message_id,
            in_reply_to=message.in_reply_to,
            references=message.references,
            sent_at=message.sent_at,
            received_at=message.received_at,
            headers=message.headers,
        )
        for message in messages
    ]
    return await _process_incoming_reply_messages(
        session_factory,
        identity_id,
        received_messages,
        fetched_messages=messages,
        folder_role=folder_role,
        folder=folder,
    )


def _validate_imap_folder_role(folder_role: str) -> None:
    if folder_role not in VALID_IMAP_FOLDER_ROLES:
        raise ValueError(f"Unsupported IMAP folder_role: {folder_role}")


async def _process_sent_imap_fetched_messages(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    messages: list[ImapFetchedMessage],
    *,
    folder_role: str,
    folder: str,
) -> int:
    detected = 0
    recipients_by_message = {
        id(message): normalize_email_list(
            [*message.to_emails, *message.cc_emails, *message.bcc_emails]
        )
        for message in messages
    }
    all_recipient_emails = sorted(
        {
            email
            for recipient_emails in recipients_by_message.values()
            for email in recipient_emails
        },
    )
    if not all_recipient_emails:
        return 0

    async with session_factory() as session:
        professors: list[Professor] = []
        for email_chunk in chunked_values(all_recipient_emails):
            professors.extend(
                await session.scalars(
                    select(Professor).where(
                        Professor.archived_at.is_(None),
                        func.lower(Professor.email).in_(email_chunk),
                    ),
                ),
            )
        professors_by_email: dict[str, list[Professor]] = {}
        for professor in professors:
            normalized_email = normalize_email_address(professor.email)
            if not normalized_email:
                continue
            professors_by_email.setdefault(normalized_email, []).append(professor)
        for message in messages:
            matched_professors_by_id: dict[int, Professor] = {}
            for email in recipients_by_message[id(message)]:
                for professor in professors_by_email.get(email, []):
                    matched_professors_by_id.setdefault(professor.id, professor)
            matched_professors = list(matched_professors_by_id.values())
            for professor in matched_professors:
                result = await ingest_sent_email_observation(
                    session,
                    EmailLogIngestRecord(
                        email_task_id=None,
                        identity_id=identity_id,
                        llm_profile_id=None,
                        professor_id=professor.id,
                        direction=EmailDirection.SENT.value,
                        subject=message.subject,
                        content=message.body_text,
                        content_html=message.body_html,
                        message_id=message.message_id,
                        from_email=message.from_email,
                        to_emails=message.to_emails,
                        cc_emails=message.cc_emails,
                        bcc_emails=message.bcc_emails,
                        created_at=message.sent_at,
                        ingest_source="imap",
                        folder_role=folder_role,
                        folder=folder,
                        uidvalidity=message.uidvalidity,
                        imap_uid=message.uid,
                        provider_payload=None,
                        reply_headers=message.headers,
                        delivery_key=message.headers.get(
                            "x-autoemailsender-delivery-id",
                        ),
                    ),
                )
                task = (
                    await session.get(EmailTask, result.email_task_id)
                    if result.email_task_id is not None and result.email_log is not None
                    else None
                )
                if task is not None:
                    if task.status != EmailTaskStatus.REPLY_DETECTED.value:
                        task.status = EmailTaskStatus.SENT.value
                    if task.sent_at is None:
                        task.sent_at = message.sent_at
                    if message.message_id and not task.last_rfc_message_id:
                        task.last_rfc_message_id = message.message_id
                    task.updated_at = utc_now()
                detected += 1
        await commit_imap_identity_sync_session(session)
    return detected


def _backfill_existing_reply(
    existing: EmailLog,
    message: ReceivedEmail,
    reply_created_at: datetime,
) -> bool:
    changed = False
    if (
        not existing.content or _looks_like_raw_mime_content(existing.content)
    ) and message.content:
        existing.content = message.content
        changed = True
    if (
        not existing.content_html or _looks_like_raw_mime_content(existing.content_html)
    ) and message.content_html:
        existing.content_html = message.content_html
        changed = True
    if not existing.reply_headers and message.headers:
        existing.reply_headers = message.headers
        changed = True
    if not _datetimes_match(existing.created_at, reply_created_at):
        existing.created_at = reply_created_at
        changed = True
    return changed


def _looks_like_raw_mime_content(content: str | None) -> bool:
    if not content:
        return False
    normalized = content[:2000].lower()
    return (
        "content-transfer-encoding:" in normalized
        or "content-type:" in normalized
        and "---=" in normalized
        or "body[section" in normalized
    )


def _get_reply_created_at(message: mail_runtime.ReceivedEmail) -> datetime:
    return message.received_at or message.sent_at


def _datetimes_match(left: datetime, right: datetime) -> bool:
    def normalize(value: datetime) -> datetime:
        if value.tzinfo is None:
            return as_utc_aware(value)
        return value.astimezone(UTC)

    return normalize(left) == normalize(right)


async def _find_reply_target(
    session: AsyncSession,
    identity_id: int,
    message: ReceivedEmail,
) -> EmailTask | None:
    normalized_from_email = normalize_email_address(message.from_email)
    reference_ids = extract_message_ids(message.in_reply_to, message.references)
    if reference_ids:
        matched_logs: list[EmailLog] = []
        for reference_id_chunk in chunked_values(reference_ids):
            matched_log = await session.scalar(
                select(EmailLog)
                .join(Professor, EmailLog.professor_id == Professor.id)
                .where(
                    EmailLog.identity_id == identity_id,
                    EmailLog.direction == EmailDirection.SENT.value,
                    EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
                    Professor.archived_at.is_(None),
                    func.lower(Professor.email) == normalized_from_email,
                    or_(
                        func.lower(EmailLog.rfc_message_id).in_(reference_id_chunk),
                        EmailLog.normalized_message_id.in_(reference_id_chunk),
                    ),
                )
                .order_by(EmailLog.created_at.desc())
                .limit(1),
            )
            if matched_log is not None:
                matched_logs.append(matched_log)
        matched_log = max(
            matched_logs,
            key=lambda item: item.created_at,
            default=None,
        )
        if matched_log and matched_log.email_task_id:
            return await _load_email_task(session, matched_log.email_task_id)

        observation_target = await _find_reply_target_from_observations(
            session,
            identity_id=identity_id,
            normalized_from_email=normalized_from_email,
            reference_ids=reference_ids,
        )
        if observation_target is not None:
            return observation_target

    if not normalized_from_email:
        return None

    candidate_tasks = list(
        (
            await session.execute(
                select(EmailTask)
                .options(*TASK_RELATION_OPTIONS)
                .join(Professor, EmailTask.professor_id == Professor.id)
                .where(
                    EmailTask.identity_id == identity_id,
                    func.lower(Professor.email) == normalized_from_email,
                    EmailTask.status.in_(
                        [
                            EmailTaskStatus.SENT.value,
                            EmailTaskStatus.REPLY_DETECTED.value,
                            EmailTaskStatus.CANCELED.value,
                        ],
                    ),
                )
                .order_by(EmailTask.sent_at.desc(), EmailTask.updated_at.desc()),
            )
        ).scalars()
    )
    if not candidate_tasks:
        return None

    normalized_incoming_subject = normalize_subject(message.subject)
    if normalized_incoming_subject:
        for task in candidate_tasks:
            if (
                normalize_subject(task.approved_subject or task.generated_subject)
                == normalized_incoming_subject
            ):
                return task
    return candidate_tasks[0]


async def _find_reply_target_from_observations(
    session: AsyncSession,
    *,
    identity_id: int,
    normalized_from_email: str,
    reference_ids: set[str],
) -> EmailTask | None:
    if not normalized_from_email or not reference_ids:
        return None
    matched_rows: list[tuple[EmailObservation, EmailLog]] = []
    for reference_id_chunk in chunked_values(reference_ids):
        matched_rows.extend(
            (
                await session.execute(
                    select(EmailObservation, EmailLog)
                    .join(Professor, EmailObservation.professor_id == Professor.id)
                    .join(
                        EmailLog,
                        EmailLog.id == EmailObservation.email_log_id,
                    )
                    .where(
                        EmailObservation.identity_id == identity_id,
                        EmailObservation.direction == EmailDirection.SENT.value,
                        EmailObservation.normalized_message_id.in_(reference_id_chunk),
                        EmailObservation.resolution
                        == EmailObservationResolution.MATCHED.value,
                        EmailLog.identity_id == identity_id,
                        EmailLog.professor_id == EmailObservation.professor_id,
                        EmailLog.direction == EmailDirection.SENT.value,
                        EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
                        EmailLog.email_task_id.is_not(None),
                        Professor.archived_at.is_(None),
                        func.lower(Professor.email) == normalized_from_email,
                    ),
                )
            ).all(),
        )
    target_log_ids = {email_log.id for _, email_log in matched_rows}
    if len(target_log_ids) != 1:
        return None
    target_log_id = target_log_ids.pop()
    target_log = next(
        email_log for _, email_log in matched_rows if email_log.id == target_log_id
    )
    if target_log.email_task_id is None:
        return None
    return await _load_email_task(session, target_log.email_task_id)


def extract_message_ids(*headers: str | None) -> set[str]:
    values: set[str] = set()
    for header in headers:
        if not header:
            continue
        values.update(value.lower() for value in re.findall(r"<[^>]+>", header))
    return values


def normalize_subject(subject: str | None) -> str:
    if not subject:
        return ""
    normalized = subject.strip().lower()
    normalized = re.sub(r"^(re|fw|fwd)\s*:\s*", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


VALID_IMAP_FOLDER_ROLES = {"inbox", "sent"}
