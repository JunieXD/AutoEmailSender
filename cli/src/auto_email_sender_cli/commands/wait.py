from __future__ import annotations

import time
from typing import Annotated

import typer

from auto_email_sender_cli.client import AgentApiClient
from auto_email_sender_cli.commands.common import (
    cli_context,
    format_detail,
    validate_context_options,
)
from auto_email_sender_cli.errors import CliError
from auto_email_sender_cli.output import emit_error, emit_success


_ROUTES: dict[str, str] = {
    "matching.jobs": "/api/agent/v1/matching/jobs/{id}",
    "enrichment.jobs": "/api/agent/v1/enrichment/jobs/{id}",
    "crawler.jobs": "/api/agent/v1/crawler/jobs/{id}",
    "campaigns": "/api/agent/v1/campaigns/{id}",
}
_TERMINAL_STATES = {
    "succeeded",
    "partially_succeeded",
    "partial_failed",
    "partially_completed",
    "completed",
    "failed",
    "canceled",
    "cancelled",
    "stopped",
    "archived",
    "expired",
    "sent",
    "send_failed",
    "draft_failed",
    "reply_detected",
}


def wait_for_resource(
    ctx: typer.Context,
    resource: Annotated[
        str,
        typer.Option(
            "--resource",
            help="任务资源：matching.jobs、enrichment.jobs、crawler.jobs 或 campaigns。",
        ),
    ],
    resource_id: Annotated[int, typer.Option("--id", min=1)],
    timeout_seconds: Annotated[
        float,
        typer.Option("--timeout-seconds", min=0, max=86_400),
    ] = 30.0,
    interval_seconds: Annotated[
        float,
        typer.Option("--interval-seconds", min=0.1, max=30),
    ] = 1.0,
) -> None:
    context = cli_context(ctx)
    command = "wait"
    route = _ROUTES.get(resource)
    if route is None:
        error = CliError(
            code="INVALID_WAIT_RESOURCE",
            message=f"不支持等待资源：{resource}",
            exit_code=2,
            details={"available_resources": sorted(_ROUTES)},
        )
        emit_error(context, command=command, error=error)
        raise typer.Exit(error.exit_code)
    try:
        validate_context_options(
            context,
            supports_filter=False,
            supports_output_file=False,
        )
        client = AgentApiClient(timeout=max(30.0, interval_seconds + 5.0))
        started = time.monotonic()
        polls = 0
        latest: object = None
        timed_out = False
        while True:
            latest = client.request("GET", route.format(id=resource_id))
            polls += 1
            status_value = _status(latest)
            if status_value in _TERMINAL_STATES:
                break
            elapsed = time.monotonic() - started
            if elapsed >= timeout_seconds:
                timed_out = True
                break
            time.sleep(min(interval_seconds, max(0.0, timeout_seconds - elapsed)))
        status_value = _status(latest)
        data = {
            "resource": resource,
            "id": resource_id,
            "status": status_value,
            "terminal": not timed_out and status_value in _TERMINAL_STATES,
            "timed_out": timed_out,
            "poll_count": polls,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "result": latest,
            "available_actions": _available_actions(resource, status_value),
        }
        warnings = ["等待超时；任务仍未进入终态，不能报告为已完成。"] if timed_out else []
        emit_success(
            context,
            command=command,
            data=data,
            human_text=format_detail(data),
            app_version=client.descriptor.app_version,
            request_id=getattr(client, "last_request_id", None),
            warnings=warnings,
        )
    except CliError as error:
        emit_error(context, command=command, error=error)
        raise typer.Exit(error.exit_code) from error


def _status(value: object) -> str | None:
    if isinstance(value, dict):
        status_value = value.get("status")
        if isinstance(status_value, str):
            return status_value.lower()
        nested = value.get("job")
        if isinstance(nested, dict) and isinstance(nested.get("status"), str):
            return str(nested["status"]).lower()
    return None


def _available_actions(resource: str, status_value: str | None) -> list[dict[str, object]]:
    if status_value is None:
        return []
    if status_value in _TERMINAL_STATES:
        if status_value in {"failed", "partial_failed", "partially_completed"}:
            return [
                {"action": "read", "allowed": True},
                {"action": "retry", "allowed": True},
                {"action": "wait", "allowed": False, "reason": "对象已结束"},
            ]
        return [
            {"action": "read", "allowed": True},
            {"action": "archive", "allowed": True},
            {"action": "wait", "allowed": False, "reason": "对象已进入终态"},
            {"action": "cancel", "allowed": False, "reason": "对象已进入终态"},
        ]
    actions = [{"action": "wait", "allowed": True}]
    if status_value in {"queued", "running"}:
        actions.extend(
            [
                {"action": "cancel", "allowed": True},
                {
                    "action": "pause",
                    "allowed": resource == "crawler.jobs",
                    "reason": None if resource == "crawler.jobs" else "当前资源不支持暂停",
                },
            ],
        )
    if status_value == "paused":
        return [
            {"action": "read", "allowed": True},
            {"action": "resume", "allowed": True},
            {"action": "cancel", "allowed": True},
            {"action": "wait", "allowed": False, "reason": "对象已暂停，请先恢复"},
        ]
    return [
        {"action": "read", "allowed": True},
        {"action": "wait", "allowed": False, "reason": f"状态 {status_value} 未声明为可等待状态"},
    ]
