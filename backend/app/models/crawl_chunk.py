from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class CrawlPageChunkStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    NO_CANDIDATES = "no_candidates"
    SPLIT_REQUIRED = "split_required"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class CrawlPageChunk(Base):
    __tablename__ = "crawl_page_chunks"
    __table_args__ = (
        UniqueConstraint("job_id", "chunk_id", name="uq_crawl_page_chunks_job_chunk_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("crawl_jobs.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_pages.id", ondelete="CASCADE"), index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    page_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_chunk_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True, server_default=text("'pending'"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    text_start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overlap_prefix: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    overlap_suffix: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    split_depth: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    split_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
    )

    job = relationship("CrawlJob")
    page = relationship("CrawlPage")
