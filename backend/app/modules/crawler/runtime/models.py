from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CrawlerWorkKind(str, Enum):
    PAGE = "page"
    CHUNK = "chunk"
    ENRICHMENT = "enrichment"
    IDLE = "idle"


@dataclass(frozen=True, slots=True)
class CrawlerWorkerConfig:
    page_concurrency: int = 2
    page_domain_concurrency: int = 2
    chunk_concurrency: int = 3
    enrichment_concurrency: int = 3
    enrichment_host_concurrency: int = 1
    lease_seconds: int = 300


@dataclass(frozen=True, slots=True)
class CrawlerClaimedWork:
    kind: CrawlerWorkKind
    work_item_id: int | None = None
    job_id: int | None = None

    @classmethod
    def idle(cls) -> "CrawlerClaimedWork":
        return cls(kind=CrawlerWorkKind.IDLE)
