from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, text
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
    # Kept as ``identity_id`` in the database for upgrade compatibility. It is
    # provenance only; materials are visible to every identity.
    identity_id: Mapped[int | None] = mapped_column(
        ForeignKey("identity_profiles.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
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

    source_identity: Mapped["IdentityProfile | None"] = relationship(
        back_populates="source_materials",
        foreign_keys=[identity_id],
    )
    default_for_identities: Mapped[list["IdentityProfile"]] = relationship(
        foreign_keys="IdentityProfile.current_primary_material_id",
        viewonly=True,
    )

    @property
    def identity(self) -> "IdentityProfile | None":
        """Deprecated alias for upload provenance used by older integrations."""
        return self.source_identity

    @identity.setter
    def identity(self, value: "IdentityProfile | None") -> None:
        self.source_identity = value
