"""drop obsolete crawler v1 state

Revision ID: 20260807_drop_crawler_runtime
Revises: 20260807_candidate_identity
Create Date: 2026-08-07 00:00:01.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260807_drop_crawler_runtime"
down_revision: Union[str, Sequence[str], None] = "20260807_candidate_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    drop_runtime_index = _index_exists("crawl_jobs", "ix_crawl_jobs_runtime_version")
    drop_runtime_column = _column_exists("crawl_jobs", "runtime_version")
    if drop_runtime_index or drop_runtime_column:
        with op.batch_alter_table("crawl_jobs", schema=None) as batch_op:
            if drop_runtime_index:
                batch_op.drop_index(batch_op.f("ix_crawl_jobs_runtime_version"))
            if drop_runtime_column:
                batch_op.drop_column("runtime_version")

    if _column_exists("app_settings", "crawler_agent_max_chunks_per_run"):
        with op.batch_alter_table("app_settings", schema=None) as batch_op:
            batch_op.drop_column("crawler_agent_max_chunks_per_run")


def downgrade() -> None:
    if not _column_exists("app_settings", "crawler_agent_max_chunks_per_run"):
        with op.batch_alter_table("app_settings", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "crawler_agent_max_chunks_per_run",
                    sa.Integer(),
                    server_default=sa.text("2"),
                    nullable=False,
                )
            )

    add_runtime_column = not _column_exists("crawl_jobs", "runtime_version")
    add_runtime_index = not _index_exists("crawl_jobs", "ix_crawl_jobs_runtime_version")
    if add_runtime_column or add_runtime_index:
        with op.batch_alter_table("crawl_jobs", schema=None) as batch_op:
            if add_runtime_column:
                batch_op.add_column(
                    sa.Column(
                        "runtime_version",
                        sa.String(length=16),
                        server_default=sa.text("'v2'"),
                        nullable=False,
                    )
                )
            if add_runtime_index:
                batch_op.create_index(
                    batch_op.f("ix_crawl_jobs_runtime_version"),
                    ["runtime_version"],
                    unique=False,
                )


def _column_exists(table_name: str, column_name: str) -> bool:
    return any(
        column["name"] == column_name
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    )


def _index_exists(table_name: str, index_name: str) -> bool:
    return any(
        index["name"] == index_name
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
    )
