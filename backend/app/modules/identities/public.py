"""Stable facade for capabilities owned by the identities domain."""

from .communication_groups.public import (
    CommunicationGroupCleanupResult,
    CommunicationGroupMutationError,
    IdentityCommunicationGroupMemberRead,
    IdentityCommunicationGroupRead,
    IdentityCommunicationGroupWrite,
    IdentityCommunicationScope,
    cleanup_communication_group_after_identity_delete,
    create_communication_group_record,
    delete_communication_group_record,
    get_communication_group_record,
    list_communication_group_records,
    resolve_identity_communication_scope,
    serialize_communication_group,
    update_communication_group_record,
)

__all__ = [
    "CommunicationGroupCleanupResult",
    "CommunicationGroupMutationError",
    "IdentityCommunicationGroupMemberRead",
    "IdentityCommunicationGroupRead",
    "IdentityCommunicationGroupWrite",
    "IdentityCommunicationScope",
    "cleanup_communication_group_after_identity_delete",
    "create_communication_group_record",
    "delete_communication_group_record",
    "get_communication_group_record",
    "list_communication_group_records",
    "resolve_identity_communication_scope",
    "serialize_communication_group",
    "update_communication_group_record",
]
