from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app.core.time import utc_now

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.batch_task import BatchTask
    from app.models.email_log import EmailLog
    from app.models.email_task import EmailTask
    from app.models.identity_communication_group import IdentityCommunicationGroup
    from app.models.identity_material import IdentityMaterial
    from app.models.outreach_template import OutreachTemplate


class IdentityProfile(Base):
    __tablename__ = "identity_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    profile_name: Mapped[str] = mapped_column(String(100), nullable=False)
    sender_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email_address: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )
    smtp_host: Mapped[str] = mapped_column(String(255), nullable=False)
    smtp_port: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("465"),
    )
    smtp_username: Mapped[str] = mapped_column(String(255), nullable=False)
    smtp_password: Mapped[str] = mapped_column(String(255), nullable=False)
    imap_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imap_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    default_language: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'zh-CN'"),
    )
    outreach_generation_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'llm'"),
    )
    outreach_template_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outreach_template_body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    outreach_template_body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_outreach_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("outreach_templates.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    current_primary_material_id: Mapped[int | None] = mapped_column(
        ForeignKey("identity_materials.id"),
        nullable=True,
    )
    communication_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("identity_communication_groups.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    match_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_send_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    send_interval_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    send_interval_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_send_after: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    same_domain_cooldown_minutes: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
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

    materials: Mapped[list["IdentityMaterial"]] = relationship(
        back_populates="identity",
        cascade="all, delete-orphan",
        foreign_keys="IdentityMaterial.identity_id",
    )
    current_primary_material: Mapped["IdentityMaterial | None"] = relationship(
        foreign_keys=[current_primary_material_id],
        post_update=True,
        uselist=False,
    )
    communication_group: Mapped["IdentityCommunicationGroup | None"] = relationship(
        back_populates="members",
        foreign_keys=[communication_group_id],
    )
    default_outreach_template: Mapped["OutreachTemplate | None"] = relationship(
        back_populates="default_for_identities",
        foreign_keys=[default_outreach_template_id],
        lazy="joined",
    )
    email_tasks: Mapped[list["EmailTask"]] = relationship(
        back_populates="identity",
        cascade="all, delete-orphan",
    )
    batch_tasks: Mapped[list["BatchTask"]] = relationship(
        back_populates="identity",
        cascade="all, delete-orphan",
    )
    email_logs: Mapped[list["EmailLog"]] = relationship(
        back_populates="identity",
        cascade="all, delete-orphan",
    )
