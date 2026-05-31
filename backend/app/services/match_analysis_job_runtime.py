from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.core.time import utc_now

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

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
from app.schemas.match_analysis_job import MatchAnalysisJobItemRead, MatchAnalysisJobRead
from app.services.task_runtime import (
    MatchAnalysisAlreadyRunningError,
    MatchCalculationCanceledError,
    calculate_task_match,
)
from app.services.operation_logs import record_operation_log


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
    unique_professor_ids = list(dict.fromkeys(professor_ids))
    if not unique_professor_ids:
        raise ValueError("请选择要分析匹配度的导师")

    async with session_factory() as session:
        identity = await session.get(IdentityProfile, identity_id)
        if identity is None:
            raise ValueError("身份不存在")
        if identity.current_primary_material_id is None:
            raise ValueError("请先设置默认材料")

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

        now = utc_now()
        job = MatchAnalysisJob(
            name=name or f"批量匹配分析 {now:%Y-%m-%d %H:%M}",
            identity_id=identity_id,
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
            if _has_professor_match_evidence(professor):
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

        if queued_count == 0:
            await session.rollback()
            raise ValueError("已选导师都缺少研究方向或近期论文，暂不能分析匹配度")

        job.target_count = queued_count
        job.skipped_count = skipped_count
        await record_operation_log(
            session,
            category="match_analysis",
            event_name="match_analysis_job.created",
            entity_type="match_analysis_job",
            entity_id=str(job.id),
            metadata={
                "name": job.name,
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "selected_count": len(professors),
                "target_count": queued_count,
                "skipped_count": skipped_count,
            },
        )
        await session.commit()
        await session.refresh(job)
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
        total_tokens=job.total_tokens,
        identity_id=job.identity_id,
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
        professor_school=item.professor.school,
        email_task_id=item.email_task_id,
        status=item.status,
        match_score=item.email_task.match_score if item.email_task else None,
        match_analysis_run_id=item.match_analysis_run_id,
        error_message=item.error_message,
        skip_reason=item.skip_reason,
        prompt_tokens=item.prompt_tokens,
        completion_tokens=item.completion_tokens,
        total_tokens=item.total_tokens,
        started_at=item.started_at,
        finished_at=item.finished_at,
        updated_at=item.updated_at,
    )


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
        job = await session.get(MatchAnalysisJob, job_id)
        if job is None:
            raise ValueError("匹配分析任务不存在")

        now = utc_now()
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
            await _record_match_analysis_job_log(session, job, "match_analysis_job.cancel_requested")
            await session.commit()
            await session.refresh(job)
            return job

        if job.status == MatchAnalysisJobStatus.RUNNING.value:
            job.cancel_requested_at = now
            job.updated_at = now
            await _record_match_analysis_job_log(session, job, "match_analysis_job.cancel_requested")
            await session.commit()
            await session.refresh(job)
            return job

        raise ValueError("只有排队中或运行中的匹配分析任务可以取消")


async def retry_failed_match_analysis_job(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
) -> MatchAnalysisJob:
    async with session_factory() as session:
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
        identity_id = job.identity_id
        llm_profile_id = job.llm_profile_id
        name = f"{job.name} - 重试"

    if not professor_ids:
        raise ValueError("没有可重试的失败项")

    return await create_match_analysis_job(
        session_factory,
        identity_id=identity_id,
        llm_profile_id=llm_profile_id,
        professor_ids=professor_ids,
        name=name,
    )


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
            EmailTask.llm_profile_id == llm_profile.id,
            EmailTask.status != EmailTaskStatus.CANCELED.value,
        )
        .order_by(EmailTask.created_at.desc(), EmailTask.id.desc())
        .limit(1),
    )
    if existing_task is not None:
        if existing_task.primary_material_id is None:
            existing_task.primary_material_id = identity.current_primary_material_id
        return existing_task

    task = EmailTask(
        professor_id=professor.id,
        identity_id=identity.id,
        llm_profile_id=llm_profile.id,
        source=EmailTaskSource.MANUAL.value,
        status=EmailTaskStatus.DISCOVERED.value,
        primary_material_id=identity.current_primary_material_id,
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

    semaphore = asyncio.Semaphore(max(item_concurrency, 1))

    async def run_item(item_id: int) -> None:
        async with semaphore:
            await _run_match_analysis_job_item(session_factory, job_id, item_id)

    await asyncio.gather(*(run_item(item_id) for item_id in queued_item_ids))
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

        item.status = MatchAnalysisJobItemStatus.RUNNING.value
        item.started_at = now
        item.updated_at = now
        await session.commit()

    try:
        result = await _calculate_task_match_until_canceled(
            session_factory,
            job_id,
            item.email_task_id,
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
        total_tokens=result.usage.total_tokens or 0,
    )


async def _calculate_task_match_until_canceled(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
    email_task_id: int,
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
    total_tokens: int,
) -> None:
    async with session_factory() as session:
        item = await session.get(MatchAnalysisJobItem, item_id)
        if item is None:
            return
        now = utc_now()
        item.status = MatchAnalysisJobItemStatus.SUCCEEDED.value
        item.match_analysis_run_id = run_id
        item.prompt_tokens = prompt_tokens
        item.completion_tokens = completion_tokens
        item.total_tokens = total_tokens
        item.error_message = None
        item.skip_reason = None
        item.finished_at = now
        item.updated_at = now
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
        item.status = MatchAnalysisJobItemStatus.CANCELED.value
        item.error_message = None
        item.skip_reason = None
        item.finished_at = now
        item.updated_at = now
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
        item.status = MatchAnalysisJobItemStatus.SKIPPED.value
        item.skip_reason = skip_reason
        item.error_message = None
        item.finished_at = now
        item.updated_at = now
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
        item.status = MatchAnalysisJobItemStatus.FAILED.value
        item.error_message = error_message
        item.finished_at = now
        item.updated_at = now
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
        job.total_tokens = sum(item.total_tokens for item in items)
        job.updated_at = utc_now()
        job.finished_at = job.updated_at

        if canceled_count > 0:
            job.status = MatchAnalysisJobStatus.CANCELED.value
        elif (
            failed_count == 0
            and skipped_count == 0
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
