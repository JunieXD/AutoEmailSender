from __future__ import annotations

import asyncio
import logging
import traceback
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Awaitable, Callable

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import API_ROUTERS
from app.core.config import get_settings
from app.core.database import dispose_engine, get_session_factory
from app.core.migrations import ensure_database_schema
from app.core.request_context import RequestContextMiddleware
from app.core.startup_logging import write_startup_phase_log
from app.core.windows_event_loop import ensure_windows_proactor_event_loop_policy
from app.services.operation_logs import cleanup_old_operation_logs
from app.services.crawl_job_runtime import recover_interrupted_crawl_jobs
from app.services.runtime_manager import RuntimeManager
from app.services.task_runtime import recover_interrupted_match_analysis_runs


ensure_windows_proactor_event_loop_policy()
logger = logging.getLogger(__name__)

STARTUP_DATABASE_LOCK_MAX_ATTEMPTS = 4
STARTUP_DATABASE_LOCK_RETRY_SECONDS = 2.0


@dataclass(slots=True)
class StartupStatus:
    state: str
    phase: str
    message: str
    started_at: datetime
    updated_at: datetime
    error: str | None = None

    def to_response(self) -> dict[str, object]:
        payload = asdict(self)
        payload["started_at"] = self.started_at.isoformat()
        payload["updated_at"] = self.updated_at.isoformat()
        payload["elapsed_seconds"] = max(
            0,
            int((datetime.now(UTC) - self.started_at).total_seconds()),
        )
        return payload


STARTUP_PHASE_MESSAGES = {
    "starting": "正在启动系统服务",
    "migrating_database": "正在检查和升级本地数据",
    "cleaning_logs": "正在整理本地运行日志",
    "starting_workers": "正在启动后台任务",
    "ready": "系统已准备就绪",
    "error": "系统准备失败",
}


def initialize_startup_status(app: FastAPI) -> None:
    now = datetime.now(UTC)
    app.state.startup_status = StartupStatus(
        state="starting",
        phase="starting",
        message=STARTUP_PHASE_MESSAGES["starting"],
        started_at=now,
        updated_at=now,
    )


def set_startup_status(
    app: FastAPI,
    *,
    state: str,
    phase: str,
    error: str | None = None,
) -> None:
    current = getattr(app.state, "startup_status", None)
    now = datetime.now(UTC)
    started_at = current.started_at if current is not None else now
    app.state.startup_status = StartupStatus(
        state=state,
        phase=phase,
        message=STARTUP_PHASE_MESSAGES[phase],
        started_at=started_at,
        updated_at=now,
        error=error,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_startup_status(app)
    app.state.runtime_ready = False
    app.state.runtime_error = None
    app.state.runtime_manager = None
    runtime_task = asyncio.create_task(initialize_runtime(app))
    runtime_task.add_done_callback(log_runtime_initialization_failure)

    try:
        yield
    finally:
        if not runtime_task.done():
            runtime_task.cancel()
            await asyncio.gather(runtime_task, return_exceptions=True)
        runtime_manager = app.state.runtime_manager
        if runtime_manager is not None:
            await runtime_manager.stop()
        await dispose_engine()


async def initialize_runtime(app: FastAPI) -> None:
    try:
        set_startup_status(app, state="starting", phase="migrating_database")
        await run_startup_step_with_database_lock_retry(
            "migrating_database",
            ensure_database_schema,
        )
        set_startup_status(app, state="starting", phase="cleaning_logs")
        await run_startup_step_with_database_lock_retry(
            "cleaning_logs",
            cleanup_runtime_state,
        )
        set_startup_status(app, state="starting", phase="starting_workers")
        if get_settings().enable_background_workers:
            runtime_manager = RuntimeManager(get_session_factory())
            await runtime_manager.start()
            app.state.runtime_manager = runtime_manager
        app.state.runtime_ready = True
        set_startup_status(app, state="ready", phase="ready")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        app.state.runtime_error = str(exc)
        set_startup_status(app, state="error", phase="error", error=str(exc))
        write_startup_diagnostic_log("桌面后端启动初始化失败", exc=exc)
        raise


async def cleanup_runtime_state() -> None:
    async with get_session_factory()() as session:
        await cleanup_old_operation_logs(session)
        await session.commit()
    await recover_interrupted_crawl_jobs(get_session_factory())
    await recover_interrupted_match_analysis_runs(get_session_factory())


async def run_startup_step_with_database_lock_retry(
    phase: str,
    step: Callable[[], Awaitable[None]],
) -> None:
    for attempt in range(1, STARTUP_DATABASE_LOCK_MAX_ATTEMPTS + 1):
        try:
            await step()
            return
        except Exception as exc:
            if not is_sqlite_database_lock_error(exc) or attempt >= STARTUP_DATABASE_LOCK_MAX_ATTEMPTS:
                raise
            write_startup_diagnostic_log(
                "启动步骤遇到 SQLite 数据库锁，准备重试",
                phase=phase,
                attempt=attempt,
                max_attempts=STARTUP_DATABASE_LOCK_MAX_ATTEMPTS,
                exc=exc,
            )
            await asyncio.sleep(STARTUP_DATABASE_LOCK_RETRY_SECONDS * attempt)


def is_sqlite_database_lock_error(exc: Exception) -> bool:
    current: BaseException | None = exc
    while current is not None:
        message = str(current).lower()
        if "database is locked" in message or "database table is locked" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


def write_startup_diagnostic_log(
    message: str,
    *,
    phase: str | None = None,
    attempt: int | None = None,
    max_attempts: int | None = None,
    exc: Exception | None = None,
) -> None:
    try:
        log_path = get_settings().data_dir / "logs" / "startup.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"[{datetime.now(UTC).isoformat()}] {message}",
        ]
        if phase is not None:
            lines.append(f"phase={phase}")
        if attempt is not None and max_attempts is not None:
            lines.append(f"attempt={attempt}/{max_attempts}")
        if exc is not None:
            lines.append(f"error={type(exc).__name__}: {exc}")
            lines.append("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip())
        append_text(log_path, "\n".join(lines) + "\n")
    except Exception:
        logger.exception("写入启动诊断日志失败")


def append_text(path: Path, content: str) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(content)


def log_runtime_initialization_failure(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    try:
        task.result()
    except Exception:
        logger.exception("桌面后端运行时初始化失败")


def create_app() -> FastAPI:
    write_startup_phase_log("main.create_app.start")
    app = FastAPI(title="Auto Email Agent API", version="3.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)

    for router in API_ROUTERS:
        app.include_router(router)

    @app.get("/api/ping")
    async def ping() -> dict[str, str]:
        return {
            "status": "ok",
            "message": "Auto Email Agent API 已启动",
        }

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/startup-status")
    async def startup_status() -> dict[str, object]:
        status = getattr(app.state, "startup_status", None)
        if status is None:
            initialize_startup_status(app)
            status = app.state.startup_status
        return status.to_response()

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        runtime_error = getattr(app.state, "runtime_error", None)
        if runtime_error:
            raise HTTPException(status_code=500, detail=runtime_error)
        if not getattr(app.state, "runtime_ready", False):
            raise HTTPException(status_code=503, detail="后端初始化中")
        return {"status": "ready"}

    write_startup_phase_log("main.create_app.ready", detail=f"routers={len(API_ROUTERS)}")
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8010, reload=True)
