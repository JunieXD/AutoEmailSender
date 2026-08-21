"""add cached token summaries to match analysis jobs

Revision ID: 20260721_match_analysis_cache
Revises: 20260721_professor_enrichment
Create Date: 2026-07-21 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260721_match_analysis_cache"
down_revision: Union[str, Sequence[str], None] = "20260721_professor_enrichment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "cached_tokens" not in _column_names("match_analysis_job_items"):
        op.add_column(
            "match_analysis_job_items",
            sa.Column(
                "cached_tokens",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )
    if "total_cached_tokens" not in _column_names("match_analysis_jobs"):
        op.add_column(
            "match_analysis_jobs",
            sa.Column(
                "total_cached_tokens",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE match_analysis_job_items
            SET cached_tokens = COALESCE(
                (
                    SELECT match_analysis_runs.cached_tokens
                    FROM match_analysis_runs
                    WHERE match_analysis_runs.id = match_analysis_job_items.match_analysis_run_id
                ),
                0
            )
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE match_analysis_jobs
            SET total_cached_tokens = COALESCE(
                (
                    SELECT SUM(match_analysis_job_items.cached_tokens)
                    FROM match_analysis_job_items
                    WHERE match_analysis_job_items.job_id = match_analysis_jobs.id
                ),
                0
            )
            """
        )
    )


def downgrade() -> None:
    if "total_cached_tokens" in _column_names("match_analysis_jobs"):
        op.drop_column("match_analysis_jobs", "total_cached_tokens")
    if "cached_tokens" in _column_names("match_analysis_job_items"):
        op.drop_column("match_analysis_job_items", "cached_tokens")


def _column_names(table_name: str) -> set[str]:
    return {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }
