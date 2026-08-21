from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class EmailObservationResolution(StrEnum):
    MATCHED = "matched"
    EXTERNAL = "external"
    PENDING = "pending"


class EmailObservation(Base):
    __tablename__ = "email_observations"
    __table_args__ = (
        Index(
            "uq_email_observations_imap_location",
            "identity_id",
            "professor_id",
            "folder_role",
            "folder",
            "uidvalidity",
            "imap_uid",
            unique=True,
            sqlite_where=text(
                "professor_id IS NOT NULL "
                "AND folder_role IS NOT NULL "
                "AND folder IS NOT NULL "
                "AND uidvalidity IS NOT NULL "
                "AND imap_uid IS NOT NULL"
            ),
            postgresql_where=text(
                "professor_id IS NOT NULL "
                "AND folder_role IS NOT NULL "
                "AND folder IS NOT NULL "
                "AND uidvalidity IS NOT NULL "
                "AND imap_uid IS NOT NULL"
            ),
        ),
        Index(
            "uq_email_observations_legacy_log",
            "legacy_email_log_id",
            unique=True,
            sqlite_where=text("legacy_email_log_id IS NOT NULL"),
            postgresql_where=text("legacy_email_log_id IS NOT NULL"),
        ),
        Index(
            "ix_email_observations_delivery_key",
            "identity_id",
            "delivery_key",
        ),
        Index(
            "ix_email_observations_message_lookup",
            "identity_id",
            "professor_id",
            "direction",
            "normalized_message_id",
        ),
        Index(
            "ix_email_observations_pending",
            "resolution",
            "identity_id",
            "professor_id",
            "message_sent_at",
        ),
        Index(
            "uq_email_observations_pending_candidate_log",
            "candidate_email_log_id",
            unique=True,
            sqlite_where=text(
                "candidate_email_log_id IS NOT NULL AND resolution = 'pending'"
            ),
            postgresql_where=text(
                "candidate_email_log_id IS NOT NULL AND resolution = 'pending'"
            ),
        ),
        Index(
            "uq_email_observations_pending_candidate_attempt",
            "delivery_attempt_id",
            unique=True,
            sqlite_where=text(
                "delivery_attempt_id IS NOT NULL "
                "AND candidate_email_log_id IS NULL "
                "AND resolution = 'pending'"
            ),
            postgresql_where=text(
                "delivery_attempt_id IS NOT NULL "
                "AND candidate_email_log_id IS NULL "
                "AND resolution = 'pending'"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email_log_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_logs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    candidate_email_log_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_logs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    delivery_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("email_delivery_attempts.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    legacy_email_log_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_logs.id", ondelete="SET NULL"),
        nullable=True,
    )
    identity_id: Mapped[int] = mapped_column(
        ForeignKey("identity_profiles.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    professor_id: Mapped[int | None] = mapped_column(
        ForeignKey("professors.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    resolution: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'pending'"),
    )
    match_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    delivery_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_message_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    folder_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    folder: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uidvalidity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imap_uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    from_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_emails: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    cc_emails: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    bcc_emails: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    content_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_fingerprint: Mapped[str | None] = mapped_column(String(71), nullable=True)
    content_fingerprint: Mapped[str | None] = mapped_column(String(71), nullable=True)
    message_sent_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    headers: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    provider_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
