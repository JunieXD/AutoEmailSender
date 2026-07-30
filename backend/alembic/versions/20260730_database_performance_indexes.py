"""add low-risk database performance indexes

Revision ID: 20260730_db_performance
Revises: 20260730_crawler_expansion
Create Date: 2026-07-30 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260730_db_performance"
down_revision: Union[str, Sequence[str], None] = "20260730_crawler_expansion"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DISPATCH_READY_WHERE = "status = 'approved' OR status = 'scheduled'"
UNSTARTED_GENERATION_WHERE = (
    "status = 'generating_draft' AND draft_generation_started_at IS NULL"
)
STARTED_GENERATION_WHERE = (
    "status = 'generating_draft' AND draft_generation_started_at IS NOT NULL"
)
BATCH_SENT_WHERE = (
    "batch_task_id IS NOT NULL "
    "AND (status = 'sent' OR status = 'reply_detected')"
)


def upgrade() -> None:
    op.create_index(
        "ix_email_tasks_dispatch_ready",
        "email_tasks",
        ["approved_at", "created_at", "id"],
        unique=False,
        sqlite_where=sa.text(DISPATCH_READY_WHERE),
        postgresql_where=sa.text(DISPATCH_READY_WHERE),
        if_not_exists=True,
    )
    op.create_index(
        "ix_email_tasks_unstarted_generation_recovery",
        "email_tasks",
        ["updated_at"],
        unique=False,
        sqlite_where=sa.text(UNSTARTED_GENERATION_WHERE),
        postgresql_where=sa.text(UNSTARTED_GENERATION_WHERE),
        if_not_exists=True,
    )
    op.create_index(
        "ix_email_tasks_started_generation_recovery",
        "email_tasks",
        ["draft_generation_started_at"],
        unique=False,
        sqlite_where=sa.text(STARTED_GENERATION_WHERE),
        postgresql_where=sa.text(STARTED_GENERATION_WHERE),
        if_not_exists=True,
    )
    op.create_index(
        "ix_email_tasks_batch_sent_at",
        "email_tasks",
        ["batch_task_id", "sent_at"],
        unique=False,
        sqlite_where=sa.text(BATCH_SENT_WHERE),
        postgresql_where=sa.text(BATCH_SENT_WHERE),
        if_not_exists=True,
    )
    op.create_index(
        "ix_match_analysis_jobs_status_deleted_created_id",
        "match_analysis_jobs",
        ["status", "deleted_at", "created_at", "id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_crawl_jobs_kind_deleted_created_id",
        "crawl_jobs",
        ["job_kind", "deleted_at", "created_at", "id"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_crawl_jobs_kind_deleted_created_id",
        table_name="crawl_jobs",
        if_exists=True,
    )
    op.drop_index(
        "ix_match_analysis_jobs_status_deleted_created_id",
        table_name="match_analysis_jobs",
        if_exists=True,
    )
    op.drop_index(
        "ix_email_tasks_batch_sent_at",
        table_name="email_tasks",
        if_exists=True,
    )
    op.drop_index(
        "ix_email_tasks_started_generation_recovery",
        table_name="email_tasks",
        if_exists=True,
    )
    op.drop_index(
        "ix_email_tasks_unstarted_generation_recovery",
        table_name="email_tasks",
        if_exists=True,
    )
    op.drop_index(
        "ix_email_tasks_dispatch_ready",
        table_name="email_tasks",
        if_exists=True,
    )
