from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.base import ApiSchema

DashboardMentorMatchBucket = Literal[
    "unmatched",
    "0_59",
    "60_69",
    "70_79",
    "80_89",
    "90_100",
]

DashboardEmailStatusKey = Literal[
    "discovered",
    "matched",
    "generating_draft",
    "draft_failed",
    "review_required",
    "approved",
    "scheduled",
    "sending",
    "sent",
    "send_failed",
    "reply_detected",
    "canceled",
]

DashboardProfileCompletenessBucket = Literal[
    "complete",
    "missing_email",
    "missing_research_direction",
    "missing_recent_papers",
    "missing_profile_url",
    "multiple_missing",
]


class DashboardMentorSummaryRead(ApiSchema):
    total_professors: int = 0
    matched_professors: int = 0
    matched_rate: float = 0.0
    high_match_professors: int = 0
    high_score_uncontacted_count: int = 0
    high_score_threshold: int = 80


class DashboardMentorMatchBucketRead(ApiSchema):
    bucket: DashboardMentorMatchBucket
    label: str
    count: int = 0


class DashboardProfileCompletenessRead(ApiSchema):
    key: Literal["email", "research_direction", "recent_papers", "profile_url", "complete"]
    label: str
    count: int = 0
    total: int = 0
    rate: float = 0.0


class DashboardSchoolDistributionRead(ApiSchema):
    school_name: str
    count: int = 0


class DashboardSchoolFilterSchoolRead(ApiSchema):
    school_name: str
    count: int = 0


class DashboardSchoolFilterRead(ApiSchema):
    university: str
    count: int = 0
    schools: list[DashboardSchoolFilterSchoolRead] = Field(default_factory=list)


class DashboardMentorFilterRead(ApiSchema):
    university: str | None = None
    school: str | None = None


class DashboardMatchContextRead(ApiSchema):
    source_identity_id: int
    source_identity_name: str
    source_identity_email: str
    source_material_id: int | None = None
    source_material_name: str | None = None
    uses_group_match_source: bool = False
    stale_result_count: int = 0


class DashboardProfileCompletenessBucketRead(ApiSchema):
    key: DashboardProfileCompletenessBucket
    label: str
    count: int = 0
    total: int = 0
    rate: float = 0.0


class DashboardMentorActionItemRead(ApiSchema):
    professor_id: int
    name: str
    university: str | None = None
    school: str | None = None
    department: str | None = None
    match_score: int | None = None
    status: str
    status_label: str
    reason: str
    updated_at: datetime
    missing_fields: list[str] = Field(default_factory=list)


class DashboardMentorSectionRead(ApiSchema):
    summary: DashboardMentorSummaryRead
    match_context: DashboardMatchContextRead
    match_score_distribution: list[DashboardMentorMatchBucketRead] = Field(default_factory=list)
    profile_completeness: list[DashboardProfileCompletenessRead] = Field(default_factory=list)
    profile_completeness_distribution: list[DashboardProfileCompletenessBucketRead] = Field(default_factory=list)
    school_distribution: list[DashboardSchoolDistributionRead] = Field(default_factory=list)
    school_filters: list[DashboardSchoolFilterRead] = Field(default_factory=list)
    active_filter: DashboardMentorFilterRead = Field(default_factory=DashboardMentorFilterRead)
    high_score_uncontacted: list[DashboardMentorActionItemRead] = Field(default_factory=list)
    incomplete_professors: list[DashboardMentorActionItemRead] = Field(default_factory=list)


class DashboardEmailSummaryRead(ApiSchema):
    sent_count: int = 0
    sent_professor_count: int = 0
    total_professor_count: int = 0
    sent_professor_rate: float = 0.0
    contacted_professor_count: int = 0
    replied_count: int = 0
    reply_rate: float = 0.0
    send_failed_count: int = 0
    send_failed_rate: float = 0.0
    review_required_count: int = 0
    scheduled_count: int = 0


class DashboardEmailTrendBucketRead(ApiSchema):
    date: str
    label: str | None = None
    sent_count: int = 0
    replied_count: int = 0
    failed_count: int = 0


class DashboardOutreachCoverageItemRead(ApiSchema):
    university: str
    school: str | None = None
    label: str
    sent_professor_count: int = 0
    total_professor_count: int = 0
    unsent_professor_count: int = 0
    sent_professor_rate: float = 0.0
    contacted_professor_count: int = 0
    replied_professor_count: int = 0
    reply_rate: float = 0.0


class DashboardOutreachCoverageRead(ApiSchema):
    universities: list[DashboardOutreachCoverageItemRead] = Field(default_factory=list)
    schools: list[DashboardOutreachCoverageItemRead] = Field(default_factory=list)


class DashboardReplyWaitBucketRead(ApiSchema):
    key: Literal["within_24h", "1_3_days", "3_7_days", "7_14_days", "over_14_days"]
    label: str
    count: int = 0
    rate: float = 0.0


class DashboardReplyWaitRead(ApiSchema):
    sample_count: int = 0
    median_hours: float | None = None
    p75_hours: float | None = None
    distribution: list[DashboardReplyWaitBucketRead] = Field(default_factory=list)


class DashboardEmailFunnelBucketRead(ApiSchema):
    key: str
    label: str
    count: int = 0


class DashboardEmailStatusBucketRead(ApiSchema):
    status: DashboardEmailStatusKey
    label: str
    count: int = 0


class DashboardEmailFollowUpRead(ApiSchema):
    professor_id: int
    task_id: int
    name: str
    university: str | None = None
    school: str | None = None
    department: str | None = None
    match_score: int | None = None
    status: str
    status_label: str
    reason: str
    updated_at: datetime


class DashboardEmailSectionRead(ApiSchema):
    summary: DashboardEmailSummaryRead
    trend_30_days: list[DashboardEmailTrendBucketRead] = Field(default_factory=list)
    outreach_coverage: DashboardOutreachCoverageRead = Field(default_factory=DashboardOutreachCoverageRead)
    reply_wait: DashboardReplyWaitRead = Field(default_factory=DashboardReplyWaitRead)
    funnel: list[DashboardEmailFunnelBucketRead] = Field(default_factory=list)
    status_distribution: list[DashboardEmailStatusBucketRead] = Field(default_factory=list)
    follow_ups: list[DashboardEmailFollowUpRead] = Field(default_factory=list)


class DashboardOverviewRead(ApiSchema):
    mentor: DashboardMentorSectionRead
    email: DashboardEmailSectionRead
