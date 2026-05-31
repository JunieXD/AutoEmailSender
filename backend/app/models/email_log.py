from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.email_task import EmailTask
    from app.models.identity_profile import IdentityProfile
    from app.models.llm_profile import LLMProfile
    from app.models.professor import Professor


class EmailDirection(StrEnum):
    SENT = "sent"
    RECEIVED = "received"
    DRAFT = "draft"


class EmailLog(Base):
    __tablename__ = "email_logs"
    __table_args__ = (
        UniqueConstraint("rfc_message_id", name="uq_email_logs_rfc_message_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_tasks.id"),
        index=True,
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
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    rfc_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_payload: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    failure_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_headers: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    email_task: Mapped["EmailTask | None"] = relationship(
        back_populates="email_logs",
    )
    identity: Mapped["IdentityProfile"] = relationship(
        back_populates="email_logs",
    )
    llm_profile: Mapped["LLMProfile"] = relationship(
        back_populates="email_logs",
    )
    professor: Mapped["Professor"] = relationship(
        back_populates="email_logs",
    )
