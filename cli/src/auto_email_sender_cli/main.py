from __future__ import annotations

import re
import shutil
import sys
from difflib import get_close_matches
from pathlib import Path
from typing import Annotated

import typer
from typer._click.core import ParameterSource
from typer._click.exceptions import UsageError
from typer.core import TyperGroup

from auto_email_sender_cli.agent_installation import inspect_agent_skill_installation
from auto_email_sender_cli.capabilities import (
    CAPABILITIES,
    CAPABILITY_CATALOG_VERSION,
    CAPABILITY_SEARCH_MODE,
    CONTRACT_VERSION,
    capability_catalog_revision,
    list_capabilities,
    list_capability_cards,
    list_resource_catalog,
    normalize_capability_command,
    search_capabilities,
    search_capability_cards,
    suggest_capabilities,
)
from auto_email_sender_cli.commands import (
    campaigns_app,
    communication_groups_app,
    communications_app,
    crawler_app,
    dashboard_app,
    deliveries_app,
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
    templates_app,
    test_email_app,
    ui_handoffs_app,
    usage_app,
    workspaces_app,
)
from auto_email_sender_cli.commands.common import validate_context_options
from auto_email_sender_cli.commands.wait import wait_for_resource
from auto_email_sender_cli.describe import (
    DESCRIPTION_SECTIONS,
    DESCRIPTION_VIEWS,
    compact_command_description,
    describe_command,
    describe_command_revisions,
    description_sections,
)
from auto_email_sender_cli.errors import CliError, RuntimeProtocolMismatchError
from auto_email_sender_cli.guide import GUIDE_TOPICS, get_guide
from auto_email_sender_cli.invoke import invoke_json_command
from auto_email_sender_cli.output import (
    CliContext,
    OutputFormat,
    ResultProjection,
    emit_error,
    emit_success,
)
from auto_email_sender_cli.result_protocol import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_MAX_OUTPUT_ITEMS,
    HARD_MAX_OUTPUT_BYTES,
    HARD_MAX_OUTPUT_ITEMS,
    MIN_MAX_OUTPUT_BYTES,
)
from auto_email_sender_cli.runtime import (
    get_runtime_file_path,
    load_runtime_descriptor,
    probe_runtime_descriptor,
)
from auto_email_sender_cli.version import (
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    get_build_identity,
    get_cli_version,
)

_ROOT_VALUE_OPTIONS = frozenset(
    {
        "--format",
        "--request-id",
        "--operation-id",
        "--if-revision",
        "--output-file",
        "--filter",
        "--projection",
        "--expand",
        "--max-output-bytes",
        "--max-items",
    },
)
_ROOT_FLAG_OPTIONS = frozenset({"--json", "--force-output", "--include-revisions"})
_ROOT_OUTPUT_FORMATS = frozenset(item.value for item in OutputFormat)
_CAPABILITY_CARD_SELECT_FIELDS = (
    "command",
    "summary",
    "resource",
    "availability",
    "risk",
    "contract_revision",
    "unavailable_reason",
    "manual_action",
    "lifecycle",
    "match",
)
_COMMAND_CONTRACT_REVISION_CACHE: dict[str, str] = {}


def _root_option_name(argument: str) -> str:
    return argument.split("=", 1)[0]


def _first_command_index(arguments: list[str]) -> int:
    """Locate the root command while accounting for root option values."""

    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            return min(index + 1, len(arguments))
        option = _root_option_name(argument)
        if option in _ROOT_FLAG_OPTIONS:
            index += 1
            continue
        if option in _ROOT_VALUE_OPTIONS:
            index += 1 if "=" in argument else 2
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return index
    return len(arguments)


def _should_move_ambiguous_root_option(
    *,
    option: str,
    value: str | None,
    index: int,
    command_index: int,
    command_path: tuple[str, str | None],
) -> bool:
    if index < command_index:
        return True
    root_command, leaf_command = command_path
    if option == "--request-id" and root_command == "diagnostics" and leaf_command in {
        "logs",
        "crawler-debug",
    }:
        return False
    if (
        option == "--format"
        and root_command == "professors"
        and leaf_command in {"export", "download-template"}
    ):
        return value is not None and value.strip().lower() in _ROOT_OUTPUT_FORMATS
    return True


def _reorder_root_options(arguments: list[str]) -> list[str]:
    """Allow documented global options on either side of a leaf command.

    Click normally stops parsing group options once it enters a subcommand.
    Moving only the known root options keeps leaf parsing intact.  The two
    historical name collisions remain leaf-local after their command; Agents
    can use ``--operation-id`` to disambiguate the root request identifier.
    """

    command_index = _first_command_index(arguments)
    root_command = arguments[command_index] if command_index < len(arguments) else ""
    leaf_command = (
        arguments[command_index + 1]
        if command_index + 1 < len(arguments)
        and not arguments[command_index + 1].startswith("-")
        else None
    )
    moved: list[str] = []
    remaining: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            remaining.extend(arguments[index:])
            break
        option = _root_option_name(argument)
        if option in _ROOT_FLAG_OPTIONS:
            moved.append(argument)
            index += 1
            continue
        if option in _ROOT_VALUE_OPTIONS:
            inline_value = argument.split("=", 1)[1] if "=" in argument else None
            value = inline_value
            consumed = 1
            if inline_value is None and index + 1 < len(arguments):
                value = arguments[index + 1]
                consumed = 2
            ambiguous = option in {"--format", "--request-id"}
            should_move = not ambiguous or _should_move_ambiguous_root_option(
                option=option,
                value=value,
                index=index,
                command_index=command_index,
                command_path=(root_command, leaf_command),
            )
            if should_move:
                moved.extend(arguments[index : index + consumed])
                index += consumed
                continue
        remaining.append(argument)
        index += 1
    return [*moved, *remaining]


def _normalize_capability_select(values: list[str]) -> tuple[list[str], list[str]]:
    selected: list[str] = []
    invalid: list[str] = []
    for value in values:
        for candidate in value.split(","):
            normalized = candidate.strip().lower().replace("-", "_")
            if not normalized or normalized in selected or normalized in invalid:
                continue
            if normalized not in _CAPABILITY_CARD_SELECT_FIELDS:
                invalid.append(candidate.strip())
            else:
                selected.append(normalized)
    return selected, invalid


def _select_capability_card_fields(
    items: list[dict[str, object]],
    selected: list[str],
) -> list[dict[str, object]]:
    if not selected:
        return items
    return [
        {key: item[key] for key in selected if key in item}
        for item in items
    ]


class AgentTyperGroup(TyperGroup):
    """Keep parser failures inside the structured Agent result protocol."""

    def main(self, *args: object, **kwargs: object) -> object:
        standalone_mode = bool(kwargs.pop("standalone_mode", True))
        raw_args = kwargs.get("args")
        arguments = (
            [str(item) for item in raw_args]
            if isinstance(raw_args, (list, tuple))
            else list(sys.argv[1:])
        )
        reordered_args = _reorder_root_options(arguments)
        kwargs["args"] = reordered_args
        try:
            result = super().main(*args, standalone_mode=False, **kwargs)
        except UsageError as exc:
            _emit_usage_error(exc, reordered_args)
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
    suggestions = _usage_error_suggestions(error)
    if suggestions:
        details["suggestions"] = suggestions
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


def _usage_error_suggestions(error: UsageError) -> list[str]:
    possibilities = getattr(error, "possibilities", None)
    if isinstance(possibilities, (list, tuple)) and possibilities:
        return [str(item) for item in possibilities[:5]]

    option_name = getattr(error, "option_name", None)
    command = getattr(getattr(error, "ctx", None), "command", None)
    if isinstance(option_name, str) and command is not None:
        option_names: list[str] = []
        for parameter in getattr(command, "params", ()):
            for item in (*getattr(parameter, "opts", ()), *getattr(parameter, "secondary_opts", ())):
                rendered = str(item)
                option_names.append(
                    rendered
                    if rendered.startswith("-")
                    else f"--{rendered.replace('_', '-')}"
                )
        return get_close_matches(option_name, option_names, n=5, cutoff=0.45)

    match = re.search(r"No such command ['\"]([^'\"]+)['\"]", str(getattr(error, "message", "")))
    context = getattr(error, "ctx", None)
    if match is not None and context is not None and isinstance(command, TyperGroup):
        return get_close_matches(
            match.group(1),
            [str(item) for item in command.list_commands(context)],
            n=5,
            cutoff=0.45,
        )
    return []


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
app.add_typer(deliveries_app, name="deliveries")
app.add_typer(plans_app, name="plans")
app.add_typer(settings_app, name="settings")
app.add_typer(tasks_app, name="tasks")
app.add_typer(test_email_app, name="test-email")
app.add_typer(usage_app, name="usage")
app.add_typer(ui_handoffs_app, name="ui-handoffs")
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
    missing_commands = [
        command
        for command in selected_commands
        if command not in _COMMAND_CONTRACT_REVISION_CACHE
    ]
    if missing_commands:
        _COMMAND_CONTRACT_REVISION_CACHE.update(
            describe_command_revisions(app, missing_commands),
        )
    revisions = {
        command: _COMMAND_CONTRACT_REVISION_CACHE[command]
        for command in selected_commands
        if command in _COMMAND_CONTRACT_REVISION_CACHE
    }
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
            "--operation-id",
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
    max_output_bytes: Annotated[
        int,
        typer.Option(
            "--max-output-bytes",
            min=MIN_MAX_OUTPUT_BYTES,
            max=HARD_MAX_OUTPUT_BYTES,
            help=(
                "业务 data 的 UTF-8 字节预算；超出时返回明确的截断路径，"
                "full/expand 也不能绕过该上限。"
            ),
        ),
    ] = DEFAULT_MAX_OUTPUT_BYTES,
    max_items: Annotated[
        int,
        typer.Option(
            "--max-items",
            min=1,
            max=HARD_MAX_OUTPUT_ITEMS,
            help="stdout 集合允许保留的最大条目数；完整大集合请改用 --output-file。",
        ),
    ] = DEFAULT_MAX_OUTPUT_ITEMS,
    include_revisions: Annotated[
        bool,
        typer.Option(
            "--include-revisions",
            help="在集合记录中包含并发保护 revision；详情读取始终包含。",
        ),
    ] = False,
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
            "max_output_bytes",
            "max_items",
            "include_revisions",
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
        max_output_bytes=max_output_bytes,
        max_items=max_items,
        include_revisions=include_revisions,
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
    query: Annotated[
        str | None,
        typer.Option(
            "--query",
            "--intent",
            help="按中文或英文任务意图检索并排序最相关命令；--intent 含义更明确。",
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, max=25, help="--query 最多返回多少个命令，默认 8。"),
    ] = None,
    resource_exact: Annotated[
        bool,
        typer.Option("--resource-exact", help="只匹配指定资源，不包含其子资源。"),
    ] = False,
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
    select: Annotated[
        list[str],
        typer.Option(
            "--select",
            help="只保留命令卡字段；逗号分隔或重复使用，例如 command,summary,risk。",
        ),
    ] = [],
    minimal: Annotated[
        bool,
        typer.Option("--minimal", help="省略 build、next 和工作区提示，只返回缓存与结果必需字段。"),
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
    normalized_query = query.strip() if query is not None else None
    selected_fields, invalid_select_fields = _normalize_capability_select(select)
    if query is not None and not normalized_query:
        error = CliError(
            code="INVALID_ARGUMENT",
            message="--query 不能为空（--intent 是其同义别名）。",
            exit_code=2,
            details={"query": query},
        )
        emit_error(context, command="capabilities", error=error)
        raise typer.Exit(error.exit_code)
    if query is not None and command is not None:
        error = CliError(
            code="INVALID_ARGUMENT",
            message="--query 与 --command 不能同时使用（--intent 是 --query 的同义别名）。",
            exit_code=2,
            details={"query": query, "command": command},
        )
        emit_error(context, command="capabilities", error=error)
        raise typer.Exit(error.exit_code)
    if resource_exact and resource is None:
        error = CliError(
            code="INVALID_ARGUMENT",
            message="--resource-exact 必须与 --resource 一起使用。",
            exit_code=2,
        )
        emit_error(context, command="capabilities", error=error)
        raise typer.Exit(error.exit_code)
    if limit is not None and query is None:
        error = CliError(
            code="INVALID_ARGUMENT",
            message="--limit 只能与 --query 一起使用（也可使用同义别名 --intent）。",
            exit_code=2,
        )
        emit_error(context, command="capabilities", error=error)
        raise typer.Exit(error.exit_code)
    if invalid_select_fields:
        error = CliError(
            code="INVALID_ARGUMENT",
            message=f"未知命令卡字段：{', '.join(invalid_select_fields)}",
            exit_code=2,
            details={
                "fields": invalid_select_fields,
                "available_fields": list(_CAPABILITY_CARD_SELECT_FIELDS),
            },
        )
        emit_error(context, command="capabilities", error=error)
        raise typer.Exit(error.exit_code)
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
    if query is not None and (all_details or requested_view not in {None, "commands"}):
        error = CliError(
            code="INVALID_ARGUMENT",
            message="--query 返回精简命令卡，只能使用 --view commands（--intent 同义）。",
            exit_code=2,
            details={"view": "full" if all_details else requested_view},
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
            or ("commands" if command or resource or query else "catalog")
        )
    )
    if selected_fields and effective_view != "commands":
        error = CliError(
            code="INVALID_ARGUMENT",
            message="--select 只能用于 commands 命令卡视图。",
            exit_code=2,
            details={"view": effective_view},
        )
        emit_error(context, command="capabilities", error=error)
        raise typer.Exit(error.exit_code)
    if effective_view in {"commands", "full"} and not (command or resource or query):
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
        query_matches = (
            search_capabilities(
                normalized_query or "",
                resource=resource,
                resource_exact=resource_exact,
                limit=limit or 8,
            )
            if query is not None
            else ()
        )
        full_items = (
            [item.to_dict() for item in query_matches]
            if query is not None
            else list_capabilities(
                command,
                resource=resource,
                resource_exact=resource_exact,
            )
        )
        if (command or resource or query) and not full_items:
            requested = command or query or resource or ""
            normalized = normalize_capability_command(requested)
            normalized_resource = normalize_capability_command(resource) if resource else None
            resource_exists = bool(
                list_capabilities(
                    resource=resource,
                    resource_exact=resource_exact,
                )
            ) if resource else None
            if query is not None:
                if resource is not None and resource_exists is False:
                    message = f"没有找到资源能力：{resource}"
                else:
                    scope_label = f"资源 {resource} 范围" if resource else "当前能力目录"
                    message = f"在{scope_label}内没有找到与任务意图匹配的命令：{query}"
                suggestions = [
                    item.command
                    for item in search_capabilities(normalized_query or "", limit=3)
                ]
            elif command is not None:
                message = f"没有找到能力：{command}"
                suggestions = suggest_capabilities(normalized)
            else:
                message = f"没有找到资源能力：{resource}"
                suggestions = suggest_capabilities(normalized)
            error = CliError(
                code="CAPABILITY_NOT_FOUND",
                message=message,
                exit_code=4,
                details={
                    "request": requested,
                    "normalized_request": normalized,
                    "command": requested if command is not None else None,
                    "normalized_command": normalized if command is not None else None,
                    "query": normalized_query,
                    "resource": resource,
                    "normalized_resource": normalized_resource,
                    "resource_exact": resource_exact,
                    "resource_exists": resource_exists,
                    "search_mode": CAPABILITY_SEARCH_MODE if query is not None else None,
                    "suggestions": suggestions,
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
        if query is not None:
            full_items = [
                {
                    **item.to_dict(),
                    **(
                        {"contract_revision": contract_revisions[item.command]}
                        if item.command in contract_revisions
                        else {}
                    ),
                }
                for item in query_matches
            ]
        else:
            full_items = list_capabilities(
                command,
                resource=resource,
                resource_exact=resource_exact,
                contract_revisions=contract_revisions,
            )
    scope_options = {
        "command": normalize_capability_command(command) if command else None,
        "resource": normalize_capability_command(resource) if resource else None,
        "resource_exact": resource_exact,
        "query": normalized_query,
        "search_mode": CAPABILITY_SEARCH_MODE if query is not None else None,
        "limit": (limit or 8) if query is not None else None,
        "select": selected_fields,
        "minimal": minimal,
    }
    scope_revision = capability_catalog_revision(
        contract_revisions,
        commands=selected_commands,
        view=effective_view,
        scope=scope_options,
    )
    # The scope hash keeps every normalized default, while routine output only
    # emits non-default selectors.  This preserves cache identity without
    # spending Agent context on nulls, empty lists, or false flags.
    scope = {
        "view": effective_view,
        **{
            key: value
            for key, value in scope_options.items()
            if value is not None and value not in (False, [], "")
        },
    }
    if since is not None and since == scope_revision:
        data = {
            "catalog_version": CAPABILITY_CATALOG_VERSION,
            "catalog_revision": catalog_revision,
            "scope": scope,
            "scope_revision": scope_revision,
            "view": effective_view,
            "items": [],
            "summary": {"commands": len(full_items), "unchanged": True},
            "cache": {"status": "not_modified", "refresh_required": False},
        }
        if not minimal:
            data["build"] = get_build_identity()
            data["next"] = {"reuse_cached_result": True}
        emit_success(
            context,
            command="capabilities",
            data=data,
            human_text="CLI 能力目录未变化；可继续使用当前 scope 的缓存结果。",
        )
        return

    if effective_view == "catalog":
        items = list_resource_catalog(
            command,
            resource=resource,
            resource_exact=resource_exact,
        )
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
        items = (
            search_capability_cards(
                normalized_query or "",
                resource=resource,
                resource_exact=resource_exact,
                limit=limit or 8,
                contract_revisions=contract_revisions,
            )
            if query is not None
            else list_capability_cards(
                command,
                resource=resource,
                resource_exact=resource_exact,
                contract_revisions=contract_revisions,
            )
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
        items = _select_capability_card_fields(items, selected_fields)
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
        "scope": scope,
        "scope_revision": scope_revision,
        "view": effective_view,
        "items": items,
        "summary": summary,
    }
    if query is not None:
        data["query_scope"] = {
            "intent": normalized_query,
            "resource": normalize_capability_command(resource) if resource else None,
            "resource_exact": resource_exact,
            "limit": limit or 8,
            "mode": CAPABILITY_SEARCH_MODE,
        }
    if not minimal:
        data["build"] = get_build_identity()
        data["next"] = {
            "list_commands": "auto-email-sender --format json capabilities --resource <resource>",
            "describe_command": "auto-email-sender --format json describe --command <command>",
            "verify_installation": "auto-email-sender --format json doctor",
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
            if not minimal and bool(get_build_identity()["dirty"])
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
        probe = probe_runtime_descriptor(descriptor)
        running = probe.desktop_process_running
        ready = probe.backend_ready and running
        state = (
            "ready"
            if ready
            else (
                "orphaned"
                if probe.runtime_matches and not running
                else (
                    "starting"
                    if running or probe.backend_process_running
                    else "stopped"
                )
            )
        )
        data = {
            "state": state,
            "desktop_process_running": running,
            "backend_process_running": probe.backend_process_running,
            "backend_reachable": probe.backend_reachable,
            "runtime_matches": probe.runtime_matches,
            "backend_ready": ready,
            "app_version": descriptor.app_version,
            "protocol_version": descriptor.protocol_version,
            "protocol_compatible": descriptor.protocol_version == PROTOCOL_VERSION,
            "runtime_file": get_runtime_file_path().as_posix(),
        }
        runtime_hint = ""
        warnings: list[str] = []
        if data["state"] == "stopped":
            runtime_hint = "\n请先手动打开 Auto Email Sender，等待加载完成后再执行业务命令。"
            warnings.append("Auto Email Sender 当前未运行，请先手动打开软件。")
        elif data["state"] == "orphaned":
            runtime_hint = "\n桌面进程已退出，残留后端正在清理；请重新打开软件。"
            warnings.append("检测到桌面进程已退出的残留后端。")
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
                f"后端进程：{'运行中' if probe.backend_process_running else '未运行'}\n"
                f"本地服务：{'已就绪' if ready else '未就绪'}\n"
                f"运行身份：{'匹配' if probe.runtime_matches else '未验证'}\n"
                "协议：兼容"
                f"{runtime_hint}"
            ),
            warnings=warnings,
        )
    except RuntimeProtocolMismatchError as error:
        emit_success(
            context,
            command="status",
            data={
                "state": "incompatible",
                "desktop_process_running": False,
                "backend_process_running": False,
                "backend_reachable": False,
                "runtime_matches": False,
                "backend_ready": False,
                "protocol_compatible": False,
                "runtime_file": get_runtime_file_path().as_posix(),
                "message": error.message,
            },
            human_text=f"状态：incompatible\n{error.message}",
            warnings=[error.message],
        )
    except CliError as error:
        data = {
            "state": "stopped",
            "desktop_process_running": False,
            "backend_process_running": False,
            "backend_reachable": False,
            "runtime_matches": False,
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
def doctor_command(
    ctx: typer.Context,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="任一诊断检查未通过时，在输出完整结果后返回非零退出码。",
        ),
    ] = False,
) -> None:
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
            probe = probe_runtime_descriptor(descriptor)
            desktop_process_running = probe.desktop_process_running
            manual_open_required = not desktop_process_running
            checks.extend(
                [
                    {
                        "id": "desktop_process",
                        "ok": desktop_process_running,
                        "message": f"pid={descriptor.desktop_pid}",
                    },
                    {
                        "id": "backend_process",
                        "ok": probe.backend_process_running,
                        "message": f"pid={descriptor.backend_pid}",
                    },
                    {
                        "id": "runtime_handshake",
                        "ok": probe.runtime_matches,
                        "message": (
                            "runtime_id 已认证"
                            if probe.runtime_matches
                            else (probe.message or "无法验证本地服务身份")
                        ),
                    },
                    {
                        "id": "backend_ready",
                        "ok": probe.backend_ready,
                        "message": probe.backend_state or "unreachable",
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
    if strict and not healthy:
        raise typer.Exit(1)


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
