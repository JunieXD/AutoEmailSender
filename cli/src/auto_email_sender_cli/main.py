from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Annotated

import httpx
import typer
from typer._click.core import ParameterSource
from typer._click.exceptions import UsageError
from typer.core import TyperGroup

from auto_email_sender_cli.capabilities import (
    CAPABILITIES,
    CAPABILITY_CATALOG_VERSION,
    CONTRACT_VERSION,
    capability_catalog_revision,
    list_capability_cards,
    list_capabilities,
    list_resource_catalog,
    normalize_capability_command,
    suggest_capabilities,
)
from auto_email_sender_cli.agent_installation import inspect_agent_skill_installation
from auto_email_sender_cli.commands import (
    campaigns_app,
    communications_app,
    communication_groups_app,
    crawler_app,
    dashboard_app,
    diagnostics_app,
    drafts_app,
    enrichment_app,
    identities_app,
    llm_profiles_app,
    matching_app,
    materials_app,
    plans_app,
    professors_app,
    settings_app,
    tasks_app,
    test_email_app,
    templates_app,
    usage_app,
    workspaces_app,
)
from auto_email_sender_cli.commands.wait import wait_for_resource
from auto_email_sender_cli.commands.common import validate_context_options
from auto_email_sender_cli.errors import CliError
from auto_email_sender_cli.describe import (
    DESCRIPTION_VIEWS,
    compact_command_description,
    describe_command,
    describe_command_revisions,
    description_sections,
)
from auto_email_sender_cli.guide import GUIDE_TOPICS, get_guide
from auto_email_sender_cli.invoke import invoke_json_command
from auto_email_sender_cli.output import (
    CliContext,
    OutputFormat,
    ResultProjection,
    emit_error,
    emit_success,
)
from auto_email_sender_cli.runtime import (
    get_runtime_file_path,
    load_runtime_descriptor,
    process_is_running,
)
from auto_email_sender_cli.version import (
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    get_build_identity,
    get_cli_version,
)


class AgentTyperGroup(TyperGroup):
    """Keep parser failures inside the structured Agent result protocol."""

    def main(self, *args: object, **kwargs: object) -> object:
        standalone_mode = bool(kwargs.pop("standalone_mode", True))
        raw_args = kwargs.get("args")
        try:
            result = super().main(*args, standalone_mode=False, **kwargs)
        except UsageError as exc:
            _emit_usage_error(exc, raw_args)
            if standalone_mode:
                raise SystemExit(exc.exit_code) from None
            raise typer.Exit(exc.exit_code) from exc
        if standalone_mode:
            raise SystemExit(int(result) if isinstance(result, int) else 0)
        return result


def _emit_usage_error(error: UsageError, raw_args: object) -> None:
    arguments = (
        [str(item) for item in raw_args]
        if isinstance(raw_args, (list, tuple))
        else list(sys.argv[1:])
    )
    context = CliContext(output_format=_output_format_from_arguments(arguments))
    command = _command_from_usage_context(error)
    details: dict[str, object] = {
        "error_type": type(error).__name__,
        "command": command,
    }
    parameter_hint = getattr(error, "param_hint", None)
    if parameter_hint:
        details["parameter"] = str(parameter_hint)
    emit_error(
        context,
        command=command,
        error=CliError(
            code="INVALID_ARGUMENT",
            message=error.format_message(),
            exit_code=error.exit_code,
            details=details,
            suggested_command=(
                f"auto-email-sender --format json describe --command {command}"
                if command != "cli"
                else "auto-email-sender --format json capabilities"
            ),
        ),
    )


def _output_format_from_arguments(arguments: list[str]) -> OutputFormat:
    if "--json" in arguments:
        return OutputFormat.JSON
    for index, argument in enumerate(arguments):
        if argument.startswith("--format="):
            value = argument.split("=", 1)[1].strip().lower()
            return _output_format_or_human(value)
        if argument == "--format" and index + 1 < len(arguments):
            return _output_format_or_human(arguments[index + 1].strip().lower())
    return OutputFormat.HUMAN


def _output_format_or_human(value: str) -> OutputFormat:
    try:
        return OutputFormat(value)
    except ValueError:
        # An invalid --format value is itself a parser error. JSON cannot be
        # inferred safely in that case, so preserve the normal human channel.
        return OutputFormat.HUMAN


def _command_from_usage_context(error: UsageError) -> str:
    names: list[str] = []
    context = error.ctx
    while context is not None:
        if context.parent is not None and context.info_name:
            names.append(str(context.info_name))
        context = context.parent
    return ".".join(reversed(names)) or "cli"


app = typer.Typer(
    name="auto-email-sender",
    help=(
        "让本地 Agent 安全查询和操作 Auto Email Sender。\n"
        "先用 capabilities 逐层发现能力，再用 describe 读取选中命令的实时契约。"
    ),
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    cls=AgentTyperGroup,
)
app.add_typer(professors_app, name="professors")
app.add_typer(campaigns_app, name="campaigns")
app.add_typer(communications_app, name="communications")
app.add_typer(communication_groups_app, name="communication-groups")
app.add_typer(crawler_app, name="crawler")
app.add_typer(dashboard_app, name="dashboard")
app.add_typer(diagnostics_app, name="diagnostics")
app.add_typer(templates_app, name="templates")
app.add_typer(materials_app, name="materials")
app.add_typer(identities_app, name="identities")
app.add_typer(llm_profiles_app, name="llm-profiles")
app.add_typer(matching_app, name="matching")
app.add_typer(enrichment_app, name="enrichment")
app.add_typer(drafts_app, name="drafts")
app.add_typer(plans_app, name="plans")
app.add_typer(settings_app, name="settings")
app.add_typer(tasks_app, name="tasks")
app.add_typer(test_email_app, name="test-email")
app.add_typer(usage_app, name="usage")
app.add_typer(workspaces_app, name="workspaces")
app.command("wait", help="等待一个已运行的后台任务进入终态；不会启动桌面应用。", no_args_is_help=True)(wait_for_resource)


def _current_command_contract_revisions(
    commands: list[str] | None = None,
) -> dict[str, str]:
    """Materialize parser-derived revisions for the published command set.

    This function deliberately runs against the live Typer tree.  A changed
    flag, argument type, output schema, semantic manifest, or state transition
    therefore invalidates a cached discovery catalog without relying on a
    separately maintained version list.
    """

    selected_commands = (
        commands
        if commands is not None
        else [
            capability.command
            for capability in CAPABILITIES
            if capability.availability == "available"
        ]
    )
    revisions = describe_command_revisions(app, selected_commands)
    missing = sorted(set(selected_commands) - set(revisions))
    if missing:
        raise RuntimeError(f"Published capability is not describable: {', '.join(missing)}")
    return revisions


@app.callback()
def root(
    ctx: typer.Context,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="输出格式：table、json 或 jsonl。"),
    ] = OutputFormat.HUMAN,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="等同于 --format json。"),
    ] = False,
    request_id: Annotated[
        str | None,
        typer.Option(
            "--request-id",
            help="可复用的操作标识；网络超时后用同一值重试不会重复本地副作用。",
        ),
    ] = None,
    if_revision: Annotated[
        str | None,
        typer.Option(
            "--if-revision",
            help="只在对象版本仍与读取结果一致时写入；冲突会返回结构化错误。",
        ),
    ] = None,
    output_file: Annotated[
        str | None,
        typer.Option(
            "--output-file",
            help="把集合结果写入 JSONL 文件；stdout 只返回导出摘要。",
        ),
    ] = None,
    force_output: Annotated[
        bool,
        typer.Option("--force-output", help="允许覆盖 --output-file 指定的已有文件。"),
    ] = False,
    filter_expression: Annotated[
        str | None,
        typer.Option(
            "--filter",
            help="集合结构化筛选 JSON，例如 '{\"status\":{\"eq\":\"review_required\"}}'。",
        ),
    ] = None,
    projection: Annotated[
        ResultProjection,
        typer.Option(
            "--projection",
            help="业务结果输出：summary（默认，正文/日志/证据摘要）或 full（显式展开全部）。",
        ),
    ] = ResultProjection.SUMMARY,
    expand: Annotated[
        list[str],
        typer.Option(
            "--expand",
            help="在 summary 中显式展开字段名或 JSON Pointer；可重复，例如 --expand body_text。",
        ),
    ] = [],
) -> None:
    specified_options = frozenset(
        name
        for name in (
            "request_id",
            "if_revision",
            "output_file",
            "force_output",
            "filter_expression",
            "projection",
            "expand",
        )
        if ctx.get_parameter_source(name)
        not in {None, ParameterSource.DEFAULT, ParameterSource.DEFAULT_MAP}
    )
    ctx.obj = CliContext(
        output_format=OutputFormat.JSON if json_output else output_format,
        request_id=request_id,
        filter_expression=filter_expression,
        if_revision=if_revision,
        output_file=output_file,
        force_output=force_output,
        projection=projection,
        expand=tuple(expand),
        specified_options=specified_options,
    )


@app.command("version")
def version_command(ctx: typer.Context) -> None:
    context = _context(ctx)
    _validate_system_context(context, "version")
    version = get_cli_version()
    build = get_build_identity()
    emit_success(
        context,
        command="version",
        data={
            "cli_version": version,
            "protocol_version": PROTOCOL_VERSION,
            "schema_version": SCHEMA_VERSION,
            "contract_version": CONTRACT_VERSION,
            "catalog_version": CAPABILITY_CATALOG_VERSION,
            "build_revision": build["revision"],
            "build_kind": build["kind"],
            "build_dirty": build["dirty"],
        },
        human_text=(
            f"Auto Email Sender CLI {version}\n"
            f"协议版本 {PROTOCOL_VERSION}\n"
            f"Schema {SCHEMA_VERSION} / 合同 {CONTRACT_VERSION} / 目录 {CAPABILITY_CATALOG_VERSION}\n"
            f"构建 {build['revision']}"
        ),
    )


@app.command("guide")
def guide_command(
    ctx: typer.Context,
    topic: Annotated[
        str | None,
        typer.Option("--topic", help="说明主题。"),
    ] = None,
) -> None:
    context = _context(ctx)
    _validate_system_context(context, "guide")
    try:
        guide = get_guide(topic)
    except KeyError as exc:
        error = CliError(
            code="INVALID_GUIDE_TOPIC",
            message=str(exc).strip("'"),
            exit_code=2,
            details={"available_topics": sorted(GUIDE_TOPICS)},
        )
        emit_error(context, command="guide", error=error)
        raise typer.Exit(error.exit_code) from exc
    human = _format_guide_human(guide)
    emit_success(
        context,
        command="guide",
        data=guide,
        human_text=human,
        guide_topic=str(guide["topic"]),
    )


@app.command(
    "invoke",
    help="以 JSON 对象调用一个已发布叶子命令；复用其真实参数解析、校验和确认保护。",
)
def invoke_command(
    ctx: typer.Context,
    command: Annotated[
        str,
        typer.Option("--command", help="要调用的叶子命令，可使用空格或点号。"),
    ],
    input_source: Annotated[
        str,
        typer.Option("--input", help="JSON 输入文件路径；使用 - 从 stdin 读取。"),
    ],
) -> None:
    context = _context(ctx)
    try:
        invoke_json_command(
            app,
            ctx,
            requested_command=command,
            input_source=input_source,
        )
    except CliError as error:
        emit_error(context, command="invoke", error=error)
        raise typer.Exit(error.exit_code) from error


@app.command("capabilities")
def capabilities_command(
    ctx: typer.Context,
    command: Annotated[
        str | None,
        typer.Option("--command", help="只查看某个命令或命令组。"),
    ] = None,
    resource: Annotated[
        str | None,
        typer.Option("--resource", help="按资源族筛选，例如 professors、communications、campaigns。"),
    ] = None,
    view: Annotated[
        str | None,
        typer.Option(
            "--view",
            help="catalog（资源目录）、commands（精简命令卡）或 full（完整能力契约）。",
        ),
    ] = None,
    all_details: Annotated[
        bool,
        typer.Option("--all", help="等同于 --view full；必须同时用 --command 或 --resource 缩小范围。"),
    ] = False,
    since: Annotated[
        str | None,
        typer.Option(
            "--since",
            help="相同选择和视图的已知 revision；相同则只返回 not_modified 响应。",
        ),
    ] = None,
) -> None:
    context = _context(ctx)
    _validate_system_context(context, "capabilities")
    requested_view = view.strip().lower() if view else None
    if requested_view is not None and requested_view not in {"catalog", "commands", "full"}:
        error = CliError(
            code="INVALID_ARGUMENT",
            message=f"未知 capabilities 视图：{view}",
            exit_code=2,
            details={"view": view, "available_views": ["catalog", "commands", "full"]},
        )
        emit_error(context, command="capabilities", error=error)
        raise typer.Exit(error.exit_code)
    if all_details and requested_view not in {None, "full"}:
        error = CliError(
            code="INVALID_ARGUMENT",
            message="--all 只能与 --view full 一起使用。",
            exit_code=2,
            details={"view": view},
        )
        emit_error(context, command="capabilities", error=error)
        raise typer.Exit(error.exit_code)
    if since is not None and not since.strip():
        error = CliError(
            code="INVALID_ARGUMENT",
            message="--since 不能为空。",
            exit_code=2,
            details={"since": since},
        )
        emit_error(context, command="capabilities", error=error)
        raise typer.Exit(error.exit_code)

    # A selector already narrows the universe, so its useful default is a
    # command-card list.  The root default is a bounded resource catalog.
    effective_view = (
        "full"
        if all_details
        else (
            requested_view
            or ("commands" if command or resource else "catalog")
        )
    )
    if effective_view in {"commands", "full"} and not (command or resource):
        error = CliError(
            code="RESULT_TOO_LARGE",
            message="根级完整命令目录过大，请先按资源或命令缩小范围。",
            exit_code=2,
            details={
                "view": effective_view,
                "suggestions": [
                    "capabilities --resource <resource>",
                    "capabilities --command <command>",
                ],
            },
        )
        emit_error(context, command="capabilities", error=error)
        raise typer.Exit(error.exit_code)
    # The global catalog revision is tied to the embedded build and semantic
    # manifests. Parser-derived leaf hashes are computed only for the selected
    # command-card/full scope; the default resource catalog stays O(resources).
    catalog_revision = capability_catalog_revision()
    try:
        full_items = list_capabilities(
            command,
            resource=resource,
        )
        if (command or resource) and not full_items:
            requested = command or resource or ""
            normalized = normalize_capability_command(requested)
            error = CliError(
                code="CAPABILITY_NOT_FOUND",
                message=f"没有找到能力：{command or resource}",
                exit_code=4,
                details={
                    "command": requested,
                    "normalized_command": normalized,
                    "suggestions": suggest_capabilities(normalized),
                },
            )
            emit_error(context, command="capabilities", error=error)
            raise typer.Exit(error.exit_code)
    except CliError as error:
        emit_error(context, command="capabilities", error=error)
        raise typer.Exit(error.exit_code) from error

    selected_commands = [
        item["command"]
        for item in full_items
        if isinstance(item.get("command"), str)
    ]
    contract_revisions: dict[str, str] | None = None
    if effective_view in {"commands", "full"}:
        available_selected_commands = [
            item["command"]
            for item in full_items
            if item.get("availability") == "available"
            and isinstance(item.get("command"), str)
        ]
        contract_revisions = _current_command_contract_revisions(
            available_selected_commands,
        )
        full_items = list_capabilities(
            command,
            resource=resource,
            contract_revisions=contract_revisions,
        )
    scope_revision = capability_catalog_revision(
        contract_revisions,
        commands=selected_commands,
        view=effective_view,
    )
    scope = {
        "command": normalize_capability_command(command) if command else None,
        "resource": normalize_capability_command(resource) if resource else None,
        "view": effective_view,
    }
    if since is not None and since == scope_revision:
        data = {
            "catalog_version": CAPABILITY_CATALOG_VERSION,
            "catalog_revision": catalog_revision,
            "build": get_build_identity(),
            "scope": scope,
            "scope_revision": scope_revision,
            "view": effective_view,
            "items": [],
            "summary": {"commands": len(full_items), "unchanged": True},
            "cache": {"status": "not_modified", "refresh_required": False},
            "next": {"reuse_cached_result": True},
        }
        emit_success(
            context,
            command="capabilities",
            data=data,
            human_text="CLI 能力目录未变化；可继续使用当前 scope 的缓存结果。",
        )
        return

    if effective_view == "catalog":
        items = list_resource_catalog(command, resource=resource)
        summary: dict[str, object] = {
            "resources": len(items),
            "commands": len(full_items),
            "available_commands": sum(
                item["availability"] == "available"
                for item in full_items
            ),
        }
        human_lines = ["当前 CLI 资源目录："]
        for item in items:
            human_lines.append(
                f"- {item['resource']}: {item['summary']} "
                f"（{item['available_count']}/{item['command_count']} 个可用命令）",
            )
    elif effective_view == "commands":
        items = list_capability_cards(
            command,
            resource=resource,
            contract_revisions=contract_revisions,
        )
        summary = {
            "commands": len(items),
            "available_commands": sum(
                item["availability"] == "available"
                for item in items
            ),
            "unavailable_commands": sum(
                item["availability"] != "available"
                for item in items
            ),
        }
        human_lines = ["当前 CLI 命令："]
        for item in items:
            risk = item.get("risk")
            risk_level = risk.get("level") if isinstance(risk, dict) else "unknown"
            human_lines.append(
                f"- [{item['availability']}] {item['command']}: {item['summary']} "
                f"(风险 {risk_level})",
            )
    else:
        items = full_items
        summary = {
            "commands": len(items),
            "available_commands": sum(
                item["availability"] == "available"
                for item in items
            ),
            "unavailable_commands": sum(
                item["availability"] != "available"
                for item in items
            ),
        }
        human_lines = ["当前 CLI 完整能力："]
        for item in items:
            human_lines.append(
                f"- [{item['availability']}] {item['command']}: {item['summary']} "
                f"(风险 {item['risk_level']})",
            )
    data = {
        "catalog_version": CAPABILITY_CATALOG_VERSION,
        "catalog_revision": catalog_revision,
        "build": get_build_identity(),
        "scope": scope,
        "scope_revision": scope_revision,
        "view": effective_view,
        "items": items,
        "summary": summary,
        "next": {
            "list_commands": "auto-email-sender --format json capabilities --resource <resource>",
            "describe_command": "auto-email-sender --format json describe --command <command>",
            "verify_installation": "auto-email-sender --format json doctor",
        },
    }
    if since is not None:
        data["cache"] = {"status": "stale", "refresh_required": True}
    emit_success(
        context,
        command="capabilities",
        data=data,
        human_text="\n".join(human_lines),
        warnings=(
            ["当前 CLI 构建来自未提交工作区；正式 Agent 安装请使用已发布构建。"]
            if bool(get_build_identity()["dirty"])
            else []
        ),
    )


@app.command("describe")
def describe_command_handler(
    ctx: typer.Context,
    command: Annotated[
        str,
        typer.Option("--command", help="要查看的命令，可使用空格或点号。"),
    ],
    view: Annotated[
        str,
        typer.Option("--view", help="summary（默认执行卡）或 full（完整机器契约）。"),
    ] = "summary",
    sections: Annotated[
        list[str],
        typer.Option(
            "--section",
            help="按需展开 input、output、effects、preconditions、trust、states、errors、actions、idempotency 或 lifecycle；可重复。",
        ),
    ] = [],
    since: Annotated[
        str | None,
        typer.Option(
            "--since",
            help="已知 contract_revision；相同则只返回 not_modified 响应。",
        ),
    ] = None,
) -> None:
    context = _context(ctx)
    _validate_system_context(context, "describe")
    normalized_view = view.strip().lower()
    if normalized_view not in DESCRIPTION_VIEWS:
        error = CliError(
            code="INVALID_ARGUMENT",
            message=f"未知 describe 视图：{view}",
            exit_code=2,
            details={"view": view, "available_views": sorted(DESCRIPTION_VIEWS)},
        )
        emit_error(context, command="describe", error=error)
        raise typer.Exit(error.exit_code)
    if normalized_view == "full" and sections:
        error = CliError(
            code="INVALID_ARGUMENT",
            message="--view full 已包含全部契约，不能同时指定 --section。",
            exit_code=2,
            details={"sections": sections},
        )
        emit_error(context, command="describe", error=error)
        raise typer.Exit(error.exit_code)
    if since is not None and not since.strip():
        error = CliError(
            code="INVALID_ARGUMENT",
            message="--since 不能为空。",
            exit_code=2,
        )
        emit_error(context, command="describe", error=error)
        raise typer.Exit(error.exit_code)
    description = describe_command(app, command)
    if description is None:
        normalized = normalize_capability_command(command)
        error = CliError(
            code="COMMAND_NOT_FOUND",
            message=f"没有找到命令：{command}",
            exit_code=4,
            details={
                "command": command,
                "normalized_command": normalized,
                "suggestions": suggest_capabilities(normalized),
            },
        )
        emit_error(context, command="describe", error=error)
        raise typer.Exit(error.exit_code)

    contract_revision = description.get("contract_revision")
    if since is not None and since == contract_revision:
        emit_success(
            context,
            command="describe",
            data={
                "command": description["command"],
                "contract_version": description.get("contract_version"),
                "contract_revision": contract_revision,
                "unchanged": True,
                "cache": {"status": "not_modified", "refresh_required": False},
            },
            human_text="命令合同未变化，可继续使用缓存。",
        )
        return

    data: dict[str, object]
    if normalized_view == "full":
        data = description
    else:
        data = compact_command_description(description)
        requested_details, invalid_sections = description_sections(description, sections)
        if invalid_sections:
            error = CliError(
                code="INVALID_ARGUMENT",
                message=f"未知 describe section：{', '.join(invalid_sections)}",
                exit_code=2,
                details={
                    "sections": invalid_sections,
                    "available_sections": list(DESCRIPTION_SECTIONS),
                },
            )
            emit_error(context, command="describe", error=error)
            raise typer.Exit(error.exit_code)
        if requested_details:
            data["details"] = requested_details
    if since is not None:
        data["cache"] = {"status": "stale", "refresh_required": True}
    emit_success(
        context,
        command="describe",
        data=data,
        human_text=_format_description_human(data),
    )


@app.command("status")
def status_command(ctx: typer.Context) -> None:
    context = _context(ctx)
    _validate_system_context(context, "status")
    try:
        descriptor = load_runtime_descriptor()
        running = process_is_running(descriptor.desktop_pid)
        ready = False
        if running:
            try:
                response = httpx.get(
                    f"{descriptor.base_url.rstrip('/')}/ready",
                    timeout=1.0,
                )
                ready = response.is_success
            except httpx.HTTPError:
                ready = False
        data = {
            "state": (
                "incompatible"
                if ready and descriptor.protocol_version != PROTOCOL_VERSION
                else ("ready" if ready else ("starting" if running else "stopped"))
            ),
            "desktop_process_running": running,
            "backend_ready": ready,
            "app_version": descriptor.app_version,
            "protocol_version": descriptor.protocol_version,
            "protocol_compatible": descriptor.protocol_version == PROTOCOL_VERSION,
            "runtime_file": get_runtime_file_path().as_posix(),
        }
        runtime_hint = ""
        warnings: list[str] = []
        if not data["protocol_compatible"]:
            warnings.append("命令行与桌面端协议不兼容，不能执行业务命令。")
        elif data["state"] == "stopped":
            runtime_hint = "\n请先手动打开 Auto Email Sender，等待加载完成后再执行业务命令。"
            warnings.append("Auto Email Sender 当前未运行，请先手动打开软件。")
        elif data["state"] == "starting":
            runtime_hint = "\n请等待 Auto Email Sender 加载完成后再执行业务命令。"
            warnings.append("Auto Email Sender 本地服务尚未就绪。")
        emit_success(
            context,
            command="status",
            data=data,
            app_version=descriptor.app_version,
            human_text=(
                f"状态：{data['state']}\n"
                f"桌面进程：{'运行中' if running else '未运行'}\n"
                f"本地服务：{'已就绪' if ready else '未就绪'}\n"
                f"协议：{'兼容' if data['protocol_compatible'] else '不兼容，请在个人中心重新安装命令行与 Agent 支持'}"
                f"{runtime_hint}"
            ),
            warnings=warnings,
        )
    except CliError as error:
        data = {
            "state": "stopped",
            "desktop_process_running": False,
            "backend_ready": False,
            "runtime_file": get_runtime_file_path().as_posix(),
            "message": error.message,
        }
        emit_success(
            context,
            command="status",
            data=data,
            human_text=f"状态：stopped\n{error.message}",
            warnings=[error.message],
        )


@app.command("doctor")
def doctor_command(ctx: typer.Context) -> None:
    context = _context(ctx)
    _validate_system_context(context, "doctor")
    command_path = shutil.which("auto-email-sender")
    runtime_path = get_runtime_file_path()
    agent_skill_installation = inspect_agent_skill_installation()
    cli_installation = agent_skill_installation.get("cli")
    cli_manifest_target = (
        cli_installation.get("target")
        if isinstance(cli_installation, dict)
        else None
    )
    command_matches_manifest = (
        True
        if not isinstance(cli_manifest_target, str)
        else _paths_resolve_to_same_file(command_path, cli_manifest_target)
    )
    checks: list[dict[str, object]] = [
        {
            "id": "cli_command",
            "ok": command_path is not None and command_matches_manifest,
            "message": (
                command_path
                if command_path is not None and command_matches_manifest
                else (
                    f"PATH 中的命令与安装清单目标不一致：{command_path}"
                    if command_path is not None
                    else "全局命令尚未注册；开发环境可继续使用 uv run。"
                )
            ),
        },
        {
            "id": "agent_skills",
            "ok": bool(agent_skill_installation["ok"]),
            "message": str(agent_skill_installation["message"]),
            "details": agent_skill_installation,
        },
        {
            "id": "runtime_descriptor",
            "ok": runtime_path.is_file(),
            "message": runtime_path.as_posix(),
        },
    ]
    if isinstance(cli_installation, dict):
        checks.insert(
            1,
            {
                "id": "cli_installation",
                "ok": bool(cli_installation.get("ok")),
                "message": str(cli_installation.get("message") or "无法验证 CLI 安装。"),
                "details": cli_installation,
            },
        )
    manual_open_required = not runtime_path.is_file()
    app_version: str | None = None
    if runtime_path.is_file():
        try:
            descriptor = load_runtime_descriptor()
            app_version = descriptor.app_version
            desktop_process_running = process_is_running(descriptor.desktop_pid)
            manual_open_required = not desktop_process_running
            checks.extend(
                [
                    {
                        "id": "desktop_process",
                        "ok": desktop_process_running,
                        "message": f"pid={descriptor.desktop_pid}",
                    },
                    {
                        "id": "protocol",
                        "ok": descriptor.protocol_version == PROTOCOL_VERSION,
                        "message": (
                            f"cli={PROTOCOL_VERSION}, app={descriptor.protocol_version}"
                        ),
                    },
                ],
            )
        except CliError as error:
            checks.append(
                {
                    "id": "runtime_valid",
                    "ok": False,
                    "message": error.message,
                },
            )
    healthy = all(bool(check["ok"]) for check in checks)
    installation_needs_update = not bool(agent_skill_installation["ok"]) or (
        isinstance(cli_installation, dict)
        and not bool(cli_installation.get("ok"))
    )
    recommended_action = None
    if manual_open_required:
        recommended_action = "请先手动打开 Auto Email Sender，等待加载完成后再执行需要本地服务的命令。"
        if installation_needs_update:
            recommended_action += "此外，请在个人中心展开“命令行与 Agent”并点击“重新安装”。"
    elif not healthy:
        recommended_action = "请在个人中心展开“命令行与 Agent”并点击“重新安装”。"
    data = {
        "healthy": healthy,
        "checks": checks,
        "recommended_action": recommended_action,
        "repair_command": (
            None
            if healthy or manual_open_required
            else "请在个人中心展开“命令行与 Agent”并点击“重新安装”。"
        ),
    }
    human_lines = ["诊断结果："]
    for check in checks:
        human_lines.append(
            f"- {'通过' if check['ok'] else '需要处理'} {check['id']}: {check['message']}",
        )
    if recommended_action is not None:
        human_lines.append(f"下一步：{recommended_action}")
    emit_success(
        context,
        command="doctor",
        data=data,
        app_version=app_version,
        human_text="\n".join(human_lines),
        warnings=[] if healthy else ["部分检查未通过。"],
    )


def _paths_resolve_to_same_file(left: str | None, right: str) -> bool:
    if left is None:
        return False
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return False


def _context(ctx: typer.Context) -> CliContext:
    value = ctx.obj
    if not isinstance(value, CliContext):
        return CliContext(output_format=OutputFormat.HUMAN)
    return value


def _validate_system_context(context: CliContext, command: str) -> None:
    try:
        validate_context_options(
            context,
            supports_filter=False,
            supports_output_file=False,
            supports_if_revision=False,
            supports_projection=False,
        )
    except CliError as error:
        emit_error(context, command=command, error=error)
        raise typer.Exit(error.exit_code) from error


def _format_guide_human(guide: dict[str, object]) -> str:
    lines = [str(guide["title"])]
    rules = guide.get("rules")
    if isinstance(rules, list):
        lines.extend(f"{index}. {rule}" for index, rule in enumerate(rules, start=1))
    topics = guide.get("topics")
    if isinstance(topics, list):
        lines.append(f"\n可用主题：{', '.join(str(item) for item in topics)}")
    return "\n".join(lines)


def _format_description_human(description: dict[str, object]) -> str:
    lines = [str(description["command"]), str(description["summary"]), "", str(description["usage"])]
    parameters = description.get("parameters")
    if isinstance(parameters, list) and parameters:
        lines.append("\n参数：")
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            flags = parameter.get("flags")
            label = " / ".join(str(flag) for flag in flags) if isinstance(flags, list) else str(parameter.get("name"))
            required = "必填" if parameter.get("required") else "可选"
            lines.append(f"- {label}（{required}）")
    elif isinstance(description.get("input"), dict):
        compact_input = description["input"]
        compact_parameters = compact_input.get("parameters")
        if isinstance(compact_parameters, dict) and compact_parameters:
            lines.append("\n参数：")
            for name, parameter in compact_parameters.items():
                if not isinstance(name, str) or not isinstance(parameter, dict):
                    continue
                flags = parameter.get("flags")
                label = " / ".join(str(flag) for flag in flags) if isinstance(flags, list) else name
                required = "必填" if parameter.get("required") else "可选"
                lines.append(f"- {label}（{required}）")
    next_steps = description.get("next_steps") or description.get("next_actions")
    if isinstance(next_steps, list) and next_steps:
        lines.append("\n下一步：")
        for step in next_steps:
            if isinstance(step, dict):
                command = step.get("command")
                reason = step.get("reason")
                lines.append(f"- {command}: {reason}" if reason else f"- {command}")
            else:
                lines.append(f"- {step}")
    return "\n".join(lines)


def run() -> None:
    _configure_standard_stream_encoding()
    app()


def _configure_standard_stream_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
