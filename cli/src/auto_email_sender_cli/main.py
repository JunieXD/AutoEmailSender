from __future__ import annotations

import shutil
from typing import Annotated

import httpx
import typer

from auto_email_sender_cli.capabilities import (
    list_capabilities,
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
from auto_email_sender_cli.errors import CliError
from auto_email_sender_cli.describe import describe_command
from auto_email_sender_cli.guide import GUIDE_TOPICS, get_guide
from auto_email_sender_cli.output import (
    CliContext,
    OutputFormat,
    emit_error,
    emit_success,
)
from auto_email_sender_cli.runtime import (
    get_runtime_file_path,
    load_runtime_descriptor,
    process_is_running,
)
from auto_email_sender_cli.version import PROTOCOL_VERSION, get_cli_version


app = typer.Typer(
    name="auto-email-sender",
    help=(
        "让本地 Agent 安全查询和操作 Auto Email Sender。\n"
        "多步骤或写入操作前，请运行 guide；使用 capabilities 查看能力，使用 describe 查看命令参数。"
    ),
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
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
) -> None:
    ctx.obj = CliContext(
        output_format=OutputFormat.JSON if json_output else output_format,
    )


@app.command("version")
def version_command(ctx: typer.Context) -> None:
    context = _context(ctx)
    version = get_cli_version()
    emit_success(
        context,
        command="version",
        data={
            "cli_version": version,
            "protocol_version": PROTOCOL_VERSION,
        },
        human_text=f"Auto Email Sender CLI {version}\n协议版本 {PROTOCOL_VERSION}",
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


@app.command("capabilities")
def capabilities_command(
    ctx: typer.Context,
    command: Annotated[
        str | None,
        typer.Option("--command", help="只查看某个命令或命令组。"),
    ] = None,
) -> None:
    context = _context(ctx)
    try:
        items = list_capabilities(command)
        if command and not items:
            normalized = normalize_capability_command(command)
            error = CliError(
                code="CAPABILITY_NOT_FOUND",
                message=f"没有找到能力：{command}",
                exit_code=4,
                details={
                    "command": command,
                    "normalized_command": normalized,
                    "suggestions": suggest_capabilities(normalized),
                },
            )
            emit_error(context, command="capabilities", error=error)
            raise typer.Exit(error.exit_code)
    except CliError as error:
        emit_error(context, command="capabilities", error=error)
        raise typer.Exit(error.exit_code) from error
    available_count = sum(item["availability"] == "available" for item in items)
    data = {
        "items": items,
        "summary": {
            "total": len(items),
            "available": available_count,
            "unavailable": len(items) - available_count,
        },
    }
    human_lines = ["当前 CLI 能力："]
    for item in items:
        human_lines.append(
            f"- [{item['availability']}] {item['command']}: {item['summary']} "
            f"(风险 {item['risk_level']})",
        )
    emit_success(
        context,
        command="capabilities",
        data=data,
        human_text="\n".join(human_lines),
    )


@app.command("describe")
def describe_command_handler(
    ctx: typer.Context,
    command: Annotated[
        str,
        typer.Option("--command", help="要查看的命令，可使用空格或点号。"),
    ],
) -> None:
    context = _context(ctx)
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
    emit_success(
        context,
        command="describe",
        data=description,
        human_text=_format_description_human(description),
    )


@app.command("status")
def status_command(ctx: typer.Context) -> None:
    context = _context(ctx)
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
    command_path = shutil.which("auto-email-sender")
    runtime_path = get_runtime_file_path()
    agent_skill_installation = inspect_agent_skill_installation()
    checks: list[dict[str, object]] = [
        {
            "id": "cli_command",
            "ok": command_path is not None,
            "message": command_path or "全局命令尚未注册；开发环境可继续使用 uv run。",
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
    skill_needs_update = not bool(agent_skill_installation["ok"])
    recommended_action = None
    if manual_open_required:
        recommended_action = "请先手动打开 Auto Email Sender，等待加载完成后再执行需要本地服务的命令。"
        if skill_needs_update:
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


def _context(ctx: typer.Context) -> CliContext:
    value = ctx.obj
    if not isinstance(value, CliContext):
        return CliContext(output_format=OutputFormat.HUMAN)
    return value


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
    next_steps = description.get("next_steps")
    if isinstance(next_steps, list) and next_steps:
        lines.append("\n下一步：")
        lines.extend(f"- {step}" for step in next_steps)
    return "\n".join(lines)


def run() -> None:
    app()
