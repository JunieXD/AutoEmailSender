from __future__ import annotations

from datetime import datetime

from app.core.time import utc_now

from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.batch_task import BatchTask
    from app.models.email_log import EmailLog
    from app.models.identity_profile import IdentityProfile
    from app.models.identity_material import IdentityMaterial
    from app.models.llm_profile import LLMProfile
    from app.models.outreach_template import OutreachTemplate
    from app.models.professor import Professor


class EmailTaskStatus(StrEnum):
    DISCOVERED = "discovered"
    MATCHED = "matched"
    GENERATING_DRAFT = "generating_draft"
    DRAFT_FAILED = "draft_failed"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    SCHEDULE_MISSED = "schedule_missed"
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
    PROFESSOR_ARCHIVED = "professor_archived"


class EmailTask(Base):
    __tablename__ = "email_tasks"
    __table_args__ = (
        UniqueConstraint("parent_task_id", name="uq_email_tasks_parent_task_id"),
        Index(
            "uq_email_tasks_workspace_task",
            "professor_id",
            "identity_id",
            unique=True,
            sqlite_where=text(
                "source = 'manual' AND batch_task_id IS NULL AND parent_task_id IS NULL"
            ),
        ),
        Index(
            "ix_email_tasks_identity_professor_created_id",
            "identity_id",
            "professor_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_email_tasks_identity_root_active_status",
            "identity_id",
            "parent_task_id",
            "batch_send_canceled_at",
            "status",
            "professor_id",
        ),
        Index(
            "ix_email_tasks_identity_status_scheduled_professor",
            "identity_id",
            "status",
            "scheduled_at",
            "professor_id",
        ),
        Index(
            "ix_email_tasks_dispatch_ready",
            "approved_at",
            "created_at",
            "id",
            sqlite_where=text("status = 'approved' OR status = 'scheduled'"),
            postgresql_where=text("status = 'approved' OR status = 'scheduled'"),
        ),
        Index(
            "ix_email_tasks_unstarted_generation_recovery",
            "updated_at",
            sqlite_where=text(
                "status = 'generating_draft' AND draft_generation_started_at IS NULL"
            ),
            postgresql_where=text(
                "status = 'generating_draft' AND draft_generation_started_at IS NULL"
            ),
        ),
        Index(
            "ix_email_tasks_started_generation_recovery",
            "draft_generation_started_at",
            sqlite_where=text(
                "status = 'generating_draft' "
                "AND draft_generation_started_at IS NOT NULL"
            ),
            postgresql_where=text(
                "status = 'generating_draft' "
                "AND draft_generation_started_at IS NOT NULL"
            ),
        ),
        Index(
            "ix_email_tasks_batch_draft_lease_recovery",
            "draft_lease_expires_at",
            sqlite_where=text(
                "status = 'generating_draft' AND draft_claim_id IS NOT NULL"
            ),
            postgresql_where=text(
                "status = 'generating_draft' AND draft_claim_id IS NOT NULL"
            ),
        ),
        Index(
            "ix_email_tasks_batch_sent_at",
            "batch_task_id",
            "sent_at",
            sqlite_where=text(
                "batch_task_id IS NOT NULL "
                "AND (status = 'sent' OR status = 'reply_detected')"
            ),
            postgresql_where=text(
                "batch_task_id IS NOT NULL "
                "AND (status = 'sent' OR status = 'reply_detected')"
            ),
        ),
        Index(
            "ix_email_tasks_schedule_canceled_at",
            "schedule_canceled_at",
            sqlite_where=text("schedule_canceled_at IS NOT NULL"),
            postgresql_where=text("schedule_canceled_at IS NOT NULL"),
        ),
        Index(
            "ix_email_tasks_delivery_upcoming_schedule",
            "scheduled_at",
            "id",
            sqlite_where=text(
                "schedule_canceled_at IS NULL AND batch_send_canceled_at IS NULL"
            ),
            postgresql_where=text(
                "schedule_canceled_at IS NULL AND batch_send_canceled_at IS NULL"
            ),
        ),
        Index(
            "ix_email_tasks_delivery_attention_updated",
            "updated_at",
            "id",
            sqlite_where=text(
                "schedule_canceled_at IS NULL AND batch_send_canceled_at IS NULL"
            ),
            postgresql_where=text(
                "schedule_canceled_at IS NULL AND batch_send_canceled_at IS NULL"
            ),
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
    batch_send_canceled_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    draft_generation_previous_status: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    draft_generation_started_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    draft_claim_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    draft_claimed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    draft_lease_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    draft_rewrite_source_subject: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    draft_rewrite_source_body_text: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    draft_rewrite_source_body_html: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    draft_rewrite_source_selected_material_ids: Mapped[list[int] | None] = (
        mapped_column(JSON, nullable=True)
    )
    # Historical compatibility snapshot. The canonical current result lives in
    # identity_professor_match_results and is resolved by identity + professor.
    # This plain integer intentionally preserves provenance even if the source
    # identity is later deleted.
    match_source_identity_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    match_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_content_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_generation_source: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    draft_fallback_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outreach_generation_mode: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    outreach_template_subject: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    outreach_template_body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    outreach_template_body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    outreach_template_snapshot_version: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    outreach_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("outreach_templates.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    selected_material_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    fit_points: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    risk_points: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    match_keywords: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    approved_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    last_scheduled_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    schedule_canceled_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    last_send_attempt_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
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
    outreach_template: Mapped["OutreachTemplate | None"] = relationship(
        back_populates="email_tasks",
        foreign_keys=[outreach_template_id],
    )
    professor: Mapped["Professor"] = relationship(
        back_populates="email_tasks",
    )
    email_logs: Mapped[list["EmailLog"]] = relationship(
        back_populates="email_task",
        cascade="all, delete-orphan",
    )
