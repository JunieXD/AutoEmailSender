from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.base import ApiSchema


class IdentityCommunicationGroupMemberRead(ApiSchema):
    id: int
    profile_name: str
    email_address: str
    is_default: bool


class IdentityCommunicationGroupRead(ApiSchema):
    id: int
    revision: str | None = None
    members: list[IdentityCommunicationGroupMemberRead]
    match_source_identity_id: int | None = None
    created_at: datetime
    updated_at: datetime


class IdentityCommunicationGroupWrite(BaseModel):
    identity_ids: list[int] = Field(min_length=2)
    match_source_identity_id: int | None = None
    confirm_merge_existing_groups: bool = False
