from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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


BetaWorkloadKind = Literal[
    "dispatcher",
    "imap_sync",
    "imap_history",
    "batch_draft",
    "matching",
    "crawler",
]


class BetaWorkloadSummaryItem(ApiSchema):
    kind: BetaWorkloadKind
    queued: int = Field(ge=0)
    running: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    interrupted: int = Field(ge=0)
    recovered: int = Field(ge=0)
    oldest_queue_age_seconds: float | None = Field(default=None, ge=0)
    oldest_running_age_seconds: float | None = Field(default=None, ge=0)
    average_duration_seconds: float | None = Field(default=None, ge=0)
    maximum_duration_seconds: float | None = Field(default=None, ge=0)


class BetaWorkloadInvariants(ApiSchema):
    sending_count: int = Field(ge=0)
    duplicate_delivery_attempt_groups: int = Field(ge=0)
    orphaned_claim_count: int = Field(ge=0)


class BetaWorkloadSummary(ApiSchema):
    schema_version: Literal[1] = 1
    generated_at: datetime
    workloads: list[BetaWorkloadSummaryItem]
    invariants: BetaWorkloadInvariants


class BetaDatabaseHealth(ApiSchema):
    schema_version: Literal[1] = 1
    generated_at: datetime
    available: bool = True
    alembic_revision: str
    integrity_check: Literal["ok", "error", "unknown"]
    foreign_key_violation_count: int = Field(ge=0)
    journal_mode: str
    busy_timeout_ms: int = Field(ge=0)
    database_bytes: int = Field(ge=0)
    wal_bytes: int = Field(ge=0)
    shm_bytes: int = Field(ge=0)
    backup_count: int = Field(ge=0)
    newest_backup_age_seconds: float | None = Field(default=None, ge=0)
    lock_errors_1h: int = Field(ge=0)
    busy_errors_1h: int = Field(ge=0)
    slow_queries_1h: int = Field(ge=0)
    maximum_query_ms_1h: float = Field(ge=0)


class BetaOperationLogLevelCounts(ApiSchema):
    debug: int = Field(ge=0)
    info: int = Field(ge=0)
    warning: int = Field(ge=0)
    error: int = Field(ge=0)


class BetaOperationLogCategorySummary(ApiSchema):
    category: Literal[
        "mail",
        "imap",
        "draft",
        "matching",
        "crawler",
        "runtime",
        "sqlite",
        "system",
        "llm",
    ]
    event_count: int = Field(ge=0)
    error_count: int = Field(ge=0)


class BetaOperationLogSummary(ApiSchema):
    schema_version: Literal[1] = 1
    generated_at: datetime
    total_1h: int = Field(ge=0)
    total_24h: int = Field(ge=0)
    levels_24h: BetaOperationLogLevelCounts
    categories_24h: list[BetaOperationLogCategorySummary]


class BetaDiagnosticsSummaryResponse(ApiSchema):
    schema_version: Literal[1] = 1
    generated_at: datetime
    workload_summary: BetaWorkloadSummary
    database_health: BetaDatabaseHealth
    operation_log_summary: BetaOperationLogSummary
