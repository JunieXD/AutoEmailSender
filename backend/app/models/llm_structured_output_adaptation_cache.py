from __future__ import annotations

from datetime import datetime

from app.core.time import utc_now

from sqlalchemy import Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class LLMStructuredOutputAdaptationCache(Base):
    """Learned structured-output protocol for one model and endpoint."""

    __tablename__ = "llm_structured_output_adaptation_cache"
    __table_args__ = (
        UniqueConstraint(
            "api_base_url",
            "model_name",
            "endpoint_kind",
            "probe_version",
            name="uq_llm_structured_output_adaptation_target",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    api_base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    endpoint_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    probe_version: Mapped[int] = mapped_column(Integer, nullable=False)
    learned_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    probed_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
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
