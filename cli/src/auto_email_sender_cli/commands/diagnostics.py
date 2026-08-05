from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from auto_email_sender_cli.client import AgentApiClient
from auto_email_sender_cli.commands.common import (
    cli_context,
    format_detail,
    run_read_command,
    validate_context_options,
)
from auto_email_sender_cli.errors import CliError
from auto_email_sender_cli.output import emit_error, emit_success


diagnostics_app = typer.Typer(
    help="读取或导出已脱敏的本地诊断日志。",
    no_args_is_help=True,
)


@diagnostics_app.command("logs")
def list_diagnostics_logs(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 100,
    offset: Annotated[int, typer.Option("--offset", min=0)] = 0,
    level: Annotated[str | None, typer.Option("--level")] = None,
    category: Annotated[str | None, typer.Option("--category")] = None,
    event_name: Annotated[str | None, typer.Option("--event-name")] = None,
    request_id: Annotated[str | None, typer.Option("--request-id")] = None,
    entity_type: Annotated[str | None, typer.Option("--entity-type")] = None,
    entity_id: Annotated[str | None, typer.Option("--entity-id")] = None,
    start_at: Annotated[
        str | None,
        typer.Option("--start-at", help="带时区的 ISO 8601 时间。"),
    ] = None,
    end_at: Annotated[
        str | None,
        typer.Option("--end-at", help="带时区的 ISO 8601 时间。"),
    ] = None,
    all_items: Annotated[
        bool,
        typer.Option("--all", help="从当前 offset 开始读取全部诊断日志。"),
    ] = False,
    fields: Annotated[
        str | None,
        typer.Option("--fields", help="只返回需要的字段，逗号分隔。"),
    ] = None,
) -> None:
    run_read_command(
        ctx,
        command="diagnostics.logs",
        path="/api/agent/v1/diagnostics/operation-logs",
        params=_diagnostic_filters(
            level=level,
            category=category,
            event_name=event_name,
            request_id=request_id,
            entity_type=entity_type,
            entity_id=entity_id,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
            offset=offset,
        ),
        guide_topic="diagnostics",
        human_formatter=format_detail,
        fetch_all=all_items,
        fields=fields,
    )


@diagnostics_app.command("export")
def export_diagnostics_logs(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option("--output", "-o", help="导出 JSON 保存位置。")],
    force: Annotated[bool, typer.Option("--force", help="覆盖已有文件。") ] = False,
    level: Annotated[str | None, typer.Option("--level")] = None,
    category: Annotated[str | None, typer.Option("--category")] = None,
    event_name: Annotated[str | None, typer.Option("--event-name")] = None,
    request_id: Annotated[str | None, typer.Option("--request-id")] = None,
    entity_type: Annotated[str | None, typer.Option("--entity-type")] = None,
    entity_id: Annotated[str | None, typer.Option("--entity-id")] = None,
    start_at: Annotated[
        str | None,
        typer.Option("--start-at", help="带时区的 ISO 8601 时间。"),
    ] = None,
    end_at: Annotated[
        str | None,
        typer.Option("--end-at", help="带时区的 ISO 8601 时间。"),
    ] = None,
) -> None:
    context = cli_context(ctx)
    command = "diagnostics.export"
    try:
        validate_context_options(
            context,
            supports_filter=False,
            supports_output_file=False,
        )
        client = AgentApiClient(timeout=120.0)
        data = client.request(
            "GET",
            "/api/agent/v1/diagnostics/export",
            params=_diagnostic_filters(
                level=level,
                category=category,
                event_name=event_name,
                request_id=request_id,
                entity_type=entity_type,
                entity_id=entity_id,
                start_at=start_at,
                end_at=end_at,
            ),
        )
        destination = _write_json_export(output, data, force=force)
        total = data.get("total") if isinstance(data, dict) else None
        emit_success(
            context,
            command=command,
            data={"output": destination.as_posix(), "total": total},
            human_text=f"已导出诊断日志到：\n{destination}",
            guide_topic="diagnostics",
            app_version=client.descriptor.app_version,
        )
    except CliError as error:
        emit_error(context, command=command, error=error, guide_topic="diagnostics")
        raise typer.Exit(error.exit_code) from error


@diagnostics_app.command("crawler-debug")
def download_crawler_debug_log(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1, help="抓取任务 ID。")],
    output: Annotated[Path, typer.Option("--output", "-o", help="JSONL 保存位置。")],
    force: Annotated[bool, typer.Option("--force", help="覆盖已有文件。") ] = False,
) -> None:
    context = cli_context(ctx)
    command = "diagnostics.crawler-debug"
    try:
        validate_context_options(
            context,
            supports_filter=False,
            supports_output_file=False,
        )
        client = AgentApiClient(timeout=120.0)
        content = client.download_bytes(
            f"/api/agent/v1/diagnostics/crawler-debug/{job_id}/export",
        )
        destination = _write_bytes_export(output, content, force=force)
        emit_success(
            context,
            command=command,
            data={
                "job_id": job_id,
                "output": destination.as_posix(),
                "size_bytes": len(content),
            },
            human_text=f"已导出抓取调试日志到：\n{destination}",
            guide_topic="diagnostics",
            app_version=client.descriptor.app_version,
        )
    except CliError as error:
        emit_error(context, command=command, error=error, guide_topic="diagnostics")
        raise typer.Exit(error.exit_code) from error


def _diagnostic_filters(
    *,
    level: str | None,
    category: str | None,
    event_name: str | None,
    request_id: str | None,
    entity_type: str | None,
    entity_id: str | None,
    start_at: str | None,
    end_at: str | None,
    limit: int | None = None,
    offset: int | None = None,
) -> dict[str, object]:
    filters: dict[str, object] = {
        "level": level,
        "category": category,
        "event_name": event_name,
        "request_id": request_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "start_at": start_at,
        "end_at": end_at,
    }
    if limit is not None:
        filters["limit"] = limit
    if offset is not None:
        filters["offset"] = offset
    return {key: value for key, value in filters.items() if value is not None}


def _write_json_export(output: Path, data: object, *, force: bool) -> Path:
    return _write_bytes_export(
        output,
        json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
        force=force,
    )


def _write_bytes_export(output: Path, content: bytes, *, force: bool) -> Path:
    destination = output.expanduser().resolve()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb" if force else "xb") as file:
            file.write(content)
    except FileExistsError as exc:
        raise CliError(
            code="OUTPUT_EXISTS",
            message=f"输出文件已存在：{destination}",
            exit_code=2,
            suggested_command="重新选择 --output，或明确使用 --force 覆盖。",
        ) from exc
    except OSError as exc:
        raise CliError(
            code="OUTPUT_WRITE_FAILED",
            message=f"无法写入导出文件：{exc}",
            exit_code=5,
        ) from exc
    return destination
