from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import stat
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.beta_diagnostics import (
    BETA_DIAGNOSTICS_MAX_RECORD_BYTES,
    BETA_DIAGNOSTICS_MAX_SEGMENT_BYTES,
    BETA_DIAGNOSTICS_SCHEMA_VERSION,
)
from app.core.config import get_settings
from app.core.schema_metadata import get_schema_backup_dir, get_sqlite_database_path
from app.models import (
    CrawlJob,
    EmailDeliveryAttempt,
    EmailDeliveryOutcome,
    EmailTask,
    EmailTaskStatus,
    ImapIdentitySyncLease,
    ImapMailboxSyncState,
    ImapProfessorSyncState,
    MatchAnalysisJob,
    OperationLog,
)
from app.schemas.diagnostics import (
    BetaDatabaseHealth,
    BetaDiagnosticsSummaryResponse,
    BetaOperationLogCategorySummary,
    BetaOperationLogLevelCounts,
    BetaOperationLogSummary,
    BetaWorkloadInvariants,
    BetaWorkloadSummary,
    BetaWorkloadSummaryItem,
)


_OPERATION_CATEGORY_MAP = {
    "email": "mail",
    "mail": "mail",
    "imap": "imap",
    "draft": "draft",
    "match_analysis": "matching",
    "matching": "matching",
    "crawler": "crawler",
    "professor_information_enrichment": "crawler",
    "backend": "runtime",
    "runtime": "runtime",
    "sqlite": "sqlite",
    "llm": "llm",
    "agent_change": "system",
    "identity": "system",
    "system": "system",
    "user_action": "system",
}
_LEVELS = ("debug", "info", "warning", "error")


async def build_beta_diagnostics_summary(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> BetaDiagnosticsSummaryResponse:
    generated_at = (now or datetime.now(UTC)).astimezone(UTC)
    recovery_counts = await _load_recovery_counts(session, generated_at)
    workloads = [
        await _summarize_status_workload(
            session,
            kind="dispatcher",
            status_column=EmailTask.status,
            created_column=EmailTask.created_at,
            started_column=EmailTask.last_send_attempt_at,
            finished_column=EmailTask.sent_at,
            queued_statuses=(EmailTaskStatus.APPROVED, EmailTaskStatus.SCHEDULED),
            running_statuses=(EmailTaskStatus.SENDING,),
            succeeded_statuses=(EmailTaskStatus.SENT,),
            failed_statuses=(EmailTaskStatus.SEND_FAILED, EmailTaskStatus.SCHEDULE_MISSED),
            interrupted_statuses=(EmailTaskStatus.CANCELED,),
            recovered=recovery_counts["dispatcher"],
            now=generated_at,
        ),
        await _summarize_imap_sync(session, recovery_counts["imap_sync"], generated_at),
        await _summarize_imap_history(
            session,
            recovery_counts["imap_history"],
            generated_at,
        ),
        await _summarize_batch_draft(
            session,
            recovery_counts["batch_draft"],
            generated_at,
        ),
        await _summarize_status_workload(
            session,
            kind="matching",
            status_column=MatchAnalysisJob.status,
            created_column=MatchAnalysisJob.created_at,
            started_column=MatchAnalysisJob.started_at,
            finished_column=MatchAnalysisJob.finished_at,
            queued_statuses=("queued",),
            running_statuses=("running",),
            succeeded_statuses=("completed",),
            failed_statuses=("failed", "partial_failed"),
            interrupted_statuses=("canceled",),
            recovered=recovery_counts["matching"],
            now=generated_at,
            filters=(MatchAnalysisJob.deleted_at.is_(None),),
        ),
        await _summarize_status_workload(
            session,
            kind="crawler",
            status_column=CrawlJob.status,
            created_column=CrawlJob.created_at,
            started_column=CrawlJob.created_at,
            finished_column=CrawlJob.updated_at,
            queued_statuses=("queued", "paused"),
            running_statuses=("running",),
            succeeded_statuses=("completed", "partially_completed", "needs_review"),
            failed_statuses=("failed",),
            interrupted_statuses=("canceled",),
            recovered=recovery_counts["crawler"],
            now=generated_at,
            filters=(CrawlJob.deleted_at.is_(None),),
        ),
    ]
    invariants = await _load_workload_invariants(session)
    operation_summary = await _build_operation_log_summary(session, generated_at)
    database_health = await asyncio.to_thread(_collect_database_health, generated_at)
    return BetaDiagnosticsSummaryResponse(
        generated_at=generated_at,
        workload_summary=BetaWorkloadSummary(
            generated_at=generated_at,
            workloads=workloads,
            invariants=invariants,
        ),
        database_health=database_health,
        operation_log_summary=operation_summary,
    )


async def _summarize_status_workload(
    session: AsyncSession,
    *,
    kind: str,
    status_column: Any,
    created_column: Any,
    started_column: Any,
    finished_column: Any,
    queued_statuses: Sequence[object],
    running_statuses: Sequence[object],
    succeeded_statuses: Sequence[object],
    failed_statuses: Sequence[object],
    interrupted_statuses: Sequence[object],
    recovered: int,
    now: datetime,
    filters: Sequence[Any] = (),
) -> BetaWorkloadSummaryItem:
    rows = (
        await session.execute(
            select(status_column, func.count()).where(*filters).group_by(status_column)
        )
    ).all()
    counts = {str(status): int(count) for status, count in rows}
    oldest_queued = await session.scalar(
        select(func.min(created_column)).where(
            *filters,
            status_column.in_(tuple(str(value) for value in queued_statuses)),
        )
    )
    oldest_running = await session.scalar(
        select(func.min(func.coalesce(started_column, created_column))).where(
            *filters,
            status_column.in_(tuple(str(value) for value in running_statuses)),
        )
    )
    durations = await _load_recent_durations(
        session,
        started_column=started_column,
        finished_column=finished_column,
        statuses=succeeded_statuses,
        status_column=status_column,
        filters=filters,
    )
    return BetaWorkloadSummaryItem(
        kind=kind,  # type: ignore[arg-type]
        queued=_sum_status_counts(counts, queued_statuses),
        running=_sum_status_counts(counts, running_statuses),
        succeeded=_sum_status_counts(counts, succeeded_statuses),
        failed=_sum_status_counts(counts, failed_statuses),
        interrupted=_sum_status_counts(counts, interrupted_statuses),
        recovered=recovered,
        oldest_queue_age_seconds=_age_seconds(oldest_queued, now),
        oldest_running_age_seconds=_age_seconds(oldest_running, now),
        average_duration_seconds=(
            round(sum(durations) / len(durations), 3) if durations else None
        ),
        maximum_duration_seconds=round(max(durations), 3) if durations else None,
    )


async def _summarize_batch_draft(
    session: AsyncSession,
    recovered: int,
    now: datetime,
) -> BetaWorkloadSummaryItem:
    generating = EmailTask.status == EmailTaskStatus.GENERATING_DRAFT
    queued_filter = and_(generating, EmailTask.draft_claim_id.is_(None))
    running_filter = and_(generating, EmailTask.draft_claim_id.is_not(None))
    queued = int(
        await session.scalar(select(func.count()).select_from(EmailTask).where(queued_filter))
        or 0
    )
    running = int(
        await session.scalar(select(func.count()).select_from(EmailTask).where(running_filter))
        or 0
    )
    succeeded = int(
        await session.scalar(
            select(func.count()).select_from(EmailTask).where(
                EmailTask.status.in_(
                    (
                        EmailTaskStatus.REVIEW_REQUIRED,
                        EmailTaskStatus.APPROVED,
                        EmailTaskStatus.SCHEDULED,
                        EmailTaskStatus.SENDING,
                        EmailTaskStatus.SENT,
                    )
                ),
                EmailTask.generated_content_text.is_not(None),
            )
        )
        or 0
    )
    failed = int(
        await session.scalar(
            select(func.count()).select_from(EmailTask).where(
                EmailTask.status == EmailTaskStatus.DRAFT_FAILED
            )
        )
        or 0
    )
    interrupted = int(
        await session.scalar(
            select(func.count()).select_from(EmailTask).where(
                EmailTask.status == EmailTaskStatus.CANCELED,
                EmailTask.draft_generation_started_at.is_not(None),
            )
        )
        or 0
    )
    oldest_queued = await session.scalar(
        select(func.min(EmailTask.created_at)).where(queued_filter)
    )
    oldest_running = await session.scalar(
        select(func.min(EmailTask.draft_generation_started_at)).where(running_filter)
    )
    return BetaWorkloadSummaryItem(
        kind="batch_draft",
        queued=queued,
        running=running,
        succeeded=succeeded,
        failed=failed,
        interrupted=interrupted,
        recovered=recovered,
        oldest_queue_age_seconds=_age_seconds(oldest_queued, now),
        oldest_running_age_seconds=_age_seconds(oldest_running, now),
    )


async def _summarize_imap_sync(
    session: AsyncSession,
    recovered: int,
    now: datetime,
) -> BetaWorkloadSummaryItem:
    running_filter = and_(
        ImapIdentitySyncLease.claim_id.is_not(None),
        ImapIdentitySyncLease.lease_expires_at > now,
    )
    interrupted_filter = and_(
        ImapIdentitySyncLease.claim_id.is_not(None),
        ImapIdentitySyncLease.lease_expires_at <= now,
    )
    running = int(
        await session.scalar(
            select(func.count()).select_from(ImapIdentitySyncLease).where(running_filter)
        )
        or 0
    )
    interrupted = int(
        await session.scalar(
            select(func.count()).select_from(ImapIdentitySyncLease).where(interrupted_filter)
        )
        or 0
    )
    oldest_running = await session.scalar(
        select(func.min(ImapIdentitySyncLease.claimed_at)).where(running_filter)
    )
    cutoff = now - timedelta(hours=24)
    succeeded = int(
        await session.scalar(
            select(func.count()).select_from(ImapMailboxSyncState).where(
                ImapMailboxSyncState.last_sync_at >= cutoff
            )
        )
        or 0
    )
    failed = int(
        await session.scalar(
            select(func.count()).select_from(ImapMailboxSyncState).where(
                ImapMailboxSyncState.last_error.is_not(None),
                ImapMailboxSyncState.updated_at >= cutoff,
            )
        )
        or 0
    )
    return BetaWorkloadSummaryItem(
        kind="imap_sync",
        queued=0,
        running=running,
        succeeded=succeeded,
        failed=failed,
        interrupted=interrupted,
        recovered=recovered,
        oldest_running_age_seconds=_age_seconds(oldest_running, now),
    )


async def _summarize_imap_history(
    session: AsyncSession,
    recovered: int,
    now: datetime,
) -> BetaWorkloadSummaryItem:
    mailbox_rows = (
        await session.execute(
            select(ImapMailboxSyncState.history_scan_status, func.count()).group_by(
                ImapMailboxSyncState.history_scan_status
            )
        )
    ).all()
    professor_rows = (
        await session.execute(
            select(ImapProfessorSyncState.historical_scan_status, func.count()).group_by(
                ImapProfessorSyncState.historical_scan_status
            )
        )
    ).all()
    counts: dict[str, int] = defaultdict(int)
    for status, count in (*mailbox_rows, *professor_rows):
        counts[str(status)] += int(count)
    mailbox_oldest_queued = await session.scalar(
        select(func.min(ImapMailboxSyncState.created_at)).where(
            ImapMailboxSyncState.history_scan_status == "pending"
        )
    )
    professor_oldest_queued = await session.scalar(
        select(func.min(ImapProfessorSyncState.created_at)).where(
            ImapProfessorSyncState.historical_scan_status == "pending"
        )
    )
    mailbox_oldest_running = await session.scalar(
        select(func.min(ImapMailboxSyncState.history_scan_started_at)).where(
            ImapMailboxSyncState.history_scan_status == "running"
        )
    )
    professor_oldest_running = await session.scalar(
        select(func.min(ImapProfessorSyncState.historical_scan_started_at)).where(
            ImapProfessorSyncState.historical_scan_status == "running"
        )
    )
    return BetaWorkloadSummaryItem(
        kind="imap_history",
        queued=counts["pending"],
        running=counts["running"],
        succeeded=counts["completed"],
        failed=counts["failed"],
        interrupted=0,
        recovered=recovered,
        oldest_queue_age_seconds=_age_seconds(
            _oldest_datetime(mailbox_oldest_queued, professor_oldest_queued),
            now,
        ),
        oldest_running_age_seconds=_age_seconds(
            _oldest_datetime(mailbox_oldest_running, professor_oldest_running),
            now,
        ),
    )


async def _load_recent_durations(
    session: AsyncSession,
    *,
    started_column: Any,
    finished_column: Any,
    status_column: Any,
    statuses: Sequence[object],
    filters: Sequence[Any],
) -> list[float]:
    rows = (
        await session.execute(
            select(started_column, finished_column)
            .where(
                *filters,
                status_column.in_(tuple(str(value) for value in statuses)),
                started_column.is_not(None),
                finished_column.is_not(None),
            )
            .order_by(finished_column.desc())
            .limit(1000)
        )
    ).all()
    durations: list[float] = []
    for started_at, finished_at in rows:
        if isinstance(started_at, datetime) and isinstance(finished_at, datetime):
            durations.append(max(0.0, (finished_at - started_at).total_seconds()))
    return durations


async def _load_recovery_counts(
    session: AsyncSession,
    now: datetime,
) -> dict[str, int]:
    rows = (
        await session.execute(
            select(OperationLog.category, OperationLog.event_name, func.count())
            .where(
                OperationLog.created_at >= now - timedelta(hours=24),
                or_(
                    func.lower(OperationLog.event_name).like("%recover%"),
                    func.lower(OperationLog.event_name).like("%resume%"),
                ),
            )
            .group_by(OperationLog.category, OperationLog.event_name)
        )
    ).all()
    result = {
        "dispatcher": 0,
        "imap_sync": 0,
        "imap_history": 0,
        "batch_draft": 0,
        "matching": 0,
        "crawler": 0,
    }
    for category, event_name, count in rows:
        normalized = f"{category}.{event_name}".lower()
        if "imap" in normalized and "history" in normalized:
            result["imap_history"] += int(count)
        elif "imap" in normalized:
            result["imap_sync"] += int(count)
        elif "draft" in normalized:
            result["batch_draft"] += int(count)
        elif "match" in normalized:
            result["matching"] += int(count)
        elif "crawl" in normalized or "enrichment" in normalized:
            result["crawler"] += int(count)
        elif "email" in normalized or "dispatch" in normalized or "send" in normalized:
            result["dispatcher"] += int(count)
    return result


async def _load_workload_invariants(session: AsyncSession) -> BetaWorkloadInvariants:
    sending_count = int(
        await session.scalar(
            select(func.count()).select_from(EmailTask).where(
                EmailTask.status == EmailTaskStatus.SENDING
            )
        )
        or 0
    )
    accepted_attempts = (
        select(
            EmailDeliveryAttempt.email_task_id.label("email_task_id"),
            func.count().label("accepted_count"),
        )
        .where(
            EmailDeliveryAttempt.outcome.in_(
                (
                    EmailDeliveryOutcome.SMTP_ACCEPTED,
                    EmailDeliveryOutcome.ASSUMED_SENT_AFTER_INTERRUPTION,
                )
            )
        )
        .group_by(EmailDeliveryAttempt.email_task_id)
        .having(func.count() > 1)
        .subquery()
    )
    duplicate_groups = int(
        await session.scalar(select(func.count()).select_from(accepted_attempts)) or 0
    )
    orphaned_claim_count = int(
        await session.scalar(
            select(func.count())
            .select_from(EmailDeliveryAttempt)
            .join(EmailTask, EmailTask.id == EmailDeliveryAttempt.email_task_id)
            .where(
                EmailDeliveryAttempt.outcome == EmailDeliveryOutcome.CLAIMED,
                EmailTask.status != EmailTaskStatus.SENDING,
            )
        )
        or 0
    )
    return BetaWorkloadInvariants(
        sending_count=sending_count,
        duplicate_delivery_attempt_groups=duplicate_groups,
        orphaned_claim_count=orphaned_claim_count,
    )


async def _build_operation_log_summary(
    session: AsyncSession,
    now: datetime,
) -> BetaOperationLogSummary:
    cutoff_1h = now - timedelta(hours=1)
    cutoff_24h = now - timedelta(hours=24)
    total_1h = int(
        await session.scalar(
            select(func.count()).select_from(OperationLog).where(
                OperationLog.created_at >= cutoff_1h
            )
        )
        or 0
    )
    total_24h = int(
        await session.scalar(
            select(func.count()).select_from(OperationLog).where(
                OperationLog.created_at >= cutoff_24h
            )
        )
        or 0
    )
    level_rows = (
        await session.execute(
            select(OperationLog.level, func.count())
            .where(OperationLog.created_at >= cutoff_24h)
            .group_by(OperationLog.level)
        )
    ).all()
    levels = {level: 0 for level in _LEVELS}
    for level, count in level_rows:
        normalized = str(level).lower()
        if normalized == "warn":
            normalized = "warning"
        if normalized in levels:
            levels[normalized] += int(count)
    category_rows = (
        await session.execute(
            select(
                OperationLog.category,
                func.count(),
                func.sum(func.lower(OperationLog.level) == "error"),
            )
            .where(OperationLog.created_at >= cutoff_24h)
            .group_by(OperationLog.category)
        )
    ).all()
    categories: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for category, event_count, error_count in category_rows:
        normalized = _OPERATION_CATEGORY_MAP.get(str(category).lower(), "system")
        categories[normalized][0] += int(event_count)
        categories[normalized][1] += int(error_count or 0)
    return BetaOperationLogSummary(
        generated_at=now,
        total_1h=total_1h,
        total_24h=total_24h,
        levels_24h=BetaOperationLogLevelCounts(**levels),
        categories_24h=[
            BetaOperationLogCategorySummary(
                category=category,  # type: ignore[arg-type]
                event_count=counts[0],
                error_count=counts[1],
            )
            for category, counts in sorted(categories.items())
        ],
    )


def _collect_database_health(now: datetime) -> BetaDatabaseHealth:
    settings = get_settings()
    database_path = get_sqlite_database_path(settings.database_url)
    if database_path is None or not database_path.is_file():
        raise RuntimeError("Beta diagnostics require an available SQLite database")
    connection = sqlite3.connect(
        f"{database_path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=2.0,
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        revision_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        revision = str(revision_row[0]) if revision_row else "unknown"
        integrity_row = connection.execute("PRAGMA integrity_check(1)").fetchone()
        integrity = "ok" if integrity_row and str(integrity_row[0]).lower() == "ok" else "error"
        try:
            foreign_key_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM pragma_foreign_key_check"
                ).fetchone()[0]
            )
        except sqlite3.DatabaseError:
            foreign_key_count = sum(1 for _row in connection.execute("PRAGMA foreign_key_check"))
        journal_row = connection.execute("PRAGMA journal_mode").fetchone()
        journal_mode = str(journal_row[0]).lower() if journal_row else "unknown"
    finally:
        connection.close()

    backup_dir = get_schema_backup_dir(settings.data_dir)
    backups = _regular_files(backup_dir, suffix=".db")
    newest_backup_age = None
    if backups:
        newest_modified = max(candidate.stat().st_mtime for candidate in backups)
        newest_backup_age = max(0.0, now.timestamp() - newest_modified)
    event_metrics = _scan_sqlite_event_metrics(
        settings.data_dir / "beta-diagnostics",
        cutoff=now - timedelta(hours=1),
    )
    return BetaDatabaseHealth(
        generated_at=now,
        alembic_revision=revision,
        integrity_check=integrity,
        foreign_key_violation_count=foreign_key_count,
        journal_mode=journal_mode,
        busy_timeout_ms=max(0, settings.sqlite_busy_timeout_ms),
        database_bytes=_regular_file_size(database_path),
        wal_bytes=_regular_file_size(Path(f"{database_path}-wal")),
        shm_bytes=_regular_file_size(Path(f"{database_path}-shm")),
        backup_count=len(backups),
        newest_backup_age_seconds=(
            round(newest_backup_age, 3) if newest_backup_age is not None else None
        ),
        lock_errors_1h=event_metrics["lock_errors"],
        busy_errors_1h=event_metrics["busy_errors"],
        slow_queries_1h=event_metrics["slow_queries"],
        maximum_query_ms_1h=round(event_metrics["maximum_query_ms"], 3),
    )


def _scan_sqlite_event_metrics(root_path: Path, *, cutoff: datetime) -> dict[str, Any]:
    result: dict[str, Any] = {
        "lock_errors": 0,
        "busy_errors": 0,
        "slow_queries": 0,
        "maximum_query_ms": 0.0,
    }
    segment_root = root_path / "segments"
    if not segment_root.is_dir() or segment_root.is_symlink():
        return result
    for component_path in segment_root.iterdir():
        if not component_path.is_dir() or component_path.is_symlink():
            continue
        for candidate in component_path.iterdir():
            if not _safe_timeline_segment(candidate, cutoff):
                continue
            try:
                with _open_bounded_timeline_segment(candidate) as file:
                    for line in file:
                        if len(line.encode("utf-8")) > BETA_DIAGNOSTICS_MAX_RECORD_BYTES:
                            continue
                        _accumulate_sqlite_event(result, line, cutoff)
            except OSError:
                continue
    return result


def _safe_timeline_segment(candidate: Path, cutoff: datetime) -> bool:
    if (
        not candidate.name.startswith("timeline-")
        or not candidate.name.endswith(".jsonl")
        or candidate.is_symlink()
        or not candidate.is_file()
    ):
        return False
    try:
        file_stat = candidate.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(file_stat.st_mode)
        and not stat.S_ISLNK(file_stat.st_mode)
        and
        file_stat.st_size <= BETA_DIAGNOSTICS_MAX_SEGMENT_BYTES
        + BETA_DIAGNOSTICS_MAX_RECORD_BYTES
        and datetime.fromtimestamp(file_stat.st_mtime, tz=UTC) >= cutoff - timedelta(hours=1)
    )


@contextmanager
def _open_bounded_timeline_segment(candidate: Path) -> Iterator[TextIO]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(candidate, flags)
    try:
        file_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_size
            > BETA_DIAGNOSTICS_MAX_SEGMENT_BYTES + BETA_DIAGNOSTICS_MAX_RECORD_BYTES
        ):
            raise OSError("Beta diagnostic timeline segment is not a bounded regular file")
        with os.fdopen(
            descriptor,
            "r",
            encoding="utf-8",
            errors="replace",
            closefd=False,
        ) as file:
            yield file
    finally:
        os.close(descriptor)


def _accumulate_sqlite_event(
    result: dict[str, Any],
    line: str,
    cutoff: datetime,
) -> None:
    try:
        record = json.loads(line)
        wall_time = datetime.fromisoformat(str(record["wall_time"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return
    if wall_time.tzinfo is None:
        wall_time = wall_time.replace(tzinfo=UTC)
    if wall_time < cutoff or record.get("schema_version") != BETA_DIAGNOSTICS_SCHEMA_VERSION:
        return
    event = record.get("event")
    if event == "sqlite_lock_error":
        result["lock_errors"] += 1
        result["busy_errors"] += 1
    elif event == "sqlite_busy_error":
        result["busy_errors"] += 1
    elif event == "sqlite_slow_query":
        result["slow_queries"] += 1
        details = record.get("details")
        elapsed_seconds = details.get("elapsed_seconds") if isinstance(details, dict) else None
        if isinstance(elapsed_seconds, int | float) and elapsed_seconds >= 0:
            result["maximum_query_ms"] = max(
                result["maximum_query_ms"],
                float(elapsed_seconds) * 1000,
            )


def _sum_status_counts(counts: dict[str, int], statuses: Iterable[object]) -> int:
    return sum(counts.get(str(status), 0) for status in statuses)


def _age_seconds(value: object, now: datetime) -> float | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return round(max(0.0, (now - value.astimezone(UTC)).total_seconds()), 3)


def _oldest_datetime(*values: object) -> datetime | None:
    dates = [value for value in values if isinstance(value, datetime)]
    return min(dates) if dates else None


def _regular_files(directory: Path, *, suffix: str) -> list[Path]:
    try:
        return [
            candidate
            for candidate in directory.iterdir()
            if candidate.name.endswith(suffix)
            and candidate.is_file()
            and not candidate.is_symlink()
        ]
    except OSError:
        return []


def _regular_file_size(file_path: Path) -> int:
    try:
        file_stat = file_path.lstat()
    except OSError:
        return 0
    return max(0, file_stat.st_size) if stat.S_ISREG(file_stat.st_mode) else 0


__all__ = ["build_beta_diagnostics_summary"]
