"""add match analysis jobs

Revision ID: a9c8e7d6f5b4
Revises: e5f1c2d3a4b6
Create Date: 2026-05-03 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9c8e7d6f5b4"
down_revision: Union[str, Sequence[str], None] = "e5f1c2d3a4b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "match_analysis_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("llm_profile_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column(
            "target_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "succeeded_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "failed_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "skipped_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "total_prompt_tokens",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "total_completion_tokens",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "total_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["identity_id"], ["identity_profiles.id"]),
        sa.ForeignKeyConstraint(["llm_profile_id"], ["llm_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_match_analysis_jobs_identity_id",
        "match_analysis_jobs",
        ["identity_id"],
    )
    op.create_index(
        "ix_match_analysis_jobs_llm_profile_id",
        "match_analysis_jobs",
        ["llm_profile_id"],
    )
    op.create_index("ix_match_analysis_jobs_status", "match_analysis_jobs", ["status"])

    op.create_table(
        "match_analysis_job_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("professor_id", sa.Integer(), nullable=False),
        sa.Column("email_task_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("match_analysis_run_id", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column(
            "prompt_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "completion_tokens",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "total_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["email_task_id"], ["email_tasks.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["match_analysis_jobs.id"]),
        sa.ForeignKeyConstraint(["match_analysis_run_id"], ["match_analysis_runs.id"]),
        sa.ForeignKeyConstraint(["professor_id"], ["professors.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_match_analysis_job_items_email_task_id",
        "match_analysis_job_items",
        ["email_task_id"],
    )
    op.create_index(
        "ix_match_analysis_job_items_job_id",
        "match_analysis_job_items",
        ["job_id"],
    )
    op.create_index(
        "ix_match_analysis_job_items_match_analysis_run_id",
        "match_analysis_job_items",
        ["match_analysis_run_id"],
    )
    op.create_index(
        "ix_match_analysis_job_items_professor_id",
        "match_analysis_job_items",
        ["professor_id"],
    )
    op.create_index(
        "ix_match_analysis_job_items_status",
        "match_analysis_job_items",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_match_analysis_job_items_status", table_name="match_analysis_job_items"
    )
    op.drop_index(
        "ix_match_analysis_job_items_professor_id",
        table_name="match_analysis_job_items",
    )
    op.drop_index(
        "ix_match_analysis_job_items_match_analysis_run_id",
        table_name="match_analysis_job_items",
    )
    op.drop_index(
        "ix_match_analysis_job_items_job_id", table_name="match_analysis_job_items"
    )
    op.drop_index(
        "ix_match_analysis_job_items_email_task_id",
        table_name="match_analysis_job_items",
    )
    op.drop_table("match_analysis_job_items")
    op.drop_index("ix_match_analysis_jobs_status", table_name="match_analysis_jobs")
    op.drop_index(
        "ix_match_analysis_jobs_llm_profile_id", table_name="match_analysis_jobs"
    )
    op.drop_index(
        "ix_match_analysis_jobs_identity_id", table_name="match_analysis_jobs"
    )
    op.drop_table("match_analysis_jobs")
