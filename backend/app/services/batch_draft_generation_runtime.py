from __future__ import annotations

import asyncio

from app.core.time import utc_now

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import AsyncIterator

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.models import BatchTask, BatchTaskStatus, EmailTask, EmailTaskCancellationReason, EmailTaskSource, EmailTaskStatus, Professor
from app.modules.campaigns.public import (
    batch_item_uses_llm_generation_column,
    normalize_batch_item_generation_mode,
)
from app.modules.campaigns.public import (
    build_missing_research_fallback_for_task,
)
from app.services.task_runtime import (
    WORKSPACE_DRAFT_REWRITE_INTERRUPTED_MESSAGE,
    WORKSPACE_DRAFT_REWRITE_TIMEOUT,
    generate_task_draft,
    restore_workspace_rewrite_source,
)


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
    resolved_now = now or utc_now()
    cutoff = resolved_now - stale_after
    async with session_factory() as session:
        tasks = list(
            await session.scalars(
                select(EmailTask)
                .options(selectinload(EmailTask.batch_task))
                .where(
                    EmailTask.status == EmailTaskStatus.GENERATING_DRAFT.value,
                    EmailTask.draft_generation_started_at.is_(None),
                    EmailTask.updated_at < cutoff,
                ),
            ),
        )
        for task in tasks:
            if task.batch_task and task.batch_task.status == BatchTaskStatus.STOPPED.value:
                task.status = EmailTaskStatus.CANCELED.value
                task.cancellation_reason = EmailTaskCancellationReason.BATCH_STOPPED.value
            elif task.batch_task and task.batch_task.status == BatchTaskStatus.EXPIRED.value:
                task.status = EmailTaskStatus.CANCELED.value
                task.cancellation_reason = EmailTaskCancellationReason.SCHEDULE_EXPIRED.value
            else:
                task.status = task.draft_generation_previous_status or EmailTaskStatus.DISCOVERED.value
                task.cancellation_reason = None
            task.draft_generation_previous_status = None
            task.updated_at = resolved_now
        await session.commit()
        return len(tasks)


async def recover_stale_workspace_draft_rewrites(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> int:
    resolved_now = now or utc_now()
    cutoff = resolved_now - WORKSPACE_DRAFT_REWRITE_TIMEOUT
    return await _recover_workspace_draft_rewrites(
        session_factory,
        now=resolved_now,
        cutoff=cutoff,
    )


async def recover_interrupted_workspace_draft_rewrites(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> int:
    return await _recover_workspace_draft_rewrites(
        session_factory,
        now=now or utc_now(),
        cutoff=None,
    )


async def _recover_workspace_draft_rewrites(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    cutoff: datetime | None,
) -> int:
    async with session_factory() as session:
        conditions = [
            EmailTask.status == EmailTaskStatus.GENERATING_DRAFT.value,
            EmailTask.draft_generation_started_at.is_not(None),
        ]
        if cutoff is not None:
            conditions.append(EmailTask.draft_generation_started_at <= cutoff)
        tasks = list(
            await session.scalars(
                select(EmailTask).where(*conditions),
            ),
        )
        for task in tasks:
            restore_workspace_rewrite_source(
                task,
                WORKSPACE_DRAFT_REWRITE_INTERRUPTED_MESSAGE,
                now=now,
            )
        await session.commit()
        return len(tasks)


async def run_queued_batch_drafts_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    concurrency: int,
    coordinator: BatchDraftGenerationCoordinator,
) -> int:
    await recover_stale_generating_drafts(session_factory)
    await materialize_missing_research_template_fallbacks(
        session_factory,
        limit=max(concurrency, 1) * 4,
    )
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

    claimed_by_batch: dict[int, list[int]] = {}
    for task_id, batch_task_id in claimed:
        claimed_by_batch.setdefault(batch_task_id, []).append(task_id)

    async def run_batch(task_ids: list[int], batch_task_id: int) -> None:
        await run_claimed(task_ids[0], batch_task_id)
        await asyncio.gather(
            *(run_claimed(task_id, batch_task_id) for task_id in task_ids[1:]),
        )

    await asyncio.gather(
        *(
            run_batch(task_ids, batch_task_id)
            for batch_task_id, task_ids in claimed_by_batch.items()
        ),
    )
    return len(claimed)


async def materialize_missing_research_template_fallbacks(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    limit: int,
) -> int:
    if limit <= 0:
        return 0

    async with session_factory() as session:
        candidates = list(
            await session.scalars(
                select(EmailTask)
                .join(BatchTask, EmailTask.batch_task_id == BatchTask.id)
                .join(Professor, EmailTask.professor_id == Professor.id)
                .options(
                    selectinload(EmailTask.batch_task),
                    selectinload(EmailTask.identity),
                    selectinload(EmailTask.professor),
                )
                .where(
                    EmailTask.source == EmailTaskSource.BATCH.value,
                    EmailTask.status.in_(
                        [
                            EmailTaskStatus.DISCOVERED.value,
                            EmailTaskStatus.MATCHED.value,
                        ],
                    ),
                    EmailTask.batch_send_canceled_at.is_(None),
                    batch_item_uses_llm_generation_column(
                        EmailTask.outreach_generation_mode,
                    ),
                    EmailTask.primary_material_id.is_not(None),
                    or_(
                        Professor.research_direction.is_(None),
                        func.trim(Professor.research_direction) == "",
                    ),
                    BatchTask.status == BatchTaskStatus.RUNNING.value,
                )
                .order_by(
                    BatchTask.created_at.asc(),
                    EmailTask.created_at.asc(),
                    EmailTask.id.asc(),
                )
                .limit(limit),
            ),
        )
        converted = 0
        now = utc_now()
        for task in candidates:
            try:
                fallback = build_missing_research_fallback_for_task(task)
            except ValueError:
                continue
            if fallback is None:
                continue
            result = await session.execute(
                update(EmailTask)
                .where(
                    EmailTask.id == task.id,
                    EmailTask.status == task.status,
                    EmailTask.batch_send_canceled_at.is_(None),
                )
                .values(
                    generated_subject=fallback.subject,
                    generated_content_text=fallback.body_text,
                    generated_content_html=fallback.body_html,
                    draft_generation_source=fallback.generation_source,
                    draft_fallback_reason=fallback.fallback_reason,
                    status=EmailTaskStatus.REVIEW_REQUIRED.value,
                    draft_generation_previous_status=None,
                    draft_generation_started_at=None,
                    last_error=None,
                    updated_at=now,
                ),
            )
            if result.rowcount == 1:
                converted += 1
        await session.commit()
        return converted


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
                    EmailTask.batch_send_canceled_at.is_(None),
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
        now = utc_now()
        for task in candidates:
            if task.batch_task_id is None:
                continue
            claim_result = await session.execute(
                update(EmailTask)
                .where(
                    EmailTask.id == task.id,
                    EmailTask.status == task.status,
                    EmailTask.batch_send_canceled_at.is_(None),
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
