from __future__ import annotations

from datetime import datetime

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
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.models.base import Base
from app.models.types import UTCDateTime


class AgentUiHandoff(Base):
    """A short-lived, typed handoff from an Agent command to the desktop UI."""

    __tablename__ = "agent_ui_handoffs"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_agent_ui_handoffs_idempotency_key",
        ),
        Index("ix_agent_ui_handoffs_status_expires_at", "status", "expires_at"),
        Index(
            "ix_agent_ui_handoffs_consumer_claim",
            "consumer_id",
            "status",
            "claim_expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    surface: Mapped[str] = mapped_column(String(80), nullable=False)
    route: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'pending'"),
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    selection_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    consumer_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    delivery_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    awaiting_user_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
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


class AgentUiHandoffItem(Base):
    """Materialized resource membership for a UI handoff."""

    __tablename__ = "agent_ui_handoff_items"
    __table_args__ = (
        UniqueConstraint(
            "handoff_id",
            "resource_type",
            "resource_id",
            name="uq_agent_ui_handoff_items_resource",
        ),
        Index(
            "ix_agent_ui_handoff_items_resource",
            "resource_type",
            "resource_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    handoff_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("agent_ui_handoffs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(120), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
