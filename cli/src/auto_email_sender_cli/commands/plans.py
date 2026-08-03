from __future__ import annotations

from typing import Annotated

import typer

from auto_email_sender_cli.commands.common import (
    cli_context,
    format_detail,
    run_read_command,
    run_write_command,
)
from auto_email_sender_cli.errors import CliError
from auto_email_sender_cli.output import emit_error


plans_app = typer.Typer(
    help="查看、确认执行或取消一次性高风险动作计划。",
    no_args_is_help=True,
)


@plans_app.command("show")
def show_plan(
    ctx: typer.Context,
    plan_id: Annotated[str, typer.Argument()],
) -> None:
    run_read_command(
        ctx,
        command="plans.show",
        path=f"/api/agent/v1/plans/{plan_id}",
        guide_topic="sending",
        human_formatter=format_detail,
    )


@plans_app.command("execute")
def execute_plan(
    ctx: typer.Context,
    plan_id: Annotated[str, typer.Argument()],
    confirm: Annotated[
        bool,
        typer.Option(
            "--confirm",
            help="仅在用户明确确认所展示的这一计划后使用。",
        ),
    ] = False,
) -> None:
    if not confirm:
        error = CliError(
            code="PLAN_CONFIRMATION_REQUIRED",
            message="尚未执行。请先展示计划并得到用户明确确认，再加 --confirm。",
            exit_code=6,
            suggested_command=f"auto-email-sender plans show {plan_id}",
        )
        emit_error(
            cli_context(ctx),
            command="plans.execute",
            error=error,
            guide_topic="sending",
        )
        raise typer.Exit(error.exit_code)
    run_write_command(
        ctx,
        command="plans.execute",
        path=f"/api/agent/v1/plans/{plan_id}/execute",
        json_body={"confirm": True},
        guide_topic="sending",
        human_formatter=format_detail,
        use_idempotency_key=False,
    )


@plans_app.command("cancel")
def cancel_plan(
    ctx: typer.Context,
    plan_id: Annotated[str, typer.Argument()],
) -> None:
    run_write_command(
        ctx,
        command="plans.cancel",
        path=f"/api/agent/v1/plans/{plan_id}/cancel",
        guide_topic="sending",
        human_formatter=format_detail,
        use_idempotency_key=False,
    )
