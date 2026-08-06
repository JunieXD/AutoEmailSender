from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CrawlerV2WorkKind(str, Enum):
    PAGE = "page"
    CHUNK = "chunk"
    ENRICHMENT = "enrichment"
    IDLE = "idle"


@dataclass(frozen=True, slots=True)
class CrawlerV2WorkerConfig:
    page_concurrency: int = 2
    page_domain_concurrency: int = 2
    chunk_concurrency: int = 3
    enrichment_concurrency: int = 3
    enrichment_host_concurrency: int = 1
    lease_seconds: int = 300


@dataclass(frozen=True, slots=True)
class CrawlerV2ClaimedWork:
    kind: CrawlerV2WorkKind
    work_item_id: int | None = None
    job_id: int | None = None

    @classmethod
    def idle(cls) -> "CrawlerV2ClaimedWork":
        return cls(kind=CrawlerV2WorkKind.IDLE)
