"""add independent outreach template library

Revision ID: 20260730_template_library
Revises: 20260721_identity_comm_groups
Create Date: 2026-07-30 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260730_template_library"
down_revision: Union[str, Sequence[str], None] = "20260721_identity_comm_groups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "outreach_templates" not in _table_names():
        op.create_table(
            "outreach_templates",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column(
                "recommended_generation_mode",
                sa.String(length=20),
                nullable=False,
                server_default=sa.text("'llm'"),
            ),
            sa.Column("subject", sa.String(length=255), nullable=True),
            sa.Column("body_text", sa.Text(), nullable=True),
            sa.Column("body_html", sa.Text(), nullable=True),
            sa.Column(
                "is_default",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("migrated_from_identity_id", sa.Integer(), nullable=True),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "migrated_from_identity_id",
                name="uq_outreach_templates_migrated_from_identity_id",
            ),
        )

    op.create_index(
        "uq_outreach_templates_global_default",
        "outreach_templates",
        ["is_default"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
        if_not_exists=True,
    )

    _ensure_reference_column(
        "identity_profiles",
        "default_outreach_template_id",
        "outreach_templates",
        "fk_identity_profiles_default_outreach_template_id_outreach_templates",
    )
    _ensure_reference_column(
        "email_tasks",
        "outreach_template_id",
        "outreach_templates",
        "fk_email_tasks_outreach_template_id_outreach_templates",
    )
    _ensure_reference_column(
        "test_compose_sessions",
        "outreach_template_id",
        "outreach_templates",
        "fk_test_compose_sessions_outreach_template_id_outreach_templates",
    )
    _ensure_plain_column(
        "email_tasks",
        sa.Column("outreach_template_snapshot_version", sa.Integer(), nullable=True),
    )

    op.create_index(
        "ix_identity_profiles_default_outreach_template_id",
        "identity_profiles",
        ["default_outreach_template_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_email_tasks_outreach_template_id",
        "email_tasks",
        ["outreach_template_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_test_compose_sessions_outreach_template_id",
        "test_compose_sessions",
        ["outreach_template_id"],
        unique=False,
        if_not_exists=True,
    )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE email_tasks
            SET outreach_template_snapshot_version = 1
            WHERE outreach_template_snapshot_version IS NULL
              AND (
                    outreach_template_id IS NOT NULL
                    OR outreach_template_subject IS NOT NULL
                    OR outreach_template_body_text IS NOT NULL
                    OR outreach_template_body_html IS NOT NULL
                  )
            """
        )
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO outreach_templates (
                name,
                recommended_generation_mode,
                subject,
                body_text,
                body_html,
                is_default,
                migrated_from_identity_id,
                created_at,
                updated_at
            )
            SELECT
                COALESCE(
                    NULLIF(TRIM(identity_profiles.profile_name), ''),
                    NULLIF(TRIM(identity_profiles.name), ''),
                    '发件身份 ' || identity_profiles.id
                ) || ' · 原默认模板',
                COALESCE(identity_profiles.outreach_generation_mode, 'llm'),
                identity_profiles.outreach_template_subject,
                identity_profiles.outreach_template_body_text,
                identity_profiles.outreach_template_body_html,
                0,
                identity_profiles.id,
                identity_profiles.created_at,
                identity_profiles.updated_at
            FROM identity_profiles
            WHERE NOT EXISTS (
                  SELECT 1
                  FROM outreach_templates
                  WHERE outreach_templates.migrated_from_identity_id = identity_profiles.id
              )
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE identity_profiles
            SET default_outreach_template_id = (
                SELECT outreach_templates.id
                FROM outreach_templates
                WHERE outreach_templates.migrated_from_identity_id = identity_profiles.id
            )
            WHERE default_outreach_template_id IS NULL
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE outreach_templates
            SET is_default = 1
            WHERE id = (
                SELECT outreach_templates.id
                FROM outreach_templates
                JOIN identity_profiles
                  ON identity_profiles.id = outreach_templates.migrated_from_identity_id
                WHERE identity_profiles.is_default = 1
                ORDER BY identity_profiles.id
                LIMIT 1
            )
              AND NOT EXISTS (
                  SELECT 1 FROM outreach_templates WHERE is_default = 1
              )
            """
        )
    )
    _verify_legacy_template_backfill(connection)


def downgrade() -> None:
    connection = op.get_bind()
    unrepresentable_count = int(
        connection.scalar(
            sa.text(
                """
                SELECT COUNT(*)
                FROM outreach_templates
                WHERE migrated_from_identity_id IS NULL
                   OR NOT EXISTS (
                       SELECT 1
                       FROM identity_profiles
                       WHERE identity_profiles.id = outreach_templates.migrated_from_identity_id
                         AND identity_profiles.default_outreach_template_id = outreach_templates.id
                   )
                """
            )
        )
        or 0
    )
    if unrepresentable_count:
        raise RuntimeError(
            "cannot downgrade outreach template library without losing independent templates",
        )

    connection.execute(
        sa.text(
            """
            UPDATE identity_profiles
            SET outreach_generation_mode = COALESCE(
                    (
                        SELECT recommended_generation_mode
                        FROM outreach_templates
                        WHERE outreach_templates.id = identity_profiles.default_outreach_template_id
                    ),
                    outreach_generation_mode
                ),
                outreach_template_subject = (
                    SELECT subject
                    FROM outreach_templates
                    WHERE outreach_templates.id = identity_profiles.default_outreach_template_id
                ),
                outreach_template_body_text = (
                    SELECT body_text
                    FROM outreach_templates
                    WHERE outreach_templates.id = identity_profiles.default_outreach_template_id
                ),
                outreach_template_body_html = (
                    SELECT body_html
                    FROM outreach_templates
                    WHERE outreach_templates.id = identity_profiles.default_outreach_template_id
                )
            WHERE default_outreach_template_id IS NOT NULL
            """
        )
    )

    _drop_reference_column(
        "test_compose_sessions",
        "outreach_template_id",
        "ix_test_compose_sessions_outreach_template_id",
        "fk_test_compose_sessions_outreach_template_id_outreach_templates",
    )
    _drop_reference_column(
        "email_tasks",
        "outreach_template_id",
        "ix_email_tasks_outreach_template_id",
        "fk_email_tasks_outreach_template_id_outreach_templates",
    )
    _drop_reference_column(
        "identity_profiles",
        "default_outreach_template_id",
        "ix_identity_profiles_default_outreach_template_id",
        "fk_identity_profiles_default_outreach_template_id_outreach_templates",
    )
    _drop_plain_column("email_tasks", "outreach_template_snapshot_version")
    op.drop_index(
        "uq_outreach_templates_global_default",
        table_name="outreach_templates",
    )
    op.drop_table("outreach_templates")


def _ensure_reference_column(
    table_name: str,
    column_name: str,
    referred_table: str,
    constraint_name: str,
) -> None:
    needs_column = column_name not in _column_names(table_name)
    needs_foreign_key = not _has_foreign_key(table_name, column_name, referred_table)
    if not needs_column and not needs_foreign_key:
        return
    with op.batch_alter_table(table_name) as batch_op:
        if needs_column:
            batch_op.add_column(sa.Column(column_name, sa.Integer(), nullable=True))
        if needs_foreign_key:
            batch_op.create_foreign_key(
                constraint_name,
                referred_table,
                [column_name],
                ["id"],
                ondelete="SET NULL",
            )


def _drop_reference_column(
    table_name: str,
    column_name: str,
    index_name: str,
    constraint_name: str,
) -> None:
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.drop_index(index_name)
        batch_op.drop_constraint(constraint_name, type_="foreignkey")
        batch_op.drop_column(column_name)


def _ensure_plain_column(table_name: str, column: sa.Column) -> None:
    if column.name in _column_names(table_name):
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.add_column(column)


def _drop_plain_column(table_name: str, column_name: str) -> None:
    if column_name not in _column_names(table_name):
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.drop_column(column_name)


def _verify_legacy_template_backfill(connection: sa.Connection) -> None:
    expected_count = int(
        connection.scalar(
            sa.text("SELECT COUNT(*) FROM identity_profiles")
        )
        or 0
    )
    migrated_count = int(
        connection.scalar(
            sa.text(
                """
                SELECT COUNT(*)
                FROM identity_profiles
                JOIN outreach_templates
                  ON outreach_templates.migrated_from_identity_id = identity_profiles.id
                 AND identity_profiles.default_outreach_template_id = outreach_templates.id
                WHERE identity_profiles.default_outreach_template_id IS NOT NULL
                """
            )
        )
        or 0
    )
    if migrated_count != expected_count:
        raise RuntimeError(
            f"outreach template migration count mismatch: expected {expected_count}, got {migrated_count}",
        )

    mismatch = connection.execute(
        sa.text(
            """
            SELECT identity_profiles.id
            FROM identity_profiles
            JOIN outreach_templates
              ON outreach_templates.id = identity_profiles.default_outreach_template_id
            WHERE (
                  NOT (
                      outreach_templates.recommended_generation_mode
                      IS identity_profiles.outreach_generation_mode
                  )
                  OR NOT (
                      outreach_templates.subject
                      IS identity_profiles.outreach_template_subject
                  )
                  OR NOT (
                      outreach_templates.body_text
                      IS identity_profiles.outreach_template_body_text
                  )
                  OR NOT (
                      outreach_templates.body_html
                      IS identity_profiles.outreach_template_body_html
                  )
              )
            LIMIT 1
            """
        )
    ).fetchone()
    if mismatch is not None:
        raise RuntimeError(
            f"outreach template migration content mismatch for identity {mismatch[0]}",
        )


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
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
