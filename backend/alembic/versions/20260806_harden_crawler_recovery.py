"""harden crawler recovery and concurrent persistence

Revision ID: 20260806_crawler_recovery
Revises: 20260806_crawl_job_serial
Create Date: 2026-08-06 12:00:00.000000
"""

from __future__ import annotations

from collections import defaultdict
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260806_crawler_recovery"
down_revision: Union[str, Sequence[str], None] = "20260806_crawl_job_serial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    _add_failure_counters()
    _add_token_claim_identity()


def downgrade() -> None:
    for index_name in (
        "uq_crawl_candidates_job_profile_url",
        "uq_crawl_candidates_job_email_ci",
        "uq_crawl_candidates_job_identity_key",
    ):
        if _index_exists("crawl_candidates", index_name):
            op.drop_index(index_name, table_name="crawl_candidates")

    for index_name in (
        op.f("ix_crawl_worker_token_usages_claim_id"),
        op.f("ix_crawl_worker_token_usages_run_id"),
    ):
        if _index_exists("crawl_worker_token_usages", index_name):
            op.drop_index(index_name, table_name="crawl_worker_token_usages")

    drop_unique = _unique_constraint_exists(
        "crawl_worker_token_usages",
        "uq_crawl_worker_token_usage_claim",
    )
    drop_foreign_key = _foreign_key_exists(
        "crawl_worker_token_usages",
        "fk_crawl_worker_token_usages_run_id_crawl_job_runs",
    )
    drop_claim_id = _column_exists("crawl_worker_token_usages", "claim_id")
    drop_run_id = _column_exists("crawl_worker_token_usages", "run_id")
    if drop_unique or drop_foreign_key or drop_claim_id or drop_run_id:
        with op.batch_alter_table("crawl_worker_token_usages", schema=None) as batch_op:
            if drop_unique:
                batch_op.drop_constraint(
                    "uq_crawl_worker_token_usage_claim",
                    type_="unique",
                )
            if drop_foreign_key:
                batch_op.drop_constraint(
                    "fk_crawl_worker_token_usages_run_id_crawl_job_runs",
                    type_="foreignkey",
                )
            if drop_claim_id:
                batch_op.drop_column("claim_id")
            if drop_run_id:
                batch_op.drop_column("run_id")

    for table_name in (
        "crawl_candidate_enrichment_tasks",
        "crawl_page_chunks",
        "crawl_page_tasks",
    ):
        if _column_exists(table_name, "failure_count"):
            with op.batch_alter_table(table_name, schema=None) as batch_op:
                batch_op.drop_column("failure_count")


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


def _unique_constraint_exists(table_name: str, constraint_name: str) -> bool:
    return any(
        constraint["name"] == constraint_name
        for constraint in sa.inspect(op.get_bind()).get_unique_constraints(table_name)
    )


def _foreign_key_exists(table_name: str, constraint_name: str) -> bool:
    return any(
        foreign_key["name"] == constraint_name
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name)
    )


def _add_failure_counters() -> None:
    for table_name in (
        "crawl_page_tasks",
        "crawl_page_chunks",
        "crawl_candidate_enrichment_tasks",
    ):
        if not _column_exists(table_name, "failure_count"):
            with op.batch_alter_table(table_name, schema=None) as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "failure_count",
                        sa.Integer(),
                        server_default=sa.text("0"),
                        nullable=False,
                    )
                )
        op.execute(
            sa.text(
                f"UPDATE {table_name} "
                "SET failure_count = CASE "
                "WHEN status = 'failed_terminal' THEN CASE WHEN attempt_count < 4 THEN 4 ELSE attempt_count END "
                "WHEN status = 'failed_retryable' THEN attempt_count "
                "ELSE 0 END"
            )
        )


def _add_token_claim_identity() -> None:
    if not _column_exists("crawl_worker_token_usages", "run_id"):
        op.add_column(
            "crawl_worker_token_usages",
            sa.Column("run_id", sa.Integer(), nullable=True),
        )
    if not _column_exists("crawl_worker_token_usages", "claim_id"):
        op.add_column(
            "crawl_worker_token_usages",
            sa.Column("claim_id", sa.String(length=128), nullable=True),
        )
    create_foreign_key = not _foreign_key_exists(
        "crawl_worker_token_usages",
        "fk_crawl_worker_token_usages_run_id_crawl_job_runs",
    )
    create_unique = not _unique_constraint_exists(
        "crawl_worker_token_usages",
        "uq_crawl_worker_token_usage_claim",
    )
    if create_foreign_key or create_unique:
        with op.batch_alter_table("crawl_worker_token_usages", schema=None) as batch_op:
            if create_foreign_key:
                batch_op.create_foreign_key(
                    "fk_crawl_worker_token_usages_run_id_crawl_job_runs",
                    "crawl_job_runs",
                    ["run_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
            if create_unique:
                batch_op.create_unique_constraint(
                    "uq_crawl_worker_token_usage_claim",
                    ["run_id", "worker_kind", "work_item_id", "claim_id"],
                )
    for index_name, column_name in (
        (op.f("ix_crawl_worker_token_usages_run_id"), "run_id"),
        (op.f("ix_crawl_worker_token_usages_claim_id"), "claim_id"),
    ):
        if not _index_exists("crawl_worker_token_usages", index_name):
            op.create_index(
                index_name,
                "crawl_worker_token_usages",
                [column_name],
            )


def _merge_duplicate_candidates() -> None:
    connection = op.get_bind()
    rows = list(
        connection.execute(
            sa.text(
                "SELECT id, job_id, email, profile_url, identity_key, review_status, "
                "name, university, school, title, department, research_direction, "
                "recent_papers, source_url FROM crawl_candidates ORDER BY id"
            )
        ).mappings()
    )
    if not rows:
        return

    parent = {int(row["id"]): int(row["id"]) for row in rows}

    def find(candidate_id: int) -> int:
        while parent[candidate_id] != candidate_id:
            parent[candidate_id] = parent[parent[candidate_id]]
            candidate_id = parent[candidate_id]
        return candidate_id

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    seen: dict[tuple[str, int, str], int] = {}
    for row in rows:
        candidate_id = int(row["id"])
        job_id = int(row["job_id"])
        values = (
            ("identity", str(row["identity_key"] or "").strip()),
            ("email", str(row["email"] or "").strip().lower()),
            ("profile", str(row["profile_url"] or "").strip()),
        )
        for kind, value in values:
            if not value:
                continue
            key = (kind, job_id, value)
            previous = seen.get(key)
            if previous is None:
                seen[key] = candidate_id
            else:
                union(previous, candidate_id)

    groups: dict[int, list[sa.RowMapping]] = defaultdict(list)
    for row in rows:
        groups[find(int(row["id"]))].append(row)

    candidate_table = sa.table(
        "crawl_candidates",
        sa.column("id", sa.Integer()),
        sa.column("email", sa.String()),
        sa.column("profile_url", sa.String()),
        sa.column("identity_key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("university", sa.String()),
        sa.column("school", sa.String()),
        sa.column("title", sa.String()),
        sa.column("department", sa.String()),
        sa.column("research_direction", sa.Text()),
        sa.column("recent_papers", sa.JSON()),
        sa.column("source_url", sa.String()),
    )
    enrichment_table = sa.table(
        "crawl_candidate_enrichment_tasks",
        sa.column("id", sa.Integer()),
        sa.column("candidate_id", sa.Integer()),
    )

    for group in groups.values():
        if len(group) <= 1:
            continue
        keeper = max(group, key=_candidate_survivor_score)
        keeper_id = int(keeper["id"])
        duplicate_ids = [int(row["id"]) for row in group if int(row["id"]) != keeper_id]
        updates: dict[str, object] = {}
        for field_name in (
            "email",
            "profile_url",
            "identity_key",
            "name",
            "university",
            "school",
            "title",
            "department",
            "research_direction",
            "recent_papers",
            "source_url",
        ):
            if keeper[field_name] not in (None, "", []):
                continue
            replacement = next(
                (
                    row[field_name]
                    for row in group
                    if row[field_name] not in (None, "", [])
                ),
                None,
            )
            if replacement not in (None, "", []):
                updates[field_name] = replacement
        if not updates.get("identity_key") and not keeper["identity_key"]:
            email = str(updates.get("email") or keeper["email"] or "").strip().lower()
            profile_url = str(
                updates.get("profile_url") or keeper["profile_url"] or ""
            ).strip()
            updates["identity_key"] = email or profile_url or None
        if updates:
            connection.execute(
                candidate_table.update()
                .where(candidate_table.c.id == keeper_id)
                .values(**updates)
            )

        task_ids = list(
            connection.execute(
                sa.select(enrichment_table.c.id).where(
                    enrichment_table.c.candidate_id.in_(duplicate_ids)
                )
            ).scalars()
        )
        keeper_task_id = connection.execute(
            sa.select(enrichment_table.c.id)
            .where(enrichment_table.c.candidate_id == keeper_id)
            .limit(1)
        ).scalar_one_or_none()
        if task_ids and keeper_task_id is None:
            keeper_task_id = int(task_ids.pop(0))
            connection.execute(
                enrichment_table.update()
                .where(enrichment_table.c.id == keeper_task_id)
                .values(candidate_id=keeper_id)
            )
        if task_ids:
            connection.execute(
                enrichment_table.delete().where(enrichment_table.c.id.in_(task_ids))
            )
        connection.execute(
            candidate_table.delete().where(candidate_table.c.id.in_(duplicate_ids))
        )


def _candidate_survivor_score(row: sa.RowMapping) -> tuple[int, int, int]:
    review_priority = {
        "accepted": 4,
        "pending": 3,
        "merged": 2,
        "rejected": 1,
    }.get(str(row["review_status"] or ""), 0)
    completeness = sum(
        row[field_name] not in (None, "", [])
        for field_name in (
            "email",
            "profile_url",
            "name",
            "title",
            "department",
            "research_direction",
            "recent_papers",
        )
    )
    return review_priority, completeness, -int(row["id"])


def _create_candidate_identity_indexes() -> None:
    op.execute(
        "UPDATE crawl_candidates SET email = lower(trim(email)) "
        "WHERE email IS NOT NULL AND trim(email) <> ''"
    )
    indexes = (
        (
            "uq_crawl_candidates_job_identity_key",
            "identity_key",
        ),
        (
            "uq_crawl_candidates_job_email_ci",
            "email",
        ),
        (
            "uq_crawl_candidates_job_profile_url",
            "profile_url",
        ),
    )
    for index_name, column_name in indexes:
        if not _index_exists("crawl_candidates", index_name):
            op.create_index(
                index_name,
                "crawl_candidates",
                ["job_id", column_name],
                unique=True,
                sqlite_where=sa.text(
                    f"{column_name} IS NOT NULL AND trim({column_name}) <> ''"
                ),
                postgresql_where=sa.text(
                    f"{column_name} IS NOT NULL AND trim({column_name}) <> ''"
                ),
            )
