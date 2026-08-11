#!/usr/bin/env python3
"""Seed isolated data through a real previous packaged desktop application."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import psutil

QA_PATH_MARKER = "auto-email-sender-packaged-qa"
DATABASE_NAME = "auto_email_sender.db"
RUNTIME_DESCRIPTOR = Path("agent") / "runtime.json"
SETTINGS_READ_ONLY_FIELDS = frozenset({"revision", "updated_at"})
T = TypeVar("T")


class UpgradeSeedFailure(RuntimeError):
    """The previous packaged application could not produce safe upgrade data."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed upgrade evidence through a previous packaged desktop app.",
    )
    parser.add_argument("--app-executable", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--package-file", type=Path, required=True)
    parser.add_argument("--user-data", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=180)
    args = parser.parse_args(argv)
    args.app_executable = args.app_executable.expanduser().resolve()
    args.artifact_root = args.artifact_root.expanduser().resolve()
    args.package_file = args.package_file.expanduser().resolve()
    args.user_data = args.user_data.expanduser().absolute()
    args.manifest = args.manifest.expanduser().absolute()
    if not args.app_executable.is_file():
        parser.error(f"previous packaged executable is missing: {args.app_executable}")
    if not args.artifact_root.exists():
        parser.error(f"previous packaged artifact root is missing: {args.artifact_root}")
    if not args.package_file.is_file():
        parser.error(f"previous package file is missing: {args.package_file}")
    try:
        args.app_executable.relative_to(
            args.artifact_root if args.artifact_root.is_dir() else args.artifact_root.parent
        )
    except ValueError:
        parser.error("previous executable must be inside --artifact-root")
    if QA_PATH_MARKER not in args.user_data.parts:
        parser.error(f"--user-data must contain the exact {QA_PATH_MARKER!r} marker")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _prepare_empty_user_data(args.user_data)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    logs_dir = args.manifest.parent / "previous-app-logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / "desktop.stdout.log"
    stderr_path = logs_dir / "desktop.stderr.log"
    marker = f"packaged-upgrade:{uuid.uuid4()}"
    process: subprocess.Popen[bytes] | None = None
    identity: dict[str, Any] | None = None
    professor: dict[str, Any] | None = None
    stdout_file = stdout_path.open("wb")
    stderr_file = stderr_path.open("wb")
    try:
        environment = os.environ.copy()
        environment.update(
            {
                "AUTO_EMAIL_SENDER_BACKEND_MODE": "combined",
                "AUTO_EMAIL_SENDER_AGENT_HOME": str(args.user_data / "agent-home"),
            }
        )
        process = subprocess.Popen(
            [str(args.app_executable), f"--user-data-dir={args.user_data}"],
            cwd=args.app_executable.parent,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
        )
        identity = _wait_until(
            lambda: _probe_runtime(args.user_data, process),
            timeout_seconds=args.timeout_seconds,
            description="previous packaged runtime readiness",
        )
        token = str(identity["access_token"])
        base_url = str(identity["base_url"]).rstrip("/")
        settings = _request_json(
            "GET",
            f"{base_url}/api/agent/v1/settings",
            token=token,
        )
        if not isinstance(settings, dict):
            raise UpgradeSeedFailure("previous settings endpoint returned no object")
        revision = settings.get("revision")
        settings_update = _build_settings_update_payload(settings, marker)
        updated = _request_json(
            "PATCH",
            f"{base_url}/api/agent/v1/settings",
            token=token,
            payload=settings_update,
            headers={
                "Idempotency-Key": f"previous-upgrade-{uuid.uuid4()}",
                **({"If-Revision": revision} if isinstance(revision, str) else {}),
            },
        )
        if not isinstance(updated, dict) or updated.get("draft_custom_instruction") != marker:
            raise UpgradeSeedFailure("previous packaged settings marker did not commit")
        professor = _request_json(
            "POST",
            f"{base_url}/api/agent/v1/professors",
            token=token,
            payload={
                "name": "升级验证导师 Ω",
                "email": f"upgrade-{uuid.uuid4().hex}@example.edu",
                "title": "Professor",
                "university": "示例大学",
                "school": "计算机学院",
                "department": "软件工程系",
                "research_direction": "可靠分布式系统",
                "recent_papers": ["Upgrade Safety 2026"],
                "profile_url": "https://previous-upgrade.test.invalid/profile",
                "source_url": "https://previous-upgrade.test.invalid/directory",
                "personal_note": "由上一稳定版真实安装包创建的升级验证记录。",
                "tag_ids": [],
            },
            headers={"Idempotency-Key": f"previous-professor-{uuid.uuid4()}"},
        )
        if not isinstance(professor, dict) or not isinstance(professor.get("id"), int):
            raise UpgradeSeedFailure("previous packaged professor did not commit")
    finally:
        if process is not None:
            _stop_process_tree(process)
        stdout_file.close()
        stderr_file.close()

    if identity is None or professor is None:
        raise UpgradeSeedFailure("previous packaged runtime produced no upgrade identity")
    database_path = args.user_data / DATABASE_NAME
    material = _seed_identity_material(database_path, args.user_data)
    connection = sqlite3.connect(database_path, timeout=10)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        revision_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        connection.close()
    if integrity != "ok" or foreign_keys != 0 or revision_row is None:
        raise UpgradeSeedFailure("previous packaged database failed integrity validation")
    manifest = {
        "protocol_version": "1",
        "purpose": "previous-stable-packaged-upgrade",
        "created_at": datetime.now(UTC).isoformat(),
        "user_data_path": str(args.user_data.resolve(strict=True)),
        "previous_app_version": identity["app_version"],
        "previous_runtime_id": identity["runtime_id"],
        "previous_artifact_root": str(args.artifact_root),
        "previous_artifact_sha256": _sha256_tree(args.artifact_root),
        "previous_executable_sha256": _sha256_file(args.app_executable),
        "previous_package_path": str(args.package_file),
        "previous_package_sha256": _sha256_file(args.package_file),
        "database_sha256": _sha256_file(database_path),
        "alembic_revision": str(revision_row[0]),
        "pre_upgrade_schema_backups": _schema_backup_inventory(args.user_data),
        "draft_custom_instruction": marker,
        "professor": {
            "id": professor["id"],
            "name": professor.get("name"),
            "email": professor.get("email"),
        },
        "material": material,
        "integrity_check": integrity,
        "foreign_key_violations": foreign_keys,
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }
    _write_json_atomic(args.manifest, manifest)
    print(f"PREVIOUS_PACKAGED_UPGRADE_MANIFEST={args.manifest}", flush=True)
    return 0


def _build_settings_update_payload(
    settings: dict[str, Any],
    marker: str,
) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in settings.items()
        if key not in SETTINGS_READ_ONLY_FIELDS
    }
    payload["draft_custom_instruction"] = marker
    return payload


def _prepare_empty_user_data(user_data: Path) -> None:
    user_data.mkdir(parents=True, mode=0o700, exist_ok=True)
    marker_seen = False
    current = Path(user_data.anchor)
    for part in user_data.parts[1:]:
        current /= part
        marker_seen = marker_seen or part == QA_PATH_MARKER
        if marker_seen and current.is_symlink():
            raise UpgradeSeedFailure("upgrade userData must not contain symbolic links")
    canonical = user_data.resolve(strict=True)
    if any(canonical.iterdir()):
        raise UpgradeSeedFailure("upgrade userData must be empty before previous app launch")
    with contextlib.suppress(OSError):
        canonical.chmod(0o700)
    if sys.platform != "win32" and canonical.stat().st_mode & 0o077:
        raise UpgradeSeedFailure("upgrade userData must not be group- or world-accessible")


def _probe_runtime(
    user_data: Path,
    process: subprocess.Popen[bytes],
) -> dict[str, Any] | None:
    if process.poll() is not None:
        raise UpgradeSeedFailure(
            f"previous packaged desktop exited with code {process.returncode}"
        )
    descriptor_path = user_data / RUNTIME_DESCRIPTOR
    try:
        payload = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    required = ("app_version", "runtime_id", "base_url", "access_token")
    if any(not isinstance(payload.get(name), str) or not payload[name] for name in required):
        return None
    base_url = str(payload["base_url"]).rstrip("/")
    try:
        runtime = _request_json(
            "GET",
            f"{base_url}/api/agent/v1/runtime",
            token=str(payload["access_token"]),
            timeout_seconds=2,
        )
    except (OSError, UpgradeSeedFailure):
        return None
    if not isinstance(runtime, dict) or runtime.get("state") != "ready":
        return None
    if runtime.get("runtime_id") != payload.get("runtime_id"):
        raise UpgradeSeedFailure("previous runtime descriptor identity does not match API")
    return payload


def _seed_identity_material(database_path: Path, user_data: Path) -> dict[str, object]:
    material_directory = user_data / "materials" / "升级 数据 Ω"
    material_directory.mkdir(parents=True, exist_ok=True)
    material_path = material_directory / "上一稳定版 简历 Ω.txt"
    content = "上一稳定版创建的真实材料。\nReliable distributed systems.\n".encode()
    material_path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    connection = sqlite3.connect(database_path, timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        suffix = uuid.uuid4().hex
        identity_id = int(
            connection.execute(
                """
                INSERT INTO identity_profiles (
                    name, profile_name, sender_name, email_address,
                    smtp_host, smtp_port, smtp_username, smtp_password
                ) VALUES (?, ?, ?, ?, 'smtp.previous.test.invalid', 465, ?, 'not-a-secret')
                """,
                (
                    "上一稳定版身份 Ω",
                    "上一稳定版身份 Ω",
                    "Upgrade Student",
                    f"upgrade-identity-{suffix}@example.invalid",
                    f"upgrade-identity-{suffix}@example.invalid",
                ),
            ).lastrowid
        )
        material_id = int(
            connection.execute(
                """
                INSERT INTO identity_materials (
                    identity_id, display_name, original_filename, file_path,
                    mime_type, size_bytes, sha256, extracted_text, material_type
                ) VALUES (?, ?, ?, ?, 'text/plain', ?, ?, ?, 'resume')
                """,
                (
                    identity_id,
                    "上一稳定版简历 Ω",
                    material_path.name,
                    str(material_path),
                    len(content),
                    digest,
                    content.decode(),
                ),
            ).lastrowid
        )
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(identity_profiles)")
        }
        if "current_primary_material_id" in columns:
            connection.execute(
                "UPDATE identity_profiles SET current_primary_material_id = ? WHERE id = ?",
                (material_id, identity_id),
            )
        connection.commit()
    finally:
        connection.close()
    return {
        "id": material_id,
        "identity_id": identity_id,
        "relative_path": material_path.relative_to(user_data).as_posix(),
        "sha256": digest,
        "bytes": len(content),
    }


def _request_json(
    method: str,
    url: str,
    *,
    token: str,
    payload: object | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 20,
) -> object:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise UpgradeSeedFailure(f"upgrade seed client refuses non-loopback URL: {url}")
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request_headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        **({"Content-Type": "application/json"} if body is not None else {}),
        **(headers or {}),
    }
    connection = http.client.HTTPConnection(
        parsed.hostname,
        parsed.port,
        timeout=timeout_seconds,
    )
    try:
        path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
    finally:
        connection.close()
    try:
        decoded = json.loads(raw.decode()) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpgradeSeedFailure(
            f"{method} {parsed.path} returned non-JSON HTTP {response.status}"
        ) from exc
    if not 200 <= response.status < 300:
        raise UpgradeSeedFailure(
            f"{method} {parsed.path} returned HTTP {response.status}: {decoded!r}"
        )
    return decoded


def _wait_until(
    probe: Any,
    *,
    timeout_seconds: float,
    description: str,
) -> T:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = probe()
            if value is not None:
                return value
        except UpgradeSeedFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - preserve transient startup cause
            last_error = exc
        time.sleep(0.1)
    suffix = f"; last error={last_error}" if last_error is not None else ""
    raise UpgradeSeedFailure(f"timed out waiting for {description}{suffix}")


def _stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    pids = {process.pid}
    with contextlib.suppress(psutil.Error):
        pids.update(child.pid for child in psutil.Process(process.pid).children(recursive=True))
    if process.poll() is None:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T"],
                capture_output=True,
                check=False,
                timeout=20,
            )
        else:
            with contextlib.suppress(ProcessLookupError):
                os.kill(process.pid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=20)
    remaining: list[psutil.Process] = []
    for pid in pids:
        try:
            candidate = psutil.Process(pid)
            if candidate.is_running() and candidate.status() != psutil.STATUS_ZOMBIE:
                remaining.append(candidate)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    for candidate in remaining:
        with contextlib.suppress(psutil.Error):
            candidate.kill()
    psutil.wait_procs(remaining, timeout=15)
    still_running = [candidate.pid for candidate in remaining if candidate.is_running()]
    if still_running:
        raise UpgradeSeedFailure(
            f"previous packaged process tree did not exit: {still_running}"
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_backup_inventory(user_data: Path) -> list[dict[str, object]]:
    backup_root = user_data / "backups" / "schema"
    if not backup_root.exists():
        return []
    inventory: list[dict[str, object]] = []
    for backup_path in sorted(backup_root.glob("*.db")):
        if not backup_path.is_file() or backup_path.is_symlink():
            continue
        inventory.append(
            {
                "relative_path": backup_path.relative_to(user_data).as_posix(),
                "sha256": _sha256_file(backup_path),
                "bytes": backup_path.stat().st_size,
            }
        )
    return inventory


def _sha256_tree(path: Path) -> str:
    root = path.resolve()
    candidates = [root] if root.is_file() else sorted(root.rglob("*"))
    digest = hashlib.sha256()
    for candidate in candidates:
        relative = candidate.name if root.is_file() else candidate.relative_to(root).as_posix()
        stat = candidate.lstat()
        if candidate.is_symlink():
            kind = "symlink"
            content = os.readlink(candidate).encode()
        elif candidate.is_file():
            kind = "file"
            content = bytes.fromhex(_sha256_file(candidate))
        elif candidate.is_dir():
            kind = "directory"
            content = b""
        else:
            kind = "other"
            content = b""
        digest.update(f"{kind}\0{relative}\0{stat.st_mode & 0o7777:o}\0".encode())
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
        with contextlib.suppress(OSError):
            temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
