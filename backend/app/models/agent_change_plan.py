from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.models.base import Base
from app.models.types import UTCDateTime


class AgentChangePlan(Base):
    """A confirmed, single-use plan for an Agent-initiated state change or delivery."""

    __tablename__ = "agent_change_plans"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_agent_change_plans_idempotency_key",
        ),
        Index("ix_agent_change_plans_status_expires_at", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'awaiting_confirmation'"),
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    execution_started_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    executed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
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
