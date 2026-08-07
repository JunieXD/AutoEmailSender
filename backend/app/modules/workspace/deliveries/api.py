from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session, get_session_factory

from .schemas import (
    EmailDeliveryActionRead,
    EmailDeliveryListRead,
    EmailDeliveryMutationRequest,
    EmailDeliveryRescheduleRequest,
    EmailDeliverySort,
    EmailDeliverySource,
    EmailDeliveryView,
)
from .service import (
    cancel_email_delivery,
    list_email_deliveries,
    reschedule_email_delivery,
    send_email_delivery_now,
)


router = APIRouter(prefix="/api/email-deliveries", tags=["email-deliveries"])


@router.get("", response_model=EmailDeliveryListRead)
async def list_deliveries(
    view: EmailDeliveryView = Query(default="upcoming"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=10, le=100),
    identity_id: int | None = Query(default=None),
    source: EmailDeliverySource = Query(default="all"),
    status: str | None = Query(default=None),
    sort: EmailDeliverySort | None = Query(default=None),
    search_fields: str | None = Query(default=None, max_length=100),
    query: str | None = Query(default=None, max_length=200),
    task_id: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_async_session),
) -> EmailDeliveryListRead:
    try:
        return await list_email_deliveries(
            session,
            view=view,
            page=page,
            page_size=page_size,
            identity_id=identity_id,
            source=source,
            status=status,
            sort=sort,
            search_fields=(
                tuple(field.strip() for field in search_fields.split(','))
                if search_fields is not None
                else None
            ),
            query=query,
            task_id=task_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/{task_id}/schedule", response_model=EmailDeliveryActionRead)
async def reschedule_delivery(
    task_id: int,
    payload: EmailDeliveryRescheduleRequest,
    session: AsyncSession = Depends(get_async_session),
) -> EmailDeliveryActionRead:
    return await reschedule_email_delivery(
        session,
        task_id=task_id,
        scheduled_at=payload.scheduled_at,
        expected_updated_at=payload.expected_updated_at,
    )


@router.post("/{task_id}/cancel", response_model=EmailDeliveryActionRead)
async def cancel_delivery(
    task_id: int,
    payload: EmailDeliveryMutationRequest,
    session: AsyncSession = Depends(get_async_session),
) -> EmailDeliveryActionRead:
    return await cancel_email_delivery(
        session,
        task_id=task_id,
        expected_updated_at=payload.expected_updated_at,
    )


@router.post("/{task_id}/send-now", response_model=EmailDeliveryActionRead)
async def send_delivery_now(
    task_id: int,
    payload: EmailDeliveryMutationRequest,
) -> EmailDeliveryActionRead:
    return await send_email_delivery_now(
        get_session_factory(),
        task_id=task_id,
        expected_updated_at=payload.expected_updated_at,
    )
