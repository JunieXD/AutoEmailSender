from __future__ import annotations

import os
from importlib import metadata


PACKAGE_NAME = "auto-email-sender-cli"
FALLBACK_VERSION = "2.4.1"
PROTOCOL_VERSION = "2"
# The runtime protocol is shared with the desktop descriptor and remains v2.
# Schema v3 adds executable action links, bounded result metadata, and invoke.
SCHEMA_VERSION = "3"


def get_cli_version() -> str:
    override = os.getenv("AUTO_EMAIL_SENDER_CLI_VERSION")
    if override and override.strip():
        return override.strip()
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return FALLBACK_VERSION
