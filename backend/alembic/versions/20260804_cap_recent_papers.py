"""cap stored recent papers at eight items

Revision ID: 20260804_cap_recent_papers
Revises: 20260803_crawl_run_app_version
Create Date: 2026-08-04 00:00:00.000000
"""

from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260804_cap_recent_papers"
down_revision: Union[str, Sequence[str], None] = "20260803_crawl_run_app_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RECENT_PAPERS_MAX_ITEMS = 8


def _decode_json_array(value: object) -> list[object] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, list) else None


def _truncate_recent_papers(table_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return
    if "recent_papers" not in {
        column["name"] for column in inspector.get_columns(table_name)
    }:
        return

    metadata = sa.MetaData()
    table = sa.Table(table_name, metadata, autoload_with=bind)
    rows = bind.execute(
        sa.select(
            table.c.id,
            sa.cast(table.c.recent_papers, sa.Text()).label("recent_papers_json"),
        ).where(table.c.recent_papers.is_not(None))
    ).mappings()
    for row in rows:
        papers = _decode_json_array(row["recent_papers_json"])
        if papers is None or len(papers) <= RECENT_PAPERS_MAX_ITEMS:
            continue
        bind.execute(
            sa.update(table)
            .where(table.c.id == row["id"])
            .values(recent_papers=papers[:RECENT_PAPERS_MAX_ITEMS])
        )


def upgrade() -> None:
    _truncate_recent_papers("professors")
    _truncate_recent_papers("crawl_candidates")


def downgrade() -> None:
    # Truncated values cannot be reconstructed safely.
    pass
