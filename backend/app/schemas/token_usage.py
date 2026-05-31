from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.base import ApiSchema

TokenUsageFeatureType = Literal["crawl", "match_analysis", "draft_generation"]
TokenUsageFeatureFilter = Literal[
    "all",
    "crawl",
    "match_analysis",
    "draft_generation",
]
TokenUsageStatus = Literal["success", "failed", "running", "unknown"]
TokenUsageChartPreset = Literal[
    "last_6_hours",
    "last_24_hours",
    "last_7_days",
    "last_30_days",
    "custom",
]
TokenUsageChartGranularity = Literal["hour", "day"]


class TokenUsageRecordRead(ApiSchema):
    id: str
    feature_type: TokenUsageFeatureType
    feature_label: str
    title: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    total_tokens: int | None = None
    model_name: str | None = None
    identity_name: str | None = None
    created_at: datetime
    status: TokenUsageStatus


class TokenUsageSummaryRead(ApiSchema):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    record_count: int = 0


class TokenUsagePaginationRead(ApiSchema):
    page: int
    page_size: int
    total_records: int
    total_pages: int


class TokenUsageRecordListRead(ApiSchema):
    records: list[TokenUsageRecordRead] = Field(default_factory=list)
    summary: TokenUsageSummaryRead
    pagination: TokenUsagePaginationRead
    model_options: list[str] = Field(default_factory=list)


class TokenUsageChartBucketRead(ApiSchema):
    bucket_start: datetime
    bucket_label: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0


class TokenUsageChartRead(ApiSchema):
    preset: TokenUsageChartPreset
    granularity: TokenUsageChartGranularity
    range_start: datetime
    range_end: datetime
    buckets: list[TokenUsageChartBucketRead] = Field(default_factory=list)


class TokenUsageFeatureDistributionRead(ApiSchema):
    feature_type: TokenUsageFeatureType
    feature_label: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    record_count: int = 0
    share: float = 0.0


class TokenUsageModelRankingRead(ApiSchema):
    model_name: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    record_count: int = 0
    share: float = 0.0


class TokenUsageVisualizationRead(ApiSchema):
    preset: TokenUsageChartPreset
    summary: TokenUsageSummaryRead
    chart: TokenUsageChartRead
    feature_distribution: list[TokenUsageFeatureDistributionRead] = Field(
        default_factory=list,
    )
    model_ranking: list[TokenUsageModelRankingRead] = Field(default_factory=list)
    recent_records: list[TokenUsageRecordRead] = Field(default_factory=list)
