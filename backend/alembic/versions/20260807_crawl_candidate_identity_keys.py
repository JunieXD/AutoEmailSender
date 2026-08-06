"""add canonical identity keys for crawl candidates

Revision ID: 20260807_candidate_identity
Revises: 20260806_crawler_recovery
Create Date: 2026-08-07 00:00:00.000000
"""

from __future__ import annotations

from html import unescape
import json
import re
from typing import Sequence, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from alembic import op
import sqlalchemy as sa


revision: str = "20260807_candidate_identity"
down_revision: Union[str, Sequence[str], None] = "20260806_crawler_recovery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_EMAIL_LOCAL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
_EMAIL_DOMAIN_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
_AT = re.compile(r"(?:[\(\[]\s*at\s*[\)\]]|(?:(?<=^)|(?<=\s))at(?=$|\s))", re.IGNORECASE)
_DOT = re.compile(r"(?:[\(\[]\s*dot\s*[\)\]]|(?:(?<=^)|(?<=\s))dot(?=$|\s))", re.IGNORECASE)
_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "yclid", "mc_cid", "mc_eid"}


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


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


def _foreign_key_exists(table_name: str, constraint_name: str) -> bool:
    return any(
        foreign_key["name"] == constraint_name
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name)
    )


def _normalize_email(value: object) -> str | None:
    if value is None:
        return None
    normalized = unescape(str(value)).strip().lower()
    normalized = normalized.translate(str.maketrans({"＠": "@", "﹫": "@", "．": ".", "。": ".", "｡": "."}))
    normalized = _INVISIBLE.sub("", normalized)
    normalized = re.sub(r"\s*(?:（at）|【at】|\[at\]|\(at\)|＠)\s*", "@", normalized, flags=re.IGNORECASE)
    normalized = _AT.sub("@", normalized)
    normalized = _DOT.sub(".", normalized)
    normalized = re.sub(r"(?<=[A-Za-z0-9])\s*点\s*(?=[A-Za-z0-9])", ".", normalized)
    normalized = re.sub(r"\s*@\s*", "@", normalized)
    normalized = re.sub(r"\s*\.\s*", ".", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    if normalized.count("@") != 1:
        return None
    local_part, domain = normalized.split("@", 1)
    domain = re.sub(r"\.{2,}", ".", domain).strip(".")
    labels = domain.split(".")
    if (
        not local_part
        or not _EMAIL_LOCAL.fullmatch(local_part)
        or len(labels) < 2
        or not all(_EMAIL_DOMAIN_LABEL.fullmatch(label) for label in labels)
        or not labels[-1].isalpha()
        or len(labels[-1]) < 2
    ):
        return None
    return f"{local_part}@{domain}"


def _normalize_profile_url(value: object) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = urlsplit(str(value).strip())
        scheme = (parsed.scheme or "https").lower()
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return None
        port = parsed.port
    except ValueError:
        return None
    netloc = hostname
    if port is not None and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{hostname}:{port}"
    query_items = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_KEYS and not key.lower().startswith("utm_")
    ]
    fragment = parsed.fragment if parsed.fragment.startswith(("/", "!/")) else ""
    return urlunsplit((scheme, netloc, parsed.path or "", urlencode(sorted(query_items), doseq=True), fragment))


def _candidate_rank(row: dict[str, object]) -> tuple[int, int, int, int]:
    status_priority = {"merged": 0, "rejected": 1, "pending": 2, "accepted": 3}
    completeness = sum(
        bool(row.get(field))
        for field in (
            "email",
            "title",
            "department",
            "research_direction",
            "recent_papers",
            "profile_url",
        )
    )
    return (
        status_priority.get(str(row.get("review_status") or ""), -1),
        int(row.get("professor_id") is not None),
        completeness,
        int(row["id"]),
    )


def _json_value(value: object, fallback: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return fallback
    return value if value is not None else fallback


def _merge_component_values(
    canonical: dict[str, object],
    component: list[dict[str, object]],
) -> dict[str, object]:
    updates: dict[str, object] = {}
    for field_name in (
        "name",
        "email",
        "title",
        "university",
        "school",
        "department",
        "research_direction",
        "profile_url",
        "source_url",
        "source_chunk_id",
        "source_kind",
    ):
        if canonical.get(field_name) not in (None, ""):
            continue
        replacement = next(
            (
                row.get(field_name)
                for row in component
                if row.get(field_name) not in (None, "")
            ),
            None,
        )
        if replacement not in (None, ""):
            updates[field_name] = replacement

    papers: list[object] = []
    for row in component:
        value = _json_value(row.get("recent_papers"), [])
        if not isinstance(value, list):
            continue
        for paper in value:
            if paper not in papers:
                papers.append(paper)
    if papers:
        updates["recent_papers"] = papers[:8]

    for field_name in ("field_confidence", "evidence", "field_sources", "conflicts"):
        merged: dict[str, object] = {}
        for row in reversed(component):
            value = _json_value(row.get(field_name), {})
            if isinstance(value, dict):
                merged.update(value)
        if merged:
            updates[field_name] = merged

    merge_history = _json_value(canonical.get("merge_history"), [])
    history = list(merge_history) if isinstance(merge_history, list) else []
    alias_ids = sorted(
        int(row["id"])
        for row in component
        if int(row["id"]) != int(canonical["id"])
    )
    if alias_ids:
        if not any(
            isinstance(item, dict) and item.get("migration") == revision
            for item in history
        ):
            history.append(
                {
                    "migration": revision,
                    "merged_candidate_ids": alias_ids,
                }
            )
        updates["merge_history"] = history[-50:]
    updates["identity_key"] = _normalize_email(
        updates.get("email", canonical.get("email")),
    ) or _normalize_profile_url(
        updates.get("profile_url", canonical.get("profile_url")),
    )
    return updates


def _backfill_candidate_identity_keys() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("DELETE FROM crawl_candidate_identity_keys"))
    rows = [
        dict(row)
        for row in connection.execute(
            sa.text(
                """
                SELECT c.id, c.job_id, c.name, c.email, c.title, c.university,
                       c.school, c.department, c.research_direction,
                       c.recent_papers, c.profile_url, c.source_url,
                       c.source_chunk_id, c.source_kind, c.identity_key,
                       c.field_confidence, c.evidence, c.merge_history,
                       c.field_sources, c.conflicts, c.review_status,
                       c.professor_id
                FROM crawl_candidates AS c
                JOIN crawl_jobs AS j ON j.id = c.job_id
                WHERE COALESCE(j.job_kind, 'faculty_crawl') = 'faculty_crawl'
                ORDER BY c.job_id, c.id
                """
            )
        ).mappings()
    ]
    rows_by_job: dict[int, list[dict[str, object]]] = {}
    for row in rows:
        rows_by_job.setdefault(int(row["job_id"]), []).append(row)

    insert_key = sa.text(
        """
        INSERT INTO crawl_candidate_identity_keys
            (job_id, candidate_id, key_type, normalized_value)
        VALUES (:job_id, :candidate_id, :key_type, :normalized_value)
        """
    )
    mark_alias = sa.text(
        "UPDATE crawl_candidates SET merged_into_candidate_id = :canonical_id WHERE id = :alias_id"
    )
    candidate_table = sa.table(
        "crawl_candidates",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("email", sa.String()),
        sa.column("title", sa.String()),
        sa.column("university", sa.String()),
        sa.column("school", sa.String()),
        sa.column("department", sa.String()),
        sa.column("research_direction", sa.Text()),
        sa.column("recent_papers", sa.JSON()),
        sa.column("profile_url", sa.String()),
        sa.column("source_url", sa.String()),
        sa.column("source_chunk_id", sa.String()),
        sa.column("source_kind", sa.String()),
        sa.column("identity_key", sa.String()),
        sa.column("field_confidence", sa.JSON()),
        sa.column("evidence", sa.JSON()),
        sa.column("merge_history", sa.JSON()),
        sa.column("field_sources", sa.JSON()),
        sa.column("conflicts", sa.JSON()),
    )

    for job_id, job_rows in rows_by_job.items():
        parent = {int(row["id"]): int(row["id"]) for row in job_rows}

        def find(candidate_id: int) -> int:
            while parent[candidate_id] != candidate_id:
                parent[candidate_id] = parent[parent[candidate_id]]
                candidate_id = parent[candidate_id]
            return candidate_id

        def union(first_id: int, second_id: int) -> None:
            first_root = find(first_id)
            second_root = find(second_id)
            if first_root != second_root:
                parent[max(first_root, second_root)] = min(first_root, second_root)

        key_owner: dict[tuple[str, str], int] = {}
        row_keys: dict[int, tuple[tuple[str, str], ...]] = {}
        for row in job_rows:
            candidate_id = int(row["id"])
            keys: list[tuple[str, str]] = []
            email = _normalize_email(row.get("email"))
            profile_url = _normalize_profile_url(row.get("profile_url"))
            if email:
                keys.append(("email", email))
            if profile_url:
                keys.append(("profile_url", profile_url))
            row_keys[candidate_id] = tuple(keys)
            for key in keys:
                owner = key_owner.setdefault(key, candidate_id)
                union(candidate_id, owner)

        components: dict[int, list[dict[str, object]]] = {}
        for row in job_rows:
            components.setdefault(find(int(row["id"])), []).append(row)

        canonical_by_row: dict[int, int] = {}
        for component in components.values():
            canonical = max(component, key=_candidate_rank)
            canonical_id = int(canonical["id"])
            connection.execute(
                candidate_table.update()
                .where(candidate_table.c.id == canonical_id)
                .values(**_merge_component_values(canonical, component))
            )
            for row in component:
                candidate_id = int(row["id"])
                canonical_by_row[candidate_id] = canonical_id
                if candidate_id != canonical_id:
                    connection.execute(
                        mark_alias,
                        {"canonical_id": canonical_id, "alias_id": candidate_id},
                    )

        inserted: set[tuple[str, str]] = set()
        for candidate_id, keys in row_keys.items():
            canonical_id = canonical_by_row[candidate_id]
            for key_type, normalized_value in keys:
                identity = (key_type, normalized_value)
                if identity in inserted:
                    continue
                inserted.add(identity)
                connection.execute(
                    insert_key,
                    {
                        "job_id": job_id,
                        "candidate_id": canonical_id,
                        "key_type": key_type,
                        "normalized_value": normalized_value,
                    },
                )


def upgrade() -> None:
    column_name = "merged_into_candidate_id"
    index_name = "ix_crawl_candidates_merged_into_candidate_id"
    foreign_key_name = "fk_crawl_candidates_merged_into_candidate_id_crawl_candidates"
    add_column = not _column_exists("crawl_candidates", column_name)
    add_foreign_key = not _foreign_key_exists("crawl_candidates", foreign_key_name)
    if add_column or add_foreign_key:
        if _index_exists("crawl_candidates", index_name):
            op.drop_index(index_name, table_name="crawl_candidates")
        with op.batch_alter_table("crawl_candidates") as batch_op:
            if add_column:
                batch_op.add_column(sa.Column(column_name, sa.Integer(), nullable=True))
            if add_foreign_key:
                batch_op.create_foreign_key(
                    foreign_key_name,
                    "crawl_candidates",
                    [column_name],
                    ["id"],
                    ondelete="CASCADE",
                )
    if not _index_exists("crawl_candidates", index_name):
        op.create_index(
            index_name,
            "crawl_candidates",
            [column_name],
            unique=False,
        )

    if not _table_exists("crawl_candidate_identity_keys"):
        op.create_table(
            "crawl_candidate_identity_keys",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("job_id", sa.Integer(), nullable=False),
            sa.Column("candidate_id", sa.Integer(), nullable=False),
            sa.Column("key_type", sa.String(length=32), nullable=False),
            sa.Column("normalized_value", sa.String(length=1000), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["candidate_id"],
                ["crawl_candidates.id"],
                name="fk_crawl_candidate_identity_keys_candidate_id_crawl_candidates",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["job_id"],
                ["crawl_jobs.id"],
                name="fk_crawl_candidate_identity_keys_job_id_crawl_jobs",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_crawl_candidate_identity_keys"),
            sa.UniqueConstraint(
                "job_id",
                "key_type",
                "normalized_value",
                name="uq_crawl_candidate_identity_keys_job_type_value",
            ),
        )
    for key_index_name, key_column_name in (
        ("ix_crawl_candidate_identity_keys_job_id", "job_id"),
        ("ix_crawl_candidate_identity_keys_candidate_id", "candidate_id"),
    ):
        if not _index_exists("crawl_candidate_identity_keys", key_index_name):
            op.create_index(
                key_index_name,
                "crawl_candidate_identity_keys",
                [key_column_name],
                unique=False,
            )
    _backfill_candidate_identity_keys()
    for index_name in (
        "uq_crawl_candidates_job_profile_url",
        "uq_crawl_candidates_job_email_ci",
        "uq_crawl_candidates_job_identity_key",
    ):
        if _index_exists("crawl_candidates", index_name):
            op.drop_index(index_name, table_name="crawl_candidates")


def downgrade() -> None:
    op.drop_index(
        "ix_crawl_candidate_identity_keys_candidate_id",
        table_name="crawl_candidate_identity_keys",
    )
    op.drop_index(
        "ix_crawl_candidate_identity_keys_job_id",
        table_name="crawl_candidate_identity_keys",
    )
    op.drop_table("crawl_candidate_identity_keys")
    with op.batch_alter_table("crawl_candidates") as batch_op:
        batch_op.drop_index("ix_crawl_candidates_merged_into_candidate_id")
        batch_op.drop_constraint(
            "fk_crawl_candidates_merged_into_candidate_id_crawl_candidates",
            type_="foreignkey",
        )
        batch_op.drop_column("merged_into_candidate_id")
