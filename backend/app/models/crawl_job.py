from __future__ import annotations

from datetime import datetime

from app.core.time import utc_now

from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.llm_profile import LLMProfile
    from app.models.professor import Professor


class CrawlJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    NEEDS_REVIEW = "needs_review"
    PARTIALLY_COMPLETED = "partially_completed"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class CrawlJobEntryType(str, Enum):
    LIST = "list"
    PROFILE = "profile"


class CrawlJobKind(str, Enum):
    FACULTY_CRAWL = "faculty_crawl"
    PROFESSOR_ENRICHMENT = "professor_enrichment"


class CrawlJobTriggerMode(str, Enum):
    CRAWL = "crawl"
    SINGLE = "single"
    BATCH = "batch"


class CrawlRuntimeVersion(str, Enum):
    V1 = "v1"
    V2 = "v2"

class CrawlPageTaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    SKIPPED_DUPLICATE = "skipped_duplicate"

class CrawlCandidateEnrichmentTaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    CANCELED = "canceled"

class CrawlWorkerKind(str, Enum):
    PAGE = "page"
    CHUNK = "chunk"
    ENRICHMENT = "enrichment"

class CrawlPageFetchMode(str, Enum):
    DIRECT = "direct"
    BROWSER = "browser"


class CrawlPageStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CrawlPageFetchStatus(str, Enum):
    SUCCEEDED = "succeeded"
    CHUNKED = "chunked"
    PROCESSED = "processed"
    TRANSIENT_FAILED = "transient_failed"
    TERMINAL_FAILED = "terminal_failed"

class CrawlCandidateReviewStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MERGED = "merged"


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"
    __table_args__ = (
        Index(
            "ix_crawl_jobs_kind_deleted_created_id",
            "job_kind",
            "deleted_at",
            "created_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    university: Mapped[str] = mapped_column(String(255), nullable=False)
    school: Mapped[str] = mapped_column(String(255), nullable=False)
    start_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    start_urls: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    entry_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'list'"),
    )
    job_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
        server_default=text("'faculty_crawl'"),
    )
    trigger_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'crawl'"),
    )
    task_center_visible: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("1"),
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    runtime_version: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'v2'"),
        index=True,
    )
    llm_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
        server_default=text("'queued'"),
    )
    progress_current: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_trace: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)
    current_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("crawl_job_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=utc_now,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        index=True,
        nullable=True,
    )

    llm_profile: Mapped["LLMProfile | None"] = relationship()
    current_run: Mapped["CrawlJobRun | None"] = relationship(
        foreign_keys=[current_run_id],
        post_update=True,
    )
    runs: Mapped[list["CrawlJobRun"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        foreign_keys="CrawlJobRun.job_id",
    )
    pages: Mapped[list["CrawlPage"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )
    page_fetch_states: Mapped[list["CrawlPageFetchState"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )
    page_tasks: Mapped[list["CrawlPageTask"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )
    candidates: Mapped[list["CrawlCandidate"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )
    enrichment_tasks: Mapped[list["CrawlCandidateEnrichmentTask"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )
    token_usages: Mapped[list["CrawlWorkerTokenUsage"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )


class CrawlJobRun(Base):
    __tablename__ = "crawl_job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("crawl_jobs.id", ondelete="CASCADE"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    active_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    active_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cached_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    host_limited_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failed_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    unchanged_candidate_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=utc_now,
    )

    job: Mapped["CrawlJob"] = relationship(
        back_populates="runs",
        foreign_keys=[job_id],
    )


class CrawlPage(Base):
    __tablename__ = "crawl_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("crawl_jobs.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    parent_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    fetch_method: Mapped[str] = mapped_column(String(64), nullable=False)
    page_type: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'unknown'"))
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    text_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    job: Mapped["CrawlJob"] = relationship(back_populates="pages")




class CrawlPageFetchState(Base):
    __tablename__ = "crawl_page_fetch_states"
    __table_args__ = (
        UniqueConstraint("job_id", "normalized_url", name="uq_crawl_page_fetch_states_job_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("crawl_jobs.id", ondelete="CASCADE"), index=True)
    normalized_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    original_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    last_fetch_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fetch_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direct_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    browser_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    terminal_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transient_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_page_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_pages.id", ondelete="SET NULL"), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    last_attempted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=utc_now,
    )

    job: Mapped["CrawlJob"] = relationship(back_populates="page_fetch_states")
    last_page: Mapped["CrawlPage | None"] = relationship()


class CrawlCandidate(Base):
    __tablename__ = "crawl_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("crawl_jobs.id", ondelete="CASCADE"), index=True)
    professor_id: Mapped[int | None] = mapped_column(
        ForeignKey("professors.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    university: Mapped[str | None] = mapped_column(String(255), nullable=True)
    school: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    research_direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    recent_papers: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    profile_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    field_confidence: Mapped[dict[str, float] | None] = mapped_column(JSON, nullable=True)
    evidence: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    source_chunk_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    boundary_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    identity_key: Mapped[str | None] = mapped_column(String(1000), nullable=True, index=True)
    merge_history: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)
    field_sources: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    conflicts: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'pending'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=utc_now,
    )

    job: Mapped["CrawlJob"] = relationship(back_populates="candidates")
    professor: Mapped["Professor | None"] = relationship()

class CrawlPageTask(Base):
    __tablename__ = "crawl_page_tasks"
    __table_args__ = (
        UniqueConstraint("job_id", "normalized_url", name="uq_crawl_page_tasks_job_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("crawl_jobs.id", ondelete="CASCADE"), index=True)
    normalized_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    original_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    parent_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    discovery_reason: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'start'"),
    )
    expansion_mode: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'entry'"),
    )
    allow_expansion: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True, server_default=text("'pending'"))
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetch_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direct_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    browser_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=utc_now)

    job: Mapped["CrawlJob"] = relationship(back_populates="page_tasks")

class CrawlCandidateEnrichmentTask(Base):
    __tablename__ = "crawl_candidate_enrichment_tasks"
    __table_args__ = (
        UniqueConstraint("job_id", "candidate_id", name="uq_crawl_candidate_enrichment_tasks_job_candidate"),
        Index(
            "uq_crawl_candidate_enrichment_tasks_active_professor",
            "professor_id",
            unique=True,
            sqlite_where=text(
                "professor_id IS NOT NULL AND status IN "
                "('pending', 'processing', 'failed_retryable')"
            ),
            postgresql_where=text(
                "professor_id IS NOT NULL AND status IN "
                "('pending', 'processing', 'failed_retryable')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("crawl_jobs.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("crawl_candidates.id", ondelete="CASCADE"), index=True)
    professor_id: Mapped[int | None] = mapped_column(
        ForeignKey("professors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True, server_default=text("'pending'"))
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    enriched_fields: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=utc_now)

    job: Mapped["CrawlJob"] = relationship(back_populates="enrichment_tasks")
    candidate: Mapped["CrawlCandidate"] = relationship()
    professor: Mapped["Professor | None"] = relationship()

class CrawlWorkerTokenUsage(Base):
    __tablename__ = "crawl_worker_token_usages"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("crawl_jobs.id", ondelete="CASCADE"), index=True)
    worker_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    work_item_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    raw_usage: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    job: Mapped["CrawlJob"] = relationship(back_populates="token_usages")
