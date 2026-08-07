from __future__ import annotations

import asyncio

from sqlalchemy import inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import NO_VALUE

from app.core.time import local_now, utc_now
from app.models import (
    EmailTask,
    EmailTaskSource,
    EmailTaskStatus,
    IdentityProfile,
    LLMProfile,
    MatchAnalysisJob,
    MatchAnalysisJobItem,
    MatchAnalysisJobItemStatus,
    MatchAnalysisJobStatus,
    MatchAnalysisRun,
    Professor,
)
from app.modules.campaigns.public import (
    get_default_outreach_template_for_identity,
    resolve_outreach_template_config,
)
from app.services.match_results import resolve_identity_match_scope
from app.services.operation_logs import record_operation_log

from .schemas import MatchAnalysisJobItemRead, MatchAnalysisJobRead
from .task_analysis import (
    MatchAnalysisAlreadyRunningError,
    MatchCalculationCanceledError,
    calculate_task_match,
)

_ACTIVE_MATCH_ANALYSIS_JOB_IDS: set[int] = set()
_MATCH_ANALYSIS_CANCEL_POLL_SECONDS = 0.2


class _MatchAnalysisJobCanceled(RuntimeError):
    pass


async def create_match_analysis_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    llm_profile_id: int,
    professor_ids: list[int],
    name: str | None = None,
) -> MatchAnalysisJob:
    async with session_factory() as session:
        job = await create_match_analysis_job_record(
            session,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            professor_ids=professor_ids,
            name=name,
        )
        await session.commit()
        await session.refresh(job)
        return job


async def create_match_analysis_job_record(
    session: AsyncSession,
    *,
    identity_id: int,
    llm_profile_id: int,
    professor_ids: list[int],
    name: str | None = None,
    match_source_identity_id: int | None = None,
    event_name: str = "match_analysis_job.created",
    actor: str | None = None,
) -> MatchAnalysisJob:
    """Create a job in the caller's transaction without committing it."""

    unique_professor_ids = list(dict.fromkeys(professor_ids))
    if not unique_professor_ids:
        raise ValueError("请选择要分析匹配度的导师")

    identity = await session.get(IdentityProfile, identity_id)
    if identity is None:
        raise ValueError("身份不存在")
    match_scope = await resolve_identity_match_scope(
        session,
        active_identity_id=identity_id,
        match_source_identity_id=match_source_identity_id,
    )
    if match_scope.source_identity.current_primary_material_id is None:
        raise ValueError("请到个人页设置默认材料")

    llm_profile = await session.get(LLMProfile, llm_profile_id)
    if llm_profile is None:
        raise ValueError("LLM 配置不存在")

    professors = list(
        await session.scalars(
            select(Professor)
            .where(
                Professor.id.in_(unique_professor_ids),
                Professor.archived_at.is_(None),
            )
            .order_by(Professor.id.asc()),
        ),
    )
    if not professors:
        raise ValueError("没有可分析的导师")

    has_evidence = {
        professor.id: _has_professor_match_evidence(professor)
        for professor in professors
    }
    if not any(has_evidence.values()):
        raise ValueError("已选导师都缺少研究方向或近期论文，暂不能分析匹配度")

    now = utc_now()
    display_time = local_now()
    job = MatchAnalysisJob(
        name=name or f"批量匹配分析 {display_time:%Y-%m-%d %H:%M}",
        identity_id=identity_id,
        match_source_identity_id=match_scope.source_identity_id,
        llm_profile_id=llm_profile_id,
        status=MatchAnalysisJobStatus.QUEUED.value,
        target_count=0,
        skipped_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    await session.flush()

    queued_count = 0
    skipped_count = 0
    for professor in professors:
        if has_evidence[professor.id]:
            email_task = await _ensure_match_email_task(
                session,
                professor=professor,
                identity=identity,
                llm_profile=llm_profile,
            )
            item = MatchAnalysisJobItem(
                job_id=job.id,
                professor_id=professor.id,
                email_task_id=email_task.id,
                status=MatchAnalysisJobItemStatus.QUEUED.value,
                created_at=now,
                updated_at=now,
            )
            queued_count += 1
        else:
            item = MatchAnalysisJobItem(
                job_id=job.id,
                professor_id=professor.id,
                email_task_id=None,
                status=MatchAnalysisJobItemStatus.SKIPPED.value,
                skip_reason="缺少研究方向或近期论文",
                finished_at=now,
                created_at=now,
                updated_at=now,
            )
            skipped_count += 1
        session.add(item)

    job.target_count = queued_count
    job.skipped_count = skipped_count
    metadata: dict[str, object] = {
        "name": job.name,
        "identity_id": identity_id,
        "match_source_identity_id": match_scope.source_identity_id,
        "llm_profile_id": llm_profile_id,
        "selected_count": len(professors),
        "target_count": queued_count,
        "skipped_count": skipped_count,
    }
    if actor is not None:
        metadata["actor"] = actor
    await record_operation_log(
        session,
        category="match_analysis",
        event_name=event_name,
        entity_type="match_analysis_job",
        entity_id=str(job.id),
        metadata=metadata,
    )
    return job


def serialize_match_analysis_job(job: MatchAnalysisJob) -> MatchAnalysisJobRead:
    return MatchAnalysisJobRead(
        id=job.id,
        name=job.name,
        status=job.status,
        target_count=job.target_count,
        succeeded_count=job.succeeded_count,
        failed_count=job.failed_count,
        skipped_count=job.skipped_count,
        total_prompt_tokens=job.total_prompt_tokens,
        total_completion_tokens=job.total_completion_tokens,
        total_cached_tokens=job.total_cached_tokens,
        total_tokens=job.total_tokens,
        identity_id=job.identity_id,
        match_source_identity_id=job.match_source_identity_id,
        llm_profile_id=job.llm_profile_id,
        cancel_requested_at=job.cancel_requested_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        deleted_at=job.deleted_at,
        last_error=job.last_error,
    )


def serialize_match_analysis_job_item(
    item: MatchAnalysisJobItem,
) -> MatchAnalysisJobItemRead:
    return MatchAnalysisJobItemRead(
        id=item.id,
        job_id=item.job_id,
        professor_id=item.professor_id,
        professor_name=item.professor.name,
        professor_email=item.professor.email,
        professor_title=item.professor.title,
        professor_university=item.professor.university,
        professor_school=item.professor.school,
        email_task_id=item.email_task_id,
        status=item.status,
        match_score=match_analysis_job_item_score(item),
        match_analysis_run_id=item.match_analysis_run_id,
        error_message=item.error_message,
        skip_reason=item.skip_reason,
        prompt_tokens=item.prompt_tokens,
        completion_tokens=item.completion_tokens,
        cached_tokens=item.cached_tokens,
        total_tokens=item.total_tokens,
        started_at=item.started_at,
        finished_at=item.finished_at,
        updated_at=item.updated_at,
    )


def match_analysis_job_item_score(item: MatchAnalysisJobItem) -> int | None:
    """Return the score produced by this job item, never a newer task snapshot.

    ``EmailTask.match_score`` is retained for compatibility, but it is mutable:
    a later analysis can overwrite it while an old job remains in the history.
    The run linked from the item is therefore authoritative.  For legacy rows
    created before ``match_analysis_run_id`` was persisted, a succeeded item may
    still use the task snapshot; queued/running items deliberately report no
    score so an old value is not presented as the pending job's result.
    """

    item_state = inspect(item)
    loaded_run = item_state.attrs.match_analysis_run.loaded_value
    if loaded_run is not NO_VALUE and loaded_run is not None:
        return _normalize_job_match_score(loaded_run.match_score)
    if item.match_analysis_run_id is not None:
        # The caller did not eager-load the run.  Avoid triggering an async lazy
        # load from this synchronous serializer and avoid falling back to a
        # potentially newer EmailTask snapshot.
        return None
    if item.status != MatchAnalysisJobItemStatus.SUCCEEDED.value:
        return None
    loaded_task = item_state.attrs.email_task.loaded_value
    if loaded_task is NO_VALUE or loaded_task is None:
        return None
    return _normalize_job_match_score(loaded_task.match_score)


def _normalize_job_match_score(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        score = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0, min(100, score))


async def run_queued_match_analysis_jobs_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    item_concurrency: int = 3,
) -> int:
    await _recover_interrupted_match_analysis_jobs(session_factory)
    job_id = await _claim_next_match_analysis_job(session_factory)
    if job_id is None:
        return 0
    try:
        await _run_match_analysis_job(
            session_factory,
            job_id,
            item_concurrency=item_concurrency,
        )
    finally:
        _ACTIVE_MATCH_ANALYSIS_JOB_IDS.discard(job_id)
    return 1


async def request_match_analysis_job_cancel(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
) -> MatchAnalysisJob:
    async with session_factory() as session:
        job = await request_match_analysis_job_cancel_record(session, job_id)
        await session.commit()
        await session.refresh(job)
        return job


async def request_match_analysis_job_cancel_record(
    session: AsyncSession,
    job_id: int,
    *,
    event_name: str = "match_analysis_job.cancel_requested",
    actor: str | None = None,
) -> MatchAnalysisJob:
    """Request cancellation in the caller's transaction without committing it."""

    job = await session.get(MatchAnalysisJob, job_id)
    if job is None:
        raise ValueError("匹配分析任务不存在")

    now = utc_now()
    metadata = {"actor": actor} if actor is not None else None
    if job.status == MatchAnalysisJobStatus.QUEUED.value:
        job.status = MatchAnalysisJobStatus.CANCELED.value
        job.cancel_requested_at = now
        job.finished_at = now
        job.updated_at = now
        await session.execute(
            update(MatchAnalysisJobItem)
            .where(
                MatchAnalysisJobItem.job_id == job.id,
                MatchAnalysisJobItem.status == MatchAnalysisJobItemStatus.QUEUED.value,
            )
            .values(
                status=MatchAnalysisJobItemStatus.CANCELED.value,
                finished_at=now,
                updated_at=now,
            ),
        )
        await _record_match_analysis_job_log(session, job, event_name, metadata=metadata)
        return job

    if job.status == MatchAnalysisJobStatus.RUNNING.value:
        job.cancel_requested_at = now
        job.updated_at = now
        await _record_match_analysis_job_log(session, job, event_name, metadata=metadata)
        return job

    raise ValueError("只有排队中或运行中的匹配分析任务可以取消")


async def retry_failed_match_analysis_job(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
) -> MatchAnalysisJob:
    async with session_factory() as session:
        job = await retry_failed_match_analysis_job_record(session, job_id)
        await session.commit()
        await session.refresh(job)
        return job


async def retry_failed_match_analysis_job_record(
    session: AsyncSession,
    job_id: int,
    *,
    event_name: str = "match_analysis_job.created",
    actor: str | None = None,
) -> MatchAnalysisJob:
    """Create a retry job in the caller's transaction without committing it."""

    job = await session.get(MatchAnalysisJob, job_id)
    if job is None:
        raise ValueError("匹配分析任务不存在")
    professor_ids = list(
        await session.scalars(
            select(MatchAnalysisJobItem.professor_id)
            .where(
                MatchAnalysisJobItem.job_id == job_id,
                MatchAnalysisJobItem.status.in_(
                    [
                        MatchAnalysisJobItemStatus.FAILED.value,
                        MatchAnalysisJobItemStatus.CANCELED.value,
                    ]
                ),
            )
            .order_by(MatchAnalysisJobItem.id.asc()),
        )
    )
    if not professor_ids:
        raise ValueError("没有可重试的失败项")
    if job.match_source_identity_id is None:
        raise ValueError("原匹配依据身份已删除，无法重试，请新建匹配任务")

    return await create_match_analysis_job_record(
        session,
        identity_id=job.identity_id,
        llm_profile_id=job.llm_profile_id,
        professor_ids=professor_ids,
        name=f"{job.name} - 重试",
        match_source_identity_id=job.match_source_identity_id,
        event_name=event_name,
        actor=actor,
    )


MATCH_ANALYSIS_JOB_DELETABLE_STATUSES = {
    MatchAnalysisJobStatus.COMPLETED.value,
    MatchAnalysisJobStatus.PARTIAL_FAILED.value,
    MatchAnalysisJobStatus.FAILED.value,
    MatchAnalysisJobStatus.CANCELED.value,
}


async def delete_match_analysis_job_record(
    session: AsyncSession,
    job_id: int,
    *,
    event_name: str = "match_analysis_job.deleted",
    actor: str | None = None,
) -> MatchAnalysisJob:
    """Move a completed match-analysis job to the trash without committing it."""

    job = await session.get(MatchAnalysisJob, job_id)
    if job is None:
        raise ValueError("匹配分析任务不存在")
    if job.status not in MATCH_ANALYSIS_JOB_DELETABLE_STATUSES:
        raise ValueError("请先中止/取消任务后再删除")
    previous_deleted_at = job.deleted_at
    if job.deleted_at is None:
        now = utc_now()
        job.deleted_at = now
        job.updated_at = now
    metadata: dict[str, object] = {
        "previous_deleted_at": (
            previous_deleted_at.isoformat() if previous_deleted_at is not None else None
        ),
    }
    if actor is not None:
        metadata["actor"] = actor
    await _record_match_analysis_job_log(session, job, event_name, metadata=metadata)
    return job


async def restore_match_analysis_job_record(
    session: AsyncSession,
    job_id: int,
    *,
    event_name: str = "match_analysis_job.restored",
    actor: str | None = None,
) -> MatchAnalysisJob:
    """Restore a trashed match-analysis job without committing it."""

    job = await session.get(MatchAnalysisJob, job_id)
    if job is None:
        raise ValueError("匹配分析任务不存在")
    previous_deleted_at = job.deleted_at
    if job.deleted_at is not None:
        job.deleted_at = None
        job.updated_at = utc_now()
    metadata: dict[str, object] = {
        "previous_deleted_at": (
            previous_deleted_at.isoformat() if previous_deleted_at is not None else None
        ),
    }
    if actor is not None:
        metadata["actor"] = actor
    await _record_match_analysis_job_log(session, job, event_name, metadata=metadata)
    return job


def _has_professor_match_evidence(professor: Professor) -> bool:
    if professor.research_direction and professor.research_direction.strip():
        return True
    return bool(professor.recent_papers)


async def _ensure_match_email_task(
    session: AsyncSession,
    *,
    professor: Professor,
    identity: IdentityProfile,
    llm_profile: LLMProfile,
) -> EmailTask:
    existing_task = await session.scalar(
        select(EmailTask)
        .where(
            EmailTask.professor_id == professor.id,
            EmailTask.identity_id == identity.id,
            EmailTask.status != EmailTaskStatus.CANCELED.value,
        )
        .order_by(EmailTask.created_at.desc(), EmailTask.id.desc())
        .limit(1),
    )
    if existing_task is not None:
        return existing_task

    selected_template = await get_default_outreach_template_for_identity(
        session,
        identity,
    )
    snapshot = resolve_outreach_template_config(identity, template=selected_template)
    task = EmailTask(
        professor_id=professor.id,
        identity_id=identity.id,
        llm_profile_id=llm_profile.id,
        source=EmailTaskSource.MANUAL.value,
        status=EmailTaskStatus.DISCOVERED.value,
        outreach_template_id=(
            selected_template.id if selected_template is not None else None
        ),
        outreach_template_snapshot_version=1,
        outreach_generation_mode=snapshot.generation_mode,
        outreach_template_subject=snapshot.subject_template,
        outreach_template_body_text=snapshot.body_text_template,
        outreach_template_body_html=snapshot.body_html_template,
        selected_material_ids=[],
    )
    session.add(task)
    await session.flush()
    return task


async def _claim_next_match_analysis_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> int | None:
    async with session_factory() as session:
        job_id = await session.scalar(
            select(MatchAnalysisJob.id)
            .where(
                MatchAnalysisJob.status == MatchAnalysisJobStatus.QUEUED.value,
                MatchAnalysisJob.deleted_at.is_(None),
            )
            .order_by(MatchAnalysisJob.created_at.asc(), MatchAnalysisJob.id.asc())
            .limit(1),
        )
        if job_id is None:
            return None

        now = utc_now()
        claim_result = await session.execute(
            update(MatchAnalysisJob)
            .where(
                MatchAnalysisJob.id == job_id,
                MatchAnalysisJob.status == MatchAnalysisJobStatus.QUEUED.value,
                MatchAnalysisJob.deleted_at.is_(None),
            )
            .values(
                status=MatchAnalysisJobStatus.RUNNING.value,
                started_at=now,
                updated_at=now,
                last_error=None,
            ),
        )
        if claim_result.rowcount != 1:
            await session.rollback()
            return None
        _ACTIVE_MATCH_ANALYSIS_JOB_IDS.add(job_id)
        await session.commit()
        return job_id


async def _recover_interrupted_match_analysis_jobs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
            running_job_ids = list(
                await session.scalars(
                    select(MatchAnalysisJob.id)
                    .where(
                        MatchAnalysisJob.status == MatchAnalysisJobStatus.RUNNING.value,
                        MatchAnalysisJob.deleted_at.is_(None),
                    )
                    .order_by(MatchAnalysisJob.created_at.asc(), MatchAnalysisJob.id.asc()),
                )
            )

    for job_id in running_job_ids:
        if job_id in _ACTIVE_MATCH_ANALYSIS_JOB_IDS:
            continue
        await _recover_interrupted_match_analysis_job(session_factory, job_id)


async def _recover_interrupted_match_analysis_job(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
) -> None:
    async with session_factory() as session:
        job = await session.get(MatchAnalysisJob, job_id)
        if job is None or job.status != MatchAnalysisJobStatus.RUNNING.value:
            return

        unfinished_statuses = [
            MatchAnalysisJobItemStatus.QUEUED.value,
            MatchAnalysisJobItemStatus.RUNNING.value,
        ]
        unfinished_item_ids = list(
            await session.scalars(
                select(MatchAnalysisJobItem.id)
                .where(
                    MatchAnalysisJobItem.job_id == job_id,
                    MatchAnalysisJobItem.status.in_(unfinished_statuses),
                )
                .order_by(MatchAnalysisJobItem.id.asc()),
            )
        )
        now = utc_now()
        if not unfinished_item_ids:
            await session.rollback()
            await _refresh_match_analysis_job_summary(session_factory, job_id)
            return

        unfinished_email_task_ids = list(
            await session.scalars(
                select(MatchAnalysisJobItem.email_task_id).where(
                    MatchAnalysisJobItem.id.in_(unfinished_item_ids),
                    MatchAnalysisJobItem.email_task_id.is_not(None),
                )
            )
        )
        if unfinished_email_task_ids:
            await session.execute(
                update(MatchAnalysisRun)
                .where(
                    MatchAnalysisRun.email_task_id.in_(unfinished_email_task_ids),
                    MatchAnalysisRun.status == "running",
                )
                .values(
                    status="failed",
                    success=False,
                    error_kind="interrupted",
                    error_message="匹配分析任务中断后恢复",
                    finished_at=now,
                ),
            )

        next_item_status = (
            MatchAnalysisJobItemStatus.CANCELED.value
            if job.cancel_requested_at is not None
            else MatchAnalysisJobItemStatus.QUEUED.value
        )
        await session.execute(
            update(MatchAnalysisJobItem)
            .where(MatchAnalysisJobItem.id.in_(unfinished_item_ids))
            .values(
                status=next_item_status,
                finished_at=now if next_item_status == MatchAnalysisJobItemStatus.CANCELED.value else None,
                updated_at=now,
            ),
        )
        if job.cancel_requested_at is not None:
            job.updated_at = now
            await session.commit()
            await _refresh_match_analysis_job_summary(session_factory, job_id)
            return

        job.status = MatchAnalysisJobStatus.QUEUED.value
        job.updated_at = now
        await session.commit()


async def _run_match_analysis_job(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
    *,
    item_concurrency: int,
) -> None:
    async with session_factory() as session:
        queued_item_ids = list(
            await session.scalars(
                select(MatchAnalysisJobItem.id)
                .where(
                    MatchAnalysisJobItem.job_id == job_id,
                    MatchAnalysisJobItem.status == MatchAnalysisJobItemStatus.QUEUED.value,
                )
                .order_by(MatchAnalysisJobItem.id.asc()),
            )
        )

    async def run_item(item_id: int) -> None:
        await _run_match_analysis_job_item(session_factory, job_id, item_id)

    if queued_item_ids:
        await run_item(queued_item_ids[0])
        pending_item_ids = queued_item_ids[1:]
        item_queue: asyncio.Queue[int] = asyncio.Queue()
        for item_id in pending_item_ids:
            item_queue.put_nowait(item_id)

        async def worker() -> None:
            while True:
                try:
                    item_id = item_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    await run_item(item_id)
                finally:
                    item_queue.task_done()

        worker_count = min(max(item_concurrency, 1), len(pending_item_ids))
        await asyncio.gather(*(worker() for _ in range(worker_count)))
    await _refresh_match_analysis_job_summary(session_factory, job_id)


async def _run_match_analysis_job_item(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
    item_id: int,
) -> None:
    now = utc_now()
    async with session_factory() as session:
        job = await session.get(MatchAnalysisJob, job_id)
        item = await session.get(
            MatchAnalysisJobItem,
            item_id,
            options=[selectinload(MatchAnalysisJobItem.email_task)],
        )
        if job is None or item is None:
            return

        if job.cancel_requested_at is not None:
            item.status = MatchAnalysisJobItemStatus.CANCELED.value
            item.finished_at = now
            item.updated_at = now
            await session.commit()
            return
        if job.match_source_identity_id is None:
            item.status = MatchAnalysisJobItemStatus.SKIPPED.value
            item.skip_reason = "匹配依据身份已删除，无法继续分析"
            item.finished_at = now
            item.updated_at = now
            await session.commit()
            return

        item.status = MatchAnalysisJobItemStatus.RUNNING.value
        item.started_at = now
        item.updated_at = now
        await session.commit()

    try:
        result = await _calculate_task_match_until_canceled(
            session_factory,
            job_id,
            item.email_task_id,
            match_source_identity_id=job.match_source_identity_id,
        )
    except (_MatchAnalysisJobCanceled, MatchCalculationCanceledError):
        await _mark_item_canceled(session_factory, item_id)
        return
    except MatchAnalysisAlreadyRunningError as exc:
        await _mark_item_skipped(
            session_factory,
            item_id,
            skip_reason=str(exc),
        )
        return
    except ValueError as exc:
        await _mark_item_skipped(
            session_factory,
            item_id,
            skip_reason=str(exc),
        )
        return
    except Exception as exc:
        await _mark_item_failed(
            session_factory,
            item_id,
            error_message=str(exc),
        )
        return

    if await _is_match_analysis_job_cancel_requested(session_factory, job_id):
        await _mark_item_canceled(session_factory, item_id)
        return

    await _mark_item_succeeded(
        session_factory,
        item_id,
        run_id=result.run_id,
        prompt_tokens=result.usage.prompt_tokens or 0,
        completion_tokens=result.usage.completion_tokens or 0,
        cached_tokens=result.usage.cached_tokens or 0,
        total_tokens=result.usage.total_tokens or 0,
    )


async def _calculate_task_match_until_canceled(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
    email_task_id: int,
    *,
    match_source_identity_id: int,
):
    async def cancel_requested() -> bool:
        return await _is_match_analysis_job_cancel_requested(session_factory, job_id)

    calculation_task = asyncio.create_task(
        calculate_task_match(
            session_factory,
            email_task_id,
            force=True,
            ignore_batch_status=True,
            cancel_requested=cancel_requested,
            match_source_identity_id=match_source_identity_id,
        )
    )
    try:
        while True:
            done, _ = await asyncio.wait(
                {calculation_task},
                timeout=_MATCH_ANALYSIS_CANCEL_POLL_SECONDS,
            )
            if calculation_task in done:
                return await calculation_task
            if await cancel_requested():
                calculation_task.cancel()
                try:
                    await calculation_task
                except asyncio.CancelledError:
                    pass
                raise _MatchAnalysisJobCanceled("匹配分析任务已取消")
    finally:
        if not calculation_task.done():
            calculation_task.cancel()
            try:
                await calculation_task
            except asyncio.CancelledError:
                pass


async def _is_match_analysis_job_cancel_requested(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
) -> bool:
    async with session_factory() as session:
        cancel_requested_at = await session.scalar(
            select(MatchAnalysisJob.cancel_requested_at).where(MatchAnalysisJob.id == job_id)
        )
        return cancel_requested_at is not None


async def _mark_item_succeeded(
    session_factory: async_sessionmaker[AsyncSession],
    item_id: int,
    *,
    run_id: int | None,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int,
    total_tokens: int,
) -> None:
    async with session_factory() as session:
        item = await session.get(MatchAnalysisJobItem, item_id)
        if item is None:
            return
        now = utc_now()
        transition = await session.execute(
            update(MatchAnalysisJobItem)
            .where(
                MatchAnalysisJobItem.id == item_id,
                MatchAnalysisJobItem.status == MatchAnalysisJobItemStatus.RUNNING.value,
            )
            .values(
                status=MatchAnalysisJobItemStatus.SUCCEEDED.value,
                match_analysis_run_id=run_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
                total_tokens=total_tokens,
                error_message=None,
                skip_reason=None,
                finished_at=now,
                updated_at=now,
            )
        )
        if transition.rowcount == 1:
            await session.execute(
                update(MatchAnalysisJob)
                .where(MatchAnalysisJob.id == item.job_id)
                .values(
                    succeeded_count=MatchAnalysisJob.succeeded_count + 1,
                    total_prompt_tokens=MatchAnalysisJob.total_prompt_tokens + prompt_tokens,
                    total_completion_tokens=(
                        MatchAnalysisJob.total_completion_tokens + completion_tokens
                    ),
                    total_cached_tokens=MatchAnalysisJob.total_cached_tokens + cached_tokens,
                    total_tokens=MatchAnalysisJob.total_tokens + total_tokens,
                    updated_at=now,
                )
            )
        await session.commit()


async def _mark_item_canceled(
    session_factory: async_sessionmaker[AsyncSession],
    item_id: int,
) -> None:
    async with session_factory() as session:
        item = await session.get(MatchAnalysisJobItem, item_id)
        if item is None:
            return
        now = utc_now()
        await session.execute(
            update(MatchAnalysisJobItem)
            .where(
                MatchAnalysisJobItem.id == item_id,
                MatchAnalysisJobItem.status.in_(
                    [
                        MatchAnalysisJobItemStatus.QUEUED.value,
                        MatchAnalysisJobItemStatus.RUNNING.value,
                    ]
                ),
            )
            .values(
                status=MatchAnalysisJobItemStatus.CANCELED.value,
                error_message=None,
                skip_reason=None,
                finished_at=now,
                updated_at=now,
            )
        )
        await session.commit()


async def _mark_item_skipped(
    session_factory: async_sessionmaker[AsyncSession],
    item_id: int,
    *,
    skip_reason: str,
) -> None:
    async with session_factory() as session:
        item = await session.get(MatchAnalysisJobItem, item_id)
        if item is None:
            return
        now = utc_now()
        transition = await session.execute(
            update(MatchAnalysisJobItem)
            .where(
                MatchAnalysisJobItem.id == item_id,
                MatchAnalysisJobItem.status.in_(
                    [
                        MatchAnalysisJobItemStatus.QUEUED.value,
                        MatchAnalysisJobItemStatus.RUNNING.value,
                    ]
                ),
            )
            .values(
                status=MatchAnalysisJobItemStatus.SKIPPED.value,
                skip_reason=skip_reason,
                error_message=None,
                finished_at=now,
                updated_at=now,
            )
        )
        if transition.rowcount == 1:
            await session.execute(
                update(MatchAnalysisJob)
                .where(MatchAnalysisJob.id == item.job_id)
                .values(
                    skipped_count=MatchAnalysisJob.skipped_count + 1,
                    updated_at=now,
                )
            )
        await session.commit()


async def _mark_item_failed(
    session_factory: async_sessionmaker[AsyncSession],
    item_id: int,
    *,
    error_message: str,
) -> None:
    async with session_factory() as session:
        item = await session.get(MatchAnalysisJobItem, item_id)
        if item is None:
            return
        now = utc_now()
        transition = await session.execute(
            update(MatchAnalysisJobItem)
            .where(
                MatchAnalysisJobItem.id == item_id,
                MatchAnalysisJobItem.status == MatchAnalysisJobItemStatus.RUNNING.value,
            )
            .values(
                status=MatchAnalysisJobItemStatus.FAILED.value,
                error_message=error_message,
                finished_at=now,
                updated_at=now,
            )
        )
        if transition.rowcount == 1:
            await session.execute(
                update(MatchAnalysisJob)
                .where(MatchAnalysisJob.id == item.job_id)
                .values(
                    failed_count=MatchAnalysisJob.failed_count + 1,
                    updated_at=now,
                )
            )
        await session.commit()


async def _refresh_match_analysis_job_summary(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
) -> None:
    async with session_factory() as session:
        job = await session.get(MatchAnalysisJob, job_id)
        if job is None:
            return

        items = list(
            await session.scalars(
                select(MatchAnalysisJobItem).where(MatchAnalysisJobItem.job_id == job_id)
            )
        )
        succeeded_count = sum(
            1 for item in items if item.status == MatchAnalysisJobItemStatus.SUCCEEDED.value
        )
        failed_count = sum(
            1 for item in items if item.status == MatchAnalysisJobItemStatus.FAILED.value
        )
        skipped_count = sum(
            1 for item in items if item.status == MatchAnalysisJobItemStatus.SKIPPED.value
        )
        canceled_count = sum(
            1 for item in items if item.status == MatchAnalysisJobItemStatus.CANCELED.value
        )
        running_count = sum(
            1 for item in items if item.status == MatchAnalysisJobItemStatus.RUNNING.value
        )
        queued_count = sum(
            1 for item in items if item.status == MatchAnalysisJobItemStatus.QUEUED.value
        )

        job.succeeded_count = succeeded_count
        job.failed_count = failed_count
        job.skipped_count = skipped_count
        job.total_prompt_tokens = sum(item.prompt_tokens for item in items)
        job.total_completion_tokens = sum(item.completion_tokens for item in items)
        job.total_cached_tokens = sum(item.cached_tokens for item in items)
        job.total_tokens = sum(item.total_tokens for item in items)
        job.updated_at = utc_now()
        job.finished_at = job.updated_at

        if canceled_count > 0:
            job.status = MatchAnalysisJobStatus.CANCELED.value
        elif (
            failed_count == 0
            and queued_count == 0
            and running_count == 0
            and succeeded_count > 0
        ):
            job.status = MatchAnalysisJobStatus.COMPLETED.value
        elif succeeded_count > 0:
            job.status = MatchAnalysisJobStatus.PARTIAL_FAILED.value
        else:
            job.status = MatchAnalysisJobStatus.FAILED.value

        if job.status == MatchAnalysisJobStatus.FAILED.value and skipped_count == len(items):
            job.last_error = "没有可分析导师"

        await record_operation_log(
            session,
            category="match_analysis",
            event_name=f"match_analysis_job.{job.status}",
            level="error" if job.status == MatchAnalysisJobStatus.FAILED.value else "info",
            message=job.last_error,
            entity_type="match_analysis_job",
            entity_id=str(job.id),
            metadata={
                "name": job.name,
                "status": job.status,
                "target_count": job.target_count,
                "succeeded_count": succeeded_count,
                "failed_count": failed_count,
                "skipped_count": skipped_count,
                "canceled_count": canceled_count,
                "total_prompt_tokens": job.total_prompt_tokens,
                "total_completion_tokens": job.total_completion_tokens,
                "total_cached_tokens": job.total_cached_tokens,
                "total_tokens": job.total_tokens,
            },
        )
        await session.commit()


async def _record_match_analysis_job_log(
    session: AsyncSession,
    job: MatchAnalysisJob,
    event_name: str,
    *,
    level: str = "info",
    message: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    base_metadata: dict[str, object] = {
        "name": job.name,
        "status": job.status,
        "identity_id": job.identity_id,
        "llm_profile_id": job.llm_profile_id,
        "target_count": job.target_count,
        "succeeded_count": job.succeeded_count,
        "failed_count": job.failed_count,
        "skipped_count": job.skipped_count,
    }
    if metadata:
        base_metadata.update(metadata)
    await record_operation_log(
        session,
        category="match_analysis",
        event_name=event_name,
        level=level,
        message=message,
        entity_type="match_analysis_job",
        entity_id=str(job.id),
        metadata=base_metadata,
    )
