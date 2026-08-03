from __future__ import annotations

from typing import Annotated

import typer

from auto_email_sender_cli.commands.common import format_detail, format_page, run_read_command


templates_app = typer.Typer(help="查询邮件模板。", no_args_is_help=True)
materials_app = typer.Typer(help="查询 AI 参考材料和可选附件。", no_args_is_help=True)
identities_app = typer.Typer(help="查询发件身份的脱敏视图。", no_args_is_help=True)
llm_profiles_app = typer.Typer(help="查询模型配置的脱敏视图。", no_args_is_help=True)


@templates_app.command("list")
def list_templates(
    ctx: typer.Context,
    include_archived: Annotated[bool, typer.Option("--include-archived")] = False,
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 100,
    all_items: Annotated[bool, typer.Option("--all")] = False,
) -> None:
    run_read_command(
        ctx,
        command="templates.list",
        path="/api/agent/v1/templates",
        params={
            "include_archived": include_archived,
            "cursor": cursor,
            "limit": limit,
        },
        fetch_all=all_items,
        human_formatter=lambda data: format_page(
            data,
            columns=(
                ("id", "ID"),
                ("name", "模板"),
                ("recommended_generation_mode", "建议生成方式"),
                ("is_default", "默认"),
            ),
        ),
    )


@templates_app.command("get")
def get_template(
    ctx: typer.Context,
    template_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_read_command(
        ctx,
        command="templates.get",
        path=f"/api/agent/v1/templates/{template_id}",
        human_formatter=format_detail,
    )


@materials_app.command("list")
def list_materials(
    ctx: typer.Context,
    identity_id: Annotated[int | None, typer.Option("--identity-id", min=1)] = None,
    material_type: Annotated[str | None, typer.Option("--material-type")] = None,
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 100,
    all_items: Annotated[bool, typer.Option("--all")] = False,
) -> None:
    run_read_command(
        ctx,
        command="materials.list",
        path="/api/agent/v1/materials",
        params={
            "identity_id": identity_id,
            "material_type": material_type,
            "cursor": cursor,
            "limit": limit,
        },
        guide_topic="materials",
        fetch_all=all_items,
        human_formatter=lambda data: format_page(
            data,
            columns=(
                ("id", "ID"),
                ("display_name", "材料"),
                ("identity_id", "身份 ID"),
                ("material_type", "类型"),
                ("is_primary", "默认参考"),
                ("size_bytes", "字节"),
            ),
        ),
    )


@materials_app.command("get")
def get_material(
    ctx: typer.Context,
    material_id: Annotated[int, typer.Argument(min=1)],
    include_text: Annotated[bool, typer.Option("--include-text", help="包含已提取的材料文本。") ] = False,
) -> None:
    run_read_command(
        ctx,
        command="materials.get",
        path=f"/api/agent/v1/materials/{material_id}",
        params={"include_text": include_text},
        guide_topic="materials",
        human_formatter=format_detail,
    )


@identities_app.command("list")
def list_identities(
    ctx: typer.Context,
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 100,
    all_items: Annotated[bool, typer.Option("--all")] = False,
) -> None:
    run_read_command(
        ctx,
        command="identities.list",
        path="/api/agent/v1/identities",
        params={"cursor": cursor, "limit": limit},
        fetch_all=all_items,
        human_formatter=lambda data: format_page(
            data,
            columns=(
                ("id", "ID"),
                ("name", "身份"),
                ("email_address", "发件邮箱"),
                ("smtp_configured", "SMTP 已配置"),
                ("imap_configured", "IMAP 已配置"),
                ("is_default", "默认"),
            ),
        ),
    )


@identities_app.command("get")
def get_identity(
    ctx: typer.Context,
    identity_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_read_command(
        ctx,
        command="identities.get",
        path=f"/api/agent/v1/identities/{identity_id}",
        human_formatter=format_detail,
    )


@llm_profiles_app.command("list")
def list_llm_profiles(
    ctx: typer.Context,
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 100,
    all_items: Annotated[bool, typer.Option("--all")] = False,
) -> None:
    run_read_command(
        ctx,
        command="llm-profiles.list",
        path="/api/agent/v1/llm-profiles",
        params={"cursor": cursor, "limit": limit},
        fetch_all=all_items,
        human_formatter=lambda data: format_page(
            data,
            columns=(
                ("id", "ID"),
                ("name", "模型配置"),
                ("provider", "提供方"),
                ("model_name", "模型"),
                ("credential_configured", "凭据已配置"),
                ("is_default", "默认"),
            ),
        ),
    )


@llm_profiles_app.command("get")
def get_llm_profile(
    ctx: typer.Context,
    profile_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_read_command(
        ctx,
        command="llm-profiles.get",
        path=f"/api/agent/v1/llm-profiles/{profile_id}",
        human_formatter=format_detail,
    )

