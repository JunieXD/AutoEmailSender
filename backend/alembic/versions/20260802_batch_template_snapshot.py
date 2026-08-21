"""add batch task outreach template snapshots

Revision ID: 20260802_batch_template_snapshot
Revises: 20260730_db_performance
Create Date: 2026-08-02 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260802_batch_template_snapshot"
down_revision: Union[str, Sequence[str], None] = "20260730_db_performance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    snapshot_columns = (
        sa.Column("outreach_template_id", sa.Integer(), nullable=True),
        sa.Column(
            "outreach_template_name_snapshot", sa.String(length=120), nullable=True
        ),
        sa.Column("outreach_template_snapshot_version", sa.Integer(), nullable=True),
        sa.Column("outreach_generation_mode", sa.String(length=20), nullable=True),
        sa.Column("outreach_template_subject", sa.String(length=255), nullable=True),
        sa.Column("outreach_template_body_text", sa.Text(), nullable=True),
        sa.Column("outreach_template_body_html", sa.Text(), nullable=True),
    )
    snapshot_column_names = {column.name for column in snapshot_columns}
    connection = op.get_bind()
    existing_columns = _column_names("batch_tasks")
    legacy_columns = [
        column["name"]
        for column in sa.inspect(connection).get_columns("batch_tasks")
        if column["name"] not in snapshot_column_names
    ]
    missing_columns = [
        column for column in snapshot_columns if column.name not in existing_columns
    ]
    needs_foreign_key = not _has_foreign_key(
        "batch_tasks",
        "outreach_template_id",
        "outreach_templates",
    )
    legacy_staging_table = "_alembic_batch_tasks_before_outreach_snapshot"
    _stage_legacy_batch_rows(connection, legacy_staging_table, legacy_columns)
    try:
        if missing_columns or needs_foreign_key:
            with op.batch_alter_table("batch_tasks") as batch_op:
                for column in missing_columns:
                    batch_op.add_column(column)
                if needs_foreign_key:
                    batch_op.create_foreign_key(
                        "fk_batch_tasks_outreach_template_id_outreach_templates",
                        "outreach_templates",
                        ["outreach_template_id"],
                        ["id"],
                        ondelete="SET NULL",
                    )

        op.create_index(
            "ix_batch_tasks_outreach_template_id",
            "batch_tasks",
            ["outreach_template_id"],
            unique=False,
            if_not_exists=True,
        )

        _backfill_batch_snapshots(connection)
        _verify_legacy_batch_rows(connection, legacy_staging_table, legacy_columns)
    finally:
        _drop_temporary_table(connection, legacy_staging_table)


def downgrade() -> None:
    op.drop_index("ix_batch_tasks_outreach_template_id", table_name="batch_tasks")
    with op.batch_alter_table("batch_tasks") as batch_op:
        batch_op.drop_constraint(
            "fk_batch_tasks_outreach_template_id_outreach_templates",
            type_="foreignkey",
        )
        batch_op.drop_column("outreach_template_body_html")
        batch_op.drop_column("outreach_template_body_text")
        batch_op.drop_column("outreach_template_subject")
        batch_op.drop_column("outreach_generation_mode")
        batch_op.drop_column("outreach_template_snapshot_version")
        batch_op.drop_column("outreach_template_name_snapshot")
        batch_op.drop_column("outreach_template_id")


def _column_names(table_name: str) -> set[str]:
    return {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _has_foreign_key(
    table_name: str,
    column_name: str,
    referred_table: str,
) -> bool:
    return any(
        foreign_key.get("constrained_columns") == [column_name]
        and foreign_key.get("referred_table") == referred_table
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name)
    )


def _backfill_batch_snapshots(connection: sa.Connection) -> None:
    """Copy only historical child snapshots that were explicitly versioned.

    Older resend behavior used the first email task in creation order.  Staging
    the expected values in a temporary table keeps that choice deterministic,
    avoids blessing incomplete legacy rows as authoritative snapshots, and lets
    the migration verify every value before Alembic records the new revision.
    """

    staging_table = "_alembic_batch_task_outreach_snapshot_backfill"
    _drop_temporary_table(connection, staging_table)
    try:
        connection.execute(
            sa.text(
                f"""
                CREATE TEMPORARY TABLE {staging_table} AS
                SELECT
                    batch_tasks.id AS batch_task_id,
                    source_template.id AS outreach_template_id,
                    source_template.name AS outreach_template_name_snapshot,
                    first_email_task.outreach_template_snapshot_version
                        AS outreach_template_snapshot_version,
                    COALESCE(
                        first_email_task.outreach_generation_mode,
                        source_identity.outreach_generation_mode,
                        'llm'
                    ) AS outreach_generation_mode,
                    COALESCE(
                        first_email_task.outreach_template_subject,
                        batch_tasks.email_subject
                    ) AS outreach_template_subject,
                    COALESCE(
                        first_email_task.outreach_template_body_text,
                        batch_tasks.email_body
                    ) AS outreach_template_body_text,
                    first_email_task.outreach_template_body_html
                        AS outreach_template_body_html
                FROM batch_tasks
                JOIN email_tasks AS first_email_task
                  ON first_email_task.id = (
                        SELECT candidate.id
                        FROM email_tasks AS candidate
                        WHERE candidate.batch_task_id = batch_tasks.id
                        ORDER BY candidate.created_at, candidate.id
                        LIMIT 1
                    )
                LEFT JOIN outreach_templates AS source_template
                  ON source_template.id = first_email_task.outreach_template_id
                LEFT JOIN identity_profiles AS source_identity
                  ON source_identity.id = batch_tasks.identity_id
                WHERE first_email_task.outreach_template_snapshot_version IS NOT NULL
                """,
            ),
        )
        connection.execute(
            sa.text(
                f"""
                UPDATE batch_tasks
                SET outreach_template_id = (
                        SELECT staged.outreach_template_id
                        FROM {staging_table} AS staged
                        WHERE staged.batch_task_id = batch_tasks.id
                    ),
                    outreach_template_name_snapshot = (
                        SELECT staged.outreach_template_name_snapshot
                        FROM {staging_table} AS staged
                        WHERE staged.batch_task_id = batch_tasks.id
                    ),
                    outreach_template_snapshot_version = (
                        SELECT staged.outreach_template_snapshot_version
                        FROM {staging_table} AS staged
                        WHERE staged.batch_task_id = batch_tasks.id
                    ),
                    outreach_generation_mode = (
                        SELECT staged.outreach_generation_mode
                        FROM {staging_table} AS staged
                        WHERE staged.batch_task_id = batch_tasks.id
                    ),
                    outreach_template_subject = (
                        SELECT staged.outreach_template_subject
                        FROM {staging_table} AS staged
                        WHERE staged.batch_task_id = batch_tasks.id
                    ),
                    outreach_template_body_text = (
                        SELECT staged.outreach_template_body_text
                        FROM {staging_table} AS staged
                        WHERE staged.batch_task_id = batch_tasks.id
                    ),
                    outreach_template_body_html = (
                        SELECT staged.outreach_template_body_html
                        FROM {staging_table} AS staged
                        WHERE staged.batch_task_id = batch_tasks.id
                    )
                WHERE EXISTS (
                    SELECT 1
                    FROM {staging_table} AS staged
                    WHERE staged.batch_task_id = batch_tasks.id
                )
                """,
            ),
        )

        mismatch = connection.execute(
            sa.text(
                f"""
                SELECT batch_tasks.id
                FROM batch_tasks
                JOIN {staging_table} AS staged
                  ON staged.batch_task_id = batch_tasks.id
                WHERE NOT (
                        batch_tasks.outreach_template_id
                        IS staged.outreach_template_id
                    )
                   OR NOT (
                        batch_tasks.outreach_template_name_snapshot
                        IS staged.outreach_template_name_snapshot
                    )
                   OR NOT (
                        batch_tasks.outreach_template_snapshot_version
                        IS staged.outreach_template_snapshot_version
                    )
                   OR NOT (
                        batch_tasks.outreach_generation_mode
                        IS staged.outreach_generation_mode
                    )
                   OR NOT (
                        batch_tasks.outreach_template_subject
                        IS staged.outreach_template_subject
                    )
                   OR NOT (
                        batch_tasks.outreach_template_body_text
                        IS staged.outreach_template_body_text
                    )
                   OR NOT (
                        batch_tasks.outreach_template_body_html
                        IS staged.outreach_template_body_html
                    )
                LIMIT 1
                """,
            ),
        ).fetchone()
        if mismatch is not None:
            raise RuntimeError(
                "batch outreach snapshot migration content mismatch "
                f"for batch task {mismatch[0]}",
            )
    finally:
        _drop_temporary_table(connection, staging_table)


def _stage_legacy_batch_rows(
    connection: sa.Connection,
    staging_table: str,
    legacy_columns: list[str],
) -> None:
    _drop_temporary_table(connection, staging_table)
    projection = ", ".join(_quote_identifier(column) for column in legacy_columns)
    connection.execute(
        sa.text(
            f"""
            CREATE TEMPORARY TABLE {staging_table} AS
            SELECT {projection}
            FROM batch_tasks
            """,
        ),
    )


def _verify_legacy_batch_rows(
    connection: sa.Connection,
    staging_table: str,
    legacy_columns: list[str],
) -> None:
    comparisons = " OR ".join(
        "NOT ("
        f"current_row.{_quote_identifier(column)} "
        f"IS original_row.{_quote_identifier(column)}"
        ")"
        for column in legacy_columns
    )
    mismatch = connection.execute(
        sa.text(
            f"""
            SELECT original_row.id
            FROM temp.{staging_table} AS original_row
            LEFT JOIN batch_tasks AS current_row
              ON current_row.id = original_row.id
            WHERE current_row.id IS NULL
               OR {comparisons}
            LIMIT 1
            """,
        ),
    ).fetchone()
    if mismatch is not None:
        raise RuntimeError(
            "batch task migration changed historical data "
            f"for batch task {mismatch[0]}",
        )

    unexpected = connection.execute(
        sa.text(
            f"""
            SELECT current_row.id
            FROM batch_tasks AS current_row
            LEFT JOIN temp.{staging_table} AS original_row
              ON original_row.id = current_row.id
            WHERE original_row.id IS NULL
            LIMIT 1
            """,
        ),
    ).fetchone()
    if unexpected is not None:
        raise RuntimeError(
            f"batch task migration unexpectedly added historical row {unexpected[0]}",
        )


def _drop_temporary_table(connection: sa.Connection, table_name: str) -> None:
    connection.execute(sa.text(f"DROP TABLE IF EXISTS temp.{table_name}"))


def _quote_identifier(value: str) -> str:
    escaped = value.replace('"', '""')
    return f'"{escaped}"'
