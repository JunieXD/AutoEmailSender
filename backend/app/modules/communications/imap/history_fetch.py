from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.models import (
    EmailDirection,
    EmailLog,
    EmailLogRecordState,
    EmailObservation,
    IdentityProfile,
    ImapMailboxSyncState,
    ImapProfessorSyncState,
    Professor,
)

from .. import transport as mail_runtime
from ..addresses import normalize_email_address, normalize_email_list
from .fetcher import ImapFetchedMessage
from .message_ingestion import _validate_imap_folder_role as _validate_imap_folder_role

IMAP_HISTORY_BODY_FETCH_COMMANDS_PER_MESSAGE = 6


@dataclass(frozen=True, slots=True)
class _HistoryBodyFetchResult:
    messages: list[ImapFetchedMessage]
    command_count: int
    highest_scanned_uid: int | None
    covered_all_headers: bool


@dataclass(frozen=True, slots=True)
class _MailboxHistoryBodyFetchResult:
    messages: list[ImapFetchedMessage]
    command_count: int
    matched_header_count: int
    covered_all_headers: bool
    highest_scanned_uid: int | None = None
    safe_match_uids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class _MailboxHistoryHeaderMatch:
    message: ImapFetchedMessage
    professor_ids: tuple[int, ...]


async def _fetch_recent_sent_message_bodies(
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityProfile,
    *,
    identity_id: int,
    folder: str,
    matched_headers: list[_MailboxHistoryHeaderMatch],
    remaining_command_budget: int,
) -> _MailboxHistoryBodyFetchResult:
    if not matched_headers:
        return _MailboxHistoryBodyFetchResult(
            messages=[],
            command_count=0,
            matched_header_count=0,
            covered_all_headers=True,
        )

    state = ImapMailboxSyncState(
        identity_id=identity_id,
        folder_role="sent",
        folder=folder,
    )
    missing_uids: list[int] = []
    covered_all_headers = True
    allowed_uid_count = None
    highest_scanned_uid: int | None = None
    safe_match_uids: list[int] = []
    for match in sorted(matched_headers, key=lambda item: item.message.uid):
        if await _history_mailbox_header_already_ingested(
            session_factory,
            identity_id=identity_id,
            state=state,
            message=match.message,
            professor_ids=match.professor_ids,
        ):
            highest_scanned_uid = _max_optional_uid(
                highest_scanned_uid, match.message.uid
            )
            safe_match_uids.append(match.message.uid)
            continue
        if allowed_uid_count is None:
            allowed_uid_count = _history_body_fetch_uid_limit(
                remaining_command_budget,
                get_settings().imap_fetch_batch_size,
            )
            if allowed_uid_count <= 0:
                return _MailboxHistoryBodyFetchResult(
                    messages=[],
                    command_count=0,
                    matched_header_count=len(matched_headers),
                    covered_all_headers=False,
                    highest_scanned_uid=None,
                    safe_match_uids=tuple(safe_match_uids),
                )
        if len(missing_uids) >= allowed_uid_count:
            covered_all_headers = False
            break
        missing_uids.append(match.message.uid)
        highest_scanned_uid = _max_optional_uid(highest_scanned_uid, match.message.uid)

    if not missing_uids:
        return _MailboxHistoryBodyFetchResult(
            messages=[],
            command_count=0,
            matched_header_count=len(matched_headers),
            covered_all_headers=covered_all_headers,
            highest_scanned_uid=highest_scanned_uid,
            safe_match_uids=tuple(safe_match_uids),
        )

    body_fetch_command_count = _history_body_fetch_command_count(
        len(missing_uids),
        get_settings().imap_fetch_batch_size,
    )
    messages = await mail_runtime.fetch_professor_history_mailbox_messages_by_uid(
        identity,
        folder,
        missing_uids,
    )
    fetched_uids = {message.uid for message in messages}
    missing_after_fetch = [uid for uid in missing_uids if uid not in fetched_uids]
    if missing_after_fetch:
        raise RuntimeError(
            f"IMAP history body fetch incomplete for UIDs: {missing_after_fetch}"
        )
    safe_match_uids.extend(missing_uids)
    return _MailboxHistoryBodyFetchResult(
        messages=messages,
        command_count=body_fetch_command_count,
        matched_header_count=len(matched_headers),
        covered_all_headers=covered_all_headers,
        highest_scanned_uid=highest_scanned_uid,
        safe_match_uids=tuple(safe_match_uids),
    )


async def _fetch_missing_history_mailbox_message_bodies(
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityProfile,
    *,
    identity_id: int,
    state: ImapMailboxSyncState,
    header_messages: list[ImapFetchedMessage],
    remaining_command_budget: int,
) -> _MailboxHistoryBodyFetchResult:
    matched_headers = await _match_history_mailbox_headers(
        session_factory,
        folder_role=state.folder_role,
        header_messages=header_messages,
    )
    if not matched_headers:
        return _MailboxHistoryBodyFetchResult(
            messages=[],
            command_count=0,
            matched_header_count=0,
            covered_all_headers=True,
        )

    missing_uids: list[int] = []
    covered_all_headers = True
    allowed_uid_count = None
    for match in sorted(
        matched_headers, key=lambda item: item.message.uid, reverse=True
    ):
        if await _history_mailbox_header_already_ingested(
            session_factory,
            identity_id=identity_id,
            state=state,
            message=match.message,
            professor_ids=match.professor_ids,
        ):
            continue
        if allowed_uid_count is None:
            allowed_uid_count = _history_body_fetch_uid_limit(
                remaining_command_budget,
                get_settings().imap_fetch_batch_size,
            )
            if allowed_uid_count <= 0:
                raise RuntimeError(
                    "IMAP history command budget exhausted before body fetch"
                )
        if len(missing_uids) >= allowed_uid_count:
            covered_all_headers = False
            break
        missing_uids.append(match.message.uid)

    if not missing_uids:
        return _MailboxHistoryBodyFetchResult(
            messages=[],
            command_count=0,
            matched_header_count=len(matched_headers),
            covered_all_headers=covered_all_headers,
        )

    body_fetch_command_count = _history_body_fetch_command_count(
        len(missing_uids),
        get_settings().imap_fetch_batch_size,
    )
    messages = await mail_runtime.fetch_professor_history_mailbox_messages_by_uid(
        identity,
        state.folder,
        missing_uids,
    )
    fetched_uids = {message.uid for message in messages}
    missing_after_fetch = [uid for uid in missing_uids if uid not in fetched_uids]
    if missing_after_fetch:
        raise RuntimeError(
            f"IMAP history body fetch incomplete for UIDs: {missing_after_fetch}"
        )
    return _MailboxHistoryBodyFetchResult(
        messages=messages,
        command_count=body_fetch_command_count,
        matched_header_count=len(matched_headers),
        covered_all_headers=covered_all_headers,
    )


async def _match_history_mailbox_headers(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    folder_role: str,
    header_messages: list[ImapFetchedMessage],
) -> list[_MailboxHistoryHeaderMatch]:
    if not header_messages:
        return []
    professor_ids_by_email = await _load_active_professor_ids_by_email(session_factory)
    if not professor_ids_by_email:
        return []

    matches: list[_MailboxHistoryHeaderMatch] = []
    for message in header_messages:
        if folder_role == "inbox":
            candidate_emails = [normalize_email_address(message.from_email)]
        elif folder_role == "sent":
            candidate_emails = normalize_email_list(
                [*message.to_emails, *message.cc_emails, *message.bcc_emails],
            )
        else:
            _validate_imap_folder_role(folder_role)
            candidate_emails = []

        professor_ids = tuple(
            dict.fromkeys(
                professor_id
                for email in candidate_emails
                if email
                for professor_id in professor_ids_by_email.get(email, [])
            ),
        )
        if professor_ids:
            matches.append(
                _MailboxHistoryHeaderMatch(message=message, professor_ids=professor_ids)
            )
    return matches


async def _load_active_professor_ids_by_email(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, list[int]]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Professor.id, Professor.email).where(
                    Professor.archived_at.is_(None),
                    Professor.email.is_not(None),
                ),
            )
        ).all()
    professor_ids_by_email: dict[str, list[int]] = {}
    for professor_id, email in rows:
        normalized_email = normalize_email_address(email)
        if not normalized_email:
            continue
        professor_ids_by_email.setdefault(normalized_email, []).append(professor_id)
    return professor_ids_by_email


async def _history_mailbox_header_already_ingested(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    state: ImapMailboxSyncState,
    message: ImapFetchedMessage,
    professor_ids: tuple[int, ...],
) -> bool:
    if not professor_ids:
        return True
    direction = (
        EmailDirection.RECEIVED.value
        if state.folder_role == "inbox"
        else EmailDirection.SENT.value
    )
    expected_professor_ids = set(professor_ids)
    normalized_message_id = (message.message_id or "").strip().lower()
    async with session_factory() as session:
        if state.folder_role == "sent":
            observation_professor_ids: set[int] = set()
            if normalized_message_id:
                observation_professor_ids.update(
                    await session.scalars(
                        select(EmailObservation.professor_id).where(
                            EmailObservation.identity_id == identity_id,
                            EmailObservation.professor_id.in_(expected_professor_ids),
                            EmailObservation.direction == direction,
                            EmailObservation.normalized_message_id
                            == normalized_message_id,
                        ),
                    ),
                )
            if message.uidvalidity is not None:
                observation_professor_ids.update(
                    await session.scalars(
                        select(EmailObservation.professor_id).where(
                            EmailObservation.identity_id == identity_id,
                            EmailObservation.professor_id.in_(expected_professor_ids),
                            EmailObservation.folder_role == state.folder_role,
                            EmailObservation.folder == state.folder,
                            EmailObservation.uidvalidity == message.uidvalidity,
                            EmailObservation.imap_uid == message.uid,
                        ),
                    ),
                )
            if observation_professor_ids >= expected_professor_ids:
                return True
        if normalized_message_id:
            rows = (
                await session.execute(
                    select(EmailLog.professor_id).where(
                        EmailLog.identity_id == identity_id,
                        EmailLog.professor_id.in_(expected_professor_ids),
                        EmailLog.direction == direction,
                        EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
                        or_(
                            EmailLog.normalized_message_id == normalized_message_id,
                            func.lower(EmailLog.rfc_message_id)
                            == normalized_message_id,
                        ),
                    ),
                )
            ).all()
            if {professor_id for (professor_id,) in rows} >= expected_professor_ids:
                return True
        if message.uidvalidity is None:
            return False
        rows = (
            await session.execute(
                select(EmailLog.professor_id).where(
                    EmailLog.identity_id == identity_id,
                    EmailLog.professor_id.in_(expected_professor_ids),
                    EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
                    EmailLog.folder_role == state.folder_role,
                    EmailLog.folder == state.folder,
                    EmailLog.uidvalidity == message.uidvalidity,
                    EmailLog.imap_uid == message.uid,
                ),
            )
        ).all()
    return {professor_id for (professor_id,) in rows} >= expected_professor_ids


async def _fetch_missing_history_message_bodies(
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityProfile,
    *,
    identity_id: int,
    state: ImapProfessorSyncState,
    header_messages: list[ImapFetchedMessage],
    remaining_command_budget: int,
) -> _HistoryBodyFetchResult:
    if not header_messages:
        return _HistoryBodyFetchResult(
            messages=[],
            command_count=0,
            highest_scanned_uid=state.last_scanned_uid,
            covered_all_headers=True,
        )
    sorted_headers = sorted(header_messages, key=lambda message: message.uid)
    missing_uids: list[int] = []
    highest_scanned_uid = state.last_scanned_uid
    covered_all_headers = True
    allowed_uid_count = None
    for message in sorted_headers:
        if await _history_header_already_ingested(
            session_factory,
            identity_id=identity_id,
            state=state,
            message=message,
        ):
            highest_scanned_uid = _max_optional_uid(highest_scanned_uid, message.uid)
            continue
        if allowed_uid_count is None:
            allowed_uid_count = _history_body_fetch_uid_limit(
                remaining_command_budget,
                get_settings().imap_fetch_batch_size,
            )
            if allowed_uid_count <= 0:
                raise RuntimeError(
                    "IMAP history command budget exhausted before body fetch"
                )
        if len(missing_uids) >= allowed_uid_count:
            covered_all_headers = False
            break
        missing_uids.append(message.uid)
        highest_scanned_uid = _max_optional_uid(highest_scanned_uid, message.uid)
    if not missing_uids:
        return _HistoryBodyFetchResult(
            messages=[],
            command_count=0,
            highest_scanned_uid=highest_scanned_uid,
            covered_all_headers=covered_all_headers,
        )
    body_fetch_command_count = _history_body_fetch_command_count(
        len(missing_uids),
        get_settings().imap_fetch_batch_size,
    )
    messages = await mail_runtime.fetch_professor_history_mailbox_messages_by_uid(
        identity,
        state.folder,
        missing_uids,
    )
    fetched_uids = {message.uid for message in messages}
    missing_after_fetch = [uid for uid in missing_uids if uid not in fetched_uids]
    if missing_after_fetch:
        raise RuntimeError(
            f"IMAP history body fetch incomplete for UIDs: {missing_after_fetch}"
        )
    return _HistoryBodyFetchResult(
        messages=messages,
        command_count=body_fetch_command_count,
        highest_scanned_uid=highest_scanned_uid,
        covered_all_headers=covered_all_headers,
    )


def _history_body_fetch_command_count(message_count: int, batch_size: int) -> int:
    if message_count <= 0:
        return 0
    effective_batch_size = max(1, batch_size)
    return (
        (message_count + effective_batch_size - 1) // effective_batch_size
        + message_count * IMAP_HISTORY_BODY_FETCH_COMMANDS_PER_MESSAGE
    )


def _history_body_fetch_uid_limit(
    remaining_command_budget: int, batch_size: int
) -> int:
    if remaining_command_budget <= 0:
        return 0
    effective_batch_size = max(1, batch_size)
    allowed = 0
    while True:
        candidate = allowed + 1
        if (
            _history_body_fetch_command_count(candidate, effective_batch_size)
            > remaining_command_budget
        ):
            return allowed
        allowed = candidate


def _max_optional_uid(current: int | None, uid: int) -> int:
    return max(current or 0, uid)


async def _history_header_already_ingested(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    state: ImapProfessorSyncState,
    message: ImapFetchedMessage,
) -> bool:
    async with session_factory() as session:
        professor = await session.get(Professor, state.professor_id)
        if professor is None:
            return False
        direction = (
            EmailDirection.RECEIVED.value
            if state.folder_role == "inbox"
            else EmailDirection.SENT.value
        )
        normalized_message_id = (message.message_id or "").strip().lower()
        if state.folder_role == "sent":
            observation_id = await session.scalar(
                select(EmailObservation.id).where(
                    EmailObservation.identity_id == identity_id,
                    EmailObservation.professor_id == professor.id,
                    or_(
                        and_(
                            normalized_message_id != "",
                            EmailObservation.normalized_message_id
                            == normalized_message_id,
                        ),
                        and_(
                            message.uidvalidity is not None,
                            EmailObservation.folder_role == state.folder_role,
                            EmailObservation.folder == state.folder,
                            EmailObservation.uidvalidity == message.uidvalidity,
                            EmailObservation.imap_uid == message.uid,
                        ),
                    ),
                ),
            )
            if observation_id is not None:
                return True
        if normalized_message_id:
            existing_by_message = await session.scalar(
                select(EmailLog.id).where(
                    EmailLog.identity_id == identity_id,
                    EmailLog.professor_id == professor.id,
                    EmailLog.direction == direction,
                    EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
                    or_(
                        EmailLog.normalized_message_id == normalized_message_id,
                        func.lower(EmailLog.rfc_message_id) == normalized_message_id,
                    ),
                ),
            )
            if existing_by_message is not None:
                return True
        if message.uidvalidity is None:
            return False
        existing_by_uid = await session.scalar(
            select(EmailLog.id).where(
                EmailLog.identity_id == identity_id,
                EmailLog.professor_id == professor.id,
                EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
                EmailLog.folder_role == state.folder_role,
                EmailLog.folder == state.folder,
                EmailLog.uidvalidity == message.uidvalidity,
                EmailLog.imap_uid == message.uid,
            ),
        )
        return existing_by_uid is not None
