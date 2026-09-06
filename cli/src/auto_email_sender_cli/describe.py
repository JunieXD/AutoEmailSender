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
from auto_email_sender_cli.operation_specs import get_operation_spec


CommandDescription = dict[str, object]


DESCRIPTION_VIEWS: Final[frozenset[str]] = frozenset({"summary", "full"})
DESCRIPTION_SECTIONS: Final[tuple[str, ...]] = (
    "globals",
    "input",
    "output",
    "effects",
    "preconditions",
    "trust",
    "states",
    "errors",
    "actions",
    "idempotency",
    "lifecycle",
)
_MAX_COMPACT_DESCRIPTION_BYTES: Final = 4_000
_CLICK_ROOT_CACHE: dict[int, tuple[typer.Typer, Command]] = {}

_DESCRIPTION_SECTION_KEYS: Final[dict[str, str]] = {
    "input": "input",
    "output": "output",
    "effects": "effects",
    "preconditions": "preconditions",
    "trust": "trust",
    "states": "state_transitions",
    "errors": "errors",
    "actions": "next_actions",
    "idempotency": "idempotency",
    "lifecycle": "lifecycle",
}


JSON_FILE_EXAMPLES: dict[str, dict[str, object]] = {
    "crawler.jobs.create-many": {
        "option": "--items-file",
        "format": "json",
        "example": {
            "items": [
                {
                    "university": "示例大学",
                    "school": "计算机学院",
                    "start_url": "https://example.edu/faculty",
                    "entry_type": "list",
                    "llm_profile_id": 1,
                },
            ],
        },
    },
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
    "professors.community.export-batch": {
        "option": "--items-file",
        "format": "json",
        "example": {
            "items": [
                {
                    "university": "示例大学",
                    "school": "计算机学院",
                    "professor_ids": [1, 2, 3],
                },
            ],
        },
    },
}


def describe_command(
    app: typer.Typer, requested_command: str
) -> CommandDescription | None:
    normalized = normalize_capability_command(requested_command)
    capability = get_capability(normalized)
    if capability is not None and capability.availability != "available":
        return _describe_unavailable_capability(capability)
    return _describe_command_from_root(_click_root(app), normalized)


def describe_commands(
    app: typer.Typer,
    commands: Iterable[str],
) -> dict[str, CommandDescription]:
    """Describe multiple commands while building the Click command tree once."""

    root: Command | None = None
    descriptions: dict[str, CommandDescription] = {}
    for requested_command in commands:
        normalized = normalize_capability_command(requested_command)
        capability = get_capability(normalized)
        if capability is not None and capability.availability != "available":
            descriptions[requested_command] = _describe_unavailable_capability(
                capability
            )
            continue
        if root is None:
            root = _click_root(app)
        description = _describe_command_from_root(root, normalized)
        if description is not None:
            descriptions[requested_command] = description
    return descriptions


def describe_command_revisions(
    app: typer.Typer,
    commands: Iterable[str],
) -> dict[str, str]:
    """Return complete parser-derived revisions with one Click tree build.

    ``capabilities`` needs all leaf hashes to negotiate a cache safely.  Calling
    ``get_command`` once per leaf made routine discovery needlessly slow, so a
    single live command tree is shared for this batch without caching any
    contract across process launches.
    """

    revisions: dict[str, str] = {}
    for command, description in describe_commands(app, commands).items():
        revision = description.get("contract_revision")
        if isinstance(revision, str):
            revisions[command] = revision
    return revisions


def _click_root(app: typer.Typer) -> Command:
    cache_key = id(app)
    cached = _CLICK_ROOT_CACHE.get(cache_key)
    if cached is not None and cached[0] is app:
        return cached[1]
    root = get_command(app)
    _CLICK_ROOT_CACHE[cache_key] = (app, root)
    return root


def _describe_command_from_root(
    root: Command,
    requested_command: str,
) -> CommandDescription | None:
    normalized = normalize_capability_command(requested_command)
    if not normalized:
        return None
    command_path = normalized.split(".")
    command: Command = root
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
        "next_steps": next_steps,
        "suggestions": suggest_capabilities(normalized),
    }
    # The contract combines live Click parameters with the explicit operation
    # manifest. Keep the legacy fields in the response for protocol-v2 callers.
    description.update(
        build_command_contract(
            command=normalized,
            parameters=parameters,
            input_file_examples=_input_file_examples(normalized),
            next_steps=next_steps,
        ),
    )
    return description


def _describe_unavailable_capability(capability: Capability) -> CommandDescription:
    """Describe a published manual/UI-only capability without inventing a parser."""

    manual_action = {
        "type": "open_desktop_ui",
        "location": capability.ui_location,
        "instruction": capability.manual_action,
    }
    next_steps = [
        capability.manual_action or "请在 Auto Email Sender 桌面端完成该操作。",
    ]
    description: CommandDescription = {
        "command": capability.command,
        "kind": "unavailable",
        "summary": capability.summary,
        "usage": "该能力没有可执行的 CLI 命令。",
        "example": None,
        "parameters": [],
        "children": [],
        "input_file_examples": [],
        "risk": _describe_risk(capability),
        "next_steps": next_steps,
        "suggestions": [],
        "unavailability": {
            "availability": capability.availability,
            "reason": capability.unavailable_reason,
            "manual_action": manual_action,
        },
    }
    description.update(
        build_command_contract(
            command=capability.command,
            parameters=[],
            input_file_examples=[],
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
    required_parameters: dict[str, object] = {}
    optional_parameter_contracts: dict[str, object] = {}
    if isinstance(parameters, list):
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            name = parameter.get("name")
            if not isinstance(name, str):
                continue
            if parameter.get("required") is True:
                required_parameters[name] = _compact_parameter(parameter)
            else:
                optional_parameter_contracts[name] = _compact_parameter(parameter)

    unavailable = description.get("kind") == "unavailable"
    input_contract = description.get("input")
    global_options: list[str] = []
    if isinstance(input_contract, dict) and not unavailable:
        raw_global_options = input_contract.get("global_options")
        if isinstance(raw_global_options, dict):
            for name, option in raw_global_options.items():
                if not isinstance(name, str) or not isinstance(option, dict):
                    continue
                if name in {"request_id", "format"} or bool(option.get("supported")):
                    global_options.append(name)

    output_contract = description.get("output")
    if not isinstance(output_contract, dict):
        output_contract = {}
    known_fields = output_contract.get("known_fields")
    fields = (
        [field for field in known_fields if isinstance(field, str)]
        if isinstance(known_fields, list)
        else []
    )

    input_summary: dict[str, object] = {
        "required": required_parameters,
        "optional_contracts": optional_parameter_contracts,
        "global_options": global_options,
    }
    if description.get("input_file_examples"):
        input_summary["file_input"] = True
    if isinstance(input_contract, dict):
        for key in ("constraints", "selection_semantics"):
            if input_contract.get(key):
                input_summary[key] = input_contract[key]

    output_traits = [
        trait
        for trait, enabled in (
            ("pagination", output_contract.get("pagination")),
            ("fields", output_contract.get("field_selection")),
            ("filter", output_contract.get("structured_filter")),
            ("file_export", output_contract.get("file_export")),
            ("state", output_contract.get("state_metadata") is not None),
        )
        if enabled
    ]
    output_summary: dict[str, object] = {
        "key_fields": _key_output_fields(fields),
    }
    if output_traits:
        output_summary["traits"] = output_traits
    if output_contract.get("pagination"):
        output_summary["key_fields"] = ["items", "next_cursor", "has_more"]

    summary: dict[str, object] = {
        "command": description.get("command"),
        "summary": description.get("summary"),
        "usage": description.get("usage"),
        "contract_version": description.get("contract_version"),
        "contract_revision": description.get("contract_revision"),
        "risk": _compact_risk(description.get("risk"), description.get("effects")),
        "input": input_summary,
        "output": output_summary,
        "details_available": True,
    }
    for key, value in (
        ("effects", _compact_effects(description.get("effects"))),
        ("preconditions", _compact_preconditions(description.get("preconditions"))),
        ("errors", _compact_errors(description.get("errors"))),
    ):
        if value:
            summary[key] = value
    next_commands = _compact_next_commands(description.get("next_actions"))
    if next_commands:
        summary["next_commands"] = next_commands
    if isinstance(description.get("unavailability"), dict):
        summary["unavailability"] = description["unavailability"]
        summary["input"] = {
            "required": {},
            "optional": [],
            "optional_contracts": {},
            "global_options": [],
            "global_option_contracts": {},
        }
        summary["output"] = {"key_fields": []}
    trust = description.get("trust")
    if isinstance(trust, dict) and trust.get("external_content") != "none":
        summary["trust"] = {
            key: trust[key]
            for key in ("external_content", "instruction_policy")
            if key in trust
        }
    idempotency = description.get("idempotency")
    if (
        isinstance(idempotency, dict)
        and idempotency.get("mode") != "not_applicable"
        and not unavailable
    ):
        summary["idempotency"] = {
            key: idempotency[key]
            for key in ("mode", "supports_idempotent_retry")
            if key in idempotency
        }
    lifecycle = description.get("lifecycle")
    if isinstance(lifecycle, dict) and lifecycle.get("deprecated"):
        summary["lifecycle"] = lifecycle
    children = description.get("children")
    if isinstance(children, list) and children:
        summary["children"] = [
            child.get("command")
            for child in children
            if isinstance(child, dict) and isinstance(child.get("command"), str)
        ]
    return _bound_compact_description(summary)


def _bound_compact_description(summary: dict[str, object]) -> dict[str, object]:
    """Drop routing hints if an unusually broad command card exceeds budget."""

    if _compact_size(summary) > _MAX_COMPACT_DESCRIPTION_BYTES:
        summary.pop("children", None)
        summary.pop("next_commands", None)
    if _compact_size(summary) > _MAX_COMPACT_DESCRIPTION_BYTES:
        summary.pop("idempotency", None)
        summary.pop("trust", None)
    return summary


def _compact_size(value: object) -> int:
    import json

    return len(
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), default=str
        ).encode("utf-8")
    )


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
        if normalized == "globals":
            input_contract = description.get("input")
            options = (
                input_contract.get("global_options", {})
                if isinstance(input_contract, dict)
                else {}
            )
            selected[normalized] = {
                name: _compact_global_option(option)
                for name, option in options.items()
                if isinstance(option, dict) and option.get("supported")
            }
            continue
        key = _DESCRIPTION_SECTION_KEYS.get(normalized)
        if key is None:
            invalid.append(requested)
            continue
        value = description.get(key)
        if normalized == "input" and isinstance(value, dict):
            # ``parameters`` is the legacy parser projection of the same
            # information already represented by ``schema``.  Keep it in the
            # full view for compatibility, but do not repeat it in an
            # explicitly requested input section.
            value = {
                item_key: item_value
                for item_key, item_value in value.items()
                if item_key != "parameters"
            }
        selected[normalized] = value
    return selected, invalid


def _compact_parameter(parameter: dict[str, object]) -> dict[str, object]:
    type_info = parameter.get("type")
    result: dict[str, object] = {
        "type": type_info.get("kind", "string")
        if isinstance(type_info, dict)
        else "string",
    }
    if parameter.get("kind") == "option":
        result["flags"] = parameter.get("flags", [])
    if isinstance(type_info, dict):
        values = type_info.get("values")
        if isinstance(values, list):
            result["enum"] = values
        for key in ("minimum", "maximum"):
            if type_info.get(key) is not None:
                result[key] = type_info[key]
    if bool(parameter.get("multiple")):
        result["multiple"] = True
    help_text = parameter.get("help")
    if isinstance(help_text, str) and help_text.strip():
        result["description"] = help_text.strip()
    if parameter.get("required") is not True and "default" in parameter:
        result["default"] = parameter.get("default")
    nargs = parameter.get("nargs")
    if isinstance(nargs, int) and nargs != 1:
        result["nargs"] = nargs
    return result


def _compact_global_option(option: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {
        "flags": option.get("flags", []),
        "type": option.get("type", "string"),
    }
    values = option.get("values")
    if isinstance(values, list):
        result["enum"] = values
    if option.get("multiple") is True:
        result["multiple"] = True
    for key in ("minimum", "maximum", "default"):
        if key in option:
            result[key] = option[key]
    return result


def _compact_risk(value: object, effects: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    effect_data = effects if isinstance(effects, dict) else {}
    downstream = effect_data.get("downstream_effects")
    downstream_data = downstream if isinstance(downstream, dict) else {}
    traits = [
        trait
        for trait, enabled in (
            ("mutates", value.get("mutates")),
            ("external_action", value.get("external_action")),
            ("cost_may_apply", effect_data.get("cost_may_apply")),
            ("downstream_mutates", downstream_data.get("mutates")),
            ("downstream_external_action", downstream_data.get("external_services")),
            ("downstream_cost_may_apply", downstream_data.get("cost_may_apply")),
            (
                "confirmation_required",
                value.get("confirmation_required_before_invocation"),
            ),
            ("produces_confirmation_plan", value.get("produces_confirmation_plan")),
            ("delegated_effects", value.get("delegated_effects")),
            ("requires_target_contract", value.get("requires_target_contract")),
            ("long_running", value.get("long_running")),
        )
        if enabled
    ]
    result: dict[str, object] = {
        "level": value.get("level"),
        "mode": value.get("risk_mode", "static"),
        "plan_role": value.get("plan_role", "none"),
        "traits": traits,
    }
    if value.get("availability") != "available":
        result["availability"] = value.get("availability")
    return result


def _compact_preconditions(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    runtime = value.get("runtime")
    return {"runtime": runtime} if runtime not in {None, "offline"} else {}


def _compact_effects(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, object] = {}
    services = value.get("external_services")
    if isinstance(services, list) and services:
        result["services"] = services
    nested = value.get("downstream_effects")
    if isinstance(nested, dict) and (
        nested.get("external_services")
        or nested.get("cost_may_apply")
        or nested.get("mutates")
    ):
        downstream: dict[str, object] = {}
        downstream_services = nested.get("external_services")
        if isinstance(downstream_services, list) and downstream_services:
            downstream["services"] = downstream_services
        downstream["traits"] = [
            trait
            for trait, enabled in (
                ("mutates", nested.get("mutates")),
                ("cost_may_apply", nested.get("cost_may_apply")),
            )
            if enabled
        ]
        result["downstream"] = downstream
    return result


def _key_output_fields(fields: list[str]) -> list[str]:
    priority = (
        "id",
        "ids",
        "plan_id",
        "job_id",
        "task_id",
        "status",
        "total_count",
        "by_status",
        "failed_ids",
        "timed_out_ids",
        "action_groups",
        "requested_count",
        "created_count",
        "accepted_count",
        "queued_count",
        "skipped_count",
        "failed_count",
        "failures",
        "revision",
        "items",
        "next_cursor",
        "has_more",
        "mutation_receipt",
        "effects",
        "warnings",
    )
    selected = [field for field in priority if field in fields]
    if selected:
        return selected
    return fields[:6]


def _compact_errors(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    generic_errors = {
        "INVALID_ARGUMENT",
        "APP_UNAVAILABLE",
        "RUNTIME_PROTOCOL_MISMATCH",
        "IF_REVISION_REQUIRES_WRITE",
    }
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for error in value:
        if not isinstance(error, dict):
            continue
        code = error.get("code")
        if not isinstance(code, str) or code in seen or code in generic_errors:
            continue
        seen.add(code)
        result.append(
            {"code": code, "retryable": True}
            if error.get("retryable")
            else {"code": code}
        )
    return result


def _compact_next_commands(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        command
        for action in value
        if isinstance(action, dict)
        and isinstance((command := action.get("command")), str)
    ]


def _describe_parameter(parameter: Any) -> dict[str, object]:
    option = parameter if isinstance(parameter, TyperOption) else None
    names = (
        list(option.opts) + list(option.secondary_opts) if option else [parameter.name]
    )
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
    return (
        "命令组。使用 describe 查看其中的具体命令。" if is_group else "命令操作说明。"
    )


def _build_usage(
    command_path: list[str], parameters: Iterable[dict[str, object]], is_group: bool
) -> str:
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


def _build_example(
    command_path: list[str], parameters: Iterable[dict[str, object]]
) -> str:
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
            "risk_mode": "static",
            "plan_role": "none",
            "delegated_effects": False,
            "requires_target_contract": False,
            "confirmation_required_before_invocation": False,
            "produces_confirmation_plan": False,
        }
    spec = get_operation_spec(capability.command)
    return {
        "level": capability.risk_level,
        "mutates": capability.mutates,
        "external_action": capability.external_action,
        "requires_plan": capability.requires_plan,
        "long_running": capability.long_running,
        "availability": capability.availability,
        "unavailable_reason": capability.unavailable_reason,
        "risk_mode": spec.effects.risk_mode if spec is not None else "static",
        "plan_role": spec.effects.plan_role if spec is not None else "none",
        "delegated_effects": spec.effects.delegated_effects
        if spec is not None
        else False,
        "requires_target_contract": (
            spec.effects.requires_target_contract if spec is not None else False
        ),
        "confirmation_required_before_invocation": (
            spec.effects.requires_confirmation_plan if spec is not None else False
        ),
        "produces_confirmation_plan": (
            spec.effects.produces_confirmation_plan if spec is not None else False
        ),
    }


def _next_steps(capability: Capability | None) -> list[str]:
    _ = capability
    return ["使用 capabilities --resource <resource> 查看具体子命令。"]


def _json_value(value: Any) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    return str(value)
