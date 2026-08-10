from __future__ import annotations

import asyncio
import os
import signal
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import cast

from app.core.agent_runtime_descriptor import get_runtime_id
from app.core.config import get_settings
from app.core.database import dispose_engine, get_session_factory
from app.core.error_formatting import safe_exception_message
from app.core.migrations import require_database_schema_at_head
from app.core.process_liveness import process_is_running
from app.core.runtime_group import (
    RuntimeSubsystemStatus,
    cleanup_owned_runtime_process_status,
    require_ready_api_leader,
    write_runtime_process_status,
)
from app.core.startup_logging import write_startup_phase_log
from app.services.runtime_manager import RuntimeManager
from app.services.operation_logs import sanitize_user_visible_error
from app.services.worker_claim_recovery import recover_interrupted_worker_claims


API_PID_ENV = "AUTO_EMAIL_SENDER_API_PID"
WORKER_GENERATION_ENV = "AUTO_EMAIL_SENDER_WORKER_GENERATION"
REQUIRED_PROCESS_POLL_SECONDS = 1.0
WORKER_HEARTBEAT_SECONDS = 2.0


def get_api_pid() -> int:
    raw_pid = os.getenv(API_PID_ENV, "").strip()
    try:
        api_pid = int(raw_pid)
    except ValueError as exc:
        raise RuntimeError(f"{API_PID_ENV} is required for the Worker role") from exc
    if api_pid <= 0:
        raise RuntimeError(f"{API_PID_ENV} must be a positive process id")
    return api_pid


def get_worker_generation() -> str:
    value = os.getenv(WORKER_GENERATION_ENV, "").strip()
    return value or uuid.uuid4().hex


async def watch_required_processes(
    stop_event: asyncio.Event,
    *,
    desktop_pid: int | None,
    api_pid: int,
    poll_seconds: float = REQUIRED_PROCESS_POLL_SECONDS,
) -> None:
    while not stop_event.is_set():
        await asyncio.sleep(poll_seconds)
        if desktop_pid is not None and not process_is_running(desktop_pid):
            write_startup_phase_log(
                "worker.desktop_parent_stopped",
                detail=f"desktop_pid={desktop_pid}",
            )
            stop_event.set()
            return
        if not process_is_running(api_pid):
            write_startup_phase_log(
                "worker.api_leader_stopped",
                detail=f"api_pid={api_pid}",
            )
            stop_event.set()
            return


def publish_worker_heartbeat(
    *,
    runtime_manager: RuntimeManager,
    runtime_id: str,
    generation: str,
    started_at: datetime,
) -> None:
    settings = get_settings()
    subsystems = cast(
        dict[str, RuntimeSubsystemStatus],
        runtime_manager.get_health_snapshot(),
    )
    write_runtime_process_status(
        settings.data_dir,
        runtime_id=runtime_id,
        role="worker",
        generation=generation,
        state="ready",
        started_at=started_at,
        health="degraded" if runtime_manager.is_degraded() else "healthy",
        draining=False,
        subsystems=subsystems,
    )


async def publish_worker_heartbeats(
    stop_event: asyncio.Event,
    *,
    runtime_manager: RuntimeManager,
    runtime_id: str,
    generation: str,
    started_at: datetime,
    interval_seconds: float = WORKER_HEARTBEAT_SECONDS,
) -> None:
    while not stop_event.is_set():
        publish_worker_heartbeat(
            runtime_manager=runtime_manager,
            runtime_id=runtime_id,
            generation=generation,
            started_at=started_at,
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue


def _install_signal_handlers(stop_event: asyncio.Event) -> Callable[[], None]:
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    previous: dict[signal.Signals, object] = {}

    def request_stop() -> None:
        stop_event.set()

    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_number, request_stop)
        except (NotImplementedError, RuntimeError):
            previous[signal_number] = signal.getsignal(signal_number)

            def handle_signal(
                signum: int,
                frame: object,
                *,
                event_loop: asyncio.AbstractEventLoop = loop,
            ) -> None:
                _ = signum, frame
                event_loop.call_soon_threadsafe(request_stop)

            signal.signal(signal_number, handle_signal)
        else:
            installed.append(signal_number)

    def cleanup() -> None:
        for signal_number in installed:
            loop.remove_signal_handler(signal_number)
        for signal_number, handler in previous.items():
            signal.signal(signal_number, handler)

    return cleanup


async def run_worker_process(*, desktop_pid: int | None) -> None:
    settings = get_settings()
    runtime_id = get_runtime_id()
    api_pid = get_api_pid()
    generation = get_worker_generation()
    started_at = datetime.now(UTC)
    stop_event = asyncio.Event()
    cleanup_signal_handlers = _install_signal_handlers(stop_event)
    runtime_manager: RuntimeManager | None = None
    watchdog: asyncio.Task[None] | None = None
    heartbeat: asyncio.Task[None] | None = None

    write_runtime_process_status(
        settings.data_dir,
        runtime_id=runtime_id,
        role="worker",
        generation=generation,
        state="starting",
        started_at=started_at,
    )
    try:
        await asyncio.to_thread(require_database_schema_at_head)
        require_ready_api_leader(
            settings.data_dir,
            runtime_id=runtime_id,
            api_pid=api_pid,
        )
        recovery_summary = await recover_interrupted_worker_claims(
            get_session_factory(),
            preserve_full_imap_claims=True,
        )
        write_startup_phase_log(
            "worker.claim_recovery.complete",
            detail=recovery_summary.to_log_detail(),
        )
        runtime_manager = RuntimeManager(
            get_session_factory(),
            runtime_id=runtime_id,
            worker_generation=generation,
        )
        await runtime_manager.start()
        watchdog = asyncio.create_task(
            watch_required_processes(
                stop_event,
                desktop_pid=desktop_pid,
                api_pid=api_pid,
            )
        )
        publish_worker_heartbeat(
            runtime_manager=runtime_manager,
            runtime_id=runtime_id,
            generation=generation,
            started_at=started_at,
        )
        heartbeat = asyncio.create_task(
            publish_worker_heartbeats(
                stop_event,
                runtime_manager=runtime_manager,
                runtime_id=runtime_id,
                generation=generation,
                started_at=started_at,
            )
        )
        write_startup_phase_log(
            "worker.ready",
            detail=f"runtime_id={runtime_id} generation={generation}",
        )
        await stop_event.wait()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        stop_event.set()
        if heartbeat is not None:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            heartbeat = None
        write_runtime_process_status(
            settings.data_dir,
            runtime_id=runtime_id,
            role="worker",
            generation=generation,
            state="error",
            started_at=started_at,
            error=sanitize_user_visible_error(safe_exception_message(exc)),
        )
        raise
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        if watchdog is not None:
            watchdog.cancel()
            with suppress(asyncio.CancelledError):
                await watchdog
        if runtime_manager is not None:
            write_runtime_process_status(
                settings.data_dir,
                runtime_id=runtime_id,
                role="worker",
                generation=generation,
                state="stopping",
                started_at=started_at,
                health=(
                    "degraded" if runtime_manager.is_degraded() else "healthy"
                ),
                draining=True,
                subsystems=cast(
                    dict[str, RuntimeSubsystemStatus],
                    runtime_manager.get_health_snapshot(),
                ),
            )
            await runtime_manager.stop()
        await dispose_engine()
        cleanup_owned_runtime_process_status(
            settings.data_dir,
            runtime_id=runtime_id,
            role="worker",
            generation=generation,
        )
        cleanup_signal_handlers()


__all__ = [
    "API_PID_ENV",
    "REQUIRED_PROCESS_POLL_SECONDS",
    "WORKER_HEARTBEAT_SECONDS",
    "WORKER_GENERATION_ENV",
    "get_api_pid",
    "get_worker_generation",
    "publish_worker_heartbeat",
    "publish_worker_heartbeats",
    "run_worker_process",
    "watch_required_processes",
]
