from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, JSON, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.email_task import EmailTask


class EmailDeliveryOutcome(StrEnum):
    CLAIMED = "claimed"
    SMTP_ACCEPTED = "smtp_accepted"
    ASSUMED_SENT_AFTER_INTERRUPTION = "assumed_sent_after_interruption"
    PRE_SUBMISSION_FAILED = "pre_submission_failed"


class EmailDeliveryAttempt(Base):
    __tablename__ = "email_delivery_attempts"
    __table_args__ = (
        Index(
            "ix_email_delivery_attempts_task_claimed",
            "email_task_id",
            "claimed_at",
        ),
        Index(
            "ix_email_delivery_attempts_outcome_finalized",
            "outcome",
            "finalized_at",
        ),
    )

    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email_task_id: Mapped[int] = mapped_column(
        ForeignKey("email_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_role: Mapped[str] = mapped_column(String(16), nullable=False)
    runtime_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_generation: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_pid: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(
        String(48),
        nullable=False,
        server_default=text("'claimed'"),
    )
    claimed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    smtp_accepted_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    prepared_rfc_message_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
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

    email_task: Mapped["EmailTask"] = relationship(
        back_populates="delivery_attempts",
    )


__all__ = ["EmailDeliveryAttempt", "EmailDeliveryOutcome"]
