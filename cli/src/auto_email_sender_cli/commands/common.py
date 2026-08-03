from __future__ import annotations

from collections.abc import Callable
import secrets
from typing import Any

import typer

from auto_email_sender_cli.client import AgentApiClient
from auto_email_sender_cli.errors import CliError
from auto_email_sender_cli.output import CliContext, OutputFormat, emit_error, emit_success


HumanFormatter = Callable[[Any], str]


def cli_context(ctx: typer.Context) -> CliContext:
    value = ctx.find_root().obj
    if isinstance(value, CliContext):
        return value
    return CliContext(output_format=OutputFormat.HUMAN)


def run_read_command(
    ctx: typer.Context,
    *,
    command: str,
    path: str,
    params: dict[str, object] | None = None,
    guide_topic: str = "overview",
    human_formatter: HumanFormatter | None = None,
    fetch_all: bool = False,
) -> Any:
    context = cli_context(ctx)
    try:
        client = AgentApiClient()
        data = (
            fetch_all_pages(client, path, params=params)
            if fetch_all
            else client.request("GET", path, params=_without_none(params))
        )
        human_text = human_formatter(data) if human_formatter else None
        emit_success(
            context,
            command=command,
            data=data,
            human_text=human_text,
            guide_topic=guide_topic,
            app_version=client.descriptor.app_version,
        )
        return data
    except CliError as error:
        emit_error(context, command=command, error=error, guide_topic=guide_topic)
        raise typer.Exit(error.exit_code) from error


def run_write_command(
    ctx: typer.Context,
    *,
    command: str,
    path: str,
    method: str = "POST",
    json_body: object | None = None,
    guide_topic: str = "overview",
    human_formatter: HumanFormatter | None = None,
    timeout: float = 360.0,
    use_idempotency_key: bool = True,
) -> Any:
    context = cli_context(ctx)
    try:
        client = AgentApiClient(timeout=timeout)
        data = client.request(
            method,
            path,
            json_body=json_body,
            idempotency_key=(
                f"cli_{secrets.token_urlsafe(24)}"
                if use_idempotency_key
                else None
            ),
        )
        emit_success(
            context,
            command=command,
            data=data,
            human_text=human_formatter(data) if human_formatter else None,
            guide_topic=guide_topic,
            app_version=client.descriptor.app_version,
        )
        return data
    except CliError as error:
        emit_error(context, command=command, error=error, guide_topic=guide_topic)
        raise typer.Exit(error.exit_code) from error


def fetch_all_pages(
    client: AgentApiClient,
    path: str,
    *,
    params: dict[str, object] | None = None,
) -> dict[str, object]:
    request_params = _without_none(params)
    request_params.pop("cursor", None)
    request_params["limit"] = 500
    items: list[object] = []
    cursor: str | None = None
    while True:
        if cursor is not None:
            request_params["cursor"] = cursor
        payload = client.request("GET", path, params=request_params)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise CliError(
                code="INVALID_API_RESPONSE",
                message="本地服务返回了无法识别的分页结果。",
                exit_code=8,
            )
        items.extend(payload["items"])
        if not payload.get("has_more"):
            break
        next_cursor = payload.get("next_cursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            raise CliError(
                code="INVALID_API_RESPONSE",
                message="本地服务分页结果缺少 next_cursor。",
                exit_code=8,
            )
        cursor = next_cursor
    return {
        "items": items,
        "next_cursor": None,
        "has_more": False,
        "fetched_all": True,
    }


def optional_bool(value: str | None, *, option_name: str) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise typer.BadParameter(
        f"{option_name} 仅支持 true 或 false",
        param_hint=option_name,
    )


def format_page(
    data: Any,
    *,
    columns: tuple[tuple[str, str], ...],
    empty_message: str = "没有符合条件的记录。",
) -> str:
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return str(data)
    items = data["items"]
    if not items:
        return empty_message
    header = "\t".join(label for _, label in columns)
    lines = [header]
    for item in items:
        if not isinstance(item, dict):
            lines.append(str(item))
            continue
        lines.append(
            "\t".join(_display_value(item.get(key)) for key, _ in columns),
        )
    if data.get("has_more"):
        lines.append(f"还有更多结果；下一游标：{data.get('next_cursor')}")
    return "\n".join(lines)


def format_detail(data: Any) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, indent=2)


def _display_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return ", ".join(
            str(item.get("name", item.get("id", item))) if isinstance(item, dict) else str(item)
            for item in value
        )
    return str(value)


def _without_none(params: dict[str, object] | None) -> dict[str, object]:
    return {
        key: value
        for key, value in (params or {}).items()
        if value is not None
    }
