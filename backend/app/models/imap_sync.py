from __future__ import annotations

from datetime import date, datetime

from app.core.time import utc_now

from enum import Enum

from sqlalchemy import Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class ImapProfessorHistoricalScanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ImapMailboxHistoricalScanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ImapFolderRole(str, Enum):
    INBOX = "inbox"
    SENT = "sent"


class ImapIdentitySyncLease(Base):
    __tablename__ = "imap_identity_sync_leases"
    __table_args__ = (
        Index("ix_imap_identity_sync_lease_expires", "lease_expires_at"),
    )

    identity_id: Mapped[int] = mapped_column(
        ForeignKey("identity_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    claim_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claim_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=utc_now,
    )


class ImapMailboxSyncState(Base):
    __tablename__ = "imap_mailbox_sync_states"
    __table_args__ = (
        Index(
            "ix_imap_mailbox_sync_identity_history_status_updated",
            "identity_id",
            "history_scan_status",
            "updated_at",
            "id",
        ),
        Index(
            "ix_imap_mailbox_history_lease_recovery",
            "history_lease_expires_at",
        ),
        UniqueConstraint(
            "identity_id",
            "folder_role",
            "folder",
            name="uq_imap_mailbox_identity_folder_role_folder",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    identity_id: Mapped[int] = mapped_column(
        ForeignKey("identity_profiles.id"),
        index=True,
        nullable=False,
    )
    folder_role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'inbox'"),
    )
    folder: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        server_default=text("'INBOX'"),
    )
    uidvalidity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seen_uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovered_sent_folder: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sent_folder_discovered_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    sent_folder_discovery_failed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    sent_folder_discovery_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    throttle_paused_until: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    throttle_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_professor_state_ensure_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    professor_state_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    history_scan_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'pending'"),
    )
    history_high_water_uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    history_next_before_uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    history_scan_started_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    history_scan_completed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    history_last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    history_scanned_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    history_matched_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    history_strategy_version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'folder-v1'"),
    )
    history_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    history_claim_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    history_lease_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
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


class ImapProfessorSyncState(Base):
    __tablename__ = "imap_professor_sync_states"
    __table_args__ = (
        Index(
            "ix_imap_professor_sync_identity_status_updated",
            "identity_id",
            "historical_scan_status",
            "updated_at",
            "id",
        ),
        Index(
            "ix_imap_professor_sync_recent_due",
            "identity_id",
            "history_strategy_version",
            "historical_scan_status",
            "available_at",
            "priority",
            "id",
        ),
        Index(
            "ix_imap_professor_history_lease_recovery",
            "history_lease_expires_at",
        ),
        UniqueConstraint(
            "identity_id",
            "professor_id",
            "professor_email",
            "folder_role",
            "folder",
            name="uq_imap_professor_identity_professor_email_folder_role_folder",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    identity_id: Mapped[int] = mapped_column(
        ForeignKey("identity_profiles.id"),
        index=True,
        nullable=False,
    )
    professor_id: Mapped[int] = mapped_column(
        ForeignKey("professors.id"),
        index=True,
        nullable=False,
    )
    professor_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    folder_role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'inbox'"),
    )
    folder: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        server_default=text("'INBOX'"),
    )
    historical_scan_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'pending'"),
    )
    last_scanned_uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    historical_scan_started_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    historical_scan_completed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    history_strategy_version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'legacy'"),
    )
    history_start_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    trigger_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    history_claim_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    history_lease_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    available_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    professor_sync_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
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
