from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app.core.time import utc_now

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.email_log import EmailLog
    from app.models.email_task import EmailTask


class Professor(Base):
    __tablename__ = "professors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    university: Mapped[str | None] = mapped_column(String(255), nullable=True)
    school: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    research_direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    recent_papers: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    profile_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    crawl_status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'discovered'"),
    )
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
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
        back_populates="professor",
        cascade="all, delete-orphan",
    )
    email_logs: Mapped[list["EmailLog"]] = relationship(
        back_populates="professor",
        cascade="all, delete-orphan",
    )
    tags: Mapped[list["ProfessorTag"]] = relationship(
        secondary="professor_tag_links",
        order_by="ProfessorTag.name",
        lazy="selectin",
    )


class ProfessorTagLink(Base):
    __tablename__ = "professor_tag_links"
    __table_args__ = (
        UniqueConstraint(
            "professor_id",
            "tag_id",
            name="uq_professor_tag_links_professor_tag",
        ),
    )

    professor_id: Mapped[int] = mapped_column(
        ForeignKey("professors.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("professor_tags.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ProfessorTag(Base):
    __tablename__ = "professor_tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    text_color: Mapped[str] = mapped_column(String(16), nullable=False)
    background_color: Mapped[str] = mapped_column(String(16), nullable=False)
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
