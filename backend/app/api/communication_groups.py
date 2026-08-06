"""Compatibility exports for the migrated communication-groups HTTP adapter."""

from app.modules.identities.communication_groups.api import (
    create_communication_group,
    delete_communication_group,
    list_communication_groups,
    router,
    update_communication_group,
)

__all__ = [
    "create_communication_group",
    "delete_communication_group",
    "list_communication_groups",
    "router",
    "update_communication_group",
]
