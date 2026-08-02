from __future__ import annotations

from datetime import datetime

from app.core.time import utc_now

from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.email_task import EmailTask
    from app.models.identity_profile import IdentityProfile
    from app.models.identity_material import IdentityMaterial
    from app.models.llm_profile import LLMProfile


class BatchTaskStatus(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    EXPIRED = "expired"


class BatchTask(Base):
    __tablename__ = "batch_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    identity_id: Mapped[int] = mapped_column(
        ForeignKey("identity_profiles.id"),
        index=True,
        nullable=False,
    )
    llm_profile_id: Mapped[int] = mapped_column(
        ForeignKey("llm_profiles.id"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    schedule_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'immediate'"),
    )
    window_start_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    window_end_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    emails_per_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scheduled_dates: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'running'"),
    )
    primary_material_id: Mapped[int | None] = mapped_column(
        ForeignKey("identity_materials.id"),
        index=True,
        nullable=True,
    )
    outreach_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("outreach_templates.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    outreach_template_name_snapshot: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )
    outreach_template_snapshot_version: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    outreach_generation_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    outreach_template_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outreach_template_body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    outreach_template_body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_material_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    target_count: Mapped[int] = mapped_column(
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
    deleted_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        index=True,
        nullable=True,
    )

    identity: Mapped["IdentityProfile"] = relationship(
        back_populates="batch_tasks",
    )
    llm_profile: Mapped["LLMProfile"] = relationship(
        back_populates="batch_tasks",
    )
    primary_material: Mapped["IdentityMaterial | None"] = relationship(
        foreign_keys=[primary_material_id],
    )
    email_tasks: Mapped[list["EmailTask"]] = relationship(
        back_populates="batch_task",
        cascade="all, delete-orphan",
    )
