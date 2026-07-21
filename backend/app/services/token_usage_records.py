from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.core.time import as_utc_aware, as_utc_naive, utc_now

from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    CrawlJob,
    CrawlJobKind,
    CrawlJobRun,
    CrawlJobStatus,
    EmailDirection,
    EmailLog,
    LLMProfile,
    MatchAnalysisJob,
    MatchAnalysisJobItem,
    MatchAnalysisJobStatus,
    MatchAnalysisRun,
)
from app.schemas.token_usage import (
    TokenUsageChartBucketRead,
    TokenUsageChartGranularity,
    TokenUsageChartPreset,
    TokenUsageChartRead,
    TokenUsageFeatureDistributionRead,
    TokenUsageModelRankingRead,
    TokenUsagePaginationRead,
    TokenUsageRecordListRead,
    TokenUsageRecordRead,
    TokenUsageSummaryRead,
    TokenUsageVisualizationRead,
)


async def list_token_usage_records(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 5,
    feature_type: str = "all",
    model_name: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> TokenUsageRecordListRead:
    if (
        start_at is not None
        and end_at is not None
        and as_utc_naive(start_at) > as_utc_naive(end_at)
    ):
        raise ValueError("开始时间不能晚于结束时间")

    resolved_page = max(page, 1)
    resolved_page_size = min(max(page_size, 1), 100)
    candidates = await _list_all_candidate_records(session)
    records = sorted(
        _filter_records(
            candidates,
            feature_type=feature_type,
            model_name=model_name,
            start_at=start_at,
            end_at=end_at,
        ),
        key=lambda item: item.created_at,
        reverse=True,
    )
    total_records = len(records)
    total_pages = (total_records + resolved_page_size - 1) // resolved_page_size
    start_index = (resolved_page - 1) * resolved_page_size
    page_records = records[start_index : start_index + resolved_page_size]

    return TokenUsageRecordListRead(
        records=page_records,
        summary=_build_summary(records),
        pagination=TokenUsagePaginationRead(
            page=resolved_page,
            page_size=resolved_page_size,
            total_records=total_records,
            total_pages=total_pages,
        ),
        model_options=await _list_model_options(session),
    )


async def build_token_usage_chart(
    session: AsyncSession,
    *,
    feature_type: str = "all",
    preset: TokenUsageChartPreset = "last_24_hours",
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    model_name: str | None = None,
    now: datetime | None = None,
) -> TokenUsageChartRead:
    range_start, range_end, granularity = _resolve_chart_range(
        preset=preset,
        start_at=start_at,
        end_at=end_at,
        now=now,
    )
    filter_start, filter_end = _resolve_chart_filter_range(
        preset=preset,
        start_at=start_at,
        end_at=end_at,
        range_start=range_start,
        range_end=range_end,
        granularity=granularity,
    )
    candidates = await _list_all_candidate_records(session)
    records = _filter_records(
        candidates,
        feature_type=feature_type,
        model_name=model_name,
        start_at=filter_start,
        end_at=filter_end,
    )
    bucket_totals = _aggregate_chart_buckets(records, granularity=granularity)
    buckets = [
        TokenUsageChartBucketRead(
            bucket_start=bucket_start,
            bucket_label=_format_bucket_label(bucket_start, granularity),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            total_tokens=input_tokens + output_tokens,
        )
        for bucket_start, input_tokens, output_tokens, cached_tokens in _iter_chart_buckets(
            range_start,
            range_end,
            granularity=granularity,
            bucket_totals=bucket_totals,
        )
    ]
    return TokenUsageChartRead(
        preset=preset,
        granularity=granularity,
        range_start=range_start,
        range_end=range_end,
        buckets=buckets,
    )


async def build_token_usage_visualization(
    session: AsyncSession,
    *,
    preset: TokenUsageChartPreset = "last_24_hours",
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    now: datetime | None = None,
) -> TokenUsageVisualizationRead:
    chart = await build_token_usage_chart(
        session,
        preset=preset,
        feature_type="all",
        model_name=None,
        start_at=start_at,
        end_at=end_at,
        now=now,
    )
    candidates = await _list_all_candidate_records(session)
    filter_start, filter_end = _resolve_chart_filter_range(
        preset=preset,
        start_at=start_at,
        end_at=end_at,
        range_start=chart.range_start,
        range_end=chart.range_end,
        granularity=chart.granularity,
    )
    records = sorted(
        _filter_records(
            candidates,
            feature_type="all",
            model_name=None,
            start_at=filter_start,
            end_at=filter_end,
        ),
        key=lambda item: item.created_at,
        reverse=True,
    )

    return TokenUsageVisualizationRead(
        preset=preset,
        summary=_build_summary(records),
        chart=chart,
        feature_distribution=_build_feature_distribution(records),
        model_ranking=_build_model_ranking(records, limit=5),
        recent_records=records[:8],
    )


async def _list_all_candidate_records(
    session: AsyncSession,
) -> list[TokenUsageRecordRead]:
    candidates: list[TokenUsageRecordRead] = []
    candidates.extend(await _list_crawl_records(session, limit=None))
    candidates.extend(await _list_match_records(session, limit=None))
    candidates.extend(await _list_draft_records(session, limit=None))
    return candidates


def _filter_records(
    records: list[TokenUsageRecordRead],
    *,
    feature_type: str,
    model_name: str | None,
    start_at: datetime | None,
    end_at: datetime | None,
) -> list[TokenUsageRecordRead]:
    filtered = records
    if feature_type != "all":
        filtered = [item for item in filtered if item.feature_type == feature_type]
    resolved_model_name = _normalize_model_name(model_name)
    if resolved_model_name is not None:
        filtered = [item for item in filtered if item.model_name == resolved_model_name]
    if start_at is not None:
        comparable_start = as_utc_naive(start_at)
        filtered = [
            item
            for item in filtered
            if as_utc_naive(item.created_at) >= comparable_start
        ]
    if end_at is not None:
        comparable_end = as_utc_naive(end_at)
        filtered = [
            item for item in filtered if as_utc_naive(item.created_at) <= comparable_end
        ]
    return filtered


def _normalize_model_name(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


async def _list_model_options(session: AsyncSession) -> list[str]:
    model_names = await session.scalars(
        select(LLMProfile.model_name).order_by(LLMProfile.model_name.asc()),
    )
    return sorted(
        {
            model_name.strip()
            for model_name in model_names
            if isinstance(model_name, str) and model_name.strip()
        },
    )


async def _list_crawl_records(
    session: AsyncSession,
    *,
    limit: int | None,
) -> list[TokenUsageRecordRead]:
    statement = (
        select(CrawlJobRun)
        .options(
            selectinload(CrawlJobRun.job).selectinload(CrawlJob.llm_profile),
        )
        .order_by(CrawlJobRun.created_at.desc())
    )
    if limit is not None:
        statement = statement.limit(limit)
    runs = list(
        await session.scalars(
            statement,
        ),
    )
    return [_crawl_run_to_record(run) for run in runs]


async def _list_match_records(
    session: AsyncSession,
    *,
    limit: int | None,
) -> list[TokenUsageRecordRead]:
    job_records = await _list_match_job_records(session, limit=limit)
    linked_run_ids_statement = select(MatchAnalysisJobItem.match_analysis_run_id).where(
        MatchAnalysisJobItem.match_analysis_run_id.is_not(None),
    )
    statement = (
        select(MatchAnalysisRun)
        .options(
            selectinload(MatchAnalysisRun.professor),
            selectinload(MatchAnalysisRun.identity),
            selectinload(MatchAnalysisRun.llm_profile),
        )
        .where(MatchAnalysisRun.id.not_in(linked_run_ids_statement))
        .order_by(MatchAnalysisRun.created_at.desc())
    )
    if limit is not None:
        statement = statement.limit(limit)
    runs = list(
        await session.scalars(
            statement,
        ),
    )
    records = [*job_records, *[_match_run_to_record(run) for run in runs]]
    records.sort(key=lambda item: item.created_at, reverse=True)
    return records if limit is None else records[:limit]


async def _list_match_job_records(
    session: AsyncSession,
    *,
    limit: int | None,
) -> list[TokenUsageRecordRead]:
    statement = (
        select(MatchAnalysisJob)
        .options(
            selectinload(MatchAnalysisJob.identity),
            selectinload(MatchAnalysisJob.llm_profile),
        )
        .order_by(MatchAnalysisJob.created_at.desc())
    )
    if limit is not None:
        statement = statement.limit(limit)
    jobs = list(await session.scalars(statement))
    return [_match_job_to_record(job) for job in jobs]


async def _list_draft_records(
    session: AsyncSession,
    *,
    limit: int | None,
) -> list[TokenUsageRecordRead]:
    statement = (
        select(EmailLog)
        .options(
            selectinload(EmailLog.professor),
            selectinload(EmailLog.identity),
            selectinload(EmailLog.llm_profile),
        )
        .where(EmailLog.direction == EmailDirection.DRAFT.value)
        .order_by(EmailLog.created_at.desc())
    )
    if limit is not None:
        statement = statement.limit(limit * 3)
    logs = list(
        await session.scalars(
            statement,
        ),
    )
    records = [
        record
        for log in logs
        for record in [_draft_log_to_record(log)]
        if record is not None
    ]
    return records if limit is None else records[:limit]


def _crawl_run_to_record(run: CrawlJobRun) -> TokenUsageRecordRead:
    job = run.job
    if job is not None and job.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value:
        return TokenUsageRecordRead(
            id=f"information_enrichment:{run.id}",
            feature_type="information_enrichment",
            feature_label="信息补全",
            title=job.display_name or f"信息补全 #{job.id}",
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            cached_tokens=run.cached_tokens,
            total_tokens=run.total_tokens,
            model_name=job.llm_profile.model_name if job.llm_profile else None,
            identity_name=None,
            created_at=run.created_at,
            status=_map_crawl_status(run.status),
        )
    title_context = None
    if job is not None:
        title_context = _crawl_job_title_context(job)
    return TokenUsageRecordRead(
        id=f"crawl:{run.id}",
        feature_type="crawl",
        feature_label="智能爬取",
        title=f"智能爬取 - {title_context or '未命名任务'}",
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        cached_tokens=run.cached_tokens,
        total_tokens=run.total_tokens,
        model_name=job.llm_profile.model_name if job and job.llm_profile else None,
        identity_name=None,
        created_at=run.created_at,
        status=_map_crawl_status(run.status),
    )


def _crawl_job_title_context(job: CrawlJob) -> str | None:
    location_parts = [
        value.strip()
        for value in (job.university, job.school)
        if isinstance(value, str) and value.strip()
    ]
    if location_parts:
        return " · ".join(location_parts)
    return job.start_url

def _match_run_to_record(run: MatchAnalysisRun) -> TokenUsageRecordRead:
    professor_name = run.professor.name if run.professor else "未关联导师"
    return TokenUsageRecordRead(
        id=f"match_analysis:{run.id}",
        feature_type="match_analysis",
        feature_label="匹配分析",
        title=f"{professor_name} - 匹配分析",
        input_tokens=run.prompt_tokens,
        output_tokens=run.completion_tokens,
        cached_tokens=run.cached_tokens,
        total_tokens=run.total_tokens,
        model_name=run.llm_profile.model_name if run.llm_profile else None,
        identity_name=_identity_name(run.identity),
        created_at=run.created_at,
        status="success" if run.success else "failed",
    )


def _match_job_to_record(job: MatchAnalysisJob) -> TokenUsageRecordRead:
    return TokenUsageRecordRead(
        id=f"match_analysis_job:{job.id}",
        feature_type="match_analysis",
        feature_label="匹配分析",
        title=job.name,
        input_tokens=job.total_prompt_tokens,
        output_tokens=job.total_completion_tokens,
        cached_tokens=job.total_cached_tokens,
        total_tokens=job.total_tokens,
        model_name=job.llm_profile.model_name if job.llm_profile else None,
        identity_name=_identity_name(job.identity),
        created_at=job.created_at,
        status=_map_match_job_status(job.status),
    )

def _map_match_job_status(status: str) -> str:
    if status in {
        MatchAnalysisJobStatus.QUEUED.value,
        MatchAnalysisJobStatus.RUNNING.value,
    }:
        return "running"
    if status in {
        MatchAnalysisJobStatus.COMPLETED.value,
        MatchAnalysisJobStatus.PARTIAL_FAILED.value,
    }:
        return "success"
    if status in {
        MatchAnalysisJobStatus.FAILED.value,
        MatchAnalysisJobStatus.CANCELED.value,
    }:
        return "failed"
    return "unknown"


def _draft_log_to_record(log: EmailLog) -> TokenUsageRecordRead | None:
    usage = _extract_usage(log.provider_payload)
    if usage is None:
        return None
    professor_name = log.professor.name if log.professor else "未关联导师"
    return TokenUsageRecordRead(
        id=f"draft_generation:{log.id}",
        feature_type="draft_generation",
        feature_label="AI 草稿",
        title=f"{professor_name} - AI 草稿",
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        cached_tokens=usage.get("cached_tokens"),
        total_tokens=usage.get("total_tokens"),
        model_name=log.llm_profile.model_name if log.llm_profile else None,
        identity_name=_identity_name(log.identity),
        created_at=log.created_at,
        status="success",
    )


def _extract_usage(payload: dict[str, object] | None) -> dict[str, int | None] | None:
    if not payload:
        return None
    raw_usage = payload.get("usage")
    if not isinstance(raw_usage, dict):
        return None
    return {
        "prompt_tokens": _int_or_none(raw_usage.get("prompt_tokens")),
        "completion_tokens": _int_or_none(raw_usage.get("completion_tokens")),
        "cached_tokens": _int_or_none(raw_usage.get("cached_tokens")),
        "total_tokens": _int_or_none(raw_usage.get("total_tokens")),
    }


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _identity_name(identity: object) -> str | None:
    profile_name = getattr(identity, "profile_name", None)
    name = getattr(identity, "name", None)
    if isinstance(profile_name, str) and profile_name.strip():
        return profile_name
    return name if isinstance(name, str) and name.strip() else None


def _map_crawl_status(status: str) -> str:
    if status in {
        CrawlJobStatus.QUEUED.value,
        CrawlJobStatus.RUNNING.value,
        CrawlJobStatus.PAUSED.value,
    }:
        return "running"
    if status in {
        CrawlJobStatus.NEEDS_REVIEW.value,
        CrawlJobStatus.PARTIALLY_COMPLETED.value,
        CrawlJobStatus.COMPLETED.value,
    }:
        return "success"
    if status in {
        CrawlJobStatus.FAILED.value,
        CrawlJobStatus.CANCELED.value,
    }:
        return "failed"
    return "unknown"


def _resolve_chart_range(
    *,
    preset: TokenUsageChartPreset,
    start_at: datetime | None,
    end_at: datetime | None,
    now: datetime | None,
) -> tuple[datetime, datetime, TokenUsageChartGranularity]:
    resolved_now = as_utc_aware(now) if now is not None else utc_now()
    if preset == "last_6_hours":
        range_end = _floor_hour(resolved_now)
        return range_end - timedelta(hours=5), range_end, "hour"
    if preset == "last_24_hours":
        range_end = _floor_hour(resolved_now)
        return range_end - timedelta(hours=23), range_end, "hour"
    if preset == "last_7_days":
        range_end = _floor_day(resolved_now)
        return range_end - timedelta(days=6), range_end, "day"
    if preset == "last_30_days":
        range_end = _floor_day(resolved_now)
        return range_end - timedelta(days=29), range_end, "day"

    if start_at is None or end_at is None:
        raise ValueError("自定义趋势图需要开始时间和结束时间")
    if as_utc_aware(start_at) > as_utc_aware(end_at):
        raise ValueError("开始时间不能晚于结束时间")

    range_start = as_utc_aware(start_at)
    range_end = as_utc_aware(end_at)
    if range_end - range_start <= timedelta(hours=48):
        return _floor_hour(range_start), _floor_hour(range_end), "hour"
    return _floor_day(range_start), _floor_day(range_end), "day"



def _resolve_chart_filter_range(
    *,
    preset: TokenUsageChartPreset,
    start_at: datetime | None,
    end_at: datetime | None,
    range_start: datetime,
    range_end: datetime,
    granularity: TokenUsageChartGranularity,
) -> tuple[datetime, datetime]:
    if preset == "custom":
        if start_at is None or end_at is None:
            raise ValueError("自定义趋势图需要开始时间和结束时间")
        return as_utc_aware(start_at), as_utc_aware(end_at)
    return range_start, range_end + _bucket_duration(granularity) - timedelta(microseconds=1)

def _aggregate_chart_buckets(
    records: list[TokenUsageRecordRead],
    *,
    granularity: TokenUsageChartGranularity,
) -> dict[datetime, tuple[int, int, int]]:
    buckets: dict[datetime, tuple[int, int, int]] = {}
    for record in records:
        bucket_start = _bucket_start(record.created_at, granularity)
        current_input, current_output, current_cached = buckets.get(
            bucket_start,
            (0, 0, 0),
        )
        buckets[bucket_start] = (
            current_input + (record.input_tokens or 0),
            current_output + (record.output_tokens or 0),
            current_cached + (record.cached_tokens or 0),
        )
    return buckets


def _iter_chart_buckets(
    range_start: datetime,
    range_end: datetime,
    *,
    granularity: TokenUsageChartGranularity,
    bucket_totals: dict[datetime, tuple[int, int, int]],
) -> list[tuple[datetime, int, int, int]]:
    buckets: list[tuple[datetime, int, int, int]] = []
    current = range_start
    while current <= range_end:
        input_tokens, output_tokens, cached_tokens = bucket_totals.get(
            current,
            (0, 0, 0),
        )
        buckets.append((current, input_tokens, output_tokens, cached_tokens))
        current = _next_bucket(current, granularity)
    return buckets


def _bucket_start(
    value: datetime,
    granularity: TokenUsageChartGranularity,
) -> datetime:
    utc_value = as_utc_aware(value)
    if granularity == "hour":
        return _floor_hour(utc_value)
    return _floor_day(utc_value)


def _next_bucket(
    value: datetime,
    granularity: TokenUsageChartGranularity,
) -> datetime:
    if granularity == "hour":
        return value + timedelta(hours=1)
    return value + timedelta(days=1)


def _bucket_duration(granularity: TokenUsageChartGranularity) -> timedelta:
    if granularity == "hour":
        return timedelta(hours=1)
    return timedelta(days=1)


def _format_bucket_label(
    value: datetime,
    granularity: TokenUsageChartGranularity,
) -> str:
    if granularity == "hour":
        return value.strftime("%H:00")
    return value.strftime("%m-%d")


def _floor_hour(value: datetime) -> datetime:
    return as_utc_aware(value).replace(minute=0, second=0, microsecond=0)


def _floor_day(value: datetime) -> datetime:
    return as_utc_aware(value).replace(hour=0, minute=0, second=0, microsecond=0)



def _build_summary(records: list[TokenUsageRecordRead]) -> TokenUsageSummaryRead:
    return TokenUsageSummaryRead(
        input_tokens=sum(item.input_tokens or 0 for item in records),
        output_tokens=sum(item.output_tokens or 0 for item in records),
        cached_tokens=sum(item.cached_tokens or 0 for item in records),
        total_tokens=sum(item.total_tokens or 0 for item in records),
        record_count=len(records),
    )


def _build_feature_distribution(
    records: list[TokenUsageRecordRead],
) -> list[TokenUsageFeatureDistributionRead]:
    total_tokens = sum(item.total_tokens or 0 for item in records)
    grouped: dict[str, list[TokenUsageRecordRead]] = {}
    for record in records:
        grouped.setdefault(record.feature_type, []).append(record)

    rows: list[TokenUsageFeatureDistributionRead] = []
    for feature_type, items in grouped.items():
        summary = _build_summary(items)
        rows.append(
            TokenUsageFeatureDistributionRead(
                feature_type=feature_type,
                feature_label=items[0].feature_label,
                input_tokens=summary.input_tokens,
                output_tokens=summary.output_tokens,
                cached_tokens=summary.cached_tokens,
                total_tokens=summary.total_tokens,
                record_count=summary.record_count,
                share=(summary.total_tokens / total_tokens) if total_tokens else 0.0,
            ),
        )

    return sorted(rows, key=lambda item: item.total_tokens, reverse=True)


def _build_model_ranking(
    records: list[TokenUsageRecordRead],
    *,
    limit: int = 5,
) -> list[TokenUsageModelRankingRead]:
    total_tokens = sum(item.total_tokens or 0 for item in records)
    grouped: dict[str, list[TokenUsageRecordRead]] = {}
    for record in records:
        model_name = record.model_name or "未关联模型"
        grouped.setdefault(model_name, []).append(record)

    rows: list[TokenUsageModelRankingRead] = []
    for model_name, items in grouped.items():
        summary = _build_summary(items)
        rows.append(
            TokenUsageModelRankingRead(
                model_name=model_name,
                input_tokens=summary.input_tokens,
                output_tokens=summary.output_tokens,
                cached_tokens=summary.cached_tokens,
                total_tokens=summary.total_tokens,
                record_count=summary.record_count,
                share=(summary.total_tokens / total_tokens) if total_tokens else 0.0,
            ),
        )

    return sorted(rows, key=lambda item: item.total_tokens, reverse=True)[:limit]
