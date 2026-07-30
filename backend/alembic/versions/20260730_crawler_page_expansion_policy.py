"""add crawler page expansion policy metadata

Revision ID: 20260730_crawler_expansion
Revises: 20260730_merge_llm_templates
Create Date: 2026-07-30 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260730_crawler_expansion"
down_revision: Union[str, Sequence[str], None] = "20260730_merge_llm_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "parent_url" not in _column_names():
        with op.batch_alter_table("crawl_page_tasks") as batch_op:
            batch_op.add_column(sa.Column("parent_url", sa.String(length=1000), nullable=True))
    if "discovery_reason" not in _column_names():
        with op.batch_alter_table("crawl_page_tasks") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "discovery_reason",
                    sa.String(length=64),
                    server_default=sa.text("'start'"),
                    nullable=False,
                )
            )
    if "expansion_mode" not in _column_names():
        with op.batch_alter_table("crawl_page_tasks") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "expansion_mode",
                    sa.String(length=64),
                    server_default=sa.text("'entry'"),
                    nullable=False,
                )
            )
    if "allow_expansion" not in _column_names():
        with op.batch_alter_table("crawl_page_tasks") as batch_op:
            batch_op.add_column(sa.Column("allow_expansion", sa.Boolean(), nullable=True))


def downgrade() -> None:
    if "allow_expansion" in _column_names():
        with op.batch_alter_table("crawl_page_tasks") as batch_op:
            batch_op.drop_column("allow_expansion")
    if "expansion_mode" in _column_names():
        with op.batch_alter_table("crawl_page_tasks") as batch_op:
            batch_op.drop_column("expansion_mode")
    if "discovery_reason" in _column_names():
        with op.batch_alter_table("crawl_page_tasks") as batch_op:
            batch_op.drop_column("discovery_reason")
    if "parent_url" in _column_names():
        with op.batch_alter_table("crawl_page_tasks") as batch_op:
            batch_op.drop_column("parent_url")


def _column_names() -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("crawl_page_tasks")
    }
