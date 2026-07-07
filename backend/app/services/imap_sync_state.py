from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from datetime import timedelta

from app.core.config import get_settings
from app.core.time import utc_now

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    IdentityProfile,
    ImapMailboxHistoricalScanStatus,
    ImapMailboxSyncState,
    ImapProfessorHistoricalScanStatus,
    ImapProfessorSyncState,
    Professor,
)


ScanStateKey = tuple[int, int, str, str, str]
ProfessorEmailKey = tuple[int, int, str]
RecentHistoryCandidate = tuple[int, str]
STALE_RUNNING_SCAN_AFTER = timedelta(hours=1)
STALE_RUNNING_MAILBOX_HISTORY_AFTER = timedelta(hours=1)
SCAN_STATE_KEY_LOOKUP_CHUNK_SIZE = 400
TARGETED_BASELINE_STRATEGY_VERSION = "folder-v1-targeted-baseline"
RECENT_HISTORY_STRATEGY_PREFIX = "recent-v1"


async def ensure_professor_scan_states(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int | None = None,
    sent_folder: str | None = None,
    historical_scan_status: str = ImapProfessorHistoricalScanStatus.PENDING.value,
    completed_professor_email_keys: set[ProfessorEmailKey] | None = None,
) -> int:
    created = 0
    async with session_factory() as session:
        rows = await _load_existing_professor_rows(session, identity_id=identity_id)
        desired_keys: list[ScanStateKey] = []
        for row_identity_id, professor_id, professor_email in rows:
            normalized_email = _normalize_email(professor_email)
            if not normalized_email:
                continue
            desired_keys.append((row_identity_id, professor_id, normalized_email, "inbox", "INBOX"))
            if sent_folder:
                desired_keys.append((row_identity_id, professor_id, normalized_email, "sent", sent_folder))
        existing_keys = await _load_existing_scan_state_keys(session, desired_keys)
        for key in desired_keys:
            if key in existing_keys:
                continue
            row_identity_id, professor_id, professor_email, folder_role, folder = key
            professor_email_key = (row_identity_id, professor_id, professor_email)
            state_status = (
                ImapProfessorHistoricalScanStatus.COMPLETED.value
                if completed_professor_email_keys is not None
                and professor_email_key in completed_professor_email_keys
                else historical_scan_status
            )
            session.add(
                ImapProfessorSyncState(
                    identity_id=row_identity_id,
                    professor_id=professor_id,
                    professor_email=professor_email,
                    folder_role=folder_role,
                    folder=folder,
                    historical_scan_status=state_status,
                    historical_scan_completed_at=utc_now()
                    if state_status == ImapProfessorHistoricalScanStatus.COMPLETED.value
                    else None,
                ),
            )
            created += 1
        await session.commit()
    return created


async def ensure_recent_history_professor_scan_states(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    candidates: set[RecentHistoryCandidate],
    strategy_version: str,
    folder: str = "INBOX",
) -> int:
    normalized_candidates = {
        (professor_id, normalized)
        for professor_id, email in candidates
        if professor_id is not None
        and professor_id > 0
        and (normalized := _normalize_email(email))
    }
    if not normalized_candidates:
        return 0

    created = 0
    async with session_factory() as session:
        candidate_keys = set(normalized_candidates)
        desired_keys: list[ScanStateKey] = [
            (identity_id, professor_id, professor_email, "inbox", folder)
            for professor_id, professor_email in sorted(normalized_candidates)
        ]
        existing_keys = await _load_existing_scan_state_keys(session, desired_keys)
        candidate_emails = sorted({email for _, email in normalized_candidates})
        existing_rows: list[ImapProfessorSyncState] = []
        for email_chunk in _chunked_values(candidate_emails, SCAN_STATE_KEY_LOOKUP_CHUNK_SIZE):
            existing_rows.extend(
                list(
                    (
                        await session.execute(
                            select(ImapProfessorSyncState).where(
                                ImapProfessorSyncState.identity_id == identity_id,
                                ImapProfessorSyncState.folder_role == "inbox",
                                ImapProfessorSyncState.folder == folder,
                                ImapProfessorSyncState.professor_email.in_(email_chunk),
                            ),
                        )
                    ).scalars(),
                ),
            )
        for row in existing_rows:
            if (row.professor_id, row.professor_email) not in candidate_keys:
                continue
            if row.history_strategy_version != strategy_version:
                row.history_strategy_version = strategy_version
                row.historical_scan_status = ImapProfessorHistoricalScanStatus.PENDING.value
                row.last_scanned_uid = None
                row.historical_scan_started_at = None
                row.historical_scan_completed_at = None
                row.last_error = None

        for key in desired_keys:
            if key in existing_keys:
                continue
            row_identity_id, professor_id, professor_email, folder_role, folder_name = key
            session.add(
                ImapProfessorSyncState(
                    identity_id=row_identity_id,
                    professor_id=professor_id,
                    professor_email=professor_email,
                    folder_role=folder_role,
                    folder=folder_name,
                    historical_scan_status=ImapProfessorHistoricalScanStatus.PENDING.value,
                    history_strategy_version=strategy_version,
                ),
            )
            created += 1
        await session.commit()
    return created


async def ensure_professor_scan_states_if_needed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    sent_folder: str | None = None,
    ttl_seconds: int | None = None,
) -> int:
    ttl = timedelta(seconds=ttl_seconds if ttl_seconds is not None else get_settings().imap_ensure_state_ttl_seconds)
    async with session_factory() as session:
        rows = await _load_existing_professor_rows(session, identity_id=identity_id)
        fingerprint = _build_professor_state_fingerprint(rows, sent_folder)
        state = await _get_or_create_ensure_mailbox_state(session, identity_id)
        if not await _mailbox_history_completed_for_targeted_catchup(
            session,
            identity_id=identity_id,
            sent_folder=sent_folder,
        ):
            return 0
        now = utc_now()
        if (
            state.professor_state_fingerprint == fingerprint
            and state.last_professor_state_ensure_at is not None
            and now - state.last_professor_state_ensure_at < ttl
        ):
            return 0
        baseline_completed = state.professor_state_fingerprint is None
        should_baseline_existing_targeted = (
            baseline_completed
            or state.history_strategy_version != TARGETED_BASELINE_STRATEGY_VERSION
        )
        completed_professor_email_keys = (
            None
            if should_baseline_existing_targeted
            else await _load_existing_targeted_professor_email_keys(session, identity_id)
        )

    if should_baseline_existing_targeted:
        await mark_professor_scan_states_completed_for_identity(session_factory, identity_id)
    created = await ensure_professor_scan_states(
        session_factory,
        identity_id=identity_id,
        sent_folder=sent_folder,
        historical_scan_status=(
            ImapProfessorHistoricalScanStatus.COMPLETED.value
            if should_baseline_existing_targeted
            else ImapProfessorHistoricalScanStatus.PENDING.value
        ),
        completed_professor_email_keys=completed_professor_email_keys,
    )
    async with session_factory() as session:
        state = await _get_or_create_ensure_mailbox_state(session, identity_id)
        state.professor_state_fingerprint = fingerprint
        state.last_professor_state_ensure_at = utc_now()
        state.history_strategy_version = TARGETED_BASELINE_STRATEGY_VERSION
        await session.commit()
    return created


async def claim_next_professor_scan(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> ImapProfessorSyncState | None:
    async with session_factory() as session:
        state = await session.scalar(
            select(ImapProfessorSyncState)
            .where(
                ImapProfessorSyncState.identity_id == identity_id,
                ImapProfessorSyncState.historical_scan_status.in_(
                    [
                        ImapProfessorHistoricalScanStatus.PENDING.value,
                        ImapProfessorHistoricalScanStatus.FAILED.value,
                    ],
                ),
            )
            .order_by(
                ImapProfessorSyncState.updated_at.asc(),
                ImapProfessorSyncState.id.asc(),
            ),
        )
        if state is None:
            return None
        state.historical_scan_status = ImapProfessorHistoricalScanStatus.RUNNING.value
        state.historical_scan_started_at = utc_now()
        state.last_error = None
        await session.commit()
        await session.refresh(state)
        return state


async def claim_next_professor_scans(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    limit: int,
    strategy_version: str | None = None,
) -> list[ImapProfessorSyncState]:
    if limit <= 0:
        return []
    async with session_factory() as session:
        stale_running_cutoff = utc_now() - STALE_RUNNING_SCAN_AFTER
        conditions = [
            ImapProfessorSyncState.identity_id == identity_id,
            or_(
                ImapProfessorSyncState.historical_scan_status.in_(
                    [
                        ImapProfessorHistoricalScanStatus.PENDING.value,
                        ImapProfessorHistoricalScanStatus.FAILED.value,
                    ],
                ),
                (
                    ImapProfessorSyncState.historical_scan_status
                    == ImapProfessorHistoricalScanStatus.RUNNING.value
                )
                & (
                    (ImapProfessorSyncState.historical_scan_started_at.is_(None))
                    | (ImapProfessorSyncState.historical_scan_started_at <= stale_running_cutoff)
                ),
            ),
        ]
        if strategy_version is not None:
            conditions.append(ImapProfessorSyncState.history_strategy_version == strategy_version)
        states = list(
            (
                await session.execute(
                    select(ImapProfessorSyncState)
                    .where(*conditions)
                    .order_by(
                        ImapProfessorSyncState.updated_at.asc(),
                        ImapProfessorSyncState.id.asc(),
                    )
                    .limit(limit),
                )
            ).scalars(),
        )
        now = utc_now()
        for state in states:
            state.historical_scan_status = ImapProfessorHistoricalScanStatus.RUNNING.value
            state.historical_scan_started_at = now
            state.last_error = None
        await session.commit()
        for state in states:
            await session.refresh(state)
        return states


async def mark_professor_scan_states_completed_for_identity(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> None:
    async with session_factory() as session:
        states = list(
            (
                await session.execute(
                    select(ImapProfessorSyncState).where(
                        ImapProfessorSyncState.identity_id == identity_id,
                        ImapProfessorSyncState.historical_scan_status
                        != ImapProfessorHistoricalScanStatus.COMPLETED.value,
                    ),
                )
            ).scalars(),
        )
        now = utc_now()
        for state in states:
            state.historical_scan_status = ImapProfessorHistoricalScanStatus.COMPLETED.value
            state.historical_scan_completed_at = now
            state.last_error = None
        await session.commit()


async def reset_professor_scans_to_pending(
    session_factory: async_sessionmaker[AsyncSession],
    state_ids: Iterable[int],
) -> None:
    ids = list(dict.fromkeys(state_ids))
    if not ids:
        return
    async with session_factory() as session:
        states = list(
            (
                await session.execute(
                    select(ImapProfessorSyncState).where(ImapProfessorSyncState.id.in_(ids)),
                )
            ).scalars(),
        )
        for state in states:
            if state.historical_scan_status == ImapProfessorHistoricalScanStatus.COMPLETED.value:
                continue
            state.historical_scan_status = ImapProfessorHistoricalScanStatus.PENDING.value
            state.historical_scan_started_at = None
        await session.commit()


async def claim_next_mailbox_history_scans(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    folders: Iterable[tuple[str, str]],
    limit: int,
) -> list[ImapMailboxSyncState]:
    if limit <= 0:
        return []
    folder_keys = list(dict.fromkeys(folders))
    if not folder_keys:
        return []
    async with session_factory() as session:
        stale_running_cutoff = utc_now() - STALE_RUNNING_MAILBOX_HISTORY_AFTER
        folder_filter = or_(
            *[
                (ImapMailboxSyncState.folder_role == folder_role)
                & (ImapMailboxSyncState.folder == folder)
                for folder_role, folder in folder_keys
            ],
        )
        states = list(
            (
                await session.execute(
                    select(ImapMailboxSyncState)
                    .where(
                        ImapMailboxSyncState.identity_id == identity_id,
                        folder_filter,
                        or_(
                            ImapMailboxSyncState.history_scan_status.in_(
                                [
                                    ImapMailboxHistoricalScanStatus.PENDING.value,
                                    ImapMailboxHistoricalScanStatus.FAILED.value,
                                ],
                            ),
                            (
                                ImapMailboxSyncState.history_scan_status
                                == ImapMailboxHistoricalScanStatus.RUNNING.value
                            )
                            & (
                                (
                                    ImapMailboxSyncState.history_scan_started_at.is_(None)
                                )
                                | (
                                    ImapMailboxSyncState.history_scan_started_at
                                    <= stale_running_cutoff
                                )
                            ),
                        ),
                    )
                    .order_by(
                        ImapMailboxSyncState.updated_at.asc(),
                        ImapMailboxSyncState.id.asc(),
                    )
                    .limit(limit),
                )
            ).scalars(),
        )
        now = utc_now()
        for state in states:
            state.history_scan_status = ImapMailboxHistoricalScanStatus.RUNNING.value
            state.history_scan_started_at = now
            state.history_last_error = None
        await session.commit()
        for state in states:
            await session.refresh(state)
        return states


async def reset_mailbox_history_scans_to_pending(
    session_factory: async_sessionmaker[AsyncSession],
    state_ids: Iterable[int],
) -> None:
    ids = list(dict.fromkeys(state_ids))
    if not ids:
        return
    async with session_factory() as session:
        states = list(
            (
                await session.execute(
                    select(ImapMailboxSyncState).where(ImapMailboxSyncState.id.in_(ids)),
                )
            ).scalars(),
        )
        for state in states:
            if state.history_scan_status == ImapMailboxHistoricalScanStatus.COMPLETED.value:
                continue
            state.history_scan_status = ImapMailboxHistoricalScanStatus.PENDING.value
            state.history_scan_started_at = None
        await session.commit()


async def mark_mailbox_history_scan_progress(
    session_factory: async_sessionmaker[AsyncSession],
    state_id: int,
    *,
    next_before_uid: int | None,
    scanned_count_delta: int,
    matched_count_delta: int,
    uidvalidity: int | None,
    high_water_uid: int | None,
    last_seen_uid_floor: int | None,
    completed: bool,
    uidvalidity_reset: bool = False,
) -> None:
    async with session_factory() as session:
        state = await session.get(ImapMailboxSyncState, state_id)
        if state is None:
            return
        if uidvalidity_reset:
            state.last_seen_uid = None
            state.history_high_water_uid = None
            state.history_scanned_count = 0
            state.history_matched_count = 0
            state.history_scan_completed_at = None
        if uidvalidity is not None:
            state.uidvalidity = uidvalidity
        if high_water_uid is not None:
            state.history_high_water_uid = high_water_uid
        if last_seen_uid_floor is not None:
            state.last_seen_uid = max(state.last_seen_uid or 0, last_seen_uid_floor)
        state.history_next_before_uid = next_before_uid
        state.history_scanned_count = (state.history_scanned_count or 0) + max(0, scanned_count_delta)
        state.history_matched_count = (state.history_matched_count or 0) + max(0, matched_count_delta)
        state.history_last_error = None
        if completed:
            state.history_scan_status = ImapMailboxHistoricalScanStatus.COMPLETED.value
            state.history_scan_completed_at = utc_now()
        else:
            state.history_scan_status = ImapMailboxHistoricalScanStatus.PENDING.value
        await session.commit()


async def mark_mailbox_history_scan_failed(
    session_factory: async_sessionmaker[AsyncSession],
    state_id: int,
    error: str,
) -> None:
    async with session_factory() as session:
        state = await session.get(ImapMailboxSyncState, state_id)
        if state is None:
            return
        state.history_scan_status = ImapMailboxHistoricalScanStatus.FAILED.value
        state.history_last_error = error
        await session.commit()


async def mark_professor_scan_completed(
    session_factory: async_sessionmaker[AsyncSession],
    state_id: int,
    last_scanned_uid: int | None,
) -> None:
    async with session_factory() as session:
        state = await session.get(ImapProfessorSyncState, state_id)
        if state is None:
            return
        state.historical_scan_status = ImapProfessorHistoricalScanStatus.COMPLETED.value
        state.historical_scan_completed_at = utc_now()
        state.last_scanned_uid = last_scanned_uid
        state.last_error = None
        await session.commit()


async def mark_professor_scan_failed(
    session_factory: async_sessionmaker[AsyncSession],
    state_id: int,
    error: str,
) -> None:
    async with session_factory() as session:
        state = await session.get(ImapProfessorSyncState, state_id)
        if state is None:
            return
        state.historical_scan_status = ImapProfessorHistoricalScanStatus.FAILED.value
        state.last_error = error
        await session.commit()


async def clear_identity_sent_folder_discovery_cache(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> bool:
    async with session_factory() as session:
        cleared = await clear_identity_sent_folder_discovery_cache_in_session(session, identity_id)
        await session.commit()
        return cleared


async def clear_identity_sent_folder_discovery_cache_in_session(
    session: AsyncSession,
    identity_id: int,
) -> bool:
    state = await session.scalar(
        select(ImapMailboxSyncState).where(
            ImapMailboxSyncState.identity_id == identity_id,
            ImapMailboxSyncState.folder_role == "sent",
            ImapMailboxSyncState.folder == "Sent",
        ),
    )
    if state is None:
        return False
    state.discovered_sent_folder = None
    state.sent_folder_discovered_at = None
    state.sent_folder_discovery_failed_at = None
    state.sent_folder_discovery_error = None
    return True


async def _load_existing_scan_state_keys(
    session: AsyncSession,
    desired_keys: list[ScanStateKey],
) -> set[ScanStateKey]:
    if not desired_keys:
        return set()
    existing: set[ScanStateKey] = set()
    for chunk in _chunked(desired_keys, SCAN_STATE_KEY_LOOKUP_CHUNK_SIZE):
        identity_ids = {key[0] for key in chunk}
        professor_ids = {key[1] for key in chunk}
        professor_emails = {key[2] for key in chunk}
        folder_roles = {key[3] for key in chunk}
        folders = {key[4] for key in chunk}
        rows = (
            await session.execute(
                select(
                    ImapProfessorSyncState.identity_id,
                    ImapProfessorSyncState.professor_id,
                    ImapProfessorSyncState.professor_email,
                    ImapProfessorSyncState.folder_role,
                    ImapProfessorSyncState.folder,
                ).where(
                    ImapProfessorSyncState.identity_id.in_(identity_ids),
                    ImapProfessorSyncState.professor_id.in_(professor_ids),
                    ImapProfessorSyncState.professor_email.in_(professor_emails),
                    ImapProfessorSyncState.folder_role.in_(folder_roles),
                    ImapProfessorSyncState.folder.in_(folders),
                ),
            )
        ).all()
        existing.update(
            (identity_id, professor_id, professor_email, folder_role, folder)
            for identity_id, professor_id, professor_email, folder_role, folder in rows
        )
    return existing


async def _load_existing_professor_rows(
    session: AsyncSession,
    *,
    identity_id: int | None,
) -> list[tuple[int, int, str | None]]:
    query = (
        select(IdentityProfile.id, Professor.id, Professor.email)
        .select_from(IdentityProfile)
        .join(Professor, Professor.email.is_not(None))
        .where(
            IdentityProfile.imap_host.is_not(None),
            IdentityProfile.imap_port.is_not(None),
            IdentityProfile.imap_username.is_not(None),
            IdentityProfile.imap_password.is_not(None),
            func.trim(IdentityProfile.imap_host) != "",
            func.trim(IdentityProfile.imap_username) != "",
            func.trim(IdentityProfile.imap_password) != "",
            Professor.email.is_not(None),
            Professor.archived_at.is_(None),
        )
        .distinct()
    )
    if identity_id is not None:
        query = query.where(IdentityProfile.id == identity_id)
    rows = (await session.execute(query)).all()
    return _dedupe_rows(rows)


async def _load_existing_targeted_professor_email_keys(
    session: AsyncSession,
    identity_id: int,
) -> set[ProfessorEmailKey]:
    rows = (
        await session.execute(
            select(
                ImapProfessorSyncState.identity_id,
                ImapProfessorSyncState.professor_id,
                ImapProfessorSyncState.professor_email,
            ).where(
                ImapProfessorSyncState.identity_id == identity_id,
            ),
        )
    ).all()
    return {
        (row_identity_id, professor_id, normalized_email)
        for row_identity_id, professor_id, professor_email in rows
        if (normalized_email := _normalize_email(professor_email))
    }


async def _mailbox_history_completed_for_targeted_catchup(
    session: AsyncSession,
    *,
    identity_id: int,
    sent_folder: str | None,
) -> bool:
    required_folders = [("inbox", "INBOX")]
    if sent_folder:
        required_folders.append(("sent", sent_folder))
    for folder_role, folder in required_folders:
        state = await session.scalar(
            select(ImapMailboxSyncState).where(
                ImapMailboxSyncState.identity_id == identity_id,
                ImapMailboxSyncState.folder_role == folder_role,
                ImapMailboxSyncState.folder == folder,
            ),
        )
        if state is None:
            return False
        if state.history_scan_status != ImapMailboxHistoricalScanStatus.COMPLETED.value:
            return False
    return True


def _dedupe_rows(
    rows: Iterable[tuple[int, int, str | None]],
) -> list[tuple[int, int, str | None]]:
    seen: set[tuple[int, int, str]] = set()
    deduped: list[tuple[int, int, str | None]] = []
    for identity_id, professor_id, professor_email in rows:
        normalized_email = _normalize_email(professor_email)
        if not normalized_email:
            continue
        key = (identity_id, professor_id, normalized_email)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((identity_id, professor_id, normalized_email))
    return deduped


def _chunked(values: list[ScanStateKey], size: int) -> Iterable[list[ScanStateKey]]:
    effective_size = max(1, size)
    for index in range(0, len(values), effective_size):
        yield values[index : index + effective_size]


def _chunked_values(values: list[str], size: int) -> Iterator[list[str]]:
    effective_size = max(1, size)
    for index in range(0, len(values), effective_size):
        yield values[index : index + effective_size]


def _normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _build_professor_state_fingerprint(
    rows: Iterable[tuple[int, int, str | None]],
    sent_folder: str | None,
) -> str:
    parts = [
        f"{identity_id}:{professor_id}:{_normalize_email(professor_email)}"
        for identity_id, professor_id, professor_email in rows
        if _normalize_email(professor_email)
    ]
    parts.sort()
    payload = "\n".join([sent_folder or "", *parts])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _get_or_create_ensure_mailbox_state(
    session: AsyncSession,
    identity_id: int,
) -> ImapMailboxSyncState:
    state = await session.scalar(
        select(ImapMailboxSyncState).where(
            ImapMailboxSyncState.identity_id == identity_id,
            ImapMailboxSyncState.folder_role == "inbox",
            ImapMailboxSyncState.folder == "INBOX",
        ),
    )
    if state is not None:
        return state
    state = ImapMailboxSyncState(
        identity_id=identity_id,
        folder_role="inbox",
        folder="INBOX",
    )
    session.add(state)
    await session.flush()
    return state
