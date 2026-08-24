from __future__ import annotations

import asyncio
import logging
import uuid

from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import AsyncIterator

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.time import utc_now
from app.models import (
    BatchTask,
    BatchTaskStatus,
    EmailTask,
    EmailTaskCancellationReason,
    EmailTaskSource,
    EmailTaskStatus,
    IdentityProfile,
    LLMProfile,
    Professor,
)
from app.modules.campaigns.public import (
    batch_item_uses_llm_generation_column,
    normalize_batch_item_generation_mode,
)
from app.modules.campaigns.public import (
    build_missing_research_fallback_for_task,
)
from app.modules.workspace.public import (
    WORKSPACE_DRAFT_REWRITE_TIMEOUT_MESSAGE,
    WORKSPACE_DRAFT_REWRITE_TIMEOUT_SECONDS,
    WORKSPACE_DRAFT_REWRITE_INTERRUPTED_MESSAGE,
    WORKSPACE_DRAFT_REWRITE_TIMEOUT,
    generate_task_draft,
    restore_workspace_rewrite_source,
)
from app.modules.system.public import get_runtime_settings


logger = logging.getLogger(__name__)
BATCH_DRAFT_LEASE = timedelta(seconds=90)
BATCH_DRAFT_IDLE_POLL_SECONDS = 1.0
BATCH_DRAFT_CANCEL_GRACE_SECONDS = 1.0
_BATCH_DRAFT_CLAIM_LOCK = asyncio.Lock()
_DETACHED_GENERATION_TASKS: set[asyncio.Task[object]] = set()


@dataclass(frozen=True, slots=True)
class BatchDraftClaim:
    task_id: int
    batch_task_id: int
    claim_id: str


class BatchDraftGenerationCoordinator:
    def __init__(self) -> None:
        self._tasks_by_batch_id: dict[int, set[asyncio.Task[object]]] = {}
        self._warming_batch_ids: set[int] = set()
        self._warmed_batch_ids: set[int] = set()

    @asynccontextmanager
    async def track(
        self, batch_task_id: int, task: asyncio.Task[object]
    ) -> AsyncIterator[None]:
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

    def cancel_all(self) -> None:
        for batch_task_id in list(self._tasks_by_batch_id):
            self.cancel_batch(batch_task_id)

    @property
    def warming_batch_ids(self) -> set[int]:
        return set(self._warming_batch_ids)

    def mark_claim_started(self, batch_task_id: int) -> None:
        if batch_task_id not in self._warmed_batch_ids:
            self._warming_batch_ids.add(batch_task_id)

    def mark_claim_finished(self, batch_task_id: int) -> None:
        if batch_task_id in self._warming_batch_ids:
            self._warming_batch_ids.discard(batch_task_id)
            self._warmed_batch_ids.add(batch_task_id)


class BatchDraftScheduler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        coordinator: BatchDraftGenerationCoordinator,
    ) -> None:
        self._session_factory = session_factory
        self._coordinator = coordinator

    async def run_forever(self, stopped: asyncio.Event) -> None:
        await self._run(
            stopped=stopped, stop_when_idle=False, concurrency_override=None
        )

    async def run_until_idle(self, *, concurrency: int) -> int:
        return await self._run(
            stopped=asyncio.Event(),
            stop_when_idle=True,
            concurrency_override=max(concurrency, 1),
        )

    async def _run(
        self,
        *,
        stopped: asyncio.Event,
        stop_when_idle: bool,
        concurrency_override: int | None,
    ) -> int:
        in_flight: dict[asyncio.Task[None], BatchDraftClaim] = {}
        claimed_count = 0
        try:
            while not stopped.is_set():
                try:
                    await recover_stale_generating_drafts(self._session_factory)
                    concurrency = concurrency_override
                    if concurrency is None:
                        async with self._session_factory() as session:
                            settings = await get_runtime_settings(session)
                        concurrency = max(
                            settings.batch_draft_generation_concurrency, 1
                        )

                    await materialize_missing_research_template_fallbacks(
                        self._session_factory,
                        limit=max(concurrency, 1) * 4,
                    )
                    while len(in_flight) < concurrency:
                        claim = await _claim_next_queued_llm_draft(
                            self._session_factory,
                            excluded_batch_ids=self._coordinator.warming_batch_ids,
                        )
                        if claim is None:
                            break
                        self._coordinator.mark_claim_started(claim.batch_task_id)
                        worker = asyncio.create_task(
                            _run_claimed_batch_draft(
                                self._session_factory,
                                claim,
                                coordinator=self._coordinator,
                            )
                        )
                        in_flight[worker] = claim
                        claimed_count += 1
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("批量草稿调度循环执行失败")
                    if stop_when_idle:
                        raise
                    await _wait_or_stop(stopped, BATCH_DRAFT_IDLE_POLL_SECONDS)
                    continue

                if not in_flight:
                    if stop_when_idle:
                        return claimed_count
                    await _wait_or_stop(stopped, BATCH_DRAFT_IDLE_POLL_SECONDS)
                    continue

                done, _ = await asyncio.wait(
                    set(in_flight),
                    timeout=BATCH_DRAFT_IDLE_POLL_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for worker in done:
                    claim = in_flight.pop(worker)
                    self._coordinator.mark_claim_finished(claim.batch_task_id)
                    if worker.cancelled():
                        continue
                    error = worker.exception()
                    if error is not None:
                        logger.error(
                            "批量草稿 worker 异常：task_id=%s",
                            claim.task_id,
                            exc_info=(type(error), error, error.__traceback__),
                        )
        finally:
            for worker in in_flight:
                worker.cancel()
            self._coordinator.cancel_all()
            if in_flight:
                await asyncio.gather(*in_flight, return_exceptions=True)
        return claimed_count


async def _wait_or_stop(stopped: asyncio.Event, timeout: float) -> None:
    try:
        await asyncio.wait_for(stopped.wait(), timeout=timeout)
    except TimeoutError:
        pass


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
                    or_(
                        (
                            (EmailTask.status == EmailTaskStatus.GENERATING_DRAFT.value)
                            & EmailTask.draft_claim_id.is_not(None)
                            & or_(
                                EmailTask.draft_lease_expires_at.is_(None),
                                EmailTask.draft_lease_expires_at <= resolved_now,
                            )
                        ),
                        (
                            (EmailTask.status == EmailTaskStatus.GENERATING_DRAFT.value)
                            & EmailTask.draft_claim_id.is_(None)
                            & EmailTask.draft_generation_started_at.is_(None)
                            & (EmailTask.updated_at < cutoff)
                        ),
                        (
                            (EmailTask.status != EmailTaskStatus.GENERATING_DRAFT.value)
                            & EmailTask.draft_claim_id.is_not(None)
                        ),
                    ),
                ),
            ),
        )
        recovered = 0
        for task in tasks:
            recovered += await _recover_stale_batch_draft_task(
                session,
                task,
                now=resolved_now,
                cutoff=cutoff,
            )
        await session.commit()
        return recovered


async def _recover_stale_batch_draft_task(
    session: AsyncSession,
    task: EmailTask,
    *,
    now: datetime,
    cutoff: datetime,
) -> int:
    guards: list[object] = [EmailTask.id == task.id]
    values: dict[str, object | None] = {
        "draft_generation_started_at": None,
        "draft_claim_id": None,
        "draft_claimed_at": None,
        "draft_lease_expires_at": None,
        "updated_at": now,
    }

    if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
        guards.append(EmailTask.status == EmailTaskStatus.GENERATING_DRAFT.value)
        if task.draft_claim_id is not None:
            guards.extend(
                [
                    EmailTask.draft_claim_id == task.draft_claim_id,
                    or_(
                        EmailTask.draft_lease_expires_at.is_(None),
                        EmailTask.draft_lease_expires_at <= now,
                    ),
                ]
            )
        else:
            guards.extend(
                [
                    EmailTask.draft_claim_id.is_(None),
                    EmailTask.draft_generation_started_at.is_(None),
                    EmailTask.updated_at < cutoff,
                ]
            )

        if task.batch_task and task.batch_task.status == BatchTaskStatus.STOPPED.value:
            values.update(
                status=EmailTaskStatus.CANCELED.value,
                cancellation_reason=EmailTaskCancellationReason.BATCH_STOPPED.value,
            )
        elif (
            task.batch_task and task.batch_task.status == BatchTaskStatus.EXPIRED.value
        ):
            values.update(
                status=EmailTaskStatus.CANCELED.value,
                cancellation_reason=EmailTaskCancellationReason.SCHEDULE_EXPIRED.value,
            )
        else:
            values.update(
                status=(
                    task.draft_generation_previous_status
                    or EmailTaskStatus.DISCOVERED.value
                ),
                cancellation_reason=None,
            )
        values["draft_generation_previous_status"] = None
    else:
        if task.draft_claim_id is None:
            return 0
        guards.extend(
            [
                EmailTask.status == task.status,
                EmailTask.draft_claim_id == task.draft_claim_id,
            ]
        )

    result = await session.execute(
        update(EmailTask)
        .where(*guards)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount == 1)


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
            EmailTask.draft_claim_id.is_(None),
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
    scheduler = BatchDraftScheduler(session_factory, coordinator=coordinator)
    return await scheduler.run_until_idle(concurrency=concurrency)


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
                    Professor.archived_at.is_(None),
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
                    EmailTask.professor_id.in_(
                        select(Professor.id).where(Professor.archived_at.is_(None))
                    ),
                    EmailTask.batch_task_id.in_(
                        select(BatchTask.id).where(
                            BatchTask.status == BatchTaskStatus.RUNNING.value,
                            BatchTask.deleted_at.is_(None),
                            BatchTask.llm_profile_id.in_(
                                select(LLMProfile.id).where(
                                    LLMProfile.deleted_at.is_(None)
                                )
                            ),
                            BatchTask.identity_id.in_(
                                select(IdentityProfile.id).where(
                                    IdentityProfile.deleted_at.is_(None)
                                )
                            ),
                        )
                    ),
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
                    draft_claim_id=None,
                    draft_claimed_at=None,
                    draft_lease_expires_at=None,
                    last_error=None,
                    updated_at=now,
                ),
            )
            if result.rowcount == 1:
                converted += 1
        await session.commit()
        return converted


async def _claim_next_queued_llm_draft(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    excluded_batch_ids: set[int],
) -> BatchDraftClaim | None:
    async with _BATCH_DRAFT_CLAIM_LOCK:
        async with session_factory() as session:
            task_eligibility = (
                EmailTask.source == EmailTaskSource.BATCH.value,
                EmailTask.status.in_(
                    [EmailTaskStatus.DISCOVERED.value, EmailTaskStatus.MATCHED.value]
                ),
                EmailTask.batch_send_canceled_at.is_(None),
                batch_item_uses_llm_generation_column(
                    EmailTask.outreach_generation_mode
                ),
                EmailTask.primary_material_id.is_not(None),
                Professor.archived_at.is_(None),
                func.trim(Professor.research_direction) != "",
            )
            batch_eligibility = (
                *task_eligibility,
                BatchTask.llm_profile_id.in_(
                    select(LLMProfile.id).where(LLMProfile.deleted_at.is_(None))
                ),
                BatchTask.identity_id.in_(
                    select(IdentityProfile.id).where(
                        IdentityProfile.deleted_at.is_(None)
                    )
                ),
                BatchTask.status == BatchTaskStatus.RUNNING.value,
                BatchTask.deleted_at.is_(None),
            )
            batch_statement = (
                select(BatchTask)
                .join(EmailTask, EmailTask.batch_task_id == BatchTask.id)
                .join(Professor, EmailTask.professor_id == Professor.id)
                .where(*batch_eligibility)
                .order_by(
                    case((BatchTask.draft_last_dispatched_at.is_(None), 0), else_=1),
                    BatchTask.draft_last_dispatched_at.asc(),
                    BatchTask.created_at.asc(),
                    BatchTask.id.asc(),
                )
                .limit(1)
            )
            if excluded_batch_ids:
                batch_statement = batch_statement.where(
                    BatchTask.id.not_in(excluded_batch_ids)
                )
            batch_task = await session.scalar(batch_statement)
            if batch_task is None:
                return None

            task = await session.scalar(
                select(EmailTask)
                .join(Professor, EmailTask.professor_id == Professor.id)
                .where(
                    EmailTask.batch_task_id == batch_task.id,
                    *task_eligibility,
                )
                .order_by(EmailTask.created_at.asc(), EmailTask.id.asc())
                .limit(1)
            )
            if task is None:
                return None

            now = utc_now()
            claim_id = str(uuid.uuid4())
            claim_result = await session.execute(
                update(EmailTask)
                .where(
                    EmailTask.id == task.id,
                    EmailTask.status == task.status,
                    EmailTask.batch_send_canceled_at.is_(None),
                    EmailTask.draft_claim_id.is_(None),
                    EmailTask.professor_id.in_(
                        select(Professor.id).where(Professor.archived_at.is_(None))
                    ),
                    EmailTask.batch_task_id.in_(
                        select(BatchTask.id).where(
                            BatchTask.status == BatchTaskStatus.RUNNING.value,
                            BatchTask.deleted_at.is_(None),
                            BatchTask.llm_profile_id.in_(
                                select(LLMProfile.id).where(
                                    LLMProfile.deleted_at.is_(None)
                                )
                            ),
                            BatchTask.identity_id.in_(
                                select(IdentityProfile.id).where(
                                    IdentityProfile.deleted_at.is_(None)
                                )
                            ),
                        )
                    ),
                )
                .values(
                    outreach_generation_mode=normalize_batch_item_generation_mode(task),
                    draft_generation_previous_status=task.status,
                    draft_generation_started_at=now,
                    draft_claim_id=claim_id,
                    draft_claimed_at=now,
                    draft_lease_expires_at=now + BATCH_DRAFT_LEASE,
                    status=EmailTaskStatus.GENERATING_DRAFT.value,
                    updated_at=now,
                ),
            )
            if claim_result.rowcount != 1:
                await session.rollback()
                return None
            batch_task.draft_last_dispatched_at = now
            await session.commit()
            return BatchDraftClaim(
                task_id=task.id,
                batch_task_id=batch_task.id,
                claim_id=claim_id,
            )


async def _run_claimed_batch_draft(
    session_factory: async_sessionmaker[AsyncSession],
    claim: BatchDraftClaim,
    *,
    coordinator: BatchDraftGenerationCoordinator,
) -> None:
    generation_task = asyncio.create_task(
        generate_task_draft(
            session_factory,
            claim.task_id,
            force=True,
            automatic_batch=True,
            require_running_batch=True,
            draft_claim_id=claim.claim_id,
        )
    )
    heartbeat_task = asyncio.create_task(
        _renew_batch_draft_claim_until_lost(session_factory, claim)
    )
    async with coordinator.track(claim.batch_task_id, generation_task):
        try:
            done, _ = await asyncio.wait(
                {generation_task, heartbeat_task},
                timeout=WORKSPACE_DRAFT_REWRITE_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                await _cancel_generation_task_with_grace(generation_task, claim)
                await _mark_batch_draft_claim_failed(
                    session_factory,
                    claim,
                    WORKSPACE_DRAFT_REWRITE_TIMEOUT_MESSAGE,
                )
                return
            if heartbeat_task in done:
                claim_is_current = heartbeat_task.result()
                if not claim_is_current:
                    await _cancel_generation_task_with_grace(generation_task, claim)
                    await _release_batch_draft_claim(session_factory, claim)
                    return
            if generation_task in done:
                try:
                    await generation_task
                except asyncio.CancelledError:
                    await _release_batch_draft_claim(session_factory, claim)
                except Exception as exc:
                    await _mark_batch_draft_claim_failed(
                        session_factory,
                        claim,
                        str(exc) or "AI 改写失败，请稍后重试",
                    )
                    logger.exception(
                        "批量草稿生成异常：task_id=%s",
                        claim.task_id,
                    )
        except asyncio.CancelledError:
            await _cancel_generation_task_with_grace(generation_task, claim)
            await _release_batch_draft_claim(session_factory, claim)
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task


async def _cancel_generation_task_with_grace(
    generation_task: asyncio.Task[object],
    claim: BatchDraftClaim,
) -> None:
    if generation_task.done():
        await asyncio.gather(generation_task, return_exceptions=True)
        return
    generation_task.cancel()
    done, _ = await asyncio.wait(
        {generation_task},
        timeout=BATCH_DRAFT_CANCEL_GRACE_SECONDS,
    )
    if done:
        await asyncio.gather(generation_task, return_exceptions=True)
        return

    logger.error(
        "批量草稿生成未在取消宽限期内退出，已隔离迟到结果：task_id=%s",
        claim.task_id,
    )
    _DETACHED_GENERATION_TASKS.add(generation_task)
    generation_task.add_done_callback(_consume_detached_generation_task)


def _consume_detached_generation_task(task: asyncio.Task[object]) -> None:
    _DETACHED_GENERATION_TASKS.discard(task)
    with suppress(asyncio.CancelledError):
        error = task.exception()
        if error is not None:
            logger.error(
                "已隔离的批量草稿生成任务异常退出",
                exc_info=(type(error), error, error.__traceback__),
            )


async def _renew_batch_draft_claim_until_lost(
    session_factory: async_sessionmaker[AsyncSession],
    claim: BatchDraftClaim,
) -> bool:
    interval_seconds = max(1.0, BATCH_DRAFT_LEASE.total_seconds() / 3)
    while True:
        await asyncio.sleep(interval_seconds)
        now = utc_now()
        try:
            async with session_factory() as session:
                result = await session.execute(
                    update(EmailTask)
                    .where(
                        EmailTask.id == claim.task_id,
                        EmailTask.status == EmailTaskStatus.GENERATING_DRAFT.value,
                        EmailTask.draft_claim_id == claim.claim_id,
                    )
                    .values(draft_lease_expires_at=now + BATCH_DRAFT_LEASE)
                )
                await session.commit()
                if result.rowcount != 1:
                    return False
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "批量草稿租约续期失败：task_id=%s",
                claim.task_id,
            )
            return False


async def _mark_batch_draft_claim_failed(
    session_factory: async_sessionmaker[AsyncSession],
    claim: BatchDraftClaim,
    message: str,
) -> None:
    async with session_factory() as session:
        now = utc_now()
        await session.execute(
            update(EmailTask)
            .where(
                EmailTask.id == claim.task_id,
                EmailTask.draft_claim_id == claim.claim_id,
            )
            .values(
                status=EmailTaskStatus.DRAFT_FAILED.value,
                draft_generation_previous_status=None,
                draft_generation_started_at=None,
                draft_claim_id=None,
                draft_claimed_at=None,
                draft_lease_expires_at=None,
                last_error=message,
                updated_at=now,
            )
        )
        await session.commit()


async def _release_batch_draft_claim(
    session_factory: async_sessionmaker[AsyncSession],
    claim: BatchDraftClaim,
) -> None:
    async with session_factory() as session:
        task = await session.scalar(
            select(EmailTask)
            .options(selectinload(EmailTask.batch_task))
            .where(
                EmailTask.id == claim.task_id,
                EmailTask.draft_claim_id == claim.claim_id,
            )
        )
        if task is None:
            return
        if task.status == EmailTaskStatus.GENERATING_DRAFT.value:
            if (
                task.batch_task
                and task.batch_task.status == BatchTaskStatus.PAUSED.value
            ):
                task.status = (
                    task.draft_generation_previous_status
                    or EmailTaskStatus.DISCOVERED.value
                )
            elif (
                task.batch_task
                and task.batch_task.status == BatchTaskStatus.EXPIRED.value
            ):
                task.status = EmailTaskStatus.CANCELED.value
                task.cancellation_reason = (
                    EmailTaskCancellationReason.SCHEDULE_EXPIRED.value
                )
            elif (
                task.batch_task
                and task.batch_task.status == BatchTaskStatus.STOPPED.value
            ):
                task.status = EmailTaskStatus.CANCELED.value
                task.cancellation_reason = (
                    EmailTaskCancellationReason.BATCH_STOPPED.value
                )
            else:
                task.status = (
                    task.draft_generation_previous_status
                    or EmailTaskStatus.DISCOVERED.value
                )
        task.draft_generation_previous_status = None
        _clear_batch_draft_claim(task)
        task.updated_at = utc_now()
        await session.commit()


def _clear_batch_draft_claim(task: EmailTask) -> None:
    task.draft_generation_started_at = None
    task.draft_claim_id = None
    task.draft_claimed_at = None
    task.draft_lease_expires_at = None
