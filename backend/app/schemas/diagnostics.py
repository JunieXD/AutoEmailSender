from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.schemas.base import ApiSchema


class OperationLogRead(ApiSchema):
    id: int
    request_id: str | None
    category: str
    event_name: str
    level: str
    message: str | None
    entity_type: str | None
    entity_id: str | None
    metadata: dict[str, object] | list[object] | None
    created_at: datetime


class OperationLogListResponse(ApiSchema):
    items: list[OperationLogRead]
    total: int
    limit: int = Field(ge=1, le=500)
    offset: int = Field(ge=0)


class DiagnosticFileRead(ApiSchema):
    name: str
    relative_path: str
    content: str


class OperationLogExportResponse(ApiSchema):
    exported_at: datetime
    items: list[OperationLogRead]
    total: int
    filters: dict[str, str | None]
    startup_logs: list[DiagnosticFileRead] = Field(default_factory=list)
