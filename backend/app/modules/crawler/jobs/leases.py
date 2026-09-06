from __future__ import annotations

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CrawlCandidateEnrichmentTask,
    CrawlCandidateEnrichmentTaskStatus,
    CrawlPageChunk,
    CrawlPageChunkStatus,
    CrawlPageTask,
    CrawlPageTaskStatus,
)


async def expire_job_work_leases(
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
            .where(model.job_id == job_id, model.status == processing_status)
            .values(lease_expires_at=now)
        )
