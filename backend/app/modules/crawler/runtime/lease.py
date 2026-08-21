from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.time import utc_now
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

from .models import CrawlerWorkKind


@dataclass(frozen=True, slots=True)
class CrawlerClaimFence:
    kind: CrawlerWorkKind
    work_item_id: int
    worker_id: str


def _claim_model_and_status(kind: CrawlerWorkKind):
    if kind is CrawlerWorkKind.PAGE:
        return CrawlPageTask, CrawlPageTaskStatus.PROCESSING.value
    if kind is CrawlerWorkKind.CHUNK:
        return CrawlPageChunk, CrawlPageChunkStatus.PROCESSING.value
    if kind is CrawlerWorkKind.ENRICHMENT:
        return (
            CrawlCandidateEnrichmentTask,
            CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
        )
    raise ValueError(f"Unsupported crawler work kind for lease: {kind}")


async def fence_crawler_claim(
    session: AsyncSession,
    claim: CrawlerClaimFence,
) -> bool:
    """Acquire a write fence and prove that a claim is still current."""

    model, processing_status = _claim_model_and_status(claim.kind)
    now = utc_now()
    result = await session.execute(
        update(model)
        .execution_options(synchronize_session=False)
        .where(
            model.id == claim.work_item_id,
            model.status == processing_status,
            model.worker_id == claim.worker_id,
            or_(model.lease_expires_at.is_(None), model.lease_expires_at > now),
            model.job_id.in_(
                select(CrawlJob.id).where(
                    CrawlJob.status.in_(
                        [CrawlJobStatus.QUEUED.value, CrawlJobStatus.RUNNING.value]
                    ),
                    CrawlJob.deleted_at.is_(None),
                )
            ),
        )
        .values(lease_expires_at=model.lease_expires_at)
    )
    return result.rowcount == 1


async def renew_crawler_claim(
    session_factory: async_sessionmaker[AsyncSession],
    claim: CrawlerClaimFence,
    *,
    lease_seconds: int,
) -> bool:
    model, processing_status = _claim_model_and_status(claim.kind)
    now = utc_now()
    lease_expires_at = now + timedelta(seconds=max(1, int(lease_seconds)))
    async with session_factory() as session:
        result = await session.execute(
            update(model)
            .execution_options(synchronize_session=False)
            .where(
                model.id == claim.work_item_id,
                model.status == processing_status,
                model.worker_id == claim.worker_id,
                or_(model.lease_expires_at.is_(None), model.lease_expires_at > now),
                model.job_id.in_(
                    select(CrawlJob.id).where(
                        CrawlJob.status.in_(
                            [CrawlJobStatus.QUEUED.value, CrawlJobStatus.RUNNING.value]
                        ),
                        CrawlJob.deleted_at.is_(None),
                    )
                ),
            )
            .values(lease_expires_at=lease_expires_at)
        )
        if result.rowcount != 1:
            await session.rollback()
            return False
        await session.commit()
        return True
