from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import AsyncIterator

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.models import BatchTask, BatchTaskStatus, EmailTask, EmailTaskCancellationReason, EmailTaskSource, EmailTaskStatus, Professor
from app.services.batch_task_item_actions import (
    batch_item_uses_llm_generation_column,
    normalize_batch_item_generation_mode,
)
from app.services.task_runtime import generate_task_draft


class BatchDraftGenerationCoordinator:
    def __init__(self) -> None:
        self._tasks_by_batch_id: dict[int, set[asyncio.Task[object]]] = {}

    @asynccontextmanager
    async def track(self, batch_task_id: int, task: asyncio.Task[object]) -> AsyncIterator[None]:
        tasks = self._tasks_by_batch_id.setdefault(batch_task_id, set())
        tasks.add(task)
        try:
            yield
        finally:
            tasks.discard(task)
            if not tasks:
                self._tasks_by_batch_id.pop(batch_task_id, None)

    def cancel_batch(self, batch_task_id: int) -> None:
        for task in list(self._tasks_by_batch_id.get(batch_task_id, set())):
            task.cancel()


async def recover_stale_generating_drafts(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    stale_after: timedelta = timedelta(minutes=30),
    now: datetime | None = None,
) -> int:
    resolved_now = now or datetime.now(UTC)
    cutoff = resolved_now - stale_after
    async with session_factory() as session:
        tasks = list(
            await session.scalars(
                select(EmailTask)
                .options(selectinload(EmailTask.batch_task))
                .where(
                    EmailTask.status == EmailTaskStatus.GENERATING_DRAFT.value,
                    EmailTask.updated_at < cutoff,
                ),
            ),
        )
        for task in tasks:
            if task.batch_task and task.batch_task.status == BatchTaskStatus.STOPPED.value:
                task.status = EmailTaskStatus.CANCELED.value
                task.cancellation_reason = EmailTaskCancellationReason.BATCH_STOPPED.value
            else:
                task.status = task.draft_generation_previous_status or EmailTaskStatus.DISCOVERED.value
            task.draft_generation_previous_status = None
            task.updated_at = resolved_now
        await session.commit()
        return len(tasks)


async def run_queued_batch_drafts_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    concurrency: int,
    coordinator: BatchDraftGenerationCoordinator,
) -> int:
    await recover_stale_generating_drafts(session_factory)
    claimed = await _claim_queued_llm_drafts(session_factory, limit=max(concurrency, 1) * 2)
    semaphore = asyncio.Semaphore(max(concurrency, 1))

    async def run_claimed(task_id: int, batch_task_id: int) -> None:
        async with semaphore:
            generation_task = asyncio.create_task(
                generate_task_draft(
                    session_factory,
                    task_id,
                    force=True,
                    automatic_batch=True,
                    require_running_batch=True,
                ),
            )
            async with coordinator.track(batch_task_id, generation_task):
                await generation_task

    await asyncio.gather(*(run_claimed(task_id, batch_id) for task_id, batch_id in claimed))
    return len(claimed)


async def _claim_queued_llm_drafts(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    limit: int,
) -> list[tuple[int, int]]:
    if limit <= 0:
        return []

    async with session_factory() as session:
        candidates = list(
            await session.scalars(
                select(EmailTask)
                .join(BatchTask, EmailTask.batch_task_id == BatchTask.id)
                .join(Professor, EmailTask.professor_id == Professor.id)
                .where(
                    EmailTask.source == EmailTaskSource.BATCH.value,
                    EmailTask.status.in_([EmailTaskStatus.DISCOVERED.value, EmailTaskStatus.MATCHED.value]),
                    batch_item_uses_llm_generation_column(EmailTask.outreach_generation_mode),
                    EmailTask.primary_material_id.is_not(None),
                    func.trim(Professor.research_direction) != "",
                    BatchTask.status == BatchTaskStatus.RUNNING.value,
                )
                .order_by(BatchTask.created_at.asc(), EmailTask.created_at.asc(), EmailTask.id.asc())
                .limit(limit),
            ),
        )
        claimed: list[tuple[int, int]] = []
        now = datetime.now(UTC)
        for task in candidates:
            if task.batch_task_id is None:
                continue
            claim_result = await session.execute(
                update(EmailTask)
                .where(
                    EmailTask.id == task.id,
                    EmailTask.status == task.status,
                )
                .values(
                    outreach_generation_mode=normalize_batch_item_generation_mode(task),
                    draft_generation_previous_status=task.status,
                    status=EmailTaskStatus.GENERATING_DRAFT.value,
                    updated_at=now,
                ),
            )
            if claim_result.rowcount != 1:
                continue
            claimed.append((task.id, task.batch_task_id))
        await session.commit()
        return claimed
