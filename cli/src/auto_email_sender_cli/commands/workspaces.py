from __future__ import annotations

from typing import Annotated

import typer

from auto_email_sender_cli.commands.common import (
    format_detail,
    run_read_command,
    run_write_command,
)


workspaces_app = typer.Typer(
    help="读取和继续单位导师的邮件工作区。",
    no_args_is_help=True,
)


@workspaces_app.command("get")
def get_workspace(
    ctx: typer.Context,
    professor_id: Annotated[int, typer.Argument(min=1, help="导师 ID。")],
    identity_id: Annotated[int, typer.Option("--identity-id", min=1, help="发件身份 ID。")],
    llm_profile_id: Annotated[
        int,
        typer.Option("--llm-profile-id", min=1, help="LLM 配置 ID。"),
    ],
) -> None:
    run_read_command(
        ctx,
        command="workspaces.get",
        path=f"/api/agent/v1/workspaces/{professor_id}",
        params={
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
        },
        guide_topic="workspaces",
        human_formatter=format_detail,
    )


@workspaces_app.command("ensure-task")
def ensure_workspace_task(
    ctx: typer.Context,
    professor_id: Annotated[int, typer.Argument(min=1, help="导师 ID。")],
    identity_id: Annotated[int, typer.Option("--identity-id", min=1, help="发件身份 ID。")],
    llm_profile_id: Annotated[
        int,
        typer.Option("--llm-profile-id", min=1, help="LLM 配置 ID。"),
    ],
) -> None:
    run_write_command(
        ctx,
        command="workspaces.ensure-task",
        path=f"/api/agent/v1/workspaces/{professor_id}/ensure-task",
        params={
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
        },
        guide_topic="workspaces",
        human_formatter=format_detail,
    )


@workspaces_app.command("refresh-replies")
def refresh_workspace_replies(
    ctx: typer.Context,
    professor_id: Annotated[int, typer.Argument(min=1, help="导师 ID。")],
    identity_id: Annotated[int, typer.Option("--identity-id", min=1, help="发件身份 ID。")],
    llm_profile_id: Annotated[
        int,
        typer.Option("--llm-profile-id", min=1, help="LLM 配置 ID。"),
    ],
) -> None:
    run_write_command(
        ctx,
        command="workspaces.refresh-replies",
        path=f"/api/agent/v1/workspaces/{professor_id}/refresh-replies",
        params={
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
        },
        guide_topic="workspaces",
        human_formatter=format_detail,
        use_idempotency_key=True,
    )
