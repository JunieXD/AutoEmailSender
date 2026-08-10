#!/usr/bin/env python3
"""Safely validate and summarize one or more local Beta diagnostic bundles."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import stat
import sys
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Iterator, Sequence
from uuid import UUID
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo


SCHEMA_VERSION = 1
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_ENTRY_BYTES = 82 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 96 * 1024 * 1024
MAX_COMPRESSION_RATIO = 250.0
MAX_JSONL_RECORD_BYTES = 64 * 1024
MAX_JSONL_RECORDS = 250_000
MAX_FORBIDDEN_TOKEN_FILE_BYTES = 64 * 1024
MAX_FORBIDDEN_TOKENS = 128

COMPONENTS = ("electron", "api", "worker", "combined")
MODES = ("combined", "split")
WORKLOAD_KINDS = (
    "dispatcher",
    "imap_sync",
    "imap_history",
    "batch_draft",
    "matching",
    "crawler",
)
RESOURCE_METRICS = (
    "cpu_percent",
    "rss_bytes",
    "handles_or_fds",
    "threads",
    "child_processes",
    "playwright_processes",
    "database_bytes",
    "wal_bytes",
    "shm_bytes",
    "logs_bytes",
    "runtime_bytes",
)
EXPECTED_ENTRIES = frozenset(
    {
        "manifest.json",
        "timeline.jsonl",
        "resource-samples.jsonl",
        "workload-summary.json",
        "database-health.json",
        "logs/operation-summary.json",
        "logs/electron.jsonl",
        "logs/api.jsonl",
        "logs/worker.jsonl",
        "logs/combined.jsonl",
        "logs/startup-summary.jsonl",
        "logs/backend-errors-summary.jsonl",
        "summary.json",
        "README.txt",
        "checksums.sha256",
    }
)
ENTRY_SIZE_LIMITS = {
    "manifest.json": 128 * 1024,
    "workload-summary.json": 2 * 1024 * 1024,
    "database-health.json": 2 * 1024 * 1024,
    "logs/operation-summary.json": 2 * 1024 * 1024,
    "summary.json": 2 * 1024 * 1024,
    "README.txt": 128 * 1024,
    "checksums.sha256": 16 * 1024,
}
CHECKSUM_PATTERN = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9._/-]+)$")
SAFE_ENTRY_PATTERN = re.compile(r"^[A-Za-z0-9._/-]{1,240}$")
SAFE_EVENT_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")
SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_.:+/-]{1,160}$")


class BundleValidationError(ValueError):
    """A stable, user-actionable validation failure."""

    def __init__(self, code: str, message: str, bundle_name: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.bundle_name = bundle_name

    def with_bundle(self, bundle_name: str) -> BundleValidationError:
        if self.bundle_name:
            return self
        return BundleValidationError(self.code, str(self), bundle_name)


@dataclass(frozen=True)
class ValidatedBundle:
    bundle_name: str
    archive_sha256: str
    manifest: dict[str, object]
    timeline: list[dict[str, object]]
    resources: list[dict[str, object]]
    workload_summary: dict[str, object]
    database_health: dict[str, object]
    operation_summary: dict[str, object]
    summary: dict[str, object]


def analyze_bundles(
    paths: Sequence[Path],
    *,
    forbidden_tokens: Sequence[bytes] = (),
) -> dict[str, object]:
    if not paths:
        raise BundleValidationError("no_input", "At least one diagnostic ZIP is required.")
    normalized_tokens = _normalize_forbidden_tokens(forbidden_tokens)
    bundles = [validate_bundle(path, forbidden_tokens=normalized_tokens) for path in paths]
    bundle_reports = [_build_bundle_report(bundle) for bundle in bundles]
    alerts = [
        {**alert, "bundle": report["bundle_name"]}
        for report in bundle_reports
        for alert in _require_list(report, "alerts")
    ]
    duplicate_report_ids = sorted(
        report_id
        for report_id, count in Counter(
            str(report["report_id"]) for report in bundle_reports
        ).items()
        if count > 1
    )
    if duplicate_report_ids:
        alerts.append(
            {
                "severity": "warning",
                "code": "duplicate_report_id",
                "message": "The batch contains duplicate report IDs.",
                "report_ids": duplicate_report_ids,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(),
        "bundle_count": len(bundle_reports),
        "bundles": bundle_reports,
        "aggregate": _build_aggregate(bundle_reports),
        "alerts": alerts,
    }


def validate_bundle(
    path: Path,
    *,
    forbidden_tokens: Sequence[bytes] = (),
) -> ValidatedBundle:
    bundle_name = path.name or "diagnostics.zip"
    try:
        with _open_regular_archive(path) as (source, archive_sha256):
            try:
                with ZipFile(source, mode="r") as archive:
                    contents = _read_validated_entries(archive)
            except BadZipFile as error:
                raise BundleValidationError("invalid_zip", "The ZIP structure or CRC is invalid.") from error
        _verify_checksums(contents)
        _assert_forbidden_tokens_absent(contents, _normalize_forbidden_tokens(forbidden_tokens))

        manifest = _load_json_object(contents, "manifest.json")
        summary = _load_json_object(contents, "summary.json")
        workload_summary = _load_json_object(contents, "workload-summary.json")
        database_health = _load_json_object(contents, "database-health.json")
        operation_summary = _load_json_object(contents, "logs/operation-summary.json")
        timeline = _load_jsonl(contents, "timeline.jsonl")
        resources = _load_jsonl(contents, "resource-samples.jsonl")

        _validate_manifest(manifest)
        _validate_timeline(timeline)
        _validate_resources(resources)
        _validate_workload_summary(workload_summary)
        _validate_database_health(database_health)
        _validate_operation_summary(operation_summary)
        _validate_summary(summary)
        _validate_component_logs(contents, timeline)
        source_log_count = _validate_classified_logs(contents)
        _validate_cross_entry_contract(
            manifest=manifest,
            summary=summary,
            timeline=timeline,
            resources=resources,
            source_log_count=source_log_count,
        )
        contents["README.txt"].decode("utf-8", errors="strict")
        return ValidatedBundle(
            bundle_name=bundle_name,
            archive_sha256=archive_sha256,
            manifest=manifest,
            timeline=timeline,
            resources=resources,
            workload_summary=workload_summary,
            database_health=database_health,
            operation_summary=operation_summary,
            summary=summary,
        )
    except BundleValidationError as error:
        raise error.with_bundle(bundle_name) from error
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError) as error:
        raise BundleValidationError(
            "unreadable_bundle",
            "The diagnostic bundle could not be read safely.",
            bundle_name,
        ) from error


@contextmanager
def _open_regular_archive(path: Path) -> Iterator[tuple[BinaryIO, str]]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise BundleValidationError(
            "archive_open_failed",
            "The input must be a readable, non-symlink regular file.",
            path.name,
        ) from error
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise BundleValidationError(
                "archive_not_regular",
                "The input must be a regular file.",
                path.name,
            )
        if file_stat.st_size <= 0 or file_stat.st_size > MAX_ARCHIVE_BYTES:
            raise BundleValidationError(
                "archive_size_limit",
                f"The compressed ZIP must be between 1 byte and {MAX_ARCHIVE_BYTES} bytes.",
                path.name,
            )
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            archive_hash = hashlib.sha256()
            while chunk := source.read(1024 * 1024):
                archive_hash.update(chunk)
            source.seek(0)
            yield source, archive_hash.hexdigest()
    finally:
        os.close(descriptor)


def _read_validated_entries(archive: ZipFile) -> dict[str, bytes]:
    if archive.comment:
        raise BundleValidationError("zip_comment", "ZIP comments are not allowed.")
    infos = archive.infolist()
    if len(infos) != len(EXPECTED_ENTRIES):
        raise BundleValidationError(
            "entry_set",
            "The ZIP does not contain the exact schema-v1 entry set.",
        )
    names: set[str] = set()
    declared_total = 0
    compressed_total = 0
    for info in infos:
        _validate_entry_info(info)
        if info.filename in names:
            raise BundleValidationError("duplicate_entry", "Duplicate ZIP entries are not allowed.")
        names.add(info.filename)
        declared_total += info.file_size
        compressed_total += info.compress_size
    if names != EXPECTED_ENTRIES:
        raise BundleValidationError(
            "entry_set",
            "The ZIP does not contain the exact schema-v1 entry set.",
        )
    if declared_total > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise BundleValidationError("zip_bomb", "The ZIP exceeds the uncompressed size limit.")
    if declared_total / max(compressed_total, 1) > MAX_COMPRESSION_RATIO:
        raise BundleValidationError("zip_bomb", "The ZIP exceeds the compression-ratio limit.")

    contents: dict[str, bytes] = {}
    actual_total = 0
    for info in sorted(infos, key=lambda item: item.filename):
        content = _read_member_bounded(archive, info)
        actual_total += len(content)
        if actual_total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise BundleValidationError("zip_bomb", "The ZIP expands beyond its total size limit.")
        contents[info.filename] = content
    return contents


def _validate_entry_info(info: ZipInfo) -> None:
    name = info.filename
    parts = name.split("/")
    if (
        not SAFE_ENTRY_PATTERN.fullmatch(name)
        or name.startswith("/")
        or "\\" in name
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise BundleValidationError("unsafe_entry_path", "The ZIP contains an unsafe entry path.")
    if info.is_dir():
        raise BundleValidationError("unexpected_directory", "Directory entries are not allowed.")
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(unix_mode)
    if file_type not in {0, stat.S_IFREG}:
        raise BundleValidationError("special_entry", "Symlinks and special-file entries are not allowed.")
    if info.flag_bits & 0x1:
        raise BundleValidationError("encrypted_entry", "Encrypted ZIP entries are not allowed.")
    if info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
        raise BundleValidationError("compression_method", "The ZIP uses an unsupported compression method.")
    if info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO:
        raise BundleValidationError("zip_bomb", "A ZIP entry exceeds the compression-ratio limit.")
    entry_limit = ENTRY_SIZE_LIMITS.get(info.filename, MAX_ENTRY_BYTES)
    if info.file_size < 0 or info.file_size > entry_limit:
        raise BundleValidationError("entry_size_limit", "A ZIP entry exceeds its size limit.")


def _read_member_bounded(archive: ZipFile, info: ZipInfo) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        with archive.open(info, mode="r") as member:
            while chunk := member.read(64 * 1024):
                total += len(chunk)
                if total > MAX_ENTRY_BYTES or total > info.file_size:
                    raise BundleValidationError(
                        "entry_size_mismatch",
                        "A ZIP entry expanded beyond its declared size.",
                    )
                chunks.append(chunk)
    except BadZipFile as error:
        raise BundleValidationError("invalid_zip", "A ZIP entry failed CRC validation.") from error
    if total != info.file_size:
        raise BundleValidationError(
            "entry_size_mismatch",
            "A ZIP entry does not match its declared size.",
        )
    return b"".join(chunks)


def _verify_checksums(contents: dict[str, bytes]) -> None:
    try:
        checksum_text = contents["checksums.sha256"].decode("ascii", errors="strict")
    except UnicodeError as error:
        raise BundleValidationError("checksum_format", "checksums.sha256 is not ASCII.") from error
    expected_names = EXPECTED_ENTRIES - {"checksums.sha256"}
    checksums: dict[str, str] = {}
    for line in checksum_text.splitlines():
        match = CHECKSUM_PATTERN.fullmatch(line)
        if match is None or match.group(2) in checksums:
            raise BundleValidationError("checksum_format", "checksums.sha256 has an invalid line.")
        checksums[match.group(2)] = match.group(1)
    if set(checksums) != expected_names:
        raise BundleValidationError(
            "checksum_set",
            "checksums.sha256 must cover every entry except itself exactly once.",
        )
    for name in sorted(expected_names):
        actual = hashlib.sha256(contents[name]).hexdigest()
        if not hmac.compare_digest(actual, checksums[name]):
            raise BundleValidationError("checksum_mismatch", f"Checksum mismatch for {name}.")


def _normalize_forbidden_tokens(tokens: Sequence[bytes]) -> tuple[bytes, ...]:
    normalized = tuple(dict.fromkeys(token for token in tokens if token))
    if len(normalized) > MAX_FORBIDDEN_TOKENS or any(len(token) > 1024 for token in normalized):
        raise BundleValidationError(
            "forbidden_token_limit",
            "The forbidden-token list exceeds its count or token-size limit.",
        )
    return normalized


def _assert_forbidden_tokens_absent(
    contents: dict[str, bytes],
    tokens: Sequence[bytes],
) -> None:
    for index, token in enumerate(tokens, start=1):
        for name, content in contents.items():
            if token in content:
                raise BundleValidationError(
                    "forbidden_token_found",
                    f"Forbidden token #{index} was found in {name}.",
                )


def _load_json_object(contents: dict[str, bytes], name: str) -> dict[str, object]:
    try:
        value = json.loads(contents[name].decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise BundleValidationError("invalid_json", f"{name} is not valid UTF-8 JSON.") from error
    if not isinstance(value, dict):
        raise BundleValidationError("invalid_schema", f"{name} must contain a JSON object.")
    _require_schema_version(value, name)
    return value


def _load_jsonl(contents: dict[str, bytes], name: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(contents[name].splitlines(), start=1):
        if not raw_line.strip():
            continue
        if len(raw_line) > MAX_JSONL_RECORD_BYTES or len(records) >= MAX_JSONL_RECORDS:
            raise BundleValidationError("jsonl_limit", f"{name} exceeds its record limits.")
        try:
            value = json.loads(raw_line.decode("utf-8", errors="strict"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise BundleValidationError(
                "invalid_jsonl",
                f"{name} contains invalid JSON at line {line_number}.",
            ) from error
        if not isinstance(value, dict):
            raise BundleValidationError(
                "invalid_schema",
                f"{name} line {line_number} must be an object.",
            )
        _require_schema_version(value, f"{name} line {line_number}")
        records.append(value)
    return records


def _require_schema_version(value: dict[str, object], source: str) -> None:
    if value.get("schema_version") != SCHEMA_VERSION:
        raise BundleValidationError(
            "unknown_schema",
            f"{source} does not use supported schema version {SCHEMA_VERSION}.",
        )


def _validate_manifest(manifest: dict[str, object]) -> None:
    for field in ("report_id", "installation_id"):
        raw = manifest.get(field)
        try:
            UUID(str(raw))
        except (ValueError, TypeError) as error:
            raise BundleValidationError("invalid_manifest", f"manifest.{field} is invalid.") from error
    _require_iso_timestamp(manifest.get("exported_at"), "manifest.exported_at")
    if not isinstance(manifest.get("partial"), bool):
        raise BundleValidationError("invalid_manifest", "manifest.partial must be boolean.")
    missing = manifest.get("missing_sections")
    if not isinstance(missing, list) or not all(
        isinstance(item, str) and 0 < len(item) <= 160 for item in missing
    ):
        raise BundleValidationError("invalid_manifest", "manifest.missing_sections is invalid.")
    app = _require_mapping(manifest, "app")
    system = _require_mapping(manifest, "system")
    backend = _require_mapping(manifest, "backend")
    counts = _require_mapping(manifest, "record_counts")
    if app.get("name") != "Auto Email Sender":
        raise BundleValidationError("invalid_manifest", "manifest.app.name is invalid.")
    if not _is_safe_string(app.get("version"), 80):
        raise BundleValidationError("invalid_manifest", "manifest.app.version is invalid.")
    if app.get("channel") not in {"stable", "alpha", "beta", "rc", "unknown"}:
        raise BundleValidationError("invalid_manifest", "manifest.app.channel is invalid.")
    if not _is_safe_string(system.get("platform"), 32) or not _is_safe_string(
        system.get("arch"), 32
    ):
        raise BundleValidationError("invalid_manifest", "manifest.system is invalid.")
    if backend.get("requested_mode") not in MODES or backend.get("effective_mode") not in MODES:
        raise BundleValidationError("invalid_manifest", "manifest.backend mode is invalid.")
    for field in ("timeline", "resource_samples", "source_log_summaries"):
        _require_nonnegative_int(counts.get(field), f"manifest.record_counts.{field}")


def _validate_timeline(records: list[dict[str, object]]) -> None:
    for record in records:
        if record.get("stream") != "timeline" or record.get("component") not in COMPONENTS:
            raise BundleValidationError("invalid_timeline", "timeline.jsonl has an invalid stream/component.")
        _require_iso_timestamp(record.get("wall_time"), "timeline.wall_time")
        _require_nonnegative_number(record.get("monotonic_ms"), "timeline.monotonic_ms")
        if not isinstance(record.get("session_id"), str) or not SAFE_IDENTIFIER_PATTERN.fullmatch(
            str(record["session_id"])
        ):
            raise BundleValidationError("invalid_timeline", "timeline.session_id is invalid.")
        if not isinstance(record.get("event"), str) or not SAFE_EVENT_PATTERN.fullmatch(
            str(record["event"])
        ):
            raise BundleValidationError("invalid_timeline", "timeline.event is invalid.")
        if record.get("severity") not in {"debug", "info", "warning", "error"}:
            raise BundleValidationError("invalid_timeline", "timeline.severity is invalid.")
        details = record.get("details")
        if not isinstance(details, dict) or any(not _is_json_scalar(value) for value in details.values()):
            raise BundleValidationError("invalid_timeline", "timeline.details is invalid.")


def _validate_resources(records: list[dict[str, object]]) -> None:
    for record in records:
        if record.get("stream") != "resource-samples" or record.get("component") not in COMPONENTS:
            raise BundleValidationError("invalid_resources", "resource-samples has an invalid stream/component.")
        _require_iso_timestamp(record.get("wall_time"), "resource.wall_time")
        _require_nonnegative_number(record.get("monotonic_ms"), "resource.monotonic_ms")
        if not isinstance(record.get("session_id"), str) or not SAFE_IDENTIFIER_PATTERN.fullmatch(
            str(record["session_id"])
        ):
            raise BundleValidationError("invalid_resources", "resource.session_id is invalid.")
        for metric in RESOURCE_METRICS:
            if metric in record:
                value = record[metric]
                _require_nonnegative_number(value, f"resource.{metric}")


def _validate_workload_summary(summary: dict[str, object]) -> None:
    if summary.get("available") is False:
        return
    workloads = summary.get("workloads")
    invariants = summary.get("invariants")
    if not isinstance(workloads, list) or not isinstance(invariants, dict):
        raise BundleValidationError("invalid_workloads", "workload-summary.json is incomplete.")
    kinds: list[str] = []
    for workload in workloads:
        if not isinstance(workload, dict) or workload.get("kind") not in WORKLOAD_KINDS:
            raise BundleValidationError("invalid_workloads", "A workload summary item is invalid.")
        kinds.append(str(workload["kind"]))
        for field in ("queued", "running", "succeeded", "failed", "interrupted", "recovered"):
            _require_nonnegative_int(workload.get(field), f"workload.{field}")
        for field in (
            "oldest_queue_age_seconds",
            "oldest_running_age_seconds",
            "average_duration_seconds",
            "maximum_duration_seconds",
        ):
            if workload.get(field) is not None:
                _require_nonnegative_number(workload.get(field), f"workload.{field}")
    if sorted(kinds) != sorted(WORKLOAD_KINDS):
        raise BundleValidationError("invalid_workloads", "All six workload kinds must appear exactly once.")
    for field in (
        "sending_count",
        "duplicate_delivery_attempt_groups",
        "orphaned_claim_count",
    ):
        _require_nonnegative_int(invariants.get(field), f"workload.invariants.{field}")


def _validate_database_health(health: dict[str, object]) -> None:
    if health.get("available") is False:
        return
    if health.get("integrity_check") not in {"ok", "error", "unknown"}:
        raise BundleValidationError("invalid_database_health", "database integrity status is invalid.")
    for field in (
        "foreign_key_violation_count",
        "busy_timeout_ms",
        "database_bytes",
        "wal_bytes",
        "shm_bytes",
        "backup_count",
        "lock_errors_1h",
        "busy_errors_1h",
        "slow_queries_1h",
    ):
        _require_nonnegative_int(health.get(field), f"database-health.{field}")
    _require_nonnegative_number(health.get("maximum_query_ms_1h"), "database-health.maximum_query_ms_1h")


def _validate_operation_summary(summary: dict[str, object]) -> None:
    if summary.get("available") is False:
        return
    for field in ("total_1h", "total_24h"):
        _require_nonnegative_int(summary.get(field), f"operation-summary.{field}")
    if not isinstance(summary.get("levels_24h"), dict) or not isinstance(
        summary.get("categories_24h"), list
    ):
        raise BundleValidationError("invalid_operation_summary", "operation-summary.json is incomplete.")


def _validate_summary(summary: dict[str, object]) -> None:
    if not isinstance(summary.get("partial"), bool):
        raise BundleValidationError("invalid_summary", "summary.partial must be boolean.")
    for field in ("timeline_records", "resource_samples"):
        _require_nonnegative_int(summary.get(field), f"summary.{field}")
    missing_sections = summary.get("missing_sections")
    if not isinstance(missing_sections, list) or not all(
        isinstance(item, str) and 0 < len(item) <= 160 for item in missing_sections
    ):
        raise BundleValidationError("invalid_summary", "summary.missing_sections is invalid.")
    if not isinstance(summary.get("lifecycle_event_counts"), dict) or not isinstance(
        summary.get("resource_peaks"), dict
    ):
        raise BundleValidationError("invalid_summary", "summary.json is incomplete.")


def _validate_component_logs(
    contents: dict[str, bytes],
    timeline: list[dict[str, object]],
) -> None:
    for component in COMPONENTS:
        name = f"logs/{component}.jsonl"
        component_records = _load_jsonl(contents, name)
        _validate_timeline(component_records)
        expected = [record for record in timeline if record.get("component") == component]
        if component_records != expected:
            raise BundleValidationError(
                "component_log_mismatch",
                f"{name} does not match the component subset of timeline.jsonl.",
            )


def _validate_classified_logs(contents: dict[str, bytes]) -> int:
    total = 0
    for name in ("logs/startup-summary.jsonl", "logs/backend-errors-summary.jsonl"):
        records = _load_jsonl(contents, name)
        total += len(records)
        for record in records:
            if not isinstance(record.get("category"), str):
                raise BundleValidationError("invalid_classified_log", f"{name} category is invalid.")
    return total


def _validate_cross_entry_contract(
    *,
    manifest: dict[str, object],
    summary: dict[str, object],
    timeline: list[dict[str, object]],
    resources: list[dict[str, object]],
    source_log_count: int,
) -> None:
    counts = _require_mapping(manifest, "record_counts")
    if counts.get("timeline") != len(timeline) or counts.get("resource_samples") != len(resources):
        raise BundleValidationError("record_count_mismatch", "Manifest record counts do not match JSONL data.")
    if counts.get("source_log_summaries") != source_log_count:
        raise BundleValidationError(
            "record_count_mismatch",
            "Manifest source-log count does not match classified logs.",
        )
    if summary.get("timeline_records") != len(timeline) or summary.get("resource_samples") != len(resources):
        raise BundleValidationError("record_count_mismatch", "Summary record counts do not match JSONL data.")
    if manifest.get("partial") != summary.get("partial"):
        raise BundleValidationError("partial_mismatch", "Manifest and summary partial states differ.")
    if manifest.get("missing_sections") != summary.get("missing_sections"):
        raise BundleValidationError("partial_mismatch", "Manifest and summary missing sections differ.")


def _build_bundle_report(bundle: ValidatedBundle) -> dict[str, object]:
    app = _require_mapping(bundle.manifest, "app")
    system = _require_mapping(bundle.manifest, "system")
    backend = _require_mapping(bundle.manifest, "backend")
    event_counts = Counter(str(record["event"]) for record in bundle.timeline)
    restart_events = sum(count for event, count in event_counts.items() if "restart" in event)
    exit_events = sum(count for event, count in event_counts.items() if "exit" in event)
    unexpected_exits = sum(
        count
        for event, count in event_counts.items()
        if "crash" in event or "unexpected" in event
    )
    timeline_lock_events = sum(
        count
        for event, count in event_counts.items()
        if "sqlite_lock" in event or "sqlite_busy" in event
    )
    workloads, invariants = _workload_report(bundle.workload_summary)
    database = _database_report(bundle.database_health)
    resource_trends = _resource_trends(bundle.resources)
    report: dict[str, object] = {
        "bundle_name": bundle.bundle_name,
        "archive_sha256": bundle.archive_sha256,
        "report_id": bundle.manifest["report_id"],
        "installation_id": bundle.manifest["installation_id"],
        "partial": bundle.manifest["partial"],
        "missing_sections": bundle.manifest["missing_sections"],
        "version": app["version"],
        "channel": app["channel"],
        "platform": system["platform"],
        "arch": system["arch"],
        "requested_mode": backend["requested_mode"],
        "effective_mode": backend["effective_mode"],
        "record_counts": {
            "timeline": len(bundle.timeline),
            "resource_samples": len(bundle.resources),
        },
        "lifecycle": {
            "restart_events": restart_events,
            "exit_events": exit_events,
            "unexpected_exit_events": unexpected_exits,
            "event_counts": dict(sorted(event_counts.items())),
        },
        "sqlite": {
            "timeline_lock_events": timeline_lock_events,
            "lock_errors_1h": database.get("lock_errors_1h"),
            "busy_errors_1h": database.get("busy_errors_1h"),
            "slow_queries_1h": database.get("slow_queries_1h"),
            "maximum_query_ms_1h": database.get("maximum_query_ms_1h"),
        },
        "workloads": workloads,
        "invariants": invariants,
        "database": database,
        "resource_trends": resource_trends,
    }
    report["alerts"] = _build_alerts(report)
    return report


def _workload_report(summary: dict[str, object]) -> tuple[list[dict[str, object]], dict[str, int]]:
    if summary.get("available") is False:
        return [], {}
    workloads = summary.get("workloads")
    invariants = summary.get("invariants")
    assert isinstance(workloads, list)
    assert isinstance(invariants, dict)
    return [dict(item) for item in workloads if isinstance(item, dict)], {
        field: int(invariants[field])
        for field in (
            "sending_count",
            "duplicate_delivery_attempt_groups",
            "orphaned_claim_count",
        )
    }


def _database_report(health: dict[str, object]) -> dict[str, object]:
    if health.get("available") is False:
        return {"available": False, "reason": health.get("reason")}
    fields = (
        "available",
        "alembic_revision",
        "integrity_check",
        "foreign_key_violation_count",
        "journal_mode",
        "database_bytes",
        "wal_bytes",
        "shm_bytes",
        "backup_count",
        "newest_backup_age_seconds",
        "lock_errors_1h",
        "busy_errors_1h",
        "slow_queries_1h",
        "maximum_query_ms_1h",
    )
    return {field: health.get(field) for field in fields}


def _resource_trends(records: list[dict[str, object]]) -> dict[str, object]:
    def summarize(selected: list[dict[str, object]]) -> dict[str, object]:
        metrics: dict[str, object] = {}
        for metric in RESOURCE_METRICS:
            values = [
                float(record[metric])
                for record in selected
                if isinstance(record.get(metric), (int, float))
                and not isinstance(record.get(metric), bool)
            ]
            if values:
                metrics[metric] = {
                    "samples": len(values),
                    "first": values[0],
                    "last": values[-1],
                    "minimum": min(values),
                    "peak": max(values),
                    "change": values[-1] - values[0],
                }
        return metrics

    ordered = sorted(
        records,
        key=lambda item: (
            str(item.get("wall_time", "")),
            float(item.get("monotonic_ms", 0)),
        ),
    )
    return {
        "overall": summarize(ordered),
        "by_component": {
            component: summarize(
                [record for record in ordered if record.get("component") == component]
            )
            for component in COMPONENTS
        },
    }


def _build_alerts(report: dict[str, object]) -> list[dict[str, object]]:
    alerts: list[dict[str, object]] = []
    if report["partial"]:
        alerts.append(
            {
                "severity": "warning",
                "code": "partial_bundle",
                "message": "The bundle is partial; inspect missing_sections before drawing conclusions.",
            }
        )
    if report["requested_mode"] != report["effective_mode"]:
        alerts.append(
            {
                "severity": "warning",
                "code": "mode_mismatch",
                "message": "Requested and effective backend modes differ.",
            }
        )
    database = _require_mapping(report, "database")
    if database.get("available") is not False:
        if database.get("integrity_check") != "ok" or int(
            database.get("foreign_key_violation_count") or 0
        ) > 0:
            alerts.append(
                {
                    "severity": "critical",
                    "code": "database_integrity",
                    "message": "Database integrity or foreign-key checks are not clean.",
                }
            )
        if int(database.get("lock_errors_1h") or 0) + int(database.get("busy_errors_1h") or 0) > 0:
            alerts.append(
                {
                    "severity": "warning",
                    "code": "sqlite_contention",
                    "message": "SQLite lock/busy events occurred during the last hour.",
                }
            )
    invariants = _require_mapping(report, "invariants")
    if int(invariants.get("duplicate_delivery_attempt_groups") or 0) > 0:
        alerts.append(
            {
                "severity": "critical",
                "code": "duplicate_delivery_attempt",
                "message": "Duplicate accepted-delivery groups were detected.",
            }
        )
    if int(invariants.get("orphaned_claim_count") or 0) > 0:
        alerts.append(
            {
                "severity": "critical",
                "code": "orphaned_claim",
                "message": "Orphaned workload claims were detected.",
            }
        )
    for workload in _require_list(report, "workloads"):
        if not isinstance(workload, dict):
            continue
        queue_age = float(workload.get("oldest_queue_age_seconds") or 0)
        running_age = float(workload.get("oldest_running_age_seconds") or 0)
        if int(workload.get("queued") or 0) > 0 and queue_age >= 300:
            alerts.append(
                {
                    "severity": "warning",
                    "code": "old_queue_backlog",
                    "workload": workload.get("kind"),
                    "message": "A workload queue has been waiting for at least five minutes.",
                }
            )
        if int(workload.get("running") or 0) > 0 and running_age >= 900:
            alerts.append(
                {
                    "severity": "warning",
                    "code": "long_running_workload",
                    "workload": workload.get("kind"),
                    "message": "A workload has been running for at least fifteen minutes.",
                }
            )
    lifecycle = _require_mapping(report, "lifecycle")
    if int(lifecycle.get("unexpected_exit_events") or 0) > 0:
        alerts.append(
            {
                "severity": "warning",
                "code": "unexpected_exit",
                "message": "Unexpected exit/crash events were recorded.",
            }
        )
    return alerts


def _build_aggregate(reports: list[dict[str, object]]) -> dict[str, object]:
    effective_modes = Counter(str(report["effective_mode"]) for report in reports)
    requested_modes = Counter(str(report["requested_mode"]) for report in reports)
    platforms = Counter(str(report["platform"]) for report in reports)
    versions = Counter(str(report["version"]) for report in reports)
    totals = {
        "restart_events": 0,
        "unexpected_exit_events": 0,
        "timeline_lock_events": 0,
        "lock_errors_1h": 0,
        "busy_errors_1h": 0,
        "duplicate_delivery_attempt_groups": 0,
        "orphaned_claim_count": 0,
    }
    resource_peaks: dict[str, float] = {}
    for report in reports:
        lifecycle = _require_mapping(report, "lifecycle")
        sqlite = _require_mapping(report, "sqlite")
        invariants = _require_mapping(report, "invariants")
        totals["restart_events"] += int(lifecycle.get("restart_events") or 0)
        totals["unexpected_exit_events"] += int(lifecycle.get("unexpected_exit_events") or 0)
        totals["timeline_lock_events"] += int(sqlite.get("timeline_lock_events") or 0)
        totals["lock_errors_1h"] += int(sqlite.get("lock_errors_1h") or 0)
        totals["busy_errors_1h"] += int(sqlite.get("busy_errors_1h") or 0)
        totals["duplicate_delivery_attempt_groups"] += int(
            invariants.get("duplicate_delivery_attempt_groups") or 0
        )
        totals["orphaned_claim_count"] += int(invariants.get("orphaned_claim_count") or 0)
        trends = _require_mapping(_require_mapping(report, "resource_trends"), "overall")
        for metric, raw in trends.items():
            if isinstance(raw, dict) and isinstance(raw.get("peak"), (int, float)):
                resource_peaks[metric] = max(resource_peaks.get(metric, 0), float(raw["peak"]))
    return {
        "installation_count": len({str(report["installation_id"]) for report in reports}),
        "partial_bundle_count": sum(bool(report["partial"]) for report in reports),
        "requested_modes": dict(sorted(requested_modes.items())),
        "effective_modes": dict(sorted(effective_modes.items())),
        "platforms": dict(sorted(platforms.items())),
        "versions": dict(sorted(versions.items())),
        "totals": totals,
        "resource_peaks": dict(sorted(resource_peaks.items())),
    }


def _require_mapping(value: dict[str, object], field: str) -> dict[str, object]:
    candidate = value.get(field)
    if not isinstance(candidate, dict):
        raise BundleValidationError("invalid_schema", f"{field} must be an object.")
    return candidate


def _require_list(value: dict[str, object], field: str) -> list[object]:
    candidate = value.get(field)
    if not isinstance(candidate, list):
        raise BundleValidationError("invalid_schema", f"{field} must be a list.")
    return candidate


def _require_iso_timestamp(value: object, field: str) -> None:
    if not isinstance(value, str):
        raise BundleValidationError("invalid_schema", f"{field} must be an ISO timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise BundleValidationError("invalid_schema", f"{field} must be an ISO timestamp.") from error
    if parsed.tzinfo is None:
        raise BundleValidationError("invalid_schema", f"{field} must include a timezone.")


def _require_finite_number(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BundleValidationError("invalid_schema", f"{field} must be finite.")


def _require_nonnegative_number(value: object, field: str) -> None:
    _require_finite_number(value, field)
    if float(value) < 0:
        raise BundleValidationError("invalid_schema", f"{field} must be nonnegative.")


def _require_nonnegative_int(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BundleValidationError("invalid_schema", f"{field} must be a nonnegative integer.")


def _is_safe_string(value: object, maximum: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= maximum and all(
        character.isprintable() for character in value
    )


def _is_json_scalar(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _read_forbidden_token_files(paths: Sequence[Path]) -> tuple[bytes, ...]:
    tokens: list[bytes] = []
    for path in paths:
        data = _read_small_regular_file(path, MAX_FORBIDDEN_TOKEN_FILE_BYTES)
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeError as error:
            raise BundleValidationError(
                "forbidden_token_file",
                "Forbidden-token files must be UTF-8.",
                path.name,
            ) from error
        tokens.extend(line.encode("utf-8") for line in text.splitlines() if line)
    return _normalize_forbidden_tokens(tokens)


def _read_small_regular_file(path: Path, maximum: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise BundleValidationError("file_open_failed", "A required input file is unreadable.", path.name) from error
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > maximum:
            raise BundleValidationError("file_size_limit", "A required input file is invalid.", path.name)
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            return source.read(maximum + 1)
    finally:
        os.close(descriptor)


def _write_report_exclusive(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as error:
        raise BundleValidationError(
            "output_exists",
            "Refusing to overwrite an existing analyzer report.",
            path.name,
        ) from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n", closefd=False) as output:
            json.dump(report, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(descriptor)
        if os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        os.close(descriptor)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and aggregate local Auto Email Sender Beta diagnostic ZIPs.",
    )
    parser.add_argument("bundles", nargs="+", type=Path, help="One or more diagnostic ZIP files.")
    parser.add_argument(
        "--forbidden-token-file",
        action="append",
        default=[],
        type=Path,
        help="UTF-8 file with one canary token per line; token values are never printed.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write JSON to a new private file instead of stdout; existing files are never overwritten.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        tokens = _read_forbidden_token_files(args.forbidden_token_file)
        report = analyze_bundles(args.bundles, forbidden_tokens=tokens)
        if args.output is not None:
            input_paths = {path.resolve(strict=False) for path in args.bundles}
            if args.output.resolve(strict=False) in input_paths:
                raise BundleValidationError(
                    "output_is_input",
                    "The analyzer output cannot replace an input bundle.",
                    args.output.name,
                )
            _write_report_exclusive(args.output, report)
        else:
            json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
            sys.stdout.write("\n")
        return 0
    except BundleValidationError as error:
        bundle = f" {error.bundle_name}" if error.bundle_name else ""
        print(f"ERROR [{error.code}]{bundle}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
