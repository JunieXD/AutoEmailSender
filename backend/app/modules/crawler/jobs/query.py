from __future__ import annotations

from typing import Literal

from sqlalchemy import Float, String, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import CrawlJob, CrawlJobKind, CrawlJobStatus

from .trace import latest_event_message

CRAWL_TASK_SEARCH_SCOPES = frozenset({"university", "school", "url", "event"})


def parse_crawl_task_search_scopes(value: str | None) -> frozenset[str]:
    if value is None or not value.strip():
        return CRAWL_TASK_SEARCH_SCOPES
    scopes = frozenset(part.strip() for part in value.split(",") if part.strip())
    if not scopes or not scopes.issubset(CRAWL_TASK_SEARCH_SCOPES):
        raise ValueError("未知抓取任务搜索范围")
    return scopes


async def query_crawl_task_center_jobs(
    session: AsyncSession,
    *,
    view: Literal["current", "trash"],
    offset: int,
    limit: int,
    keyword: str | None,
    search_scopes: frozenset[str],
    status_filter: CrawlJobStatus | str | None,
    sort_key: Literal["updated", "created", "progress"],
    sort_direction: Literal["asc", "desc"],
    unpaged: bool,
) -> tuple[list[CrawlJob], int]:
    filters = [CrawlJob.job_kind == CrawlJobKind.FACULTY_CRAWL.value]
    filters.append(
        CrawlJob.deleted_at.is_(None)
        if view == "current"
        else CrawlJob.deleted_at.is_not(None)
    )
    if status_filter is not None:
        filters.append(CrawlJob.status == status_filter)

    normalized_keyword = (keyword or "").strip().lower()
    if normalized_keyword and "event" in search_scopes:
        return await _query_jobs_with_event_search(
            session,
            filters=filters,
            offset=offset,
            limit=limit,
            keyword=normalized_keyword,
            search_scopes=search_scopes,
            sort_key=sort_key,
            sort_direction=sort_direction,
            unpaged=unpaged,
        )

    if normalized_keyword:
        filters.append(
            or_(
                *_keyword_conditions(
                    normalized_keyword,
                    search_scopes=search_scopes,
                )
            )
        )
    total_count = int(
        await session.scalar(select(func.count()).select_from(CrawlJob).where(*filters))
        or 0
    )
    statement = (
        select(CrawlJob).options(selectinload(CrawlJob.current_run)).where(*filters)
    )
    statement = _order_statement(
        statement,
        sort_key=sort_key,
        sort_direction=sort_direction,
    )
    if not unpaged:
        statement = statement.offset(offset).limit(limit)
    return list(await session.scalars(statement)), total_count


async def _query_jobs_with_event_search(
    session: AsyncSession,
    *,
    filters: list[object],
    offset: int,
    limit: int,
    keyword: str,
    search_scopes: frozenset[str],
    sort_key: str,
    sort_direction: str,
    unpaged: bool,
) -> tuple[list[CrawlJob], int]:
    candidates = list(
        await session.scalars(
            select(CrawlJob)
            .where(*filters)
            .order_by(CrawlJob.created_at.desc(), CrawlJob.id.desc())
        )
    )
    matching_jobs = [
        job
        for job in candidates
        if _matches_keyword(
            job,
            keyword=keyword,
            search_scopes=search_scopes,
        )
    ]
    _sort_jobs(
        matching_jobs,
        sort_key=sort_key,
        sort_direction=sort_direction,
    )
    total_count = len(matching_jobs)
    selected_jobs = matching_jobs if unpaged else matching_jobs[offset : offset + limit]
    if not selected_jobs:
        return [], total_count
    selected_ids = [job.id for job in selected_jobs]
    loaded_jobs = list(
        await session.scalars(
            select(CrawlJob)
            .options(selectinload(CrawlJob.current_run))
            .where(CrawlJob.id.in_(selected_ids))
        )
    )
    jobs_by_id = {job.id: job for job in loaded_jobs}
    return [jobs_by_id[job_id] for job_id in selected_ids], total_count


def _keyword_conditions(
    keyword: str,
    *,
    search_scopes: frozenset[str],
) -> list[object]:
    pattern = f"%{_escape_keyword(keyword)}%"
    conditions: list[object] = []
    if "university" in search_scopes:
        conditions.append(
            func.lower(func.coalesce(CrawlJob.university, "")).like(
                pattern,
                escape="\\",
            )
        )
    if "school" in search_scopes:
        conditions.append(
            func.lower(func.coalesce(CrawlJob.school, "")).like(
                pattern,
                escape="\\",
            )
        )
    if "url" in search_scopes:
        conditions.extend(
            [
                func.lower(func.coalesce(CrawlJob.start_url, "")).like(
                    pattern,
                    escape="\\",
                ),
                func.lower(func.coalesce(cast(CrawlJob.start_urls, String), "")).like(
                    pattern,
                    escape="\\",
                ),
            ]
        )
    return conditions


def _matches_keyword(
    job: CrawlJob,
    *,
    keyword: str,
    search_scopes: frozenset[str],
) -> bool:
    values: list[str] = []
    if "university" in search_scopes:
        values.append(job.university)
    if "school" in search_scopes:
        values.append(job.school)
    if "url" in search_scopes:
        values.extend([job.start_url, " ".join(job.start_urls or [])])
    if "event" in search_scopes:
        values.append(latest_event_message(job.agent_trace) or "")
    return any(keyword in value.lower() for value in values)


def _order_statement(statement: object, *, sort_key: str, sort_direction: str):
    if sort_key == "updated":
        primary_sort = CrawlJob.updated_at
    elif sort_key == "progress":
        primary_sort = case(
            (
                CrawlJob.progress_total > 0,
                cast(CrawlJob.progress_current, Float)
                / cast(CrawlJob.progress_total, Float),
            ),
            else_=0.0,
        )
    else:
        primary_sort = CrawlJob.created_at
    ordered_primary = (
        primary_sort.asc() if sort_direction == "asc" else primary_sort.desc()
    )
    return statement.order_by(
        ordered_primary,
        CrawlJob.created_at.desc(),
        CrawlJob.id.desc(),
    )


def _sort_jobs(
    jobs: list[CrawlJob],
    *,
    sort_key: str,
    sort_direction: str,
) -> None:
    if sort_key == "updated":
        get_value = lambda job: job.updated_at
    elif sort_key == "progress":
        get_value = lambda job: (
            job.progress_current / job.progress_total if job.progress_total > 0 else 0
        )
    else:
        get_value = lambda job: job.created_at
    jobs.sort(key=get_value, reverse=sort_direction == "desc")


def _escape_keyword(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


__all__ = [
    "CRAWL_TASK_SEARCH_SCOPES",
    "parse_crawl_task_search_scopes",
    "query_crawl_task_center_jobs",
]
