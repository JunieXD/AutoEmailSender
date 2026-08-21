from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

CURRENT_SCHEMA_VERSION = 1
APP_VERSION_ENV_VAR = "AUTO_EMAIL_SENDER_APP_VERSION"
DATABASE_REQUIRES_NEWER_APP = "DATABASE_REQUIRES_NEWER_APP"


@dataclass(slots=True)
class DatabaseRequiresNewerAppError(RuntimeError):
    current_app_version: str
    minimum_supported_app_version: str
    backup_directory: Path

    @property
    def code(self) -> str:
        return DATABASE_REQUIRES_NEWER_APP

    def __str__(self) -> str:
        return (
            "当前数据由较新版本创建，当前版本无法直接打开。"
            f"当前版本：{self.current_app_version}；"
            f"最低可用版本：{self.minimum_supported_app_version}。"
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": "当前数据由较新版本创建，当前版本无法直接打开。",
            "current_app_version": self.current_app_version,
            "minimum_supported_app_version": self.minimum_supported_app_version,
            "backup_directory": str(self.backup_directory),
            "suggested_actions": [
                f"安装 {self.minimum_supported_app_version} 或更高版本继续使用",
                "如需回退，请从升级前备份恢复数据库",
            ],
        }


def get_schema_backup_dir(data_dir: Path) -> Path:
    return data_dir / "backups" / "schema"


def get_sqlite_database_path(database_url: str) -> Path | None:
    parsed = urlparse(database_url)
    if parsed.scheme not in {"sqlite", "sqlite+aiosqlite"}:
        return None
    if parsed.netloc:
        raw_path = f"//{parsed.netloc}{parsed.path}"
    else:
        raw_path = parsed.path
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    if not raw_path:
        return None
    return Path(unquote(raw_path))


def ensure_app_metadata_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS app_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
    )


def read_app_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'app_metadata'
        """,
    ).fetchone()
    if row is None:
        return {}
    rows = connection.execute("SELECT key, value FROM app_metadata").fetchall()
    return {str(key): str(value) for key, value in rows}


def check_database_compatibility(
    connection: sqlite3.Connection,
    *,
    current_app_version: str,
    backup_directory: Path | None = None,
) -> None:
    metadata = read_app_metadata(connection)
    minimum = metadata.get("minimum_supported_app_version")
    if minimum is None:
        return
    if compare_versions(current_app_version, minimum) < 0:
        raise DatabaseRequiresNewerAppError(
            current_app_version=current_app_version,
            minimum_supported_app_version=minimum,
            backup_directory=backup_directory or Path("backups") / "schema",
        )


def get_current_app_version() -> str:
    env_version = os.environ.get(APP_VERSION_ENV_VAR, "").strip()
    if env_version:
        return env_version.lstrip("vV")
    package_json = Path(__file__).resolve().parents[3] / "desktop" / "package.json"
    try:
        raw = json.loads(package_json.read_text(encoding="utf-8"))
        version = str(raw.get("version", "")).strip()
    except (OSError, json.JSONDecodeError):
        version = ""
    return version.lstrip("vV") or "0.0.0"


def get_minimum_supported_app_version(schema_revision: str) -> str:
    return get_current_app_version()


def update_app_metadata(
    connection: sqlite3.Connection,
    *,
    app_version: str,
    schema_revision: str,
) -> None:
    ensure_app_metadata_table(connection)
    values = {
        "schema_version": str(CURRENT_SCHEMA_VERSION),
        "schema_revision": schema_revision,
        "schema_updated_by_app_version": app_version,
        "minimum_supported_app_version": get_minimum_supported_app_version(
            schema_revision
        ),
        "schema_updated_at": datetime.now(UTC).isoformat(),
    }
    connection.executemany(
        """
        INSERT INTO app_metadata (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        values.items(),
    )
    connection.commit()


def compare_versions(left: str, right: str) -> int:
    left_parts = _parse_version(left)
    right_parts = _parse_version(right)
    max_length = max(len(left_parts), len(right_parts))
    left_parts.extend([0] * (max_length - len(left_parts)))
    right_parts.extend([0] * (max_length - len(right_parts)))
    if left_parts < right_parts:
        return -1
    if left_parts > right_parts:
        return 1
    return 0


def _parse_version(value: str) -> list[int]:
    normalized = value.strip().lstrip("vV").split("-", 1)[0]
    parts: list[int] = []
    for part in normalized.split("."):
        match = re.match(r"\d+", part)
        parts.append(int(match.group(0)) if match else 0)
    return parts or [0]
