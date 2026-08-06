from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta

from app.core.config import get_settings
from app.core.time import utc_now

from sqlalchemy import and_, case, func, or_, select
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
RECENT_V2_STRATEGY_VERSION = "recent-v2"
RECENT_V2_OBSOLETE_STRATEGY_VERSION = "recent-v2-obsolete"
RECENT_V2_PRIORITY_REPAIR = 10
RECENT_V2_PRIORITY_ACTIVE = 100


@dataclass(frozen=True, slots=True)
class RecentV2QueueSummary:
    professor_count: int
    sent_state_count: int
    inbox_state_count: int
    bulk_sent_state_count: int


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


async def ensure_recent_v2_professor_scan_states(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    sent_folder: str | None,
    history_start_date: date,
    settle_seconds: int,
) -> int:
    now = utc_now()
    settle = timedelta(seconds=max(0, settle_seconds))
    touched = 0
    batch_id = f"queue:{uuid.uuid4().hex}"
    async with session_factory() as session:
        professors = list(
            (
                await session.execute(
                    select(Professor).where(
                        Professor.archived_at.is_(None),
                        Professor.email.is_not(None),
                        func.trim(Professor.email) != "",
                    ),
                )
            ).scalars(),
        )
        if not professors:
            return 0

        existing_states = list(
            (
                await session.execute(
                    select(ImapProfessorSyncState).where(
                        ImapProfessorSyncState.identity_id == identity_id,
                    ),
                )
            ).scalars(),
        )
        for state in existing_states:
            if (
                state.folder_role == "sent"
                and state.history_strategy_version == RECENT_V2_STRATEGY_VERSION
                and state.folder != sent_folder
            ):
                state.history_strategy_version = RECENT_V2_OBSOLETE_STRATEGY_VERSION
                state.historical_scan_status = ImapProfessorHistoricalScanStatus.COMPLETED.value
                state.historical_scan_started_at = None
                state.historical_scan_completed_at = now
                state.last_error = None
        existing_by_key = {
            (
                state.professor_id,
                _normalize_email(state.professor_email),
                state.folder_role,
                state.folder,
            ): state
            for state in existing_states
        }

        for professor in professors:
            professor_email = _normalize_email(professor.email)
            if not professor_email:
                continue
            professor_version = max(1, professor.communication_sync_version or 1)
            requested_at = professor.updated_at or professor.created_at or now
            recently_changed = now - requested_at < settle
            trigger_reason = "professor_activated" if recently_changed else "upgrade_repair"
            priority = RECENT_V2_PRIORITY_ACTIVE if recently_changed else RECENT_V2_PRIORITY_REPAIR
            available_at = max(now, requested_at + settle) if recently_changed else now
            folder_specs = [("inbox", "INBOX")]
            if sent_folder:
                folder_specs.insert(0, ("sent", sent_folder))

            for folder_role, folder in folder_specs:
                state_trigger_reason = trigger_reason
                state_priority = priority
                state_available_at = available_at
                key = (professor.id, professor_email, folder_role, folder)
                state = existing_by_key.get(key)
                if state is None:
                    state = ImapProfessorSyncState(
                        identity_id=identity_id,
                        professor_id=professor.id,
                        professor_email=professor_email,
                        folder_role=folder_role,
                        folder=folder,
                    )
                    session.add(state)
                    existing_by_key[key] = state
                    needs_reset = True
                else:
                    needs_reset = (
                        state.history_strategy_version != RECENT_V2_STRATEGY_VERSION
                        or state.professor_sync_version != professor_version
                        or state.history_start_date is None
                        or state.history_start_date > history_start_date
                    )
                if not needs_reset:
                    continue

                if state.history_strategy_version == RECENT_V2_STRATEGY_VERSION:
                    state_trigger_reason = "reconcile"
                    state_priority = RECENT_V2_PRIORITY_ACTIVE
                    state_available_at = max(now, requested_at + settle)
                state.history_strategy_version = RECENT_V2_STRATEGY_VERSION
                state.history_start_date = history_start_date
                state.trigger_reason = state_trigger_reason
                state.batch_id = batch_id
                state.available_at = state_available_at
                state.priority = state_priority
                state.professor_sync_version = professor_version
                state.historical_scan_status = ImapProfessorHistoricalScanStatus.PENDING.value
                state.last_scanned_uid = None
                state.historical_scan_started_at = None
                state.historical_scan_completed_at = None
                state.last_error = None
                touched += 1

        inbox_state = await _get_or_create_ensure_mailbox_state(session, identity_id)
        inbox_state.professor_state_fingerprint = _build_recent_v2_professor_fingerprint(
            professors,
            sent_folder,
        )
        inbox_state.last_professor_state_ensure_at = now
        await session.commit()
    return touched


async def get_recent_v2_due_summary(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> RecentV2QueueSummary:
    async with session_factory() as session:
        conditions = _recent_v2_due_conditions(identity_id, utc_now())
        rows = (
            await session.execute(
                select(
                    func.count(func.distinct(ImapProfessorSyncState.professor_id)),
                    func.sum(
                        case(
                            (ImapProfessorSyncState.folder_role == "sent", 1),
                            else_=0,
                        ),
                    ),
                    func.sum(
                        case(
                            (ImapProfessorSyncState.folder_role == "inbox", 1),
                            else_=0,
                        ),
                    ),
                    func.sum(
                        case(
                            (
                                and_(
                                    ImapProfessorSyncState.folder_role == "sent",
                                    ImapProfessorSyncState.batch_id.like("bulk:%"),
                                ),
                                1,
                            ),
                            else_=0,
                        ),
                    ),
                )
                .select_from(ImapProfessorSyncState)
                .join(Professor, Professor.id == ImapProfessorSyncState.professor_id)
                .where(*conditions),
            )
        ).one()
    professor_count, sent_count, inbox_count, bulk_sent_count = rows
    return RecentV2QueueSummary(
        professor_count=int(professor_count or 0),
        sent_state_count=int(sent_count or 0),
        inbox_state_count=int(inbox_count or 0),
        bulk_sent_state_count=int(bulk_sent_count or 0),
    )


async def claim_recent_v2_professor_scans(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    limit: int,
) -> list[ImapProfessorSyncState]:
    if limit <= 0:
        return []
    async with session_factory() as session:
        states = list(
            (
                await session.execute(
                    select(ImapProfessorSyncState)
                    .join(Professor, Professor.id == ImapProfessorSyncState.professor_id)
                    .where(
                        *_recent_v2_due_conditions(identity_id, utc_now()),
                        or_(
                            ImapProfessorSyncState.batch_id.is_(None),
                            ~ImapProfessorSyncState.batch_id.like("bulk:%"),
                        ),
                    )
                    .order_by(
                        ImapProfessorSyncState.priority.desc(),
                        ImapProfessorSyncState.available_at.asc(),
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


async def prepare_recent_v2_bulk_sent_batch(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> tuple[str | None, list[int]]:
    async with session_factory() as session:
        now = utc_now()
        states = list(
            (
                await session.execute(
                    select(ImapProfessorSyncState)
                    .join(Professor, Professor.id == ImapProfessorSyncState.professor_id)
                    .where(
                        *_recent_v2_due_conditions(identity_id, now),
                        ImapProfessorSyncState.folder_role == "sent",
                    )
                    .order_by(
                        ImapProfessorSyncState.priority.desc(),
                        ImapProfessorSyncState.id.asc(),
                    ),
                )
            ).scalars(),
        )
        if not states:
            return None, []
        existing_batch_id = next(
            (
                state.batch_id
                for state in states
                if state.batch_id and state.batch_id.startswith("bulk:")
            ),
            None,
        )
        batch_id = existing_batch_id or f"bulk:{uuid.uuid4().hex}"
        selected = [
            state
            for state in states
            if existing_batch_id is None or state.batch_id == existing_batch_id
        ]
        if existing_batch_id is None:
            for state in selected:
                state.batch_id = batch_id
                if state.historical_scan_status == ImapProfessorHistoricalScanStatus.FAILED.value:
                    state.historical_scan_status = ImapProfessorHistoricalScanStatus.PENDING.value
                    state.last_error = None
        await session.commit()
        return batch_id, [state.id for state in selected]


async def mark_recent_v2_batch_completed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    batch_id: str,
    folder_role: str,
) -> int:
    completed = 0
    async with session_factory() as session:
        states = list(
            (
                await session.execute(
                    select(ImapProfessorSyncState).where(
                        ImapProfessorSyncState.history_strategy_version
                        == RECENT_V2_STRATEGY_VERSION,
                        ImapProfessorSyncState.batch_id == batch_id,
                        ImapProfessorSyncState.folder_role == folder_role,
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
            state.historical_scan_started_at = None
            state.last_error = None
            completed += 1
        await session.commit()
    return completed


def _recent_v2_due_conditions(identity_id: int, now) -> list[object]:
    stale_running_cutoff = now - STALE_RUNNING_SCAN_AFTER
    return [
        ImapProfessorSyncState.identity_id == identity_id,
        ImapProfessorSyncState.history_strategy_version == RECENT_V2_STRATEGY_VERSION,
        Professor.archived_at.is_(None),
        Professor.email.is_not(None),
        func.lower(func.trim(Professor.email)) == ImapProfessorSyncState.professor_email,
        Professor.communication_sync_version == ImapProfessorSyncState.professor_sync_version,
        or_(
            ImapProfessorSyncState.available_at.is_(None),
            ImapProfessorSyncState.available_at <= now,
        ),
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
            & or_(
                ImapProfessorSyncState.historical_scan_started_at.is_(None),
                ImapProfessorSyncState.historical_scan_started_at <= stale_running_cutoff,
            ),
        ),
    ]


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


def _build_recent_v2_professor_fingerprint(
    professors: Iterable[Professor],
    sent_folder: str | None,
) -> str:
    parts = [
        (
            f"{professor.id}:{_normalize_email(professor.email)}:"
            f"{max(1, professor.communication_sync_version or 1)}"
        )
        for professor in professors
        if _normalize_email(professor.email)
    ]
    parts.sort()
    payload = "\n".join([RECENT_V2_STRATEGY_VERSION, sent_folder or "", *parts])
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
