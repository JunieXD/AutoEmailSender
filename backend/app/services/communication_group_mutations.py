"""Compatibility exports for the migrated communication-group service."""

from app.modules.identities.communication_groups.service import (
    CommunicationGroupMutationError,
    create_communication_group_record,
    delete_communication_group_record,
    get_communication_group_or_raise,
    get_communication_group_record,
    list_communication_group_records,
    serialize_communication_group,
    update_communication_group_record,
)

__all__ = [
    "CommunicationGroupMutationError",
    "create_communication_group_record",
    "delete_communication_group_record",
    "get_communication_group_or_raise",
    "get_communication_group_record",
    "list_communication_group_records",
    "serialize_communication_group",
    "update_communication_group_record",
]
