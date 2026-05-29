from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
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

_ACTIVE_JOB_STATUSES = {CrawlJobStatus.QUEUED.value, CrawlJobStatus.RUNNING.value}
_PAUSED_JOB_STATUSES = {CrawlJobStatus.PAUSED.value, CrawlJobStatus.CANCELED.value}


async def ensure_job_active(session: AsyncSession, job_id: int) -> bool:
    job = await session.get(CrawlJob, job_id)
    return job is not None and job.status not in _PAUSED_JOB_STATUSES


async def claim_next_v2_work(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    worker_id: str,
    config: CrawlerV2WorkerConfig | None = None,
) -> CrawlerV2ClaimedWork:
    config = config or CrawlerV2WorkerConfig()
    async with session_factory() as session:
        now = datetime.now(UTC)
        lease_expires_at = now + timedelta(seconds=config.lease_seconds)
        claimed = await _claim_page_task(session, worker_id=worker_id, now=now, lease_expires_at=lease_expires_at, config=config)
        if claimed.kind is not CrawlerV2WorkKind.IDLE:
            await session.commit()
            return claimed
        claimed = await _claim_chunk(session, worker_id=worker_id, now=now, lease_expires_at=lease_expires_at, config=config)
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
    now = datetime.now(UTC)
    for job in jobs:
        if await _job_has_available_or_leased_work(session, job_id=job.id, now=now):
            continue
        terminal_failures = await _job_has_terminal_failures(session, job_id=job.id)
        job.status = CrawlJobStatus.PARTIALLY_COMPLETED.value if terminal_failures else CrawlJobStatus.NEEDS_REVIEW.value
        job.updated_at = now


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
            or_(
                CrawlPageTask.status == CrawlPageTaskStatus.PENDING.value,
                (CrawlPageTask.status == CrawlPageTaskStatus.PROCESSING.value) & (CrawlPageTask.lease_expires_at <= now),
                CrawlPageTask.status == CrawlPageTaskStatus.FAILED_RETRYABLE.value,
            ),
        )
        .order_by(CrawlPageTask.priority.desc(), CrawlPageTask.id.asc())
        .limit(1)
    )
    if task is None:
        return CrawlerV2ClaimedWork.idle()
    task.status = CrawlPageTaskStatus.PROCESSING.value
    task.worker_id = worker_id
    task.claimed_at = now
    task.lease_expires_at = lease_expires_at
    task.attempt_count = int(task.attempt_count or 0) + 1
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
            or_(
                CrawlPageChunk.status == CrawlPageChunkStatus.PENDING.value,
                (CrawlPageChunk.status == CrawlPageChunkStatus.PROCESSING.value) & (CrawlPageChunk.lease_expires_at <= now),
                CrawlPageChunk.status == CrawlPageChunkStatus.FAILED_RETRYABLE.value,
            ),
        )
        .order_by(CrawlPageChunk.id.asc())
        .limit(1)
    )
    if chunk is None:
        return CrawlerV2ClaimedWork.idle()
    chunk.status = CrawlPageChunkStatus.PROCESSING.value
    chunk.worker_id = worker_id
    chunk.claimed_at = now
    chunk.lease_expires_at = lease_expires_at
    chunk.attempt_count = int(chunk.attempt_count or 0) + 1
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
    task = await session.scalar(
        select(CrawlCandidateEnrichmentTask)
        .join(CrawlJob, CrawlJob.id == CrawlCandidateEnrichmentTask.job_id)
        .where(
            CrawlJob.runtime_version == "v2",
            CrawlJob.status.in_(_ACTIVE_JOB_STATUSES),
            or_(
                CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.PENDING.value,
                (CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.PROCESSING.value) & (CrawlCandidateEnrichmentTask.lease_expires_at <= now),
                CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value,
            ),
        )
        .order_by(CrawlCandidateEnrichmentTask.id.asc())
        .limit(1)
    )
    if task is None:
        return CrawlerV2ClaimedWork.idle()
    task.status = CrawlCandidateEnrichmentTaskStatus.PROCESSING.value
    task.worker_id = worker_id
    task.claimed_at = now
    task.lease_expires_at = lease_expires_at
    task.attempt_count = int(task.attempt_count or 0) + 1
    return CrawlerV2ClaimedWork(kind=CrawlerV2WorkKind.ENRICHMENT, work_item_id=task.id, job_id=task.job_id)


async def _job_has_available_or_leased_work(session: AsyncSession, *, job_id: int, now: datetime) -> bool:
    page = await session.scalar(
        select(CrawlPageTask.id).where(
            CrawlPageTask.job_id == job_id,
            CrawlPageTask.status.in_([CrawlPageTaskStatus.PENDING.value, CrawlPageTaskStatus.FAILED_RETRYABLE.value, CrawlPageTaskStatus.PROCESSING.value]),
            or_(CrawlPageTask.status != CrawlPageTaskStatus.PROCESSING.value, CrawlPageTask.lease_expires_at > now),
        ).limit(1)
    )
    if page is not None:
        return True
    chunk = await session.scalar(
        select(CrawlPageChunk.id).where(
            CrawlPageChunk.job_id == job_id,
            CrawlPageChunk.status.in_([CrawlPageChunkStatus.PENDING.value, CrawlPageChunkStatus.FAILED_RETRYABLE.value, CrawlPageChunkStatus.PROCESSING.value]),
            or_(CrawlPageChunk.status != CrawlPageChunkStatus.PROCESSING.value, CrawlPageChunk.lease_expires_at > now),
        ).limit(1)
    )
    if chunk is not None:
        return True
    enrichment = await session.scalar(
        select(CrawlCandidateEnrichmentTask.id).where(
            CrawlCandidateEnrichmentTask.job_id == job_id,
            CrawlCandidateEnrichmentTask.status.in_([CrawlCandidateEnrichmentTaskStatus.PENDING.value, CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value, CrawlCandidateEnrichmentTaskStatus.PROCESSING.value]),
            or_(CrawlCandidateEnrichmentTask.status != CrawlCandidateEnrichmentTaskStatus.PROCESSING.value, CrawlCandidateEnrichmentTask.lease_expires_at > now),
        ).limit(1)
    )
    return enrichment is not None


async def _job_has_terminal_failures(session: AsyncSession, *, job_id: int) -> bool:
    page = await session.scalar(select(CrawlPageTask.id).where(CrawlPageTask.job_id == job_id, CrawlPageTask.status == CrawlPageTaskStatus.FAILED_TERMINAL.value).limit(1))
    if page is not None:
        return True
    chunk = await session.scalar(select(CrawlPageChunk.id).where(CrawlPageChunk.job_id == job_id, CrawlPageChunk.status == CrawlPageChunkStatus.FAILED_TERMINAL.value).limit(1))
    if chunk is not None:
        return True
    enrichment = await session.scalar(select(CrawlCandidateEnrichmentTask.id).where(CrawlCandidateEnrichmentTask.job_id == job_id, CrawlCandidateEnrichmentTask.status == CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value).limit(1))
    return enrichment is not None
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
