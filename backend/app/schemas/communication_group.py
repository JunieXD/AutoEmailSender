"""Compatibility exports for migrated communication-group schemas."""

from app.modules.identities.communication_groups.schemas import (
    IdentityCommunicationGroupMemberRead,
    IdentityCommunicationGroupRead,
    IdentityCommunicationGroupWrite,
)

__all__ = [
    "IdentityCommunicationGroupMemberRead",
    "IdentityCommunicationGroupRead",
    "IdentityCommunicationGroupWrite",
]
