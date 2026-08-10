from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


TEST_FAULTS_ENABLED_ENV = "AUTO_EMAIL_SENDER_TEST_FAULTS"
TEST_FAULT_DIR_ENV = "AUTO_EMAIL_SENDER_TEST_FAULT_DIR"
TEST_FAULT_POINTS_ENV = "AUTO_EMAIL_SENDER_TEST_FAULT_POINTS"
TEST_FAULT_PROCESS_ID_ENV = "AUTO_EMAIL_SENDER_TEST_PROCESS_ID"
TEST_FAULT_TIMEOUT_ENV = "AUTO_EMAIL_SENDER_TEST_FAULT_TIMEOUT_SECONDS"
TEST_CRAWL_LOOPBACK_HOSTS_ENV = "AUTO_EMAIL_SENDER_TEST_CRAWL_LOOPBACK_HOSTS"
TEST_CLOCK_OFFSET_FILE_ENV = "AUTO_EMAIL_SENDER_TEST_CLOCK_OFFSET_FILE"

_TEST_FAULTS_ENABLED_VALUE = "enabled-for-tests-only"
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_DEFAULT_TIMEOUT_SECONDS = 60.0
_MAX_TIMEOUT_SECONDS = 300.0
_POLL_SECONDS = 0.01
_WINDOWS_FILE_RETRY_DELAYS_SECONDS = (0.01, 0.02, 0.04, 0.08, 0.16, 0.32)
_MAX_ABSOLUTE_CLOCK_OFFSET_SECONDS = 10 * 366 * 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class _FaultPointConfig:
    directory: Path
    process_id: str
    timeout_seconds: float


def _resolve_fault_point_config(name: str) -> _FaultPointConfig | None:
    if not _SAFE_COMPONENT.fullmatch(name):
        raise ValueError(f"Invalid fault point name: {name!r}")
    if os.getenv(TEST_FAULTS_ENABLED_ENV) != _TEST_FAULTS_ENABLED_VALUE:
        return None

    enabled_points = {
        value.strip()
        for value in os.getenv(TEST_FAULT_POINTS_ENV, "").split(",")
        if value.strip()
    }
    if name not in enabled_points:
        return None

    raw_directory = os.getenv(TEST_FAULT_DIR_ENV, "").strip()
    if not raw_directory:
        raise RuntimeError(f"{TEST_FAULT_DIR_ENV} is required when test faults are enabled")
    directory = Path(raw_directory).expanduser().resolve()
    if not directory.is_dir():
        raise RuntimeError(f"Test fault directory does not exist: {directory}")

    process_id = os.getenv(TEST_FAULT_PROCESS_ID_ENV, str(os.getpid())).strip()
    if not process_id or not _SAFE_COMPONENT.fullmatch(process_id):
        raise RuntimeError(f"Invalid test fault process id: {process_id!r}")

    raw_timeout = os.getenv(TEST_FAULT_TIMEOUT_ENV, str(_DEFAULT_TIMEOUT_SECONDS))
    try:
        timeout_seconds = float(raw_timeout)
    except ValueError as exc:
        raise RuntimeError(f"Invalid test fault timeout: {raw_timeout!r}") from exc
    timeout_seconds = min(_MAX_TIMEOUT_SECONDS, max(_POLL_SECONDS, timeout_seconds))
    return _FaultPointConfig(
        directory=directory,
        process_id=process_id,
        timeout_seconds=timeout_seconds,
    )


def _create_fault_hit(
    name: str,
    config: _FaultPointConfig,
) -> tuple[Path, Path, Path]:
    hit_id = uuid.uuid4().hex
    stem = f"{config.process_id}--{name}--{hit_id}"
    reached_path = config.directory / f"{stem}.reached"
    release_path = config.directory / f"{stem}.release"
    completed_path = config.directory / f"{stem}.completed"
    reached_path.write_text(
        json.dumps(
            {
                "fault_point": name,
                "process_id": config.process_id,
                "pid": os.getpid(),
                "hit_id": hit_id,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return reached_path, release_path, completed_path


def _run_fault_file_operation(
    operation: Callable[[], object],
    *,
    platform_name: str,
    sleep: Callable[[float], object],
) -> None:
    """Retry only the brief Windows sharing locks seen in test fault markers."""

    retry_delays = (
        _WINDOWS_FILE_RETRY_DELAYS_SECONDS if platform_name == "nt" else ()
    )
    for delay_seconds in (*retry_delays, None):
        try:
            operation()
            return
        except PermissionError:
            if delay_seconds is None:
                raise
            sleep(delay_seconds)


def _complete_fault_hit(
    reached_path: Path,
    release_path: Path,
    completed_path: Path,
    *,
    platform_name: str = os.name,
    sleep: Callable[[float], object] = time.sleep,
) -> None:
    _run_fault_file_operation(
        lambda: release_path.unlink(missing_ok=True),
        platform_name=platform_name,
        sleep=sleep,
    )
    try:
        _run_fault_file_operation(
            lambda: reached_path.replace(completed_path),
            platform_name=platform_name,
            sleep=sleep,
        )
    except FileNotFoundError:
        return


async def wait_at_fault_point(name: str) -> bool:
    """Pause an async test process at a named, explicitly enabled fault point.

    The hook is inert unless all test-only environment controls are present and
    ``name`` is explicitly listed. Production code may call this function at a
    critical boundary without changing normal runtime behavior.
    """

    config = _resolve_fault_point_config(name)
    if config is None:
        return False
    reached_path, release_path, completed_path = _create_fault_hit(name, config)
    deadline = time.monotonic() + config.timeout_seconds
    while not release_path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting to release test fault point {name!r}")
        await asyncio.sleep(_POLL_SECONDS)
    _complete_fault_hit(reached_path, release_path, completed_path)
    return True


def wait_at_fault_point_sync(name: str) -> bool:
    """Synchronous counterpart used by migration and process bootstrap tests."""

    config = _resolve_fault_point_config(name)
    if config is None:
        return False
    reached_path, release_path, completed_path = _create_fault_hit(name, config)
    deadline = time.monotonic() + config.timeout_seconds
    while not release_path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting to release test fault point {name!r}")
        time.sleep(_POLL_SECONDS)
    _complete_fault_hit(reached_path, release_path, completed_path)
    return True


def resolve_test_crawl_loopback_host(hostname: str) -> str | None:
    """Resolve an explicit reserved test hostname to loopback under the fault gate.

    This exists solely so real crawler process tests can use a local HTTP server
    without weakening the production SSRF checks.  Only ``*.test.invalid`` names
    explicitly listed by the test harness are eligible.
    """

    if os.getenv(TEST_FAULTS_ENABLED_ENV) != _TEST_FAULTS_ENABLED_VALUE:
        return None
    normalized = hostname.rstrip(".").lower()
    allowed_hosts = {
        value.strip().rstrip(".").lower()
        for value in os.getenv(TEST_CRAWL_LOOPBACK_HOSTS_ENV, "").split(",")
        if value.strip()
    }
    if normalized not in allowed_hosts:
        return None
    if not normalized.endswith(".test.invalid") or not _SAFE_COMPONENT.fullmatch(
        normalized
    ):
        raise RuntimeError(
            f"Test crawl loopback host must use *.test.invalid: {hostname!r}"
        )
    raw_directory = os.getenv(TEST_FAULT_DIR_ENV, "").strip()
    if not raw_directory or not Path(raw_directory).expanduser().resolve().is_dir():
        raise RuntimeError(
            f"{TEST_FAULT_DIR_ENV} must name an existing directory for crawl overrides"
        )
    return "127.0.0.1"


def get_test_browser_host_resolver_args() -> tuple[str, ...]:
    """Return Chromium loopback mappings under the same strict crawler test gate.

    Python's direct-fetch transport can connect to the loopback override without
    system DNS.  Chromium is a separate process, so packaged lifecycle tests need
    an equivalent explicit resolver rule to exercise and then audit its process
    tree.  No argument is returned outside the existing tests-only gate.
    """

    if os.getenv(TEST_FAULTS_ENABLED_ENV) != _TEST_FAULTS_ENABLED_VALUE:
        return ()
    hosts = sorted(
        {
            value.strip().rstrip(".").lower()
            for value in os.getenv(TEST_CRAWL_LOOPBACK_HOSTS_ENV, "").split(",")
            if value.strip()
        }
    )
    if not hosts:
        return ()
    for hostname in hosts:
        if resolve_test_crawl_loopback_host(hostname) is None:
            raise RuntimeError(f"Invalid test browser loopback host: {hostname!r}")
    rules = ",".join(f"MAP {hostname} 127.0.0.1" for hostname in hosts)
    return (f"--host-resolver-rules={rules}",)


def resolve_test_clock_offset_seconds() -> float:
    """Return a dynamic wall-clock offset under the strict process-test gate."""

    if os.getenv(TEST_FAULTS_ENABLED_ENV) != _TEST_FAULTS_ENABLED_VALUE:
        return 0.0
    raw_path = os.getenv(TEST_CLOCK_OFFSET_FILE_ENV, "").strip()
    if not raw_path:
        return 0.0
    raw_directory = os.getenv(TEST_FAULT_DIR_ENV, "").strip()
    if not raw_directory:
        raise RuntimeError(
            f"{TEST_FAULT_DIR_ENV} is required for a test clock override"
        )
    directory = Path(raw_directory).expanduser().resolve()
    offset_path = Path(raw_path).expanduser().resolve()
    if not directory.is_dir() or not offset_path.is_relative_to(directory):
        raise RuntimeError("Test clock offset file must be inside the test fault directory")
    if not offset_path.is_file():
        raise RuntimeError(f"Test clock offset file does not exist: {offset_path}")
    raw_offset = offset_path.read_text(encoding="utf-8").strip()
    try:
        offset_seconds = float(raw_offset)
    except ValueError as exc:
        raise RuntimeError(f"Invalid test clock offset: {raw_offset!r}") from exc
    if (
        not math.isfinite(offset_seconds)
        or abs(offset_seconds) > _MAX_ABSOLUTE_CLOCK_OFFSET_SECONDS
    ):
        raise RuntimeError(f"Test clock offset is outside the safe bound: {raw_offset!r}")
    return offset_seconds


__all__ = [
    "TEST_CRAWL_LOOPBACK_HOSTS_ENV",
    "TEST_CLOCK_OFFSET_FILE_ENV",
    "TEST_FAULT_DIR_ENV",
    "TEST_FAULT_POINTS_ENV",
    "TEST_FAULT_PROCESS_ID_ENV",
    "TEST_FAULT_TIMEOUT_ENV",
    "TEST_FAULTS_ENABLED_ENV",
    "get_test_browser_host_resolver_args",
    "wait_at_fault_point",
    "wait_at_fault_point_sync",
    "resolve_test_crawl_loopback_host",
    "resolve_test_clock_offset_seconds",
]
