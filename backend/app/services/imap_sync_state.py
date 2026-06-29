from __future__ import annotations

from collections.abc import Iterable

from app.core.time import utc_now

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import IdentityProfile, ImapProfessorHistoricalScanStatus, ImapProfessorSyncState, Professor


async def ensure_professor_scan_states(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int | None = None,
    sent_folder: str | None = None,
) -> int:
    created = 0
    async with session_factory() as session:
        rows = await _load_existing_professor_rows(session, identity_id=identity_id)
        for identity_id, professor_id, professor_email in rows:
            normalized_email = _normalize_email(professor_email)
            if not normalized_email:
                continue
            created += await _ensure_professor_scan_state(
                session,
                identity_id=identity_id,
                professor_id=professor_id,
                professor_email=normalized_email,
                folder_role="inbox",
                folder="INBOX",
            )
            if sent_folder:
                created += await _ensure_professor_scan_state(
                    session,
                    identity_id=identity_id,
                    professor_id=professor_id,
                    professor_email=normalized_email,
                    folder_role="sent",
                    folder=sent_folder,
                )
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


async def _ensure_professor_scan_state(
    session: AsyncSession,
    *,
    identity_id: int,
    professor_id: int,
    professor_email: str,
    folder_role: str,
    folder: str,
) -> int:
    existing = await session.scalar(
        select(ImapProfessorSyncState).where(
            ImapProfessorSyncState.identity_id == identity_id,
            ImapProfessorSyncState.professor_id == professor_id,
            ImapProfessorSyncState.professor_email == professor_email,
            ImapProfessorSyncState.folder_role == folder_role,
            ImapProfessorSyncState.folder == folder,
        ),
    )
    if existing is not None:
        return 0
    session.add(
        ImapProfessorSyncState(
            identity_id=identity_id,
            professor_id=professor_id,
            professor_email=professor_email,
            folder_role=folder_role,
            folder=folder,
        ),
    )
    return 1


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


def _normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()
