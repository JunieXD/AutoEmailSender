from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import timedelta

from app.core.config import get_settings
from app.core.time import utc_now

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    IdentityProfile,
    ImapMailboxSyncState,
    ImapProfessorHistoricalScanStatus,
    ImapProfessorSyncState,
    Professor,
)


ScanStateKey = tuple[int, int, str, str, str]
STALE_RUNNING_SCAN_AFTER = timedelta(hours=1)
SCAN_STATE_KEY_LOOKUP_CHUNK_SIZE = 400


async def ensure_professor_scan_states(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int | None = None,
    sent_folder: str | None = None,
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
            session.add(
                ImapProfessorSyncState(
                    identity_id=row_identity_id,
                    professor_id=professor_id,
                    professor_email=professor_email,
                    folder_role=folder_role,
                    folder=folder,
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
        now = utc_now()
        if (
            state.professor_state_fingerprint == fingerprint
            and state.last_professor_state_ensure_at is not None
            and now - state.last_professor_state_ensure_at < ttl
        ):
            return 0

    created = await ensure_professor_scan_states(
        session_factory,
        identity_id=identity_id,
        sent_folder=sent_folder,
    )
    async with session_factory() as session:
        state = await _get_or_create_ensure_mailbox_state(session, identity_id)
        state.professor_state_fingerprint = fingerprint
        state.last_professor_state_ensure_at = utc_now()
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
) -> list[ImapProfessorSyncState]:
    if limit <= 0:
        return []
    async with session_factory() as session:
        stale_running_cutoff = utc_now() - STALE_RUNNING_SCAN_AFTER
        states = list(
            (
                await session.execute(
                    select(ImapProfessorSyncState)
                    .where(
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
                                (
                                    ImapProfessorSyncState.historical_scan_started_at.is_(None)
                                )
                                | (
                                    ImapProfessorSyncState.historical_scan_started_at
                                    <= stale_running_cutoff
                                )
                            ),
                        ),
                    )
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
