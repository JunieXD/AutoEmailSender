from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.models import CrawlJobStatus
from app.services.crawl_job_runs import extract_token_usage


@dataclass(frozen=True, slots=True)
class CrawlJobMetrics:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    duration_seconds: int = 0


def build_crawl_job_metrics(job: Any, *, now: datetime | None = None) -> CrawlJobMetrics:
    current_run = getattr(job, "current_run", None)
    if current_run is not None:
        return _build_current_run_metrics(current_run, now=now)

    trace_events = _normalize_trace(getattr(job, "agent_trace", None))
    input_tokens = 0
    output_tokens = 0
    cached_tokens = 0
    total_tokens = 0

    for event in trace_events:
        usage = extract_token_usage(event)
        if usage is None:
            continue
        input_tokens += usage["input_tokens"]
        output_tokens += usage["output_tokens"]
        cached_tokens += usage.get("cached_tokens") or 0
        total_tokens += usage["total_tokens"]

    created_at = _ensure_datetime(getattr(job, "created_at", None))
    updated_at = _ensure_datetime(getattr(job, "updated_at", None))
    duration_seconds = 0
    if created_at is not None and updated_at is not None:
        duration_seconds = max(0, int((updated_at - created_at).total_seconds()))

    return CrawlJobMetrics(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        total_tokens=total_tokens,
        duration_seconds=duration_seconds,
    )


def _build_current_run_metrics(current_run: Any, *, now: datetime | None) -> CrawlJobMetrics:
    duration_seconds = int(getattr(current_run, "active_seconds", 0) or 0)
    active_started_at = _ensure_datetime(getattr(current_run, "active_started_at", None))
    run_status = getattr(current_run, "status", None)
    if run_status == CrawlJobStatus.RUNNING.value and active_started_at is not None:
        resolved_now = _ensure_datetime(now) or datetime.now(UTC)
        duration_seconds += max(0, int((resolved_now - active_started_at).total_seconds()))

    return CrawlJobMetrics(
        input_tokens=int(getattr(current_run, "input_tokens", 0) or 0),
        output_tokens=int(getattr(current_run, "output_tokens", 0) or 0),
        cached_tokens=int(getattr(current_run, "cached_tokens", 0) or 0),
        total_tokens=int(getattr(current_run, "total_tokens", 0) or 0),
        duration_seconds=duration_seconds,
    )


def _normalize_trace(value: Any) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _ensure_datetime(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
