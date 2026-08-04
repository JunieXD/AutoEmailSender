from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class AgentMutationReceipt(Base):
    """Persist the result of an idempotent, non-delivery Agent mutation."""

    __tablename__ = "agent_mutation_receipts"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_agent_mutation_receipts_idempotency_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    command: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
