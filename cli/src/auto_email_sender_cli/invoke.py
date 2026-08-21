"""Generic JSON invocation that reuses the live Typer command tree."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from typer._click import Command, Context
from typer._click._compat import get_text_stdin
from typer._click.exceptions import UsageError
from typer.core import TyperArgument, TyperGroup, TyperOption
from typer.main import get_command

from auto_email_sender_cli.capabilities import (
    get_capability,
    normalize_capability_command,
    suggest_capabilities,
)
from auto_email_sender_cli.errors import CliError
from auto_email_sender_cli.output import CliContext


_MAX_INPUT_BYTES = 1_048_576


def invoke_json_command(
    app: typer.Typer,
    ctx: typer.Context,
    *,
    requested_command: str,
    input_source: str,
) -> None:
    """Parse JSON into the target's real Click parameters and invoke it.

    This is intentionally not an HTTP dispatcher.  The target command's exact
    parser, local validation, confirmation gate, idempotency logic, and output
    shaping all run unchanged.
    """

    normalized, target = _resolve_target(app, requested_command)
    payload = _read_input_object(input_source)
    argv = _input_to_argv(target, payload)
    root_context = ctx.find_root()
    context_value = root_context.obj
    previous_command: str | None = None
    previous_input: dict[str, object] | None = None
    if isinstance(context_value, CliContext):
        previous_command = context_value.invoke_command
        previous_input = context_value.invoke_input
        context_value.invoke_command = normalized
        context_value.invoke_input = dict(payload)
    try:
        try:
            target_context = target.make_context(
                normalized,
                argv,
                parent=root_context,
            )
            with target_context:
                target.invoke(target_context)
        except UsageError as exc:
            raise CliError(
                code="INVALID_INVOKE_INPUT",
                message=f"JSON 输入不符合 {normalized} 的实时参数合同：{exc.format_message()}",
                exit_code=2,
                details={"command": normalized},
            ) from exc
    finally:
        if isinstance(context_value, CliContext):
            context_value.invoke_command = previous_command
            context_value.invoke_input = previous_input


def _resolve_target(app: typer.Typer, requested_command: str) -> tuple[str, Command]:
    normalized = normalize_capability_command(requested_command)
    if not normalized:
        raise CliError(
            code="INVOKE_COMMAND_NOT_FOUND",
            message="--command 不能为空。",
            exit_code=2,
        )
    if normalized == "invoke":
        raise CliError(
            code="INVOKE_RECURSION_NOT_ALLOWED",
            message="invoke 不能调用自身。",
            exit_code=2,
        )
    capability = get_capability(normalized)
    if capability is None:
        raise CliError(
            code="INVOKE_COMMAND_NOT_FOUND",
            message=f"没有找到可调用命令：{requested_command}",
            exit_code=4,
            details={
                "command": requested_command,
                "normalized_command": normalized,
                "suggestions": suggest_capabilities(normalized),
            },
        )
    if capability.availability != "available":
        raise CliError(
            code="INVOKE_COMMAND_UNAVAILABLE",
            message=f"命令当前不可用：{normalized}",
            exit_code=4,
            details={"command": normalized, "availability": capability.availability},
        )
    command: Command = get_command(app)
    for segment in normalized.split("."):
        if not isinstance(command, TyperGroup):
            raise CliError(
                code="INVOKE_TARGET_NOT_LEAF",
                message=f"命令不是可执行叶子命令：{normalized}",
                exit_code=2,
            )
        child = command.get_command(Context(command), segment)
        if child is None:
            raise CliError(
                code="INVOKE_COMMAND_NOT_FOUND",
                message=f"没有找到可调用命令：{requested_command}",
                exit_code=4,
                details={
                    "command": requested_command,
                    "normalized_command": normalized,
                },
            )
        command = child
    if isinstance(command, TyperGroup):
        raise CliError(
            code="INVOKE_TARGET_NOT_LEAF",
            message=f"命令组不能直接 invoke：{normalized}",
            exit_code=2,
        )
    return normalized, command


def _read_input_object(input_source: str) -> dict[str, object]:
    if not input_source.strip():
        raise CliError(
            code="INVALID_INVOKE_INPUT",
            message="--input 不能为空；使用 - 从 stdin 读取 JSON 对象。",
            exit_code=2,
        )
    try:
        if input_source == "-":
            raw = get_text_stdin().read(_MAX_INPUT_BYTES + 1)
        else:
            path = Path(input_source).expanduser()
            if not path.is_file():
                raise CliError(
                    code="INVOKE_INPUT_NOT_FOUND",
                    message=f"找不到 JSON 输入文件：{path}",
                    exit_code=4,
                )
            if path.stat().st_size > _MAX_INPUT_BYTES:
                raise CliError(
                    code="INVOKE_INPUT_TOO_LARGE",
                    message="JSON 输入超过 1 MiB 安全上限。",
                    exit_code=2,
                )
            raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(
            code="INVOKE_INPUT_READ_FAILED",
            message="无法读取 JSON 输入。",
            exit_code=8,
            details={"reason": type(exc).__name__},
        ) from exc
    if len(raw.encode("utf-8")) > _MAX_INPUT_BYTES:
        raise CliError(
            code="INVOKE_INPUT_TOO_LARGE",
            message="JSON 输入超过 1 MiB 安全上限。",
            exit_code=2,
        )
    try:
        payload = json.loads(raw.lstrip("\ufeff"))
    except json.JSONDecodeError as exc:
        raise CliError(
            code="INVALID_INVOKE_INPUT",
            message="--input 必须是合法 JSON 对象。",
            exit_code=2,
            details={"line": exc.lineno, "column": exc.colno},
        ) from exc
    if not isinstance(payload, dict):
        raise CliError(
            code="INVALID_INVOKE_INPUT",
            message="--input 顶层必须是 JSON 对象，键名使用 describe 返回的参数 name。",
            exit_code=2,
        )
    if not all(isinstance(key, str) for key in payload):
        raise CliError(
            code="INVALID_INVOKE_INPUT",
            message="--input 的键名必须是字符串。",
            exit_code=2,
        )
    return payload


def _input_to_argv(target: Command, payload: dict[str, object]) -> list[str]:
    parameters = list(target.params)
    known_names = {parameter.name for parameter in parameters if parameter.name}
    unknown = sorted(str(key) for key in payload if key not in known_names)
    if unknown:
        raise CliError(
            code="INVALID_INVOKE_INPUT",
            message=f"JSON 包含未声明参数：{', '.join(unknown)}",
            exit_code=2,
            details={"unknown_fields": unknown, "allowed_fields": sorted(known_names)},
        )
    arguments: list[str] = []
    options: list[str] = []
    for parameter in parameters:
        name = parameter.name
        if not name or name not in payload:
            continue
        value = payload[name]
        if value is None:
            # Omit null rather than accidentally turning it into the string
            # "None". Click then applies the command's established default or
            # reports the same missing-required error as flag invocation.
            continue
        destination = options if isinstance(parameter, TyperOption) else arguments
        if isinstance(parameter, TyperOption):
            _append_option(destination, parameter, value, name=name)
        else:
            assert isinstance(parameter, TyperArgument)
            _append_argument(destination, parameter, value, name=name)
    return [*arguments, *options]


def _append_option(
    argv: list[str],
    option: TyperOption,
    value: object,
    *,
    name: str,
) -> None:
    if option.is_flag:
        if not isinstance(value, bool):
            raise _invalid_value(name, "布尔 flag 需要 true 或 false")
        if value:
            argv.append(_preferred_option_flag(option))
        elif option.secondary_opts:
            argv.append(_preferred_option_flag(option, secondary=True))
        return
    values = _parameter_values(value, option.nargs, multiple=option.multiple, name=name)
    flag = _preferred_option_flag(option)
    for group in values:
        argv.append(flag)
        argv.extend(group)


def _append_argument(
    argv: list[str],
    argument: TyperArgument,
    value: object,
    *,
    name: str,
) -> None:
    for group in _parameter_values(value, argument.nargs, multiple=False, name=name):
        argv.extend(group)


def _parameter_values(
    value: object,
    nargs: int,
    *,
    multiple: bool,
    name: str,
) -> list[list[str]]:
    groups: list[object]
    if multiple:
        if not isinstance(value, list):
            raise _invalid_value(name, "可重复参数需要 JSON 数组")
        groups = value
    else:
        groups = [value]
    values: list[list[str]] = []
    for group in groups:
        if nargs != 1:
            if not isinstance(group, list) or len(group) != nargs:
                raise _invalid_value(name, f"该参数需要恰好 {nargs} 个值")
            values.append([_scalar_to_text(item, name=name) for item in group])
        else:
            values.append([_scalar_to_text(group, name=name)])
    return values


def _preferred_option_flag(option: TyperOption, *, secondary: bool = False) -> str:
    candidates = option.secondary_opts if secondary else option.opts
    return next((flag for flag in candidates if flag.startswith("--")), candidates[0])


def _scalar_to_text(value: object, *, name: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str | int | float):
        return str(value)
    raise _invalid_value(name, "参数值必须是字符串、数字、布尔值或这些值组成的数组")


def _invalid_value(name: str, reason: str) -> CliError:
    return CliError(
        code="INVALID_INVOKE_INPUT",
        message=f"参数 {name} 无效：{reason}。",
        exit_code=2,
        details={"parameter": name},
    )
