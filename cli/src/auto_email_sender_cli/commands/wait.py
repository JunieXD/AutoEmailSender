from __future__ import annotations

import time
from collections import Counter
from typing import Annotated

import typer

from auto_email_sender_cli.action_links import resolve_action_links
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
_ATTENTION_REQUIRED_STATES = {
    "needs_review",
    "review_required",
    "paused",
}
_WAIT_CONDITIONS = {"settled", "terminal"}


def wait_for_resource(
    ctx: typer.Context,
    resource: Annotated[
        str,
        typer.Option(
            "--resource",
            help="任务资源：matching.jobs、enrichment.jobs、crawler.jobs 或 campaigns。",
        ),
    ],
    resource_id: Annotated[
        list[int],
        typer.Option("--id", min=1, help="可重复指定一组资源 ID。"),
    ],
    timeout_seconds: Annotated[
        float,
        typer.Option("--timeout-seconds", min=0, max=86_400),
    ] = 30.0,
    interval_seconds: Annotated[
        float,
        typer.Option("--interval-seconds", min=0.1, max=30),
    ] = 1.0,
    until: Annotated[
        str,
        typer.Option(
            "--until",
            help="停止条件：settled 表示后台已停止运行，terminal 表示最终结束。",
        ),
    ] = "settled",
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
        normalized_until = until.strip().lower()
        if normalized_until not in _WAIT_CONDITIONS:
            raise CliError(
                code="INVALID_WAIT_CONDITION",
                message=f"不支持的等待条件：{until}",
                exit_code=2,
                details={"available_conditions": sorted(_WAIT_CONDITIONS)},
            )
        if len(set(resource_id)) != len(resource_id):
            raise CliError(
                code="DUPLICATE_WAIT_RESOURCE_ID",
                message="--id 不能重复。",
                exit_code=2,
            )
        if len(resource_id) > 100:
            raise CliError(
                code="WAIT_RESOURCE_LIMIT_EXCEEDED",
                message="一次最多等待 100 个资源。",
                exit_code=2,
            )
        validate_context_options(
            context,
            supports_filter=False,
            supports_output_file=False,
            supports_projection=False,
        )
        client = AgentApiClient(timeout=max(30.0, interval_seconds + 5.0))
        started = time.monotonic()
        polls = 0
        poll_rounds = 0
        latest_by_id: dict[int, object] = {}
        timed_out = False
        while True:
            poll_rounds += 1
            pending_ids = [
                item_id
                for item_id in resource_id
                if not _wait_condition_met(_status(latest_by_id.get(item_id)), normalized_until)
            ]
            for item_id in pending_ids:
                latest_by_id[item_id] = client.request("GET", route.format(id=item_id))
                polls += 1
            if all(
                _wait_condition_met(_status(latest_by_id.get(item_id)), normalized_until)
                for item_id in resource_id
            ):
                break
            elapsed = time.monotonic() - started
            if elapsed >= timeout_seconds:
                timed_out = True
                break
            time.sleep(min(interval_seconds, max(0.0, timeout_seconds - elapsed)))
        elapsed_seconds = round(time.monotonic() - started, 3)
        timed_out_ids = [
            item_id
            for item_id in resource_id
            if not _wait_condition_met(_status(latest_by_id.get(item_id)), normalized_until)
        ]
        if len(resource_id) == 1:
            item_id = resource_id[0]
            latest = latest_by_id.get(item_id)
            status_value = _status(latest)
            state_category = _state_category(status_value)
            data = {
                "resource": resource,
                "id": item_id,
                "status": status_value,
                "state_category": state_category,
                "settled": state_category in {"attention_required", "terminal"},
                "terminal": state_category == "terminal",
                "timed_out": timed_out,
                "until": normalized_until,
                "poll_count": polls,
                "poll_rounds": poll_rounds,
                "elapsed_seconds": elapsed_seconds,
                "result": latest,
                "available_actions": _available_actions(resource, item_id, status_value),
            }
        else:
            resources = [
                _wait_resource_summary(
                    item_id,
                    latest_by_id.get(item_id),
                )
                for item_id in resource_id
            ]
            by_status = Counter(str(item["status"] or "unknown") for item in resources)
            data = {
                "resource": resource,
                "ids": resource_id,
                "total_count": len(resource_id),
                "settled_count": sum(bool(item["settled"]) for item in resources),
                "terminal_count": sum(bool(item["terminal"]) for item in resources),
                "by_status": dict(sorted(by_status.items())),
                "failed_ids": [
                    int(item["id"])
                    for item in resources
                    if item["status"] in {"failed", "send_failed", "draft_failed", "partial_failed"}
                ],
                "attention_required_ids": [
                    int(item["id"])
                    for item in resources
                    if item["state_category"] == "attention_required"
                ],
                "timed_out_ids": timed_out_ids,
                "settled": all(bool(item["settled"]) for item in resources),
                "terminal": all(bool(item["terminal"]) for item in resources),
                "timed_out": timed_out,
                "until": normalized_until,
                "poll_count": polls,
                "poll_rounds": poll_rounds,
                "elapsed_seconds": elapsed_seconds,
                "resources": resources,
                "action_groups": _wait_action_groups(resource, resources),
            }
        warnings = ["等待超时；部分任务仍未满足停止条件。"] if timed_out else []
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


def _state_category(status_value: str | None) -> str:
    if status_value in _TERMINAL_STATES:
        return "terminal"
    if status_value in _ATTENTION_REQUIRED_STATES:
        return "attention_required"
    if status_value is None:
        return "unknown"
    return "active"


def _wait_condition_met(status_value: str | None, until: str) -> bool:
    category = _state_category(status_value)
    if until == "terminal":
        return category == "terminal"
    return category in {"attention_required", "terminal"}


def _wait_resource_summary(
    resource_id: int,
    latest: object,
) -> dict[str, object]:
    status_value = _status(latest)
    state_category = _state_category(status_value)
    return {
        "id": resource_id,
        "status": status_value,
        "state_category": state_category,
        "settled": state_category in {"attention_required", "terminal"},
        "terminal": state_category == "terminal",
    }


def _wait_action_groups(
    resource: str,
    resources: list[dict[str, object]],
) -> list[dict[str, object]]:
    ids_by_status: dict[str, list[int]] = {}
    for item in resources:
        status_value = str(item.get("status") or "unknown")
        ids_by_status.setdefault(status_value, []).append(int(item["id"]))
    groups: list[dict[str, object]] = []
    for status_value, resource_ids in sorted(ids_by_status.items()):
        actions = _available_actions(resource, resource_ids[0], status_value)
        compact_actions = [
            {key: value for key, value in action.items() if key != "arguments"}
            for action in actions
        ]
        groups.append(
            {
                "status": status_value,
                "ids": resource_ids,
                "available_actions": compact_actions,
            },
        )
    return groups


def _available_actions(
    resource: str,
    resource_id: int,
    status_value: str | None,
) -> list[dict[str, object]]:
    if status_value is None:
        return []
    allowed: list[str]
    blocked: dict[str, str]
    if status_value in _TERMINAL_STATES:
        if status_value in {"failed", "partial_failed", "partially_completed"}:
            allowed = ["read", "retry"]
            if resource == "crawler.jobs" and status_value == "partially_completed":
                allowed.append("enrich")
            blocked = {"wait": "对象已结束"}
        else:
            allowed = ["read", "archive"]
            blocked = {"wait": "对象已进入终态", "cancel": "对象已进入终态"}
    elif status_value in {"queued", "running"}:
        allowed = ["read", "wait", "cancel"]
        blocked = {}
        if resource == "crawler.jobs":
            allowed.append("pause")
        else:
            blocked["pause"] = "当前资源不支持暂停"
    elif status_value == "paused":
        allowed = ["read", "resume", "cancel"]
        blocked = {"wait": "对象已暂停，请先恢复"}
    elif status_value in {"needs_review", "review_required"}:
        allowed = ["read"]
        blocked = {"wait": "对象正在等待审核，不是后台执行中"}
        if resource == "crawler.jobs":
            allowed.extend(["resume-review", "approve", "enrich"])
    else:
        allowed = ["read"]
        blocked = {"wait": f"状态 {status_value} 未声明为可等待状态"}

    links, _ = resolve_action_links(
        f"{resource}.get",
        {"id": resource_id},
        actions=allowed,
        blocked_actions=blocked,
    )
    return links
