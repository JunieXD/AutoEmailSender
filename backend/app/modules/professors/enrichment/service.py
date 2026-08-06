from __future__ import annotations

from collections import Counter
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.time import utc_now
from app.models import (
    CrawlCandidate,
    CrawlCandidateEnrichmentTask,
    CrawlCandidateEnrichmentTaskStatus,
    CrawlCandidateReviewStatus,
    CrawlJob,
    CrawlJobKind,
    CrawlJobStatus,
    CrawlJobTriggerMode,
    CrawlWorkerKind,
    CrawlWorkerTokenUsage,
    LLMProfile,
    Professor,
)
from .schemas import (
    ProfessorInformationEnrichmentItemRead,
    ProfessorInformationEnrichmentJobRead,
)
from app.services.crawl_job_metrics import build_crawl_job_metrics
from app.services.crawl_job_runs import (
    create_initial_crawl_job_run,
    mark_crawl_job_run_finished,
)
from app.services.crawler_tools import validate_safe_public_crawl_url
from app.services.crawler_v2_profile_text_cache import profile_text_cache
from app.services.operation_logs import record_operation_log, sanitize_user_visible_error
from app.modules.professors.public import (
    is_valid_professor_email,
    normalize_professor_email,
    normalize_professor_title,
    normalize_recent_papers,
    normalize_research_direction,
)


INFORMATION_ENRICHMENT_FIELDS = (
    "email",
    "title",
    "department",
    "research_direction",
    "recent_papers",
)
INFORMATION_ENRICHMENT_FIELD_LABELS = {
    "email": "邮箱",
    "title": "职称",
    "department": "系所",
    "research_direction": "研究方向",
    "recent_papers": "近期论文",
}
ACTIVE_JOB_STATUSES = {
    CrawlJobStatus.QUEUED.value,
    CrawlJobStatus.RUNNING.value,
}
ACTIVE_TASK_STATUSES = {
    CrawlCandidateEnrichmentTaskStatus.PENDING.value,
    CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
    CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value,
}
TERMINAL_TASK_STATUSES = {
    CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value,
    CrawlCandidateEnrichmentTaskStatus.SKIPPED.value,
    CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
    CrawlCandidateEnrichmentTaskStatus.CANCELED.value,
}
DELETABLE_JOB_STATUSES = {
    CrawlJobStatus.COMPLETED.value,
    CrawlJobStatus.PARTIALLY_COMPLETED.value,
    CrawlJobStatus.FAILED.value,
    CrawlJobStatus.CANCELED.value,
}


async def create_professor_information_enrichment_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    professor_ids: list[int],
    llm_profile_id: int,
    trigger_mode: str,
    name: str | None = None,
) -> int:
    async with session_factory() as session:
        job = await create_professor_information_enrichment_job_record(
            session,
            professor_ids=professor_ids,
            llm_profile_id=llm_profile_id,
            trigger_mode=trigger_mode,
            name=name,
        )
        await session.commit()
        return job.id


async def create_professor_information_enrichment_job_record(
    session: AsyncSession,
    *,
    professor_ids: list[int],
    llm_profile_id: int,
    trigger_mode: str,
    name: str | None = None,
    event_name: str | None = None,
    actor: str | None = None,
) -> CrawlJob:
    """Create an enrichment job in the caller's transaction without committing it."""

    requested_ids = list(dict.fromkeys(int(item) for item in professor_ids if int(item) > 0))
    if not requested_ids:
        raise ValueError("请至少选择一位导师")
    if trigger_mode not in {
        CrawlJobTriggerMode.SINGLE.value,
        CrawlJobTriggerMode.BATCH.value,
    }:
        raise ValueError("未知的信息补全触发方式")
    if trigger_mode == CrawlJobTriggerMode.SINGLE.value and len(requested_ids) != 1:
        raise ValueError("单导师补全一次只能选择一位导师")

    llm_profile = await session.get(LLMProfile, llm_profile_id)
    if llm_profile is None:
        raise ValueError("所选模型配置不存在")

    professors = list(
        await session.scalars(
            select(Professor).where(Professor.id.in_(requested_ids)),
        )
    )
    professors_by_id = {professor.id: professor for professor in professors}
    missing_ids = [item for item in requested_ids if item not in professors_by_id]
    if missing_ids:
        raise ValueError("导师不存在")

    ordered_professors = [professors_by_id[item] for item in requested_ids]
    active_professor_ids = await _active_professor_ids(session, requested_ids)
    if trigger_mode == CrawlJobTriggerMode.SINGLE.value:
        _validate_single_professor(
            ordered_professors[0],
            active=ordered_professors[0].id in active_professor_ids,
        )

    now = utc_now()
    display_name = _build_display_name(
        ordered_professors,
        trigger_mode=trigger_mode,
        requested_name=name,
        now=now,
    )
    valid_urls = [
        professor.profile_url.strip()
        for professor in ordered_professors
        if _is_valid_profile_url(professor.profile_url)
    ]
    job = CrawlJob(
        university=_summarize_location(
            [professor.university for professor in ordered_professors],
            fallback="导师信息补全",
            multiple="多所院校",
        ),
        school=_summarize_location(
            [professor.school for professor in ordered_professors],
            fallback="",
            multiple="多个学院",
        ),
        start_url=valid_urls[0] if valid_urls else "https://example.invalid/information-enrichment",
        start_urls=valid_urls,
        entry_type="profile",
        job_kind=CrawlJobKind.PROFESSOR_ENRICHMENT.value,
        trigger_mode=trigger_mode,
        task_center_visible=trigger_mode == CrawlJobTriggerMode.BATCH.value,
        display_name=display_name,
        runtime_version="v2",
        llm_profile_id=llm_profile.id,
        status=CrawlJobStatus.QUEUED.value,
        progress_current=0,
        progress_total=len(ordered_professors),
        agent_trace=[],
    )
    session.add(job)
    await session.flush()
    await create_initial_crawl_job_run(session, job, now=now)

    queued_count = 0
    skipped_count = 0
    for professor in ordered_professors:
        skip_reason = _batch_skip_reason(
            professor,
            active=professor.id in active_professor_ids,
        )
        candidate = _build_candidate(job.id, professor)
        session.add(candidate)
        await session.flush()
        task_status = (
            CrawlCandidateEnrichmentTaskStatus.SKIPPED.value
            if skip_reason is not None
            else CrawlCandidateEnrichmentTaskStatus.PENDING.value
        )
        session.add(
            CrawlCandidateEnrichmentTask(
                job_id=job.id,
                candidate_id=candidate.id,
                professor_id=professor.id,
                status=task_status,
                skip_reason=skip_reason,
                enriched_fields=[],
                finished_at=now if skip_reason is not None else None,
            )
        )
        if skip_reason is None:
            queued_count += 1
        else:
            skipped_count += 1

    metadata: dict[str, object] = {
        "trigger_mode": trigger_mode,
        "llm_profile_id": llm_profile.id,
        "target_count": len(ordered_professors),
        "queued_count": queued_count,
        "skipped_count": skipped_count,
        "professor_ids": requested_ids,
    }
    if actor is not None:
        metadata["actor"] = actor
    await record_operation_log(
        session,
        category="professor_information_enrichment",
        event_name=(
            event_name
            or (
                "professor_information_enrichment.single_created"
                if trigger_mode == CrawlJobTriggerMode.SINGLE.value
                else "professor_information_enrichment.batch_created"
            )
        ),
        entity_type="crawl_job",
        entity_id=str(job.id),
        metadata=metadata,
    )
    job.progress_current = skipped_count
    if queued_count == 0:
        await finalize_professor_information_enrichment_job(session, job, now=now)
    return job


async def list_professor_information_enrichment_jobs(
    session: AsyncSession,
    *,
    view: str,
    limit: int = 50,
) -> list[ProfessorInformationEnrichmentJobRead]:
    statement = (
        select(CrawlJob)
        .options(selectinload(CrawlJob.current_run))
        .where(
            CrawlJob.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value,
            CrawlJob.task_center_visible.is_(True),
        )
        .order_by(CrawlJob.created_at.desc(), CrawlJob.id.desc())
        .limit(limit)
    )
    if view == "current":
        statement = statement.where(CrawlJob.deleted_at.is_(None))
    elif view == "trash":
        statement = statement.where(CrawlJob.deleted_at.is_not(None))
    else:
        raise ValueError("未知任务视图")
    jobs = list(await session.scalars(statement))
    return [await serialize_professor_information_enrichment_job(session, job) for job in jobs]


async def get_professor_information_enrichment_job(
    session: AsyncSession,
    job_id: int,
) -> ProfessorInformationEnrichmentJobRead | None:
    job = await _get_information_enrichment_job(session, job_id)
    if job is None:
        return None
    return await serialize_professor_information_enrichment_job(session, job)


async def get_active_professor_information_enrichment_job(
    session: AsyncSession,
    professor_id: int,
) -> ProfessorInformationEnrichmentJobRead | None:
    job = await session.scalar(
        select(CrawlJob)
        .options(selectinload(CrawlJob.current_run))
        .join(
            CrawlCandidateEnrichmentTask,
            CrawlCandidateEnrichmentTask.job_id == CrawlJob.id,
        )
        .where(
            CrawlJob.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value,
            CrawlJob.status.in_(ACTIVE_JOB_STATUSES),
            CrawlCandidateEnrichmentTask.professor_id == professor_id,
        )
        .order_by(CrawlJob.created_at.desc(), CrawlJob.id.desc())
        .limit(1)
    )
    if job is None:
        return None
    return await serialize_professor_information_enrichment_job(session, job)


async def serialize_professor_information_enrichment_job(
    session: AsyncSession,
    job: CrawlJob,
) -> ProfessorInformationEnrichmentJobRead:
    counts = Counter(
        await session.scalars(
            select(CrawlCandidateEnrichmentTask.status).where(
                CrawlCandidateEnrichmentTask.job_id == job.id,
            )
        )
    )
    queued_count = counts[CrawlCandidateEnrichmentTaskStatus.PENDING.value] + counts[
        CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value
    ]
    running_count = counts[CrawlCandidateEnrichmentTaskStatus.PROCESSING.value]
    succeeded_count = counts[CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value]
    failed_count = counts[CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value]
    skipped_count = counts[CrawlCandidateEnrichmentTaskStatus.SKIPPED.value]
    canceled_count = counts[CrawlCandidateEnrichmentTaskStatus.CANCELED.value]
    completed_count = succeeded_count + failed_count + skipped_count + canceled_count
    target_count = sum(counts.values())
    metrics = build_crawl_job_metrics(job)
    current_run = job.current_run
    last_error = job.error_message
    if not last_error and failed_count:
        last_error = await session.scalar(
            select(CrawlCandidateEnrichmentTask.last_error)
            .where(
                CrawlCandidateEnrichmentTask.job_id == job.id,
                CrawlCandidateEnrichmentTask.status
                == CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
                CrawlCandidateEnrichmentTask.last_error.is_not(None),
            )
            .order_by(
                CrawlCandidateEnrichmentTask.updated_at.desc(),
                CrawlCandidateEnrichmentTask.id.desc(),
            )
            .limit(1)
        )
    return ProfessorInformationEnrichmentJobRead(
        id=job.id,
        name=job.display_name or f"信息补全 #{job.id}",
        trigger_mode=(
            CrawlJobTriggerMode.SINGLE.value
            if job.trigger_mode == CrawlJobTriggerMode.SINGLE.value
            else CrawlJobTriggerMode.BATCH.value
        ),
        status=job.status,
        target_count=target_count,
        completed_count=completed_count,
        queued_count=queued_count,
        running_count=running_count,
        succeeded_count=succeeded_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        canceled_count=canceled_count,
        input_tokens=metrics.input_tokens,
        output_tokens=metrics.output_tokens,
        cached_tokens=metrics.cached_tokens,
        total_tokens=metrics.total_tokens,
        llm_profile_id=job.llm_profile_id,
        started_at=current_run.started_at if current_run is not None else None,
        finished_at=current_run.finished_at if current_run is not None else None,
        duration_seconds=metrics.duration_seconds,
        created_at=job.created_at,
        updated_at=job.updated_at,
        deleted_at=job.deleted_at,
        last_error=sanitize_user_visible_error(last_error) if last_error else None,
    )


async def list_professor_information_enrichment_items(
    session: AsyncSession,
    job_id: int,
) -> list[ProfessorInformationEnrichmentItemRead] | None:
    job_exists = await session.scalar(
        select(CrawlJob.id).where(
            CrawlJob.id == job_id,
            CrawlJob.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value,
        )
    )
    if job_exists is None:
        return None
    tasks = list(
        await session.scalars(
            select(CrawlCandidateEnrichmentTask)
            .options(
                selectinload(CrawlCandidateEnrichmentTask.candidate),
                selectinload(CrawlCandidateEnrichmentTask.professor),
            )
            .where(CrawlCandidateEnrichmentTask.job_id == job_id)
            .order_by(CrawlCandidateEnrichmentTask.id.asc())
        )
    )
    usages = list(
        await session.scalars(
            select(CrawlWorkerTokenUsage).where(
                CrawlWorkerTokenUsage.job_id == job_id,
                CrawlWorkerTokenUsage.worker_kind == CrawlWorkerKind.ENRICHMENT.value,
            )
        )
    )
    usage_by_task: dict[str, dict[str, int]] = {}
    for usage in usages:
        totals = usage_by_task.setdefault(
            usage.work_item_id,
            {"input": 0, "output": 0, "cached": 0},
        )
        totals["input"] += int(usage.input_tokens or 0)
        totals["output"] += int(usage.output_tokens or 0)
        totals["cached"] += int(usage.cached_tokens or 0)

    items: list[ProfessorInformationEnrichmentItemRead] = []
    for task in tasks:
        candidate = task.candidate
        professor = task.professor
        usage = usage_by_task.get(str(task.id), {"input": 0, "output": 0, "cached": 0})
        items.append(
            ProfessorInformationEnrichmentItemRead(
                id=task.id,
                job_id=task.job_id,
                professor_id=task.professor_id,
                professor_name=(professor.name if professor is not None else candidate.name),
                professor_email=(professor.email if professor is not None else candidate.email),
                professor_title=(professor.title if professor is not None else candidate.title),
                professor_university=(
                    professor.university if professor is not None else candidate.university
                ),
                professor_school=(professor.school if professor is not None else candidate.school),
                professor_department=(
                    professor.department if professor is not None else candidate.department
                ),
                profile_url=candidate.profile_url,
                status=_public_item_status(task.status),
                enriched_fields=list(task.enriched_fields or []),
                error_message=(
                    sanitize_user_visible_error(task.last_error) if task.last_error else None
                ),
                skip_reason=task.skip_reason,
                input_tokens=usage["input"],
                output_tokens=usage["output"],
                cached_tokens=usage["cached"],
                total_tokens=usage["input"] + usage["output"],
                attempt_count=int(task.attempt_count or 0),
                started_at=task.started_at,
                finished_at=task.finished_at,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
        )
    return items


async def request_professor_information_enrichment_cancel(
    session: AsyncSession,
    job: CrawlJob,
    *,
    event_name: str = "professor_information_enrichment.cancel_requested",
    actor: str | None = None,
) -> None:
    if job.status in DELETABLE_JOB_STATUSES:
        return
    now = utc_now()
    await session.execute(
        update(CrawlCandidateEnrichmentTask)
        .where(
            CrawlCandidateEnrichmentTask.job_id == job.id,
            CrawlCandidateEnrichmentTask.status.in_(ACTIVE_TASK_STATUSES),
        )
        .values(
            status=CrawlCandidateEnrichmentTaskStatus.CANCELED.value,
            worker_id=None,
            claimed_at=None,
            lease_expires_at=None,
            finished_at=now,
        )
    )
    job.status = CrawlJobStatus.CANCELED.value
    job.updated_at = now
    await mark_crawl_job_run_finished(
        session,
        job,
        status=CrawlJobStatus.CANCELED.value,
        now=now,
    )
    metadata: dict[str, object] = {"status": job.status}
    if actor is not None:
        metadata["actor"] = actor
    await record_operation_log(
        session,
        category="professor_information_enrichment",
        event_name=event_name,
        entity_type="crawl_job",
        entity_id=str(job.id),
        metadata=metadata,
    )
    profile_text_cache.discard_job(job_id=job.id)


async def retry_failed_professor_information_enrichment_job(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
) -> int:
    async with session_factory() as session:
        job = await retry_failed_professor_information_enrichment_job_record(session, job_id)
        await session.commit()
        return job.id


async def retry_failed_professor_information_enrichment_job_record(
    session: AsyncSession,
    job_id: int,
    *,
    actor: str | None = None,
) -> CrawlJob:
    """Create a retry job in the caller's transaction without committing it."""

    job = await _get_information_enrichment_job(session, job_id)
    if job is None or not job.task_center_visible:
        raise ValueError("信息补全任务不存在")
    professor_ids = list(
        await session.scalars(
            select(CrawlCandidateEnrichmentTask.professor_id)
            .where(
                CrawlCandidateEnrichmentTask.job_id == job.id,
                CrawlCandidateEnrichmentTask.status.in_(
                    {
                        CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
                        CrawlCandidateEnrichmentTaskStatus.CANCELED.value,
                    }
                ),
                CrawlCandidateEnrichmentTask.professor_id.is_not(None),
            )
            .order_by(CrawlCandidateEnrichmentTask.id.asc())
        )
    )
    if not professor_ids:
        raise ValueError("该任务没有可重试的失败或取消项")
    if job.llm_profile_id is None:
        raise ValueError("原任务缺少模型配置")

    retry_job = await create_professor_information_enrichment_job_record(
        session,
        professor_ids=[int(item) for item in professor_ids if item is not None],
        llm_profile_id=job.llm_profile_id,
        trigger_mode=CrawlJobTriggerMode.BATCH.value,
        name=f"{job.display_name or f'信息补全 #{job.id}'} · 失败重试",
        event_name="professor_information_enrichment.batch_created",
        actor=actor,
    )
    metadata: dict[str, object] = {"source_job_id": job_id}
    if actor is not None:
        metadata["actor"] = actor
    await record_operation_log(
        session,
        category="professor_information_enrichment",
        event_name="professor_information_enrichment.retry_created",
        entity_type="crawl_job",
        entity_id=str(retry_job.id),
        metadata=metadata,
    )
    return retry_job


async def delete_professor_information_enrichment_job_record(
    session: AsyncSession,
    job_id: int,
    *,
    event_name: str = "professor_information_enrichment.deleted",
    actor: str | None = None,
) -> CrawlJob:
    """Move a finished enrichment job to the trash without committing it."""

    job = await _get_information_enrichment_job(session, job_id)
    if job is None or not job.task_center_visible:
        raise ValueError("信息补全任务不存在")
    if job.status not in DELETABLE_JOB_STATUSES:
        raise ValueError("请先取消任务后再删除")
    previous_deleted_at = job.deleted_at
    if job.deleted_at is None:
        job.deleted_at = utc_now()
        job.updated_at = utc_now()
    metadata: dict[str, object] = {
        "status": job.status,
        "previous_deleted_at": (
            previous_deleted_at.isoformat() if previous_deleted_at is not None else None
        ),
    }
    if actor is not None:
        metadata["actor"] = actor
    await record_operation_log(
        session,
        category="professor_information_enrichment",
        event_name=event_name,
        entity_type="crawl_job",
        entity_id=str(job.id),
        metadata=metadata,
    )
    return job


async def restore_professor_information_enrichment_job_record(
    session: AsyncSession,
    job_id: int,
    *,
    event_name: str = "professor_information_enrichment.restored",
    actor: str | None = None,
) -> CrawlJob:
    """Restore an enrichment job from the trash without committing it."""

    job = await _get_information_enrichment_job(session, job_id)
    if job is None or not job.task_center_visible:
        raise ValueError("信息补全任务不存在")
    previous_deleted_at = job.deleted_at
    if job.deleted_at is not None:
        job.deleted_at = None
        job.updated_at = utc_now()
    metadata: dict[str, object] = {
        "status": job.status,
        "previous_deleted_at": (
            previous_deleted_at.isoformat() if previous_deleted_at is not None else None
        ),
    }
    if actor is not None:
        metadata["actor"] = actor
    await record_operation_log(
        session,
        category="professor_information_enrichment",
        event_name=event_name,
        entity_type="crawl_job",
        entity_id=str(job.id),
        metadata=metadata,
    )
    return job


async def finalize_professor_information_enrichment_job(
    session: AsyncSession,
    job: CrawlJob,
    *,
    now: datetime | None = None,
) -> None:
    resolved_now = now or utc_now()
    rows = await session.execute(
        select(CrawlCandidateEnrichmentTask.status, CrawlCandidateEnrichmentTask.last_error).where(
            CrawlCandidateEnrichmentTask.job_id == job.id,
        )
    )
    task_rows = list(rows)
    counts = Counter(status for status, _error in task_rows)
    succeeded = counts[CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value]
    failed = counts[CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value]
    skipped = counts[CrawlCandidateEnrichmentTaskStatus.SKIPPED.value]
    canceled = counts[CrawlCandidateEnrichmentTaskStatus.CANCELED.value]
    total = len(task_rows)
    job.progress_total = total
    job.progress_current = succeeded + failed + skipped + canceled

    if failed > 0 and succeeded == 0:
        final_status = CrawlJobStatus.FAILED.value
    elif succeeded > 0 and (failed > 0 or canceled > 0):
        final_status = CrawlJobStatus.PARTIALLY_COMPLETED.value
    else:
        final_status = CrawlJobStatus.COMPLETED.value

    errors = [error for _status, error in task_rows if error]
    error_message = sanitize_user_visible_error(errors[-1]) if errors else None
    job.status = final_status
    job.error_message = error_message if final_status == CrawlJobStatus.FAILED.value else None
    job.updated_at = resolved_now
    trace = list(job.agent_trace or [])
    trace.append(
        {
            "event_type": "information_enrichment",
            "message": (
                f"导师信息补全完成：成功 {succeeded} 位，失败 {failed} 位，"
                f"跳过 {skipped} 位，取消 {canceled} 位"
            ),
            "created_at": resolved_now.isoformat(),
            "raw": {
                "succeeded_count": succeeded,
                "failed_count": failed,
                "skipped_count": skipped,
                "canceled_count": canceled,
            },
        }
    )
    job.agent_trace = trace[-100:]
    await mark_crawl_job_run_finished(
        session,
        job,
        status=final_status,
        error_message=error_message,
        now=resolved_now,
    )
    await record_operation_log(
        session,
        category="professor_information_enrichment",
        event_name="professor_information_enrichment.completed",
        level="error" if final_status == CrawlJobStatus.FAILED.value else "info",
        message=error_message,
        entity_type="crawl_job",
        entity_id=str(job.id),
        metadata={
            "status": final_status,
            "succeeded_count": succeeded,
            "failed_count": failed,
            "skipped_count": skipped,
            "canceled_count": canceled,
        },
    )
    profile_text_cache.discard_job(job_id=job.id)


async def apply_enrichment_to_professor(
    session: AsyncSession,
    *,
    task: CrawlCandidateEnrichmentTask,
    candidate: CrawlCandidate,
) -> tuple[list[str], str | None]:
    professor_id = task.professor_id or candidate.professor_id
    professor = await session.get(Professor, professor_id) if professor_id is not None else None
    if professor is None:
        raise ValueError("关联导师不存在")
    if professor.archived_at is not None:
        return [], "导师已在回收站"

    enriched_fields: list[str] = []
    if _is_empty_text(professor.email) and candidate.email:
        email = normalize_professor_email(candidate.email)
        duplicate = None
        if email and is_valid_professor_email(email):
            duplicate = await session.scalar(
                select(Professor.id).where(
                    Professor.email == email,
                    Professor.id != professor.id,
                )
            )
        if email and is_valid_professor_email(email) and duplicate is None:
            professor.email = email
            enriched_fields.append("email")

    if _is_empty_text(professor.title) and candidate.title:
        title = normalize_professor_title(candidate.title)
        if title:
            professor.title = title
            enriched_fields.append("title")

    if _is_empty_text(professor.department) and candidate.department:
        department = candidate.department.strip()
        if department:
            professor.department = department
            enriched_fields.append("department")

    if _is_empty_text(professor.research_direction) and candidate.research_direction:
        research_direction = normalize_research_direction(candidate.research_direction)
        if research_direction:
            professor.research_direction = research_direction
            enriched_fields.append("research_direction")

    if not normalize_recent_papers(professor.recent_papers):
        recent_papers = normalize_recent_papers(candidate.recent_papers)
        if recent_papers:
            professor.recent_papers = recent_papers
            enriched_fields.append("recent_papers")

    if enriched_fields:
        professor.updated_at = utc_now()
    await record_operation_log(
        session,
        category="professor_information_enrichment",
        event_name="professor_information_enrichment.item_completed",
        entity_type="professor",
        entity_id=str(professor.id),
        metadata={
            "job_id": task.job_id,
            "task_id": task.id,
            "enriched_fields": enriched_fields,
        },
    )
    return enriched_fields, None


async def _get_information_enrichment_job(
    session: AsyncSession,
    job_id: int,
) -> CrawlJob | None:
    return await session.scalar(
        select(CrawlJob)
        .options(selectinload(CrawlJob.current_run))
        .where(
            CrawlJob.id == job_id,
            CrawlJob.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value,
        )
    )


async def _active_professor_ids(session: AsyncSession, professor_ids: list[int]) -> set[int]:
    return set(
        await session.scalars(
            select(CrawlCandidateEnrichmentTask.professor_id)
            .join(CrawlJob, CrawlJob.id == CrawlCandidateEnrichmentTask.job_id)
            .where(
                CrawlJob.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value,
                CrawlJob.status.in_(ACTIVE_JOB_STATUSES),
                CrawlCandidateEnrichmentTask.professor_id.in_(professor_ids),
            )
        )
    )


def _validate_single_professor(professor: Professor, *, active: bool) -> None:
    if professor.archived_at is not None:
        raise ValueError("回收站中的导师不能发起信息补全")
    if not _is_valid_profile_url(professor.profile_url):
        raise ValueError("请先保存有效的导师主页链接")
    if not get_missing_information_enrichment_fields(professor):
        raise ValueError("该导师资料已完整，无需补全")
    if active:
        raise RuntimeError("该导师已有信息补全正在进行")


def _batch_skip_reason(professor: Professor, *, active: bool) -> str | None:
    if professor.archived_at is not None:
        return "导师已在回收站"
    if not _is_valid_profile_url(professor.profile_url):
        return "缺少有效的导师主页链接"
    if not get_missing_information_enrichment_fields(professor):
        return "资料已完整，无需补全"
    if active:
        return "已有信息补全正在进行"
    return None


def get_missing_information_enrichment_fields(professor: Professor) -> list[str]:
    missing: list[str] = []
    for field in INFORMATION_ENRICHMENT_FIELDS:
        value = getattr(professor, field)
        if field == "recent_papers":
            if not normalize_recent_papers(value):
                missing.append(field)
        elif _is_empty_text(value):
            missing.append(field)
    return missing


def _build_candidate(job_id: int, professor: Professor) -> CrawlCandidate:
    return CrawlCandidate(
        job_id=job_id,
        professor_id=professor.id,
        name=professor.name,
        email=professor.email,
        title=professor.title,
        university=professor.university,
        school=professor.school,
        department=professor.department,
        research_direction=professor.research_direction,
        recent_papers=normalize_recent_papers(professor.recent_papers),
        profile_url=professor.profile_url.strip() if professor.profile_url else None,
        source_url=professor.source_url,
        confidence=1.0,
        source_kind="professor_information_enrichment",
        review_status=CrawlCandidateReviewStatus.MERGED.value,
    )


def _build_display_name(
    professors: list[Professor],
    *,
    trigger_mode: str,
    requested_name: str | None,
    now: datetime,
) -> str:
    if requested_name and requested_name.strip():
        return requested_name.strip()
    if trigger_mode == CrawlJobTriggerMode.SINGLE.value:
        return f"{professors[0].name} · 信息补全"
    return f"信息补全 {now.strftime('%Y-%m-%d %H:%M')}"


def _summarize_location(
    values: list[str | None],
    *,
    fallback: str,
    multiple: str,
) -> str:
    unique = list(dict.fromkeys(value.strip() for value in values if value and value.strip()))
    if not unique:
        return fallback
    if len(unique) == 1:
        return unique[0]
    return multiple


def _is_valid_profile_url(value: str | None) -> bool:
    if not value or not value.strip().startswith(("http://", "https://")):
        return False
    try:
        validate_safe_public_crawl_url(value.strip())
    except ValueError:
        return False
    return True


def _is_empty_text(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _public_item_status(status: str) -> str:
    if status in {
        CrawlCandidateEnrichmentTaskStatus.PENDING.value,
        CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value,
    }:
        return "queued"
    if status == CrawlCandidateEnrichmentTaskStatus.PROCESSING.value:
        return "running"
    if status == CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value:
        return "succeeded"
    if status == CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value:
        return "failed"
    if status == CrawlCandidateEnrichmentTaskStatus.SKIPPED.value:
        return "skipped"
    return "canceled"
