from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.schemas.communication_group import (
    IdentityCommunicationGroupRead,
    IdentityCommunicationGroupWrite,
)
from app.services.communication_group_mutations import (
    CommunicationGroupMutationError,
    create_communication_group_record,
    delete_communication_group_record,
    get_communication_group_record,
    list_communication_group_records,
    update_communication_group_record,
)


router = APIRouter(prefix="/api/communication-groups", tags=["communication-groups"])


@router.get("", response_model=list[IdentityCommunicationGroupRead])
async def list_communication_groups(
    session: AsyncSession = Depends(get_async_session),
) -> list[IdentityCommunicationGroupRead]:
    return await list_communication_group_records(session)


@router.post(
    "",
    response_model=IdentityCommunicationGroupRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_communication_group(
    payload: IdentityCommunicationGroupWrite,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityCommunicationGroupRead:
    try:
        group = await create_communication_group_record(session, payload)
    except CommunicationGroupMutationError as exc:
        _raise_mutation_error(exc)
    await session.commit()
    return await get_communication_group_record(session, group.id)


@router.put("/{group_id}", response_model=IdentityCommunicationGroupRead)
async def update_communication_group(
    group_id: int,
    payload: IdentityCommunicationGroupWrite,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityCommunicationGroupRead:
    try:
        group = await update_communication_group_record(session, group_id, payload)
    except CommunicationGroupMutationError as exc:
        _raise_mutation_error(exc)
    await session.commit()
    return await get_communication_group_record(session, group.id)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_communication_group(
    group_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    try:
        await delete_communication_group_record(session, group_id)
    except CommunicationGroupMutationError as exc:
        _raise_mutation_error(exc)
    await session.commit()


def _raise_mutation_error(error: CommunicationGroupMutationError) -> None:
    if error.details is None:
        raise HTTPException(status_code=error.status_code, detail=error.message)
    raise HTTPException(
        status_code=error.status_code,
        detail={"message": error.message, **error.details},
    )
