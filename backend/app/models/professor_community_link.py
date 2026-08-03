from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.professor import Professor


class ProfessorCommunityLink(Base):
    __tablename__ = "professor_community_links"

    professor_id: Mapped[int] = mapped_column(
        ForeignKey("professors.id", ondelete="CASCADE"),
        primary_key=True,
    )
    community_record_id: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        unique=True,
    )
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)
    imported_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    last_checked_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    remote_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'active'"),
    )
    remote_revoked_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )

    professor: Mapped["Professor"] = relationship(back_populates="community_link")
