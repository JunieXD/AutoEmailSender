from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models import IdentityCommunicationGroup, IdentityProfile


@dataclass(frozen=True)
class IdentityCommunicationScope:
    active_identity: IdentityProfile
    identities: tuple[IdentityProfile, ...]

    @property
    def identity_ids(self) -> tuple[int, ...]:
        return tuple(identity.id for identity in self.identities)


@dataclass(frozen=True)
class CommunicationGroupCleanupResult:
    group_id: int
    previous_member_ids: tuple[int, ...]
    member_ids: tuple[int, ...]
    dissolved: bool


async def resolve_identity_communication_scope(
    session: AsyncSession,
    *,
    active_identity_id: int,
) -> IdentityCommunicationScope:
    active_identity = await session.scalar(
        select(IdentityProfile).where(
            IdentityProfile.id == active_identity_id,
            IdentityProfile.deleted_at.is_(None),
        )
    )
    if active_identity is None:
        raise ValueError("未找到身份配置")

    if active_identity.communication_group_id is None:
        return IdentityCommunicationScope(
            active_identity=active_identity,
            identities=(active_identity,),
        )

    members = list(
        await session.scalars(
            select(IdentityProfile)
            .where(
                IdentityProfile.communication_group_id
                == active_identity.communication_group_id,
                IdentityProfile.deleted_at.is_(None),
            )
            .order_by(IdentityProfile.id.asc()),
        ),
    )
    if len(members) < 2:
        return IdentityCommunicationScope(
            active_identity=active_identity,
            identities=(active_identity,),
        )

    ordered_members = [active_identity]
    ordered_members.extend(
        member for member in members if member.id != active_identity.id
    )
    return IdentityCommunicationScope(
        active_identity=active_identity,
        identities=tuple(ordered_members),
    )


async def cleanup_communication_group_after_identity_delete(
    session: AsyncSession,
    *,
    group_id: int,
    removed_identity_id: int,
) -> CommunicationGroupCleanupResult | None:
    group = await session.get(IdentityCommunicationGroup, group_id)
    if group is None:
        return None

    remaining_members = list(
        await session.scalars(
            select(IdentityProfile)
            .where(
                IdentityProfile.communication_group_id == group_id,
                IdentityProfile.deleted_at.is_(None),
            )
            .order_by(IdentityProfile.id.asc()),
        ),
    )
    remaining_ids = tuple(member.id for member in remaining_members)
    previous_ids = tuple(sorted((removed_identity_id, *remaining_ids)))
    if len(remaining_members) >= 2:
        group.updated_at = utc_now()
        return CommunicationGroupCleanupResult(
            group_id=group_id,
            previous_member_ids=previous_ids,
            member_ids=remaining_ids,
            dissolved=False,
        )

    for member in remaining_members:
        member.communication_group_id = None
    await session.delete(group)
    return CommunicationGroupCleanupResult(
        group_id=group_id,
        previous_member_ids=previous_ids,
        member_ids=remaining_ids,
        dissolved=True,
    )
