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
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
    all_items: Annotated[bool, typer.Option("--all", help="自动读取全部分页结果。")] = False,
) -> None:
    run_read_command(
        ctx,
        command="communication-groups.list",
        path="/api/agent/v1/communication-groups",
        params={"cursor": cursor, "limit": limit},
        fetch_all=all_items,
        fields=fields,
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
    match_source_identity_id: Annotated[
        int | None,
        typer.Option(
            "--match-source-identity-id",
            min=1,
            help="可选；统一使用该身份的默认材料计算组内匹配度。",
        ),
    ] = None,
) -> None:
    json_body: dict[str, object] = {
        "identity_ids": identity_ids,
        "confirm_merge_existing_groups": confirm_merge_existing_groups,
    }
    if match_source_identity_id is not None:
        json_body["match_source_identity_id"] = match_source_identity_id
    run_write_command(
        ctx,
        command="communication-groups.create",
        path="/api/agent/v1/communication-groups",
        json_body=json_body,
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
    match_source_identity_id: Annotated[
        int | None,
        typer.Option(
            "--match-source-identity-id",
            min=1,
            help="指定组内统一使用哪个身份的默认材料计算匹配度。",
        ),
    ] = None,
    clear_match_source_identity: Annotated[
        bool,
        typer.Option(
            "--clear-match-source-identity",
            help="清除统一匹配依据，恢复为各身份独立计算。",
        ),
    ] = False,
) -> None:
    if match_source_identity_id is not None and clear_match_source_identity:
        raise typer.BadParameter(
            "--match-source-identity-id 与 --clear-match-source-identity 不能同时使用",
        )
    json_body: dict[str, object] = {
        "identity_ids": identity_ids,
        "confirm_merge_existing_groups": confirm_merge_existing_groups,
    }
    if clear_match_source_identity:
        json_body["match_source_identity_id"] = None
    elif match_source_identity_id is not None:
        json_body["match_source_identity_id"] = match_source_identity_id
    run_write_command(
        ctx,
        command="communication-groups.update",
        method="PUT",
        path=f"/api/agent/v1/communication-groups/{group_id}",
        json_body=json_body,
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
