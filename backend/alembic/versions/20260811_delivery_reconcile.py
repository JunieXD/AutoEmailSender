"""separate logical email deliveries from mailbox observations

Revision ID: 20260811_delivery_reconcile
Revises: 20260811_global_material_library
Create Date: 2026-08-11 00:00:00.000000
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260811_delivery_reconcile"
down_revision: str | Sequence[str] | None = "20260811_global_material_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RECONCILIATION_VERSION = 1
LEGACY_MATCH_MAX_SECONDS = 30 * 60
LEGACY_BODY_SIMILARITY = 0.9
LEGACY_CONTAINMENT_MIN_CHARS = 12
LEGACY_CONTAINMENT_MIN_RATIO = 0.3


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    return {
        str(column["name"])
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _index_names(table_name: str) -> set[str]:
    return {
        str(index["name"])
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
        if index.get("name")
    }


def _normalize_text(value: object | None) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(
        " ".join(line.split()) for line in normalized.split("\n") if line.strip()
    ).strip()


def _fingerprint(value: object | None) -> str:
    digest = hashlib.sha256(_normalize_text(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _normalize_message_id(value: object | None) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized or None


def _coerce_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        resolved = value
    else:
        resolved = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=UTC)
    return resolved.astimezone(UTC)


def _time_delta_seconds(left: object, right: object) -> float:
    return abs((_coerce_datetime(left) - _coerce_datetime(right)).total_seconds())


def _json_value(value: object | None) -> object | None:
    if value is None or isinstance(value, dict | list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def _create_delivery_attempts_table() -> None:
    if "email_delivery_attempts" in _table_names():
        return
    op.create_table(
        "email_delivery_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email_task_id", sa.Integer(), nullable=True),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("professor_id", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("subject_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("app_message_id", sa.String(length=255), nullable=True),
        sa.Column("normalized_app_message_id", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'prepared'"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["email_task_id"],
            ["email_tasks.id"],
            name="fk_email_delivery_attempts_email_task_id_email_tasks",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["identity_profiles.id"],
            name="fk_email_delivery_attempts_identity_id_identity_profiles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["professor_id"],
            ["professors.id"],
            name="fk_email_delivery_attempts_professor_id_professors",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_email_delivery_attempts"),
        sa.UniqueConstraint(
            "email_task_id",
            "attempt_number",
            name="uq_email_delivery_attempts_task_number",
        ),
    )
    op.create_index(
        "ix_email_delivery_attempts_email_task_id",
        "email_delivery_attempts",
        ["email_task_id"],
    )
    op.create_index(
        "ix_email_delivery_attempts_identity_id",
        "email_delivery_attempts",
        ["identity_id"],
    )
    op.create_index(
        "ix_email_delivery_attempts_professor_id",
        "email_delivery_attempts",
        ["professor_id"],
    )
    op.create_index(
        "ix_email_delivery_attempts_identity_professor_started",
        "email_delivery_attempts",
        ["identity_id", "professor_id", "started_at", "id"],
    )
    op.create_index(
        "ix_email_delivery_attempts_message_id",
        "email_delivery_attempts",
        ["identity_id", "normalized_app_message_id"],
    )


def _add_email_log_reconciliation_columns() -> None:
    columns = _column_names("email_logs")
    needs_delivery_attempt = "delivery_attempt_id" not in columns
    needs_merged_into = "merged_into_id" not in columns
    with op.batch_alter_table("email_logs") as batch_op:
        if needs_delivery_attempt:
            batch_op.add_column(
                sa.Column("delivery_attempt_id", sa.String(length=36), nullable=True)
            )
        if needs_merged_into:
            batch_op.add_column(
                sa.Column("merged_into_id", sa.Integer(), nullable=True)
            )
        if "record_state" not in columns:
            batch_op.add_column(
                sa.Column(
                    "record_state",
                    sa.String(length=20),
                    server_default=sa.text("'canonical'"),
                    nullable=False,
                ),
            )
        if "reconciliation_version" not in columns:
            batch_op.add_column(
                sa.Column(
                    "reconciliation_version",
                    sa.Integer(),
                    server_default=sa.text(str(RECONCILIATION_VERSION)),
                    nullable=False,
                ),
            )
        if needs_delivery_attempt:
            batch_op.create_foreign_key(
                "fk_email_logs_delivery_attempt_id_email_delivery_attempts",
                "email_delivery_attempts",
                ["delivery_attempt_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if needs_merged_into:
            batch_op.create_foreign_key(
                "fk_email_logs_merged_into_id_email_logs",
                "email_logs",
                ["merged_into_id"],
                ["id"],
                ondelete="SET NULL",
            )

    indexes = _index_names("email_logs")
    if "ix_email_logs_record_state_identity_direction_created" not in indexes:
        op.create_index(
            "ix_email_logs_record_state_identity_direction_created",
            "email_logs",
            ["record_state", "identity_id", "direction", "created_at", "id"],
        )
    if "uq_email_logs_delivery_attempt_id" not in indexes:
        op.create_index(
            "uq_email_logs_delivery_attempt_id",
            "email_logs",
            ["delivery_attempt_id"],
            unique=True,
            sqlite_where=sa.text("delivery_attempt_id IS NOT NULL"),
            postgresql_where=sa.text("delivery_attempt_id IS NOT NULL"),
        )


def _create_email_observations_table() -> None:
    if "email_observations" in _table_names():
        return
    op.create_table(
        "email_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email_log_id", sa.Integer(), nullable=True),
        sa.Column("candidate_email_log_id", sa.Integer(), nullable=True),
        sa.Column("delivery_attempt_id", sa.String(length=36), nullable=True),
        sa.Column("legacy_email_log_id", sa.Integer(), nullable=True),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("professor_id", sa.Integer(), nullable=True),
        sa.Column("direction", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column(
            "resolution",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("match_method", sa.String(length=40), nullable=True),
        sa.Column("delivery_key", sa.String(length=64), nullable=True),
        sa.Column("message_id", sa.String(length=255), nullable=True),
        sa.Column("normalized_message_id", sa.String(length=255), nullable=True),
        sa.Column("folder_role", sa.String(length=20), nullable=True),
        sa.Column("folder", sa.String(length=255), nullable=True),
        sa.Column("uidvalidity", sa.Integer(), nullable=True),
        sa.Column("imap_uid", sa.Integer(), nullable=True),
        sa.Column("from_email", sa.String(length=255), nullable=True),
        sa.Column("to_emails", sa.JSON(), nullable=True),
        sa.Column("cc_emails", sa.JSON(), nullable=True),
        sa.Column("bcc_emails", sa.JSON(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("content_html", sa.Text(), nullable=True),
        sa.Column("subject_fingerprint", sa.String(length=71), nullable=True),
        sa.Column("content_fingerprint", sa.String(length=71), nullable=True),
        sa.Column("message_sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("headers", sa.JSON(), nullable=True),
        sa.Column("provider_payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["email_log_id"],
            ["email_logs.id"],
            name="fk_email_observations_email_log_id_email_logs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_email_log_id"],
            ["email_logs.id"],
            name="fk_email_observations_candidate_email_log_id_email_logs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["delivery_attempt_id"],
            ["email_delivery_attempts.id"],
            name="fk_email_observations_delivery_attempt_id_email_delivery_attempts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["legacy_email_log_id"],
            ["email_logs.id"],
            name="fk_email_observations_legacy_email_log_id_email_logs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["identity_profiles.id"],
            name="fk_email_observations_identity_id_identity_profiles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["professor_id"],
            ["professors.id"],
            name="fk_email_observations_professor_id_professors",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_email_observations"),
    )
    op.create_index(
        "ix_email_observations_email_log_id", "email_observations", ["email_log_id"]
    )
    op.create_index(
        "ix_email_observations_candidate_email_log_id",
        "email_observations",
        ["candidate_email_log_id"],
    )
    op.create_index(
        "ix_email_observations_delivery_attempt_id",
        "email_observations",
        ["delivery_attempt_id"],
    )
    op.create_index(
        "ix_email_observations_identity_id", "email_observations", ["identity_id"]
    )
    op.create_index(
        "ix_email_observations_professor_id", "email_observations", ["professor_id"]
    )
    op.create_index(
        "uq_email_observations_imap_location",
        "email_observations",
        [
            "identity_id",
            "professor_id",
            "folder_role",
            "folder",
            "uidvalidity",
            "imap_uid",
        ],
        unique=True,
        sqlite_where=sa.text(
            "professor_id IS NOT NULL AND folder_role IS NOT NULL AND folder IS NOT NULL "
            "AND uidvalidity IS NOT NULL AND imap_uid IS NOT NULL"
        ),
        postgresql_where=sa.text(
            "professor_id IS NOT NULL AND folder_role IS NOT NULL AND folder IS NOT NULL "
            "AND uidvalidity IS NOT NULL AND imap_uid IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_email_observations_legacy_log",
        "email_observations",
        ["legacy_email_log_id"],
        unique=True,
        sqlite_where=sa.text("legacy_email_log_id IS NOT NULL"),
        postgresql_where=sa.text("legacy_email_log_id IS NOT NULL"),
    )
    op.create_index(
        "ix_email_observations_delivery_key",
        "email_observations",
        ["identity_id", "delivery_key"],
    )
    op.create_index(
        "ix_email_observations_message_lookup",
        "email_observations",
        ["identity_id", "professor_id", "direction", "normalized_message_id"],
    )
    op.create_index(
        "ix_email_observations_pending",
        "email_observations",
        ["resolution", "identity_id", "professor_id", "message_sent_at"],
    )
    op.create_index(
        "uq_email_observations_pending_candidate_log",
        "email_observations",
        ["candidate_email_log_id"],
        unique=True,
        sqlite_where=sa.text(
            "candidate_email_log_id IS NOT NULL AND resolution = 'pending'"
        ),
        postgresql_where=sa.text(
            "candidate_email_log_id IS NOT NULL AND resolution = 'pending'"
        ),
    )
    op.create_index(
        "uq_email_observations_pending_candidate_attempt",
        "email_observations",
        ["delivery_attempt_id"],
        unique=True,
        sqlite_where=sa.text(
            "delivery_attempt_id IS NOT NULL "
            "AND candidate_email_log_id IS NULL "
            "AND resolution = 'pending'"
        ),
        postgresql_where=sa.text(
            "delivery_attempt_id IS NOT NULL "
            "AND candidate_email_log_id IS NULL "
            "AND resolution = 'pending'"
        ),
    )


def _legacy_attempt_id(email_log_id: int) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"https://auto-email-sender.local/legacy/email-log/{email_log_id}",
        ),
    )


def _backfill_delivery_attempts(
    bind: sa.engine.Connection, metadata: sa.MetaData
) -> int:
    email_logs = metadata.tables["email_logs"]
    email_tasks = metadata.tables["email_tasks"]
    professors = metadata.tables["professors"]
    attempts = metadata.tables["email_delivery_attempts"]
    rows = list(
        bind.execute(
            sa.select(
                email_logs,
                email_tasks.c.retry_count.label("task_retry_count"),
                professors.c.email.label("professor_email"),
            )
            .join(email_tasks, email_tasks.c.id == email_logs.c.email_task_id)
            .join(professors, professors.c.id == email_logs.c.professor_id)
            .where(
                email_logs.c.direction == "sent",
                email_logs.c.ingest_source != "imap",
                email_logs.c.email_task_id.is_not(None),
            )
            .order_by(email_logs.c.email_task_id, email_logs.c.id),
        ).mappings(),
    )
    inserted = 0
    rows_by_task: dict[int, list[sa.RowMapping]] = defaultdict(list)
    for row in rows:
        rows_by_task[int(row["email_task_id"])].append(row)

    for task_id, task_rows in rows_by_task.items():
        retry_count = max(1, int(task_rows[0]["task_retry_count"] or 1))
        first_attempt_number = max(1, retry_count - len(task_rows) + 1)
        for offset, row in enumerate(task_rows):
            attempt_id = _legacy_attempt_id(int(row["id"]))
            failure_summary = str(row["failure_summary"] or "").strip()
            bind.execute(
                attempts.insert().values(
                    id=attempt_id,
                    email_task_id=task_id,
                    identity_id=int(row["identity_id"]),
                    professor_id=int(row["professor_id"]),
                    attempt_number=first_attempt_number + offset,
                    recipient_email=str(row["professor_email"] or ""),
                    subject_fingerprint=_fingerprint(row["subject"]),
                    content_fingerprint=_fingerprint(row["content"]),
                    app_message_id=row["rfc_message_id"],
                    normalized_app_message_id=_normalize_message_id(
                        row["rfc_message_id"]
                    ),
                    status="failed" if failure_summary else "accepted",
                    started_at=row["created_at"],
                    completed_at=row["created_at"],
                    created_at=row["created_at"],
                ),
            )
            bind.execute(
                email_logs.update()
                .where(email_logs.c.id == row["id"])
                .values(
                    delivery_attempt_id=attempt_id,
                    reconciliation_version=RECONCILIATION_VERSION,
                ),
            )
            inserted += 1
    return inserted


def _legacy_imap_group_key(row: sa.RowMapping) -> tuple[object, ...]:
    message_id = _normalize_message_id(row["rfc_message_id"])
    return (
        int(row["identity_id"]),
        int(row["professor_id"]),
        message_id if message_id else f"legacy-log:{int(row['id'])}",
    )


def _candidate_signature(row: sa.RowMapping) -> tuple[object, ...]:
    return (
        int(row["identity_id"]),
        int(row["professor_id"]),
        _fingerprint(row["subject"]),
    )


def _body_similarity(left: object | None, right: object | None) -> float:
    normalized_left = _normalize_text(left).casefold()
    normalized_right = _normalize_text(right).casefold()
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0
    shorter, longer = sorted((normalized_left, normalized_right), key=len)
    if (
        len(shorter) >= LEGACY_CONTAINMENT_MIN_CHARS
        and len(shorter) / len(longer) >= LEGACY_CONTAINMENT_MIN_RATIO
        and shorter in longer
    ):
        return 0.97
    return SequenceMatcher(
        None, normalized_left, normalized_right, autojunk=False
    ).ratio()


def _select_one_to_one_legacy_matches(
    system_rows: list[sa.RowMapping],
    imap_groups: dict[tuple[object, ...], list[sa.RowMapping]],
) -> dict[tuple[object, ...], sa.RowMapping]:
    systems_by_signature: dict[tuple[object, ...], list[sa.RowMapping]] = defaultdict(
        list
    )
    for system_row in system_rows:
        systems_by_signature[_candidate_signature(system_row)].append(system_row)

    edges: list[tuple[float, float, int, str, tuple[object, ...], sa.RowMapping]] = []
    for group_key, rows in imap_groups.items():
        representative = min(rows, key=lambda row: (row["created_at"], row["id"]))
        for system_row in systems_by_signature.get(
            _candidate_signature(representative), []
        ):
            delta = _time_delta_seconds(
                system_row["created_at"], representative["created_at"]
            )
            if delta > LEGACY_MATCH_MAX_SECONDS:
                continue
            similarity = _body_similarity(
                system_row["content"], representative["content"]
            )
            if similarity < LEGACY_BODY_SIMILARITY:
                continue
            edges.append(
                (
                    -similarity,
                    delta,
                    int(system_row["id"]),
                    str(group_key),
                    group_key,
                    system_row,
                ),
            )

    matches: dict[tuple[object, ...], sa.RowMapping] = {}
    used_system_ids: set[int] = set()
    for _, _, system_id, _, group_key, system_row in sorted(edges):
        if group_key in matches or system_id in used_system_ids:
            continue
        matches[group_key] = system_row
        used_system_ids.add(system_id)
    return matches


def _backfill_observations_and_reconcile(
    bind: sa.engine.Connection,
    metadata: sa.MetaData,
) -> tuple[int, int, int, int]:
    email_logs = metadata.tables["email_logs"]
    observations = metadata.tables["email_observations"]
    system_rows = list(
        bind.execute(
            sa.select(email_logs).where(
                email_logs.c.direction == "sent",
                email_logs.c.ingest_source != "imap",
                email_logs.c.email_task_id.is_not(None),
                sa.func.trim(sa.func.coalesce(email_logs.c.failure_summary, "")) == "",
            ),
        ).mappings(),
    )
    imap_rows = list(
        bind.execute(
            sa.select(email_logs).where(
                email_logs.c.direction == "sent",
                email_logs.c.ingest_source == "imap",
            ),
        ).mappings(),
    )
    imap_groups: dict[tuple[object, ...], list[sa.RowMapping]] = defaultdict(list)
    for row in imap_rows:
        imap_groups[_legacy_imap_group_key(row)].append(row)
    matches = _select_one_to_one_legacy_matches(system_rows, imap_groups)

    observed_at = datetime.now(UTC)
    candidate_count = 0
    external_count = 0
    pending_count = 0
    for group_key, rows in imap_groups.items():
        matched_system = matches.get(group_key)
        candidate_row_id = min(int(row["id"]) for row in rows)
        for row in rows:
            canonical_log_id = None if matched_system is not None else int(row["id"])
            candidate_log_id = (
                int(matched_system["id"])
                if matched_system is not None and int(row["id"]) == candidate_row_id
                else None
            )
            resolution = "pending" if matched_system is not None else "external"
            match_method = (
                "legacy_automatic_fold_v1"
                if matched_system is not None
                else "legacy_external_v1"
            )
            bind.execute(
                observations.insert().values(
                    email_log_id=canonical_log_id,
                    candidate_email_log_id=candidate_log_id,
                    delivery_attempt_id=None,
                    legacy_email_log_id=int(row["id"]),
                    identity_id=int(row["identity_id"]),
                    professor_id=int(row["professor_id"]),
                    direction="sent",
                    source="imap",
                    resolution=resolution,
                    match_method=match_method,
                    delivery_key=None,
                    message_id=row["rfc_message_id"],
                    normalized_message_id=(
                        row["normalized_message_id"]
                        or _normalize_message_id(row["rfc_message_id"])
                    ),
                    folder_role=row["folder_role"],
                    folder=row["folder"],
                    uidvalidity=row["uidvalidity"],
                    imap_uid=row["imap_uid"],
                    from_email=row["from_email"],
                    to_emails=_json_value(row["to_emails"]),
                    cc_emails=_json_value(row["cc_emails"]),
                    bcc_emails=_json_value(row["bcc_emails"]),
                    subject=row["subject"],
                    content=str(row["content"] or ""),
                    content_html=row["content_html"],
                    subject_fingerprint=_fingerprint(row["subject"]),
                    content_fingerprint=_fingerprint(row["content"]),
                    message_sent_at=row["created_at"],
                    observed_at=row["synced_at"] or observed_at,
                    headers=_json_value(row["reply_headers"]),
                    provider_payload=_json_value(row["provider_payload"]),
                ),
            )
            if matched_system is not None:
                # Content and time identify a candidate, not proof that two emails are one.
                bind.execute(
                    email_logs.update()
                    .where(email_logs.c.id == row["id"])
                    .values(
                        record_state="pending",
                        reconciliation_version=RECONCILIATION_VERSION,
                    ),
                )
                candidate_count += 1
                pending_count += 1
            else:
                bind.execute(
                    email_logs.update()
                    .where(email_logs.c.id == row["id"])
                    .values(reconciliation_version=RECONCILIATION_VERSION),
                )
                external_count += 1

    bind.execute(
        email_logs.update()
        .where(email_logs.c.reconciliation_version < RECONCILIATION_VERSION)
        .values(reconciliation_version=RECONCILIATION_VERSION),
    )
    return len(imap_rows), candidate_count, external_count, pending_count


def _record_migration_summary(
    bind: sa.engine.Connection,
    metadata: sa.MetaData,
    *,
    attempt_count: int,
    observation_count: int,
    candidate_count: int,
    external_count: int,
    pending_count: int,
) -> None:
    if "app_metadata" not in metadata.tables:
        return
    app_metadata = metadata.tables["app_metadata"]
    key = "email_reconciliation_v1_summary"
    summary = json.dumps(
        {
            "attempt_count": attempt_count,
            "observation_count": observation_count,
            "matched_count": 0,
            "candidate_count": candidate_count,
            "external_count": external_count,
            "pending_count": pending_count,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    bind.execute(app_metadata.delete().where(app_metadata.c.key == key))
    bind.execute(app_metadata.insert().values(key=key, value=summary))


def _backfill_and_reconcile() -> None:
    bind = op.get_bind()
    available_tables = _table_names()
    metadata = _migration_metadata(
        include_app_metadata="app_metadata" in available_tables
    )
    attempt_count = _backfill_delivery_attempts(bind, metadata)
    (
        observation_count,
        candidate_count,
        external_count,
        pending_count,
    ) = _backfill_observations_and_reconcile(bind, metadata)
    _record_migration_summary(
        bind,
        metadata,
        attempt_count=attempt_count,
        observation_count=observation_count,
        candidate_count=candidate_count,
        external_count=external_count,
        pending_count=pending_count,
    )


def _migration_metadata(*, include_app_metadata: bool) -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "email_logs",
        metadata,
        sa.Column("id", sa.Integer()),
        sa.Column("delivery_attempt_id", sa.String(length=36)),
        sa.Column("merged_into_id", sa.Integer()),
        sa.Column("record_state", sa.String(length=20)),
        sa.Column("reconciliation_version", sa.Integer()),
        sa.Column("email_task_id", sa.Integer()),
        sa.Column("identity_id", sa.Integer()),
        sa.Column("llm_profile_id", sa.Integer()),
        sa.Column("professor_id", sa.Integer()),
        sa.Column("direction", sa.String(length=20)),
        sa.Column("subject", sa.Text()),
        sa.Column("content", sa.Text()),
        sa.Column("content_html", sa.Text()),
        sa.Column("rfc_message_id", sa.String(length=255)),
        sa.Column("ingest_source", sa.String(length=20)),
        sa.Column("folder_role", sa.String(length=20)),
        sa.Column("folder", sa.String(length=255)),
        sa.Column("uidvalidity", sa.Integer()),
        sa.Column("imap_uid", sa.Integer()),
        sa.Column("normalized_message_id", sa.String(length=255)),
        sa.Column("from_email", sa.String(length=255)),
        sa.Column("to_emails", sa.JSON()),
        sa.Column("cc_emails", sa.JSON()),
        sa.Column("bcc_emails", sa.JSON()),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("provider_payload", sa.JSON()),
        sa.Column("failure_summary", sa.Text()),
        sa.Column("reply_headers", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    sa.Table(
        "email_tasks",
        metadata,
        sa.Column("id", sa.Integer()),
        sa.Column("retry_count", sa.Integer()),
    )
    sa.Table(
        "professors",
        metadata,
        sa.Column("id", sa.Integer()),
        sa.Column("email", sa.String(length=255)),
    )
    sa.Table(
        "email_delivery_attempts",
        metadata,
        sa.Column("id", sa.String(length=36)),
        sa.Column("email_task_id", sa.Integer()),
        sa.Column("identity_id", sa.Integer()),
        sa.Column("professor_id", sa.Integer()),
        sa.Column("attempt_number", sa.Integer()),
        sa.Column("recipient_email", sa.String(length=255)),
        sa.Column("subject_fingerprint", sa.String(length=71)),
        sa.Column("content_fingerprint", sa.String(length=71)),
        sa.Column("app_message_id", sa.String(length=255)),
        sa.Column("normalized_app_message_id", sa.String(length=255)),
        sa.Column("status", sa.String(length=20)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    sa.Table(
        "email_observations",
        metadata,
        sa.Column("id", sa.Integer()),
        sa.Column("email_log_id", sa.Integer()),
        sa.Column("candidate_email_log_id", sa.Integer()),
        sa.Column("delivery_attempt_id", sa.String(length=36)),
        sa.Column("legacy_email_log_id", sa.Integer()),
        sa.Column("identity_id", sa.Integer()),
        sa.Column("professor_id", sa.Integer()),
        sa.Column("direction", sa.String(length=20)),
        sa.Column("source", sa.String(length=20)),
        sa.Column("resolution", sa.String(length=20)),
        sa.Column("match_method", sa.String(length=40)),
        sa.Column("delivery_key", sa.String(length=64)),
        sa.Column("message_id", sa.String(length=255)),
        sa.Column("normalized_message_id", sa.String(length=255)),
        sa.Column("folder_role", sa.String(length=20)),
        sa.Column("folder", sa.String(length=255)),
        sa.Column("uidvalidity", sa.Integer()),
        sa.Column("imap_uid", sa.Integer()),
        sa.Column("from_email", sa.String(length=255)),
        sa.Column("to_emails", sa.JSON()),
        sa.Column("cc_emails", sa.JSON()),
        sa.Column("bcc_emails", sa.JSON()),
        sa.Column("subject", sa.Text()),
        sa.Column("content", sa.Text()),
        sa.Column("content_html", sa.Text()),
        sa.Column("subject_fingerprint", sa.String(length=71)),
        sa.Column("content_fingerprint", sa.String(length=71)),
        sa.Column("message_sent_at", sa.DateTime(timezone=True)),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("headers", sa.JSON()),
        sa.Column("provider_payload", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    if include_app_metadata:
        sa.Table(
            "app_metadata",
            metadata,
            sa.Column("key", sa.Text()),
            sa.Column("value", sa.Text()),
        )
    return metadata


def upgrade() -> None:
    _create_delivery_attempts_table()
    _add_email_log_reconciliation_columns()
    _create_email_observations_table()
    _backfill_and_reconcile()


def downgrade() -> None:
    tables = _table_names()
    if "app_metadata" in tables:
        app_metadata = sa.table(
            "app_metadata",
            sa.column("key", sa.Text()),
        )
        op.get_bind().execute(
            app_metadata.delete().where(
                app_metadata.c.key == "email_reconciliation_v1_summary",
            ),
        )
    if "email_observations" in tables:
        op.drop_table("email_observations")

    if "email_logs" in tables:
        columns = _column_names("email_logs")
        indexes = _index_names("email_logs")
        if "uq_email_logs_delivery_attempt_id" in indexes:
            op.drop_index("uq_email_logs_delivery_attempt_id", table_name="email_logs")
        if "ix_email_logs_record_state_identity_direction_created" in indexes:
            op.drop_index(
                "ix_email_logs_record_state_identity_direction_created",
                table_name="email_logs",
            )
        with op.batch_alter_table("email_logs") as batch_op:
            if "delivery_attempt_id" in columns:
                batch_op.drop_constraint(
                    "fk_email_logs_delivery_attempt_id_email_delivery_attempts",
                    type_="foreignkey",
                )
                batch_op.drop_column("delivery_attempt_id")
            if "merged_into_id" in columns:
                batch_op.drop_constraint(
                    "fk_email_logs_merged_into_id_email_logs",
                    type_="foreignkey",
                )
                batch_op.drop_column("merged_into_id")
            if "record_state" in columns:
                batch_op.drop_column("record_state")
            if "reconciliation_version" in columns:
                batch_op.drop_column("reconciliation_version")

    if "email_delivery_attempts" in tables:
        op.drop_table("email_delivery_attempts")
