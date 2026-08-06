from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CrawlJob, CrawlWorkerKind, CrawlWorkerTokenUsage
from ..jobs.runs import get_or_create_current_crawl_job_run

async def record_crawler_v2_token_usage(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    worker_kind: str | CrawlWorkerKind,
    work_item_id: int | str,
    model_name: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    raw_usage: dict[str, Any] | None = None,
) -> None:
    kind = worker_kind.value if isinstance(worker_kind, CrawlWorkerKind) else worker_kind
    normalized_input_tokens = max(0, int(input_tokens or 0))
    normalized_output_tokens = max(0, int(output_tokens or 0))
    normalized_cached_tokens = max(0, int(cached_tokens or 0))
    total_tokens = normalized_input_tokens + normalized_output_tokens
    async with session_factory() as session:
        session.add(
            CrawlWorkerTokenUsage(
                job_id=job_id,
                worker_kind=kind,
                work_item_id=str(work_item_id),
                model_name=model_name,
                input_tokens=normalized_input_tokens,
                output_tokens=normalized_output_tokens,
                cached_tokens=normalized_cached_tokens,
                raw_usage=raw_usage,
            )
        )
        job = await session.get(CrawlJob, job_id)
        if job is not None:
            run = await get_or_create_current_crawl_job_run(session, job)
            run.input_tokens += normalized_input_tokens
            run.output_tokens += normalized_output_tokens
            run.total_tokens += total_tokens
            run.cached_tokens = (run.cached_tokens or 0) + normalized_cached_tokens
        await session.commit()
