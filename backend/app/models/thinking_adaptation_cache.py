from __future__ import annotations

from datetime import datetime

from app.core.time import utc_now

from sqlalchemy import DateTime, JSON, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class ThinkingAdaptationCache(Base):
    """Per-(base_url, model_name) cache of the extra_body needed to bypass thinking-mode protocol errors."""

    __tablename__ = "thinking_adaptation_cache"
    __table_args__ = (
        UniqueConstraint(
            "api_base_url",
            "model_name",
            name="uq_thinking_adaptation_cache_api_base_url",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    api_base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    learned_extra_body: Mapped[dict[str, object] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    probed_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
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
