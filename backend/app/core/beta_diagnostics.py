from __future__ import annotations

import asyncio
import json
import os
import re
import stat
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import psutil

from app.core.backend_role import BackendRole, get_backend_role
from app.core.config import get_settings


BETA_DIAGNOSTICS_SCHEMA_VERSION = 1
BETA_DIAGNOSTICS_DIRECTORY_NAME = "beta-diagnostics"
BETA_DIAGNOSTICS_RETENTION_DAYS = 14
BETA_DIAGNOSTICS_MAX_TOTAL_BYTES = 64 * 1024 * 1024
BETA_DIAGNOSTICS_MAX_SEGMENT_BYTES = 2 * 1024 * 1024
BETA_DIAGNOSTICS_MAX_SEGMENT_AGE_SECONDS = 60 * 60
BETA_DIAGNOSTICS_MAX_RECORD_BYTES = 64 * 1024
BETA_DIAGNOSTICS_RESOURCE_SAMPLE_SECONDS = 10.0
BETA_DIAGNOSTICS_TEST_ENABLE_VALUE = "enabled-for-tests-only"
_CLOCK_JUMP_THRESHOLD_SECONDS = 5.0
_SAFE_EVENT = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:/+-]{0,160}$")
_TIMELINE_DETAIL_KEYS = {
    "api_available",
    "api_pid",
    "backoff_ms",
    "clock_offset_ms",
    "code",
    "current_version",
    "effective_mode",
    "elapsed_seconds",
    "error_code",
    "phase",
    "process_id",
    "reason",
    "restart_count",
    "runtime_id",
    "signal",
    "sleep_gap_ms",
    "source",
    "state",
    "worker_count",
    "worker_health",
    "worker_pid",
}
_SEVERITIES = {"debug", "info", "warning", "error"}


DiagnosticSeverity = Literal["debug", "info", "warning", "error"]
HealthProvider = Callable[[], Mapping[str, object]]
_current_recorder: BetaDiagnosticsRecorder | None = None


def beta_diagnostics_enabled(
    app_version: str | None = None,
    environment_value: str | None = None,
) -> bool:
    resolved_version = (
        app_version
        if app_version is not None
        else os.getenv("AUTO_EMAIL_SENDER_APP_VERSION", "")
    ).strip()
    resolved_environment = (
        environment_value
        if environment_value is not None
        else os.getenv("AUTO_EMAIL_SENDER_BETA_DIAGNOSTICS", "")
    )
    if resolved_environment == BETA_DIAGNOSTICS_TEST_ENABLE_VALUE:
        return True
    return re.search(r"-(?:alpha|beta|rc)(?:[.-]|$)", resolved_version, re.I) is not None


class RotatingJsonlWriter:
    def __init__(
        self,
        *,
        root_path: Path,
        component: BackendRole,
        stream: Literal["timeline", "resource-samples"],
        max_segment_bytes: int = BETA_DIAGNOSTICS_MAX_SEGMENT_BYTES,
        max_segment_age_seconds: float = BETA_DIAGNOSTICS_MAX_SEGMENT_AGE_SECONDS,
        max_record_bytes: int = BETA_DIAGNOSTICS_MAX_RECORD_BYTES,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root_path = root_path
        self.component = component
        self.stream = stream
        self.max_segment_bytes = max_segment_bytes
        self.max_segment_age_seconds = max_segment_age_seconds
        self.max_record_bytes = max_record_bytes
        self.clock = clock or (lambda: datetime.now(UTC))
        self._file: object | None = None
        self._path: Path | None = None
        self._opened_at = 0.0
        self._bytes_written = 0
        self._lock = threading.Lock()

    @property
    def current_path(self) -> Path | None:
        return self._path

    def append(self, record: Mapping[str, object]) -> None:
        serialized = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        if len(serialized) > self.max_record_bytes:
            raise ValueError("Beta diagnostic record exceeds the bounded record size")
        with self._lock:
            now = self.clock()
            if (
                self._file is None
                or self._bytes_written + len(serialized) > self.max_segment_bytes
                or now.timestamp() - self._opened_at >= self.max_segment_age_seconds
            ):
                self._rotate(now)
            assert self._file is not None
            self._file.write(serialized)  # type: ignore[union-attr]
            self._file.flush()  # type: ignore[union-attr]
            self._bytes_written += len(serialized)

    def close(self) -> None:
        with self._lock:
            self._close_current()

    def _rotate(self, now: datetime) -> None:
        self._close_current()
        component_path = self.root_path / "segments" / self.component
        _ensure_private_directory(self.root_path)
        _ensure_private_directory(self.root_path / "segments")
        _ensure_private_directory(component_path)
        self._finalize_stale_active_segments(component_path)
        timestamp = re.sub(r"[^0-9]", "", now.isoformat())
        file_name = (
            f"{self.stream}-{timestamp}-{os.getpid()}-{uuid.uuid4()}"
            ".active.jsonl"
        )
        next_path = component_path / file_name
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(next_path, flags, 0o600)
        try:
            self._file = os.fdopen(descriptor, "wb", buffering=0)
        except Exception:
            os.close(descriptor)
            raise
        self._path = next_path
        self._opened_at = now.timestamp()
        self._bytes_written = 0
        _set_private_permissions(next_path, 0o600)

    def _close_current(self) -> None:
        active_file = self._file
        active_path = self._path
        self._file = None
        self._path = None
        if active_file is None:
            return
        try:
            active_file.flush()  # type: ignore[union-attr]
            os.fsync(active_file.fileno())  # type: ignore[union-attr]
        except OSError:
            pass
        active_file.close()  # type: ignore[union-attr]
        if active_path is not None:
            _finalize_active_segment(active_path)

    def _finalize_stale_active_segments(self, component_path: Path) -> None:
        prefix = f"{self.stream}-"
        for candidate in component_path.iterdir():
            if (
                candidate.name.startswith(prefix)
                and candidate.name.endswith(".active.jsonl")
                and candidate.is_file()
                and not candidate.is_symlink()
            ):
                try:
                    _finalize_active_segment(candidate)
                except OSError:
                    continue


class BetaDiagnosticsRecorder:
    def __init__(
        self,
        *,
        data_dir: Path,
        role: BackendRole,
        app_version: str,
        enabled: bool,
        sample_interval_seconds: float = BETA_DIAGNOSTICS_RESOURCE_SAMPLE_SECONDS,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.role = role
        self.app_version = app_version
        self.enabled = enabled
        self.sample_interval_seconds = sample_interval_seconds
        self.clock = clock or (lambda: datetime.now(UTC))
        self.monotonic_clock = monotonic_clock or time.monotonic
        self.root_path = data_dir / BETA_DIAGNOSTICS_DIRECTORY_NAME
        self.session_id = str(uuid.uuid4())
        self.last_error: str | None = None
        self._timeline = RotatingJsonlWriter(
            root_path=self.root_path,
            component=role,
            stream="timeline",
            clock=self.clock,
        )
        self._resources = RotatingJsonlWriter(
            root_path=self.root_path,
            component=role,
            stream="resource-samples",
            clock=self.clock,
        )
        self._sample_task: asyncio.Task[None] | None = None
        self._started = False
        self._stopping = False
        self._last_wall_seconds: float | None = None
        self._last_monotonic_seconds: float | None = None
        self._process = psutil.Process(os.getpid())
        self._health_provider: HealthProvider | None = None

    def set_health_provider(self, provider: HealthProvider | None) -> None:
        self._health_provider = provider

    async def start(self) -> None:
        if not self.enabled or self._started:
            return
        self._started = True
        try:
            self._process.cpu_percent(interval=None)
            self.record_timeline(
                "backend_process_started",
                {
                    "process_id": os.getpid(),
                    "current_version": self.app_version,
                    "source": self.role,
                },
            )
            self.record_resource_sample()
            self._prune()
            self._sample_task = asyncio.create_task(self._sample_loop())
        except Exception as exc:
            self._capture_error(exc)

    async def stop(self) -> None:
        if not self.enabled or not self._started or self._stopping:
            return
        self.record_timeline("backend_process_stopping", {"source": self.role})
        self._stopping = True
        if self._sample_task is not None:
            self._sample_task.cancel()
            await asyncio.gather(self._sample_task, return_exceptions=True)
            self._sample_task = None
        try:
            self._timeline.close()
            self._resources.close()
        except Exception as exc:
            self._capture_error(exc)

    def record_timeline(
        self,
        event: str,
        details: Mapping[str, object] | None = None,
        severity: DiagnosticSeverity = "info",
    ) -> None:
        if (
            not self.enabled
            or not self._started
            or self._stopping
            or _SAFE_EVENT.fullmatch(event) is None
            or severity not in _SEVERITIES
        ):
            return
        try:
            timestamp = self._timestamp()
            self._timeline.append(
                {
                    "schema_version": BETA_DIAGNOSTICS_SCHEMA_VERSION,
                    "stream": "timeline",
                    "wall_time": timestamp["wall_time"],
                    "monotonic_ms": timestamp["monotonic_ms"],
                    "component": self.role,
                    "session_id": self.session_id,
                    "event": event,
                    "severity": severity,
                    "details": _sanitize_timeline_details(details),
                }
            )
            self._record_clock_jump_if_needed(timestamp)
        except Exception as exc:
            self._capture_error(exc)

    def record_resource_sample(self) -> None:
        if not self.enabled or self._stopping:
            return
        try:
            timestamp = self._timestamp()
            memory = self._process.memory_info()
            children = self._safe_children()
            database_path = self.data_dir / "auto_email_sender.db"
            health_counts = self._health_counts()
            record: dict[str, object] = {
                "schema_version": BETA_DIAGNOSTICS_SCHEMA_VERSION,
                "stream": "resource-samples",
                "wall_time": timestamp["wall_time"],
                "monotonic_ms": timestamp["monotonic_ms"],
                "component": self.role,
                "session_id": self.session_id,
                "cpu_percent": round(max(0.0, self._process.cpu_percent(interval=None)), 2),
                "rss_bytes": max(0, memory.rss),
                "handles_or_fds": self._handle_or_fd_count(),
                "threads": max(0, self._process.num_threads()),
                "child_processes": len(children),
                "playwright_processes": sum(_is_playwright_process(child) for child in children),
                "database_bytes": _safe_file_size(database_path),
                "wal_bytes": _safe_file_size(Path(f"{database_path}-wal")),
                "shm_bytes": _safe_file_size(Path(f"{database_path}-shm")),
                "logs_bytes": _safe_directory_size(self.data_dir / "logs", 4096),
                "runtime_bytes": _safe_directory_size(self.data_dir / "runtime", 256),
                "api_present": self.role in {"api", "combined"},
                "worker_present": self.role in {"worker", "combined"},
                **health_counts,
            }
            self._resources.append(record)
            self._record_clock_jump_if_needed(timestamp)
            self._prune()
        except Exception as exc:
            self._capture_error(exc)

    async def _sample_loop(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(self.sample_interval_seconds)
            except asyncio.CancelledError:
                return
            self.record_resource_sample()

    def _timestamp(self) -> dict[str, float | str]:
        wall = self.clock()
        if wall.tzinfo is None:
            wall = wall.replace(tzinfo=UTC)
        return {
            "wall_time": wall.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "wall_seconds": wall.timestamp(),
            "monotonic_ms": round(self.monotonic_clock() * 1000, 3),
        }

    def _record_clock_jump_if_needed(self, timestamp: Mapping[str, float | str]) -> None:
        wall_seconds = float(timestamp["wall_seconds"])
        monotonic_seconds = float(timestamp["monotonic_ms"]) / 1000
        previous_wall = self._last_wall_seconds
        previous_monotonic = self._last_monotonic_seconds
        self._last_wall_seconds = wall_seconds
        self._last_monotonic_seconds = monotonic_seconds
        if previous_wall is None or previous_monotonic is None:
            return
        wall_delta = wall_seconds - previous_wall
        monotonic_delta = monotonic_seconds - previous_monotonic
        offset = wall_delta - monotonic_delta
        event: str | None = None
        details: dict[str, object] = {}
        if abs(offset) >= _CLOCK_JUMP_THRESHOLD_SECONDS:
            event = "wall_clock_jump_detected"
            details["clock_offset_ms"] = round(offset * 1000, 3)
        elif monotonic_delta >= max(30.0, self.sample_interval_seconds * 2.5):
            event = "process_timer_gap_detected"
            details["sleep_gap_ms"] = round(monotonic_delta * 1000, 3)
        if event is None:
            return
        self._timeline.append(
            {
                "schema_version": BETA_DIAGNOSTICS_SCHEMA_VERSION,
                "stream": "timeline",
                "wall_time": timestamp["wall_time"],
                "monotonic_ms": timestamp["monotonic_ms"],
                "component": self.role,
                "session_id": self.session_id,
                "event": event,
                "severity": "warning",
                "details": details,
            }
        )

    def _safe_children(self) -> list[psutil.Process]:
        try:
            return self._process.children(recursive=True)
        except (psutil.Error, OSError):
            return []

    def _handle_or_fd_count(self) -> int:
        try:
            if os.name == "nt":
                return max(0, self._process.num_handles())
            return max(0, self._process.num_fds())
        except (AttributeError, psutil.Error, OSError):
            return 0

    def _health_counts(self) -> dict[str, int]:
        if self._health_provider is None:
            return {
                "healthy_subsystems": 0,
                "degraded_subsystems": 0,
                "failed_subsystems": 0,
            }
        try:
            snapshot = self._health_provider()
        except Exception:
            return {
                "healthy_subsystems": 0,
                "degraded_subsystems": 0,
                "failed_subsystems": 0,
            }
        counts = {"healthy": 0, "degraded": 0, "failed": 0}
        for value in snapshot.values():
            state = value.get("state") if isinstance(value, Mapping) else None
            if state is None and isinstance(value, Mapping):
                consecutive_failures = value.get("consecutive_failures")
                state = (
                    "degraded"
                    if isinstance(consecutive_failures, int) and consecutive_failures > 0
                    else "healthy"
                )
            if state in counts:
                counts[state] += 1
        return {
            "healthy_subsystems": counts["healthy"],
            "degraded_subsystems": counts["degraded"],
            "failed_subsystems": counts["failed"],
        }

    def _prune(self) -> None:
        prune_beta_diagnostics(self.root_path)

    def _capture_error(self, exc: Exception) -> None:
        self.last_error = type(exc).__name__


def create_beta_diagnostics_recorder() -> BetaDiagnosticsRecorder:
    global _current_recorder
    settings = get_settings()
    app_version = os.getenv("AUTO_EMAIL_SENDER_APP_VERSION", "development")
    recorder = BetaDiagnosticsRecorder(
        data_dir=settings.data_dir,
        role=get_backend_role(),
        app_version=app_version,
        enabled=beta_diagnostics_enabled(app_version),
    )
    _current_recorder = recorder
    return recorder


def record_beta_diagnostic_event(
    event: str,
    details: Mapping[str, object] | None = None,
    severity: DiagnosticSeverity = "info",
) -> None:
    recorder = _current_recorder
    if recorder is None:
        return
    recorder.record_timeline(event, details, severity)


def prune_beta_diagnostics(
    root_path: Path,
    *,
    now: datetime | None = None,
    retention_days: int = BETA_DIAGNOSTICS_RETENTION_DAYS,
    max_total_bytes: int = BETA_DIAGNOSTICS_MAX_TOTAL_BYTES,
) -> None:
    segment_root = root_path / "segments"
    if not segment_root.is_dir() or segment_root.is_symlink():
        return
    resolved_now = now or datetime.now(UTC)
    cutoff = resolved_now - timedelta(days=max(0, retention_days))
    segments: list[tuple[Path, int, float]] = []
    protected_bytes = 0
    for component_path in segment_root.iterdir():
        if not component_path.is_dir() or component_path.is_symlink():
            continue
        for candidate in component_path.iterdir():
            if not candidate.name.endswith(".jsonl") or not candidate.is_file() or candidate.is_symlink():
                continue
            try:
                file_stat = candidate.stat()
            except OSError:
                continue
            if candidate.name.endswith(".active.jsonl"):
                protected_bytes += file_stat.st_size
                continue
            modified = datetime.fromtimestamp(file_stat.st_mtime, tz=UTC)
            if modified < cutoff:
                try:
                    candidate.unlink()
                except OSError:
                    pass
                continue
            segments.append((candidate, file_stat.st_size, file_stat.st_mtime))
    segments.sort(key=lambda entry: entry[2])
    total_bytes = protected_bytes + sum(entry[1] for entry in segments)
    for candidate, size, _modified in segments:
        if total_bytes <= max_total_bytes:
            break
        try:
            candidate.unlink()
        except OSError:
            continue
        total_bytes -= size


def _sanitize_timeline_details(
    details: Mapping[str, object] | None,
) -> dict[str, str | int | float | bool | None]:
    sanitized: dict[str, str | int | float | bool | None] = {}
    if details is None:
        return sanitized
    for key, value in details.items():
        if key not in _TIMELINE_DETAIL_KEYS:
            continue
        if value is None or isinstance(value, bool):
            sanitized[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if value == value and value not in {float("inf"), float("-inf")}:
                sanitized[key] = value
        elif isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value.strip()) is not None:
            sanitized[key] = value.strip()
    return sanitized


def _ensure_private_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    file_stat = directory.lstat()
    if not stat.S_ISDIR(file_stat.st_mode) or stat.S_ISLNK(file_stat.st_mode):
        raise OSError("Beta diagnostics path is not a private directory")
    _set_private_permissions(directory, 0o700)


def _set_private_permissions(target: Path, mode: int) -> None:
    if os.name != "nt":
        target.chmod(mode)


def _finalize_active_segment(active_path: Path) -> None:
    if not active_path.name.endswith(".active.jsonl"):
        return
    final_path = active_path.with_name(
        active_path.name.removesuffix(".active.jsonl") + ".jsonl"
    )
    active_path.replace(final_path)


def _safe_file_size(file_path: Path) -> int:
    try:
        file_stat = file_path.lstat()
    except OSError:
        return 0
    return max(0, file_stat.st_size) if stat.S_ISREG(file_stat.st_mode) else 0


def _safe_directory_size(directory: Path, max_entries: int) -> int:
    try:
        entries = list(directory.iterdir())[:max_entries]
    except OSError:
        return 0
    return sum(_safe_file_size(entry) for entry in entries)


def _is_playwright_process(process: psutil.Process) -> bool:
    try:
        name = process.name().lower()
    except (psutil.Error, OSError):
        return False
    return any(marker in name for marker in ("chromium", "chrome", "playwright"))


__all__ = [
    "BETA_DIAGNOSTICS_DIRECTORY_NAME",
    "BETA_DIAGNOSTICS_MAX_RECORD_BYTES",
    "BETA_DIAGNOSTICS_MAX_SEGMENT_BYTES",
    "BETA_DIAGNOSTICS_MAX_TOTAL_BYTES",
    "BETA_DIAGNOSTICS_RETENTION_DAYS",
    "BETA_DIAGNOSTICS_SCHEMA_VERSION",
    "BetaDiagnosticsRecorder",
    "RotatingJsonlWriter",
    "beta_diagnostics_enabled",
    "create_beta_diagnostics_recorder",
    "prune_beta_diagnostics",
    "record_beta_diagnostic_event",
]
