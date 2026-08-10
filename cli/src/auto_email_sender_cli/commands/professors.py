from __future__ import annotations

import json
import mimetypes
import secrets
from enum import StrEnum
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
from auto_email_sender_cli.commands.ui_handoffs import run_ui_handoff_command
from auto_email_sender_cli.errors import CliError
from auto_email_sender_cli.output import emit_error, emit_success

professors_app = typer.Typer(help="查询导师档案与标签。", no_args_is_help=True)
tags_app = typer.Typer(help="查询导师标签。", no_args_is_help=True)
community_app = typer.Typer(help="查询、比对和导入社区导师。", no_args_is_help=True)
professors_app.add_typer(tags_app, name="tags")
professors_app.add_typer(community_app, name="community")


class ProfessorUiSurface(StrEnum):
    MANAGEMENT = "management"
    HOME = "home"


class UiSelectionMode(StrEnum):
    REPLACE = "replace"
    ADD = "add"


class UiSelectionDisplay(StrEnum):
    SELECTED_ONLY = "selected-only"
    KEEP_CURRENT = "keep-current"


@professors_app.command("list")
def list_professors(
    ctx: typer.Context,
    query: Annotated[
        str | None,
        typer.Option(
            "--query",
            "--search",
            "-q",
            help="按姓名、邮箱、学校、方向或备注搜索；--search 含义更明确。",
        ),
    ] = None,
    archived: Annotated[str, typer.Option("--archived", help="active、archived 或 all。") ]="active",
    tag_id: Annotated[int | None, typer.Option("--tag-id", min=1)] = None,
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    all_items: Annotated[bool, typer.Option("--all", help="自动读取全部分页结果。") ] = False,
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
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
        fields=fields,
        human_formatter=lambda data: format_page(
            data,
            columns=(("id", "ID"), ("name", "姓名"), ("email", "邮箱"), ("university", "学校")),
        ),
    )


def _download_professor_file(
    ctx: typer.Context,
    *,
    command: str,
    path: str,
    output: Path,
    format_name: str,
    force: bool,
    human_label: str,
) -> None:
    context = cli_context(ctx)
    try:
        validate_context_options(
            context,
            supports_filter=False,
            supports_output_file=False,
        )
        destination = output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        client = AgentApiClient(timeout=360.0)
        content = client.download_bytes(
            path,
            params={"format": format_name},
        )
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
                message=f"无法写入导出文件：{exc}",
                exit_code=5,
            ) from exc
        emit_success(
            context,
            command=command,
            data={
                "output": destination.as_posix(),
                "format": format_name,
                "size_bytes": len(content),
            },
            human_text=f"{human_label}：\n{destination}",
            app_version=client.descriptor.app_version,
        )
    except CliError as error:
        emit_error(context, command=command, error=error)
        raise typer.Exit(error.exit_code) from error


@professors_app.command("export")
def export_professors(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option("--output", "-o", help="导出文件保存位置。")],
    format: Annotated[str, typer.Option("--format", help="xlsx 或 csv。") ] = "xlsx",
    force: Annotated[bool, typer.Option("--force", help="覆盖已有文件。") ] = False,
) -> None:
    _download_professor_file(
        ctx,
        command="professors.export",
        path="/api/agent/v1/professors/export",
        output=output,
        format_name=format,
        force=force,
        human_label="已导出导师表到",
    )


@professors_app.command("download-template")
def download_professor_template(
    ctx: typer.Context,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="空白导入模板保存位置。"),
    ],
    format: Annotated[str, typer.Option("--format", help="xlsx 或 csv。") ] = "xlsx",
    force: Annotated[bool, typer.Option("--force", help="覆盖已有文件。") ] = False,
) -> None:
    _download_professor_file(
        ctx,
        command="professors.download-template",
        path="/api/agent/v1/professors/import-template",
        output=output,
        format_name=format,
        force=force,
        human_label="已下载导师导入模板到",
    )


@professors_app.command("import")
def prepare_professor_import(
    ctx: typer.Context,
    file_path: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
) -> None:
    context = cli_context(ctx)
    command = "professors.import"
    try:
        validate_context_options(
            context,
            supports_filter=False,
            supports_output_file=False,
        )
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        with file_path.open("rb") as import_file:
            request_id = context.request_id or f"cli_{secrets.token_urlsafe(24)}"
            context.request_id = request_id
            client = AgentApiClient(timeout=360.0)
            data = client.request(
                "POST",
                "/api/agent/v1/professors/prepare-import",
                files={"file": (file_path.name, import_file, mime_type)},
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
            guide_topic="safety",
            app_version=client.descriptor.app_version,
            request_id=getattr(client, "last_request_id", None) or request_id,
        )
    except OSError as exc:
        error = CliError(
            code="LOCAL_FILE_UNAVAILABLE",
            message=f"无法读取导师导入文件：{exc}",
            exit_code=2,
        )
        emit_error(context, command=command, error=error, guide_topic="safety")
        raise typer.Exit(error.exit_code) from exc
    except CliError as error:
        emit_error(context, command=command, error=error, guide_topic="safety")
        raise typer.Exit(error.exit_code) from error


@community_app.command("catalog")
def get_community_catalog(
    ctx: typer.Context,
    refresh: Annotated[bool, typer.Option("--refresh", help="从社区数据源刷新目录。") ] = False,
) -> None:
    run_read_command(
        ctx,
        command="professors.community.catalog",
        path="/api/agent/v1/community-mentors/catalog",
        params={"refresh": refresh},
        guide_topic="community",
        human_formatter=format_detail,
        timeout=90.0,
    )


@community_app.command("records")
def list_community_records(
    ctx: typer.Context,
    dataset_version: Annotated[str, typer.Option("--dataset-version", help="catalog 返回的数据版本。")],
    unit_paths: Annotated[
        list[str],
        typer.Option("--unit-path", help="重复指定 catalog 中的学院分片路径。"),
    ] = [],
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
) -> None:
    run_write_command(
        ctx,
        command="professors.community.records",
        path="/api/agent/v1/community-mentors/records",
        json_body={"dataset_version": dataset_version, "unit_paths": unit_paths},
        guide_topic="community",
        human_formatter=format_detail,
        timeout=90.0,
        use_idempotency_key=False,
        fields=fields,
    )


@community_app.command("preview")
def preview_community_import(
    ctx: typer.Context,
    dataset_version: Annotated[str, typer.Option("--dataset-version", help="catalog 返回的数据版本。")],
    unit_paths: Annotated[
        list[str],
        typer.Option("--unit-path", help="重复指定 catalog 中的学院分片路径。"),
    ] = [],
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
    record_ids: Annotated[
        list[str],
        typer.Option("--record-id", help="重复指定要比对的社区导师 ID。"),
    ] = [],
) -> None:
    run_write_command(
        ctx,
        command="professors.community.preview",
        path="/api/agent/v1/community-mentors/preview",
        json_body={
            "dataset_version": dataset_version,
            "unit_paths": unit_paths,
            "record_ids": record_ids,
        },
        guide_topic="community",
        human_formatter=format_detail,
        timeout=90.0,
        use_idempotency_key=False,
        fields=fields,
    )


@community_app.command("import")
def prepare_community_import(
    ctx: typer.Context,
    items_file: Annotated[
        Path,
        typer.Option(
            "--items-file",
            help="包含 dataset_version、unit_paths 和 items 的 JSON 文件。",
        ),
    ],
) -> None:
    context = cli_context(ctx)
    command = "professors.community.import"
    try:
        payload = json.loads(items_file.read_text(encoding="utf-8"))
    except OSError as exc:
        error = CliError(
            code="LOCAL_FILE_UNAVAILABLE",
            message=f"无法读取社区导师导入文件：{exc}",
            exit_code=2,
        )
        emit_error(context, command=command, error=error, guide_topic="community")
        raise typer.Exit(error.exit_code) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        error = CliError(
            code="COMMUNITY_IMPORT_FILE_INVALID",
            message="社区导师导入文件必须是 UTF-8 编码的 JSON 对象。",
            exit_code=2,
        )
        emit_error(context, command=command, error=error, guide_topic="community")
        raise typer.Exit(error.exit_code) from exc
    if not isinstance(payload, dict):
        error = CliError(
            code="COMMUNITY_IMPORT_FILE_INVALID",
            message="社区导师导入文件必须是一个 JSON 对象。",
            exit_code=2,
        )
        emit_error(context, command=command, error=error, guide_topic="community")
        raise typer.Exit(error.exit_code)
    run_write_command(
        ctx,
        command=command,
        path="/api/agent/v1/community-mentors/prepare-import",
        json_body=payload,
        guide_topic="community",
        human_formatter=format_detail,
        timeout=90.0,
    )


@community_app.command("export-package")
def export_community_share_package(
    ctx: typer.Context,
    professor_ids: Annotated[
        list[int],
        typer.Option("--professor-id", min=1, help="重复指定要导出的本地导师 ID。"),
    ] = [],
    output: Annotated[Path, typer.Option("--output", "-o", help="导出文件保存位置。") ] = Path("community-share.xlsx"),
    force: Annotated[bool, typer.Option("--force", help="覆盖已有文件。") ] = False,
) -> None:
    context = cli_context(ctx)
    command = "professors.community.export-package"
    if not professor_ids:
        error = CliError(
            code="PROFESSOR_IDS_REQUIRED",
            message="请至少指定一位要导出的导师。",
            exit_code=2,
        )
        emit_error(context, command=command, error=error, guide_topic="community")
        raise typer.Exit(error.exit_code)
    try:
        validate_context_options(
            context,
            supports_filter=False,
            supports_output_file=False,
        )
        destination = output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        client = AgentApiClient(timeout=90.0)
        content = client.download_bytes(
            "/api/agent/v1/community-mentors/share-package",
            params={"professor_ids": ",".join(str(professor_id) for professor_id in professor_ids)},
        )
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
                message=f"无法写入导出文件：{exc}",
                exit_code=5,
            ) from exc
        emit_success(
            context,
            command=command,
            data={
                "output": destination.as_posix(),
                "professor_ids": professor_ids,
                "size_bytes": len(content),
            },
            human_text=f"已导出社区共享包到：\n{destination}",
            guide_topic="community",
            app_version=client.descriptor.app_version,
        )
    except CliError as error:
        emit_error(context, command=command, error=error, guide_topic="community")
        raise typer.Exit(error.exit_code) from error


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


@professors_app.command("create")
def create_professor(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="导师姓名。")],
    email: Annotated[str, typer.Option("--email", help="导师邮箱。")],
    title: Annotated[str | None, typer.Option("--title")] = None,
    university: Annotated[str | None, typer.Option("--university")] = None,
    school: Annotated[str | None, typer.Option("--school")] = None,
    department: Annotated[str | None, typer.Option("--department")] = None,
    research_direction: Annotated[str | None, typer.Option("--research-direction")] = None,
    recent_papers: Annotated[list[str], typer.Option("--recent-paper")] = [],
    profile_url: Annotated[str | None, typer.Option("--profile-url")] = None,
    source_url: Annotated[str | None, typer.Option("--source-url")] = None,
    personal_note: Annotated[str | None, typer.Option("--personal-note")] = None,
    tag_ids: Annotated[list[int], typer.Option("--tag-id", min=1)] = [],
) -> None:
    run_write_command(
        ctx,
        command="professors.create",
        path="/api/agent/v1/professors",
        json_body={
            "name": name,
            "email": email,
            "title": title,
            "university": university,
            "school": school,
            "department": department,
            "research_direction": research_direction,
            "recent_papers": recent_papers,
            "profile_url": profile_url,
            "source_url": source_url,
            "personal_note": personal_note,
            "tag_ids": tag_ids,
        },
        human_formatter=format_detail,
    )


@professors_app.command("update")
def update_professor(
    ctx: typer.Context,
    professor_id: Annotated[int, typer.Argument(min=1, help="导师 ID。")],
    name: Annotated[str | None, typer.Option("--name")] = None,
    email: Annotated[str | None, typer.Option("--email")] = None,
    title: Annotated[str | None, typer.Option("--title")] = None,
    university: Annotated[str | None, typer.Option("--university")] = None,
    school: Annotated[str | None, typer.Option("--school")] = None,
    department: Annotated[str | None, typer.Option("--department")] = None,
    research_direction: Annotated[str | None, typer.Option("--research-direction")] = None,
    recent_papers: Annotated[list[str] | None, typer.Option("--recent-paper")] = None,
    clear_recent_papers: Annotated[bool, typer.Option("--clear-recent-papers")] = False,
    profile_url: Annotated[str | None, typer.Option("--profile-url")] = None,
    source_url: Annotated[str | None, typer.Option("--source-url")] = None,
    personal_note: Annotated[str | None, typer.Option("--personal-note")] = None,
) -> None:
    if clear_recent_papers and recent_papers is not None:
        raise typer.BadParameter(
            "--clear-recent-papers 不能和 --recent-paper 同时使用。",
            param_hint="--clear-recent-papers",
        )
    payload = {
        key: value
        for key, value in {
            "name": name,
            "email": email,
            "title": title,
            "university": university,
            "school": school,
            "department": department,
            "research_direction": research_direction,
            "recent_papers": [] if clear_recent_papers else recent_papers,
            "profile_url": profile_url,
            "source_url": source_url,
            "personal_note": personal_note,
        }.items()
        if value is not None
    }
    if not payload:
        raise typer.BadParameter("请至少提供一个需要修改的字段。")
    run_write_command(
        ctx,
        command="professors.update",
        path=f"/api/agent/v1/professors/{professor_id}",
        method="PUT",
        json_body=payload,
        human_formatter=format_detail,
    )


@professors_app.command("archive")
def archive_professor(
    ctx: typer.Context,
    professor_id: Annotated[int, typer.Argument(min=1, help="导师 ID。")],
) -> None:
    run_write_command(
        ctx,
        command="professors.archive",
        path=f"/api/agent/v1/professors/{professor_id}/archive",
        human_formatter=format_detail,
    )


@professors_app.command("prepare-bulk-archive")
def prepare_bulk_professor_archive(
    ctx: typer.Context,
    professor_ids: Annotated[
        list[int],
        typer.Option("--professor-id", min=1, help="重复指定要移入回收站的导师 ID。"),
    ] = [],
    selection_filter: Annotated[
        str | None,
        typer.Option(
            "--selection-filter",
            help=(
                "按结构化 JSON 条件选择导师；例如 "
                "{\"name\":{\"contains_script\":\"latin\"}}。"
                "服务端会把匹配到的 ID 冻结进确认计划。"
            ),
        ),
    ] = None,
    archived: Annotated[
        str,
        typer.Option("--archived", help="选择范围：active、archived 或 all。"),
    ] = "active",
    exclude_ids: Annotated[
        list[int],
        typer.Option("--exclude-id", min=1, help="从选择结果中排除导师 ID；可重复。"),
    ] = [],
) -> None:
    if archived not in {"active", "archived", "all"}:
        raise typer.BadParameter("--archived 仅支持 active、archived 或 all。", param_hint="--archived")
    if len(set(professor_ids)) != len(professor_ids):
        raise typer.BadParameter("--professor-id 不能包含重复 ID。", param_hint="--professor-id")
    if len(set(exclude_ids)) != len(exclude_ids):
        raise typer.BadParameter("--exclude-id 不能包含重复 ID。", param_hint="--exclude-id")
    if professor_ids and selection_filter is not None:
        raise typer.BadParameter(
            "--professor-id 与 --selection-filter 不能同时使用。",
            param_hint="--selection-filter",
        )
    if not professor_ids and selection_filter is None:
        raise typer.BadParameter(
            "请提供至少一个 --professor-id，或使用 --selection-filter。",
            param_hint="--professor-id",
        )

    if selection_filter is not None:
        try:
            parsed_filter = json.loads(selection_filter)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(
                "--selection-filter 必须是合法 JSON 对象。",
                param_hint="--selection-filter",
            ) from exc
        if not isinstance(parsed_filter, dict) or not parsed_filter:
            raise typer.BadParameter(
                "--selection-filter 必须是非空 JSON 对象。",
                param_hint="--selection-filter",
            )
        json_body: dict[str, object] = {
            "selection": {
                "mode": "filter",
                "filter": {"archived": archived, "where": parsed_filter},
                "exclude_ids": exclude_ids,
            },
        }
    elif exclude_ids:
        if archived != "active":
            raise typer.BadParameter(
                "显式 --professor-id 选择不能使用 --archived；该选项只约束筛选选择。",
                param_hint="--archived",
            )
        json_body = {
            "selection": {
                "mode": "ids",
                "ids": professor_ids,
                "exclude_ids": exclude_ids,
            },
        }
    else:
        if archived != "active":
            raise typer.BadParameter(
                "显式 --professor-id 选择不能使用 --archived；该选项只约束筛选选择。",
                param_hint="--archived",
            )
        # Preserve the original wire shape for existing clients and stored
        # idempotency fingerprints.
        json_body = {"professor_ids": professor_ids}
    run_write_command(
        ctx,
        command="professors.prepare-bulk-archive",
        path="/api/agent/v1/professors/prepare-bulk-archive",
        json_body=json_body,
        guide_topic="safety",
        human_formatter=format_detail,
    )


@professors_app.command("present-selection")
def present_professor_selection(
    ctx: typer.Context,
    professor_ids: Annotated[
        list[int],
        typer.Option("--professor-id", min=1, help="重复指定要在界面中勾选的导师 ID。"),
    ] = [],
    selection_filter: Annotated[
        str | None,
        typer.Option(
            "--selection-filter",
            help=(
                "按结构化 JSON 条件冻结选择；例如 "
                "{\"name\":{\"contains_script\":\"latin\"}}。"
            ),
        ),
    ] = None,
    all_professors: Annotated[
        bool,
        typer.Option("--all", help="选择当前归档范围中的全部导师。"),
    ] = False,
    exclude_ids: Annotated[
        list[int],
        typer.Option("--exclude-id", min=1, help="从冻结选择中排除导师 ID；可重复。"),
    ] = [],
    archived: Annotated[
        str,
        typer.Option("--archived", help="筛选或 --all 的范围：active、archived 或 all。"),
    ] = "active",
    surface: Annotated[
        ProfessorUiSurface,
        typer.Option("--surface", help="management 或 home。"),
    ] = ProfessorUiSurface.MANAGEMENT,
    identity_id: Annotated[
        int | None,
        typer.Option("--identity-id", min=1, help="home 页面必须指定的发件身份 ID。"),
    ] = None,
    selection_mode: Annotated[
        UiSelectionMode,
        typer.Option("--selection-mode", help="replace 或 add。"),
    ] = UiSelectionMode.REPLACE,
    display: Annotated[
        UiSelectionDisplay,
        typer.Option("--display", help="selected-only 或 keep-current。"),
    ] = UiSelectionDisplay.SELECTED_ONLY,
) -> None:
    if archived not in {"active", "archived", "all"}:
        raise typer.BadParameter(
            "--archived 仅支持 active、archived 或 all。",
            param_hint="--archived",
        )
    if len(set(professor_ids)) != len(professor_ids):
        raise typer.BadParameter("--professor-id 不能包含重复 ID。", param_hint="--professor-id")
    if len(set(exclude_ids)) != len(exclude_ids):
        raise typer.BadParameter("--exclude-id 不能包含重复 ID。", param_hint="--exclude-id")
    selection_inputs = sum(
        (bool(professor_ids), selection_filter is not None, all_professors),
    )
    if selection_inputs != 1:
        raise typer.BadParameter(
            "请且只能使用一种选择方式：--professor-id、--selection-filter 或 --all。",
            param_hint="--professor-id",
        )
    if surface is ProfessorUiSurface.HOME and identity_id is None:
        raise typer.BadParameter("--surface home 必须提供 --identity-id。", param_hint="--identity-id")
    if surface is ProfessorUiSurface.MANAGEMENT and identity_id is not None:
        raise typer.BadParameter(
            "--surface management 不能提供 --identity-id。",
            param_hint="--identity-id",
        )
    if surface is ProfessorUiSurface.HOME and archived != "active":
        raise typer.BadParameter(
            "--surface home 只支持 --archived active；已归档导师请在 management 页面查看。",
            param_hint="--archived",
        )

    if selection_filter is not None:
        try:
            parsed_filter = json.loads(selection_filter)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(
                "--selection-filter 必须是合法 JSON 对象。",
                param_hint="--selection-filter",
            ) from exc
        if not isinstance(parsed_filter, dict) or not parsed_filter:
            raise typer.BadParameter(
                "--selection-filter 必须是非空 JSON 对象。",
                param_hint="--selection-filter",
            )
        selection: dict[str, object] = {
            "mode": "filter",
            "filter": {"archived": archived, "where": parsed_filter},
            "exclude_ids": exclude_ids,
        }
    elif all_professors:
        if archived == "active":
            selection = {
                "mode": "all",
                "exclude_ids": exclude_ids,
            }
        else:
            selection = {
                "mode": "filter",
                "filter": {"archived": archived},
                "exclude_ids": exclude_ids,
            }
    else:
        if archived != "active":
            raise typer.BadParameter(
                "显式 --professor-id 不使用 --archived；导师状态由 ID 自身决定。",
                param_hint="--archived",
            )
        if not set(exclude_ids).issubset(professor_ids):
            raise typer.BadParameter(
                "显式 ID 模式中的 --exclude-id 必须属于 --professor-id。",
                param_hint="--exclude-id",
            )
        selection = {
            "mode": "ids",
            "ids": professor_ids,
            "exclude_ids": exclude_ids,
        }

    run_ui_handoff_command(
        ctx,
        command="professors.present-selection",
        path="/api/agent/v1/professors/present-selection",
        json_body={
            "selection": selection,
            "surface": f"professors.{surface.value}",
            "selection_mode": selection_mode.value,
            "display": display.value.replace("-", "_"),
            "identity_id": identity_id,
        },
        use_idempotency_key=True,
    )


@professors_app.command("restore")
def restore_professor(
    ctx: typer.Context,
    professor_id: Annotated[int, typer.Argument(min=1, help="导师 ID。")],
) -> None:
    run_write_command(
        ctx,
        command="professors.restore",
        path=f"/api/agent/v1/professors/{professor_id}/restore",
        human_formatter=format_detail,
    )


@tags_app.command("list")
def list_professor_tags(
    ctx: typer.Context,
    cursor: Annotated[int, typer.Option("--cursor", min=0)] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 25,
    all_items: Annotated[bool, typer.Option("--all")] = False,
    fields: Annotated[str | None, typer.Option("--fields", help="只返回需要的字段，逗号分隔。") ] = None,
) -> None:
    run_read_command(
        ctx,
        command="professors.tags.list",
        path="/api/agent/v1/professor-tags",
        params={"cursor": cursor, "limit": limit},
        fetch_all=all_items,
        fields=fields,
        human_formatter=lambda data: format_page(
            data,
            columns=(("id", "ID"), ("name", "标签")),
        ),
    )


@tags_app.command("create")
def create_professor_tag(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name")],
    text_color: Annotated[str, typer.Option("--text-color")],
    background_color: Annotated[str, typer.Option("--background-color")],
) -> None:
    run_write_command(
        ctx,
        command="professors.tags.create",
        path="/api/agent/v1/professor-tags",
        json_body={
            "name": name,
            "text_color": text_color,
            "background_color": background_color,
        },
        human_formatter=format_detail,
    )


@tags_app.command("usage")
def get_professor_tag_usage(
    ctx: typer.Context,
    tag_id: Annotated[int, typer.Argument(min=1, help="标签 ID。")],
) -> None:
    run_read_command(
        ctx,
        command="professors.tags.usage",
        path=f"/api/agent/v1/professor-tags/{tag_id}/usage",
        human_formatter=format_detail,
    )


@tags_app.command("prepare-delete")
def prepare_professor_tag_delete(
    ctx: typer.Context,
    tag_id: Annotated[int, typer.Argument(min=1, help="标签 ID。")],
) -> None:
    run_write_command(
        ctx,
        command="professors.tags.prepare-delete",
        path=f"/api/agent/v1/professor-tags/{tag_id}/prepare-delete",
        guide_topic="safety",
        human_formatter=format_detail,
    )


@tags_app.command("set")
def set_professor_tags(
    ctx: typer.Context,
    professor_id: Annotated[int, typer.Argument(min=1, help="导师 ID。")],
    tag_ids: Annotated[
        list[int],
        typer.Option("--tag-id", min=1, help="重复指定；不提供则清空标签。"),
    ] = [],
) -> None:
    run_write_command(
        ctx,
        command="professors.tags.set",
        path=f"/api/agent/v1/professors/{professor_id}/tags",
        method="PUT",
        json_body={"tag_ids": tag_ids},
        human_formatter=format_detail,
    )


@tags_app.command("prepare-bulk")
def prepare_bulk_professor_tags(
    ctx: typer.Context,
    professor_ids: Annotated[
        list[int],
        typer.Option("--professor-id", min=1, help="重复指定要修改的导师 ID。"),
    ],
    mode: Annotated[
        str,
        typer.Option("--mode", help="add、remove 或 replace。"),
    ] = "add",
    tag_ids: Annotated[
        list[int],
        typer.Option("--tag-id", min=1, help="重复指定目标标签；replace 可不提供以清空。"),
    ] = [],
) -> None:
    run_write_command(
        ctx,
        command="professors.tags.prepare-bulk",
        path="/api/agent/v1/professors/prepare-bulk-tags",
        json_body={
            "professor_ids": professor_ids,
            "mode": mode,
            "tag_ids": tag_ids,
        },
        guide_topic="safety",
        human_formatter=format_detail,
    )
