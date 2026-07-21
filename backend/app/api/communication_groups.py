from __future__ import annotations

from collections.abc import Iterable

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_async_session
from app.core.time import utc_now
from app.models import IdentityCommunicationGroup, IdentityProfile
from app.schemas.communication_group import (
    IdentityCommunicationGroupMemberRead,
    IdentityCommunicationGroupRead,
    IdentityCommunicationGroupWrite,
)
from app.services.operation_logs import record_operation_log


router = APIRouter(prefix="/api/communication-groups", tags=["communication-groups"])


@router.get("", response_model=list[IdentityCommunicationGroupRead])
async def list_communication_groups(
    session: AsyncSession = Depends(get_async_session),
) -> list[IdentityCommunicationGroupRead]:
    groups = list(
        await session.scalars(
            _group_query().order_by(IdentityCommunicationGroup.created_at.asc()),
        ),
    )
    return [_serialize_group(group) for group in groups]


@router.post(
    "",
    response_model=IdentityCommunicationGroupRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_communication_group(
    payload: IdentityCommunicationGroupWrite,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityCommunicationGroupRead:
    selected = await _load_selected_identities(session, payload.identity_ids)
    conflicting_group_ids = _group_ids(selected)
    if conflicting_group_ids and not payload.confirm_merge_existing_groups:
        await _raise_group_conflict(session, conflicting_group_ids)

    final_members = await _expand_conflicting_group_members(
        session,
        selected=selected,
        conflicting_group_ids=conflicting_group_ids,
    )
    group = IdentityCommunicationGroup()
    session.add(group)
    await session.flush()

    for identity in final_members:
        identity.communication_group_id = group.id
    await _delete_groups(session, conflicting_group_ids)
    member_ids = sorted(identity.id for identity in final_members)
    await _record_group_log(
        session,
        event_name=(
            "communication_group.merged"
            if conflicting_group_ids
            else "communication_group.created"
        ),
        group_id=group.id,
        before_member_ids=[],
        after_member_ids=member_ids,
        merged_group_ids=sorted(conflicting_group_ids),
    )
    await session.commit()
    return _serialize_group(await _get_group(session, group.id))


@router.put("/{group_id}", response_model=IdentityCommunicationGroupRead)
async def update_communication_group(
    group_id: int,
    payload: IdentityCommunicationGroupWrite,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityCommunicationGroupRead:
    group = await _get_group(session, group_id)
    before_member_ids = sorted(member.id for member in group.members)
    selected = await _load_selected_identities(session, payload.identity_ids)
    conflicting_group_ids = _group_ids(selected) - {group_id}
    if conflicting_group_ids and not payload.confirm_merge_existing_groups:
        await _raise_group_conflict(session, conflicting_group_ids)

    final_members = await _expand_conflicting_group_members(
        session,
        selected=selected,
        conflicting_group_ids=conflicting_group_ids,
    )
    final_member_ids = {identity.id for identity in final_members}
    for member in group.members:
        if member.id not in final_member_ids:
            member.communication_group_id = None
    for identity in final_members:
        identity.communication_group_id = group_id
    await _delete_groups(session, conflicting_group_ids)
    group.updated_at = utc_now()
    await _record_group_log(
        session,
        event_name=(
            "communication_group.merged"
            if conflicting_group_ids
            else "communication_group.updated"
        ),
        group_id=group_id,
        before_member_ids=before_member_ids,
        after_member_ids=sorted(final_member_ids),
        merged_group_ids=sorted(conflicting_group_ids),
    )
    await session.commit()
    return _serialize_group(await _get_group(session, group_id))


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_communication_group(
    group_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    group = await _get_group(session, group_id)
    before_member_ids = sorted(member.id for member in group.members)
    for member in group.members:
        member.communication_group_id = None
    await _record_group_log(
        session,
        event_name="communication_group.deleted",
        group_id=group_id,
        before_member_ids=before_member_ids,
        after_member_ids=[],
        merged_group_ids=[],
    )
    await session.delete(group)
    await session.commit()


def _group_query():
    return (
        select(IdentityCommunicationGroup)
        .options(selectinload(IdentityCommunicationGroup.members))
        .execution_options(populate_existing=True)
    )


async def _get_group(
    session: AsyncSession,
    group_id: int,
) -> IdentityCommunicationGroup:
    group = await session.scalar(
        _group_query().where(IdentityCommunicationGroup.id == group_id),
    )
    if group is None:
        raise HTTPException(status_code=404, detail="未找到通信共享组")
    return group


async def _load_selected_identities(
    session: AsyncSession,
    raw_identity_ids: list[int],
) -> list[IdentityProfile]:
    identity_ids = list(dict.fromkeys(raw_identity_ids))
    if len(identity_ids) < 2:
        raise HTTPException(status_code=422, detail="通信共享组至少需要两个身份")

    identities = list(
        await session.scalars(
            select(IdentityProfile)
            .where(IdentityProfile.id.in_(identity_ids))
            .order_by(IdentityProfile.id.asc()),
        ),
    )
    found_ids = {identity.id for identity in identities}
    missing_ids = sorted(set(identity_ids) - found_ids)
    if missing_ids:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "部分身份不存在",
                "identity_ids": missing_ids,
            },
        )
    return identities


def _group_ids(identities: Iterable[IdentityProfile]) -> set[int]:
    return {
        identity.communication_group_id
        for identity in identities
        if identity.communication_group_id is not None
    }


async def _expand_conflicting_group_members(
    session: AsyncSession,
    *,
    selected: list[IdentityProfile],
    conflicting_group_ids: set[int],
) -> list[IdentityProfile]:
    members_by_id = {identity.id: identity for identity in selected}
    if conflicting_group_ids:
        group_members = await session.scalars(
            select(IdentityProfile).where(
                IdentityProfile.communication_group_id.in_(conflicting_group_ids),
            ),
        )
        for identity in group_members:
            members_by_id[identity.id] = identity
    return [members_by_id[identity_id] for identity_id in sorted(members_by_id)]


async def _raise_group_conflict(
    session: AsyncSession,
    group_ids: set[int],
) -> None:
    members = list(
        await session.scalars(
            select(IdentityProfile)
            .where(IdentityProfile.communication_group_id.in_(group_ids))
            .order_by(IdentityProfile.id.asc()),
        ),
    )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "message": "所选身份已属于其他通信共享组，请确认后合并",
            "group_ids": sorted(group_ids),
            "members": [
                {
                    "id": member.id,
                    "profile_name": member.profile_name or member.name,
                    "email_address": member.email_address,
                }
                for member in members
            ],
        },
    )


async def _delete_groups(session: AsyncSession, group_ids: set[int]) -> None:
    for group_id in sorted(group_ids):
        group = await session.get(IdentityCommunicationGroup, group_id)
        if group is not None:
            await session.delete(group)


async def _record_group_log(
    session: AsyncSession,
    *,
    event_name: str,
    group_id: int,
    before_member_ids: list[int],
    after_member_ids: list[int],
    merged_group_ids: list[int],
) -> None:
    await record_operation_log(
        session,
        category="identity",
        event_name=event_name,
        entity_type="identity_communication_group",
        entity_id=str(group_id),
        metadata={
            "before_member_ids": before_member_ids,
            "after_member_ids": after_member_ids,
            "merged_group_ids": merged_group_ids,
        },
    )


def _serialize_group(
    group: IdentityCommunicationGroup,
) -> IdentityCommunicationGroupRead:
    return IdentityCommunicationGroupRead(
        id=group.id,
        members=[
            IdentityCommunicationGroupMemberRead(
                id=member.id,
                profile_name=member.profile_name or member.name,
                email_address=member.email_address,
                is_default=member.is_default,
            )
            for member in sorted(group.members, key=lambda item: item.id)
        ],
        created_at=group.created_at,
        updated_at=group.updated_at,
    )
