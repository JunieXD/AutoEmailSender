from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.base import ApiSchema
from app.schemas.selection import SelectionSpec


AgentUiHandoffStatus = Literal[
    "pending",
    "claimed",
    "awaiting_user",
    "applied",
    "failed",
    "canceled",
    "expired",
]
AgentUiHandoffSurface = Literal[
    "professors.management",
    "professors.home",
    "tasks.center",
    "crawler.job",
    "communications.thread",
    "draft.workspace",
]
AgentUiSelectionMode = Literal["replace", "add"]
AgentUiSelectionDisplay = Literal["keep_current", "selected_only"]


class AgentProfessorPresentSelectionRequest(ApiSchema):
    selection: SelectionSpec
    surface: Literal["professors.management", "professors.home"] = (
        "professors.management"
    )
    selection_mode: AgentUiSelectionMode = "replace"
    display: AgentUiSelectionDisplay = "selected_only"
    identity_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_surface_context(self) -> "AgentProfessorPresentSelectionRequest":
        if self.surface == "professors.home" and self.identity_id is None:
            raise ValueError("首页导师选择必须提供 identity_id")
        if self.surface == "professors.management" and self.identity_id is not None:
            raise ValueError("导师管理页选择不能提供 identity_id")
        return self


class AgentUiHandoffClaimRequest(ApiSchema):
    consumer_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$")


class AgentUiHandoffAcknowledgeRequest(AgentUiHandoffClaimRequest):
    status: Literal["applied", "awaiting_user", "failed"]
    result: dict[str, object] = Field(default_factory=dict)
    failure_message: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_failure(self) -> "AgentUiHandoffAcknowledgeRequest":
        if self.status == "failed" and not (self.failure_message or "").strip():
            raise ValueError("failed 回执必须提供 failure_message")
        if self.status != "failed" and self.failure_message is not None:
            raise ValueError("只有 failed 回执可以提供 failure_message")
        return self


class AgentUiHandoffRead(ApiSchema):
    handoff_id: str
    schema_version: int
    surface: AgentUiHandoffSurface
    route: str
    status: AgentUiHandoffStatus
    selection_count: int
    selection_fingerprint: str | None = None
    ui_effects: list[str] = Field(default_factory=list)
    result: dict[str, object] | None = None
    failure_message: str | None = None
    delivery_attempts: int
    expires_at: datetime
    claimed_at: datetime | None = None
    awaiting_user_at: datetime | None = None
    applied_at: datetime | None = None
    failed_at: datetime | None = None
    canceled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    idempotent_replay: bool = False
    available_actions: list[str] = Field(default_factory=list)


class AgentUiHandoffClaimRead(AgentUiHandoffRead):
    consumer_id: str
    claim_expires_at: datetime
    payload: dict[str, object]
    selected_ids: list[int] = Field(default_factory=list)


__all__ = [
    "AgentProfessorPresentSelectionRequest",
    "AgentUiHandoffAcknowledgeRequest",
    "AgentUiHandoffClaimRead",
    "AgentUiHandoffClaimRequest",
    "AgentUiHandoffRead",
    "AgentUiHandoffStatus",
    "AgentUiHandoffSurface",
    "AgentUiSelectionDisplay",
    "AgentUiSelectionMode",
]
