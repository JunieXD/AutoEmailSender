from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.models.base import Base
from app.models.types import UTCDateTime


class EmailDeliveryAttemptStatus(StrEnum):
    PREPARED = "prepared"
    ACCEPTED = "accepted"
    FAILED = "failed"
    UNKNOWN = "unknown"


class EmailDeliveryOutcome(StrEnum):
    CLAIMED = "claimed"
    SMTP_ACCEPTED = "smtp_accepted"
    ASSUMED_SENT_AFTER_INTERRUPTION = "assumed_sent_after_interruption"
    PRE_SUBMISSION_FAILED = "pre_submission_failed"


class EmailDeliveryAttempt(Base):
    __tablename__ = "email_delivery_attempts"
    __table_args__ = (
        UniqueConstraint(
            "email_task_id",
            "attempt_number",
            name="uq_email_delivery_attempts_task_number",
        ),
        Index(
            "ix_email_delivery_attempts_identity_professor_started",
            "identity_id",
            "professor_id",
            "started_at",
            "id",
        ),
        Index(
            "ix_email_delivery_attempts_message_id",
            "identity_id",
            "normalized_app_message_id",
        ),
        Index(
            "ix_email_delivery_attempts_outcome_finalized",
            "outcome",
            "finalized_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # The split-runtime implementation originally called the primary key
    # attempt_id. Keep the Python alias while master owns the physical schema.
    attempt_id = synonym("id")
    email_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_tasks.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    identity_id: Mapped[int] = mapped_column(
        ForeignKey("identity_profiles.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    professor_id: Mapped[int] = mapped_column(
        ForeignKey("professors.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    app_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_app_message_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'prepared'"),
    )
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    claimed_at = synonym("started_at")
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    owner_role: Mapped[str] = mapped_column(
        String(16), server_default="legacy", nullable=False
    )
    runtime_id: Mapped[str] = mapped_column(
        String(128), server_default="legacy", nullable=False
    )
    owner_generation: Mapped[str] = mapped_column(
        String(128), server_default="pre-split", nullable=False
    )
    owner_pid: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(
        String(48),
        nullable=False,
        server_default=text("'claimed'"),
    )
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    smtp_accepted_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    prepared_rfc_message_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    subject: Mapped[str] = mapped_column(Text, server_default="", nullable=False)
    content: Mapped[str] = mapped_column(Text, server_default="", nullable=False)
    content_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    provider_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = [
    "EmailDeliveryAttempt",
    "EmailDeliveryAttemptStatus",
    "EmailDeliveryOutcome",
]
