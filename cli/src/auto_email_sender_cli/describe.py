from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Final

import typer
from typer._click import Command, Context
from typer.core import TyperGroup, TyperOption
from typer.main import get_command

from auto_email_sender_cli.capabilities import (
    Capability,
    get_capability,
    normalize_capability_command,
    suggest_capabilities,
)
from auto_email_sender_cli.contracts import build_command_contract


CommandDescription = dict[str, object]


DESCRIPTION_VIEWS: Final[frozenset[str]] = frozenset({"summary", "full"})
DESCRIPTION_SECTIONS: Final[tuple[str, ...]] = (
    "input",
    "output",
    "effects",
    "preconditions",
    "states",
    "errors",
    "actions",
)

_DESCRIPTION_SECTION_KEYS: Final[dict[str, str]] = {
    "input": "input",
    "output": "output",
    "effects": "effects",
    "preconditions": "preconditions",
    "states": "state_transitions",
    "errors": "errors",
    "actions": "next_actions",
}


JSON_FILE_EXAMPLES: dict[str, dict[str, object]] = {
    "professors.community.import": {
        "option": "--items-file",
        "format": "json",
        "example": {
            "dataset_version": "<catalog returned dataset_version>",
            "unit_paths": ["data/<school>.json"],
            "items": [
                {
                    "community_record_id": "<record id>",
                    "comparison_token": "<preview returned token>",
                    "field_choices": {"research_direction": "community"},
                    "confirm_identity_match": False,
                },
            ],
        },
    },
}


def describe_command(app: typer.Typer, requested_command: str) -> CommandDescription | None:
    normalized = normalize_capability_command(requested_command)
    if not normalized:
        return None
    command_path = normalized.split(".")
    command: Command = get_command(app)
    for segment in command_path:
        if not isinstance(command, TyperGroup):
            return None
        child = command.get_command(Context(command), segment)
        if child is None:
            return None
        command = child

    capability = get_capability(normalized)
    is_group = isinstance(command, TyperGroup)
    parameters = [_describe_parameter(parameter) for parameter in command.params]
    children = _describe_children(command, normalized) if is_group else []
    usage = _build_usage(command_path, parameters, is_group)
    next_steps = _next_steps(capability)
    description: CommandDescription = {
        "command": normalized,
        "kind": "group" if is_group else "command",
        "summary": _summary(command, capability, is_group),
        "usage": usage,
        "example": _build_example(command_path, parameters),
        "parameters": parameters,
        "children": children,
        "input_file_examples": _input_file_examples(normalized),
        "risk": _describe_risk(capability),
        "preconditions": _describe_preconditions(normalized),
        "next_steps": next_steps,
        "suggestions": suggest_capabilities(normalized),
    }
    # The contract fields are generated from the same Click parameters and
    # Capability registry used above.  Keep the legacy fields in the response
    # so existing protocol-v2 callers remain compatible.
    description.update(
        build_command_contract(
            command=normalized,
            parameters=parameters,
            input_file_examples=_input_file_examples(normalized),
            capability=capability,
            next_steps=next_steps,
        ),
    )
    return description


def compact_command_description(description: CommandDescription) -> dict[str, object]:
    """Return the execution facts an Agent needs before requesting details.

    ``describe`` used to return its legacy fields, a full input schema, and a
    full output schema together.  That is useful for an explicit contract
    inspection but too expensive for ordinary routing.  Keep all detail
    addressable through ``description_sections`` and ``--view full``.
    """

    parameters = description.get("parameters")
    compact_parameters: dict[str, object] = {}
    if isinstance(parameters, list):
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            name = parameter.get("name")
            if not isinstance(name, str):
                continue
            compact_parameters[name] = _compact_parameter(parameter)

    input_contract = description.get("input")
    global_options: dict[str, object] = {}
    if isinstance(input_contract, dict):
        raw_global_options = input_contract.get("global_options")
        if isinstance(raw_global_options, dict):
            for name, option in raw_global_options.items():
                if not isinstance(name, str) or not isinstance(option, dict):
                    continue
                if name in {"request_id", "format"} or bool(option.get("supported")):
                    global_options[name] = {
                        key: option[key]
                        for key in ("flags", "type", "supported", "description")
                        if key in option
                    }

    output_contract = description.get("output")
    if not isinstance(output_contract, dict):
        output_contract = {}
    known_fields = output_contract.get("known_fields")
    fields = [field for field in known_fields if isinstance(field, str)] if isinstance(known_fields, list) else []

    summary: dict[str, object] = {
        "command": description.get("command"),
        "kind": description.get("kind"),
        "summary": description.get("summary"),
        "usage": description.get("usage"),
        "example": description.get("example"),
        "risk": _compact_risk(description.get("risk")),
        "input": {
            "parameters": compact_parameters,
            "global_options": global_options,
            "file_input_available": bool(description.get("input_file_examples")),
        },
        "output": {
            "shape": "page" if output_contract.get("pagination") else "object",
            "field_count": len(fields),
            "key_fields": _key_output_fields(fields),
            "pagination": bool(output_contract.get("pagination")),
            "field_selection": bool(output_contract.get("field_selection")),
            "structured_filter": bool(output_contract.get("structured_filter")),
            "file_export": bool(output_contract.get("file_export")),
            "terminal_states": output_contract.get("terminal_states", []),
            "state_metadata": output_contract.get("state_metadata") is not None,
        },
        "effects": description.get("effects"),
        "preconditions": description.get("preconditions"),
        "state_transitions": description.get("state_transitions", []),
        "errors": _compact_errors(description.get("errors")),
        "next_actions": description.get("next_actions", []),
        "details_available": {
            "sections": list(DESCRIPTION_SECTIONS),
            "full_view": True,
        },
    }
    children = description.get("children")
    if isinstance(children, list) and children:
        summary["children"] = children
    return summary


def description_sections(
    description: CommandDescription,
    requested_sections: Iterable[str],
) -> tuple[dict[str, object], list[str]]:
    """Return only explicitly requested full-contract sections and invalid names."""

    selected: dict[str, object] = {}
    invalid: list[str] = []
    seen: set[str] = set()
    for requested in requested_sections:
        normalized = requested.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        key = _DESCRIPTION_SECTION_KEYS.get(normalized)
        if key is None:
            invalid.append(requested)
            continue
        selected[normalized] = description.get(key)
    return selected, invalid


def _compact_parameter(parameter: dict[str, object]) -> dict[str, object]:
    type_info = parameter.get("type")
    result: dict[str, object] = {
        "kind": parameter.get("kind"),
        "type": type_info.get("kind", "string") if isinstance(type_info, dict) else "string",
        "required": bool(parameter.get("required")),
        "flags": parameter.get("flags", []),
    }
    if isinstance(type_info, dict):
        values = type_info.get("values")
        if isinstance(values, list):
            result["enum"] = values
        for key in ("minimum", "maximum"):
            if type_info.get(key) is not None:
                result[key] = type_info[key]
    if bool(parameter.get("multiple")):
        result["multiple"] = True
    if parameter.get("default") is not None:
        result["default"] = parameter["default"]
    if parameter.get("help"):
        result["description"] = parameter["help"]
    return result


def _compact_risk(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {
        key: value[key]
        for key in ("level", "availability", "mutates", "external_action", "requires_plan", "long_running")
        if key in value
    }


def _key_output_fields(fields: list[str]) -> list[str]:
    priority = (
        "id",
        "plan_id",
        "job_id",
        "task_id",
        "status",
        "revision",
        "items",
        "next_cursor",
        "has_more",
        "mutation_receipt",
        "warnings",
    )
    selected = [field for field in priority if field in fields]
    if selected:
        return selected
    return fields[:6]


def _compact_errors(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for error in value:
        if not isinstance(error, dict):
            continue
        code = error.get("code")
        if not isinstance(code, str) or code in seen:
            continue
        seen.add(code)
        result.append({"code": code, "retryable": bool(error.get("retryable"))})
    return result


def _describe_parameter(parameter: Any) -> dict[str, object]:
    option = parameter if isinstance(parameter, TyperOption) else None
    names = list(option.opts) + list(option.secondary_opts) if option else [parameter.name]
    default = parameter.default
    return {
        "name": parameter.name,
        "kind": "option" if option else "argument",
        "flags": names,
        "required": parameter.required,
        "multiple": parameter.multiple,
        "nargs": parameter.nargs,
        "type": _describe_type(parameter.type),
        "default": _json_value(default),
        "is_flag": option.is_flag if option else False,
        "help": option.help if option else None,
    }


def _describe_type(param_type: object) -> dict[str, object]:
    type_name = str(getattr(param_type, "name", "string")).lower()
    result: dict[str, object] = {"name": type_name}
    choices = getattr(param_type, "choices", None)
    if choices is not None:
        result["kind"] = "enum"
        result["values"] = [str(value) for value in choices]
    elif "int" in type_name:
        result["kind"] = "integer"
        result["minimum"] = getattr(param_type, "min", None)
        result["maximum"] = getattr(param_type, "max", None)
    elif "float" in type_name or "number" in type_name:
        result["kind"] = "number"
        result["minimum"] = getattr(param_type, "min", None)
        result["maximum"] = getattr(param_type, "max", None)
    elif "path" in type_name:
        result["kind"] = "path"
    elif "boolean" in type_name or "bool" in type_name:
        result["kind"] = "boolean"
    else:
        result["kind"] = "string"
    return result


def _describe_children(command: Command, parent: str) -> list[dict[str, object]]:
    if not isinstance(command, TyperGroup):
        return []
    context = Context(command)
    children: list[dict[str, object]] = []
    for name in sorted(command.list_commands(context)):
        child = command.get_command(context, name)
        if child is None:
            continue
        capability = get_capability(f"{parent}.{name}")
        children.append(
            {
                "command": f"{parent}.{name}",
                "kind": "group" if isinstance(child, TyperGroup) else "command",
                "summary": _summary(child, capability, isinstance(child, TyperGroup)),
            },
        )
    return children


def _summary(command: Command, capability: Capability | None, is_group: bool) -> str:
    if capability is not None:
        return capability.summary
    if command.help:
        return command.help.strip()
    return "命令组。使用 describe 查看其中的具体命令。" if is_group else "命令操作说明。"


def _build_usage(command_path: list[str], parameters: Iterable[dict[str, object]], is_group: bool) -> str:
    parts = ["auto-email-sender", *command_path]
    if is_group:
        parts.append("<subcommand>")
    else:
        for parameter in parameters:
            if parameter["kind"] == "argument":
                name = str(parameter["name"]).replace("_", "-")
                parts.append(f"<{name}>" if parameter["required"] else f"[{name}]")
        parts.append("[OPTIONS]")
    return " ".join(parts)


def _build_example(command_path: list[str], parameters: Iterable[dict[str, object]]) -> str:
    parts = ["auto-email-sender", "--format", "json", *command_path]
    for parameter in parameters:
        if not parameter["required"]:
            continue
        if parameter["kind"] == "argument":
            parts.append(f"<{str(parameter['name']).replace('_', '-')}>")
            continue
        flags = parameter["flags"]
        if not isinstance(flags, list) or not flags:
            continue
        flag = str(flags[0])
        if parameter["is_flag"]:
            parts.append(flag)
            continue
        type_info = parameter["type"]
        type_kind = type_info.get("kind") if isinstance(type_info, dict) else "value"
        if type_kind == "enum":
            values = type_info.get("values") if isinstance(type_info, dict) else None
            value = str(values[0]) if isinstance(values, list) and values else "<value>"
        elif type_kind == "integer":
            value = "<id>"
        elif type_kind == "path":
            value = "<path>"
        else:
            value = "<value>"
        parts.extend([flag, value])
    return " ".join(parts)


def _input_file_examples(command: str) -> list[dict[str, object]]:
    example = JSON_FILE_EXAMPLES.get(command)
    return [example] if example else []


def _describe_risk(capability: Capability | None) -> dict[str, object]:
    if capability is None:
        return {
            "level": "L0",
            "mutates": False,
            "external_action": False,
            "requires_plan": False,
            "long_running": False,
            "availability": "available",
        }
    return {
        "level": capability.risk_level,
        "mutates": capability.mutates,
        "external_action": capability.external_action,
        "requires_plan": capability.requires_plan,
        "long_running": capability.long_running,
        "availability": capability.availability,
        "unavailable_reason": capability.unavailable_reason,
    }


def _describe_preconditions(command: str) -> dict[str, object]:
    offline_commands = {"version", "guide", "capabilities", "describe", "doctor"}
    return {
        "desktop_app_must_be_open": command not in offline_commands,
        "manual_app_open_required": command not in offline_commands,
        "note": (
            "请先手动打开 Auto Email Sender 并等待加载完成。"
            if command not in offline_commands
            else "此说明命令可在桌面软件未打开时使用。"
        ),
    }


def _next_steps(capability: Capability | None) -> list[str]:
    if capability is None:
        return ["使用 capabilities --resource <resource> 查看具体子命令。"]
    steps: list[str] = []
    if capability.requires_plan:
        steps.append("展示返回的计划；只有用户明确确认后，才能运行 plans execute <plan-id> --confirm。")
    elif capability.mutates:
        steps.append("读取或报告返回结果，确认实际变更和后续待处理项。")
    else:
        steps.append("使用返回的稳定 ID 继续下一步查询或操作。")
    if capability.long_running:
        steps.append("这是异步或耗时操作；轮询对应 get/list 命令后再报告最终结果。")
    return steps


def _json_value(value: Any) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    return str(value)
