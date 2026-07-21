"""add professor information enrichment task metadata

Revision ID: 20260721_professor_enrichment
Revises: 20260719_professor_history_queue
Create Date: 2026-07-21 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260721_professor_enrichment"
down_revision: Union[str, Sequence[str], None] = "20260719_professor_history_queue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ACTIVE_ENRICHMENT_STATUSES = "('pending', 'processing', 'failed_retryable')"


def upgrade() -> None:
    crawl_job_columns = _column_names("crawl_jobs")
    additions = (
        (
            "job_kind",
            sa.Column(
                "job_kind",
                sa.String(length=32),
                nullable=False,
                server_default="faculty_crawl",
            ),
        ),
        (
            "trigger_mode",
            sa.Column(
                "trigger_mode",
                sa.String(length=32),
                nullable=False,
                server_default="crawl",
            ),
        ),
        (
            "task_center_visible",
            sa.Column(
                "task_center_visible",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        ),
        ("display_name", sa.Column("display_name", sa.String(length=255), nullable=True)),
    )
    for name, column in additions:
        if name not in crawl_job_columns:
            op.add_column("crawl_jobs", column)

    op.create_index(
        "ix_crawl_jobs_job_kind",
        "crawl_jobs",
        ["job_kind"],
        unique=False,
        if_not_exists=True,
    )

    task_columns = _column_names("crawl_candidate_enrichment_tasks")
    task_additions = (
        (
            "professor_id",
            sa.Column(
                "professor_id",
                sa.Integer(),
                sa.ForeignKey("professors.id", ondelete="SET NULL"),
                nullable=True,
            ),
        ),
        ("skip_reason", sa.Column("skip_reason", sa.Text(), nullable=True)),
        ("enriched_fields", sa.Column("enriched_fields", sa.JSON(), nullable=True)),
        ("started_at", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)),
        ("finished_at", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True)),
    )
    with op.batch_alter_table("crawl_candidate_enrichment_tasks") as batch_op:
        for name, column in task_additions:
            if name not in task_columns:
                batch_op.add_column(column)

    op.create_index(
        "ix_crawl_candidate_enrichment_tasks_professor_id",
        "crawl_candidate_enrichment_tasks",
        ["professor_id"],
        unique=False,
        if_not_exists=True,
    )
    active_where = sa.text(
        f"professor_id IS NOT NULL AND status IN {ACTIVE_ENRICHMENT_STATUSES}",
    )
    op.create_index(
        "uq_crawl_candidate_enrichment_tasks_active_professor",
        "crawl_candidate_enrichment_tasks",
        ["professor_id"],
        unique=True,
        sqlite_where=active_where,
        postgresql_where=active_where,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_crawl_candidate_enrichment_tasks_active_professor",
        table_name="crawl_candidate_enrichment_tasks",
        if_exists=True,
    )
    op.drop_index(
        "ix_crawl_candidate_enrichment_tasks_professor_id",
        table_name="crawl_candidate_enrichment_tasks",
        if_exists=True,
    )
    task_columns = _column_names("crawl_candidate_enrichment_tasks")
    with op.batch_alter_table("crawl_candidate_enrichment_tasks") as batch_op:
        for name in (
            "finished_at",
            "started_at",
            "enriched_fields",
            "skip_reason",
            "professor_id",
        ):
            if name in task_columns:
                batch_op.drop_column(name)
                task_columns.remove(name)

    op.drop_index("ix_crawl_jobs_job_kind", table_name="crawl_jobs", if_exists=True)
    crawl_job_columns = _column_names("crawl_jobs")
    for name in ("display_name", "task_center_visible", "trigger_mode", "job_kind"):
        if name in crawl_job_columns:
            op.drop_column("crawl_jobs", name)
            crawl_job_columns.remove(name)


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}
