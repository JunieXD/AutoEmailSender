from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app.core.time import utc_now

from sqlalchemy import Boolean, Float, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.batch_task import BatchTask
    from app.models.email_log import EmailLog
    from app.models.email_task import EmailTask


class LLMProfile(Base):
    __tablename__ = "llm_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'openai'"),
    )
    api_base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_key: Mapped[str] = mapped_column(String(255), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    matcher_prompt_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    writer_prompt_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_default: Mapped[bool] = mapped_column(
        Boolean,
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

    email_tasks: Mapped[list["EmailTask"]] = relationship(
        back_populates="llm_profile",
        cascade="all, delete-orphan",
    )
    batch_tasks: Mapped[list["BatchTask"]] = relationship(
        back_populates="llm_profile",
        cascade="all, delete-orphan",
    )
    email_logs: Mapped[list["EmailLog"]] = relationship(
        back_populates="llm_profile",
        cascade="all, delete-orphan",
    )
