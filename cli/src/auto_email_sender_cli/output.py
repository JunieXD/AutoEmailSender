from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import typer

from auto_email_sender_cli.errors import (
    CliError,
    redact_error_details,
    sanitize_error_message,
)
from auto_email_sender_cli.version import (
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    get_cli_version,
)


class OutputFormat(StrEnum):
    HUMAN = "table"
    JSON = "json"
    JSONL = "jsonl"


@dataclass(slots=True)
class CliContext:
    output_format: OutputFormat
    request_id: str | None = None
    fields: tuple[str, ...] = ()
    filter_expression: str | None = None
    if_revision: str | None = None
    output_file: str | None = None
    force_output: bool = False


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
        "cli_version": get_cli_version(),
        "app_version": app_version,
        "warnings": warnings or [],
    }
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
) -> None:
    meta = build_meta(
        command=command,
        guide_topic=guide_topic,
        app_version=app_version,
        warnings=warnings,
        request_id=request_id or context.request_id,
    )
    # Some non-paged envelopes (for example campaigns.resend-context) contain
    # an ``items`` field for business data. Only treat it as a collection when
    # the normalized pagination fields are present; otherwise metadata and
    # JSONL output would falsely advertise/flatten pagination.
    is_paged_collection = (
        isinstance(data, dict)
        and isinstance(data.get("items"), list)
        and any(key in data for key in ("next_cursor", "has_more", "pagination_mode"))
    )
    page_items = data.get("items") if is_paged_collection else None
    if is_paged_collection:
        meta["pagination"] = {
            "next_cursor": data.get("next_cursor"),
            "has_more": bool(data.get("has_more")),
        }
    envelope = {
        "ok": True,
        "data": data,
        "_meta": meta,
    }
    if context.output_format is OutputFormat.HUMAN:
        typer.echo(_sanitize_terminal_text(human_text if human_text is not None else _pretty_json(data)))
        return
    if context.output_format is OutputFormat.JSONL:
        typer.echo(json.dumps({"type": "meta", "meta": envelope["_meta"]}, ensure_ascii=False))
        jsonl_items = page_items if isinstance(page_items, list) else data
        if isinstance(jsonl_items, list):
            for item in jsonl_items:
                typer.echo(json.dumps({"type": "item", "data": item}, ensure_ascii=False))
            typer.echo(
                json.dumps({"type": "summary", "data": {"total": len(jsonl_items)}}, ensure_ascii=False),
            )
        else:
            typer.echo(json.dumps({"type": "item", "data": data}, ensure_ascii=False))
        return
    typer.echo(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))


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
    typer.echo(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))


def _pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


_UNSAFE_TERMINAL_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _sanitize_terminal_text(value: str) -> str:
    return _UNSAFE_TERMINAL_CONTROL.sub("�", value)
