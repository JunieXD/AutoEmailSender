"""add crawler runtime v2 tables

Revision ID: b2e7c9f1a4d6
Revises: a9c3e7d1f4b2
Create Date: 2026-05-30 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b2e7c9f1a4d6"
down_revision: Union[str, Sequence[str], None] = "a9c3e7d1f4b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("crawl_jobs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "runtime_version",
                sa.String(length=16),
                server_default=sa.text("'v1'"),
                nullable=False,
            )
        )
        batch_op.create_index(
            batch_op.f("ix_crawl_jobs_runtime_version"),
            ["runtime_version"],
            unique=False,
        )

    with op.batch_alter_table("crawl_jobs") as batch_op:
        batch_op.alter_column("runtime_version", server_default=sa.text("'v2'"))

    with op.batch_alter_table("crawl_page_fetch_states") as batch_op:
        batch_op.add_column(
            sa.Column("fetch_mode", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("direct_status", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("fallback_reason", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column("browser_status", sa.String(length=64), nullable=True)
        )

    with op.batch_alter_table("crawl_page_chunks") as batch_op:
        batch_op.add_column(
            sa.Column("worker_id", sa.String(length=128), nullable=True)
        )
        batch_op.add_column(
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_crawl_page_chunks_worker_id"), ["worker_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_crawl_page_chunks_lease_expires_at"),
            ["lease_expires_at"],
            unique=False,
        )

    op.create_table(
        "crawl_page_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("normalized_url", sa.String(length=1000), nullable=False),
        sa.Column("original_url", sa.String(length=1000), nullable=False),
        sa.Column("depth", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "priority", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "status",
            sa.String(length=64),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("fetch_mode", sa.String(length=64), nullable=True),
        sa.Column("direct_status", sa.String(length=64), nullable=True),
        sa.Column("fallback_reason", sa.String(length=255), nullable=True),
        sa.Column("browser_status", sa.String(length=64), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["crawl_jobs.id"],
            name=op.f("fk_crawl_page_tasks_job_id_crawl_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_page_tasks")),
        sa.UniqueConstraint(
            "job_id", "normalized_url", name=op.f("uq_crawl_page_tasks_job_url")
        ),
    )
    op.create_index(
        op.f("ix_crawl_page_tasks_job_id"), "crawl_page_tasks", ["job_id"], unique=False
    )
    op.create_index(
        op.f("ix_crawl_page_tasks_status"), "crawl_page_tasks", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_crawl_page_tasks_worker_id"),
        "crawl_page_tasks",
        ["worker_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_crawl_page_tasks_lease_expires_at"),
        "crawl_page_tasks",
        ["lease_expires_at"],
        unique=False,
    )

    op.create_table(
        "crawl_candidate_enrichment_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=64),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["crawl_candidates.id"],
            name=op.f(
                "fk_crawl_candidate_enrichment_tasks_candidate_id_crawl_candidates"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["crawl_jobs.id"],
            name=op.f("fk_crawl_candidate_enrichment_tasks_job_id_crawl_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_candidate_enrichment_tasks")),
        sa.UniqueConstraint(
            "job_id",
            "candidate_id",
            name=op.f("uq_crawl_candidate_enrichment_tasks_job_candidate"),
        ),
    )
    op.create_index(
        op.f("ix_crawl_candidate_enrichment_tasks_job_id"),
        "crawl_candidate_enrichment_tasks",
        ["job_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_crawl_candidate_enrichment_tasks_candidate_id"),
        "crawl_candidate_enrichment_tasks",
        ["candidate_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_crawl_candidate_enrichment_tasks_status"),
        "crawl_candidate_enrichment_tasks",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_crawl_candidate_enrichment_tasks_worker_id"),
        "crawl_candidate_enrichment_tasks",
        ["worker_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_crawl_candidate_enrichment_tasks_lease_expires_at"),
        "crawl_candidate_enrichment_tasks",
        ["lease_expires_at"],
        unique=False,
    )

    op.create_table(
        "crawl_worker_token_usages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("worker_kind", sa.String(length=32), nullable=False),
        sa.Column("work_item_id", sa.String(length=128), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column(
            "input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "cached_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("raw_usage", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["crawl_jobs.id"],
            name=op.f("fk_crawl_worker_token_usages_job_id_crawl_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_worker_token_usages")),
    )
    op.create_index(
        op.f("ix_crawl_worker_token_usages_job_id"),
        "crawl_worker_token_usages",
        ["job_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_crawl_worker_token_usages_worker_kind"),
        "crawl_worker_token_usages",
        ["worker_kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_crawl_worker_token_usages_work_item_id"),
        "crawl_worker_token_usages",
        ["work_item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_crawl_worker_token_usages_work_item_id"),
        table_name="crawl_worker_token_usages",
    )
    op.drop_index(
        op.f("ix_crawl_worker_token_usages_worker_kind"),
        table_name="crawl_worker_token_usages",
    )
    op.drop_index(
        op.f("ix_crawl_worker_token_usages_job_id"),
        table_name="crawl_worker_token_usages",
    )
    op.drop_table("crawl_worker_token_usages")
    op.drop_index(
        op.f("ix_crawl_candidate_enrichment_tasks_lease_expires_at"),
        table_name="crawl_candidate_enrichment_tasks",
    )
    op.drop_index(
        op.f("ix_crawl_candidate_enrichment_tasks_worker_id"),
        table_name="crawl_candidate_enrichment_tasks",
    )
    op.drop_index(
        op.f("ix_crawl_candidate_enrichment_tasks_status"),
        table_name="crawl_candidate_enrichment_tasks",
    )
    op.drop_index(
        op.f("ix_crawl_candidate_enrichment_tasks_candidate_id"),
        table_name="crawl_candidate_enrichment_tasks",
    )
    op.drop_index(
        op.f("ix_crawl_candidate_enrichment_tasks_job_id"),
        table_name="crawl_candidate_enrichment_tasks",
    )
    op.drop_table("crawl_candidate_enrichment_tasks")
    op.drop_index(
        op.f("ix_crawl_page_tasks_lease_expires_at"), table_name="crawl_page_tasks"
    )
    op.drop_index(op.f("ix_crawl_page_tasks_worker_id"), table_name="crawl_page_tasks")
    op.drop_index(op.f("ix_crawl_page_tasks_status"), table_name="crawl_page_tasks")
    op.drop_index(op.f("ix_crawl_page_tasks_job_id"), table_name="crawl_page_tasks")
    op.drop_table("crawl_page_tasks")
    with op.batch_alter_table("crawl_page_chunks") as batch_op:
        batch_op.drop_index(batch_op.f("ix_crawl_page_chunks_lease_expires_at"))
        batch_op.drop_index(batch_op.f("ix_crawl_page_chunks_worker_id"))
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("claimed_at")
        batch_op.drop_column("worker_id")
    with op.batch_alter_table("crawl_page_fetch_states") as batch_op:
        batch_op.drop_column("browser_status")
        batch_op.drop_column("fallback_reason")
        batch_op.drop_column("direct_status")
        batch_op.drop_column("fetch_mode")
    with op.batch_alter_table("crawl_jobs") as batch_op:
        batch_op.drop_index(batch_op.f("ix_crawl_jobs_runtime_version"))
        batch_op.drop_column("runtime_version")
