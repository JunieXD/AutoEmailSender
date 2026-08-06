"""Compatibility exports for the migrated identity-profile HTTP adapter."""

from app.modules.identities.profiles.api import (
    DUPLICATE_EMAIL_DETAIL,
    create_identity,
    delete_identity,
    imap_test,
    import_identity_template,
    import_unsaved_identity_template,
    list_identities,
    router,
    set_default_identity,
    smtp_test,
    update_identity,
    update_identity_default_template,
)

__all__ = [
    "DUPLICATE_EMAIL_DETAIL",
    "create_identity",
    "delete_identity",
    "imap_test",
    "import_identity_template",
    "import_unsaved_identity_template",
    "list_identities",
    "router",
    "set_default_identity",
    "smtp_test",
    "update_identity",
    "update_identity_default_template",
]
