from __future__ import annotations

import secrets
import time
from typing import Annotated, Any

import typer

from auto_email_sender_cli.client import AgentApiClient
from auto_email_sender_cli.commands.common import (
    add_revisions,
    augment_state_metadata,
    cli_context,
    format_detail,
    run_read_command,
    validate_context_options,
)
from auto_email_sender_cli.errors import CliError
from auto_email_sender_cli.output import emit_error, emit_success

ui_handoffs_app = typer.Typer(
    help="读取、等待、取消或重试 Agent 发给桌面界面的临时交接。",
    no_args_is_help=True,
)

_SETTLED_STATUSES = frozenset(
    {"awaiting_user", "applied", "failed", "canceled", "expired"},
)
_TERMINAL_STATUSES = frozenset({"applied", "failed", "canceled", "expired"})
_WAIT_CONDITIONS = frozenset({"settled", "terminal", "applied"})


def run_ui_handoff_command(
    ctx: typer.Context,
    *,
    command: str,
    path: str,
    json_body: object | None = None,
    use_idempotency_key: bool,
) -> Any:
    """Create/control a handoff without mislabeling it as a data mutation."""

    context = cli_context(ctx)
    try:
        validate_context_options(
            context,
            supports_filter=False,
            supports_output_file=False,
            supports_if_revision=False,
        )
        request_id = (
            (context.request_id or f"cli_{secrets.token_urlsafe(24)}")
            if use_idempotency_key
            else None
        )
        if request_id is not None:
            context.request_id = request_id
        client = AgentApiClient()
        data = client.request(
            "POST",
            path,
            json_body=json_body,
            idempotency_key=request_id,
        )
        data = augment_state_metadata(add_revisions(data), command=command)
        emit_success(
            context,
            command=command,
            data=data,
            human_text=format_detail(data),
            app_version=client.descriptor.app_version,
            request_id=getattr(client, "last_request_id", None) or request_id,
        )
        return data
    except CliError as error:
        emit_error(context, command=command, error=error)
        raise typer.Exit(error.exit_code) from error


@ui_handoffs_app.command("get")
def get_ui_handoff(
    ctx: typer.Context,
    handoff_id: Annotated[str, typer.Argument(help="present 命令返回的 handoff_id。")],
) -> None:
    run_read_command(
        ctx,
        command="ui-handoffs.get",
        path=f"/api/agent/v1/ui-handoffs/{handoff_id}",
        human_formatter=format_detail,
    )


@ui_handoffs_app.command("cancel")
def cancel_ui_handoff(
    ctx: typer.Context,
    handoff_id: Annotated[str, typer.Argument(help="尚未应用的 handoff_id。")],
) -> None:
    run_ui_handoff_command(
        ctx,
        command="ui-handoffs.cancel",
        path=f"/api/agent/v1/ui-handoffs/{handoff_id}/cancel",
        use_idempotency_key=False,
    )


@ui_handoffs_app.command("retry")
def retry_ui_handoff(
    ctx: typer.Context,
    handoff_id: Annotated[str, typer.Argument(help="failed 或 awaiting_user 的 handoff_id。")],
) -> None:
    run_ui_handoff_command(
        ctx,
        command="ui-handoffs.retry",
        path=f"/api/agent/v1/ui-handoffs/{handoff_id}/retry",
        use_idempotency_key=False,
    )


@ui_handoffs_app.command("wait")
def wait_for_ui_handoff(
    ctx: typer.Context,
    handoff_id: Annotated[str, typer.Argument(help="present 命令返回的 handoff_id。")],
    timeout_seconds: Annotated[
        float,
        typer.Option("--timeout-seconds", min=0, max=86_400),
    ] = 30.0,
    interval_seconds: Annotated[
        float,
        typer.Option("--interval-seconds", min=0.1, max=30),
    ] = 0.5,
    until: Annotated[
        str,
        typer.Option(
            "--until",
            help="settled、terminal 或 applied；settled 也会在等待用户处理时返回。",
        ),
    ] = "settled",
) -> None:
    context = cli_context(ctx)
    command = "ui-handoffs.wait"
    try:
        normalized_until = until.strip().lower()
        if normalized_until not in _WAIT_CONDITIONS:
            raise CliError(
                code="INVALID_WAIT_CONDITION",
                message=f"不支持的等待条件：{until}",
                exit_code=2,
                details={"available_conditions": sorted(_WAIT_CONDITIONS)},
            )
        validate_context_options(
            context,
            supports_filter=False,
            supports_output_file=False,
            supports_if_revision=False,
        )
        client = AgentApiClient(timeout=min(30.0, max(0.1, timeout_seconds)))
        started = time.monotonic()
        deadline = started + timeout_seconds
        poll_count = 0
        timed_out = False
        stopped_on_terminal = False
        latest: dict[str, object] | None = None
        while True:
            if poll_count > 0 and time.monotonic() >= deadline:
                timed_out = True
                break
            response = client.request(
                "GET",
                f"/api/agent/v1/ui-handoffs/{handoff_id}",
                request_timeout=_remaining_timeout(
                    deadline,
                    allow_expired=timeout_seconds == 0 and poll_count == 0,
                ),
            )
            if not isinstance(response, dict):
                raise CliError(
                    code="INVALID_API_RESPONSE",
                    message="本地服务返回了无法识别的界面交接状态。",
                    exit_code=8,
                )
            latest = response
            poll_count += 1
            status = str(latest.get("status") or "").lower()
            if _handoff_wait_condition_met(status, normalized_until):
                break
            if normalized_until == "applied" and status in _TERMINAL_STATUSES:
                stopped_on_terminal = True
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            time.sleep(min(interval_seconds, remaining))

        assert latest is not None
        status = str(latest.get("status") or "").lower()
        condition_met = _handoff_wait_condition_met(status, normalized_until)
        data = {
            **latest,
            "settled": status in _SETTLED_STATUSES,
            "terminal": status in _TERMINAL_STATUSES,
            "condition_met": condition_met,
            "timed_out": timed_out,
            "until": normalized_until,
            "poll_count": poll_count,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        data = augment_state_metadata(add_revisions(data), command=command)
        warnings: list[str] = []
        if timed_out:
            warnings.append("等待超时；桌面界面尚未满足停止条件。")
        elif stopped_on_terminal:
            warnings.append(
                f"界面交接已进入 {status} 终态，当前等待无法自行满足 applied 条件；"
                "请根据 available_actions 决定是否重试或重新创建。",
            )
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


def _remaining_timeout(deadline: float, *, allow_expired: bool) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        if allow_expired:
            return 0.1
        raise CliError(
            code="WAIT_TIMEOUT",
            message="等待界面交接超时。",
            exit_code=8,
            retryable=True,
        )
    return max(0.05, min(30.0, remaining))


def _handoff_wait_condition_met(status: str, until: str) -> bool:
    if until == "applied":
        return status == "applied"
    if until == "terminal":
        return status in _TERMINAL_STATUSES
    return status in _SETTLED_STATUSES


__all__ = ["run_ui_handoff_command", "ui_handoffs_app"]
