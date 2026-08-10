from __future__ import annotations

import asyncio
import logging
import os
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.backend_error_logging import write_backend_worker_error_log
from app.core.config import get_settings
from app.models import AppSetting
from app.modules.campaigns.public import (
    BatchDraftGenerationCoordinator,
    BatchDraftScheduler,
)
from app.modules.crawler.public import run_crawler_v2_once
from app.modules.matching.public import run_queued_match_analysis_jobs_once
from app.modules.system.public import get_runtime_settings
from app.modules.communications.public import poll_for_replies_once, poll_imap_history_once
from app.modules.workspace.public import (
    DEFAULT_SEND_INTERVAL_MAX_SECONDS,
    dispatch_due_tasks_once,
    mark_overdue_manual_schedules_missed,
)
from app.services.operation_logs import sanitize_user_visible_error


logger = logging.getLogger(__name__)
CRAWLER_WORK_ITEM_WORKER_COUNT = 8
CRAWLER_WORKER_ID_MAX_LENGTH = 128
RUNTIME_STOP_GRACE_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class RuntimeWorkerStartupSettings:
    match_analysis_job_worker_count: int
    match_analysis_job_interval_seconds: int


@dataclass(slots=True)
class RuntimeLoopHealth:
    last_started_at: datetime | None = None
    last_succeeded_at: datetime | None = None
    last_failed_at: datetime | None = None
    consecutive_failures: int = 0
    error: str | None = None

    def to_payload(self) -> dict[str, object | None]:
        return {
            "last_started_at": _isoformat_or_none(self.last_started_at),
            "last_succeeded_at": _isoformat_or_none(self.last_succeeded_at),
            "last_failed_at": _isoformat_or_none(self.last_failed_at),
            "consecutive_failures": self.consecutive_failures,
            "error": self.error,
        }


def _positive_int(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return max(1, fallback)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, fallback)


async def _load_worker_runtime_settings(session: AsyncSession) -> AppSetting | None:
    return await session.scalar(select(AppSetting).where(AppSetting.id == 1))


class RuntimeManager:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        runtime_id: str | None = None,
        worker_generation: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._runtime_id = runtime_id or "development"
        self._worker_generation = (
            worker_generation
            or f"local-{os.getpid()}-{uuid.uuid4().hex}"
        )
        self._tasks: list[asyncio.Task[None]] = []
        self._stopped = asyncio.Event()
        self._loop_health: dict[str, RuntimeLoopHealth] = {}
        self._batch_draft_coordinator = BatchDraftGenerationCoordinator()
        self._batch_draft_scheduler = BatchDraftScheduler(
            session_factory,
            coordinator=self._batch_draft_coordinator,
            on_iteration_started=lambda: self._mark_loop_started("batch-drafts"),
            on_iteration_succeeded=lambda: self._mark_loop_succeeded(
                "batch-drafts"
            ),
            on_iteration_failed=lambda exc: self._mark_loop_failed(
                "batch-drafts",
                exc,
            ),
        )

    async def _resolve_worker_startup_settings(
        self,
        settings: object,
    ) -> RuntimeWorkerStartupSettings:
        fallback = RuntimeWorkerStartupSettings(
            match_analysis_job_worker_count=_positive_int(
                getattr(settings, "match_analysis_job_worker_count", 1),
                1,
            ),
            match_analysis_job_interval_seconds=_positive_int(
                getattr(settings, "match_analysis_job_interval_seconds", 10),
                10,
            ),
        )

        try:
            async with self._session_factory() as session:
                runtime_settings = await _load_worker_runtime_settings(session)
        except Exception:
            logger.exception("读取运行时 worker 设置失败，已回退到环境配置")
            return fallback

        if runtime_settings is None:
            return fallback

        try:
            return RuntimeWorkerStartupSettings(
                match_analysis_job_worker_count=_positive_int(
                    runtime_settings.match_analysis_job_worker_count,
                    fallback.match_analysis_job_worker_count,
                ),
                match_analysis_job_interval_seconds=_positive_int(
                    runtime_settings.match_analysis_job_interval_seconds,
                    fallback.match_analysis_job_interval_seconds,
                ),
            )
        except Exception:
            logger.exception("运行时 worker 设置字段不完整，已回退到环境配置")
            return fallback

    async def start(self) -> None:
        if self._tasks:
            return
        self._stopped.clear()
        settings = get_settings()
        worker_settings = await self._resolve_worker_startup_settings(settings)
        loop_names = [
            "dispatcher",
            "imap-incremental-poller",
            "imap-history-poller",
            "batch-drafts",
            *[
                f"match-analysis-worker-{index}"
                for index in range(1, worker_settings.match_analysis_job_worker_count + 1)
            ],
            *[
                f"crawler-worker-{index}"
                for index in range(1, CRAWLER_WORK_ITEM_WORKER_COUNT + 1)
            ],
        ]
        self._loop_health = {name: RuntimeLoopHealth() for name in loop_names}
        crawler_tasks = [
            asyncio.create_task(
                self._loop(
                    f"crawler-worker-{index}",
                    5,
                    partial(
                        run_crawler_v2_once,
                        worker_id=self._crawler_worker_id(index),
                        propagate_work_failures=True,
                    ),
                    processed_jitter_seconds=(2, 5),
                ),
            )
            for index in range(1, CRAWLER_WORK_ITEM_WORKER_COUNT + 1)
        ]
        match_analysis_tasks = [
            asyncio.create_task(
                self._loop(
                    f"match-analysis-worker-{index}",
                    worker_settings.match_analysis_job_interval_seconds,
                    _run_match_analysis_worker_once,
                ),
            )
            for index in range(1, worker_settings.match_analysis_job_worker_count + 1)
        ]

        dispatcher_startup_recovered = False

        async def run_dispatcher_once(session_factory: async_sessionmaker[AsyncSession]) -> int:
            nonlocal dispatcher_startup_recovered
            if not dispatcher_startup_recovered:
                await mark_overdue_manual_schedules_missed(session_factory)
                dispatcher_startup_recovered = True
            return await dispatch_due_tasks_once(
                session_factory,
                count_identity_window_deferred=True,
            )

        self._tasks = [
            asyncio.create_task(
                self._loop(
                    "dispatcher",
                    settings.dispatcher_interval_seconds,
                    run_dispatcher_once,
                    processed_jitter_seconds=(
                        DEFAULT_SEND_INTERVAL_MAX_SECONDS,
                        DEFAULT_SEND_INTERVAL_MAX_SECONDS,
                    ),
                ),
            ),
            asyncio.create_task(
                self._loop(
                    "imap-incremental-poller",
                    settings.imap_poll_interval_seconds,
                    poll_for_replies_once,
                    wait_after_processed=True,
                ),
            ),
            asyncio.create_task(
                self._loop(
                    "imap-history-poller",
                    settings.imap_poll_interval_seconds,
                    poll_imap_history_once,
                    wait_after_processed=True,
                ),
            ),
            asyncio.create_task(
                self._batch_draft_scheduler.run_forever(self._stopped),
            ),
            *match_analysis_tasks,
            *crawler_tasks,
        ]

    def _crawler_worker_id(self, index: int) -> str:
        worker_id = (
            f"crawler-worker-{index}:"
            f"{self._runtime_id}:{self._worker_generation}"
        )
        if len(worker_id) > CRAWLER_WORKER_ID_MAX_LENGTH:
            raise RuntimeError(
                "Runtime identity is too long for the persisted crawler worker id"
            )
        return worker_id

    async def stop(
        self,
        *,
        grace_seconds: float = RUNTIME_STOP_GRACE_SECONDS,
    ) -> None:
        self._stopped.set()
        if not self._tasks:
            return
        _, pending = await asyncio.wait(
            self._tasks,
            timeout=max(0.0, grace_seconds),
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def cancel_batch_draft_generation(self, batch_task_id: int) -> None:
        self._batch_draft_coordinator.cancel_batch(batch_task_id)

    def get_health_snapshot(self) -> dict[str, dict[str, object | None]]:
        return {
            name: health.to_payload()
            for name, health in sorted(self._loop_health.items())
        }

    def is_degraded(self) -> bool:
        return any(
            health.consecutive_failures > 0
            for health in self._loop_health.values()
        )

    def _health_for(self, worker_name: str) -> RuntimeLoopHealth:
        return self._loop_health.setdefault(worker_name, RuntimeLoopHealth())

    def _mark_loop_started(self, worker_name: str) -> None:
        self._health_for(worker_name).last_started_at = datetime.now(UTC)

    def _mark_loop_succeeded(self, worker_name: str) -> None:
        health = self._health_for(worker_name)
        health.last_succeeded_at = datetime.now(UTC)
        health.consecutive_failures = 0
        health.error = None

    def _mark_loop_failed(self, worker_name: str, exc: Exception) -> None:
        health = self._health_for(worker_name)
        health.last_failed_at = datetime.now(UTC)
        health.consecutive_failures += 1
        health.error = sanitize_user_visible_error(exc)[:1000]

    async def _loop(
        self,
        worker_name: str,
        interval_seconds: int,
        worker: Callable[[async_sessionmaker[AsyncSession]], Awaitable[int]],
        *,
        processed_jitter_seconds: tuple[float, float] | None = None,
        wait_after_processed: bool = False,
    ) -> None:
        while not self._stopped.is_set():
            processed = 0
            self._mark_loop_started(worker_name)
            try:
                processed = await worker(self._session_factory)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._mark_loop_failed(worker_name, exc)
                logger.exception("%s 执行失败", worker_name)
                write_backend_worker_error_log(worker_name=worker_name, exc=exc)
            else:
                self._mark_loop_succeeded(worker_name)

            if processed > 0:
                if wait_after_processed:
                    try:
                        await asyncio.wait_for(self._stopped.wait(), timeout=interval_seconds)
                    except TimeoutError:
                        continue
                    continue
                if processed_jitter_seconds is None:
                    continue
                min_seconds, max_seconds = processed_jitter_seconds
                try:
                    await asyncio.wait_for(self._stopped.wait(), timeout=random.uniform(min_seconds, max_seconds))
                except TimeoutError:
                    continue
                continue

            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue



async def _run_match_analysis_worker_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session:
        runtime_settings = await get_runtime_settings(session)

    return await run_queued_match_analysis_jobs_once(
        session_factory,
        item_concurrency=runtime_settings.match_analysis_job_item_concurrency,
    )


def _isoformat_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
