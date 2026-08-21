from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app.core.time import utc_now

from sqlalchemy import ForeignKey, JSON, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.identity_profile import IdentityProfile
    from app.models.llm_profile import LLMProfile
    from app.models.outreach_template import OutreachTemplate
    from app.models.test_compose_message import TestComposeMessage


class TestComposeSession(Base):
    __tablename__ = "test_compose_sessions"

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
    outreach_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("outreach_templates.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("''"),
    )
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_material_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
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

    identity: Mapped["IdentityProfile"] = relationship()
    llm_profile: Mapped["LLMProfile"] = relationship()
    outreach_template: Mapped["OutreachTemplate | None"] = relationship(
        back_populates="test_compose_sessions",
        foreign_keys=[outreach_template_id],
    )
    messages: Mapped[list["TestComposeMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )
