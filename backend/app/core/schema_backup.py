from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_BACKUP_KEEP_COUNT = 5

@dataclass(frozen=True, slots=True)
class SchemaBackupResult:
    database_backup_path: Path
    metadata_path: Path

def create_schema_backup(
    *,
    database_path: Path,
    backup_dir: Path,
    app_version: str,
    source_schema_revision: str | None,
    target_schema_revision: str,
) -> SchemaBackupResult:
    backup_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(UTC)
    timestamp = created_at.strftime("%Y%m%d-%H%M%S")
    backup_path = _unique_backup_path(backup_dir, app_version, timestamp)
    metadata_path = backup_path.with_suffix(".json")

    source = sqlite3.connect(database_path)
    try:
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    metadata = {
        "created_at": created_at.isoformat(),
        "app_version": app_version,
        "database_path": str(database_path),
        "reason": "before_schema_migration",
        "source_schema_revision": source_schema_revision,
        "target_schema_revision": target_schema_revision,
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    prune_schema_backups(backup_dir, keep=SCHEMA_BACKUP_KEEP_COUNT)
    return SchemaBackupResult(database_backup_path=backup_path, metadata_path=metadata_path)

def prune_schema_backups(
    backup_dir: Path,
    *,
    keep: int = SCHEMA_BACKUP_KEEP_COUNT,
) -> None:
    if not backup_dir.exists():
        return
    backups = sorted(
        backup_dir.glob("auto_email_sender.before-*.db"),
        key=_backup_sort_key,
        reverse=True,
    )
    for db_path in backups[keep:]:
        db_path.unlink(missing_ok=True)
        db_path.with_suffix(".json").unlink(missing_ok=True)

def _unique_backup_path(backup_dir: Path, app_version: str, timestamp: str) -> Path:
    base_name = f"auto_email_sender.before-{app_version}.{timestamp}"
    candidate = backup_dir / f"{base_name}.db"
    counter = 1
    while candidate.exists() or candidate.with_suffix(".json").exists():
        candidate = backup_dir / f"{base_name}-{counter}.db"
        counter += 1
    return candidate

def _backup_sort_key(db_path: Path) -> tuple[datetime, str]:
    metadata_path = db_path.with_suffix(".json")
    if metadata_path.exists():
        try:
            raw = json.loads(metadata_path.read_text(encoding="utf-8"))
            created_at = raw.get("created_at")
            if isinstance(created_at, str):
                return (datetime.fromisoformat(created_at), db_path.name)
        except Exception:
            pass
    return (datetime.fromtimestamp(db_path.stat().st_mtime, tz=UTC), db_path.name)