from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
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
    IdentityProfile,
    ImapMailboxHistoricalScanStatus,
    ImapMailboxSyncState,
    ImapProfessorSyncState,
    Professor,
)
from app.services.operation_logs import record_operation_log

from .. import transport as mail_runtime
from ..addresses import normalize_email_address, normalize_email_list
from ..ingestion import (
    EmailLogIngestRecord,
    ingest_sent_email_observation,
    upsert_email_log,
)
from ..transport import ReceivedEmail
from .errors import (
    is_account_level_throttle_error as _is_account_level_throttle_error,
    is_provider_throttle_error,
)
from .fetcher import ImapFetchedMessage
from .state import (
    ImapIdentitySyncClaim,
    ImapIdentitySyncLeaseLostError,
    RECENT_V2_STRATEGY_VERSION,
    bind_imap_identity_sync_claim,
    claim_next_professor_scans,
    claim_imap_identity_sync,
    claim_recent_v2_professor_scans,
    clear_identity_sent_folder_discovery_cache,
    ensure_recent_v2_professor_scan_states,
    get_recent_v2_due_summary,
    mark_professor_scan_completed,
    mark_professor_scan_failed,
    mark_recent_v2_batch_completed,
    prepare_recent_v2_bulk_sent_batch,
    commit_imap_identity_sync_session,
    release_imap_identity_sync_claim,
    reset_imap_identity_sync_claim,
    renew_imap_identity_sync_claim,
    reset_professor_scans_to_pending,
)


TASK_RELATION_OPTIONS = (
    selectinload(EmailTask.batch_task),
    selectinload(EmailTask.identity),
    selectinload(EmailTask.identity).selectinload(
        IdentityProfile.current_primary_material
    ),
    selectinload(EmailTask.llm_profile),
    selectinload(EmailTask.professor),
    selectinload(EmailTask.primary_material),
)

_IMAP_IDENTITY_LOCKS: dict[int, asyncio.Lock] = {}

_IMAP_INCREMENTAL_LOCKS: dict[int, asyncio.Lock] = {}

_IMAP_HISTORY_LOCKS: dict[int, asyncio.Lock] = {}

_IMAP_IDENTITY_LOCKS_GUARD = asyncio.Lock()

VALID_IMAP_FOLDER_ROLES = {"inbox", "sent"}

IMAP_HISTORY_THROTTLE_PREFIX = "history:"

IMAP_ACCOUNT_THROTTLE_PREFIX = "account:"

IMAP_HISTORY_BODY_FETCH_COMMANDS_PER_MESSAGE = 6

_IMAP_TASK_CANCEL_GRACE_SECONDS = 1.0

_DETACHED_IMAP_TASKS: set[asyncio.Task[object]] = set()

RECENT_HISTORY_STRATEGY_NAME = RECENT_V2_STRATEGY_VERSION

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RecentHistoryWindow:
    start_date: date
    strategy_version: str


def build_recent_history_window(now: datetime | None = None) -> RecentHistoryWindow:
    current = now or utc_now()
    start_year = current.year - 1
    return RecentHistoryWindow(
        start_date=date(start_year, 1, 1),
        strategy_version=RECENT_HISTORY_STRATEGY_NAME,
    )


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


@dataclass(slots=True)
class _RecentSentDiscoveryResult:
    detected: int
    command_count: int
    completed: bool = False


async def poll_for_replies_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session:
        identity_ids = list(
            (
                await session.execute(
                    select(IdentityProfile.id).where(
                        IdentityProfile.imap_host.is_not(None),
                        IdentityProfile.imap_port.is_not(None),
                        IdentityProfile.imap_username.is_not(None),
                        IdentityProfile.imap_password.is_not(None),
                        func.trim(IdentityProfile.imap_host) != "",
                        func.trim(IdentityProfile.imap_username) != "",
                        func.trim(IdentityProfile.imap_password) != "",
                    ),
                )
            ).scalars()
        )

    return await _run_imap_identities_bounded(
        session_factory,
        identity_ids,
        sync_identity_incremental_poll_once,
        poll_name="incremental",
    )


async def poll_imap_history_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session:
        identity_ids = list(
            (
                await session.execute(
                    select(IdentityProfile.id).where(
                        IdentityProfile.imap_host.is_not(None),
                        IdentityProfile.imap_port.is_not(None),
                        IdentityProfile.imap_username.is_not(None),
                        IdentityProfile.imap_password.is_not(None),
                        func.trim(IdentityProfile.imap_host) != "",
                        func.trim(IdentityProfile.imap_username) != "",
                        func.trim(IdentityProfile.imap_password) != "",
                    ),
                )
            ).scalars()
        )

    return await _run_imap_identities_bounded(
        session_factory,
        identity_ids,
        sync_identity_history_poll_once,
        poll_name="history",
    )


async def _run_imap_identities_bounded(
    session_factory: async_sessionmaker[AsyncSession],
    identity_ids: list[int],
    worker: Callable[[async_sessionmaker[AsyncSession], int], Awaitable[int]],
    *,
    poll_name: str,
) -> int:
    if not identity_ids:
        return 0
    semaphore = asyncio.Semaphore(get_settings().imap_identity_concurrency)

    async def run_identity(identity_id: int) -> int:
        async with semaphore:
            try:
                return await worker(session_factory, identity_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "IMAP %s poll failed for identity_id=%s",
                    poll_name,
                    identity_id,
                )
                return 0

    return sum(await asyncio.gather(*(run_identity(item) for item in identity_ids)))


async def poll_identity_replies(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> int:
    return await sync_identity_imap_once(session_factory, identity_id)


async def sync_identity_imap_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> int:
    lock = await _get_imap_identity_lock(identity_id)
    incremental_lock = await _get_imap_incremental_lock(identity_id)
    history_lock = await _get_imap_history_lock(identity_id)
    if lock.locked() or incremental_lock.locked() or history_lock.locked():
        return 0
    async with lock:
        if incremental_lock.locked() or history_lock.locked():
            return 0
        return await _run_identity_sync_with_lease(
            session_factory,
            identity_id,
            claim_kind="full",
            operation=lambda: _sync_identity_imap_once_unlocked(
                session_factory,
                identity_id,
            ),
        )


async def sync_identity_incremental_poll_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> int:
    identity_lock = await _get_imap_identity_lock(identity_id)
    if identity_lock.locked():
        return 0
    history_lock = await _get_imap_history_lock(identity_id)
    if history_lock.locked():
        return 0
    lock = await _get_imap_incremental_lock(identity_id)
    if lock.locked():
        return 0
    async with lock:
        if identity_lock.locked() or history_lock.locked():
            return 0
        return await _run_identity_sync_with_lease(
            session_factory,
            identity_id,
            claim_kind="incremental",
            operation=lambda: _sync_identity_incremental_once_unlocked(
                session_factory,
                identity_id,
            ),
        )


async def sync_identity_history_poll_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> int:
    if await _is_imap_history_work_locked(identity_id):
        return 0
    lock = await _get_imap_history_lock(identity_id)
    async with lock:
        if await _is_imap_identity_locked(identity_id):
            return 0
        if (await _get_imap_incremental_lock(identity_id)).locked():
            return 0
        if await is_imap_history_paused(session_factory, identity_id):
            return 0
        return await _run_identity_sync_with_lease(
            session_factory,
            identity_id,
            claim_kind="history",
            operation=lambda: sync_identity_history_once(
                session_factory,
                identity_id,
            ),
        )


async def _run_identity_sync_with_lease(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    claim_kind: str,
    operation: Callable[[], Awaitable[int]],
) -> int:
    settings = get_settings()
    claim = await claim_imap_identity_sync(
        session_factory,
        identity_id,
        claim_kind=claim_kind,
        lease_seconds=settings.imap_identity_lease_seconds,
    )
    if claim is None:
        return 0

    async def run_bound_operation() -> int:
        token = bind_imap_identity_sync_claim(
            claim,
            lease_seconds=settings.imap_identity_lease_seconds,
        )
        try:
            return await operation()
        finally:
            reset_imap_identity_sync_claim(token)

    work_task = asyncio.create_task(run_bound_operation())
    heartbeat_task = asyncio.create_task(
        _run_imap_identity_heartbeat(
            session_factory,
            claim,
            lease_seconds=settings.imap_identity_lease_seconds,
        )
    )
    release_claim = False
    try:
        async with asyncio.timeout(settings.imap_identity_sync_timeout_seconds):
            done, _ = await asyncio.wait(
                {work_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if work_task in done:
                release_claim = True
                result = await work_task
                claim_is_current = (
                    await heartbeat_task
                    if heartbeat_task in done
                    else await _renew_imap_identity_claim_for_completion(
                        session_factory,
                        claim,
                        lease_seconds=settings.imap_identity_lease_seconds,
                    )
                )
                if not claim_is_current:
                    release_claim = False
                    return 0
                return result
            claim_is_current = await heartbeat_task
            if not claim_is_current:
                await _cancel_imap_task_with_grace(work_task)
                return 0
            return await work_task
    except ImapIdentitySyncLeaseLostError:
        release_claim = False
        logger.warning(
            "IMAP identity sync stopped after lease loss: identity_id=%s kind=%s",
            identity_id,
            claim_kind,
        )
        return 0
    except TimeoutError:
        await _cancel_imap_task_with_grace(work_task)
        logger.warning(
            "IMAP identity sync timed out: identity_id=%s kind=%s timeout=%ss",
            identity_id,
            claim_kind,
            settings.imap_identity_sync_timeout_seconds,
        )
        return 0
    except asyncio.CancelledError:
        await _cancel_imap_task_with_grace(work_task)
        raise
    finally:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
        if release_claim:
            try:
                await release_imap_identity_sync_claim(session_factory, claim)
            except Exception:
                logger.exception(
                    "IMAP identity lease release failed: identity_id=%s kind=%s",
                    claim.identity_id,
                    claim.claim_kind,
                )


async def _renew_imap_identity_claim_for_completion(
    session_factory: async_sessionmaker[AsyncSession],
    claim: ImapIdentitySyncClaim,
    *,
    lease_seconds: int,
) -> bool:
    try:
        return await renew_imap_identity_sync_claim(
            session_factory,
            claim,
            lease_seconds=lease_seconds,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "IMAP identity lease completion check failed: identity_id=%s kind=%s",
            claim.identity_id,
            claim.claim_kind,
        )
        return False


async def _cancel_imap_task_with_grace(task: asyncio.Task[object]) -> None:
    if task.done():
        with suppress(asyncio.CancelledError, Exception):
            task.result()
        return
    if task.cancelling() == 0:
        task.cancel()
    done, _ = await asyncio.wait(
        {task},
        timeout=_IMAP_TASK_CANCEL_GRACE_SECONDS,
    )
    if task in done:
        with suppress(asyncio.CancelledError, Exception):
            task.result()
        return
    _DETACHED_IMAP_TASKS.add(task)
    task.add_done_callback(_consume_detached_imap_task_result)


def _consume_detached_imap_task_result(task: asyncio.Task[object]) -> None:
    _DETACHED_IMAP_TASKS.discard(task)
    with suppress(asyncio.CancelledError, Exception):
        task.result()


async def _run_imap_identity_heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    claim: ImapIdentitySyncClaim,
    *,
    lease_seconds: int,
) -> bool:
    interval = max(1.0, min(30.0, lease_seconds / 3))
    while True:
        await asyncio.sleep(interval)
        try:
            current = await renew_imap_identity_sync_claim(
                session_factory,
                claim,
                lease_seconds=lease_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "IMAP identity lease heartbeat failed: identity_id=%s kind=%s",
                claim.identity_id,
                claim.claim_kind,
            )
            return False
        if not current:
            return False


async def get_cached_or_discover_sent_folder(
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityProfile,
) -> str | None:
    settings = get_settings()
    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity.id,
            folder_role="sent",
            folder="Sent",
        )
        if state.discovered_sent_folder:
            return state.discovered_sent_folder
        if state.sent_folder_discovery_failed_at is not None:
            retry_at = state.sent_folder_discovery_failed_at + timedelta(
                seconds=settings.imap_sent_folder_failure_ttl_seconds,
            )
            if utc_now() < retry_at:
                return None

    try:
        sent_folder = await mail_runtime.discover_sent_folder(identity)
    except Exception as exc:
        await _record_sent_folder_discovery_failure(
            session_factory, identity.id, str(exc)
        )
        if is_provider_throttle_error(exc):
            await mark_imap_throttled(
                session_factory,
                identity.id,
                reason=str(exc),
                account_level=_is_account_level_throttle_error(exc),
            )
        return None

    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity.id,
            folder_role="sent",
            folder="Sent",
        )
        if sent_folder:
            state.discovered_sent_folder = sent_folder
            state.sent_folder_discovered_at = utc_now()
            state.sent_folder_discovery_failed_at = None
            state.sent_folder_discovery_error = None
        else:
            state.sent_folder_discovery_failed_at = utc_now()
            state.sent_folder_discovery_error = "Sent folder not found"
        await commit_imap_identity_sync_session(session)
    return sent_folder


async def _record_sent_folder_discovery_failure(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    error: str,
) -> None:
    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role="sent",
            folder="Sent",
        )
        state.sent_folder_discovery_failed_at = utc_now()
        state.sent_folder_discovery_error = error
        await commit_imap_identity_sync_session(session)


async def mark_imap_throttled(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    reason: str,
    account_level: bool,
) -> None:
    settings = get_settings()
    prefix = (
        IMAP_ACCOUNT_THROTTLE_PREFIX if account_level else IMAP_HISTORY_THROTTLE_PREFIX
    )
    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role="inbox",
            folder="INBOX",
        )
        state.throttle_paused_until = utc_now() + timedelta(
            seconds=settings.imap_throttle_backoff_seconds
        )
        state.throttle_reason = f"{prefix}{reason}"
        await commit_imap_identity_sync_session(session)


async def is_imap_history_paused(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> bool:
    reason = await _get_active_imap_throttle_reason(session_factory, identity_id)
    return reason is not None


async def is_imap_incremental_paused(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> bool:
    reason = await _get_active_imap_throttle_reason(session_factory, identity_id)
    return bool(reason and reason.startswith(IMAP_ACCOUNT_THROTTLE_PREFIX))


async def _get_active_imap_throttle_reason(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> str | None:
    async with session_factory() as session:
        state = await session.scalar(
            select(ImapMailboxSyncState).where(
                ImapMailboxSyncState.identity_id == identity_id,
                ImapMailboxSyncState.folder_role == "inbox",
                ImapMailboxSyncState.folder == "INBOX",
            ),
        )
        if state is None or state.throttle_paused_until is None:
            return None
        if state.throttle_paused_until <= utc_now():
            state.throttle_paused_until = None
            state.throttle_reason = None
            await commit_imap_identity_sync_session(session)
            return None
        return state.throttle_reason or ""


async def log_imap_history_progress(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    folders: list[tuple[str, str]] | None = None,
) -> None:
    folder_conditions = [
        and_(
            ImapMailboxSyncState.folder_role == folder_role,
            ImapMailboxSyncState.folder == folder,
        )
        for folder_role, folder in (folders or [])
    ]
    mailbox_where = [ImapMailboxSyncState.identity_id == identity_id]
    if folders is not None:
        mailbox_where.append(or_(*folder_conditions) if folder_conditions else False)
    async with session_factory() as session:
        mailbox_rows = (
            await session.execute(
                select(
                    ImapMailboxSyncState.history_scan_status,
                    func.count(ImapMailboxSyncState.id),
                )
                .where(*mailbox_where)
                .group_by(ImapMailboxSyncState.history_scan_status),
            )
        ).all()
        professor_rows = (
            await session.execute(
                select(
                    ImapProfessorSyncState.historical_scan_status,
                    func.count(ImapProfessorSyncState.id),
                )
                .where(ImapProfessorSyncState.identity_id == identity_id)
                .group_by(ImapProfessorSyncState.historical_scan_status),
            )
        ).all()
        progress = await session.execute(
            select(
                func.coalesce(func.sum(ImapMailboxSyncState.history_scanned_count), 0),
                func.coalesce(func.sum(ImapMailboxSyncState.history_matched_count), 0),
            ).where(*mailbox_where),
        )
        scanned_count, matched_count = progress.one()
        mailbox_counts = {status: count for status, count in mailbox_rows}
        professor_counts = {status: count for status, count in professor_rows}
    logger.info(
        (
            "imap_history_progress identity_id=%s mailbox_pending=%s mailbox_completed=%s "
            "mailbox_failed=%s mailbox_running=%s scanned=%s matched=%s "
            "targeted_pending=%s targeted_completed=%s targeted_failed=%s targeted_running=%s"
        ),
        identity_id,
        mailbox_counts.get("pending", 0),
        mailbox_counts.get("completed", 0),
        mailbox_counts.get("failed", 0),
        mailbox_counts.get("running", 0),
        scanned_count,
        matched_count,
        professor_counts.get("pending", 0),
        professor_counts.get("completed", 0),
        professor_counts.get("failed", 0),
        professor_counts.get("running", 0),
    )


async def _sync_identity_imap_once_unlocked(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> int:
    incremental_detected = await _sync_identity_incremental_once_unlocked(
        session_factory,
        identity_id,
    )
    history_detected = 0
    if not await is_imap_history_paused(session_factory, identity_id):
        history_detected = await sync_identity_history_once(
            session_factory, identity_id
        )
    return incremental_detected + history_detected


async def _sync_identity_incremental_once_unlocked(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> int:
    sent_folder = None
    incremental_paused = await is_imap_incremental_paused(session_factory, identity_id)
    async with session_factory() as session:
        identity = await session.get(IdentityProfile, identity_id)
    if identity is not None and not incremental_paused:
        sent_folder = await get_cached_or_discover_sent_folder(
            session_factory, identity
        )

    if await is_imap_incremental_paused(session_factory, identity_id):
        return 0

    inbox_detected = 0
    if not incremental_paused:
        inbox_detected = await sync_identity_incremental_once(
            session_factory,
            identity_id,
            folder_role="inbox",
            folder="INBOX",
        )
    sent_detected = 0
    if sent_folder and not await is_imap_incremental_paused(
        session_factory, identity_id
    ):
        sent_detected = await sync_identity_incremental_once(
            session_factory,
            identity_id,
            folder_role="sent",
            folder=sent_folder,
        )
    return inbox_detected + sent_detected


async def sync_identity_history_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> int:
    settings = get_settings()
    if await is_imap_history_paused(session_factory, identity_id):
        return 0
    if (
        settings.imap_history_batch_size <= 0
        or settings.imap_history_command_budget_per_minute <= 0
    ):
        return 0

    async with session_factory() as session:
        identity = await session.get(IdentityProfile, identity_id)
    if identity is None:
        return 0
    if not _identity_has_imap_config(identity):
        return 0

    sent_folder = await get_cached_or_discover_sent_folder(session_factory, identity)
    if await is_imap_history_paused(session_factory, identity_id):
        return 0

    window = build_recent_history_window()
    await ensure_recent_v2_professor_scan_states(
        session_factory,
        identity_id=identity_id,
        sent_folder=sent_folder,
        history_start_date=window.start_date,
        settle_seconds=_int_setting(settings, "imap_history_queue_settle_seconds", 0),
    )
    queue_summary = await get_recent_v2_due_summary(session_factory, identity_id)
    if queue_summary.professor_count <= 0:
        return 0

    command_budget = settings.imap_history_command_budget_per_minute
    sent_discovery = _RecentSentDiscoveryResult(
        detected=0,
        command_count=0,
    )
    bulk_probe: mail_runtime.ImapMailboxUidSearchResult | None = None
    use_bulk_sent = queue_summary.bulk_sent_state_count > 0
    if (
        not use_bulk_sent
        and sent_folder
        and queue_summary.sent_state_count > 0
        and command_budget > 0
        and queue_summary.professor_count
        > _recent_v2_targeted_professor_limit(settings)
    ):
        try:
            probe = await mail_runtime.search_mailbox_uids_since_date(
                identity,
                sent_folder,
                window.start_date,
            )
            command_budget = max(0, command_budget - probe.command_count)
            use_bulk_sent = _should_use_recent_v2_bulk_sent(
                professor_count=queue_summary.professor_count,
                recent_sent_uid_count=probe.uid_count,
                settings=settings,
            )
            if use_bulk_sent:
                bulk_probe = probe
        except Exception as exc:
            if is_provider_throttle_error(exc):
                await mark_imap_throttled(
                    session_factory,
                    identity_id,
                    reason=str(exc),
                    account_level=_is_account_level_throttle_error(exc),
                )
                return 0
            logger.warning(
                "imap recent-v2 sent probe failed; falling back to targeted sync identity_id=%s error=%s",
                identity_id,
                exc,
            )

    if use_bulk_sent and sent_folder and command_budget > 0:
        batch_id, _state_ids = await prepare_recent_v2_bulk_sent_batch(
            session_factory,
            identity_id,
        )
        if batch_id:
            try:
                sent_discovery = await _sync_recent_sent_history_once(
                    session_factory,
                    identity,
                    identity_id=identity_id,
                    sent_folder=sent_folder,
                    window=window,
                    command_budget=command_budget,
                    batch_id=batch_id,
                    known_uids=(
                        bulk_probe.uids
                        if bulk_probe is not None
                        and len(bulk_probe.uids) == bulk_probe.uid_count
                        else None
                    ),
                    known_uidvalidity=(
                        bulk_probe.uidvalidity if bulk_probe is not None else None
                    ),
                )
                if sent_discovery.completed:
                    await mark_recent_v2_batch_completed(
                        session_factory,
                        batch_id=batch_id,
                        folder_role="sent",
                    )
            except Exception as exc:
                if is_provider_throttle_error(exc):
                    await mark_imap_throttled(
                        session_factory,
                        identity_id,
                        reason=str(exc),
                        account_level=_is_account_level_throttle_error(exc),
                    )
                await _mark_recent_sent_history_failed(
                    session_factory,
                    identity_id=identity_id,
                    sent_folder=sent_folder,
                    error=exc,
                )
                await log_imap_history_progress(
                    session_factory,
                    identity_id,
                    folders=[("inbox", "INBOX")],
                )
                return 0
            command_budget = max(0, command_budget - sent_discovery.command_count)

    targeted_detected = await _sync_identity_targeted_history_once(
        session_factory,
        identity_id,
        mailbox_folders=_mailbox_history_folder_specs(sent_folder),
        since_date=window.start_date,
        strategy_version=window.strategy_version,
        command_budget=command_budget,
    )
    await log_imap_history_progress(
        session_factory, identity_id, folders=[("inbox", "INBOX")]
    )
    return sent_discovery.detected + targeted_detected


def _recent_v2_targeted_professor_limit(settings) -> int:
    effective_rate = min(
        _int_setting(settings, "imap_history_command_budget_per_minute", 120),
        _int_setting(settings, "imap_history_command_rate_per_minute", 40),
    )
    reserved_search_budget = max(1, effective_rate * 75 // 100)
    return max(1, reserved_search_budget // 2)


def _should_use_recent_v2_bulk_sent(
    *,
    professor_count: int,
    recent_sent_uid_count: int,
    settings,
) -> bool:
    if professor_count <= _recent_v2_targeted_professor_limit(settings):
        return False
    if recent_sent_uid_count > _int_setting(
        settings, "imap_history_bulk_header_limit", 5000
    ):
        return False
    header_batch_size = max(1, _int_setting(settings, "imap_fetch_batch_size", 20))
    bulk_command_cost = (
        1 + (recent_sent_uid_count + header_batch_size - 1) // header_batch_size
    )
    targeted_sent_command_cost = professor_count
    return bulk_command_cost < targeted_sent_command_cost


def _int_setting(settings, name: str, default: int) -> int:
    value = getattr(settings, name, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


async def _sync_recent_sent_history_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityProfile,
    *,
    identity_id: int,
    sent_folder: str,
    window: RecentHistoryWindow,
    command_budget: int,
    batch_id: str,
    known_uids: tuple[int, ...] | None = None,
    known_uidvalidity: int | None = None,
) -> _RecentSentDiscoveryResult:
    if command_budget <= 0:
        return _RecentSentDiscoveryResult(
            detected=0,
            command_count=0,
            completed=False,
        )

    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role="sent",
            folder=sent_folder,
        )
        if (
            state.history_strategy_version != window.strategy_version
            or state.history_batch_id != batch_id
        ):
            state.history_strategy_version = window.strategy_version
            state.history_batch_id = batch_id
            state.history_high_water_uid = None
            state.history_next_before_uid = None
            state.history_scan_status = "sent_recent_discovery_pending"
            state.history_scanned_count = 0
            state.history_matched_count = 0
            state.history_last_error = None
        min_uid = state.history_high_water_uid
        expected_uidvalidity = state.uidvalidity
        await commit_imap_identity_sync_session(session)

    if known_uids is None:
        header_result = await mail_runtime.fetch_recent_mailbox_message_headers_since(
            identity,
            sent_folder,
            window.start_date,
            min_uid=min_uid,
            max_fetch_batches=max(0, command_budget - 1),
            expected_uidvalidity=expected_uidvalidity,
        )
    else:
        header_result = await mail_runtime.fetch_recent_mailbox_message_headers_since(
            identity,
            sent_folder,
            window.start_date,
            min_uid=min_uid,
            max_fetch_batches=max(0, command_budget - 1),
            expected_uidvalidity=expected_uidvalidity,
            known_uids=known_uids,
            known_uidvalidity=known_uidvalidity,
        )
    if header_result.command_count > command_budget:
        raise RuntimeError(
            "IMAP history command budget exhausted during recent sent header fetch"
        )
    remaining_command_budget = command_budget - header_result.command_count
    matched_headers = await _match_recent_sent_headers(
        session_factory,
        header_result.messages,
    )
    body_result = await _fetch_recent_sent_message_bodies(
        session_factory,
        identity,
        identity_id=identity_id,
        folder=sent_folder,
        matched_headers=matched_headers,
        remaining_command_budget=remaining_command_budget,
    )
    detected = await process_imap_fetched_messages(
        session_factory,
        identity_id,
        body_result.messages,
        folder_role="sent",
        folder=sent_folder,
    )
    covered_recent_headers = (
        not header_result.exhausted and body_result.covered_all_headers
    )
    high_water_uid, safe_scanned_count, safe_matched_count = (
        _recent_sent_safe_scan_progress(
            None if header_result.uidvalidity_changed else min_uid,
            header_result.messages,
            matched_headers,
            body_result.safe_match_uids,
        )
    )
    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role="sent",
            folder=sent_folder,
        )
        if header_result.uidvalidity is not None:
            state.uidvalidity = header_result.uidvalidity
        state.history_high_water_uid = high_water_uid
        state.history_next_before_uid = None
        state.history_scan_status = (
            "completed" if covered_recent_headers else "sent_recent_discovery_running"
        )
        state.history_scan_completed_at = utc_now() if covered_recent_headers else None
        state.history_scanned_count = (
            state.history_scanned_count or 0
        ) + safe_scanned_count
        state.history_matched_count = (
            state.history_matched_count or 0
        ) + safe_matched_count
        state.history_last_error = None
        await commit_imap_identity_sync_session(session)

    return _RecentSentDiscoveryResult(
        detected=detected,
        command_count=header_result.command_count + body_result.command_count,
        completed=covered_recent_headers,
    )


async def _mark_recent_sent_history_failed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    sent_folder: str,
    error: Exception,
) -> None:
    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role="sent",
            folder=sent_folder,
        )
        state.history_scan_status = "sent_recent_discovery_failed"
        state.history_last_error = str(error)
        await commit_imap_identity_sync_session(session)


async def _match_recent_sent_headers(
    session_factory: async_sessionmaker[AsyncSession],
    header_messages: list[ImapFetchedMessage],
) -> list[_MailboxHistoryHeaderMatch]:
    if not header_messages:
        return []
    professor_ids_by_email = await _load_active_professor_ids_by_email(session_factory)
    if not professor_ids_by_email:
        return []

    matches: list[_MailboxHistoryHeaderMatch] = []
    for message in header_messages:
        candidate_emails = normalize_email_list(
            [*message.to_emails, *message.cc_emails, *message.bcc_emails],
        )
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


def _recent_sent_safe_scan_progress(
    min_uid: int | None,
    header_messages: list[ImapFetchedMessage],
    matched_headers: list[_MailboxHistoryHeaderMatch],
    safe_match_uids: tuple[int, ...],
) -> tuple[int | None, int, int]:
    matched_uids = {match.message.uid for match in matched_headers}
    safe_matched_uids = set(safe_match_uids)
    high_water_uid = min_uid
    scanned_count = 0
    matched_count = 0
    for message in sorted(header_messages, key=lambda item: item.uid):
        if message.uid in matched_uids and message.uid not in safe_matched_uids:
            break
        high_water_uid = _max_optional_uid(high_water_uid, message.uid)
        scanned_count += 1
        if message.uid in matched_uids:
            matched_count += 1
    return high_water_uid, scanned_count, matched_count


def _mailbox_history_folder_specs(sent_folder: str | None) -> list[tuple[str, str]]:
    specs = [("inbox", "INBOX")]
    if sent_folder:
        specs.append(("sent", sent_folder))
    return specs


async def _load_mailbox_uidvalidity(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    folder_role: str,
    folder: str,
) -> int | None:
    async with session_factory() as session:
        return await session.scalar(
            select(ImapMailboxSyncState.uidvalidity).where(
                ImapMailboxSyncState.identity_id == identity_id,
                ImapMailboxSyncState.folder_role == folder_role,
                ImapMailboxSyncState.folder == folder,
            ),
        )


async def _record_targeted_mailbox_uidvalidity(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    folder_role: str,
    folder: str,
    uidvalidity: int,
) -> None:
    async with session_factory() as session:
        mailbox_state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role=folder_role,
            folder=folder,
        )
        mailbox_state.uidvalidity = uidvalidity
        await commit_imap_identity_sync_session(session)


async def _reset_other_targeted_professor_cursors_for_uidvalidity_change(
    session_factory: async_sessionmaker[AsyncSession],
    current_state: ImapProfessorSyncState,
) -> None:
    async with session_factory() as session:
        sibling_states = list(
            (
                await session.execute(
                    select(ImapProfessorSyncState).where(
                        ImapProfessorSyncState.identity_id == current_state.identity_id,
                        ImapProfessorSyncState.folder_role == current_state.folder_role,
                        ImapProfessorSyncState.folder == current_state.folder,
                        ImapProfessorSyncState.history_strategy_version
                        == current_state.history_strategy_version,
                        ImapProfessorSyncState.id != current_state.id,
                    ),
                )
            ).scalars(),
        )
        for sibling in sibling_states:
            sibling.last_scanned_uid = None
            sibling.historical_scan_status = "pending"
            sibling.historical_scan_started_at = None
            sibling.historical_scan_completed_at = None
            sibling.history_claim_id = None
            sibling.history_lease_expires_at = None
            sibling.last_error = None
        await commit_imap_identity_sync_session(session)


async def _sync_identity_targeted_history_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    command_budget: int | None = None,
    mailbox_folders: list[tuple[str, str]] | None = None,
    since_date: date | None = None,
    strategy_version: str | None = None,
) -> int:
    settings = get_settings()
    if await is_imap_history_paused(session_factory, identity_id):
        return 0
    effective_command_budget = (
        settings.imap_history_command_budget_per_minute
        if command_budget is None
        else command_budget
    )
    claim_limit = min(
        settings.imap_history_batch_size,
        effective_command_budget,
    )
    if claim_limit <= 0:
        return 0
    if strategy_version == RECENT_V2_STRATEGY_VERSION:
        states = await claim_recent_v2_professor_scans(
            session_factory,
            identity_id,
            limit=claim_limit,
        )
    else:
        states = await claim_next_professor_scans(
            session_factory,
            identity_id,
            limit=claim_limit,
            strategy_version=strategy_version,
        )
    if not states:
        return 0
    claim_ids = {
        state.id: state.history_claim_id
        for state in states
        if state.history_claim_id is not None
    }
    if mailbox_folders is not None:
        allowed_folders = set(mailbox_folders)
        allowed_states = [
            state
            for state in states
            if (state.folder_role, state.folder) in allowed_folders
        ]
        disallowed_state_ids = [
            state.id
            for state in states
            if (state.folder_role, state.folder) not in allowed_folders
        ]
        if disallowed_state_ids:
            await reset_professor_scans_to_pending(
                session_factory,
                disallowed_state_ids,
                claim_ids=claim_ids,
            )
        states = allowed_states
        if not states:
            return 0
    detected_total = 0
    command_budget = effective_command_budget
    for index, state in enumerate(states):
        try:
            async with session_factory() as session:
                identity = await session.get(IdentityProfile, identity_id)
            if identity is None:
                await mark_professor_scan_completed(
                    session_factory,
                    state.id,
                    state.last_scanned_uid,
                    claim_id=state.history_claim_id,
                )
                continue
            if command_budget <= 0:
                raise RuntimeError("IMAP history command budget exhausted")
            header_search_reserve = 4 if state.folder_role == "sent" else 1
            header_fetch_budget = max(
                0,
                command_budget
                - header_search_reserve
                - _history_body_fetch_command_count(1, settings.imap_fetch_batch_size),
            )
            expected_uidvalidity: int | None | object = mail_runtime._UIDVALIDITY_UNSET
            if state.folder_role == "inbox":
                expected_uidvalidity = await _load_mailbox_uidvalidity(
                    session_factory,
                    identity_id=identity_id,
                    folder_role=state.folder_role,
                    folder=state.folder,
                )
            header_result = await mail_runtime.fetch_professor_history_mailbox_message_headers_with_command_count(
                identity,
                state.folder,
                state.professor_email,
                folder_role=state.folder_role,
                min_uid=state.last_scanned_uid,
                max_fetch_batches=header_fetch_budget,
                since_date=since_date,
                expected_uidvalidity=expected_uidvalidity,
            )
            if header_result.command_count > command_budget:
                raise RuntimeError(
                    "IMAP history command budget exhausted during header fetch"
                )
            inbox_uidvalidity_changed = (
                state.folder_role == "inbox" and header_result.uidvalidity_changed
            )
            if state.folder_role == "inbox" and header_result.uidvalidity is not None:
                await _record_targeted_mailbox_uidvalidity(
                    session_factory,
                    identity_id=identity_id,
                    folder_role=state.folder_role,
                    folder=state.folder,
                    uidvalidity=header_result.uidvalidity,
                )
            if inbox_uidvalidity_changed:
                await _reset_other_targeted_professor_cursors_for_uidvalidity_change(
                    session_factory,
                    state,
                )
                state.last_scanned_uid = None
            command_budget -= header_result.command_count
            body_result = await _fetch_missing_history_message_bodies(
                session_factory,
                identity,
                identity_id=identity_id,
                state=state,
                header_messages=header_result.messages,
                remaining_command_budget=command_budget,
            )
            messages = body_result.messages
            command_budget -= body_result.command_count
            detected = await process_imap_fetched_messages(
                session_factory,
                identity_id,
                messages,
                folder_role=state.folder_role,
                folder=state.folder,
            )
            max_uid = body_result.highest_scanned_uid
            if header_result.exhausted or not body_result.covered_all_headers:
                await reset_professor_scans_to_pending(
                    session_factory,
                    [state.id],
                    claim_ids=claim_ids,
                    last_scanned_uids={state.id: max_uid},
                )
                await reset_professor_scans_to_pending(
                    session_factory,
                    [pending_state.id for pending_state in states[index + 1 :]],
                    claim_ids=claim_ids,
                )
                detected_total += detected
                break
            await mark_professor_scan_completed(
                session_factory,
                state.id,
                max_uid,
                claim_id=state.history_claim_id,
            )
            detected_total += detected
            if inbox_uidvalidity_changed:
                break
        except Exception as exc:
            if _is_history_command_budget_error(exc):
                await reset_professor_scans_to_pending(
                    session_factory,
                    [pending_state.id for pending_state in states[index:]],
                    claim_ids=claim_ids,
                )
                break
            if is_provider_throttle_error(exc):
                await mark_imap_throttled(
                    session_factory,
                    identity_id,
                    reason=str(exc),
                    account_level=_is_account_level_throttle_error(exc),
                )
                await reset_professor_scans_to_pending(
                    session_factory,
                    [pending_state.id for pending_state in states[index + 1 :]],
                    claim_ids=claim_ids,
                )
            await mark_professor_scan_failed(
                session_factory,
                state.id,
                str(exc),
                claim_id=state.history_claim_id,
            )
            if is_provider_throttle_error(exc):
                break
    await log_imap_history_progress(
        session_factory, identity_id, folders=mailbox_folders
    )
    return detected_total


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
                            EmailObservation.normalized_message_id == normalized_message_id,
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
                            EmailObservation.normalized_message_id == normalized_message_id,
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


async def sync_identity_incremental_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    folder_role: str = "inbox",
    folder: str = "INBOX",
) -> int:
    _validate_imap_folder_role(folder_role)
    async with session_factory() as session:
        identity = await session.get(IdentityProfile, identity_id)
        if identity is None:
            return 0
        state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role=folder_role,
            folder=folder,
        )
        if not _identity_has_imap_config(identity):
            state.last_error = None
            await commit_imap_identity_sync_session(session)
            return 0
        last_seen_uid = state.last_seen_uid
        expected_uidvalidity = state.uidvalidity
        should_bootstrap_history_cursor = (
            last_seen_uid is None
            and state.history_high_water_uid is None
            and state.history_scan_status
            != ImapMailboxHistoricalScanStatus.COMPLETED.value
        )
        await commit_imap_identity_sync_session(session)
    try:
        if should_bootstrap_history_cursor:
            bootstrap_result = (
                await mail_runtime.fetch_history_mailbox_message_headers_before_uid(
                    identity,
                    folder,
                    before_uid=None,
                    limit=0,
                    max_fetch_batches=0,
                    expected_uidvalidity=expected_uidvalidity,
                )
            )
            if bootstrap_result.high_water_uid is not None:
                async with session_factory() as session:
                    state = await _get_or_create_mailbox_state(
                        session,
                        identity_id,
                        folder_role=folder_role,
                        folder=folder,
                    )
                    if bootstrap_result.uidvalidity is not None:
                        state.uidvalidity = bootstrap_result.uidvalidity
                    state.history_high_water_uid = bootstrap_result.high_water_uid
                    state.history_next_before_uid = bootstrap_result.next_before_uid
                    state.last_seen_uid = max(
                        state.last_seen_uid or 0, bootstrap_result.high_water_uid
                    )
                    state.last_sync_at = utc_now()
                    state.last_error = None
                    await commit_imap_identity_sync_session(session)
                return 0
            async with session_factory() as session:
                state = await _get_or_create_mailbox_state(
                    session,
                    identity_id,
                    folder_role=folder_role,
                    folder=folder,
                )
                if bootstrap_result.uidvalidity is not None:
                    state.uidvalidity = bootstrap_result.uidvalidity
                state.last_sync_at = utc_now()
                state.last_error = "IMAP high-water bootstrap failed; incremental sync skipped to avoid full mailbox fetch"
                await commit_imap_identity_sync_session(session)
            return 0
        fetch_with_uidvalidity = getattr(
            mail_runtime,
            "fetch_incremental_mailbox_messages_with_uidvalidity",
            None,
        )
        used_uidvalidity_aware_fetch = callable(fetch_with_uidvalidity)
        if used_uidvalidity_aware_fetch:
            max_seen_uid, messages, current_uidvalidity = await fetch_with_uidvalidity(
                identity,
                folder,
                last_seen_uid,
                expected_uidvalidity=expected_uidvalidity,
            )
        else:
            (
                max_seen_uid,
                messages,
            ) = await mail_runtime.fetch_incremental_mailbox_messages(
                identity,
                folder,
                last_seen_uid,
            )
            current_uidvalidity = _resolve_messages_uidvalidity(messages)
    except Exception as exc:
        async with session_factory() as session:
            state = await _get_or_create_mailbox_state(
                session,
                identity_id,
                folder_role=folder_role,
                folder=folder,
            )
            state.last_error = str(exc)
            await commit_imap_identity_sync_session(session)
        if is_provider_throttle_error(exc):
            await mark_imap_throttled(
                session_factory,
                identity_id,
                reason=str(exc),
                account_level=True,
            )
        if folder_role == "sent" and _is_imap_mailbox_selection_error(exc):
            await clear_identity_sent_folder_discovery_cache(
                session_factory, identity_id
            )
        return 0
    detected = await process_imap_fetched_messages(
        session_factory,
        identity_id,
        messages,
        folder_role=folder_role,
        folder=folder,
    )
    async with session_factory() as session:
        state = await _get_or_create_mailbox_state(
            session,
            identity_id,
            folder_role=folder_role,
            folder=folder,
        )
        uidvalidity_changed = (
            current_uidvalidity is not None and current_uidvalidity != state.uidvalidity
        )
        if uidvalidity_changed:
            state.last_seen_uid = None
        if current_uidvalidity is not None:
            state.uidvalidity = current_uidvalidity
        should_apply_max_seen_uid = not (
            uidvalidity_changed and not used_uidvalidity_aware_fetch
        )
        if max_seen_uid is not None and should_apply_max_seen_uid:
            state.last_seen_uid = max(state.last_seen_uid or 0, max_seen_uid)
        state.last_sync_at = utc_now()
        state.last_error = None
        await commit_imap_identity_sync_session(session)
    return detected


def _resolve_messages_uidvalidity(messages: list[ImapFetchedMessage]) -> int | None:
    for message in messages:
        if message.uidvalidity is not None:
            return message.uidvalidity
    return None


def _is_imap_mailbox_selection_error(exc: object) -> bool:
    text = str(exc).lower()
    return (
        "imap 选择邮箱文件夹失败" in text
        or "select" in text
        and "mailbox" in text
        or "no such mailbox" in text
    )


def _is_history_command_budget_error(exc: object) -> bool:
    return "imap history command budget" in str(exc).lower()


def _identity_has_imap_config(identity: IdentityProfile) -> bool:
    return bool(
        identity.imap_host
        and str(identity.imap_host).strip()
        and identity.imap_port
        and identity.imap_username
        and str(identity.imap_username).strip()
        and identity.imap_password
        and str(identity.imap_password).strip()
    )


async def sync_workspace_professor_replies(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    professor_id: int,
) -> int:
    if await _is_imap_history_work_locked(identity_id):
        return 0
    lock = await _get_imap_history_lock(identity_id)
    async with lock:
        if await _is_imap_identity_locked(identity_id):
            return 0
        async with session_factory() as session:
            identity = await session.get(IdentityProfile, identity_id)
            professor = await session.get(Professor, professor_id)
        if identity is None or professor is None or not professor.email:
            return 0
        messages = await mail_runtime.fetch_professor_history_inbox_messages(
            identity,
            professor.email,
        )
        return await process_imap_fetched_messages(
            session_factory, identity_id, messages
        )


async def _get_or_create_mailbox_state(
    session: AsyncSession,
    identity_id: int,
    *,
    folder_role: str = "inbox",
    folder: str = "INBOX",
) -> ImapMailboxSyncState:
    state = await session.scalar(
        select(ImapMailboxSyncState).where(
            ImapMailboxSyncState.identity_id == identity_id,
            ImapMailboxSyncState.folder_role == folder_role,
            ImapMailboxSyncState.folder == folder,
        ),
    )
    if state is not None:
        return state
    state = ImapMailboxSyncState(
        identity_id=identity_id,
        folder_role=folder_role,
        folder=folder,
    )
    session.add(state)
    await session.flush()
    return state


async def _get_imap_identity_lock(identity_id: int) -> asyncio.Lock:
    return await _get_imap_lock(_IMAP_IDENTITY_LOCKS, identity_id)


async def _get_imap_incremental_lock(identity_id: int) -> asyncio.Lock:
    return await _get_imap_lock(_IMAP_INCREMENTAL_LOCKS, identity_id)


async def _get_imap_history_lock(identity_id: int) -> asyncio.Lock:
    return await _get_imap_lock(_IMAP_HISTORY_LOCKS, identity_id)


async def _is_imap_identity_locked(identity_id: int) -> bool:
    return (await _get_imap_identity_lock(identity_id)).locked()


async def _is_imap_history_work_locked(identity_id: int) -> bool:
    return (
        await _is_imap_identity_locked(identity_id)
        or (await _get_imap_incremental_lock(identity_id)).locked()
        or (await _get_imap_history_lock(identity_id)).locked()
    )


async def _get_imap_lock(
    locks: dict[int, asyncio.Lock],
    identity_id: int,
) -> asyncio.Lock:
    async with _IMAP_IDENTITY_LOCKS_GUARD:
        lock = locks.get(identity_id)
        if lock is None:
            lock = asyncio.Lock()
            locks[identity_id] = lock
        return lock


async def repair_identity_replies(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    professor_email: str | None = None,
) -> int:
    async with session_factory() as session:
        identity = await session.get(IdentityProfile, identity_id)
    if not identity:
        return 0

    if professor_email and professor_email.strip():
        if await _is_imap_history_work_locked(identity_id):
            return 0
        lock = await _get_imap_history_lock(identity_id)
        async with lock:
            if await _is_imap_identity_locked(identity_id):
                return 0
            messages = await mail_runtime.fetch_professor_history_inbox_messages(
                identity, professor_email
            )
            return await process_imap_fetched_messages(
                session_factory, identity_id, messages
            )
    return await sync_identity_imap_once(session_factory, identity_id)


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
                    if result.email_task_id is not None
                    and result.email_log is not None
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
        email_log
        for _, email_log in matched_rows
        if email_log.id == target_log_id
    )
    if target_log.email_task_id is None:
        return None
    return await _load_email_task(session, target_log.email_task_id)


async def _load_email_task(session: AsyncSession, task_id: int) -> EmailTask | None:
    return await session.scalar(
        select(EmailTask)
        .options(*TASK_RELATION_OPTIONS)
        .where(EmailTask.id == task_id),
    )


async def _record_email_task_log(
    session: AsyncSession,
    task: EmailTask,
    event_name: str,
    *,
    level: str = "info",
    message: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    base_metadata: dict[str, object] = {
        "task_id": task.id,
        "source": task.source,
        "status": task.status,
        "batch_task_id": task.batch_task_id,
        "parent_task_id": task.parent_task_id,
        "professor_id": task.professor_id,
        "identity_id": task.identity_id,
        "llm_profile_id": task.llm_profile_id,
    }
    if metadata:
        base_metadata.update(metadata)
    await record_operation_log(
        session,
        category="email",
        event_name=event_name,
        level=level,
        message=message,
        entity_type="email_task",
        entity_id=str(task.id),
        metadata=base_metadata,
    )


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
