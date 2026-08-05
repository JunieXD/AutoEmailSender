from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app.core.time import utc_now

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.email_task import EmailTask
    from app.models.identity_material import IdentityMaterial
    from app.models.identity_profile import IdentityProfile
    from app.models.llm_profile import LLMProfile
    from app.models.match_analysis_run import MatchAnalysisRun
    from app.models.professor import Professor


class IdentityProfessorMatchResult(Base):
    """Canonical current match result for one identity and one professor.

    EmailTask keeps a compatibility snapshot for historical task rendering. New
    product reads must use this record (through the match-result resolver) as
    the source of truth.
    """

    __tablename__ = "identity_professor_match_results"
    __table_args__ = (
        UniqueConstraint(
            "identity_id",
            "professor_id",
            name="uq_identity_professor_match_results_identity_professor",
        ),
        CheckConstraint(
            "match_score >= 0 AND match_score <= 100",
            name="ck_identity_professor_match_results_score_range",
        ),
        Index(
            "ix_identity_professor_match_results_identity_updated",
            "identity_id",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    identity_id: Mapped[int] = mapped_column(
        ForeignKey("identity_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    professor_id: Mapped[int] = mapped_column(
        ForeignKey("professors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    llm_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    primary_material_id: Mapped[int | None] = mapped_column(
        ForeignKey("identity_materials.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_email_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    latest_analysis_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("match_analysis_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    match_score: Mapped[int] = mapped_column(Integer, nullable=False)
    match_reason: Mapped[str] = mapped_column(Text, nullable=False)
    fit_points: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    risk_points: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    match_keywords: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    analyzed_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
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

    identity: Mapped["IdentityProfile"] = relationship(
        back_populates="professor_match_results",
        foreign_keys=[identity_id],
    )
    professor: Mapped["Professor"] = relationship(
        back_populates="identity_match_results",
        foreign_keys=[professor_id],
    )
    llm_profile: Mapped["LLMProfile | None"] = relationship(
        foreign_keys=[llm_profile_id],
    )
    primary_material: Mapped["IdentityMaterial | None"] = relationship(
        foreign_keys=[primary_material_id],
    )
    source_email_task: Mapped["EmailTask | None"] = relationship(
        foreign_keys=[source_email_task_id],
    )
    latest_analysis_run: Mapped["MatchAnalysisRun | None"] = relationship(
        foreign_keys=[latest_analysis_run_id],
    )
