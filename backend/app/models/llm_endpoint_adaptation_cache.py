from __future__ import annotations

from datetime import datetime

from app.core.time import utc_now

from sqlalchemy import String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class LLMEndpointAdaptationCache(Base):
    """Per-(base_url, model_name) cache of the learned LLM endpoint protocol."""

    __tablename__ = "llm_endpoint_adaptation_cache"
    __table_args__ = (
        UniqueConstraint(
            "api_base_url",
            "model_name",
            name="uq_llm_endpoint_adaptation_cache_target",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    api_base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    learned_endpoint_kind: Mapped[str] = mapped_column(String(32), nullable=False)
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
