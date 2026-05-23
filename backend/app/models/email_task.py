from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.batch_task import BatchTask
    from app.models.email_log import EmailLog
    from app.models.identity_profile import IdentityProfile
    from app.models.identity_material import IdentityMaterial
    from app.models.llm_profile import LLMProfile
    from app.models.professor import Professor


class EmailTaskStatus(StrEnum):
    DISCOVERED = "discovered"
    MATCHED = "matched"
    GENERATING_DRAFT = "generating_draft"
    DRAFT_FAILED = "draft_failed"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    SENDING = "sending"
    SENT = "sent"
    SEND_FAILED = "send_failed"
    REPLY_DETECTED = "reply_detected"
    CANCELED = "canceled"


class EmailTaskSource(StrEnum):
    MANUAL = "manual"
    BATCH = "batch"


class EmailTaskCancellationReason(StrEnum):
    BATCH_STOPPED = "batch_stopped"
    SCHEDULE_EXPIRED = "schedule_expired"
    USER_REMOVED = "user_removed"


class EmailTask(Base):
    __tablename__ = "email_tasks"
    __table_args__ = (
        UniqueConstraint("parent_task_id", name="uq_email_tasks_parent_task_id"),
        Index(
            "uq_email_tasks_workspace_task",
            "professor_id",
            "identity_id",
            "llm_profile_id",
            unique=True,
            sqlite_where=text("source = 'manual' AND batch_task_id IS NULL AND parent_task_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'manual'"),
    )
    batch_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("batch_tasks.id"),
        index=True,
        nullable=True,
    )
    parent_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_tasks.id"),
        nullable=True,
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
    professor_id: Mapped[int] = mapped_column(
        ForeignKey("professors.id"),
        index=True,
        nullable=False,
    )
    primary_material_id: Mapped[int | None] = mapped_column(
        ForeignKey("identity_materials.id"),
        index=True,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'discovered'"),
    )
    cancellation_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    draft_generation_previous_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    match_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_content_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    outreach_generation_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    outreach_template_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outreach_template_body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    outreach_template_body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_material_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    fit_points: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    risk_points: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    match_keywords: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    approved_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_send_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_rfc_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("0"),
    )
    is_replied: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("0"),
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    batch_task: Mapped["BatchTask | None"] = relationship(
        back_populates="email_tasks",
    )
    parent_task: Mapped["EmailTask | None"] = relationship(
        back_populates="child_tasks",
        remote_side=lambda: [EmailTask.id],
        foreign_keys=[parent_task_id],
    )
    child_tasks: Mapped[list["EmailTask"]] = relationship(
        back_populates="parent_task",
        foreign_keys=[parent_task_id],
    )
    identity: Mapped["IdentityProfile"] = relationship(
        back_populates="email_tasks",
    )
    llm_profile: Mapped["LLMProfile"] = relationship(
        back_populates="email_tasks",
    )
    primary_material: Mapped["IdentityMaterial | None"] = relationship(
        foreign_keys=[primary_material_id],
    )
    professor: Mapped["Professor"] = relationship(
        back_populates="email_tasks",
    )
    email_logs: Mapped[list["EmailLog"]] = relationship(
        back_populates="email_task",
        cascade="all, delete-orphan",
    )
