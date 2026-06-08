"""add crawl job runs

Revision ID: f2a7c9d8e1b3
Revises: 6d7e8f9a0b12
Create Date: 2026-04-29 00:00:00.000000
"""

from __future__ import annotations

from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "f2a7c9d8e1b3"
down_revision = "6d7e8f9a0b12"
branch_labels = None
depends_on = None


def _table_exists(connection: sa.engine.Connection, table_name: str) -> bool:
    return bool(
        connection.scalar(
            sa.text(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = :table_name
                """
            ),
            {"table_name": table_name},
        )
    )


def _index_exists(connection: sa.engine.Connection, index_name: str) -> bool:
    return bool(
        connection.scalar(
            sa.text(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'index' AND name = :index_name
                """
            ),
            {"index_name": index_name},
        )
    )


def _column_exists(connection: sa.engine.Connection, table_name: str, column_name: str) -> bool:
    rows = connection.execute(sa.text(f"PRAGMA table_info({table_name})")).mappings()
    return any(row["name"] == column_name for row in rows)


def upgrade() -> None:
    connection = op.get_bind()
    if not _table_exists(connection, "crawl_job_runs"):
        op.create_table(
            "crawl_job_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("job_id", sa.Integer(), nullable=False),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=64), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("active_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("active_seconds", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("total_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
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
                name=op.f("fk_crawl_job_runs_job_id_crawl_jobs"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_job_runs")),
            sa.UniqueConstraint("job_id", "attempt_number", name=op.f("uq_crawl_job_runs_job_attempt")),
        )
    if not _index_exists(connection, op.f("ix_crawl_job_runs_job_id")):
        op.create_index(op.f("ix_crawl_job_runs_job_id"), "crawl_job_runs", ["job_id"], unique=False)
    if not _index_exists(connection, op.f("ix_crawl_job_runs_status")):
        op.create_index(op.f("ix_crawl_job_runs_status"), "crawl_job_runs", ["status"], unique=False)

    if not _column_exists(connection, "crawl_jobs", "current_run_id"):
        with op.batch_alter_table("crawl_jobs") as batch_op:
            batch_op.add_column(sa.Column("current_run_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                batch_op.f("fk_crawl_jobs_current_run_id_crawl_job_runs"),
                "crawl_job_runs",
                ["current_run_id"],
                ["id"],
                ondelete="SET NULL",
            )

    jobs = connection.execute(
        sa.text(
            """
            SELECT id, status, created_at, updated_at, agent_trace, current_run_id
            FROM crawl_jobs
            ORDER BY id
            """
        )
    ).mappings()
    for job in jobs:
        existing_run_id = connection.scalar(
            sa.text(
                """
                SELECT id
                FROM crawl_job_runs
                WHERE job_id = :job_id AND attempt_number = 1
                """
            ),
            {"job_id": job["id"]},
        )
        if existing_run_id is not None:
            run_id = existing_run_id
            if job["current_run_id"] is None:
                connection.execute(
                    sa.text("UPDATE crawl_jobs SET current_run_id = :run_id WHERE id = :job_id"),
                    {"run_id": run_id, "job_id": job["id"]},
                )
            continue
        active_seconds = _legacy_active_seconds(job["created_at"], job["updated_at"])
        tokens = _legacy_token_totals(job["agent_trace"])
        result = connection.execute(
            sa.text(
                """
                INSERT INTO crawl_job_runs (
                    job_id, attempt_number, status, finished_at, active_seconds,
                    input_tokens, output_tokens, total_tokens, created_at, updated_at
                )
                VALUES (
                    :job_id, 1, :status, :finished_at, :active_seconds,
                    :input_tokens, :output_tokens, :total_tokens, :created_at, :updated_at
                )
                """
            ),
            {
                "job_id": job["id"],
                "status": job["status"],
                "finished_at": job["updated_at"]
                if job["status"] in {"needs_review", "completed", "failed", "canceled"}
                else None,
                "active_seconds": active_seconds,
                "input_tokens": tokens[0],
                "output_tokens": tokens[1],
                "total_tokens": tokens[2],
                "created_at": job["created_at"],
                "updated_at": job["updated_at"],
            },
        )
        run_id = result.lastrowid
        connection.execute(
            sa.text("UPDATE crawl_jobs SET current_run_id = :run_id WHERE id = :job_id"),
            {"run_id": run_id, "job_id": job["id"]},
        )


def downgrade() -> None:
    with op.batch_alter_table("crawl_jobs") as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_crawl_jobs_current_run_id_crawl_job_runs"),
            type_="foreignkey",
        )
        batch_op.drop_column("current_run_id")

    op.drop_index(op.f("ix_crawl_job_runs_status"), table_name="crawl_job_runs")
    op.drop_index(op.f("ix_crawl_job_runs_job_id"), table_name="crawl_job_runs")
    op.drop_table("crawl_job_runs")


def _legacy_active_seconds(created_at: object, updated_at: object) -> int:
    parsed_created_at = _parse_datetime(created_at)
    parsed_updated_at = _parse_datetime(updated_at)
    if parsed_created_at is None or parsed_updated_at is None:
        return 0
    return max(0, int((parsed_updated_at - parsed_created_at).total_seconds()))


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    for candidate in (value, value.replace(" ", "T", 1)):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _legacy_token_totals(agent_trace: object) -> tuple[int, int, int]:
    import json
    import re

    if not agent_trace:
        return (0, 0, 0)
    if isinstance(agent_trace, str):
        try:
            trace = json.loads(agent_trace)
        except json.JSONDecodeError:
            return (0, 0, 0)
    else:
        trace = agent_trace
    if not isinstance(trace, list):
        return (0, 0, 0)

    patterns = (
        re.compile(
            r"usage_metadata=\{'input_tokens':\s*(?P<input>\d+),\s*"
            r"'output_tokens':\s*(?P<output>\d+),\s*'total_tokens':\s*(?P<total>\d+)"
        ),
        re.compile(
            r"'token_usage':\s*\{'completion_tokens':\s*(?P<output>\d+),\s*"
            r"'prompt_tokens':\s*(?P<input>\d+),\s*'total_tokens':\s*(?P<total>\d+)"
        ),
    )
    totals = [0, 0, 0]
    for event in trace:
        haystack = str(event)
        for pattern in patterns:
            match = pattern.search(haystack)
            if match:
                totals[0] += int(match.group("input"))
                totals[1] += int(match.group("output"))
                totals[2] += int(match.group("total"))
                break
    return (totals[0], totals[1], totals[2])
