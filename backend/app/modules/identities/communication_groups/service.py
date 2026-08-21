from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.agent_revisions import revision_for
from app.core.query_chunks import chunked_values
from app.core.time import utc_now
from app.models import IdentityCommunicationGroup, IdentityProfile
from app.services.operation_logs import record_operation_log

from .schemas import (
    IdentityCommunicationGroupMemberRead,
    IdentityCommunicationGroupRead,
    IdentityCommunicationGroupWrite,
)


class CommunicationGroupMutationError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


async def list_communication_group_records(
    session: AsyncSession,
) -> list[IdentityCommunicationGroupRead]:
    groups = list(
        await session.scalars(
            _group_query().order_by(IdentityCommunicationGroup.created_at.asc()),
        ),
    )
    return [_serialize_group(group) for group in groups]


async def get_communication_group_record(
    session: AsyncSession,
    group_id: int,
) -> IdentityCommunicationGroupRead:
    return _serialize_group(await get_communication_group_or_raise(session, group_id))


async def create_communication_group_record(
    session: AsyncSession,
    payload: IdentityCommunicationGroupWrite,
    *,
    event_name: str = "communication_group.created",
    actor: str | None = None,
) -> IdentityCommunicationGroup:
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
    group.match_source_identity_id = _resolve_requested_match_source_identity_id(
        payload,
        final_members=final_members,
        existing_match_source_identity_id=None,
        preserve_when_omitted=False,
    )
    await _delete_groups(session, conflicting_group_ids)
    await _record_group_log(
        session,
        event_name=_event_name_for_merge(event_name, bool(conflicting_group_ids)),
        group_id=group.id,
        before_member_ids=[],
        after_member_ids=sorted(identity.id for identity in final_members),
        merged_group_ids=sorted(conflicting_group_ids),
        before_match_source_identity_id=None,
        after_match_source_identity_id=group.match_source_identity_id,
        actor=actor,
    )
    await session.flush()
    return group


async def update_communication_group_record(
    session: AsyncSession,
    group_id: int,
    payload: IdentityCommunicationGroupWrite,
    *,
    event_name: str = "communication_group.updated",
    actor: str | None = None,
) -> IdentityCommunicationGroup:
    group = await get_communication_group_or_raise(session, group_id)
    before_member_ids = sorted(member.id for member in group.members)
    before_match_source_identity_id = group.match_source_identity_id
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
    group.match_source_identity_id = _resolve_requested_match_source_identity_id(
        payload,
        final_members=final_members,
        existing_match_source_identity_id=before_match_source_identity_id,
        preserve_when_omitted=True,
    )
    await _delete_groups(session, conflicting_group_ids)
    group.updated_at = utc_now()
    await _record_group_log(
        session,
        event_name=_event_name_for_merge(event_name, bool(conflicting_group_ids)),
        group_id=group_id,
        before_member_ids=before_member_ids,
        after_member_ids=sorted(final_member_ids),
        merged_group_ids=sorted(conflicting_group_ids),
        before_match_source_identity_id=before_match_source_identity_id,
        after_match_source_identity_id=group.match_source_identity_id,
        actor=actor,
    )
    await session.flush()
    return group


async def delete_communication_group_record(
    session: AsyncSession,
    group_id: int,
    *,
    event_name: str = "communication_group.deleted",
    actor: str | None = None,
) -> None:
    group = await get_communication_group_or_raise(session, group_id)
    before_member_ids = sorted(member.id for member in group.members)
    for member in group.members:
        member.communication_group_id = None
    await _record_group_log(
        session,
        event_name=event_name,
        group_id=group_id,
        before_member_ids=before_member_ids,
        after_member_ids=[],
        merged_group_ids=[],
        before_match_source_identity_id=group.match_source_identity_id,
        after_match_source_identity_id=None,
        actor=actor,
    )
    await session.delete(group)
    await session.flush()


async def get_communication_group_or_raise(
    session: AsyncSession,
    group_id: int,
) -> IdentityCommunicationGroup:
    group = await session.scalar(
        _group_query().where(IdentityCommunicationGroup.id == group_id),
    )
    if group is None:
        raise CommunicationGroupMutationError(
            status_code=404,
            code="COMMUNICATION_GROUP_NOT_FOUND",
            message="未找到通信共享组",
        )
    return group


def _group_query():
    return (
        select(IdentityCommunicationGroup)
        .options(selectinload(IdentityCommunicationGroup.members))
        .execution_options(populate_existing=True)
    )


async def _load_selected_identities(
    session: AsyncSession,
    raw_identity_ids: list[int],
) -> list[IdentityProfile]:
    identity_ids = list(dict.fromkeys(raw_identity_ids))
    if len(identity_ids) < 2:
        raise CommunicationGroupMutationError(
            status_code=422,
            code="COMMUNICATION_GROUP_MINIMUM_MEMBERS",
            message="通信共享组至少需要两个身份",
        )

    identities: list[IdentityProfile] = []
    for identity_id_chunk in chunked_values(identity_ids):
        identities.extend(
            await session.scalars(
                select(IdentityProfile).where(
                    IdentityProfile.id.in_(identity_id_chunk),
                ),
            ),
        )
    identities.sort(key=lambda identity: identity.id)
    found_ids = {identity.id for identity in identities}
    missing_ids = sorted(set(identity_ids) - found_ids)
    if missing_ids:
        raise CommunicationGroupMutationError(
            status_code=422,
            code="COMMUNICATION_GROUP_IDENTITIES_NOT_FOUND",
            message="部分身份不存在",
            details={"identity_ids": missing_ids},
        )
    return identities


def _group_ids(identities: Iterable[IdentityProfile]) -> set[int]:
    return {
        identity.communication_group_id
        for identity in identities
        if identity.communication_group_id is not None
    }


def _event_name_for_merge(event_name: str, merged: bool) -> str:
    if not merged:
        return event_name
    if event_name.endswith(".created"):
        return f"{event_name[: -len('.created')]}.merged"
    if event_name.endswith(".updated"):
        return f"{event_name[: -len('.updated')]}.merged"
    return event_name


async def _expand_conflicting_group_members(
    session: AsyncSession,
    *,
    selected: list[IdentityProfile],
    conflicting_group_ids: set[int],
) -> list[IdentityProfile]:
    members_by_id = {identity.id: identity for identity in selected}
    for group_id_chunk in chunked_values(conflicting_group_ids):
        group_members = await session.scalars(
            select(IdentityProfile).where(
                IdentityProfile.communication_group_id.in_(group_id_chunk),
            ),
        )
        for identity in group_members:
            members_by_id[identity.id] = identity
    return [members_by_id[identity_id] for identity_id in sorted(members_by_id)]


async def _raise_group_conflict(
    session: AsyncSession,
    group_ids: set[int],
) -> None:
    members: list[IdentityProfile] = []
    for group_id_chunk in chunked_values(group_ids):
        members.extend(
            await session.scalars(
                select(IdentityProfile).where(
                    IdentityProfile.communication_group_id.in_(group_id_chunk),
                ),
            ),
        )
    members.sort(key=lambda identity: identity.id)
    raise CommunicationGroupMutationError(
        status_code=409,
        code="COMMUNICATION_GROUP_MERGE_CONFIRMATION_REQUIRED",
        message="所选身份已属于其他通信共享组，请确认后合并",
        details={
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


def _resolve_requested_match_source_identity_id(
    payload: IdentityCommunicationGroupWrite,
    *,
    final_members: list[IdentityProfile],
    existing_match_source_identity_id: int | None,
    preserve_when_omitted: bool,
) -> int | None:
    final_member_ids = {identity.id for identity in final_members}
    if "match_source_identity_id" not in payload.model_fields_set:
        if (
            preserve_when_omitted
            and existing_match_source_identity_id in final_member_ids
        ):
            return existing_match_source_identity_id
        return None

    requested_id = payload.match_source_identity_id
    if requested_id is None:
        return None
    if requested_id not in final_member_ids:
        raise CommunicationGroupMutationError(
            status_code=422,
            code="COMMUNICATION_GROUP_MATCH_SOURCE_NOT_MEMBER",
            message="匹配依据身份必须属于当前共享组",
            details={"match_source_identity_id": requested_id},
        )
    return requested_id


async def _record_group_log(
    session: AsyncSession,
    *,
    event_name: str,
    group_id: int,
    before_member_ids: list[int],
    after_member_ids: list[int],
    merged_group_ids: list[int],
    before_match_source_identity_id: int | None,
    after_match_source_identity_id: int | None,
    actor: str | None,
) -> None:
    metadata: dict[str, Any] = {
        "before_member_ids": before_member_ids,
        "after_member_ids": after_member_ids,
        "merged_group_ids": merged_group_ids,
        "before_match_source_identity_id": before_match_source_identity_id,
        "after_match_source_identity_id": after_match_source_identity_id,
    }
    if actor is not None:
        metadata["actor"] = actor
    await record_operation_log(
        session,
        category="identity",
        event_name=event_name,
        entity_type="identity_communication_group",
        entity_id=str(group_id),
        metadata=metadata,
    )


def serialize_communication_group(
    group: IdentityCommunicationGroup,
) -> IdentityCommunicationGroupRead:
    return _serialize_group(group)


def _serialize_group(
    group: IdentityCommunicationGroup,
) -> IdentityCommunicationGroupRead:
    result = IdentityCommunicationGroupRead(
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
        match_source_identity_id=group.match_source_identity_id,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )
    return result.model_copy(update={"revision": revision_for(result)})
