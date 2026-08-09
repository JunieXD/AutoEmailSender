from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, text
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
        Index(
            "uq_email_logs_identity_professor_direction_message",
            "identity_id",
            "professor_id",
            "direction",
            "normalized_message_id",
            unique=True,
            sqlite_where=text("normalized_message_id IS NOT NULL"),
            postgresql_where=text("normalized_message_id IS NOT NULL"),
        ),
        Index(
            "uq_email_logs_identity_professor_imap_uid",
            "identity_id",
            "professor_id",
            "folder_role",
            "folder",
            "uidvalidity",
            "imap_uid",
            unique=True,
            sqlite_where=text(
                "folder_role IS NOT NULL "
                "AND folder IS NOT NULL "
                "AND uidvalidity IS NOT NULL "
                "AND imap_uid IS NOT NULL",
            ),
            postgresql_where=text(
                "folder_role IS NOT NULL "
                "AND folder IS NOT NULL "
                "AND uidvalidity IS NOT NULL "
                "AND imap_uid IS NOT NULL",
            ),
        ),
        Index(
            "uq_email_logs_identity_professor_direction_fingerprint",
            "identity_id",
            "professor_id",
            "direction",
            "message_fingerprint",
            unique=True,
            sqlite_where=text("message_fingerprint IS NOT NULL"),
            postgresql_where=text("message_fingerprint IS NOT NULL"),
        ),
        Index(
            "ix_email_logs_status_identity_professor_direction_created",
            "identity_id",
            "professor_id",
            "direction",
            "created_at",
            "id",
        ),
        Index(
            "ix_email_logs_identity_direction_professor_created",
            "identity_id",
            "direction",
            "professor_id",
            "created_at",
            "id",
        ),
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
    llm_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_profiles.id"),
        index=True,
        nullable=True,
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
    ingest_source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'system'"),
    )
    folder_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    folder: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uidvalidity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imap_uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    normalized_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_emails: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    cc_emails: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    bcc_emails: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
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
    llm_profile: Mapped["LLMProfile | None"] = relationship(
        back_populates="email_logs",
    )
    professor: Mapped["Professor"] = relationship(
        back_populates="email_logs",
    )
