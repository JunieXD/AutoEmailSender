from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, JSON, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.email_task import EmailTask
    from app.models.identity_material import IdentityMaterial
    from app.models.identity_profile import IdentityProfile
    from app.models.llm_profile import LLMProfile
    from app.models.professor import Professor


class MatchAnalysisRun(Base):
    __tablename__ = "match_analysis_runs"
    __table_args__ = (
        Index(
            "uq_match_analysis_runs_running_per_task",
            "email_task_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ),
        Index(
            "uq_match_analysis_runs_running_per_identity_professor",
            "identity_id",
            "professor_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_tasks.id"),
        index=True,
        nullable=True,
    )
    professor_id: Mapped[int] = mapped_column(
        ForeignKey("professors.id"),
        index=True,
        nullable=False,
    )
    identity_id: Mapped[int] = mapped_column(
        ForeignKey("identity_profiles.id"),
        index=True,
        nullable=False,
    )
    llm_profile_id: Mapped[int] = mapped_column(
        ForeignKey("llm_profiles.id"),
        index=True,
        nullable=False,
    )
    primary_material_id: Mapped[int | None] = mapped_column(
        ForeignKey("identity_materials.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'failed'")
    )
    success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0")
    )
    match_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    fit_points: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    risk_points: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    match_keywords: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    endpoint_kind: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stable_prefix_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        index=True,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    email_task: Mapped["EmailTask | None"] = relationship()
    professor: Mapped["Professor"] = relationship()
    identity: Mapped["IdentityProfile"] = relationship()
    llm_profile: Mapped["LLMProfile"] = relationship()
    primary_material: Mapped["IdentityMaterial | None"] = relationship()
