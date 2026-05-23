from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
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
    match_analysis_job_worker_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    match_analysis_job_item_concurrency: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("3"),
    )
    match_analysis_job_interval_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("10"),
    )
    crawler_worker_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("2"),
    )
    crawler_profile_enrichment_concurrency: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("3"),
    )
    crawler_host_concurrency: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    draft_max_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("6000"),
    )
    batch_draft_generation_concurrency: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("3"),
    )
    draft_rewrite_intensity: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'moderate'"),
    )
    draft_rewrite_tone: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'polite'"),
    )
    draft_rewrite_formality: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'balanced'"),
    )
    draft_rewrite_length: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'default'"),
    )
    draft_rewrite_specificity: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'balanced'"),
    )
    draft_template_preservation: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'structure_first'"),
    )
    draft_custom_instruction: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("''"),
    )
