from __future__ import annotations

import os
from importlib import metadata


PACKAGE_NAME = "auto-email-sender-cli"
FALLBACK_VERSION = "2.4.1"
PROTOCOL_VERSION = "2"
# The runtime protocol is unchanged; this version tracks the public CLI
# response shape.  v2 introduces progressive capability and command views.
SCHEMA_VERSION = "2"


def get_cli_version() -> str:
    override = os.getenv("AUTO_EMAIL_SENDER_CLI_VERSION")
    if override and override.strip():
        return override.strip()
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return FALLBACK_VERSION
