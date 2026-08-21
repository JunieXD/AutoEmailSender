from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.time import as_utc_aware, utc_now

from app.models import CrawlJobStatus


@dataclass(frozen=True, slots=True)
class CrawlJobMetrics:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    duration_seconds: int = 0


def build_crawl_job_metrics(
    job: Any, *, now: datetime | None = None
) -> CrawlJobMetrics:
    current_run = getattr(job, "current_run", None)
    if current_run is None:
        return CrawlJobMetrics()
    return _build_current_run_metrics(current_run, now=now)


def _build_current_run_metrics(
    current_run: Any, *, now: datetime | None
) -> CrawlJobMetrics:
    duration_seconds = int(getattr(current_run, "active_seconds", 0) or 0)
    active_started_at = _ensure_datetime(
        getattr(current_run, "active_started_at", None)
    )
    run_status = getattr(current_run, "status", None)
    if run_status == CrawlJobStatus.RUNNING.value and active_started_at is not None:
        resolved_now = _ensure_datetime(now) or utc_now()
        duration_seconds += max(
            0, int((resolved_now - active_started_at).total_seconds())
        )

    return CrawlJobMetrics(
        input_tokens=int(getattr(current_run, "input_tokens", 0) or 0),
        output_tokens=int(getattr(current_run, "output_tokens", 0) or 0),
        cached_tokens=int(getattr(current_run, "cached_tokens", 0) or 0),
        total_tokens=int(getattr(current_run, "total_tokens", 0) or 0),
        duration_seconds=duration_seconds,
    )


def _ensure_datetime(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return as_utc_aware(value)
    return value
