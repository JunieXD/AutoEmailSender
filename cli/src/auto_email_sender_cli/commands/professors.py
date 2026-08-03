from __future__ import annotations

from typing import Annotated

import typer

from auto_email_sender_cli.commands.common import format_detail, format_page, run_read_command


professors_app = typer.Typer(help="查询导师档案与标签。", no_args_is_help=True)
tags_app = typer.Typer(help="查询导师标签。", no_args_is_help=True)
professors_app.add_typer(tags_app, name="tags")


@professors_app.command("list")
def list_professors(
    ctx: typer.Context,
    query: Annotated[str | None, typer.Option("--query", "-q", help="按姓名、邮箱、学校、方向或备注搜索。")]=None,
    archived: Annotated[str, typer.Option("--archived", help="active、archived 或 all。") ]="active",
    tag_id: Annotated[int | None, typer.Option("--tag-id", min=1)] = None,
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 100,
    all_items: Annotated[bool, typer.Option("--all", help="自动读取全部分页结果。") ] = False,
) -> None:
    run_read_command(
        ctx,
        command="professors.list",
        path="/api/agent/v1/professors",
        params={
            "q": query,
            "archived": archived,
            "tag_id": tag_id,
            "cursor": cursor,
            "limit": limit,
        },
        fetch_all=all_items,
        human_formatter=lambda data: format_page(
            data,
            columns=(("id", "ID"), ("name", "姓名"), ("email", "邮箱"), ("university", "学校")),
        ),
    )


@professors_app.command("get")
def get_professor(
    ctx: typer.Context,
    professor_id: Annotated[int, typer.Argument(min=1, help="导师 ID。")],
) -> None:
    run_read_command(
        ctx,
        command="professors.get",
        path=f"/api/agent/v1/professors/{professor_id}",
        human_formatter=format_detail,
    )


@tags_app.command("list")
def list_professor_tags(
    ctx: typer.Context,
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 100,
    all_items: Annotated[bool, typer.Option("--all")] = False,
) -> None:
    run_read_command(
        ctx,
        command="professors.tags.list",
        path="/api/agent/v1/professor-tags",
        params={"cursor": cursor, "limit": limit},
        fetch_all=all_items,
        human_formatter=lambda data: format_page(
            data,
            columns=(("id", "ID"), ("name", "标签")),
        ),
    )
