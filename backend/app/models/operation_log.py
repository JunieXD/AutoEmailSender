from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    category: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    event_name: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'info'"),
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    event_metadata: Mapped[dict[str, object] | list[object] | None] = mapped_column(
        "metadata",
        JSON,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        index=True,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
