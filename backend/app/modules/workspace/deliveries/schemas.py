from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.base import ApiSchema


EmailDeliveryView = Literal["upcoming", "attention", "history"]
EmailDeliverySource = Literal["all", "manual", "batch"]
EmailDeliverySort = Literal[
    "scheduled_asc",
    "scheduled_desc",
    "updated_desc",
    "updated_asc",
    "event_desc",
    "event_asc",
]


class EmailDeliveryViewCountsRead(ApiSchema):
    upcoming: int = 0
    attention: int = 0
    history: int = 0


class EmailDeliveryItemRead(ApiSchema):
    id: int
    source: str
    batch_task_id: int | None
    batch_task_name: str | None
    batch_task_status: str | None
    professor_id: int
    professor_name: str
    professor_email: str | None
    professor_archived_at: datetime | None
    identity_id: int
    identity_name: str
    sender_email: str
    identity_retired_at: datetime | None
    subject: str | None
    attachment_count: int = 0
    attachment_size_bytes: int = 0
    status: str
    status_label: str
    status_description: str
    scheduled_at: datetime | None
    last_scheduled_at: datetime | None
    schedule_canceled_at: datetime | None
    batch_send_canceled_at: datetime | None
    approved_at: datetime | None
    last_send_attempt_at: datetime | None
    sent_at: datetime | None
    last_error: str | None
    retry_count: int
    created_at: datetime
    updated_at: datetime
    can_reschedule: bool
    can_cancel: bool
    can_send_now: bool
    can_restore: bool
    can_edit: bool


class EmailDeliveryListRead(ApiSchema):
    items: list[EmailDeliveryItemRead] = Field(default_factory=list)
    counts: EmailDeliveryViewCountsRead
    page: int
    page_size: int
    total_count: int
    total_pages: int


class EmailDeliveryRescheduleRequest(BaseModel):
    scheduled_at: datetime
    expected_updated_at: datetime


class EmailDeliveryMutationRequest(BaseModel):
    expected_updated_at: datetime


class EmailDeliveryActionRead(ApiSchema):
    ok: bool
    task_id: int
    message: str
