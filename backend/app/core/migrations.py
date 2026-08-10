from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.config import get_settings
from app.core.fault_injection import wait_at_fault_point_sync
from app.core.instance_lock import DatabaseMigrationLock
from app.core.schema_backup import create_schema_backup
from app.core.schema_metadata import (
    get_current_app_version,
    check_database_compatibility,
    get_schema_backup_dir,
    get_sqlite_database_path,
    read_app_metadata,
    update_app_metadata,
)
from app.core.sqlite_runtime import (
    configure_sqlite_runtime,
    require_sqlite_runtime_ready,
)

ALEMBIC_INI_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"


def get_alembic_config() -> Config:
    return Config(str(ALEMBIC_INI_PATH))


def get_head_revision(config: Config | None = None) -> str:
    script = ScriptDirectory.from_config(config or get_alembic_config())
    head = script.get_current_head()
    if head is None:
        raise RuntimeError("Alembic head revision is not available")
    return head


def get_current_database_revision(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'alembic_version'
        """,
    ).fetchone()
    if row is None:
        return None
    version_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    return str(version_row[0]) if version_row is not None else None


def run_migrations_to_head() -> None:
    settings = get_settings()
    with DatabaseMigrationLock(settings.data_dir):
        wait_at_fault_point_sync("migration.lock_acquired")
        _run_migrations_to_head_locked()


def _run_migrations_to_head_locked() -> None:
    settings = get_settings()
    database_path = get_sqlite_database_path(settings.database_url)
    config = get_alembic_config()
    target_revision = get_head_revision(config)
    current_app_version = get_current_app_version()

    source_revision: str | None = None
    should_backup = False
    should_update_metadata = database_path is not None
    if database_path is not None and database_path.exists() and should_update_metadata:
        connection = sqlite3.connect(database_path)
        try:
            check_database_compatibility(
                connection,
                current_app_version=current_app_version,
                backup_directory=get_schema_backup_dir(settings.data_dir),
            )
            source_revision = get_current_database_revision(connection)
            metadata = read_app_metadata(connection)
            should_backup = source_revision != target_revision or not metadata
            should_update_metadata = should_backup
        finally:
            connection.close()

    if database_path is not None and database_path.exists() and should_backup:
        create_schema_backup(
            database_path=database_path,
            backup_dir=get_schema_backup_dir(settings.data_dir),
            app_version=current_app_version,
            source_schema_revision=source_revision,
            target_schema_revision=target_revision,
        )

    wait_at_fault_point_sync("migration.before_upgrade")
    command.upgrade(config, "head")
    wait_at_fault_point_sync("migration.after_upgrade")

    if database_path is not None and database_path.exists() and should_update_metadata:
        connection = sqlite3.connect(database_path)
        try:
            update_app_metadata(
                connection,
                app_version=current_app_version,
                schema_revision=target_revision,
            )
        finally:
            connection.close()


async def ensure_database_schema() -> None:
    await asyncio.to_thread(run_migrations_to_head)
    await asyncio.to_thread(configure_sqlite_runtime)


def require_database_schema_at_head() -> None:
    """Validate Worker schema compatibility without performing any migration."""

    settings = get_settings()
    database_path = get_sqlite_database_path(settings.database_url)
    if database_path is None:
        raise RuntimeError("Desktop Worker requires a SQLite database URL")
    if not database_path.is_file():
        raise RuntimeError("Desktop database does not exist; start the API before Worker")

    config = get_alembic_config()
    expected_revision = get_head_revision(config)
    connection = sqlite3.connect(database_path)
    try:
        check_database_compatibility(
            connection,
            current_app_version=get_current_app_version(),
            backup_directory=get_schema_backup_dir(settings.data_dir),
        )
        current_revision = get_current_database_revision(connection)
    finally:
        connection.close()
    if current_revision != expected_revision:
        raise RuntimeError(
            "Desktop database schema is not current; "
            f"expected {expected_revision}, found {current_revision or 'unversioned'}"
        )
    require_sqlite_runtime_ready()
