from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.config import get_settings
from app.core.schema_backup import create_schema_backup
from app.core.schema_metadata import (
    get_current_app_version,
    check_database_compatibility,
    get_schema_backup_dir,
    get_sqlite_database_path,
    read_app_metadata,
    update_app_metadata,
)

ALEMBIC_INI_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"
PUBLIC_BETA_REVISION = "20260812_merge_beta_master"

_PUBLIC_BETA_REQUIRED_COLUMNS = {
    "email_delivery_attempts": {
        "id",
        "email_task_id",
        "identity_id",
        "professor_id",
        "attempt_number",
        "recipient_email",
        "subject_fingerprint",
        "content_fingerprint",
        "app_message_id",
        "normalized_app_message_id",
        "status",
        "started_at",
        "completed_at",
        "created_at",
        "owner_role",
        "runtime_id",
        "owner_generation",
        "owner_pid",
        "outcome",
        "finalized_at",
        "smtp_accepted_at",
        "prepared_rfc_message_id",
        "subject",
        "content",
        "content_html",
        "attachment_count",
        "provider_payload",
        "error_summary",
    },
    "email_tasks": {
        "delivery_attempt_id",
        "delivery_outcome",
        "delivery_outcome_at",
    },
    "email_logs": {
        "delivery_attempt_id",
        "merged_into_id",
        "record_state",
        "reconciliation_version",
    },
    "email_observations": {
        "email_log_id",
        "candidate_email_log_id",
        "delivery_attempt_id",
        "legacy_email_log_id",
        "resolution",
    },
    "agent_ui_handoffs": {"id"},
    "agent_ui_handoff_items": {"id", "handoff_id"},
}
_PUBLIC_BETA_REQUIRED_INDEXES = {
    "email_delivery_attempts": {
        "ix_email_delivery_attempts_identity_professor_started",
        "ix_email_delivery_attempts_message_id",
        "ix_email_delivery_attempts_outcome_finalized",
    },
    "email_tasks": {"ix_email_tasks_delivery_sending_attempt"},
    "email_logs": {
        "ix_email_logs_record_state_identity_direction_created",
        "uq_email_logs_delivery_attempt_id",
    },
    "email_observations": {
        "ix_email_observations_delivery_key",
        "uq_email_observations_legacy_log",
    },
}
_PUBLIC_BETA_REQUIRED_FOREIGN_KEYS = {
    "email_delivery_attempts": {
        ("email_task_id", "email_tasks", "id"),
        ("identity_id", "identity_profiles", "id"),
        ("professor_id", "professors", "id"),
    },
    "email_logs": {
        ("delivery_attempt_id", "email_delivery_attempts", "id"),
        ("merged_into_id", "email_logs", "id"),
    },
    "email_observations": {
        ("delivery_attempt_id", "email_delivery_attempts", "id"),
        ("legacy_email_log_id", "email_logs", "id"),
    },
}


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
    version_row = connection.execute(
        "SELECT version_num FROM alembic_version"
    ).fetchone()
    return str(version_row[0]) if version_row is not None else None


def _pragma_rows(
    connection: sqlite3.Connection,
    pragma: str,
    table_name: str,
) -> list[tuple[object, ...]]:
    escaped_table_name = table_name.replace("'", "''")
    return list(connection.execute(f"PRAGMA {pragma}('{escaped_table_name}')"))


def validate_public_beta_schema(connection: sqlite3.Connection) -> None:
    revisions = [
        str(row[0])
        for row in connection.execute("SELECT version_num FROM alembic_version")
    ]
    problems: list[str] = []
    if revisions != [PUBLIC_BETA_REVISION]:
        problems.append("revision marker")

    table_names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'",
        )
    }
    for table_name, required_columns in _PUBLIC_BETA_REQUIRED_COLUMNS.items():
        if table_name not in table_names:
            problems.append(f"table {table_name}")
            continue
        columns = {
            str(row[1]) for row in _pragma_rows(connection, "table_info", table_name)
        }
        missing_columns = sorted(required_columns - columns)
        if missing_columns:
            problems.append(
                f"columns {table_name}({', '.join(missing_columns)})",
            )

    for table_name, required_indexes in _PUBLIC_BETA_REQUIRED_INDEXES.items():
        if table_name not in table_names:
            continue
        indexes = {
            str(row[1]) for row in _pragma_rows(connection, "index_list", table_name)
        }
        missing_indexes = sorted(required_indexes - indexes)
        if missing_indexes:
            problems.append(
                f"indexes {table_name}({', '.join(missing_indexes)})",
            )

    for table_name, required_foreign_keys in _PUBLIC_BETA_REQUIRED_FOREIGN_KEYS.items():
        if table_name not in table_names:
            continue
        foreign_keys = {
            (str(row[3]), str(row[2]), str(row[4]))
            for row in _pragma_rows(connection, "foreign_key_list", table_name)
        }
        missing_foreign_keys = sorted(required_foreign_keys - foreign_keys)
        if missing_foreign_keys:
            problems.append(f"foreign keys {table_name}")

    integrity_result = connection.execute("PRAGMA quick_check").fetchone()
    if integrity_result is None or str(integrity_result[0]).lower() != "ok":
        problems.append("SQLite integrity")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        problems.append("foreign key integrity")

    if problems:
        raise RuntimeError(
            "The v2.6.0-beta.1 database does not match the published beta schema: "
            + "; ".join(problems),
        )


def run_migrations_to_head() -> None:
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
            if source_revision == PUBLIC_BETA_REVISION:
                validate_public_beta_schema(connection)
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

    command.upgrade(config, "head")

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
