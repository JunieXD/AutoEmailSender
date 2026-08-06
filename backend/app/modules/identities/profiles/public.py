"""Stable facade for the identity-profile slice."""

from .schemas import (
    ConnectionTestResult,
    IdentityProfileBase,
    IdentityProfileCreate,
    IdentityProfileRead,
    IdentityProfileUpdate,
    IdentityTemplateImportResult,
    OutreachGenerationMode,
)
from .serializer import serialize_identity

__all__ = [
    "ConnectionTestResult",
    "IdentityProfileBase",
    "IdentityProfileCreate",
    "IdentityProfileRead",
    "IdentityProfileUpdate",
    "IdentityTemplateImportResult",
    "OutreachGenerationMode",
    "serialize_identity",
]
