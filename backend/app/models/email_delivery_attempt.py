from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class EmailDeliveryAttemptStatus(StrEnum):
    PREPARED = "prepared"
    ACCEPTED = "accepted"
    FAILED = "failed"
    UNKNOWN = "unknown"


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
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
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
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
