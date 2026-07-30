from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app.core.time import utc_now

from sqlalchemy import Boolean, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.email_task import EmailTask
    from app.models.identity_profile import IdentityProfile
    from app.models.test_compose_session import TestComposeSession


class OutreachTemplate(Base):
    __tablename__ = "outreach_templates"
    __table_args__ = (
        Index(
            "uq_outreach_templates_global_default",
            "is_default",
            unique=True,
            sqlite_where=text("is_default = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    recommended_generation_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'llm'"),
    )
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("0"),
    )
    migrated_from_identity_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        unique=True,
    )
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
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

    default_for_identities: Mapped[list["IdentityProfile"]] = relationship(
        back_populates="default_outreach_template",
        foreign_keys="IdentityProfile.default_outreach_template_id",
    )
    email_tasks: Mapped[list["EmailTask"]] = relationship(
        back_populates="outreach_template",
        foreign_keys="EmailTask.outreach_template_id",
    )
    test_compose_sessions: Mapped[list["TestComposeSession"]] = relationship(
        back_populates="outreach_template",
        foreign_keys="TestComposeSession.outreach_template_id",
    )
