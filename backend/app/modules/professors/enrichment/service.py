from __future__ import annotations

from collections import Counter
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.query_chunks import chunked_values, unique_positive_ids
from app.core.time import local_now, utc_now
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
from app.modules.crawler.public import (
    build_crawl_job_metrics,
    create_initial_crawl_job_run,
    mark_crawl_job_run_finished,
    validate_safe_public_crawl_url,
)
from app.modules.professors.public import (
    is_valid_professor_email,
    normalize_professor_email,
    normalize_professor_title,
    normalize_recent_papers,
    normalize_research_direction,
)
from app.services.operation_logs import (
    record_operation_log,
    sanitize_user_visible_error,
)

from .schemas import (
    ProfessorInformationEnrichmentItemRead,
    ProfessorInformationEnrichmentItemsPageRead,
    ProfessorInformationEnrichmentJobRead,
    ProfessorInformationEnrichmentSkipReasonRead,
)
from .skip_reasons import (
    ALREADY_COMPLETE_SKIP_REASON,
    ENRICHMENT_IN_PROGRESS_SKIP_REASON,
    MISSING_PROFILE_URL_SKIP_REASON,
    PROFESSOR_ARCHIVED_SKIP_REASON,
    UNCLASSIFIED_SKIP_REASON,
    InformationEnrichmentSkipReason,
    resolve_information_enrichment_skip_reason,
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

    requested_ids = unique_positive_ids(professor_ids)
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

    professors: list[Professor] = []
    for professor_id_chunk in chunked_values(requested_ids):
        professors.extend(
            await session.scalars(
                select(Professor).where(Professor.id.in_(professor_id_chunk)),
            ),
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
        now=local_now(),
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
    candidates: list[CrawlCandidate] = []
    skip_reasons: list[str | None] = []
    for professor in ordered_professors:
        skip_reason_definition = _batch_skip_reason(
            professor,
            active=professor.id in active_professor_ids,
        )
        skip_reason = (
            skip_reason_definition.legacy_message
            if skip_reason_definition is not None
            else None
        )
        candidate = _build_candidate(job.id, professor)
        candidates.append(candidate)
        skip_reasons.append(skip_reason)
    session.add_all(candidates)
    await session.flush()

    tasks: list[CrawlCandidateEnrichmentTask] = []
    for professor, candidate, skip_reason in zip(
        ordered_professors,
        candidates,
        skip_reasons,
        strict=True,
    ):
        task_status = (
            CrawlCandidateEnrichmentTaskStatus.SKIPPED.value
            if skip_reason is not None
            else CrawlCandidateEnrichmentTaskStatus.PENDING.value
        )
        tasks.append(
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
    session.add_all(tasks)

    metadata: dict[str, object] = {
        "trigger_mode": trigger_mode,
        "llm_profile_id": llm_profile.id,
        "target_count": len(ordered_professors),
        "queued_count": queued_count,
        "skipped_count": skipped_count,
        "professor_ids": requested_ids[:1_000],
        "professor_ids_truncated": len(requested_ids) > 1_000,
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
    offset: int = 0,
    limit: int = 50,
    status: str | None = None,
    llm_profile_id: int | None = None,
) -> list[ProfessorInformationEnrichmentJobRead]:
    statement = (
        select(CrawlJob)
        .options(selectinload(CrawlJob.current_run))
        .where(
            CrawlJob.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value,
            CrawlJob.task_center_visible.is_(True),
        )
        .order_by(CrawlJob.created_at.desc(), CrawlJob.id.desc())
        .offset(offset)
        .limit(limit)
    )
    if view == "current":
        statement = statement.where(CrawlJob.deleted_at.is_(None))
    elif view == "trash":
        statement = statement.where(CrawlJob.deleted_at.is_not(None))
    else:
        raise ValueError("未知任务视图")
    if status is not None:
        statement = statement.where(CrawlJob.status == status)
    if llm_profile_id is not None:
        statement = statement.where(CrawlJob.llm_profile_id == llm_profile_id)
    jobs = list(await session.scalars(statement))
    return await _serialize_professor_information_enrichment_jobs(session, jobs)


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
    serialized = await _serialize_professor_information_enrichment_jobs(session, [job])
    return serialized[0]


async def _serialize_professor_information_enrichment_jobs(
    session: AsyncSession,
    jobs: list[CrawlJob],
) -> list[ProfessorInformationEnrichmentJobRead]:
    if not jobs:
        return []

    job_ids = [job.id for job in jobs]
    aggregate_rows = (
        await session.execute(
            select(
                CrawlCandidateEnrichmentTask.job_id,
                CrawlCandidateEnrichmentTask.status,
                CrawlCandidateEnrichmentTask.skip_reason,
                func.count(CrawlCandidateEnrichmentTask.id).label("task_count"),
            )
            .where(CrawlCandidateEnrichmentTask.job_id.in_(job_ids))
            .group_by(
                CrawlCandidateEnrichmentTask.job_id,
                CrawlCandidateEnrichmentTask.status,
                CrawlCandidateEnrichmentTask.skip_reason,
            )
        )
    ).all()
    counts_by_job: dict[int, Counter[str]] = {
        job_id: Counter() for job_id in job_ids
    }
    skip_counts_by_job: dict[int, Counter[str]] = {
        job_id: Counter() for job_id in job_ids
    }
    skip_definitions_by_job: dict[
        int,
        dict[str, InformationEnrichmentSkipReason],
    ] = {job_id: {} for job_id in job_ids}
    for row in aggregate_rows:
        task_count = int(row.task_count)
        counts_by_job[row.job_id][row.status] += task_count
        if row.status != CrawlCandidateEnrichmentTaskStatus.SKIPPED.value:
            continue
        reason = (
            resolve_information_enrichment_skip_reason(row.skip_reason)
            or UNCLASSIFIED_SKIP_REASON
        )
        skip_counts_by_job[row.job_id][reason.code] += task_count
        skip_definitions_by_job[row.job_id][reason.code] = reason

    failed_status = CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value
    failed_job_ids = [
        job.id
        for job in jobs
        if not job.error_message and counts_by_job[job.id][failed_status]
    ]
    latest_errors_by_job: dict[int, str] = {}
    if failed_job_ids:
        row_number = func.row_number().over(
            partition_by=CrawlCandidateEnrichmentTask.job_id,
            order_by=(
                CrawlCandidateEnrichmentTask.updated_at.desc(),
                CrawlCandidateEnrichmentTask.id.desc(),
            ),
        ).label("row_number")
        ranked_errors = (
            select(
                CrawlCandidateEnrichmentTask.job_id.label("job_id"),
                CrawlCandidateEnrichmentTask.last_error.label("last_error"),
                row_number,
            )
            .where(
                CrawlCandidateEnrichmentTask.job_id.in_(failed_job_ids),
                CrawlCandidateEnrichmentTask.status == failed_status,
                CrawlCandidateEnrichmentTask.last_error.is_not(None),
            )
            .subquery()
        )
        error_rows = (
            await session.execute(
                select(ranked_errors.c.job_id, ranked_errors.c.last_error).where(
                    ranked_errors.c.row_number == 1,
                )
            )
        ).all()
        latest_errors_by_job = {
            int(row.job_id): str(row.last_error) for row in error_rows
        }

    return [
        _build_professor_information_enrichment_job_read(
            job,
            counts=counts_by_job[job.id],
            skip_reason_counts=skip_counts_by_job[job.id],
            skip_reason_definitions=skip_definitions_by_job[job.id],
            latest_task_error=latest_errors_by_job.get(job.id),
        )
        for job in jobs
    ]


def _build_professor_information_enrichment_job_read(
    job: CrawlJob,
    *,
    counts: Counter[str],
    skip_reason_counts: Counter[str],
    skip_reason_definitions: dict[str, InformationEnrichmentSkipReason],
    latest_task_error: str | None,
) -> ProfessorInformationEnrichmentJobRead:
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
    last_error = job.error_message or latest_task_error
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
        skip_reasons=[
            ProfessorInformationEnrichmentSkipReasonRead(
                code=code,
                count=skip_reason_counts[code],
                message=skip_reason_definitions[code].message,
                recoverable=skip_reason_definitions[code].recoverable,
                suggested_action=skip_reason_definitions[code].suggested_action,
            )
            for code in sorted(skip_reason_counts)
        ],
    )


async def list_professor_information_enrichment_items(
    session: AsyncSession,
    job_id: int,
) -> list[ProfessorInformationEnrichmentItemRead] | None:
    result = await _list_professor_information_enrichment_items(
        session,
        job_id,
        with_total_count=False,
    )
    return None if result is None else result[0]


async def list_professor_information_enrichment_items_page(
    session: AsyncSession,
    job_id: int,
    *,
    cursor: int = 0,
    limit: int = 20,
    status_filter: str | None = None,
) -> ProfessorInformationEnrichmentItemsPageRead | None:
    result = await _list_professor_information_enrichment_items(
        session,
        job_id,
        cursor=cursor,
        limit=limit,
        status_filter=status_filter,
        with_total_count=True,
    )
    if result is None:
        return None
    items, total_count, has_more = result
    return ProfessorInformationEnrichmentItemsPageRead(
        items=items,
        total_count=total_count,
        next_cursor=cursor + limit if has_more else None,
        has_more=has_more,
    )


async def _list_professor_information_enrichment_items(
    session: AsyncSession,
    job_id: int,
    *,
    cursor: int | None = None,
    limit: int | None = None,
    status_filter: str | None = None,
    with_total_count: bool,
) -> tuple[list[ProfessorInformationEnrichmentItemRead], int, bool] | None:
    job_exists = await session.scalar(
        select(CrawlJob.id).where(
            CrawlJob.id == job_id,
            CrawlJob.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value,
        )
    )
    if job_exists is None:
        return None

    filters = [CrawlCandidateEnrichmentTask.job_id == job_id]
    if status_filter is not None:
        filters.append(
            CrawlCandidateEnrichmentTask.status.in_(
                _internal_item_statuses_for_filter(status_filter)
            )
        )
    total_count = 0
    if with_total_count:
        total_count = int(
            await session.scalar(
                select(func.count())
                .select_from(CrawlCandidateEnrichmentTask)
                .where(*filters)
            )
            or 0
        )
    statement = (
        select(CrawlCandidateEnrichmentTask)
        .options(
            selectinload(CrawlCandidateEnrichmentTask.candidate),
            selectinload(CrawlCandidateEnrichmentTask.professor),
        )
        .where(*filters)
        .order_by(CrawlCandidateEnrichmentTask.id.asc())
    )
    if cursor is not None:
        statement = statement.offset(cursor)
    if limit is not None:
        statement = statement.limit(limit + 1)
    tasks = list(await session.scalars(statement))
    has_more = limit is not None and len(tasks) > limit
    page_tasks = tasks[:limit] if limit is not None else tasks
    usage_by_task = await _load_item_usage_totals(session, job_id, page_tasks)
    return (
        [
            _serialize_professor_information_enrichment_item(
                task,
                usage=usage_by_task.get(
                    str(task.id),
                    {"input": 0, "output": 0, "cached": 0},
                ),
            )
            for task in page_tasks
        ],
        total_count,
        has_more,
    )


async def _load_item_usage_totals(
    session: AsyncSession,
    job_id: int,
    tasks: list[CrawlCandidateEnrichmentTask],
) -> dict[str, dict[str, int]]:
    task_ids = [str(task.id) for task in tasks]
    if not task_ids:
        return {}
    rows = await session.execute(
        select(
            CrawlWorkerTokenUsage.work_item_id,
            func.coalesce(func.sum(CrawlWorkerTokenUsage.input_tokens), 0),
            func.coalesce(func.sum(CrawlWorkerTokenUsage.output_tokens), 0),
            func.coalesce(func.sum(CrawlWorkerTokenUsage.cached_tokens), 0),
        )
        .where(
            CrawlWorkerTokenUsage.job_id == job_id,
            CrawlWorkerTokenUsage.worker_kind == CrawlWorkerKind.ENRICHMENT.value,
            CrawlWorkerTokenUsage.work_item_id.in_(task_ids),
        )
        .group_by(CrawlWorkerTokenUsage.work_item_id)
    )
    return {
        work_item_id: {
            "input": int(input_tokens),
            "output": int(output_tokens),
            "cached": int(cached_tokens),
        }
        for work_item_id, input_tokens, output_tokens, cached_tokens in rows
    }


def _serialize_professor_information_enrichment_item(
    task: CrawlCandidateEnrichmentTask,
    *,
    usage: dict[str, int],
) -> ProfessorInformationEnrichmentItemRead:
    candidate = task.candidate
    professor = task.professor
    skip_reason = resolve_information_enrichment_skip_reason(task.skip_reason)
    if (
        skip_reason is None
        and task.status == CrawlCandidateEnrichmentTaskStatus.SKIPPED.value
    ):
        skip_reason = UNCLASSIFIED_SKIP_REASON
    return ProfessorInformationEnrichmentItemRead(
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
        error_message=sanitize_user_visible_error(task.last_error) if task.last_error else None,
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
        skip_reason_code=skip_reason.code if skip_reason is not None else None,
        skip_recoverable=skip_reason.recoverable if skip_reason is not None else None,
        suggested_action=skip_reason.suggested_action if skip_reason is not None else None,
    )


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
    active_professor_ids: set[int] = set()
    for professor_id_chunk in chunked_values(unique_positive_ids(professor_ids)):
        active_professor_ids.update(
            await session.scalars(
                select(CrawlCandidateEnrichmentTask.professor_id)
                .join(CrawlJob, CrawlJob.id == CrawlCandidateEnrichmentTask.job_id)
                .where(
                    CrawlJob.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value,
                    CrawlJob.status.in_(ACTIVE_JOB_STATUSES),
                    CrawlCandidateEnrichmentTask.professor_id.in_(professor_id_chunk),
                )
            ),
        )
    return active_professor_ids


def _validate_single_professor(professor: Professor, *, active: bool) -> None:
    if professor.archived_at is not None:
        raise ValueError("回收站中的导师不能发起信息补全")
    if not _is_valid_profile_url(professor.profile_url):
        raise ValueError("请先保存有效的导师主页链接")
    if not get_missing_information_enrichment_fields(professor):
        raise ValueError("该导师资料已完整，无需补全")
    if active:
        raise RuntimeError("该导师已有信息补全正在进行")


def _batch_skip_reason(
    professor: Professor,
    *,
    active: bool,
) -> InformationEnrichmentSkipReason | None:
    if professor.archived_at is not None:
        return PROFESSOR_ARCHIVED_SKIP_REASON
    if not _is_valid_profile_url(professor.profile_url):
        return MISSING_PROFILE_URL_SKIP_REASON
    if not get_missing_information_enrichment_fields(professor):
        return ALREADY_COMPLETE_SKIP_REASON
    if active:
        return ENRICHMENT_IN_PROGRESS_SKIP_REASON
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


def _internal_item_statuses_for_filter(status: str) -> tuple[str, ...]:
    statuses = {
        "queued": (
            CrawlCandidateEnrichmentTaskStatus.PENDING.value,
            CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value,
        ),
        "running": (CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,),
        "succeeded": (CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value,),
        "failed": (CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,),
        "skipped": (CrawlCandidateEnrichmentTaskStatus.SKIPPED.value,),
        "canceled": (CrawlCandidateEnrichmentTaskStatus.CANCELED.value,),
    }.get(status)
    if statuses is None:
        raise ValueError("未知信息补全导师状态")
    return statuses
