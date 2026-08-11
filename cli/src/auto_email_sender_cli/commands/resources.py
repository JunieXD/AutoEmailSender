from __future__ import annotations

import mimetypes
import secrets
from pathlib import Path
from typing import Annotated

import typer

from auto_email_sender_cli.client import AgentApiClient
from auto_email_sender_cli.commands.common import (
    add_mutation_receipt,
    cli_context,
    format_detail,
    format_page,
    run_read_command,
    run_write_command,
    validate_context_options,
)
from auto_email_sender_cli.errors import CliError
from auto_email_sender_cli.output import emit_error, emit_success


templates_app = typer.Typer(help="查询邮件模板。", no_args_is_help=True)
materials_app = typer.Typer(help="查询 AI 参考材料和可选附件。", no_args_is_help=True)
identities_app = typer.Typer(help="查询发件身份的脱敏视图。", no_args_is_help=True)
llm_profiles_app = typer.Typer(help="查询模型配置的脱敏视图。", no_args_is_help=True)


@templates_app.command("list")
def list_templates(
    ctx: typer.Context,
    include_archived: Annotated[bool, typer.Option("--include-archived")] = False,
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    all_items: Annotated[bool, typer.Option("--all")] = False,
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
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
        fields=fields,
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


@templates_app.command("import-file")
def import_template_file(
    ctx: typer.Context,
    file_path: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
) -> None:
    context = cli_context(ctx)
    command = "templates.import-file"
    try:
        validate_context_options(
            context,
            supports_filter=False,
            supports_output_file=False,
        )
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        with file_path.open("rb") as template_file:
            request_id = context.request_id or f"cli_{secrets.token_urlsafe(24)}"
            context.request_id = request_id
            client = AgentApiClient(timeout=360.0)
            data = client.request(
                "POST",
                "/api/agent/v1/templates/import-file",
                files={"file": (file_path.name, template_file, mime_type)},
                idempotency_key=request_id,
                if_revision=context.if_revision,
            )
        data = add_mutation_receipt(
            data,
            command=command,
            request_id=request_id,
            json_body=None,
            response_headers=getattr(client, "last_response_headers", {}),
        )
        emit_success(
            context,
            command=command,
            data=data,
            human_text=format_detail(data),
            app_version=client.descriptor.app_version,
            request_id=getattr(client, "last_request_id", None) or request_id,
        )
    except OSError as exc:
        error = CliError(
            code="LOCAL_FILE_UNAVAILABLE",
            message=f"无法读取模板文件：{exc}",
            exit_code=2,
        )
        emit_error(context, command=command, error=error)
        raise typer.Exit(error.exit_code) from exc
    except CliError as error:
        emit_error(context, command=command, error=error)
        raise typer.Exit(error.exit_code) from error


@templates_app.command("create")
def create_template(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="模板名称。")],
    generation_mode: Annotated[
        str,
        typer.Option("--generation-mode", help="llm 或 template。"),
    ] = "llm",
    subject: Annotated[str | None, typer.Option("--subject")] = None,
    body_text: Annotated[str | None, typer.Option("--body-text")] = None,
    body_html: Annotated[str | None, typer.Option("--body-html")] = None,
    set_default: Annotated[bool, typer.Option("--set-default")] = False,
) -> None:
    run_write_command(
        ctx,
        command="templates.create",
        path="/api/agent/v1/templates",
        json_body={
            "name": name,
            "recommended_generation_mode": generation_mode,
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "is_default": set_default,
        },
        human_formatter=format_detail,
    )


@templates_app.command("update")
def update_template(
    ctx: typer.Context,
    template_id: Annotated[int, typer.Argument(min=1, help="模板 ID。")],
    name: Annotated[str | None, typer.Option("--name")] = None,
    generation_mode: Annotated[str | None, typer.Option("--generation-mode")] = None,
    subject: Annotated[str | None, typer.Option("--subject")] = None,
    body_text: Annotated[str | None, typer.Option("--body-text")] = None,
    body_html: Annotated[str | None, typer.Option("--body-html")] = None,
    set_default: Annotated[bool, typer.Option("--set-default")] = False,
    unset_default: Annotated[bool, typer.Option("--unset-default")] = False,
) -> None:
    if set_default and unset_default:
        raise typer.BadParameter(
            "--set-default 和 --unset-default 不能同时使用。",
            param_hint="--set-default",
        )
    payload = {
        key: value
        for key, value in {
            "name": name,
            "recommended_generation_mode": generation_mode,
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
        }.items()
        if value is not None
    }
    if set_default:
        payload["is_default"] = True
    elif unset_default:
        payload["is_default"] = False
    if not payload:
        raise typer.BadParameter("请至少提供一个需要修改的字段。")
    run_write_command(
        ctx,
        command="templates.update",
        path=f"/api/agent/v1/templates/{template_id}",
        method="PUT",
        json_body=payload,
        human_formatter=format_detail,
    )


@templates_app.command("duplicate")
def duplicate_template(
    ctx: typer.Context,
    template_id: Annotated[int, typer.Argument(min=1, help="模板 ID。")],
) -> None:
    run_write_command(
        ctx,
        command="templates.duplicate",
        path=f"/api/agent/v1/templates/{template_id}/duplicate",
        human_formatter=format_detail,
    )


@templates_app.command("set-default")
def set_default_template(
    ctx: typer.Context,
    template_id: Annotated[int, typer.Argument(min=1, help="模板 ID。")],
) -> None:
    run_write_command(
        ctx,
        command="templates.set-default",
        path=f"/api/agent/v1/templates/{template_id}/default",
        human_formatter=format_detail,
    )


@templates_app.command("restore")
def restore_template(
    ctx: typer.Context,
    template_id: Annotated[int, typer.Argument(min=1, help="模板 ID。")],
) -> None:
    run_write_command(
        ctx,
        command="templates.restore",
        path=f"/api/agent/v1/templates/{template_id}/restore",
        human_formatter=format_detail,
    )


@templates_app.command("prepare-archive")
def prepare_template_archive(
    ctx: typer.Context,
    template_id: Annotated[int, typer.Argument(min=1, help="模板 ID。")],
) -> None:
    run_write_command(
        ctx,
        command="templates.prepare-archive",
        path=f"/api/agent/v1/templates/{template_id}/prepare-archive",
        human_formatter=format_detail,
    )


@materials_app.command("list")
def list_materials(
    ctx: typer.Context,
    identity_id: Annotated[
        int | None,
        typer.Option(
            "--identity-id",
            min=1,
            help="兼容参数：仅查看由该身份上传的材料。",
        ),
    ] = None,
    source_identity_id: Annotated[
        int | None,
        typer.Option("--source-identity-id", min=1, help="仅查看由该身份历史上传的材料。"),
    ] = None,
    target_identity_id: Annotated[
        int | None,
        typer.Option(
            "--target-identity-id",
            min=1,
            help="按该身份标记默认材料，不限制全局材料列表。",
        ),
    ] = None,
    material_type: Annotated[str | None, typer.Option("--material-type")] = None,
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    all_items: Annotated[bool, typer.Option("--all")] = False,
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
) -> None:
    run_read_command(
        ctx,
        command="materials.list",
        path="/api/agent/v1/materials",
        params={
            "identity_id": identity_id,
            "source_identity_id": source_identity_id,
            "target_identity_id": target_identity_id,
            "material_type": material_type,
            "cursor": cursor,
            "limit": limit,
        },
        guide_topic="materials",
        fetch_all=all_items,
        fields=fields,
        human_formatter=lambda data: format_page(
            data,
            columns=(
                ("id", "ID"),
                ("display_name", "材料"),
                ("source_identity_id", "上传来源身份"),
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
    target_identity_id: Annotated[
        int | None,
        typer.Option(
            "--target-identity-id",
            min=1,
            help="按该身份判断材料是否为默认。",
        ),
    ] = None,
    include_text: Annotated[bool, typer.Option("--include-text", help="包含已提取的材料文本。") ] = False,
) -> None:
    run_read_command(
        ctx,
        command="materials.get",
        path=f"/api/agent/v1/materials/{material_id}",
        params={
            "target_identity_id": target_identity_id,
            "include_text": include_text,
        },
        guide_topic="materials",
        human_formatter=format_detail,
    )


@materials_app.command("upload")
def upload_material(
    ctx: typer.Context,
    file_path: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    identity_id: Annotated[
        int | None,
        typer.Option(
            "--identity-id",
            min=1,
            help="可选上传来源；若该身份尚无默认材料，合格文件会自动设为默认。",
        ),
    ] = None,
    material_type: Annotated[str, typer.Option("--material-type")] = "other",
    display_name: Annotated[str | None, typer.Option("--display-name")] = None,
) -> None:
    context = cli_context(ctx)
    try:
        validate_context_options(
            context,
            supports_filter=False,
            supports_output_file=False,
        )
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        with file_path.open("rb") as uploaded_file:
            request_id = context.request_id or f"cli_{secrets.token_urlsafe(24)}"
            context.request_id = request_id
            client = AgentApiClient(timeout=360.0)
            data = client.request(
                "POST",
                "/api/agent/v1/materials",
                data={
                    key: value
                    for key, value in {
                        "identity_id": identity_id,
                        "material_type": material_type,
                        "display_name": display_name or "",
                    }.items()
                    if value is not None
                },
                files={"file": (file_path.name, uploaded_file, mime_type)},
                idempotency_key=request_id,
                if_revision=context.if_revision,
            )
        data = add_mutation_receipt(
            data,
            command="materials.upload",
            request_id=request_id,
            json_body={"identity_id": identity_id, "material_type": material_type},
            response_headers=getattr(client, "last_response_headers", {}),
        )
        emit_success(
            context,
            command="materials.upload",
            data=data,
            human_text=format_detail(data),
            guide_topic="materials",
            app_version=client.descriptor.app_version,
            request_id=getattr(client, "last_request_id", None) or request_id,
        )
    except OSError as exc:
        error = CliError(
            code="LOCAL_FILE_UNAVAILABLE",
            message=f"无法读取材料文件：{exc}",
            exit_code=2,
        )
        emit_error(context, command="materials.upload", error=error, guide_topic="materials")
        raise typer.Exit(error.exit_code) from exc
    except CliError as error:
        emit_error(context, command="materials.upload", error=error, guide_topic="materials")
        raise typer.Exit(error.exit_code) from error


@materials_app.command("set-primary")
def set_primary_material(
    ctx: typer.Context,
    material_id: Annotated[int, typer.Argument(min=1)],
    identity_id: Annotated[
        int,
        typer.Option(
            "--identity-id",
            min=1,
            help="要使用该默认材料的发件身份。",
        ),
    ],
) -> None:
    run_write_command(
        ctx,
        command="materials.set-primary",
        path=f"/api/agent/v1/materials/{material_id}/set-primary",
        params={"identity_id": identity_id},
        guide_topic="materials",
        human_formatter=format_detail,
    )


@materials_app.command("download")
def download_material(
    ctx: typer.Context,
    material_id: Annotated[int, typer.Argument(min=1, help="材料 ID。")],
    output: Annotated[Path, typer.Option("--output", "-o", help="下载保存位置。")],
    force: Annotated[bool, typer.Option("--force", help="覆盖已有文件。") ] = False,
) -> None:
    context = cli_context(ctx)
    command = "materials.download"
    try:
        validate_context_options(
            context,
            supports_filter=False,
            supports_output_file=False,
        )
        destination = output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        client = AgentApiClient(timeout=360.0)
        content = client.download_bytes(f"/api/agent/v1/materials/{material_id}/download")
        try:
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
                message=f"无法写入下载文件：{exc}",
                exit_code=5,
            ) from exc
        emit_success(
            context,
            command=command,
            data={
                "material_id": material_id,
                "output": destination.as_posix(),
                "size_bytes": len(content),
            },
            human_text=f"已下载材料到：\n{destination}",
            guide_topic="materials",
            app_version=client.descriptor.app_version,
        )
    except CliError as error:
        emit_error(context, command=command, error=error, guide_topic="materials")
        raise typer.Exit(error.exit_code) from error


@materials_app.command("prepare-delete")
def prepare_material_delete(
    ctx: typer.Context,
    material_id: Annotated[int, typer.Argument(min=1, help="材料 ID。")],
) -> None:
    run_write_command(
        ctx,
        command="materials.prepare-delete",
        path=f"/api/agent/v1/materials/{material_id}/prepare-delete",
        guide_topic="materials",
        human_formatter=format_detail,
    )


@identities_app.command("list")
def list_identities(
    ctx: typer.Context,
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    all_items: Annotated[bool, typer.Option("--all")] = False,
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
) -> None:
    run_read_command(
        ctx,
        command="identities.list",
        path="/api/agent/v1/identities",
        params={"cursor": cursor, "limit": limit},
        fetch_all=all_items,
        fields=fields,
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


@identities_app.command("update-settings")
def update_identity_settings(
    ctx: typer.Context,
    identity_id: Annotated[int, typer.Argument(min=1, help="身份 ID。")],
    profile_name: Annotated[str | None, typer.Option("--profile-name")] = None,
    sender_name: Annotated[str | None, typer.Option("--sender-name")] = None,
    default_language: Annotated[str | None, typer.Option("--default-language")] = None,
    outreach_generation_mode: Annotated[
        str | None,
        typer.Option("--outreach-generation-mode", help="llm 或 template。"),
    ] = None,
    match_threshold: Annotated[
        int | None,
        typer.Option("--match-threshold", min=0, max=100),
    ] = None,
    clear_match_threshold: Annotated[bool, typer.Option("--clear-match-threshold")] = False,
    daily_send_limit: Annotated[
        int | None,
        typer.Option("--daily-send-limit", min=0),
    ] = None,
    clear_daily_send_limit: Annotated[bool, typer.Option("--clear-daily-send-limit")] = False,
    send_interval_min: Annotated[
        int | None,
        typer.Option("--send-interval-min", min=0),
    ] = None,
    clear_send_interval_min: Annotated[
        bool,
        typer.Option("--clear-send-interval-min"),
    ] = False,
    send_interval_max: Annotated[
        int | None,
        typer.Option("--send-interval-max", min=0),
    ] = None,
    clear_send_interval_max: Annotated[
        bool,
        typer.Option("--clear-send-interval-max"),
    ] = False,
    same_domain_cooldown_minutes: Annotated[
        int | None,
        typer.Option("--same-domain-cooldown-minutes", min=0),
    ] = None,
    clear_same_domain_cooldown_minutes: Annotated[
        bool,
        typer.Option("--clear-same-domain-cooldown-minutes"),
    ] = False,
) -> None:
    payload: dict[str, object] = {
        key: value
        for key, value in {
            "profile_name": profile_name,
            "sender_name": sender_name,
            "default_language": default_language,
            "outreach_generation_mode": outreach_generation_mode,
        }.items()
        if value is not None
    }
    for field_name, value, should_clear in (
        ("match_threshold", match_threshold, clear_match_threshold),
        ("daily_send_limit", daily_send_limit, clear_daily_send_limit),
        ("send_interval_min", send_interval_min, clear_send_interval_min),
        ("send_interval_max", send_interval_max, clear_send_interval_max),
        (
            "same_domain_cooldown_minutes",
            same_domain_cooldown_minutes,
            clear_same_domain_cooldown_minutes,
        ),
    ):
        _add_clearable_settings_field(payload, field_name, value, should_clear)
    if not payload:
        raise typer.BadParameter("请至少提供一个需要修改的身份设置字段。")
    run_write_command(
        ctx,
        command="identities.update-settings",
        path=f"/api/agent/v1/identities/{identity_id}/settings",
        method="PUT",
        json_body=payload,
        guide_topic="identities",
        human_formatter=format_detail,
    )


@identities_app.command("set-default")
def set_default_identity(
    ctx: typer.Context,
    identity_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="identities.set-default",
        path=f"/api/agent/v1/identities/{identity_id}/default",
        guide_topic="identities",
        human_formatter=format_detail,
    )


@identities_app.command("set-default-template")
def set_identity_default_template(
    ctx: typer.Context,
    identity_id: Annotated[int, typer.Argument(min=1)],
    template_id: Annotated[int | None, typer.Option("--template-id", min=1)] = None,
    clear_template: Annotated[
        bool,
        typer.Option("--clear-template", help="清除该身份的默认模板。"),
    ] = False,
) -> None:
    if template_id is None and not clear_template:
        raise typer.BadParameter("请提供 --template-id，或明确使用 --clear-template。")
    if template_id is not None and clear_template:
        raise typer.BadParameter("--template-id 和 --clear-template 不能同时使用。")
    run_write_command(
        ctx,
        command="identities.set-default-template",
        path=f"/api/agent/v1/identities/{identity_id}/default-template",
        json_body={"template_id": None if clear_template else template_id},
        guide_topic="identities",
        human_formatter=format_detail,
    )


@identities_app.command("test-smtp")
def test_identity_smtp(
    ctx: typer.Context,
    identity_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="identities.test-smtp",
        path=f"/api/agent/v1/identities/{identity_id}/smtp-test",
        guide_topic="identities",
        human_formatter=format_detail,
    )


@identities_app.command("test-imap")
def test_identity_imap(
    ctx: typer.Context,
    identity_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="identities.test-imap",
        path=f"/api/agent/v1/identities/{identity_id}/imap-test",
        guide_topic="identities",
        human_formatter=format_detail,
    )


@llm_profiles_app.command("list")
def list_llm_profiles(
    ctx: typer.Context,
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    all_items: Annotated[bool, typer.Option("--all")] = False,
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
) -> None:
    run_read_command(
        ctx,
        command="llm-profiles.list",
        path="/api/agent/v1/llm-profiles",
        params={"cursor": cursor, "limit": limit},
        fetch_all=all_items,
        fields=fields,
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


@llm_profiles_app.command("update-settings")
def update_llm_profile_settings(
    ctx: typer.Context,
    profile_id: Annotated[int, typer.Argument(min=1, help="模型配置 ID。")],
    name: Annotated[str | None, typer.Option("--name")] = None,
    model_name: Annotated[str | None, typer.Option("--model-name")] = None,
    temperature: Annotated[
        float | None,
        typer.Option("--temperature", min=0, max=2),
    ] = None,
    clear_temperature: Annotated[bool, typer.Option("--clear-temperature")] = False,
    max_tokens: Annotated[int | None, typer.Option("--max-tokens", min=1)] = None,
    clear_max_tokens: Annotated[bool, typer.Option("--clear-max-tokens")] = False,
) -> None:
    payload: dict[str, object] = {
        key: value
        for key, value in {"name": name, "model_name": model_name}.items()
        if value is not None
    }
    _add_clearable_settings_field(payload, "temperature", temperature, clear_temperature)
    _add_clearable_settings_field(payload, "max_tokens", max_tokens, clear_max_tokens)
    if not payload:
        raise typer.BadParameter("请至少提供一个需要修改的模型设置字段。")
    run_write_command(
        ctx,
        command="llm-profiles.update-settings",
        path=f"/api/agent/v1/llm-profiles/{profile_id}/settings",
        method="PUT",
        json_body=payload,
        guide_topic="llm-profiles",
        human_formatter=format_detail,
    )


@llm_profiles_app.command("set-default")
def set_default_llm_profile(
    ctx: typer.Context,
    profile_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="llm-profiles.set-default",
        path=f"/api/agent/v1/llm-profiles/{profile_id}/default",
        guide_topic="llm-profiles",
        human_formatter=format_detail,
    )


@llm_profiles_app.command("models")
def fetch_llm_profile_models(
    ctx: typer.Context,
    profile_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_read_command(
        ctx,
        command="llm-profiles.models",
        path=f"/api/agent/v1/llm-profiles/{profile_id}/models",
        guide_topic="llm-profiles",
        human_formatter=format_detail,
        timeout=360.0,
    )


@llm_profiles_app.command("test")
def test_llm_profile(
    ctx: typer.Context,
    profile_id: Annotated[int, typer.Argument(min=1)],
) -> None:
    run_write_command(
        ctx,
        command="llm-profiles.test",
        path=f"/api/agent/v1/llm-profiles/{profile_id}/test",
        guide_topic="llm-profiles",
        human_formatter=format_detail,
    )


def _add_clearable_settings_field(
    payload: dict[str, object],
    field_name: str,
    value: int | float | None,
    should_clear: bool,
) -> None:
    if value is not None and should_clear:
        option_name = field_name.replace("_", "-")
        raise typer.BadParameter(
            f"--{option_name} 不能和 --clear-{option_name} 同时使用。",
            param_hint=f"--clear-{option_name}",
        )
    if value is not None:
        payload[field_name] = value
    elif should_clear:
        payload[field_name] = None
