from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.time import utc_now
from app.models import (
    CrawlCandidate,
    CrawlCandidateEnrichmentTask,
    CrawlCandidateEnrichmentTaskStatus,
    CrawlJob,
    CrawlJobKind,
    CrawlJobStatus,
    CrawlPageChunk,
    CrawlPageChunkStatus,
    CrawlPageTask,
    CrawlPageTaskStatus,
)

from ..candidate_identity import canonical_candidate_clause, consolidate_job_candidates
from .leases import expire_job_work_leases
from .records import pause_faculty_crawl_job_record
from .runs import mark_crawl_job_run_finished, mark_crawl_job_run_queued

INTERRUPTED_JOB_ERROR = "抓取任务因桌面端进程中断而停止"
INTERRUPTED_JOB_PAUSED_MESSAGE = (
    "检测到上次运行被中断，智能抓取任务已自动暂停，可手动继续"
)
MAX_AGENT_TRACE_EVENTS = 100


async def recover_interrupted_crawl_jobs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        running_job_ids = list(
            await session.scalars(
                select(CrawlJob.id)
                .where(
                    CrawlJob.status == CrawlJobStatus.RUNNING.value,
                    CrawlJob.deleted_at.is_(None),
                )
                .order_by(CrawlJob.created_at.asc(), CrawlJob.id.asc()),
            )
        )

    for job_id in running_job_ids:
        await _recover_interrupted_crawl_job(session_factory, job_id)


async def _recover_interrupted_crawl_job(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
) -> None:
    async with session_factory() as session:
        job = await session.get(CrawlJob, job_id)
        if job is None or job.status != CrawlJobStatus.RUNNING.value:
            return

        if job.job_kind == CrawlJobKind.FACULTY_CRAWL.value:
            job = await pause_faculty_crawl_job_record(
                session,
                job.id,
                event_name="crawl_job.interrupted_paused",
                actor="system",
            )
            now = utc_now()
            trace = _normalize_trace(job.agent_trace)
            trace.append(
                {
                    "event_type": "job_interrupted_paused",
                    "message": INTERRUPTED_JOB_PAUSED_MESSAGE,
                    "created_at": now.isoformat(),
                }
            )
            job.agent_trace = trace[-MAX_AGENT_TRACE_EVENTS:]
            job.updated_at = now
            await session.commit()
            return

        if (
            job.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value
            or await _crawl_job_has_pending_work(session, job_id=job.id)
        ):
            now = utc_now()
            await expire_job_work_leases(session, job_id=job.id, now=now)
            job.status = CrawlJobStatus.QUEUED.value
            job.updated_at = now
            await mark_crawl_job_run_queued(session, job, now=now)
            await session.commit()
            return

        await consolidate_job_candidates(session, job_id)
        candidate_count = await session.scalar(
            select(func.count())
            .select_from(CrawlCandidate)
            .where(
                CrawlCandidate.job_id == job_id,
                canonical_candidate_clause(),
            )
        )
        now = utc_now()
        if int(candidate_count or 0) > 0:
            job.status = CrawlJobStatus.NEEDS_REVIEW.value
            job.error_message = None
            error_message = None
        else:
            job.status = CrawlJobStatus.FAILED.value
            job.error_message = INTERRUPTED_JOB_ERROR
            error_message = INTERRUPTED_JOB_ERROR

        trace = _normalize_trace(job.agent_trace)
        trace.append(
            {
                "event_type": "job_recovered",
                "message": INTERRUPTED_JOB_ERROR,
                "created_at": now.isoformat(),
            }
        )
        job.agent_trace = trace[-MAX_AGENT_TRACE_EVENTS:]
        job.updated_at = now
        await mark_crawl_job_run_finished(
            session,
            job,
            status=job.status,
            error_message=error_message,
            now=now,
        )
        await session.commit()


async def _crawl_job_has_pending_work(session: AsyncSession, *, job_id: int) -> bool:
    pending_queries = (
        select(CrawlPageChunk.id).where(
            CrawlPageChunk.job_id == job_id,
            CrawlPageChunk.status.in_(
                [
                    CrawlPageChunkStatus.PENDING.value,
                    CrawlPageChunkStatus.PROCESSING.value,
                    CrawlPageChunkStatus.SPLIT_REQUIRED.value,
                    CrawlPageChunkStatus.FAILED_RETRYABLE.value,
                ]
            ),
        ),
        select(CrawlPageTask.id).where(
            CrawlPageTask.job_id == job_id,
            CrawlPageTask.status.in_(
                [
                    CrawlPageTaskStatus.PENDING.value,
                    CrawlPageTaskStatus.PROCESSING.value,
                    CrawlPageTaskStatus.FAILED_RETRYABLE.value,
                ]
            ),
        ),
        select(CrawlCandidateEnrichmentTask.id).where(
            CrawlCandidateEnrichmentTask.job_id == job_id,
            CrawlCandidateEnrichmentTask.status.in_(
                [
                    CrawlCandidateEnrichmentTaskStatus.PENDING.value,
                    CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
                    CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value,
                ]
            ),
        ),
    )
    for query in pending_queries:
        if await session.scalar(query.limit(1)) is not None:
            return True
    return False


def _normalize_trace(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
