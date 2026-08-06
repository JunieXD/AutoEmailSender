"""Compatibility exports for migrated identity and material serializers."""

from app.modules.identities.materials.serializer import serialize_material
from app.modules.identities.profiles.serializer import serialize_identity

__all__ = ["serialize_identity", "serialize_material"]
