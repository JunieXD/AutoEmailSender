from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from shutil import copyfile
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
AGENT_MANIFEST_CONTRACT_PATH = (
    REPOSITORY_ROOT / "contracts" / "agent-support-manifest.schema.json"
)


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
    manifest_versions = validate_agent_installation_contract(executable)
    print(
        json.dumps(
            {
                "ok": True,
                "executable": executable.as_posix(),
                "build_revision": version["data"]["build_revision"],
                "agent_manifest_schema_versions": manifest_versions,
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
        raise RuntimeError(
            f"unexpected frozen CLI build kind: {build_kind or '<empty>'}"
        )
    for key in (
        "cli_version",
        "protocol_version",
        "schema_version",
        "contract_version",
        "catalog_version",
    ):
        if not str(version_data.get(key) or "").strip():
            raise RuntimeError(f"frozen CLI version payload is missing {key}")

    if version_meta.get("command") != "version":
        raise RuntimeError("frozen CLI version metadata names the wrong command")
    if capability_meta.get("command") != "capabilities":
        raise RuntimeError("frozen CLI capability metadata names the wrong command")
    if version_meta.get("schema_version") != version_data.get("schema_version"):
        raise RuntimeError("frozen CLI metadata schema does not match version data")

    capability_build = _object(capability_data.get("build"), "capabilities.data.build")
    if capability_build.get("revision") != revision:
        raise RuntimeError(
            "capability catalog was generated from a different build revision"
        )
    if capability_build.get("kind") != build_kind:
        raise RuntimeError(
            "capability catalog was generated from a different build kind"
        )
    if not str(capability_data.get("scope_revision") or "").strip():
        raise RuntimeError("capability catalog is missing scope_revision")


def validate_agent_installation_contract(
    executable: Path,
    *,
    contract_path: Path = AGENT_MANIFEST_CONTRACT_PATH,
) -> list[int]:
    try:
        contract = _object(
            json.loads(contract_path.read_text(encoding="utf-8")),
            "agent manifest contract",
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"cannot read Agent manifest contract: {contract_path}"
        ) from exc

    current_version = _positive_integer(
        contract.get("x-current-version"),
        "agent manifest x-current-version",
    )
    raw_supported_versions = contract.get("x-supported-versions")
    if not isinstance(raw_supported_versions, list) or not raw_supported_versions:
        raise RuntimeError(
            "agent manifest x-supported-versions must be a non-empty array"
        )
    supported_versions = [
        _positive_integer(value, "agent manifest supported version")
        for value in raw_supported_versions
    ]
    if current_version not in supported_versions:
        raise RuntimeError(
            "current Agent manifest version is not declared as supported"
        )

    with tempfile.TemporaryDirectory(
        prefix="auto-email-sender-cli-contract-"
    ) as temp_dir:
        verification_root = Path(temp_dir)
        for schema_version in supported_versions:
            expected_hash = _expected_cli_hash(executable, schema_version)
            cli_target, expected_binding = _create_verification_cli_target(
                executable,
                verification_root,
                schema_version,
            )
            manifest_path = verification_root / f"installation-v{schema_version}.json"
            runtime_path = verification_root / f"missing-runtime-v{schema_version}.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": schema_version,
                        "enabled": True,
                        "prompt_dismissed": True,
                        "app_version": "build-verification",
                        "cli_source": executable.as_posix(),
                        "skill_source": executable.parent.as_posix(),
                        "cli_target": cli_target.as_posix(),
                        "cli_sha256": expected_hash,
                        "path_managed": False,
                        "agents": {},
                        "updated_at": "build-verification",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            doctor = _run_json(
                executable,
                "doctor",
                environment_overrides={
                    "AUTO_EMAIL_SENDER_AGENT_MANIFEST_FILE": manifest_path.as_posix(),
                    "AUTO_EMAIL_SENDER_RUNTIME_FILE": runtime_path.as_posix(),
                },
            )
            _validate_agent_installation_check(
                doctor,
                schema_version=schema_version,
                expected_hash=expected_hash,
                expected_binding=expected_binding,
            )
    return supported_versions


def _validate_agent_installation_check(
    doctor: dict[str, Any],
    *,
    schema_version: int,
    expected_hash: str,
    expected_binding: str | None,
) -> None:
    data = _object(doctor.get("data"), "doctor.data")
    checks = data.get("checks")
    if not isinstance(checks, list):
        raise RuntimeError("frozen CLI doctor payload is missing checks")
    cli_check = next(
        (
            check
            for check in checks
            if isinstance(check, dict) and check.get("id") == "cli_installation"
        ),
        None,
    )
    if cli_check is None:
        raise RuntimeError("frozen CLI doctor is missing the cli_installation check")
    if cli_check.get("ok") is not True:
        message = str(cli_check.get("message") or "validation failed")
        raise RuntimeError(
            f"frozen CLI cannot validate Agent manifest schema {schema_version}: {message}",
        )
    details = _object(cli_check.get("details"), "doctor cli_installation details")
    if details.get("state") != "installed":
        raise RuntimeError(
            f"frozen CLI reported an unexpected installation state for schema {schema_version}",
        )
    if details.get("expected_sha256") != expected_hash:
        raise RuntimeError(
            f"frozen CLI reported the wrong installation fingerprint for schema {schema_version}",
        )
    if schema_version == 5 and details.get("hash_kind") != "canonical_directory_v1":
        raise RuntimeError(
            "frozen CLI does not implement the schema 5 directory fingerprint contract"
        )
    if expected_binding is not None:
        checks = details.get("checks")
        if not isinstance(checks, list):
            raise RuntimeError(
                f"frozen CLI omitted target binding checks for schema {schema_version}",
            )
        binding = next(
            (
                check
                for check in checks
                if isinstance(check, dict) and check.get("id") == "cli_target_binding"
            ),
            None,
        )
        if (
            binding is None
            or binding.get("ok") is not True
            or binding.get("binding_type") != expected_binding
        ):
            raise RuntimeError(
                f"frozen CLI cannot validate the {expected_binding} target for schema {schema_version}",
            )


def _expected_cli_hash(executable: Path, schema_version: int) -> str:
    if schema_version == 4:
        return _sha256_file(executable)
    if schema_version == 5:
        return _sha256_directory(executable.parent)
    raise RuntimeError(
        f"update frozen CLI verification for Agent manifest schema {schema_version}",
    )


def _create_verification_cli_target(
    executable: Path,
    verification_root: Path,
    schema_version: int,
) -> tuple[Path, str | None]:
    if schema_version == 4:
        target = verification_root / f"installed-v4{executable.suffix}"
        copyfile(executable, target)
        return target, None
    if schema_version == 5 and os.name == "nt":
        target = verification_root / "auto-email-sender-v5.cmd"
        expected_source = str(executable.resolve()).replace("%", "%%")
        target.write_bytes(
            f'@echo off\r\n"{expected_source}" %*\r\nexit /b %ERRORLEVEL%\r\n'.encode(),
        )
        return target, "windows_launcher"
    if schema_version == 5:
        target = verification_root / "auto-email-sender-v5"
        target.symlink_to(executable.resolve())
        return target, "symlink"
    raise RuntimeError(
        f"update frozen CLI target verification for Agent manifest schema {schema_version}",
    )


def _sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_directory(directory: Path) -> str:
    entries: list[str] = []

    def visit(relative_directory: Path) -> None:
        current = directory / relative_directory
        for child in sorted(current.iterdir(), key=lambda item: item.name):
            relative = (relative_directory / child.name).as_posix()
            if child.is_symlink():
                entries.append(f"L\t{relative}\t{os.readlink(child)}")
            elif child.is_dir():
                entries.append(f"D\t{relative}")
                visit(relative_directory / child.name)
            elif child.is_file():
                entries.append(f"F\t{relative}\t{_sha256_file(child)}")
            else:
                entries.append(f"O\t{relative}")

    visit(Path())
    canonical_listing = "\n".join(entries)
    if entries:
        canonical_listing += "\n"
    return hashlib.sha256(canonical_listing.encode("utf-8")).hexdigest()


def _run_json(
    executable: Path,
    command: str,
    *,
    environment_overrides: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    environment = os.environ.copy()
    # Validate what PyInstaller embedded, not development/test overrides from
    # the shell that happened to invoke the build script.
    environment.pop("AUTO_EMAIL_SENDER_BUILD_REVISION", None)
    environment.pop("AUTO_EMAIL_SENDER_CLI_VERSION", None)
    for name in (
        "AUTO_EMAIL_SENDER_AGENT_MANIFEST_FILE",
        "AUTO_EMAIL_SENDER_RUNTIME_FILE",
        "AUTO_EMAIL_SENDER_DATA_DIR",
        "AUTO_EMAIL_SENDER_BASE_URL",
        "AUTO_EMAIL_SENDER_AGENT_TOKEN",
    ):
        environment.pop(name, None)
    if environment_overrides is not None:
        environment.update(environment_overrides)
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


def _positive_integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RuntimeError(f"{label} must be a positive integer")
    return value


if __name__ == "__main__":
    main()
