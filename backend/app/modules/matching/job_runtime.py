from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, insert, inspect, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import NO_VALUE

from app.core.query_chunks import chunked_values, unique_positive_ids
from app.core.sqlite_diagnostics import sqlite_lock_user_message
from app.core.time import local_now, utc_now
from app.core.config import get_settings
from app.models import (
    IdentityProfile,
    LLMProfile,
    MatchAnalysisJob,
    MatchAnalysisJobItem,
    MatchAnalysisJobItemStatus,
    MatchAnalysisJobStatus,
    MatchAnalysisRun,
    Professor,
)
from app.services.match_results import (
    load_resolved_match_results,
    resolve_identity_match_scope,
)
from app.services.operation_logs import record_operation_log

from .schemas import (
    MatchAnalysisJobItemRead,
    MatchAnalysisJobRead,
    MatchAnalysisSelectionSummaryRead,
)
from .task_analysis import (
    MatchAnalysisAlreadyRunningError,
    MatchCalculationCanceledError,
    calculate_identity_professor_match,
)

_MATCH_ANALYSIS_CANCEL_POLL_SECONDS = 0.2
_MATCH_ANALYSIS_ITEM_LEASE_SECONDS = 60
_MATCH_ANALYSIS_HEARTBEAT_SECONDS = 20
_MATCH_ANALYSIS_TIMEOUT_HEADROOM_SECONDS = 30
_MATCH_ANALYSIS_CANCEL_GRACE_SECONDS = 1.0
_DETACHED_MATCH_ANALYSIS_TASKS: set[asyncio.Task[object]] = set()
logger = logging.getLogger(__name__)


class _MatchAnalysisJobCanceled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _MatchAnalysisItemClaim:
    job_id: int
    item_id: int
    claim_id: str


async def create_match_analysis_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: int,
    llm_profile_id: int,
    professor_ids: list[int],
    name: str | None = None,
    skip_existing: bool = False,
) -> MatchAnalysisJob:
    async with session_factory() as session:
        job = await create_match_analysis_job_record(
            session,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            professor_ids=professor_ids,
            name=name,
            skip_existing=skip_existing,
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
    skip_existing: bool = False,
) -> MatchAnalysisJob:
    """Create a job in the caller's transaction without committing it."""

    unique_professor_ids = unique_positive_ids(professor_ids)
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

    professors = await _load_active_match_analysis_professors(
        session,
        unique_professor_ids,
    )
    if not professors:
        raise ValueError("没有可分析的导师")

    skipped_existing_count = 0
    if skip_existing:
        resolved_matches = await load_resolved_match_results(
            session,
            active_identity_id=identity_id,
            match_source_identity_id=match_scope.source_identity_id,
            professor_ids=[professor.id for professor in professors],
        )
        scored_professor_ids = set(resolved_matches.by_professor_id)
        skipped_existing_count = sum(
            professor.id in scored_professor_ids
            for professor in professors
        )
        professors = [
            professor
            for professor in professors
            if professor.id not in scored_professor_ids
        ]
        if not professors:
            raise ValueError("已选导师都已有匹配分，无需重复分析")

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
    item_rows: list[dict[str, object]] = []
    for professor in professors:
        if has_evidence[professor.id]:
            item_status = MatchAnalysisJobItemStatus.QUEUED.value
            skip_reason = None
            finished_at = None
            queued_count += 1
        else:
            item_status = MatchAnalysisJobItemStatus.SKIPPED.value
            skip_reason = "缺少研究方向或近期论文"
            finished_at = now
            skipped_count += 1
        item_rows.append(
            {
                "job_id": job.id,
                "professor_id": professor.id,
                "email_task_id": None,
                "status": item_status,
                "skip_reason": skip_reason,
                "finished_at": finished_at,
                "created_at": now,
                "updated_at": now,
            },
        )
    for item_row_chunk in chunked_values(item_rows):
        await session.execute(insert(MatchAnalysisJobItem), list(item_row_chunk))

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
        "skipped_existing_count": skipped_existing_count,
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


async def summarize_match_analysis_selection(
    session: AsyncSession,
    *,
    identity_id: int,
    professor_ids: list[int],
) -> MatchAnalysisSelectionSummaryRead:
    unique_professor_ids = unique_positive_ids(professor_ids)
    professors = await _load_active_match_analysis_professors(
        session,
        unique_professor_ids,
    )
    resolved_matches = await load_resolved_match_results(
        session,
        active_identity_id=identity_id,
        professor_ids=[professor.id for professor in professors],
    )
    analyzable_professor_ids = {
        professor.id
        for professor in professors
        if _has_professor_match_evidence(professor)
    }
    already_scored_count = len(
        analyzable_professor_ids.intersection(
            resolved_matches.by_professor_id,
        ),
    )
    analyzable_count = len(analyzable_professor_ids)
    return MatchAnalysisSelectionSummaryRead(
        selected_count=len(professors),
        analyzable_count=analyzable_count,
        missing_evidence_count=len(professors) - analyzable_count,
        already_scored_count=already_scored_count,
        unscored_analyzable_count=analyzable_count - already_scored_count,
    )


async def _load_active_match_analysis_professors(
    session: AsyncSession,
    professor_ids: list[int],
) -> list[Professor]:
    professors: list[Professor] = []
    for professor_id_chunk in chunked_values(professor_ids):
        professors.extend(
            await session.scalars(
                select(Professor).where(
                    Professor.id.in_(professor_id_chunk),
                    Professor.archived_at.is_(None),
                ),
            ),
        )
    professors.sort(key=lambda professor: professor.id)
    return professors


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
    await _recover_expired_match_analysis_items(session_factory)
    await _finalize_cancel_requested_match_analysis_jobs(session_factory)
    first_claim = await _claim_next_match_analysis_item(session_factory)
    if first_claim is None:
        return 0
    await _run_claimed_match_analysis_item(session_factory, first_claim)
    await _refresh_match_analysis_job_summary(session_factory, first_claim.job_id)

    claims = [first_claim]
    remaining_claims: list[_MatchAnalysisItemClaim] = []
    for _ in range(max(0, item_concurrency - 1)):
        claim = await _claim_next_match_analysis_item(session_factory)
        if claim is None:
            break
        claims.append(claim)
        remaining_claims.append(claim)
    if remaining_claims:
        await asyncio.gather(
            *(
                _run_claimed_match_analysis_item(session_factory, claim)
                for claim in remaining_claims
            )
        )
        for job_id in dict.fromkeys(claim.job_id for claim in remaining_claims):
            await _refresh_match_analysis_job_summary(session_factory, job_id)
    return len(set(claim.job_id for claim in claims))


async def _finalize_cancel_requested_match_analysis_jobs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utc_now()
    async with session_factory() as session:
        job_ids = list(
            await session.scalars(
                select(MatchAnalysisJob.id).where(
                    MatchAnalysisJob.cancel_requested_at.is_not(None),
                    MatchAnalysisJob.status.in_(
                        [
                            MatchAnalysisJobStatus.QUEUED.value,
                            MatchAnalysisJobStatus.RUNNING.value,
                        ]
                    ),
                    MatchAnalysisJob.deleted_at.is_(None),
                )
            )
        )
        if job_ids:
            for job_id_chunk in chunked_values(job_ids):
                await session.execute(
                    update(MatchAnalysisJobItem)
                    .where(
                        MatchAnalysisJobItem.job_id.in_(job_id_chunk),
                        MatchAnalysisJobItem.status
                        == MatchAnalysisJobItemStatus.QUEUED.value,
                    )
                    .values(
                        status=MatchAnalysisJobItemStatus.CANCELED.value,
                        finished_at=now,
                        updated_at=now,
                    )
                )
            await session.commit()
    for job_id in job_ids:
        await _refresh_match_analysis_job_summary(session_factory, job_id)


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
        await session.execute(
            update(MatchAnalysisJobItem)
            .where(
                MatchAnalysisJobItem.job_id == job.id,
                MatchAnalysisJobItem.status
                == MatchAnalysisJobItemStatus.QUEUED.value,
            )
            .values(
                status=MatchAnalysisJobItemStatus.CANCELED.value,
                finished_at=now,
                updated_at=now,
            )
        )
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


async def _claim_next_match_analysis_item(
    session_factory: async_sessionmaker[AsyncSession],
) -> _MatchAnalysisItemClaim | None:
    active_statuses = [
        MatchAnalysisJobStatus.QUEUED.value,
        MatchAnalysisJobStatus.RUNNING.value,
    ]
    for _ in range(5):
        async with session_factory() as session:
            candidate = (
                await session.execute(
                    select(MatchAnalysisJobItem.id, MatchAnalysisJobItem.job_id)
                    .join(
                        MatchAnalysisJob,
                        MatchAnalysisJob.id == MatchAnalysisJobItem.job_id,
                    )
                    .where(
                        MatchAnalysisJobItem.status
                        == MatchAnalysisJobItemStatus.QUEUED.value,
                        MatchAnalysisJob.status.in_(active_statuses),
                        MatchAnalysisJob.cancel_requested_at.is_(None),
                        MatchAnalysisJob.deleted_at.is_(None),
                    )
                    .order_by(
                        MatchAnalysisJob.item_last_dispatched_at.is_not(None).asc(),
                        MatchAnalysisJob.item_last_dispatched_at.asc(),
                        MatchAnalysisJob.created_at.asc(),
                        MatchAnalysisJob.id.asc(),
                        MatchAnalysisJobItem.id.asc(),
                    )
                    .limit(1)
                )
            ).first()
            if candidate is None:
                return None

            item_id, job_id = int(candidate.id), int(candidate.job_id)
            now = utc_now()
            claim_id = str(uuid.uuid4())
            transition = await session.execute(
                update(MatchAnalysisJobItem)
                .where(
                    MatchAnalysisJobItem.id == item_id,
                    MatchAnalysisJobItem.status
                    == MatchAnalysisJobItemStatus.QUEUED.value,
                    MatchAnalysisJobItem.job_id.in_(
                        select(MatchAnalysisJob.id).where(
                            MatchAnalysisJob.status.in_(active_statuses),
                            MatchAnalysisJob.cancel_requested_at.is_(None),
                            MatchAnalysisJob.deleted_at.is_(None),
                        )
                    ),
                )
                .values(
                    status=MatchAnalysisJobItemStatus.RUNNING.value,
                    claim_id=claim_id,
                    claimed_at=now,
                    lease_expires_at=now
                    + timedelta(seconds=_MATCH_ANALYSIS_ITEM_LEASE_SECONDS),
                    attempt_count=func.coalesce(
                        MatchAnalysisJobItem.attempt_count,
                        0,
                    )
                    + 1,
                    started_at=now,
                    finished_at=None,
                    error_message=None,
                    skip_reason=None,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if transition.rowcount != 1:
                await session.rollback()
                continue
            await session.execute(
                update(MatchAnalysisJob)
                .where(
                    MatchAnalysisJob.id == job_id,
                    MatchAnalysisJob.status.in_(active_statuses),
                )
                .values(
                    status=MatchAnalysisJobStatus.RUNNING.value,
                    started_at=func.coalesce(MatchAnalysisJob.started_at, now),
                    finished_at=None,
                    item_last_dispatched_at=now,
                    last_error=None,
                    updated_at=now,
                )
            )
            await session.commit()
            return _MatchAnalysisItemClaim(
                job_id=job_id,
                item_id=item_id,
                claim_id=claim_id,
            )
    return None


async def _recover_expired_match_analysis_items(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    now = utc_now()
    recovered_job_ids: set[int] = set()
    recovered = 0
    async with session_factory() as session:
        rows = list(
            await session.execute(
                select(
                    MatchAnalysisJobItem.id,
                    MatchAnalysisJobItem.job_id,
                    MatchAnalysisJobItem.professor_id,
                    MatchAnalysisJobItem.claim_id,
                    MatchAnalysisJob.cancel_requested_at,
                    MatchAnalysisJob.match_source_identity_id,
                )
                .join(
                    MatchAnalysisJob,
                    MatchAnalysisJob.id == MatchAnalysisJobItem.job_id,
                )
                .where(
                    MatchAnalysisJobItem.status
                    == MatchAnalysisJobItemStatus.RUNNING.value,
                    or_(
                        MatchAnalysisJobItem.lease_expires_at.is_(None),
                        MatchAnalysisJobItem.lease_expires_at <= now,
                    ),
                )
            )
        )
        for row in rows:
            next_status = (
                MatchAnalysisJobItemStatus.CANCELED.value
                if row.cancel_requested_at is not None
                else MatchAnalysisJobItemStatus.QUEUED.value
            )
            transition = await session.execute(
                update(MatchAnalysisJobItem)
                .where(
                    MatchAnalysisJobItem.id == row.id,
                    MatchAnalysisJobItem.status
                    == MatchAnalysisJobItemStatus.RUNNING.value,
                    MatchAnalysisJobItem.claim_id == row.claim_id,
                    or_(
                        MatchAnalysisJobItem.lease_expires_at.is_(None),
                        MatchAnalysisJobItem.lease_expires_at <= now,
                    ),
                )
                .values(
                    status=next_status,
                    claim_id=None,
                    claimed_at=None,
                    lease_expires_at=None,
                    finished_at=(
                        now
                        if next_status == MatchAnalysisJobItemStatus.CANCELED.value
                        else None
                    ),
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if transition.rowcount != 1:
                continue
            recovered += 1
            recovered_job_ids.add(int(row.job_id))
            if row.match_source_identity_id is not None:
                await session.execute(
                    update(MatchAnalysisRun)
                    .where(
                        MatchAnalysisRun.identity_id == row.match_source_identity_id,
                        MatchAnalysisRun.professor_id == row.professor_id,
                        MatchAnalysisRun.status == "running",
                    )
                    .values(
                        status="failed",
                        success=False,
                        error_kind="interrupted",
                        error_message="匹配分析工作项租约过期后恢复",
                        finished_at=now,
                    )
                )
        await session.commit()

    for job_id in recovered_job_ids:
        await _refresh_match_analysis_job_summary(session_factory, job_id)
    return recovered


async def _run_claimed_match_analysis_item(
    session_factory: async_sessionmaker[AsyncSession],
    claim: _MatchAnalysisItemClaim,
) -> None:
    work_task = asyncio.create_task(
        _run_match_analysis_job_item(session_factory, claim)
    )
    heartbeat_task = asyncio.create_task(
        _run_match_analysis_item_heartbeat(session_factory, claim)
    )
    try:
        done, _ = await asyncio.wait(
            {work_task, heartbeat_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        await _cancel_task_with_grace(work_task)
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        raise
    if work_task in done:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
        await work_task
        return

    claim_is_current = False
    try:
        claim_is_current = await heartbeat_task
    except Exception:
        logger.exception(
            "匹配分析租约心跳异常，停止旧 claim：item=%s",
            claim.item_id,
        )
    if not claim_is_current:
        await _cancel_task_with_grace(work_task)


async def _cancel_task_with_grace(task: asyncio.Task[object]) -> None:
    if task.done():
        with suppress(asyncio.CancelledError, Exception):
            task.result()
        return
    if task.cancelling() == 0:
        task.cancel()
    done, _ = await asyncio.wait(
        {task},
        timeout=_MATCH_ANALYSIS_CANCEL_GRACE_SECONDS,
    )
    if task in done:
        with suppress(asyncio.CancelledError, Exception):
            task.result()
        return
    _DETACHED_MATCH_ANALYSIS_TASKS.add(task)
    task.add_done_callback(_consume_detached_task_result)


def _consume_detached_task_result(task: asyncio.Task[object]) -> None:
    _DETACHED_MATCH_ANALYSIS_TASKS.discard(task)
    with suppress(asyncio.CancelledError, Exception):
        task.result()


async def _run_match_analysis_item_heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    claim: _MatchAnalysisItemClaim,
) -> bool:
    while True:
        await asyncio.sleep(_MATCH_ANALYSIS_HEARTBEAT_SECONDS)
        if not await _renew_match_analysis_item_claim(session_factory, claim):
            return False


async def _renew_match_analysis_item_claim(
    session_factory: async_sessionmaker[AsyncSession],
    claim: _MatchAnalysisItemClaim,
) -> bool:
    now = utc_now()
    async with session_factory() as session:
        result = await session.execute(
            update(MatchAnalysisJobItem)
            .where(
                MatchAnalysisJobItem.id == claim.item_id,
                MatchAnalysisJobItem.status
                == MatchAnalysisJobItemStatus.RUNNING.value,
                MatchAnalysisJobItem.claim_id == claim.claim_id,
                MatchAnalysisJobItem.lease_expires_at > now,
            )
            .values(
                lease_expires_at=now
                + timedelta(seconds=_MATCH_ANALYSIS_ITEM_LEASE_SECONDS),
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await session.rollback()
            return False
        await session.commit()
        return True


async def _match_analysis_claim_is_current(
    session_factory: async_sessionmaker[AsyncSession],
    claim: _MatchAnalysisItemClaim,
) -> bool:
    now = utc_now()
    async with session_factory() as session:
        current = await session.scalar(
            select(MatchAnalysisJobItem.id).where(
                MatchAnalysisJobItem.id == claim.item_id,
                MatchAnalysisJobItem.status
                == MatchAnalysisJobItemStatus.RUNNING.value,
                MatchAnalysisJobItem.claim_id == claim.claim_id,
                MatchAnalysisJobItem.lease_expires_at > now,
            )
        )
        return current is not None


async def _run_match_analysis_job_item(
    session_factory: async_sessionmaker[AsyncSession],
    claim: _MatchAnalysisItemClaim,
) -> None:
    async with session_factory() as session:
        job = await session.get(MatchAnalysisJob, claim.job_id)
        item = await session.get(
            MatchAnalysisJobItem,
            claim.item_id,
            options=[selectinload(MatchAnalysisJobItem.email_task)],
        )
        if (
            job is None
            or item is None
            or item.status != MatchAnalysisJobItemStatus.RUNNING.value
            or item.claim_id != claim.claim_id
        ):
            return
        active_identity_id = job.identity_id
        match_source_identity_id = job.match_source_identity_id
        llm_profile_id = job.llm_profile_id
        professor_id = item.professor_id

    if job.cancel_requested_at is not None:
        await _mark_item_canceled(session_factory, claim)
        return
    if match_source_identity_id is None:
        await _mark_item_skipped(
            session_factory,
            claim,
            skip_reason="匹配依据身份已删除，无法继续分析",
        )
        return
    timeout_seconds = max(
        1,
        get_settings().llm_request_timeout_seconds
        + _MATCH_ANALYSIS_TIMEOUT_HEADROOM_SECONDS,
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            result = await _calculate_identity_professor_match_until_canceled(
                session_factory,
                claim,
                active_identity_id=active_identity_id,
                professor_id=professor_id,
                llm_profile_id=llm_profile_id,
                match_source_identity_id=match_source_identity_id,
            )
    except TimeoutError:
        await _fail_timed_out_match_analysis_run(session_factory, claim)
        await _mark_item_failed(
            session_factory,
            claim,
            error_message=f"匹配分析超过 {timeout_seconds} 秒，已停止",
        )
        return
    except (_MatchAnalysisJobCanceled, MatchCalculationCanceledError):
        await _mark_item_canceled(session_factory, claim)
        return
    except MatchAnalysisAlreadyRunningError as exc:
        await _mark_item_skipped(session_factory, claim, skip_reason=str(exc))
        return
    except ValueError as exc:
        await _mark_item_skipped(session_factory, claim, skip_reason=str(exc))
        return
    except Exception as exc:
        logger.exception(
            "匹配分析工作项执行失败：job=%s item=%s",
            claim.job_id,
            claim.item_id,
        )
        await _mark_item_failed(
            session_factory,
            claim,
            error_message=sqlite_lock_user_message(exc) or str(exc),
        )
        return

    if not await _match_analysis_claim_is_current(session_factory, claim):
        return
    if await _is_match_analysis_job_cancel_requested(
        session_factory,
        claim.job_id,
    ):
        await _mark_item_canceled(session_factory, claim)
        return
    run_error = await _match_analysis_run_error(
        session_factory,
        result.run_id,
    )
    if run_error is not None:
        await _mark_item_failed(
            session_factory,
            claim,
            error_message=run_error,
        )
        return
    await _mark_item_succeeded(
        session_factory,
        claim,
        run_id=result.run_id,
        prompt_tokens=result.usage.prompt_tokens or 0,
        completion_tokens=result.usage.completion_tokens or 0,
        cached_tokens=result.usage.cached_tokens or 0,
        total_tokens=result.usage.total_tokens or 0,
    )


async def _fail_timed_out_match_analysis_run(
    session_factory: async_sessionmaker[AsyncSession],
    claim: _MatchAnalysisItemClaim,
) -> None:
    now = utc_now()
    current_item = (
        select(
            MatchAnalysisJobItem.professor_id,
            MatchAnalysisJobItem.claimed_at,
            MatchAnalysisJob.match_source_identity_id,
        )
        .join(MatchAnalysisJob, MatchAnalysisJob.id == MatchAnalysisJobItem.job_id)
        .where(
            MatchAnalysisJobItem.id == claim.item_id,
            MatchAnalysisJobItem.status == MatchAnalysisJobItemStatus.RUNNING.value,
            MatchAnalysisJobItem.claim_id == claim.claim_id,
        )
    )
    professor_id = current_item.with_only_columns(
        MatchAnalysisJobItem.professor_id
    ).scalar_subquery()
    match_source_identity_id = current_item.with_only_columns(
        MatchAnalysisJob.match_source_identity_id
    ).scalar_subquery()
    claimed_at = current_item.with_only_columns(
        MatchAnalysisJobItem.claimed_at,
    ).scalar_subquery()
    async with session_factory() as session:
        await session.execute(
            update(MatchAnalysisRun)
            .where(
                MatchAnalysisRun.identity_id == match_source_identity_id,
                MatchAnalysisRun.professor_id == professor_id,
                MatchAnalysisRun.status == "running",
                MatchAnalysisRun.started_at >= claimed_at,
            )
            .values(
                status="failed",
                success=False,
                error_kind="timeout",
                error_message="匹配分析工作项总超时",
                finished_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()


async def _match_analysis_run_error(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: int | None,
) -> str | None:
    if run_id is None:
        return "匹配分析未产生运行结果"
    async with session_factory() as session:
        run = await session.get(MatchAnalysisRun, run_id)
        if run is None:
            return "匹配分析运行记录不存在"
        if run.status == "succeeded" and run.success:
            return None
        return run.error_message or "匹配分析失败"


async def _calculate_identity_professor_match_until_canceled(
    session_factory: async_sessionmaker[AsyncSession],
    claim: _MatchAnalysisItemClaim,
    *,
    active_identity_id: int,
    professor_id: int,
    llm_profile_id: int,
    match_source_identity_id: int,
):
    async def cancel_requested() -> bool:
        return (
            await _is_match_analysis_job_cancel_requested(
                session_factory,
                claim.job_id,
            )
            or not await _match_analysis_claim_is_current(session_factory, claim)
        )

    calculation_task = asyncio.create_task(
        calculate_identity_professor_match(
            session_factory,
            active_identity_id=active_identity_id,
            professor_id=professor_id,
            llm_profile_id=llm_profile_id,
            match_source_identity_id=match_source_identity_id,
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
                await _cancel_task_with_grace(calculation_task)
                raise _MatchAnalysisJobCanceled("匹配分析任务已取消或租约已失效")
    finally:
        if not calculation_task.done():
            await _cancel_task_with_grace(calculation_task)


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
    claim: _MatchAnalysisItemClaim,
    *,
    run_id: int | None,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int,
    total_tokens: int,
) -> None:
    async with session_factory() as session:
        item = await session.get(MatchAnalysisJobItem, claim.item_id)
        if item is None:
            return
        now = utc_now()
        transition = await session.execute(
            update(MatchAnalysisJobItem)
            .where(
                MatchAnalysisJobItem.id == claim.item_id,
                MatchAnalysisJobItem.status == MatchAnalysisJobItemStatus.RUNNING.value,
                MatchAnalysisJobItem.claim_id == claim.claim_id,
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
                claim_id=None,
                claimed_at=None,
                lease_expires_at=None,
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
    claim: _MatchAnalysisItemClaim,
) -> None:
    async with session_factory() as session:
        item = await session.get(MatchAnalysisJobItem, claim.item_id)
        if item is None:
            return
        now = utc_now()
        transition = await session.execute(
            update(MatchAnalysisJobItem)
            .where(
                MatchAnalysisJobItem.id == claim.item_id,
                MatchAnalysisJobItem.status
                == MatchAnalysisJobItemStatus.RUNNING.value,
                MatchAnalysisJobItem.claim_id == claim.claim_id,
            )
            .values(
                status=MatchAnalysisJobItemStatus.CANCELED.value,
                error_message=None,
                skip_reason=None,
                claim_id=None,
                claimed_at=None,
                lease_expires_at=None,
                finished_at=now,
                updated_at=now,
            )
        )
        await session.commit()


async def _mark_item_skipped(
    session_factory: async_sessionmaker[AsyncSession],
    claim: _MatchAnalysisItemClaim,
    *,
    skip_reason: str,
) -> None:
    async with session_factory() as session:
        item = await session.get(MatchAnalysisJobItem, claim.item_id)
        if item is None:
            return
        now = utc_now()
        transition = await session.execute(
            update(MatchAnalysisJobItem)
            .where(
                MatchAnalysisJobItem.id == claim.item_id,
                MatchAnalysisJobItem.status
                == MatchAnalysisJobItemStatus.RUNNING.value,
                MatchAnalysisJobItem.claim_id == claim.claim_id,
            )
            .values(
                status=MatchAnalysisJobItemStatus.SKIPPED.value,
                skip_reason=skip_reason,
                error_message=None,
                claim_id=None,
                claimed_at=None,
                lease_expires_at=None,
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
    claim: _MatchAnalysisItemClaim,
    *,
    error_message: str,
) -> None:
    async with session_factory() as session:
        item = await session.get(MatchAnalysisJobItem, claim.item_id)
        if item is None:
            return
        now = utc_now()
        transition = await session.execute(
            update(MatchAnalysisJobItem)
            .where(
                MatchAnalysisJobItem.id == claim.item_id,
                MatchAnalysisJobItem.status == MatchAnalysisJobItemStatus.RUNNING.value,
                MatchAnalysisJobItem.claim_id == claim.claim_id,
            )
            .values(
                status=MatchAnalysisJobItemStatus.FAILED.value,
                error_message=error_message,
                claim_id=None,
                claimed_at=None,
                lease_expires_at=None,
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
        job.finished_at = None

        if queued_count > 0 or running_count > 0:
            job.status = (
                MatchAnalysisJobStatus.RUNNING.value
                if running_count > 0
                else MatchAnalysisJobStatus.QUEUED.value
            )
            await session.commit()
            return

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
