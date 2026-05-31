from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.identity_profile import IdentityProfile


class IdentityMaterialType(StrEnum):
    RESUME = "resume"
    TRANSCRIPT = "transcript"
    PUBLICATION = "publication"
    PORTFOLIO = "portfolio"
    OTHER = "other"


class IdentityMaterial(Base):
    __tablename__ = "identity_materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    identity_id: Mapped[int] = mapped_column(
        ForeignKey("identity_profiles.id"),
        index=True,
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    material_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'other'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    identity: Mapped["IdentityProfile"] = relationship(
        back_populates="materials",
        foreign_keys=[identity_id],
    )
