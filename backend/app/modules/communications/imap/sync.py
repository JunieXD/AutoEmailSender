from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.time import utc_now
from app.models import (
    IdentityProfile,
    ImapMailboxHistoricalScanStatus,
    ImapMailboxSyncState,
    ImapProfessorSyncState,
    Professor,
)
from app.modules.identities.public import get_active_identity_profile

from .. import transport as mail_runtime
from ..addresses import normalize_email_list
from .errors import (
    is_account_level_throttle_error as _is_account_level_throttle_error,
    is_provider_throttle_error,
)
from .fetcher import ImapFetchedMessage
from .history_fetch import (
    IMAP_HISTORY_BODY_FETCH_COMMANDS_PER_MESSAGE as IMAP_HISTORY_BODY_FETCH_COMMANDS_PER_MESSAGE,
    _fetch_missing_history_mailbox_message_bodies as _fetch_missing_history_mailbox_message_bodies,
    _fetch_missing_history_message_bodies as _fetch_missing_history_message_bodies,
    _fetch_recent_sent_message_bodies as _fetch_recent_sent_message_bodies,
    _history_body_fetch_command_count as _history_body_fetch_command_count,
    _history_body_fetch_uid_limit as _history_body_fetch_uid_limit,
    _history_header_already_ingested as _history_header_already_ingested,
    _history_mailbox_header_already_ingested as _history_mailbox_header_already_ingested,
    _HistoryBodyFetchResult as _HistoryBodyFetchResult,
    _load_active_professor_ids_by_email as _load_active_professor_ids_by_email,
    _MailboxHistoryBodyFetchResult as _MailboxHistoryBodyFetchResult,
    _MailboxHistoryHeaderMatch as _MailboxHistoryHeaderMatch,
    _match_history_mailbox_headers as _match_history_mailbox_headers,
    _max_optional_uid as _max_optional_uid,
)
from .message_ingestion import (
    VALID_IMAP_FOLDER_ROLES as VALID_IMAP_FOLDER_ROLES,
    _backfill_existing_reply as _backfill_existing_reply,
    _datetimes_match as _datetimes_match,
    _find_existing_professor_for_incoming_message as _find_existing_professor_for_incoming_message,
    _find_existing_received_log_for_reply as _find_existing_received_log_for_reply,
    _find_reply_target as _find_reply_target,
    _find_reply_target_from_observations as _find_reply_target_from_observations,
    _get_reply_created_at as _get_reply_created_at,
    _looks_like_raw_mime_content as _looks_like_raw_mime_content,
    _mark_task_reply_detected as _mark_task_reply_detected,
    _process_incoming_reply_messages as _process_incoming_reply_messages,
    _process_sent_imap_fetched_messages as _process_sent_imap_fetched_messages,
    _upsert_unbound_received_log as _upsert_unbound_received_log,
    _validate_imap_folder_role as _validate_imap_folder_role,
    extract_message_ids as extract_message_ids,
    normalize_subject as normalize_subject,
    process_imap_fetched_messages as process_imap_fetched_messages,
)
from .state import (
    RECENT_V2_STRATEGY_VERSION,
    ImapIdentitySyncClaim,
    ImapIdentitySyncLeaseLostError,
    bind_imap_identity_sync_claim,
    claim_imap_identity_sync,
    claim_next_professor_scans,
    claim_recent_v2_professor_scans,
    clear_identity_sent_folder_discovery_cache,
    commit_imap_identity_sync_session,
    ensure_recent_v2_professor_scan_states,
    get_recent_v2_due_summary,
    mark_professor_scan_completed,
    mark_professor_scan_failed,
    mark_recent_v2_batch_completed,
    prepare_recent_v2_bulk_sent_batch,
    release_imap_identity_sync_claim,
    renew_imap_identity_sync_claim,
    reset_imap_identity_sync_claim,
    reset_professor_scans_to_pending,
)

_IMAP_IDENTITY_LOCKS: dict[int, asyncio.Lock] = {}

_IMAP_INCREMENTAL_LOCKS: dict[int, asyncio.Lock] = {}

_IMAP_HISTORY_LOCKS: dict[int, asyncio.Lock] = {}

_IMAP_IDENTITY_LOCKS_GUARD = asyncio.Lock()


IMAP_HISTORY_THROTTLE_PREFIX = "history:"

IMAP_ACCOUNT_THROTTLE_PREFIX = "account:"


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
                        IdentityProfile.deleted_at.is_(None),
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
                        IdentityProfile.deleted_at.is_(None),
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
        identity = await get_active_identity_profile(session, identity_id)
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
        identity = await get_active_identity_profile(session, identity_id)
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
        settle_seconds=settings.imap_history_queue_settle_seconds,
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


def _recent_v2_targeted_professor_limit(settings: Settings) -> int:
    effective_rate = min(
        settings.imap_history_command_budget_per_minute,
        settings.imap_history_command_rate_per_minute,
    )
    reserved_search_budget = max(1, effective_rate * 75 // 100)
    return max(1, reserved_search_budget // 2)


def _should_use_recent_v2_bulk_sent(
    *,
    professor_count: int,
    recent_sent_uid_count: int,
    settings: Settings,
) -> bool:
    if professor_count <= _recent_v2_targeted_professor_limit(settings):
        return False
    if recent_sent_uid_count > settings.imap_history_bulk_header_limit:
        return False
    header_batch_size = max(1, settings.imap_fetch_batch_size)
    bulk_command_cost = (
        1 + (recent_sent_uid_count + header_batch_size - 1) // header_batch_size
    )
    targeted_sent_command_cost = professor_count
    return bulk_command_cost < targeted_sent_command_cost


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
                identity = await get_active_identity_profile(session, identity_id)
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


async def sync_identity_incremental_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    *,
    folder_role: str = "inbox",
    folder: str = "INBOX",
) -> int:
    _validate_imap_folder_role(folder_role)
    async with session_factory() as session:
        identity = await get_active_identity_profile(session, identity_id)
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
            identity = await get_active_identity_profile(session, identity_id)
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
        identity = await get_active_identity_profile(session, identity_id)
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
