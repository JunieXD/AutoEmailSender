from __future__ import annotations

import os


PACKAGE_NAME = "auto-email-sender-cli"
FALLBACK_VERSION = "2.4.1"
PROTOCOL_VERSION = "2"
# The runtime protocol is shared with the desktop descriptor and remains v2.
# Schema v4 makes routine discovery, result metadata, and action links sparse.
SCHEMA_VERSION = "4"
DEVELOPMENT_BUILD_REVISION = "development"


def get_cli_version() -> str:
    override = os.getenv("AUTO_EMAIL_SENDER_CLI_VERSION")
    if override and override.strip():
        return override.strip()
    embedded = os.getenv("AUTO_EMAIL_SENDER_EMBEDDED_CLI_VERSION")
    return embedded.strip() if embedded and embedded.strip() else FALLBACK_VERSION


def get_build_identity() -> dict[str, object]:
    """Return the O(1) identity embedded by the CLI packaging scripts."""

    explicit = os.getenv("AUTO_EMAIL_SENDER_BUILD_REVISION")
    embedded = os.getenv("AUTO_EMAIL_SENDER_EMBEDDED_BUILD_REVISION")
    explicit_revision = explicit.strip() if explicit else ""
    embedded_revision = embedded.strip() if embedded else ""
    revision = explicit_revision or embedded_revision or DEVELOPMENT_BUILD_REVISION
    dirty_value = os.getenv("AUTO_EMAIL_SENDER_EMBEDDED_BUILD_DIRTY", "0")
    return {
        "revision": revision,
        "kind": (
            "override"
            if explicit_revision
            else ("embedded" if embedded_revision else "development")
        ),
        "dirty": dirty_value.strip().lower() in {"1", "true", "yes"},
    }
