from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

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


class CrawlPageStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CrawlCandidateReviewStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MERGED = "merged"


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

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
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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
    candidates: Mapped[list["CrawlCandidate"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )


class CrawlJobRun(Base):
    __tablename__ = "crawl_job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("crawl_jobs.id", ondelete="CASCADE"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
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
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
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
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    job: Mapped["CrawlJob"] = relationship(back_populates="pages")


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
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
    )

    job: Mapped["CrawlJob"] = relationship(back_populates="candidates")
    professor: Mapped["Professor | None"] = relationship()
