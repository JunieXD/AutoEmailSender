from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import uuid
from collections.abc import Awaitable
from datetime import datetime, timedelta
from urllib.parse import urlparse

from app.core.time import utc_now

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    CrawlCandidate,
    CrawlCandidateEnrichmentTask,
    CrawlCandidateEnrichmentTaskStatus,
    CrawlJob,
    CrawlJobKind,
    CrawlJobRun,
    CrawlJobStatus,
    CrawlPageChunk,
    CrawlPageChunkStatus,
    CrawlPageFetchState,
    CrawlPageTask,
    CrawlPageTaskStatus,
)
from .models import CrawlerV2ClaimedWork, CrawlerV2WorkerConfig, CrawlerV2WorkKind
from .profile_text_cache import profile_text_cache
from app.modules.system.public import get_runtime_settings
from app.modules.crawler.candidate_identity import (
    canonical_candidate_clause,
    consolidate_job_candidates,
)
from ..jobs.runs import (
    mark_crawl_job_run_finished,
    mark_crawl_job_run_queued,
    mark_crawl_job_run_running,
)
from app.modules.llm.public import format_llm_runtime_error_for_user
from .lease import CrawlerV2ClaimFence, renew_crawler_v2_claim

_ACTIVE_JOB_STATUSES = {CrawlJobStatus.QUEUED.value, CrawlJobStatus.RUNNING.value}
_PAUSED_JOB_STATUSES = {CrawlJobStatus.PAUSED.value, CrawlJobStatus.CANCELED.value}
ZERO_CANDIDATE_BROWSER_RETRY_REASON = "zero_candidates_force_browser"
_CLAIM_LOCK = asyncio.Lock()
logger = logging.getLogger(__name__)


async def ensure_job_active(session: AsyncSession, job_id: int) -> bool:
    job = await session.get(CrawlJob, job_id)
    return job is not None and job.status in _ACTIVE_JOB_STATUSES


async def claim_next_v2_work(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    worker_id: str,
    config: CrawlerV2WorkerConfig | None = None,
) -> CrawlerV2ClaimedWork:
    async with _CLAIM_LOCK:
        return await _claim_next_v2_work_locked(
            session_factory,
            worker_id=worker_id,
            config=config,
        )


async def _claim_next_v2_work_locked(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    worker_id: str,
    config: CrawlerV2WorkerConfig | None,
) -> CrawlerV2ClaimedWork:
    config = config or CrawlerV2WorkerConfig()
    async with session_factory() as session:
        runtime_settings = await get_runtime_settings(session)
        job_concurrency = max(1, int(runtime_settings.crawler_worker_count or 1))
        config = CrawlerV2WorkerConfig(
            page_concurrency=config.page_concurrency,
            page_domain_concurrency=config.page_domain_concurrency,
            chunk_concurrency=config.chunk_concurrency,
            enrichment_concurrency=max(1, int(runtime_settings.crawler_profile_enrichment_concurrency or config.enrichment_concurrency)),
            enrichment_host_concurrency=max(1, int(runtime_settings.crawler_host_concurrency or config.enrichment_host_concurrency)),
            lease_seconds=config.lease_seconds,
        )
        now = utc_now()
        lease_expires_at = now + timedelta(seconds=config.lease_seconds)
        job_ids = await _eligible_job_ids(
            session,
            job_concurrency=job_concurrency,
        )
        for job_id in job_ids:
            claimed = await _claim_chunk(
                session,
                job_id=job_id,
                worker_id=worker_id,
                now=now,
                lease_expires_at=lease_expires_at,
                config=config,
            )
            if claimed.kind is CrawlerV2WorkKind.IDLE:
                claimed = await _claim_page_task(
                    session,
                    job_id=job_id,
                    worker_id=worker_id,
                    now=now,
                    lease_expires_at=lease_expires_at,
                    config=config,
                )
            if claimed.kind is CrawlerV2WorkKind.IDLE:
                claimed = await _claim_enrichment_task(
                    session,
                    job_id=job_id,
                    worker_id=worker_id,
                    now=now,
                    lease_expires_at=lease_expires_at,
                    config=config,
                )
            if claimed.kind is not CrawlerV2WorkKind.IDLE:
                await session.commit()
                return claimed
        await session.commit()
        return CrawlerV2ClaimedWork.idle()


async def _eligible_job_ids(
    session: AsyncSession,
    *,
    job_concurrency: int,
) -> list[int]:
    running_jobs = list(
        await session.scalars(
            select(CrawlJob)
            .where(
                CrawlJob.runtime_version == "v2",
                CrawlJob.status == CrawlJobStatus.RUNNING.value,
                CrawlJob.deleted_at.is_(None),
            )
            .order_by(CrawlJob.updated_at.asc(), CrawlJob.created_at.asc(), CrawlJob.id.asc())
        )
    )
    admitted_jobs = list(running_jobs[:job_concurrency])
    overflow_jobs = running_jobs[job_concurrency:]
    for job in overflow_jobs:
        demoted_at = utc_now()
        await _expire_job_work_leases(session, job_id=job.id, now=demoted_at)
        job.status = CrawlJobStatus.QUEUED.value
        job.updated_at = demoted_at
        await mark_crawl_job_run_queued(session, job, now=demoted_at)

    available_slots = max(0, job_concurrency - len(admitted_jobs))
    if available_slots > 0:
        admitted_jobs.extend(
            list(
                await session.scalars(
                    select(CrawlJob)
                    .where(
                        CrawlJob.runtime_version == "v2",
                        CrawlJob.status == CrawlJobStatus.QUEUED.value,
                        CrawlJob.deleted_at.is_(None),
                    )
                    .order_by(CrawlJob.created_at.asc(), CrawlJob.id.asc())
                    .limit(available_slots)
                )
            )
        )
    admitted_jobs.sort(
        key=lambda job: (
            job.updated_at or job.created_at,
            job.created_at,
            job.id,
        )
    )
    return [job.id for job in admitted_jobs]


async def _expire_job_work_leases(
    session: AsyncSession,
    *,
    job_id: int,
    now: datetime,
) -> None:
    for model, processing_status in (
        (CrawlPageTask, CrawlPageTaskStatus.PROCESSING.value),
        (CrawlPageChunk, CrawlPageChunkStatus.PROCESSING.value),
        (
            CrawlCandidateEnrichmentTask,
            CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
        ),
    ):
        await session.execute(
            update(model)
            .where(
                model.job_id == job_id,
                model.status == processing_status,
            )
            .values(lease_expires_at=now)
        )


async def run_crawler_v2_scheduler_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    worker_id: str = "crawler-v2-scheduler",
    config: CrawlerV2WorkerConfig | None = None,
) -> int:
    claimed = await claim_next_v2_work(session_factory, worker_id=worker_id, config=config)
    if claimed.kind is not CrawlerV2WorkKind.IDLE:
        return 1
    async with session_factory() as session:
        await finalize_idle_jobs(session)
        await session.commit()
    return 0


async def finalize_idle_jobs(session: AsyncSession) -> None:
    jobs = list(
        await session.scalars(
            select(CrawlJob).where(
                CrawlJob.runtime_version == "v2",
                CrawlJob.status.in_([CrawlJobStatus.QUEUED.value, CrawlJobStatus.RUNNING.value]),
                CrawlJob.deleted_at.is_(None),
            )
        )
    )
    now = utc_now()
    for job in jobs:
        if await _job_has_available_or_leased_work(session, job_id=job.id, now=now):
            continue
        if job.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value:
            from app.modules.professors.public import (
                finalize_professor_information_enrichment_job,
            )

            await finalize_professor_information_enrichment_job(session, job, now=now)
            continue
        await consolidate_job_candidates(session, job.id)
        has_candidates = await _job_has_candidates(session, job_id=job.id)
        error_message = None
        if not has_candidates:
            if await _requeue_direct_entry_pages_for_browser_retry(session, job=job):
                continue
            final_status = CrawlJobStatus.FAILED.value
            error_message = await _job_terminal_failure_message(session, job_id=job.id) or "抓取未发现候选导师"
        else:
            final_status = CrawlJobStatus.NEEDS_REVIEW.value
        await _append_enrichment_completion_event_if_needed(session, job, now=now)
        job.status = final_status
        job.error_message = error_message
        job.updated_at = now
        await mark_crawl_job_run_finished(session, job, status=final_status, error_message=error_message, now=now)
        profile_text_cache.discard_job(job_id=job.id)


async def _claim_page_task(
    session: AsyncSession,
    *,
    job_id: int,
    worker_id: str,
    now: datetime,
    lease_expires_at: datetime,
    config: CrawlerV2WorkerConfig,
) -> CrawlerV2ClaimedWork:
    active_count = await session.scalar(
        select(func.count()).select_from(CrawlPageTask).join(
            CrawlJob,
            CrawlJob.id == CrawlPageTask.job_id,
        ).where(
            CrawlJob.runtime_version == "v2",
            CrawlJob.status.in_(_ACTIVE_JOB_STATUSES),
            CrawlJob.deleted_at.is_(None),
            CrawlPageTask.status == CrawlPageTaskStatus.PROCESSING.value,
            CrawlPageTask.lease_expires_at > now,
        )
    )
    if int(active_count or 0) >= config.page_concurrency:
        return CrawlerV2ClaimedWork.idle()
    task = await session.scalar(
        select(CrawlPageTask)
        .join(CrawlJob, CrawlJob.id == CrawlPageTask.job_id)
        .where(
            CrawlJob.runtime_version == "v2",
            CrawlJob.status.in_(_ACTIVE_JOB_STATUSES),
            CrawlJob.deleted_at.is_(None),
            CrawlPageTask.job_id == job_id,
            _page_task_claimable(now),
        )
        .order_by(CrawlPageTask.priority.desc(), CrawlPageTask.id.asc())
        .limit(1)
    )
    if task is None:
        return CrawlerV2ClaimedWork.idle()
    result = await _conditional_claim_page_task(session, task=task, worker_id=worker_id, now=now, lease_expires_at=lease_expires_at)
    if result.rowcount != 1:
        await session.rollback()
        return CrawlerV2ClaimedWork.idle()
    await _mark_job_running_for_claimed_work(session, job_id=task.job_id, now=now)
    return CrawlerV2ClaimedWork(kind=CrawlerV2WorkKind.PAGE, work_item_id=task.id, job_id=task.job_id)

async def _claim_chunk(
    session: AsyncSession,
    *,
    job_id: int,
    worker_id: str,
    now: datetime,
    lease_expires_at: datetime,
    config: CrawlerV2WorkerConfig,
) -> CrawlerV2ClaimedWork:
    active_count = await session.scalar(
        select(func.count()).select_from(CrawlPageChunk).join(
            CrawlJob,
            CrawlJob.id == CrawlPageChunk.job_id,
        ).where(
            CrawlJob.runtime_version == "v2",
            CrawlJob.status.in_(_ACTIVE_JOB_STATUSES),
            CrawlJob.deleted_at.is_(None),
            CrawlPageChunk.status == CrawlPageChunkStatus.PROCESSING.value,
            CrawlPageChunk.lease_expires_at > now,
        )
    )
    if int(active_count or 0) >= config.chunk_concurrency:
        return CrawlerV2ClaimedWork.idle()
    chunk = await session.scalar(
        select(CrawlPageChunk)
        .join(CrawlJob, CrawlJob.id == CrawlPageChunk.job_id)
        .where(
            CrawlJob.runtime_version == "v2",
            CrawlJob.status.in_(_ACTIVE_JOB_STATUSES),
            CrawlJob.deleted_at.is_(None),
            CrawlPageChunk.job_id == job_id,
            _chunk_claimable(now),
        )
        .order_by(CrawlPageChunk.id.asc())
        .limit(1)
    )
    if chunk is None:
        return CrawlerV2ClaimedWork.idle()
    result = await _conditional_claim_chunk(session, chunk=chunk, worker_id=worker_id, now=now, lease_expires_at=lease_expires_at)
    if result.rowcount != 1:
        await session.rollback()
        return CrawlerV2ClaimedWork.idle()
    await _mark_job_running_for_claimed_work(session, job_id=chunk.job_id, now=now)
    return CrawlerV2ClaimedWork(kind=CrawlerV2WorkKind.CHUNK, work_item_id=chunk.id, job_id=chunk.job_id)

async def _claim_enrichment_task(
    session: AsyncSession,
    *,
    job_id: int,
    worker_id: str,
    now: datetime,
    lease_expires_at: datetime,
    config: CrawlerV2WorkerConfig,
) -> CrawlerV2ClaimedWork:
    active_count = await session.scalar(
        select(func.count()).select_from(CrawlCandidateEnrichmentTask).join(
            CrawlJob,
            CrawlJob.id == CrawlCandidateEnrichmentTask.job_id,
        ).where(
            CrawlJob.runtime_version == "v2",
            CrawlJob.status.in_(_ACTIVE_JOB_STATUSES),
            CrawlJob.deleted_at.is_(None),
            CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
            CrawlCandidateEnrichmentTask.lease_expires_at > now,
        )
    )
    if int(active_count or 0) >= config.enrichment_concurrency:
        return CrawlerV2ClaimedWork.idle()
    active_hosts = await _active_enrichment_host_counts(session, now=now)
    rows = list(
        await session.execute(
            select(CrawlCandidateEnrichmentTask, CrawlCandidate.profile_url)
            .join(CrawlJob, CrawlJob.id == CrawlCandidateEnrichmentTask.job_id)
            .join(CrawlCandidate, CrawlCandidate.id == CrawlCandidateEnrichmentTask.candidate_id)
            .where(
                CrawlJob.runtime_version == "v2",
                CrawlJob.status.in_(_ACTIVE_JOB_STATUSES),
                CrawlJob.deleted_at.is_(None),
                CrawlCandidateEnrichmentTask.job_id == job_id,
                _enrichment_task_claimable(now),
            )
            .order_by(CrawlCandidateEnrichmentTask.id.asc())
            .limit(20)
        )
    )
    task = None
    for candidate_task, profile_url in rows:
        host = _candidate_profile_host(profile_url)
        if host and active_hosts.get(host, 0) >= config.enrichment_host_concurrency:
            continue
        task = candidate_task
        break
    if task is None:
        return CrawlerV2ClaimedWork.idle()
    result = await _conditional_claim_enrichment_task(session, task=task, worker_id=worker_id, now=now, lease_expires_at=lease_expires_at)
    if result.rowcount != 1:
        await session.rollback()
        return CrawlerV2ClaimedWork.idle()
    await _mark_job_running_for_claimed_work(session, job_id=task.job_id, now=now)
    return CrawlerV2ClaimedWork(kind=CrawlerV2WorkKind.ENRICHMENT, work_item_id=task.id, job_id=task.job_id)



async def _active_enrichment_host_counts(session: AsyncSession, *, now: datetime) -> dict[str, int]:
    rows = await session.execute(
        select(CrawlCandidate.profile_url)
        .select_from(CrawlCandidateEnrichmentTask)
        .join(CrawlCandidate, CrawlCandidate.id == CrawlCandidateEnrichmentTask.candidate_id)
        .join(CrawlJob, CrawlJob.id == CrawlCandidateEnrichmentTask.job_id)
        .where(
            CrawlJob.runtime_version == "v2",
            CrawlJob.status.in_(_ACTIVE_JOB_STATUSES),
            CrawlJob.deleted_at.is_(None),
            CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
            CrawlCandidateEnrichmentTask.lease_expires_at > now,
        )
    )
    counts: dict[str, int] = {}
    for profile_url in rows.scalars():
        host = _candidate_profile_host(profile_url)
        if host:
            counts[host] = counts.get(host, 0) + 1
    return counts


def _candidate_profile_host(profile_url: str | None) -> str | None:
    if not profile_url:
        return None
    try:
        return (urlparse(profile_url).hostname or "").lower() or None
    except ValueError:
        return None

async def _mark_job_running_for_claimed_work(session: AsyncSession, *, job_id: int, now: datetime) -> None:
    job = await session.get(CrawlJob, job_id)
    if job is None:
        return
    job.updated_at = now
    if job.status == CrawlJobStatus.QUEUED.value:
        job.status = CrawlJobStatus.RUNNING.value
        job.error_message = None
        await mark_crawl_job_run_running(session, job, now=now)
def _page_task_claimable(now: datetime):
    return or_(
        CrawlPageTask.status == CrawlPageTaskStatus.PENDING.value,
        (CrawlPageTask.status == CrawlPageTaskStatus.PROCESSING.value)
        & (
            CrawlPageTask.lease_expires_at.is_(None)
            | (CrawlPageTask.lease_expires_at <= now)
        ),
        (CrawlPageTask.status == CrawlPageTaskStatus.FAILED_RETRYABLE.value) & ((CrawlPageTask.lease_expires_at.is_(None)) | (CrawlPageTask.lease_expires_at <= now)),
    )


def _page_task_unfinished(now: datetime):
    return or_(
        _page_task_claimable(now),
        CrawlPageTask.status == CrawlPageTaskStatus.PROCESSING.value,
        CrawlPageTask.status == CrawlPageTaskStatus.FAILED_RETRYABLE.value,
    )


def _chunk_claimable(now: datetime):
    return or_(
        CrawlPageChunk.status == CrawlPageChunkStatus.PENDING.value,
        (CrawlPageChunk.status == CrawlPageChunkStatus.PROCESSING.value)
        & (
            CrawlPageChunk.lease_expires_at.is_(None)
            | (CrawlPageChunk.lease_expires_at <= now)
        ),
        (CrawlPageChunk.status == CrawlPageChunkStatus.FAILED_RETRYABLE.value) & ((CrawlPageChunk.lease_expires_at.is_(None)) | (CrawlPageChunk.lease_expires_at <= now)),
    )


def _chunk_unfinished(now: datetime):
    return or_(
        _chunk_claimable(now),
        CrawlPageChunk.status == CrawlPageChunkStatus.PROCESSING.value,
        CrawlPageChunk.status == CrawlPageChunkStatus.FAILED_RETRYABLE.value,
    )


def _enrichment_task_claimable(now: datetime):
    return or_(
        CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.PENDING.value,
        (CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.PROCESSING.value)
        & (
            CrawlCandidateEnrichmentTask.lease_expires_at.is_(None)
            | (CrawlCandidateEnrichmentTask.lease_expires_at <= now)
        ),
        (CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value) & ((CrawlCandidateEnrichmentTask.lease_expires_at.is_(None)) | (CrawlCandidateEnrichmentTask.lease_expires_at <= now)),
    )


def _enrichment_task_unfinished(now: datetime):
    return or_(
        _enrichment_task_claimable(now),
        CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
        CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value,
    )


async def _conditional_claim_page_task(
    session: AsyncSession,
    *,
    task: CrawlPageTask,
    worker_id: str,
    now: datetime,
    lease_expires_at: datetime,
):
    return await session.execute(
        update(CrawlPageTask)
        .execution_options(synchronize_session=False)
        .where(CrawlPageTask.id == task.id, _page_task_claimable(now))
        .values(
            status=CrawlPageTaskStatus.PROCESSING.value,
            worker_id=worker_id,
            claimed_at=now,
            lease_expires_at=lease_expires_at,
            attempt_count=int(task.attempt_count or 0) + 1,
        )
    )


async def _conditional_claim_chunk(
    session: AsyncSession,
    *,
    chunk: CrawlPageChunk,
    worker_id: str,
    now: datetime,
    lease_expires_at: datetime,
):
    return await session.execute(
        update(CrawlPageChunk)
        .execution_options(synchronize_session=False)
        .where(CrawlPageChunk.id == chunk.id, _chunk_claimable(now))
        .values(
            status=CrawlPageChunkStatus.PROCESSING.value,
            worker_id=worker_id,
            claimed_at=now,
            lease_expires_at=lease_expires_at,
            attempt_count=int(chunk.attempt_count or 0) + 1,
        )
    )


async def _conditional_claim_enrichment_task(
    session: AsyncSession,
    *,
    task: CrawlCandidateEnrichmentTask,
    worker_id: str,
    now: datetime,
    lease_expires_at: datetime,
):
    return await session.execute(
        update(CrawlCandidateEnrichmentTask)
        .execution_options(synchronize_session=False)
        .where(CrawlCandidateEnrichmentTask.id == task.id, _enrichment_task_claimable(now))
        .values(
            status=CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
            worker_id=worker_id,
            claimed_at=now,
            lease_expires_at=lease_expires_at,
            attempt_count=int(task.attempt_count or 0) + 1,
            started_at=task.started_at or now,
            finished_at=None,
        )
    )

async def _job_has_available_or_leased_work(session: AsyncSession, *, job_id: int, now: datetime) -> bool:
    page = await session.scalar(
        select(CrawlPageTask.id).where(
            CrawlPageTask.job_id == job_id,
            _page_task_unfinished(now),
        ).limit(1)
    )
    if page is not None:
        return True
    chunk = await session.scalar(
        select(CrawlPageChunk.id).where(
            CrawlPageChunk.job_id == job_id,
            _chunk_unfinished(now),
        ).limit(1)
    )
    if chunk is not None:
        return True
    enrichment = await session.scalar(
        select(CrawlCandidateEnrichmentTask.id).where(
            CrawlCandidateEnrichmentTask.job_id == job_id,
            _enrichment_task_unfinished(now),
        ).limit(1)
    )
    return enrichment is not None


async def _job_has_candidates(session: AsyncSession, *, job_id: int) -> bool:
    candidate = await session.scalar(
        select(CrawlCandidate.id)
        .where(
            CrawlCandidate.job_id == job_id,
            canonical_candidate_clause(),
        )
        .limit(1)
    )
    return candidate is not None


async def _requeue_direct_entry_pages_for_browser_retry(
    session: AsyncSession,
    *,
    job: CrawlJob,
) -> bool:
    if job.entry_type != "list":
        return False

    tasks = list(
        await session.scalars(
            select(CrawlPageTask).where(
                CrawlPageTask.job_id == job.id,
                CrawlPageTask.parent_url.is_(None),
                CrawlPageTask.depth == 0,
                CrawlPageTask.status == CrawlPageTaskStatus.SUCCEEDED.value,
                CrawlPageTask.fetch_mode == "direct",
                CrawlPageTask.browser_status.is_(None),
            )
        )
    )
    requeued = False
    for task in tasks:
        state = await session.scalar(
            select(CrawlPageFetchState).where(
                CrawlPageFetchState.job_id == job.id,
                CrawlPageFetchState.normalized_url == task.normalized_url,
            )
        )
        if state is not None and (
            state.fetch_mode == "browser" or state.browser_status is not None
        ):
            continue
        task.status = CrawlPageTaskStatus.PENDING.value
        task.worker_id = None
        task.claimed_at = None
        task.lease_expires_at = None
        task.allow_expansion = None
        task.last_error = None
        task.fallback_reason = ZERO_CANDIDATE_BROWSER_RETRY_REASON
        requeued = True
    return requeued


async def _append_enrichment_completion_event_if_needed(
    session: AsyncSession,
    job: CrawlJob,
    *,
    now: datetime,
) -> None:
    active_started_at = None
    if job.current_run_id is not None:
        run = await session.get(CrawlJobRun, job.current_run_id)
        if run is not None:
            active_started_at = run.active_started_at
    filters = [
        CrawlCandidateEnrichmentTask.job_id == job.id,
        CrawlCandidateEnrichmentTask.status.in_(
            [
                CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value,
                CrawlCandidateEnrichmentTaskStatus.SKIPPED.value,
                CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
            ]
        ),
    ]
    if active_started_at is not None:
        filters.append(CrawlCandidateEnrichmentTask.updated_at >= active_started_at)

    canonical_candidate_id = func.coalesce(
        CrawlCandidate.merged_into_candidate_id,
        CrawlCandidate.id,
    )
    rows = await session.execute(
        select(canonical_candidate_id, CrawlCandidateEnrichmentTask.status)
        .join(
            CrawlCandidate,
            CrawlCandidate.id == CrawlCandidateEnrichmentTask.candidate_id,
        )
        .where(*filters)
    )
    status_priority = {
        CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value: 1,
        CrawlCandidateEnrichmentTaskStatus.SKIPPED.value: 2,
        CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value: 3,
    }
    status_by_candidate_id: dict[int, str] = {}
    for candidate_id, status in rows:
        current_status = status_by_candidate_id.get(int(candidate_id))
        if current_status is None or status_priority[status] > status_priority[current_status]:
            status_by_candidate_id[int(candidate_id)] = status
    counts = {
        status: sum(1 for value in status_by_candidate_id.values() if value == status)
        for status in status_priority
    }
    enriched = counts.get(CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value, 0)
    unchanged = counts.get(CrawlCandidateEnrichmentTaskStatus.SKIPPED.value, 0)
    failed = counts.get(CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value, 0)
    candidate_count = enriched + unchanged + failed
    if candidate_count == 0:
        return

    message = f"候选导师详情补全完成：成功 {enriched} 位，未变化 {unchanged} 位，失败 {failed} 位"
    trace = list(job.agent_trace or [])
    trace.append(
        {
            "event_type": "enrichment",
            "message": message,
            "created_at": now.isoformat(),
            "raw": {
                "candidate_count": candidate_count,
                "enriched_count": enriched,
                "unchanged_count": unchanged,
                "failed_count": failed,
            },
        }
    )
    job.agent_trace = trace[-100:]


async def _job_terminal_failure_message(session: AsyncSession, *, job_id: int) -> str | None:
    terminal_errors: list[tuple[datetime | None, str]] = []
    page_errors = await session.execute(
        select(CrawlPageTask.updated_at, CrawlPageTask.last_error).where(
            CrawlPageTask.job_id == job_id,
            CrawlPageTask.status == CrawlPageTaskStatus.FAILED_TERMINAL.value,
            CrawlPageTask.last_error.is_not(None),
        )
    )
    terminal_errors.extend(
        (updated_at, last_error.strip())
        for updated_at, last_error in page_errors
        if last_error and last_error.strip()
    )
    chunk_errors = await session.execute(
        select(CrawlPageChunk.updated_at, CrawlPageChunk.last_error).where(
            CrawlPageChunk.job_id == job_id,
            CrawlPageChunk.status == CrawlPageChunkStatus.FAILED_TERMINAL.value,
            CrawlPageChunk.last_error.is_not(None),
        )
    )
    terminal_errors.extend(
        (updated_at, last_error.strip())
        for updated_at, last_error in chunk_errors
        if last_error and last_error.strip()
    )
    enrichment_errors = await session.execute(
        select(CrawlCandidateEnrichmentTask.updated_at, CrawlCandidateEnrichmentTask.last_error).where(
            CrawlCandidateEnrichmentTask.job_id == job_id,
            CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
            CrawlCandidateEnrichmentTask.last_error.is_not(None),
        )
    )
    terminal_errors.extend(
        (updated_at, last_error.strip())
        for updated_at, last_error in enrichment_errors
        if last_error and last_error.strip()
    )
    if not terminal_errors:
        return None
    message = max(terminal_errors, key=lambda item: item[0].timestamp() if item[0] is not None else 0)[1]
    return format_llm_runtime_error_for_user(message)


async def run_crawler_v2_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    worker_id: str = "crawler-v2-worker",
    config: CrawlerV2WorkerConfig | None = None,
) -> int:
    resolved_config = config or CrawlerV2WorkerConfig()
    claim_owner_id = f"{worker_id[:80]}:{uuid.uuid4().hex}"
    claimed = await claim_next_v2_work(
        session_factory,
        worker_id=claim_owner_id,
        config=config,
    )
    if claimed.kind is CrawlerV2WorkKind.IDLE:
        async with session_factory() as session:
            await finalize_idle_jobs(session)
            await session.commit()
        return 0
    if claimed.work_item_id is None:
        return 0
    work: Awaitable[int]
    if claimed.kind is CrawlerV2WorkKind.PAGE:
        from .page_worker import run_crawler_v2_page_worker_once

        work = run_crawler_v2_page_worker_once(
            session_factory,
            task_id=claimed.work_item_id,
            worker_id=claim_owner_id,
        )
    elif claimed.kind is CrawlerV2WorkKind.CHUNK:
        from .chunk_worker import run_crawler_v2_chunk_worker_once

        work = run_crawler_v2_chunk_worker_once(
            session_factory,
            chunk_id=claimed.work_item_id,
            worker_id=claim_owner_id,
        )
    elif claimed.kind is CrawlerV2WorkKind.ENRICHMENT:
        from .enrichment_worker import run_crawler_v2_enrichment_worker_once

        work = run_crawler_v2_enrichment_worker_once(
            session_factory,
            task_id=claimed.work_item_id,
            worker_id=claim_owner_id,
        )
    else:
        return 0

    return await _run_claimed_work_with_heartbeat(
        session_factory,
        claim=CrawlerV2ClaimFence(
            kind=claimed.kind,
            work_item_id=claimed.work_item_id,
            worker_id=claim_owner_id,
        ),
        lease_seconds=resolved_config.lease_seconds,
        work=work,
    )


async def _run_claimed_work_with_heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    claim: CrawlerV2ClaimFence,
    lease_seconds: int,
    work: Awaitable[int],
) -> int:
    work_task = asyncio.create_task(work)
    heartbeat_task = asyncio.create_task(
        _run_claim_heartbeat(
            session_factory,
            claim=claim,
            lease_seconds=lease_seconds,
        )
    )
    try:
        done, _ = await asyncio.wait(
            {work_task, heartbeat_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        work_task.cancel()
        heartbeat_task.cancel()
        await asyncio.gather(work_task, heartbeat_task, return_exceptions=True)
        raise
    if work_task in done:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
        return await work_task

    claim_is_current = False
    try:
        claim_is_current = await heartbeat_task
    except Exception:
        logger.exception(
            "抓取工作项租约心跳异常，停止旧 claim：kind=%s item=%s",
            claim.kind.value,
            claim.work_item_id,
        )
    if not claim_is_current:
        work_task.cancel()
        with suppress(asyncio.CancelledError):
            await work_task
        return 0
    return await work_task


async def _run_claim_heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    claim: CrawlerV2ClaimFence,
    lease_seconds: int,
) -> bool:
    interval_seconds = max(0.1, min(60.0, max(1, lease_seconds) / 3))
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            if not await renew_crawler_v2_claim(
                session_factory,
                claim,
                lease_seconds=lease_seconds,
            ):
                return False
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "抓取工作项租约续租失败，将在短间隔后重试：kind=%s item=%s",
                claim.kind.value,
                claim.work_item_id,
            )
            await asyncio.sleep(min(5.0, interval_seconds))
