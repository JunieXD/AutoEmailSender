from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import typer

from auto_email_sender_cli.errors import CliError
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


def guide_metadata(topic: str = "overview") -> dict[str, str]:
    return {
        "version": get_cli_version(),
        "command": f"auto-email-sender --format json guide --topic {topic}",
        "message": "多步骤、写入或真实发送前，请先读取相关 Agent 使用说明。",
    }


def build_meta(
    *,
    command: str,
    guide_topic: str = "overview",
    app_version: str | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "command": command,
        "cli_version": get_cli_version(),
        "app_version": app_version,
        "agent_guide": guide_metadata(guide_topic),
        "warnings": warnings or [],
    }


def emit_success(
    context: CliContext,
    *,
    command: str,
    data: Any,
    human_text: str | None = None,
    guide_topic: str = "overview",
    app_version: str | None = None,
    warnings: list[str] | None = None,
) -> None:
    meta = build_meta(
        command=command,
        guide_topic=guide_topic,
        app_version=app_version,
        warnings=warnings,
    )
    page_items = data.get("items") if isinstance(data, dict) else None
    if isinstance(page_items, list):
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
        typer.echo(
            f"\nAgent 使用说明：auto-email-sender --format json guide --topic {guide_topic}",
        )
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
        "message": error.message,
        "retryable": error.retryable,
        "details": error.details,
    }
    if error.suggested_command:
        payload["suggested_action"] = {"command": error.suggested_command}
    envelope = {
        "ok": False,
        "error": payload,
        "_meta": build_meta(command=command, guide_topic=guide_topic),
    }
    if context.output_format is OutputFormat.HUMAN:
        typer.echo(_sanitize_terminal_text(f"错误 [{error.code}]：{error.message}"), err=True)
        if error.suggested_command:
            typer.echo(_sanitize_terminal_text(f"建议：{error.suggested_command}"), err=True)
        return
    typer.echo(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))


def _pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


_UNSAFE_TERMINAL_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _sanitize_terminal_text(value: str) -> str:
    return _UNSAFE_TERMINAL_CONTROL.sub("�", value)
