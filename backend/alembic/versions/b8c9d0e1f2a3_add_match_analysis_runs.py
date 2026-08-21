"""add match analysis runs

Revision ID: b8c9d0e1f2a3
Revises: a4b6c8d0e2f1
Create Date: 2026-04-29 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a4b6c8d0e2f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "match_analysis_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email_task_id", sa.Integer(), nullable=False),
        sa.Column("professor_id", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("llm_profile_id", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("match_score", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("endpoint_kind", sa.String(length=50), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("stable_prefix_hash", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["email_task_id"], ["email_tasks.id"]),
        sa.ForeignKeyConstraint(["professor_id"], ["professors.id"]),
        sa.ForeignKeyConstraint(["identity_id"], ["identity_profiles.id"]),
        sa.ForeignKeyConstraint(["llm_profile_id"], ["llm_profiles.id"]),
    )
    op.create_index(
        "ix_match_analysis_runs_email_task_id",
        "match_analysis_runs",
        ["email_task_id"],
    )
    op.create_index(
        "ix_match_analysis_runs_professor_id",
        "match_analysis_runs",
        ["professor_id"],
    )
    op.create_index(
        "ix_match_analysis_runs_identity_id",
        "match_analysis_runs",
        ["identity_id"],
    )
    op.create_index(
        "ix_match_analysis_runs_llm_profile_id",
        "match_analysis_runs",
        ["llm_profile_id"],
    )
    op.create_index(
        "ix_match_analysis_runs_created_at",
        "match_analysis_runs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_match_analysis_runs_created_at", table_name="match_analysis_runs")
    op.drop_index(
        "ix_match_analysis_runs_llm_profile_id", table_name="match_analysis_runs"
    )
    op.drop_index(
        "ix_match_analysis_runs_identity_id", table_name="match_analysis_runs"
    )
    op.drop_index(
        "ix_match_analysis_runs_professor_id", table_name="match_analysis_runs"
    )
    op.drop_index(
        "ix_match_analysis_runs_email_task_id", table_name="match_analysis_runs"
    )
    op.drop_table("match_analysis_runs")
