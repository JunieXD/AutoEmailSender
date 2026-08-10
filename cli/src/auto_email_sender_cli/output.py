from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import typer

from auto_email_sender_cli.errors import (
    CliError,
    redact_error_details,
    sanitize_error_message,
)
from auto_email_sender_cli.result_protocol import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_MAX_OUTPUT_ITEMS,
    prepare_result_data,
    result_protocol_metadata,
)
from auto_email_sender_cli.version import (
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
)


# Windows PowerShell 5 can decode captured native UTF-8 output with a legacy
# code page. Escaping non-ASCII characters keeps machine-readable JSON intact
# without changing the human-facing table output.
_MACHINE_OUTPUT_REQUIRES_ASCII = sys.platform == "win32"


class OutputFormat(StrEnum):
    HUMAN = "table"
    JSON = "json"
    JSONL = "jsonl"


class ResultProjection(StrEnum):
    SUMMARY = "summary"
    FULL = "full"


@dataclass(slots=True)
class CliContext:
    output_format: OutputFormat
    request_id: str | None = None
    fields: tuple[str, ...] = ()
    filter_expression: str | None = None
    if_revision: str | None = None
    output_file: str | None = None
    force_output: bool = False
    projection: ResultProjection = ResultProjection.SUMMARY
    expand: tuple[str, ...] = ()
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    max_items: int = DEFAULT_MAX_OUTPUT_ITEMS
    include_revisions: bool = False
    specified_options: frozenset[str] = frozenset()
    invoke_command: str | None = None
    invoke_input: dict[str, object] | None = None


def build_meta(
    *,
    command: str,
    guide_topic: str = "overview",
    app_version: str | None = None,
    warnings: list[str] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    # ``guide_topic`` remains an internal compatibility parameter while older
    # command handlers migrate.  Repeating a prose guide hint in every result
    # wastes context and makes a static manual appear authoritative.
    _ = guide_topic
    meta: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "command": command,
    }
    if app_version is not None:
        meta["app_version"] = app_version
    if warnings:
        meta["warnings"] = warnings
    if request_id:
        meta["request_id"] = request_id
    return meta


def emit_success(
    context: CliContext,
    *,
    command: str,
    data: Any,
    human_text: str | None = None,
    guide_topic: str = "overview",
    app_version: str | None = None,
    warnings: list[str] | None = None,
    request_id: str | None = None,
    continuation_input: dict[str, object] | None = None,
) -> None:
    emitted_data = prepare_result_data(
        data,
        command=command,
        projection=context.projection.value,
        expanded_paths=context.expand,
        max_output_bytes=context.max_output_bytes,
        max_items=context.max_items,
        continuation_input=continuation_input,
        invoke_input=(
            context.invoke_input
            if context.invoke_command == command
            else None
        ),
    )
    response_request_id = request_id or context.request_id
    receipt = emitted_data.get("mutation_receipt") if isinstance(emitted_data, dict) else None
    if isinstance(receipt, dict) and receipt.get("request_id") == response_request_id:
        response_request_id = None
    meta = build_meta(
        command=command,
        guide_topic=guide_topic,
        app_version=app_version,
        warnings=warnings,
        request_id=response_request_id,
    )
    # Some non-paged envelopes (for example campaigns.resend-context) contain
    # an ``items`` field for business data. Only treat it as a collection when
    # the normalized pagination fields are present; otherwise metadata and
    # JSONL output would falsely advertise/flatten pagination.
    is_paged_collection = (
        isinstance(emitted_data, dict)
        and isinstance(emitted_data.get("items"), list)
        and any(key in emitted_data for key in ("next_cursor", "has_more", "pagination_mode"))
    )
    page_items = emitted_data.get("items") if is_paged_collection else None
    if is_paged_collection and context.output_format is OutputFormat.JSONL:
        meta["pagination"] = {
            "next_cursor": emitted_data.get("next_cursor"),
            "has_more": bool(emitted_data.get("has_more")),
        }
    envelope = {
        "ok": True,
        "data": emitted_data,
        "_meta": meta,
    }
    if context.output_format is OutputFormat.HUMAN:
        result_metadata = result_protocol_metadata(emitted_data)
        omitted_paths = result_metadata.get("omitted_paths", []) if result_metadata else []
        has_summarized_content = any(
            path != "/items/*"
            for path in omitted_paths
            if isinstance(path, str)
        )
        rendered = (
            _pretty_json(emitted_data)
            if has_summarized_content
            else (human_text if human_text is not None else _pretty_json(emitted_data))
        )
        typer.echo(_sanitize_terminal_text(rendered))
        return
    if context.output_format is OutputFormat.JSONL:
        meta_row: dict[str, object] = {"type": "meta", "meta": envelope["_meta"]}
        result_metadata = result_protocol_metadata(emitted_data)
        if result_metadata is not None:
            meta_row["result"] = result_metadata
        typer.echo(json.dumps(meta_row, ensure_ascii=_MACHINE_OUTPUT_REQUIRES_ASCII))
        jsonl_items = page_items if isinstance(page_items, list) else emitted_data
        if isinstance(jsonl_items, list):
            for item in jsonl_items:
                typer.echo(
                    json.dumps(
                        {"type": "item", "data": item},
                        ensure_ascii=_MACHINE_OUTPUT_REQUIRES_ASCII,
                    )
                )
            typer.echo(
                json.dumps(
                    {"type": "summary", "data": {"total": len(jsonl_items)}},
                    ensure_ascii=_MACHINE_OUTPUT_REQUIRES_ASCII,
                ),
            )
        else:
            typer.echo(
                json.dumps(
                    {"type": "item", "data": emitted_data},
                    ensure_ascii=_MACHINE_OUTPUT_REQUIRES_ASCII,
                )
            )
        return
    typer.echo(
        json.dumps(
            envelope,
            ensure_ascii=_MACHINE_OUTPUT_REQUIRES_ASCII,
            separators=(",", ":"),
        )
    )


def emit_error(
    context: CliContext,
    *,
    command: str,
    error: CliError,
    guide_topic: str = "troubleshooting",
) -> None:
    payload: dict[str, Any] = {
        "code": error.code,
        "message": sanitize_error_message(error.message),
        "retryable": error.retryable,
        "details": redact_error_details(error.details),
    }
    if error.suggested_command:
        payload["suggested_action"] = {"command": sanitize_error_message(error.suggested_command)}
    envelope = {
        "ok": False,
        "error": payload,
        "_meta": build_meta(
            command=command,
            guide_topic=guide_topic,
            request_id=context.request_id,
        ),
    }
    if context.output_format is OutputFormat.HUMAN:
        typer.echo(
            _sanitize_terminal_text(
                f"错误 [{error.code}]：{sanitize_error_message(error.message)}",
            ),
            err=True,
        )
        if error.suggested_command:
            typer.echo(
                _sanitize_terminal_text(
                    f"建议：{sanitize_error_message(error.suggested_command)}",
                ),
                err=True,
            )
        return
    if context.output_format is OutputFormat.JSONL:
        typer.echo(
            json.dumps(
                {"type": "error", "error": payload, "meta": envelope["_meta"]},
                ensure_ascii=_MACHINE_OUTPUT_REQUIRES_ASCII,
                separators=(",", ":"),
            ),
        )
        return
    typer.echo(
        json.dumps(
            envelope,
            ensure_ascii=_MACHINE_OUTPUT_REQUIRES_ASCII,
            separators=(",", ":"),
        )
    )


def _pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


_UNSAFE_TERMINAL_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _sanitize_terminal_text(value: str) -> str:
    return _UNSAFE_TERMINAL_CONTROL.sub("�", value)
