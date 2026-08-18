from __future__ import annotations

from datetime import datetime

from app.core.schema_metadata import get_current_app_version
from app.core.time import as_utc_aware, utc_now

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CrawlJob, CrawlJobRun, CrawlJobStatus
from app.modules.crawler.pages.browser_session import browser_cookie_session_cache


_BROWSER_SESSION_TERMINAL_STATUSES = {
    CrawlJobStatus.COMPLETED.value,
    CrawlJobStatus.PARTIALLY_COMPLETED.value,
    CrawlJobStatus.FAILED.value,
    CrawlJobStatus.CANCELED.value,
}


async def create_initial_crawl_job_run(
    session: AsyncSession,
    job: CrawlJob,
    *,
    now: datetime | None = None,
) -> CrawlJobRun:
    resolved_now = as_utc_aware(now) if now is not None else utc_now()
    run = CrawlJobRun(
        job_id=job.id,
        attempt_number=1,
        status=job.status,
        app_version=get_current_app_version(),
        created_at=resolved_now,
        updated_at=resolved_now,
    )
    session.add(run)
    await session.flush()
    job.current_run_id = run.id
    return run


async def create_retry_crawl_job_run(
    session: AsyncSession,
    job: CrawlJob,
    *,
    now: datetime | None = None,
) -> CrawlJobRun:
    if job.current_run_id is not None:
        browser_cookie_session_cache.discard_scope(("run", job.current_run_id))
    resolved_now = as_utc_aware(now) if now is not None else utc_now()
    max_attempt = await session.scalar(
        select(func.max(CrawlJobRun.attempt_number)).where(CrawlJobRun.job_id == job.id)
    )
    run = CrawlJobRun(
        job_id=job.id,
        attempt_number=int(max_attempt or 0) + 1,
        status=CrawlJobStatus.QUEUED.value,
        app_version=get_current_app_version(),
        created_at=resolved_now,
        updated_at=resolved_now,
    )
    session.add(run)
    await session.flush()
    job.current_run_id = run.id
    return run


async def get_or_create_current_crawl_job_run(
    session: AsyncSession,
    job: CrawlJob,
    *,
    now: datetime | None = None,
) -> CrawlJobRun:
    if job.current_run_id is not None:
        run = await session.get(CrawlJobRun, job.current_run_id)
        if run is not None:
            return run
    return await create_initial_crawl_job_run(session, job, now=now)


async def mark_crawl_job_run_running(
    session: AsyncSession,
    job: CrawlJob,
    *,
    now: datetime | None = None,
) -> CrawlJobRun:
    resolved_now = as_utc_aware(now) if now is not None else utc_now()
    run = await get_or_create_current_crawl_job_run(session, job, now=resolved_now)
    run.status = CrawlJobStatus.RUNNING.value
    if run.started_at is None:
        run.started_at = resolved_now
    if run.active_started_at is None:
        run.active_started_at = resolved_now
    run.updated_at = resolved_now
    return run


async def mark_crawl_job_run_paused(
    session: AsyncSession,
    job: CrawlJob,
    *,
    now: datetime | None = None,
) -> CrawlJobRun:
    resolved_now = as_utc_aware(now) if now is not None else utc_now()
    run = await get_or_create_current_crawl_job_run(session, job, now=resolved_now)
    _settle_active_segment(run, now=resolved_now)
    run.status = CrawlJobStatus.PAUSED.value
    run.paused_at = resolved_now
    run.updated_at = resolved_now
    return run


async def mark_crawl_job_run_queued(
    session: AsyncSession,
    job: CrawlJob,
    *,
    now: datetime | None = None,
) -> CrawlJobRun:
    resolved_now = as_utc_aware(now) if now is not None else utc_now()
    run = await get_or_create_current_crawl_job_run(session, job, now=resolved_now)
    if run.active_started_at is not None:
        _settle_active_segment(run, now=resolved_now)
    run.status = CrawlJobStatus.QUEUED.value
    run.updated_at = resolved_now
    return run


async def mark_crawl_job_run_finished(
    session: AsyncSession,
    job: CrawlJob,
    *,
    status: str,
    error_message: str | None = None,
    now: datetime | None = None,
) -> CrawlJobRun:
    resolved_now = as_utc_aware(now) if now is not None else utc_now()
    run = await get_or_create_current_crawl_job_run(session, job, now=resolved_now)
    _settle_active_segment(run, now=resolved_now)
    run.status = status
    run.finished_at = resolved_now
    run.error_message = error_message
    run.updated_at = resolved_now
    if status in _BROWSER_SESSION_TERMINAL_STATUSES:
        browser_cookie_session_cache.discard_scope(("run", run.id))
    return run


def extract_token_usage_from_llm_response(response: object) -> dict[str, int | None] | None:
    metadata = getattr(response, "response_metadata", None)
    usage_metadata = getattr(response, "usage_metadata", None)
    direct_usage = getattr(response, "usage", None)
    response_usage = None
    if isinstance(metadata, dict):
        candidate = metadata.get("token_usage") or metadata.get("usage")
        response_usage = candidate if isinstance(candidate, dict) else None
    raw_usage = usage_metadata if isinstance(usage_metadata, dict) else None
    if raw_usage is None:
        raw_usage = response_usage
    if raw_usage is None and isinstance(direct_usage, dict):
        raw_usage = direct_usage
    if raw_usage is None and direct_usage is not None:
        raw_usage = {
            "prompt_tokens": getattr(direct_usage, "prompt_tokens", None),
            "completion_tokens": getattr(direct_usage, "completion_tokens", None),
            "total_tokens": getattr(direct_usage, "total_tokens", None),
            "cached_tokens": getattr(direct_usage, "cached_tokens", None),
        }
    if not isinstance(raw_usage, dict):
        return None

    input_tokens = _coerce_token_count(raw_usage.get("prompt_tokens", raw_usage.get("input_tokens")))
    output_tokens = _coerce_token_count(
        raw_usage.get("completion_tokens", raw_usage.get("output_tokens")),
    )
    total_tokens = _coerce_token_count(raw_usage.get("total_tokens"))
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None

    resolved_input_tokens = input_tokens or 0
    resolved_output_tokens = output_tokens or 0
    resolved_total_tokens = (
        total_tokens
        if total_tokens is not None
        else resolved_input_tokens + resolved_output_tokens
    )
    cached_candidates = [
        cached
        for source in (raw_usage, response_usage)
        if (cached := _extract_cached_tokens_from_mapping(source)) is not None
    ]
    cached_tokens = max(cached_candidates) if cached_candidates else None
    return {
        "input_tokens": resolved_input_tokens,
        "output_tokens": resolved_output_tokens,
        "total_tokens": resolved_total_tokens,
        "cached_tokens": cached_tokens,
    }


def _coerce_token_count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _extract_cached_tokens_from_mapping(value: object) -> int | None:
    if not isinstance(value, dict):
        return None

    for key in (
        "prompt_cache_hit_tokens",
        "cached_tokens",
        "cache_read",
        "cache_read_input_tokens",
    ):
        cached_tokens = _coerce_token_count(value.get(key))
        if cached_tokens is not None:
            return cached_tokens

    for details_key in (
        "prompt_tokens_details",
        "input_tokens_details",
        "input_token_details",
    ):
        details = value.get(details_key)
        if not isinstance(details, dict):
            continue
        for key, raw_count in details.items():
            if key in {"cached_tokens", "cache_read"} or key.endswith("_cache_read"):
                cached_tokens = _coerce_token_count(raw_count)
                if cached_tokens is not None:
                    return cached_tokens
    return None


def _settle_active_segment(run: CrawlJobRun, *, now: datetime) -> None:
    active_started_at = as_utc_aware(run.active_started_at) if isinstance(run.active_started_at, datetime) else None
    if active_started_at is None:
        return
    resolved_now = as_utc_aware(now)
    run.active_seconds += max(0, int((resolved_now - active_started_at).total_seconds()))
    run.active_started_at = None
