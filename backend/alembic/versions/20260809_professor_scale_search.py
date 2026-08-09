"""add professor scale indexes and SQLite full-text search

Revision ID: 20260809_professor_scale_search
Revises: 20260808_crawl_llm_snapshot
Create Date: 2026-08-09 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "20260809_professor_scale_search"
down_revision: str | Sequence[str] | None = "20260808_crawl_llm_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FTS_COLUMNS = (
    "name",
    "email",
    "university",
    "school",
    "department",
    "title",
    "research_direction",
    "personal_note",
)


def _create_sqlite_professor_fts() -> None:
    columns = ", ".join(FTS_COLUMNS)
    new_columns = ", ".join(f"new.{column}" for column in FTS_COLUMNS)
    old_columns = ", ".join(f"old.{column}" for column in FTS_COLUMNS)
    op.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS professors_fts USING fts5(
            {columns},
            content='professors',
            content_rowid='id',
            tokenize='trigram'
        )
        """,
    )
    op.execute("INSERT INTO professors_fts(professors_fts) VALUES ('rebuild')")
    op.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS professors_fts_ai
        AFTER INSERT ON professors
        BEGIN
            INSERT INTO professors_fts(rowid, {columns})
            VALUES (new.id, {new_columns});
        END
        """,
    )
    op.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS professors_fts_ad
        AFTER DELETE ON professors
        BEGIN
            INSERT INTO professors_fts(professors_fts, rowid, {columns})
            VALUES ('delete', old.id, {old_columns});
        END
        """,
    )
    op.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS professors_fts_au
        AFTER UPDATE OF {columns} ON professors
        BEGIN
            INSERT INTO professors_fts(professors_fts, rowid, {columns})
            VALUES ('delete', old.id, {old_columns});
            INSERT INTO professors_fts(rowid, {columns})
            VALUES (new.id, {new_columns});
        END
        """,
    )


def upgrade() -> None:
    op.create_index(
        "ix_professors_archived_updated_id",
        "professors",
        ["archived_at", "updated_at", "id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_professors_archived_name_id",
        "professors",
        ["archived_at", "name", "id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_professors_archived_university_name_id",
        "professors",
        ["archived_at", "university", "name", "id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_professor_tag_links_tag_professor",
        "professor_tag_links",
        ["tag_id", "professor_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_email_tasks_identity_root_active_status",
        "email_tasks",
        [
            "identity_id",
            "parent_task_id",
            "batch_send_canceled_at",
            "status",
            "professor_id",
        ],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_email_tasks_identity_status_scheduled_professor",
        "email_tasks",
        ["identity_id", "status", "scheduled_at", "professor_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_email_logs_identity_direction_professor_created",
        "email_logs",
        ["identity_id", "direction", "professor_id", "created_at", "id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_identity_professor_match_results_identity_score_professor",
        "identity_professor_match_results",
        ["identity_id", "match_score", "professor_id"],
        unique=False,
        if_not_exists=True,
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_professors_archived_trimmed_hierarchy "
            "ON professors(archived_at, trim(university), trim(school), "
            "trim(department))",
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_professors_archived_trimmed_title "
            "ON professors(archived_at, trim(title))",
        )
        _create_sqlite_professor_fts()


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS professors_fts_au")
        op.execute("DROP TRIGGER IF EXISTS professors_fts_ad")
        op.execute("DROP TRIGGER IF EXISTS professors_fts_ai")
        op.execute("DROP TABLE IF EXISTS professors_fts")
        op.execute("DROP INDEX IF EXISTS ix_professors_archived_trimmed_title")
        op.execute("DROP INDEX IF EXISTS ix_professors_archived_trimmed_hierarchy")
    op.drop_index(
        "ix_identity_professor_match_results_identity_score_professor",
        table_name="identity_professor_match_results",
        if_exists=True,
    )
    op.drop_index(
        "ix_email_logs_identity_direction_professor_created",
        table_name="email_logs",
        if_exists=True,
    )
    op.drop_index(
        "ix_email_tasks_identity_status_scheduled_professor",
        table_name="email_tasks",
        if_exists=True,
    )
    op.drop_index(
        "ix_email_tasks_identity_root_active_status",
        table_name="email_tasks",
        if_exists=True,
    )
    op.drop_index(
        "ix_professor_tag_links_tag_professor",
        table_name="professor_tag_links",
        if_exists=True,
    )
    op.drop_index(
        "ix_professors_archived_university_name_id",
        table_name="professors",
        if_exists=True,
    )
    op.drop_index(
        "ix_professors_archived_name_id",
        table_name="professors",
        if_exists=True,
    )
    op.drop_index(
        "ix_professors_archived_updated_id",
        table_name="professors",
        if_exists=True,
    )
