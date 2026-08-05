"""extract canonical identity-professor match results

Revision ID: 20260805_identity_match_results
Revises: 20260804_merge_agent_change_recent_papers
Create Date: 2026-08-05 00:00:00.000000
"""

from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260805_identity_match_results"
down_revision: Union[str, Sequence[str], None] = (
    "20260804_merge_agent_change_recent_papers"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    _create_match_results_table()
    _add_email_task_match_source()
    _add_group_match_source()
    _add_job_match_source()
    _add_match_analysis_result_details()
    _deduplicate_running_identity_professor_analyses()
    op.create_index(
        "uq_match_analysis_runs_running_per_identity_professor",
        "match_analysis_runs",
        ["identity_id", "professor_id"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
        postgresql_where=sa.text("status = 'running'"),
        if_not_exists=True,
    )
    _backfill_match_results()


def downgrade() -> None:
    if "identity_professor_match_results" in _table_names():
        op.drop_table("identity_professor_match_results")

    if "uq_match_analysis_runs_running_per_identity_professor" in _index_names(
        "match_analysis_runs"
    ):
        op.drop_index(
            "uq_match_analysis_runs_running_per_identity_professor",
            table_name="match_analysis_runs",
        )

    run_columns = _column_names("match_analysis_runs")
    removable_run_columns = [
        name
        for name in [
            "match_reason",
            "fit_points",
            "risk_points",
            "match_keywords",
        ]
        if name in run_columns
    ]
    if removable_run_columns:
        with op.batch_alter_table("match_analysis_runs") as batch_op:
            for column_name in removable_run_columns:
                batch_op.drop_column(column_name)

    if "match_source_identity_id" in _column_names("match_analysis_jobs"):
        match_job_foreign_key_name = _foreign_key_name(
            "match_analysis_jobs",
            ["match_source_identity_id"],
            "identity_profiles",
        )
        with op.batch_alter_table("match_analysis_jobs") as batch_op:
            if "ix_match_analysis_jobs_match_source_identity_id" in _index_names(
                "match_analysis_jobs"
            ):
                batch_op.drop_index(
                    "ix_match_analysis_jobs_match_source_identity_id"
                )
            if match_job_foreign_key_name is not None:
                batch_op.drop_constraint(
                    match_job_foreign_key_name,
                    type_="foreignkey",
                )
            batch_op.drop_column("match_source_identity_id")

    if "match_source_identity_id" in _column_names(
        "identity_communication_groups"
    ):
        communication_group_foreign_key_name = _foreign_key_name(
            "identity_communication_groups",
            ["match_source_identity_id"],
            "identity_profiles",
        )
        with op.batch_alter_table("identity_communication_groups") as batch_op:
            if (
                "ix_identity_communication_groups_match_source_identity_id"
                in _index_names("identity_communication_groups")
            ):
                batch_op.drop_index(
                    "ix_identity_communication_groups_match_source_identity_id"
                )
            if communication_group_foreign_key_name is not None:
                batch_op.drop_constraint(
                    communication_group_foreign_key_name,
                    type_="foreignkey",
                )
            batch_op.drop_column("match_source_identity_id")

    if "match_source_identity_id" in _column_names("email_tasks"):
        with op.batch_alter_table("email_tasks") as batch_op:
            batch_op.drop_column("match_source_identity_id")


def _create_match_results_table() -> None:
    if "identity_professor_match_results" not in _table_names():
        op.create_table(
            "identity_professor_match_results",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("identity_id", sa.Integer(), nullable=False),
            sa.Column("professor_id", sa.Integer(), nullable=False),
            sa.Column("llm_profile_id", sa.Integer(), nullable=True),
            sa.Column("primary_material_id", sa.Integer(), nullable=True),
            sa.Column("source_email_task_id", sa.Integer(), nullable=True),
            sa.Column("latest_analysis_run_id", sa.Integer(), nullable=True),
            sa.Column("match_score", sa.Integer(), nullable=False),
            sa.Column("match_reason", sa.Text(), nullable=False),
            sa.Column(
                "fit_points",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
            sa.Column(
                "risk_points",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
            sa.Column(
                "match_keywords",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
            sa.Column(
                "analyzed_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.CheckConstraint(
                "match_score >= 0 AND match_score <= 100",
                name="ck_identity_professor_match_results_score_range",
            ),
            sa.ForeignKeyConstraint(
                ["identity_id"],
                ["identity_profiles.id"],
                name="fk_identity_professor_match_results_identity_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["professor_id"],
                ["professors.id"],
                name="fk_identity_professor_match_results_professor_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["llm_profile_id"],
                ["llm_profiles.id"],
                name="fk_identity_professor_match_results_llm_profile_id",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["primary_material_id"],
                ["identity_materials.id"],
                name="fk_identity_professor_match_results_primary_material_id",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["source_email_task_id"],
                ["email_tasks.id"],
                name="fk_identity_professor_match_results_source_email_task_id",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["latest_analysis_run_id"],
                ["match_analysis_runs.id"],
                name="fk_identity_professor_match_results_latest_analysis_run_id",
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "identity_id",
                "professor_id",
                name="uq_identity_professor_match_results_identity_professor",
            ),
        )
    for column_name in [
        "identity_id",
        "professor_id",
        "llm_profile_id",
        "primary_material_id",
        "source_email_task_id",
        "latest_analysis_run_id",
    ]:
        op.create_index(
            f"ix_identity_professor_match_results_{column_name}",
            "identity_professor_match_results",
            [column_name],
            unique=False,
            if_not_exists=True,
        )
    op.create_index(
        "ix_identity_professor_match_results_identity_updated",
        "identity_professor_match_results",
        ["identity_id", "updated_at", "id"],
        unique=False,
        if_not_exists=True,
    )


def _add_group_match_source() -> None:
    column_missing = "match_source_identity_id" not in _column_names(
        "identity_communication_groups"
    )
    foreign_key_missing = not _has_foreign_key(
        "identity_communication_groups",
        ["match_source_identity_id"],
        "identity_profiles",
    )
    if column_missing or foreign_key_missing:
        with op.batch_alter_table("identity_communication_groups") as batch_op:
            if column_missing:
                batch_op.add_column(
                    sa.Column("match_source_identity_id", sa.Integer(), nullable=True)
                )
            if foreign_key_missing:
                batch_op.create_foreign_key(
                    "fk_identity_communication_groups_match_source_identity_id",
                    "identity_profiles",
                    ["match_source_identity_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
    op.create_index(
        "ix_identity_communication_groups_match_source_identity_id",
        "identity_communication_groups",
        ["match_source_identity_id"],
        unique=False,
        if_not_exists=True,
    )


def _add_email_task_match_source() -> None:
    if "match_source_identity_id" not in _column_names("email_tasks"):
        with op.batch_alter_table("email_tasks") as batch_op:
            batch_op.add_column(
                sa.Column("match_source_identity_id", sa.Integer(), nullable=True)
            )
    tasks = _table("email_tasks")
    op.get_bind().execute(
        sa.update(tasks)
        .where(
            tasks.c.match_score.is_not(None),
            tasks.c.match_source_identity_id.is_(None),
        )
        .values(match_source_identity_id=tasks.c.identity_id)
    )


def _add_job_match_source() -> None:
    column_missing = "match_source_identity_id" not in _column_names(
        "match_analysis_jobs"
    )
    foreign_key_missing = not _has_foreign_key(
        "match_analysis_jobs",
        ["match_source_identity_id"],
        "identity_profiles",
    )
    if column_missing or foreign_key_missing:
        with op.batch_alter_table("match_analysis_jobs") as batch_op:
            if column_missing:
                batch_op.add_column(
                    sa.Column("match_source_identity_id", sa.Integer(), nullable=True)
                )
            if foreign_key_missing:
                batch_op.create_foreign_key(
                    "fk_match_analysis_jobs_match_source_identity_id",
                    "identity_profiles",
                    ["match_source_identity_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
    op.create_index(
        "ix_match_analysis_jobs_match_source_identity_id",
        "match_analysis_jobs",
        ["match_source_identity_id"],
        unique=False,
        if_not_exists=True,
    )
    jobs = _table("match_analysis_jobs")
    op.get_bind().execute(
        sa.update(jobs)
        .where(jobs.c.match_source_identity_id.is_(None))
        .values(match_source_identity_id=jobs.c.identity_id)
    )


def _add_match_analysis_result_details() -> None:
    columns = _column_names("match_analysis_runs")
    additions = [
        ("match_reason", sa.Text()),
        ("fit_points", sa.JSON()),
        ("risk_points", sa.JSON()),
        ("match_keywords", sa.JSON()),
    ]
    missing = [(name, column_type) for name, column_type in additions if name not in columns]
    if not missing:
        return
    with op.batch_alter_table("match_analysis_runs") as batch_op:
        for name, column_type in missing:
            batch_op.add_column(sa.Column(name, column_type, nullable=True))


def _deduplicate_running_identity_professor_analyses() -> None:
    runs = _table("match_analysis_runs")
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.select(runs.c.identity_id, runs.c.professor_id)
        .where(runs.c.status == "running")
        .group_by(runs.c.identity_id, runs.c.professor_id)
        .having(sa.func.count(runs.c.id) > 1)
    ).all()
    for identity_id, professor_id in duplicates:
        ids = list(
            bind.scalars(
                sa.select(runs.c.id)
                .where(
                    runs.c.identity_id == identity_id,
                    runs.c.professor_id == professor_id,
                    runs.c.status == "running",
                )
                .order_by(runs.c.id.desc())
            )
        )
        if len(ids) <= 1:
            continue
        bind.execute(
            sa.update(runs)
            .where(runs.c.id.in_(ids[1:]))
            .values(
                status="failed",
                success=False,
                error_kind="migration_deduplicated",
                error_message="升级数据库时合并了重复的进行中匹配分析",
                finished_at=sa.func.current_timestamp(),
            )
        )


def _backfill_match_results() -> None:
    bind = op.get_bind()
    tasks = _table("email_tasks")
    results = _table("identity_professor_match_results")
    runs = _table("match_analysis_runs")
    identities = _table("identity_profiles")
    materials = _table("identity_materials")

    current_material_by_identity = dict(
        bind.execute(
            sa.select(
                identities.c.id,
                identities.c.current_primary_material_id,
            )
        ).all()
    )
    material_owner_by_id = dict(
        bind.execute(
            sa.select(materials.c.id, materials.c.identity_id),
        ).all()
    )
    task_rows = bind.execute(
        sa.select(
            tasks.c.id,
            tasks.c.identity_id,
            tasks.c.professor_id,
            tasks.c.llm_profile_id,
            tasks.c.primary_material_id,
            sa.cast(tasks.c.match_score, sa.Text).label("match_score"),
            tasks.c.match_reason,
            sa.cast(tasks.c.fit_points, sa.Text).label("fit_points"),
            sa.cast(tasks.c.risk_points, sa.Text).label("risk_points"),
            sa.cast(tasks.c.match_keywords, sa.Text).label("match_keywords"),
            tasks.c.created_at,
            tasks.c.updated_at,
        )
        .where(
            tasks.c.match_score.is_not(None),
            tasks.c.batch_send_canceled_at.is_(None),
            ~(
                (tasks.c.status == "canceled")
                & (tasks.c.cancellation_reason == "user_removed")
            ),
        )
        .order_by(
            tasks.c.identity_id.asc(),
            tasks.c.professor_id.asc(),
            tasks.c.updated_at.desc(),
            tasks.c.created_at.desc(),
            tasks.c.id.desc(),
        )
    ).mappings()

    seen: set[tuple[int, int]] = set(
        bind.execute(
            sa.select(results.c.identity_id, results.c.professor_id),
        ).all()
    )
    for task in task_rows:
        match_score = _normalize_match_score(task["match_score"])
        if match_score is None:
            continue
        key = (task["identity_id"], task["professor_id"])
        if key in seen:
            continue
        seen.add(key)
        run = bind.execute(
            sa.select(
                runs.c.id,
                runs.c.primary_material_id,
                runs.c.finished_at,
            )
            .where(
                runs.c.email_task_id == task["id"],
                runs.c.success.is_(True),
            )
            .order_by(runs.c.finished_at.desc(), runs.c.id.desc())
            .limit(1)
        ).mappings().first()
        analyzed_at = (
            run["finished_at"]
            if run is not None and run["finished_at"] is not None
            else task["updated_at"] or task["created_at"]
        )
        material_candidates = (
            run["primary_material_id"] if run is not None else None,
            task["primary_material_id"],
            current_material_by_identity.get(task["identity_id"]),
        )
        primary_material_id = next(
            (
                material_id
                for material_id in material_candidates
                if material_id is not None
                and material_owner_by_id.get(material_id) == task["identity_id"]
            ),
            None,
        )
        values = {
            "identity_id": task["identity_id"],
            "professor_id": task["professor_id"],
            "llm_profile_id": task["llm_profile_id"],
            "primary_material_id": primary_material_id,
            "source_email_task_id": task["id"],
            "latest_analysis_run_id": run["id"] if run is not None else None,
            "match_score": match_score,
            "match_reason": task["match_reason"] or "",
            "fit_points": _normalize_string_list(task["fit_points"]),
            "risk_points": _normalize_string_list(task["risk_points"]),
            "match_keywords": _normalize_string_list(task["match_keywords"]),
            "analyzed_at": analyzed_at,
            "created_at": analyzed_at,
            "updated_at": analyzed_at,
        }
        bind.execute(sa.insert(results).values(**values))
        if run is not None:
            bind.execute(
                sa.update(runs)
                .where(runs.c.id == run["id"])
                .values(
                    match_reason=values["match_reason"],
                    fit_points=values["fit_points"],
                    risk_points=values["risk_points"],
                    match_keywords=values["match_keywords"],
                )
            )


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    if table_name not in _table_names():
        return set()
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _index_names(table_name: str) -> set[str]:
    if table_name not in _table_names():
        return set()
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
        if index.get("name")
    }


def _has_foreign_key(
    table_name: str,
    constrained_columns: list[str],
    referred_table: str,
) -> bool:
    return any(
        foreign_key.get("constrained_columns") == constrained_columns
        and foreign_key.get("referred_table") == referred_table
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name)
    )


def _foreign_key_name(
    table_name: str,
    constrained_columns: list[str],
    referred_table: str,
) -> str | None:
    for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name):
        if (
            foreign_key.get("constrained_columns") == constrained_columns
            and foreign_key.get("referred_table") == referred_table
        ):
            name = foreign_key.get("name")
            return str(name) if name else None
    return None


def _normalize_match_score(value: object) -> int | None:
    try:
        score = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0, min(100, score))


def _normalize_string_list(value: object) -> list[str]:
    if value is None:
        return []
    parsed = value
    if isinstance(value, (bytes, bytearray)):
        parsed = bytes(value).decode("utf-8", errors="replace")
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str)]


def _table(table_name: str) -> sa.Table:
    return sa.Table(
        table_name,
        sa.MetaData(),
        autoload_with=op.get_bind(),
    )
