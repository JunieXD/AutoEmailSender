"""Stable facade for material DTOs and serialization."""

from .schemas import IdentityMaterialRead, IdentityMaterialTypeRead
from .serializer import serialize_material

__all__ = [
    "IdentityMaterialRead",
    "IdentityMaterialTypeRead",
    "serialize_material",
]
