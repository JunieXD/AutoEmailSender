"""add crawl jobs

Revision ID: 7b9c2d4e6f10
Revises: 4c1a2b3d4e5f
Create Date: 2026-04-26 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7b9c2d4e6f10"
down_revision: Union[str, Sequence[str], None] = "4c1a2b3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "crawl_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("university", sa.String(length=255), nullable=False),
        sa.Column("school", sa.String(length=255), nullable=False),
        sa.Column("start_url", sa.String(length=1000), nullable=False),
        sa.Column("llm_profile_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=64),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column(
            "progress_current",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "progress_total", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("agent_trace", sa.JSON(), nullable=True),
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
            ["llm_profile_id"],
            ["llm_profiles.id"],
            name=op.f("fk_crawl_jobs_llm_profile_id_llm_profiles"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_jobs")),
    )
    op.create_index(
        op.f("ix_crawl_jobs_status"), "crawl_jobs", ["status"], unique=False
    )

    op.create_table(
        "crawl_pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("parent_url", sa.String(length=1000), nullable=True),
        sa.Column("fetch_method", sa.String(length=64), nullable=False),
        sa.Column(
            "page_type",
            sa.String(length=64),
            server_default=sa.text("'unknown'"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("text_excerpt", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["crawl_jobs.id"],
            name=op.f("fk_crawl_pages_job_id_crawl_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_pages")),
    )
    op.create_index(
        op.f("ix_crawl_pages_job_id"), "crawl_pages", ["job_id"], unique=False
    )

    op.create_table(
        "crawl_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("professor_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("university", sa.String(length=255), nullable=True),
        sa.Column("school", sa.String(length=255), nullable=True),
        sa.Column("department", sa.String(length=255), nullable=True),
        sa.Column("research_direction", sa.Text(), nullable=True),
        sa.Column("recent_papers", sa.JSON(), nullable=True),
        sa.Column("profile_url", sa.String(length=1000), nullable=True),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column(
            "confidence", sa.Float(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("field_confidence", sa.JSON(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column(
            "review_status",
            sa.String(length=64),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
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
            name=op.f("fk_crawl_candidates_job_id_crawl_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["professor_id"],
            ["professors.id"],
            name=op.f("fk_crawl_candidates_professor_id_professors"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_candidates")),
    )
    op.create_index(
        op.f("ix_crawl_candidates_email"), "crawl_candidates", ["email"], unique=False
    )
    op.create_index(
        op.f("ix_crawl_candidates_job_id"), "crawl_candidates", ["job_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_crawl_candidates_job_id"), table_name="crawl_candidates")
    op.drop_index(op.f("ix_crawl_candidates_email"), table_name="crawl_candidates")
    op.drop_table("crawl_candidates")

    op.drop_index(op.f("ix_crawl_pages_job_id"), table_name="crawl_pages")
    op.drop_table("crawl_pages")

    op.drop_index(op.f("ix_crawl_jobs_status"), table_name="crawl_jobs")
    op.drop_table("crawl_jobs")
