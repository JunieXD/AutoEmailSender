from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e8f7a6b5c4d3"
down_revision = "e8f2a4b6c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in ("batch_tasks", "crawl_jobs", "match_analysis_jobs"):
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
            )
            batch_op.create_index(
                batch_op.f(f"ix_{table_name}_deleted_at"), ["deleted_at"], unique=False
            )


def downgrade() -> None:
    for table_name in ("match_analysis_jobs", "crawl_jobs", "batch_tasks"):
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.drop_index(batch_op.f(f"ix_{table_name}_deleted_at"))
            batch_op.drop_column("deleted_at")
