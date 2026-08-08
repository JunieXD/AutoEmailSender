from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the frozen Agent CLI's protocol and embedded build identity.",
    )
    parser.add_argument("--executable", required=True, type=Path)
    arguments = parser.parse_args()

    executable = arguments.executable.resolve()
    validate_bundle_layout(executable)
    version = _run_json(executable, "version")
    capabilities = _run_json(executable, "capabilities")
    validate_payloads(version, capabilities)
    print(
        json.dumps(
            {
                "ok": True,
                "executable": executable.as_posix(),
                "build_revision": version["data"]["build_revision"],
            },
        ),
    )


def validate_bundle_layout(executable: Path) -> None:
    if not executable.is_file():
        raise RuntimeError(f"frozen CLI executable is missing: {executable}")
    runtime_directory = executable.parent / "_internal"
    if not runtime_directory.is_dir():
        raise RuntimeError(
            "frozen CLI must use the onedir layout with an adjacent _internal directory",
        )


def validate_payloads(
    version: dict[str, Any],
    capabilities: dict[str, Any],
) -> None:
    if version.get("ok") is not True or capabilities.get("ok") is not True:
        raise RuntimeError("frozen CLI self-check returned a failed envelope")
    version_data = _object(version.get("data"), "version.data")
    capability_data = _object(capabilities.get("data"), "capabilities.data")
    version_meta = _object(version.get("_meta"), "version._meta")
    capability_meta = _object(capabilities.get("_meta"), "capabilities._meta")

    revision = str(version_data.get("build_revision") or "").strip()
    build_kind = str(version_data.get("build_kind") or "").strip()
    if not revision or revision == "development":
        raise RuntimeError("frozen CLI is missing an embedded build revision")
    if build_kind != "embedded":
        raise RuntimeError(f"unexpected frozen CLI build kind: {build_kind or '<empty>'}")
    for key in (
        "cli_version",
        "protocol_version",
        "schema_version",
        "contract_version",
        "catalog_version",
    ):
        if not str(version_data.get(key) or "").strip():
            raise RuntimeError(f"frozen CLI version payload is missing {key}")

    for meta in (version_meta, capability_meta):
        if meta.get("build_revision") != revision:
            raise RuntimeError("frozen CLI metadata build revision does not match version data")
        if meta.get("build_kind") != build_kind:
            raise RuntimeError("frozen CLI metadata build kind does not match version data")

    capability_build = _object(capability_data.get("build"), "capabilities.data.build")
    if capability_build.get("revision") != revision:
        raise RuntimeError("capability catalog was generated from a different build revision")
    if capability_build.get("kind") != build_kind:
        raise RuntimeError("capability catalog was generated from a different build kind")
    if not str(capability_data.get("scope_revision") or "").strip():
        raise RuntimeError("capability catalog is missing scope_revision")


def _run_json(executable: Path, command: str) -> dict[str, Any]:
    environment = os.environ.copy()
    # Validate what PyInstaller embedded, not development/test overrides from
    # the shell that happened to invoke the build script.
    environment.pop("AUTO_EMAIL_SENDER_BUILD_REVISION", None)
    environment.pop("AUTO_EMAIL_SENDER_CLI_VERSION", None)
    invocation = [executable.as_posix(), "--format", "json", command]
    completed = subprocess.run(
        invocation,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )
    if completed.returncode != 0:
        stdout = completed.stdout.rstrip() or "<empty>"
        stderr = completed.stderr.rstrip() or "<empty>"
        raise RuntimeError(
            f"frozen CLI {command} failed with exit code {completed.returncode}\n"
            f"command: {subprocess.list2cmdline(invocation)}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"frozen CLI {command} did not return JSON") from exc
    return _object(payload, command)


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


if __name__ == "__main__":
    main()
