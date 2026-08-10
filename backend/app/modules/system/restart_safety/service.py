from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models import (
    CrawlCandidateEnrichmentTask,
    CrawlCandidateEnrichmentTaskStatus,
    CrawlPageChunk,
    CrawlPageChunkStatus,
    CrawlPageTask,
    CrawlPageTaskStatus,
    EmailTask,
    EmailTaskStatus,
    ImapIdentitySyncLease,
    MatchAnalysisJobItem,
    MatchAnalysisJobItemStatus,
)

from .schemas import RestartSafetyRead, RestartSafetyWorkCounts


@dataclass(frozen=True, slots=True)
class RestartSafetyCounts:
    sending: int = 0
    draft_generation: int = 0
    match_analysis: int = 0
    crawler_pages: int = 0
    crawler_chunks: int = 0
    crawler_enrichment: int = 0
    imap_sync: int = 0


async def get_restart_safety(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> RestartSafetyRead:
    checked_at = now or utc_now()
    counts = RestartSafetyCounts(
        sending=await _count_where(
            session,
            EmailTask,
            EmailTask.status == EmailTaskStatus.SENDING.value,
        ),
        draft_generation=await _count_where(
            session,
            EmailTask,
            EmailTask.status == EmailTaskStatus.GENERATING_DRAFT.value,
            EmailTask.draft_claim_id.is_not(None),
            EmailTask.draft_lease_expires_at > checked_at,
        ),
        match_analysis=await _count_where(
            session,
            MatchAnalysisJobItem,
            MatchAnalysisJobItem.status == MatchAnalysisJobItemStatus.RUNNING.value,
            MatchAnalysisJobItem.claim_id.is_not(None),
            MatchAnalysisJobItem.lease_expires_at > checked_at,
        ),
        crawler_pages=await _count_where(
            session,
            CrawlPageTask,
            CrawlPageTask.status == CrawlPageTaskStatus.PROCESSING.value,
            CrawlPageTask.worker_id.is_not(None),
            CrawlPageTask.lease_expires_at > checked_at,
        ),
        crawler_chunks=await _count_where(
            session,
            CrawlPageChunk,
            CrawlPageChunk.status == CrawlPageChunkStatus.PROCESSING.value,
            CrawlPageChunk.worker_id.is_not(None),
            CrawlPageChunk.lease_expires_at > checked_at,
        ),
        crawler_enrichment=await _count_where(
            session,
            CrawlCandidateEnrichmentTask,
            CrawlCandidateEnrichmentTask.status
            == CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
            CrawlCandidateEnrichmentTask.worker_id.is_not(None),
            CrawlCandidateEnrichmentTask.lease_expires_at > checked_at,
        ),
        imap_sync=await _count_where(
            session,
            ImapIdentitySyncLease,
            ImapIdentitySyncLease.claim_id.is_not(None),
            ImapIdentitySyncLease.lease_expires_at > checked_at,
        ),
    )
    return summarize_restart_safety(counts)


def summarize_restart_safety(counts: RestartSafetyCounts) -> RestartSafetyRead:
    crawler_count = (
        counts.crawler_pages
        + counts.crawler_chunks
        + counts.crawler_enrichment
    )
    recoverable_work_count = (
        counts.draft_generation
        + counts.match_analysis
        + crawler_count
        + counts.imap_sync
    )
    active_work_count = counts.sending + recoverable_work_count
    work_counts = RestartSafetyWorkCounts(
        draft_generation=counts.draft_generation,
        match_analysis=counts.match_analysis,
        crawler=crawler_count,
        imap_sync=counts.imap_sync,
    )

    if counts.sending > 0:
        return RestartSafetyRead(
            safe_to_restart=False,
            confirmation_required=False,
            active_work_count=active_work_count,
            sending_count=counts.sending,
            work_counts=work_counts,
            message=(
                f"有 {counts.sending} 封邮件正处于发送与本地提交窗口，"
                "为避免重复发送，请等待发送结束后再重启。"
            ),
        )

    if recoverable_work_count > 0:
        return RestartSafetyRead(
            safe_to_restart=True,
            confirmation_required=True,
            active_work_count=active_work_count,
            sending_count=0,
            work_counts=work_counts,
            message=(
                f"当前有 {recoverable_work_count} 项后台工作正在进行。"
                "重启会先安全停止进程，未完成工作将在下次启动后恢复。"
            ),
        )

    return RestartSafetyRead(
        safe_to_restart=True,
        confirmation_required=False,
        active_work_count=0,
        sending_count=0,
        work_counts=work_counts,
        message="当前没有正在执行的后台工作，可以安全重启。",
    )


async def _count_where(
    session: AsyncSession,
    model: type[object],
    *conditions: object,
) -> int:
    value = await session.scalar(
        select(func.count()).select_from(model).where(*conditions),
    )
    return int(value or 0)
