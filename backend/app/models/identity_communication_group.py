from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app.core.time import utc_now

from sqlalchemy import text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.types import UTCDateTime

if TYPE_CHECKING:
    from app.models.identity_profile import IdentityProfile


class IdentityCommunicationGroup(Base):
    __tablename__ = "identity_communication_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
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

    members: Mapped[list["IdentityProfile"]] = relationship(
        back_populates="communication_group",
        order_by="IdentityProfile.id",
        passive_deletes=True,
    )
