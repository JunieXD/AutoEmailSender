from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.identity_profile import IdentityProfile
    from app.models.llm_profile import LLMProfile
    from app.models.test_compose_session import TestComposeSession


class TestComposeMessage(Base):
    __tablename__ = "test_compose_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("test_compose_sessions.id"),
        index=True,
        nullable=False,
    )
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
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'sent'"),
    )
    rfc_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    failure_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    session: Mapped["TestComposeSession"] = relationship(
        back_populates="messages",
    )
    identity: Mapped["IdentityProfile"] = relationship()
    llm_profile: Mapped["LLMProfile"] = relationship()
