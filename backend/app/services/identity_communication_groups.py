"""Compatibility exports for migrated communication-group scope helpers."""

from app.modules.identities.communication_groups.scope import (
    CommunicationGroupCleanupResult,
    IdentityCommunicationScope,
    cleanup_communication_group_after_identity_delete,
    resolve_identity_communication_scope,
)

__all__ = [
    "CommunicationGroupCleanupResult",
    "IdentityCommunicationScope",
    "cleanup_communication_group_after_identity_delete",
    "resolve_identity_communication_scope",
]
