from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RestartSafetyWorkCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_generation: int
    match_analysis: int
    crawler: int
    imap_sync: int


class RestartSafetyRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    safe_to_restart: bool
    confirmation_required: bool
    active_work_count: int
    sending_count: int
    work_counts: RestartSafetyWorkCounts
    message: str
