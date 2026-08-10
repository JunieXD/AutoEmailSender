from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.config import get_settings
from app.core.schema_metadata import get_sqlite_database_path


def configure_sqlite_runtime() -> None:
    """Configure the API-owned SQLite file before any Worker may start."""

    settings = get_settings()
    database_path = get_sqlite_database_path(settings.database_url)
    if database_path is None:
        return
    _validate_database_path(database_path)
    _configure_or_verify(
        database_path,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
        wal_required=settings.sqlite_wal_enabled,
        configure_wal=True,
        require_write_probe=True,
    )


def require_sqlite_runtime_ready() -> None:
    """Verify a Worker can safely join the API-owned SQLite runtime."""

    settings = get_settings()
    database_path = get_sqlite_database_path(settings.database_url)
    if database_path is None:
        raise RuntimeError("Desktop Worker requires a SQLite database URL")
    _validate_database_path(database_path)
    _configure_or_verify(
        database_path,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
        wal_required=settings.sqlite_wal_enabled,
        configure_wal=False,
        require_write_probe=False,
    )


def _configure_or_verify(
    database_path: Path,
    *,
    busy_timeout_ms: int,
    wal_required: bool,
    configure_wal: bool,
    require_write_probe: bool,
) -> None:
    timeout_seconds = max(0, busy_timeout_ms) / 1000
    connection = sqlite3.connect(
        f"{database_path.resolve().as_uri()}?mode=rw",
        uri=True,
        timeout=timeout_seconds,
    )
    try:
        connection.execute(f"PRAGMA busy_timeout={max(0, busy_timeout_ms)}")
        if configure_wal and wal_required:
            row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
        else:
            row = connection.execute("PRAGMA journal_mode").fetchone()
        actual_mode = str(row[0]).lower() if row else "unknown"
        if wal_required and actual_mode != "wal":
            raise RuntimeError(
                "SQLite WAL mode is required for the desktop API + Worker runtime; "
                f"database reported journal_mode={actual_mode}"
            )
        if require_write_probe:
            # Acquiring and rolling back an immediate transaction verifies that the
            # WAL/SHM directory is writable without changing application rows.
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
    finally:
        connection.close()


def _validate_database_path(database_path: Path) -> None:
    if not database_path.is_file():
        raise RuntimeError(f"Desktop SQLite database does not exist: {database_path}")


__all__ = [
    "configure_sqlite_runtime",
    "require_sqlite_runtime_ready",
]
