from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.backend_error_logging import write_backend_worker_error_log
from app.core.config import get_settings
from app.models import AppSetting
from app.services.batch_draft_generation_runtime import (
    BatchDraftGenerationCoordinator,
    run_queued_batch_drafts_once,
)
from app.services.crawler_v2_scheduler import run_crawler_v2_once
from app.services.match_analysis_job_runtime import run_queued_match_analysis_jobs_once
from app.services.runtime_settings import get_runtime_settings
from app.services.task_runtime import (
    dispatch_due_tasks_once,
    poll_for_replies_once,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RuntimeWorkerStartupSettings:
    crawler_worker_count: int
    match_analysis_job_worker_count: int
    match_analysis_job_interval_seconds: int


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
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._tasks: list[asyncio.Task[None]] = []
        self._stopped = asyncio.Event()
        self._batch_draft_coordinator = BatchDraftGenerationCoordinator()

    async def _resolve_worker_startup_settings(
        self,
        settings: object,
    ) -> RuntimeWorkerStartupSettings:
        fallback = RuntimeWorkerStartupSettings(
            crawler_worker_count=_positive_int(
                getattr(settings, "crawler_worker_count", 8),
                8,
            ),
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
                crawler_worker_count=_positive_int(
                    runtime_settings.crawler_worker_count,
                    fallback.crawler_worker_count,
                ),
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
        crawler_tasks = [
            asyncio.create_task(
                self._loop(
                    f"crawler-worker-{index}",
                    10,
                    run_crawler_v2_once,
                    processed_jitter_seconds=(2, 10),
                ),
            )
            for index in range(1, worker_settings.crawler_worker_count + 1)
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

        async def run_batch_draft_worker(session_factory: async_sessionmaker[AsyncSession]) -> int:
            async with session_factory() as session:
                runtime_settings = await get_runtime_settings(session)
            return await run_queued_batch_drafts_once(
                session_factory,
                concurrency=runtime_settings.batch_draft_generation_concurrency,
                coordinator=self._batch_draft_coordinator,
            )

        self._tasks = [
            asyncio.create_task(
                self._loop(
                    "dispatcher",
                    settings.dispatcher_interval_seconds,
                    dispatch_due_tasks_once,
                ),
            ),
            asyncio.create_task(
                self._loop(
                    "imap-poller",
                    settings.imap_poll_interval_seconds,
                    poll_for_replies_once,
                    wait_after_processed=True,
                ),
            ),
            asyncio.create_task(
                self._loop(
                    "batch-draft-worker",
                    settings.dispatcher_interval_seconds,
                    run_batch_draft_worker,
                ),
            ),
            *match_analysis_tasks,
            *crawler_tasks,
        ]

    async def stop(self) -> None:
        self._stopped.set()
        if not self._tasks:
            return
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def cancel_batch_draft_generation(self, batch_task_id: int) -> None:
        self._batch_draft_coordinator.cancel_batch(batch_task_id)

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
            try:
                processed = await worker(self._session_factory)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("%s 执行失败", worker_name)
                write_backend_worker_error_log(worker_name=worker_name, exc=exc)

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
