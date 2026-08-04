from __future__ import annotations

from typing import Annotated

import typer

from auto_email_sender_cli.commands.common import (
    format_detail,
    format_page,
    run_read_command,
    run_write_command,
)


communication_groups_app = typer.Typer(
    help="管理多个发件身份共享的通信范围。",
    no_args_is_help=True,
)


@communication_groups_app.command("list")
def list_communication_groups(
    ctx: typer.Context,
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 100,
    all_items: Annotated[bool, typer.Option("--all", help="自动读取全部分页结果。")] = False,
) -> None:
    run_read_command(
        ctx,
        command="communication-groups.list",
        path="/api/agent/v1/communication-groups",
        params={"cursor": cursor, "limit": limit},
        fetch_all=all_items,
        guide_topic="communication-groups",
        human_formatter=lambda data: format_page(
            data,
            columns=(("id", "ID"), ("members", "成员")),
        ),
    )


@communication_groups_app.command("get")
def get_communication_group(
    ctx: typer.Context,
    group_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_read_command(
        ctx,
        command="communication-groups.get",
        path=f"/api/agent/v1/communication-groups/{group_id}",
        guide_topic="communication-groups",
        human_formatter=format_detail,
    )


@communication_groups_app.command("create")
def create_communication_group(
    ctx: typer.Context,
    identity_ids: Annotated[
        list[int],
        typer.Option("--identity-id", min=1, help="可重复指定通信组成员的身份 ID。"),
    ] = [],
    confirm_merge_existing_groups: Annotated[
        bool,
        typer.Option(
            "--confirm-merge-existing-groups",
            help="确认把已属于其他通信组的身份及其原组成员一并合并。",
        ),
    ] = False,
) -> None:
    run_write_command(
        ctx,
        command="communication-groups.create",
        path="/api/agent/v1/communication-groups",
        json_body={
            "identity_ids": identity_ids,
            "confirm_merge_existing_groups": confirm_merge_existing_groups,
        },
        guide_topic="communication-groups",
        human_formatter=format_detail,
    )


@communication_groups_app.command("update")
def update_communication_group(
    ctx: typer.Context,
    group_id: Annotated[int, typer.Argument(min=1)],
    identity_ids: Annotated[
        list[int],
        typer.Option("--identity-id", min=1, help="可重复指定更新后的全部成员身份 ID。"),
    ] = [],
    confirm_merge_existing_groups: Annotated[
        bool,
        typer.Option(
            "--confirm-merge-existing-groups",
            help="确认把已属于其他通信组的身份及其原组成员一并合并。",
        ),
    ] = False,
) -> None:
    run_write_command(
        ctx,
        command="communication-groups.update",
        method="PUT",
        path=f"/api/agent/v1/communication-groups/{group_id}",
        json_body={
            "identity_ids": identity_ids,
            "confirm_merge_existing_groups": confirm_merge_existing_groups,
        },
        guide_topic="communication-groups",
        human_formatter=format_detail,
    )


@communication_groups_app.command("delete")
def delete_communication_group(
    ctx: typer.Context,
    group_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="communication-groups.delete",
        path=f"/api/agent/v1/communication-groups/{group_id}/delete",
        guide_topic="communication-groups",
        human_formatter=format_detail,
    )
