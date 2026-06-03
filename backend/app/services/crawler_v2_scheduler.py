from __future__ import annotations

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
    CrawlJobStatus,
    CrawlPageChunk,
    CrawlPageChunkStatus,
    CrawlPageTask,
    CrawlPageTaskStatus,
)
from app.services.crawler_v2_models import CrawlerV2ClaimedWork, CrawlerV2WorkerConfig, CrawlerV2WorkKind
from app.services.runtime_settings import get_runtime_settings
from app.services.crawl_job_runs import mark_crawl_job_run_finished, mark_crawl_job_run_running

_ACTIVE_JOB_STATUSES = {CrawlJobStatus.QUEUED.value, CrawlJobStatus.RUNNING.value}
_PAUSED_JOB_STATUSES = {CrawlJobStatus.PAUSED.value, CrawlJobStatus.CANCELED.value}


async def ensure_job_active(session: AsyncSession, job_id: int) -> bool:
    job = await session.get(CrawlJob, job_id)
    return job is not None and job.status in _ACTIVE_JOB_STATUSES


async def claim_next_v2_work(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    worker_id: str,
    config: CrawlerV2WorkerConfig | None = None,
) -> CrawlerV2ClaimedWork:
    config = config or CrawlerV2WorkerConfig()
    async with session_factory() as session:
        runtime_settings = await get_runtime_settings(session)
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
        claimed = await _claim_chunk(session, worker_id=worker_id, now=now, lease_expires_at=lease_expires_at, config=config)
        if claimed.kind is not CrawlerV2WorkKind.IDLE:
            await session.commit()
            return claimed
        claimed = await _claim_page_task(session, worker_id=worker_id, now=now, lease_expires_at=lease_expires_at, config=config)
        if claimed.kind is not CrawlerV2WorkKind.IDLE:
            await session.commit()
            return claimed
        claimed = await _claim_enrichment_task(session, worker_id=worker_id, now=now, lease_expires_at=lease_expires_at, config=config)
        await session.commit()
        return claimed


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
            )
        )
    )
    now = utc_now()
    for job in jobs:
        if await _job_has_available_or_leased_work(session, job_id=job.id, now=now):
            continue
        has_candidates = await _job_has_candidates(session, job_id=job.id)
        error_message = None
        if not has_candidates:
            final_status = CrawlJobStatus.FAILED.value
            error_message = "抓取未发现候选导师"
        else:
            final_status = CrawlJobStatus.NEEDS_REVIEW.value
        job.status = final_status
        job.error_message = error_message
        job.updated_at = now
        await mark_crawl_job_run_finished(session, job, status=final_status, error_message=error_message, now=now)


async def _claim_page_task(
    session: AsyncSession,
    *,
    worker_id: str,
    now: datetime,
    lease_expires_at: datetime,
    config: CrawlerV2WorkerConfig,
) -> CrawlerV2ClaimedWork:
    active_count = await session.scalar(
        select(func.count()).select_from(CrawlPageTask).where(
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
    worker_id: str,
    now: datetime,
    lease_expires_at: datetime,
    config: CrawlerV2WorkerConfig,
) -> CrawlerV2ClaimedWork:
    active_count = await session.scalar(
        select(func.count()).select_from(CrawlPageChunk).where(
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
    worker_id: str,
    now: datetime,
    lease_expires_at: datetime,
    config: CrawlerV2WorkerConfig,
) -> CrawlerV2ClaimedWork:
    active_count = await session.scalar(
        select(func.count()).select_from(CrawlCandidateEnrichmentTask).where(
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
        .where(
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
    if job is None or job.status != CrawlJobStatus.QUEUED.value:
        return
    job.status = CrawlJobStatus.RUNNING.value
    job.error_message = None
    job.updated_at = now
    await mark_crawl_job_run_running(session, job, now=now)
def _page_task_claimable(now: datetime):
    return or_(
        CrawlPageTask.status == CrawlPageTaskStatus.PENDING.value,
        (CrawlPageTask.status == CrawlPageTaskStatus.PROCESSING.value) & (CrawlPageTask.lease_expires_at <= now),
        CrawlPageTask.status == CrawlPageTaskStatus.FAILED_RETRYABLE.value,
    )


def _page_task_unfinished(now: datetime):
    return or_(
        _page_task_claimable(now),
        CrawlPageTask.status == CrawlPageTaskStatus.PROCESSING.value,
    )


def _chunk_claimable(now: datetime):
    return or_(
        CrawlPageChunk.status == CrawlPageChunkStatus.PENDING.value,
        (CrawlPageChunk.status == CrawlPageChunkStatus.PROCESSING.value) & (CrawlPageChunk.lease_expires_at <= now),
        CrawlPageChunk.status == CrawlPageChunkStatus.FAILED_RETRYABLE.value,
    )


def _chunk_unfinished(now: datetime):
    return or_(
        _chunk_claimable(now),
        CrawlPageChunk.status == CrawlPageChunkStatus.PROCESSING.value,
    )


def _enrichment_task_claimable(now: datetime):
    return or_(
        CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.PENDING.value,
        (CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.PROCESSING.value) & (CrawlCandidateEnrichmentTask.lease_expires_at <= now),
        CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value,
    )


def _enrichment_task_unfinished(now: datetime):
    return or_(
        _enrichment_task_claimable(now),
        CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
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
    candidate = await session.scalar(select(CrawlCandidate.id).where(CrawlCandidate.job_id == job_id).limit(1))
    return candidate is not None

async def run_crawler_v2_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    worker_id: str = "crawler-v2-worker",
    config: CrawlerV2WorkerConfig | None = None,
) -> int:
    claimed = await claim_next_v2_work(session_factory, worker_id=worker_id, config=config)
    if claimed.kind is CrawlerV2WorkKind.IDLE:
        async with session_factory() as session:
            await finalize_idle_jobs(session)
            await session.commit()
        return 0
    if claimed.work_item_id is None:
        return 0
    if claimed.kind is CrawlerV2WorkKind.PAGE:
        from app.services.crawler_v2_page_worker import run_crawler_v2_page_worker_once

        return await run_crawler_v2_page_worker_once(session_factory, task_id=claimed.work_item_id, worker_id=worker_id)
    if claimed.kind is CrawlerV2WorkKind.CHUNK:
        from app.services.crawler_v2_chunk_worker import run_crawler_v2_chunk_worker_once

        return await run_crawler_v2_chunk_worker_once(session_factory, chunk_id=claimed.work_item_id, worker_id=worker_id)
    if claimed.kind is CrawlerV2WorkKind.ENRICHMENT:
        from app.services.crawler_v2_enrichment_worker import run_crawler_v2_enrichment_worker_once

        return await run_crawler_v2_enrichment_worker_once(session_factory, task_id=claimed.work_item_id, worker_id=worker_id)
    return 0
