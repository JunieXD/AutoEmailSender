"""Stable facade for the identity-profile slice."""

from .availability import (
    RETIRED_IDENTITY_MESSAGE,
    get_active_identity_profile,
    identity_profile_is_active,
)
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
from .usage import IdentityProfileRetiringError, track_identity_profile_usage

__all__ = [
    "ConnectionTestResult",
    "IdentityProfileBase",
    "IdentityProfileCreate",
    "IdentityProfileRead",
    "IdentityProfileUpdate",
    "IdentityTemplateImportResult",
    "IdentityProfileRetiringError",
    "OutreachGenerationMode",
    "RETIRED_IDENTITY_MESSAGE",
    "get_active_identity_profile",
    "identity_profile_is_active",
    "serialize_identity",
    "track_identity_profile_usage",
]
