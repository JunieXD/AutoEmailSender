from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CrawlWorkerKind, CrawlWorkerTokenUsage


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
    async with session_factory() as session:
        session.add(
            CrawlWorkerTokenUsage(
                job_id=job_id,
                worker_kind=kind,
                work_item_id=str(work_item_id),
                model_name=model_name,
                input_tokens=max(0, int(input_tokens or 0)),
                output_tokens=max(0, int(output_tokens or 0)),
                cached_tokens=max(0, int(cached_tokens or 0)),
                raw_usage=raw_usage,
            )
        )
        await session.commit()