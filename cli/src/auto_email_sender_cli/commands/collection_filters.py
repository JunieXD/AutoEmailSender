from __future__ import annotations

import json
import unicodedata
from typing import Any

from auto_email_sender_cli.capabilities import (
    collection_filter_fields,
    collection_filter_operator_fields,
    collection_filter_operators,
    supports_dynamic_action_links,
    supports_pagination,
)
from auto_email_sender_cli.errors import CliError

_SERVER_FILTER_PARAMETERS: dict[str, dict[str, str]] = {
    "professors.list": {
        "id": "professor_id",
    },
    "professors.tags.list": {
        "id": "tag_id",
        "name": "name",
    },
    "campaigns.list": {
        "identity_id": "identity_id",
        "status": "status",
    },
    "crawler.jobs.list": {
        "effective_models": "effective_model_name",
        "llm_profile_id": "llm_profile_id",
        "requested_model_name": "requested_model_name",
        "school": "school",
        "status": "status",
        "university": "university",
    },
    "enrichment.jobs.list": {
        "llm_profile_id": "llm_profile_id",
        "status": "status",
    },
    "matching.jobs.list": {
        "identity_id": "identity_id",
        "llm_profile_id": "llm_profile_id",
        "status": "status",
    },
    "communications.threads.list": {
        "identity_id": "identity_id",
        "professor_id": "professor_id",
        "has_sent": "sent",
        "has_reply": "replied",
    },
    "communications.messages.list": {
        "identity_id": "identity_id",
        "professor_id": "professor_id",
        "direction": "direction",
    },
    "templates.list": {
        "id": "template_id",
        "is_default": "is_default",
    },
    "materials.list": {
        "id": "material_id",
        # Both response spellings describe upload provenance. Default-state
        # context is exposed separately as --target-identity-id.
        "identity_id": "source_identity_id",
        "source_identity_id": "source_identity_id",
        "material_type": "material_type",
    },
    "identities.list": {
        "id": "identity_id",
        "is_default": "is_default",
        "smtp_configured": "smtp_configured",
        "imap_configured": "imap_configured",
    },
    "llm-profiles.list": {
        "id": "profile_id",
        "provider": "provider",
        "model_name": "model_name",
        "is_default": "is_default",
    },
    "communication-groups.list": {
        "id": "group_id",
        "match_source_identity_id": "match_source_identity_id",
    },
    "diagnostics.logs": {
        "level": "level",
        "category": "category",
        "event_name": "event_name",
        "request_id": "request_id",
        "entity_type": "entity_type",
        "entity_id": "entity_id",
    },
    "usage.records": {
        "feature_type": "feature_type",
        "model_name": "model_name",
    },
    "deliveries.list": {
        "identity_id": "identity_id",
        "source": "source",
        "status": "status",
    },
}


_SERVER_OPERATOR_FILTER_PARAMETERS: dict[tuple[str, str, str], str] = {
    ("professors.list", "name", "contains_script"): "name_script",
}


_SERVER_FIELD_PROJECTION_COMMANDS = frozenset(
    {
        "professors.list",
        "professors.tags.list",
        "communications.threads.list",
        "communications.messages.list",
        "templates.list",
        "materials.list",
        "identities.list",
        "llm-profiles.list",
        "communication-groups.list",
        "usage.records",
        "diagnostics.logs",
    },
)


_FILTER_OPERATORS = {
    "eq",
    "ne",
    "in",
    "contains",
    "contains_script",
    "empty",
    "exists",
    "gt",
    "gte",
    "lt",
    "lte",
}


_UNICODE_SCRIPTS = frozenset({"latin", "han", "cyrillic", "arabic", "digit"})


def apply_structured_filter(
    data: Any,
    expression: str | None,
    *,
    command: str | None = None,
) -> Any:
    """Apply a small, explicit collection filter after a complete fetch.

    This is deliberately a whitelist evaluator.  It does not parse SQL, Python
    or arbitrary predicates, and therefore remains safe when the expression is
    supplied by an Agent.
    """

    if not expression:
        return data
    try:
        parsed = json.loads(expression)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CliError(
            code="INVALID_FILTER",
            message="--filter 必须是合法 JSON 对象。",
            exit_code=2,
        ) from exc
    if not isinstance(parsed, dict) or not parsed:
        raise CliError(
            code="INVALID_FILTER",
            message="--filter 必须是非空 JSON 对象。",
            exit_code=2,
        )
    collection_key = (
        "items"
        if isinstance(data, dict) and isinstance(data.get("items"), list)
        else None
    )
    if collection_key is None:
        raise CliError(
            code="FILTER_NOT_SUPPORTED",
            message="当前命令不是可列表资源，不能使用 --filter。",
            exit_code=2,
        )
    declared_fields = collection_filter_fields(command or "")
    declared_operators = set(collection_filter_operators(command or ""))
    if not declared_fields or not declared_operators:
        raise CliError(
            code="FILTER_NOT_SUPPORTED",
            message="当前命令未声明可用的筛选字段或运算符。",
            exit_code=2,
        )
    filters: list[tuple[str, str, object]] = []
    for field, condition in parsed.items():
        if (
            not isinstance(field, str)
            or not field
            or not field.replace("_", "").isalnum()
        ):
            raise CliError(
                code="INVALID_FILTER", message="筛选字段名无效。", exit_code=2
            )
        if field not in declared_fields:
            raise CliError(
                code="INVALID_FILTER",
                message=f"字段 {field} 未在当前命令合同中声明。",
                exit_code=2,
                details={"allowed_fields": sorted(declared_fields)},
            )
        if isinstance(condition, dict):
            if len(condition) != 1:
                raise CliError(
                    code="INVALID_FILTER",
                    message=f"字段 {field} 只能指定一个运算符。",
                    exit_code=2,
                )
            operator, expected = next(iter(condition.items()))
        else:
            operator, expected = "eq", condition
        if operator not in declared_operators or operator not in _FILTER_OPERATORS:
            raise CliError(
                code="INVALID_FILTER",
                message=f"字段 {field} 的运算符 {operator} 不受支持。",
                exit_code=2,
                details={"allowed_operators": sorted(_FILTER_OPERATORS)},
            )
        if operator in {"exists", "empty"} and not isinstance(expected, bool):
            raise CliError(
                code="INVALID_FILTER",
                message=f"字段 {field} 的运算符 {operator} 需要 JSON 布尔值。",
                exit_code=2,
            )
        if operator == "in" and not isinstance(expected, list):
            raise CliError(
                code="INVALID_FILTER",
                message=f"字段 {field} 的运算符 in 需要 JSON 数组。",
                exit_code=2,
            )
        if operator == "contains" and isinstance(expected, (dict, list)):
            raise CliError(
                code="INVALID_FILTER",
                message=f"字段 {field} 的运算符 contains 需要字符串或标量。",
                exit_code=2,
            )
        if operator == "contains_script":
            allowed_fields = collection_filter_operator_fields(command or "", operator)
            if field not in allowed_fields:
                raise CliError(
                    code="INVALID_FILTER",
                    message=f"字段 {field} 不支持运算符 contains_script。",
                    exit_code=2,
                    details={"allowed_fields": sorted(allowed_fields)},
                )
            if (
                not isinstance(expected, str)
                or expected.strip().lower() not in _UNICODE_SCRIPTS
            ):
                raise CliError(
                    code="INVALID_FILTER",
                    message=(
                        f"字段 {field} 的运算符 contains_script 仅支持："
                        f"{', '.join(sorted(_UNICODE_SCRIPTS))}。"
                    ),
                    exit_code=2,
                    details={"allowed_scripts": sorted(_UNICODE_SCRIPTS)},
                )
            expected = expected.strip().lower()
        filters.append((field, str(operator), expected))

    def matches(item: object) -> bool:
        if not isinstance(item, dict):
            return False
        return all(
            _matches_filter(item.get(field), operator, expected)
            for field, operator, expected in filters
        )

    filtered = [item for item in data[collection_key] if matches(item)]
    result = {
        **data,
        collection_key: filtered,
        "filter": parsed,
        "filtered_count": len(filtered),
    }
    if isinstance(result.get("records"), list):
        result["records"] = filtered
    if command == "usage.records":
        # The backend summary describes the unfiltered page set.  Once the
        # CLI applies a local Agent filter, recompute the token totals so the
        # summary cannot be mistaken for the filtered result.
        result["summary"] = {
            field: sum(
                int(item.get(field) or 0) for item in filtered if isinstance(item, dict)
            )
            for field in (
                "input_tokens",
                "output_tokens",
                "cached_tokens",
                "total_tokens",
            )
        }
        result["summary"]["record_count"] = len(filtered)
    # Filtering is complete locally, so a cursor must not suggest another page.
    result["next_cursor"] = None
    result["has_more"] = False
    result["fetched_all"] = True
    return result


def _annotate_filter_execution(
    data: Any,
    *,
    expression: str | None,
    server_params: dict[str, object],
) -> Any:
    if not expression or not isinstance(data, dict):
        return data
    return {
        **data,
        "filter_execution": {
            "mode": (
                "server_requested_local_validated"
                if server_params
                else "local_complete_scan"
            ),
            "server_parameters": sorted(server_params),
            "local_validation": True,
        },
    }


def server_filter_params(expression: str | None, *, command: str) -> dict[str, object]:
    """Translate safe native equality filters into backend query parameters.

    The CLI still evaluates the full expression after fetching every returned
    page. Older desktop versions may ignore an unknown query parameter, while
    the local pass preserves correctness and acts as the compatibility fallback.
    """

    mapping = _SERVER_FILTER_PARAMETERS.get(command)
    if not expression or not mapping:
        return {}
    try:
        parsed = json.loads(expression)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, object] = {}
    for field, condition in parsed.items():
        if not isinstance(field, str):
            continue
        if isinstance(condition, dict):
            if len(condition) != 1:
                continue
            operator, expected = next(iter(condition.items()))
        else:
            operator = "eq"
            expected = condition
        parameter = _SERVER_OPERATOR_FILTER_PARAMETERS.get(
            (command, field, str(operator))
        )
        if parameter is None:
            parameter = mapping.get(field)
            allowed_operator = (
                "contains"
                if command == "crawler.jobs.list" and field == "effective_models"
                else "eq"
            )
            if parameter is None or operator != allowed_operator:
                continue
        if expected is None or isinstance(expected, dict | list):
            continue
        if operator == "contains_script":
            if (
                not isinstance(expected, str)
                or expected.strip().lower() not in _UNICODE_SCRIPTS
            ):
                continue
            expected = expected.strip().lower()
        if not _server_filter_value_is_safe(command, field, expected):
            continue
        result[parameter] = expected
    return result


def server_field_params(
    fields: str | None,
    *,
    expression: str | None,
    command: str,
    include_revisions: bool,
) -> dict[str, object]:
    """Request a safe DTO projection while retaining local fallback inputs."""

    if (
        not fields
        or command not in _SERVER_FIELD_PROJECTION_COMMANDS
        or include_revisions
        or supports_dynamic_action_links(command)
    ):
        return {}
    selected = [field.strip() for field in fields.split(",") if field.strip()]
    if not selected or "revision" in selected:
        return {}
    try:
        parsed_filter = json.loads(expression) if expression else {}
    except (TypeError, json.JSONDecodeError):
        parsed_filter = {}
    if isinstance(parsed_filter, dict):
        selected.extend(
            field
            for field in parsed_filter
            if isinstance(field, str) and field not in selected
        )
    if command == "usage.records":
        selected.extend(
            field
            for field in (
                "input_tokens",
                "output_tokens",
                "cached_tokens",
                "total_tokens",
            )
            if field not in selected
        )
    # Keep deterministic ordering for cache keys and request recordings.
    return {"fields": ",".join(dict.fromkeys(selected))}


def _server_filter_value_is_safe(command: str, field: str, value: object) -> bool:
    if field == "id" or field.endswith("_id"):
        return isinstance(value, int) and not isinstance(value, bool) and value > 0
    if field in {
        "has_sent",
        "has_reply",
        "is_default",
        "smtp_configured",
        "imap_configured",
    }:
        return isinstance(value, bool)
    if command == "communications.messages.list" and field == "direction":
        return value in {"sent", "received", "draft"}
    if command == "usage.records" and field == "feature_type":
        return value in {
            "crawl",
            "information_enrichment",
            "match_analysis",
            "draft_generation",
        }
    if command == "campaigns.list" and field == "status":
        return value in {"running", "paused", "stopped", "completed", "expired"}
    if command == "deliveries.list" and field == "source":
        return value in {"manual", "batch"}
    if command == "deliveries.list" and field == "status":
        return value in {
            "waiting_scheduled",
            "send_asap",
            "batch_paused",
            "sending",
            "send_failed",
            "schedule_missed",
            "draft_failed",
            "batch_stopped",
            "schedule_expired",
            "sent",
            "replied",
            "canceled_schedule",
            "canceled_send",
        }
    return isinstance(value, str) and bool(value.strip())


def _merge_server_filter_params(
    request_params: dict[str, object],
    filter_params: dict[str, object],
) -> dict[str, object]:
    result = dict(request_params)
    for key, value in filter_params.items():
        existing = result.get(key)
        if existing is None or existing == value:
            result[key] = value
    return result


def _matches_filter(value: object, operator: str, expected: object) -> bool:
    if operator == "exists":
        return (value is not None) is bool(expected)
    if operator == "empty":
        is_empty = value is None or value == "" or value == []
        return is_empty is bool(expected)
    if operator == "in":
        return isinstance(expected, list) and value in expected
    if operator == "contains":
        if isinstance(value, list):
            return expected in value
        return str(expected).lower() in str(value or "").lower()
    if operator == "contains_script":
        return _contains_unicode_script(value, expected)
    if operator == "eq":
        return value == expected
    if operator == "ne":
        return value != expected
    try:
        if operator == "gt":
            return value is not None and value > expected
        if operator == "gte":
            return value is not None and value >= expected
        if operator == "lt":
            return value is not None and value < expected
        if operator == "lte":
            return value is not None and value <= expected
    except TypeError:
        return False
    return False


def _contains_unicode_script(value: object, expected: object) -> bool:
    if not isinstance(value, str) or not isinstance(expected, str):
        return False
    script = expected.lower()
    for character in value:
        if script == "digit" and character.isdigit():
            return True
        unicode_name = unicodedata.name(character, "")
        if script == "latin" and unicode_name.startswith("LATIN"):
            return True
        if script == "han" and unicode_name.startswith(
            ("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH"),
        ):
            return True
        if script == "cyrillic" and "CYRILLIC" in unicode_name:
            return True
        if script == "arabic" and "ARABIC" in unicode_name:
            return True
    return False


def project_fields(data: Any, fields: str | None, *, command: str | None = None) -> Any:
    """Apply the common collection field-selection contract locally.

    The backend still controls which records are returned.  Projection happens
    after pagination/all-pages fetching so a caller can safely request a small
    Agent context without changing business filtering semantics.
    """

    if not fields:
        return data
    selected = tuple(item.strip() for item in fields.split(",") if item.strip())
    if not selected:
        raise CliError(
            code="INVALID_FIELD_SELECTION",
            message="--fields 至少需要一个字段名。",
            exit_code=2,
        )
    if any(not item.replace("_", "").isalnum() for item in selected):
        raise CliError(
            code="INVALID_FIELD_SELECTION",
            message="--fields 只支持逗号分隔的字段名，不支持表达式。",
            exit_code=2,
        )
    collection_key = (
        "items"
        if isinstance(data, dict) and isinstance(data.get("items"), list)
        else (
            "records"
            if isinstance(data, dict) and isinstance(data.get("records"), list)
            else None
        )
    )
    if collection_key is None:
        raise CliError(
            code="FIELD_SELECTION_NOT_SUPPORTED",
            message="当前命令不是可列表资源，不能使用 --fields。",
            exit_code=2,
        )
    records = [item for item in data[collection_key] if isinstance(item, dict)]
    available = sorted({key for item in records for key in item})
    declared = declared_collection_fields(command)
    if command:
        declared = declared.union(collection_filter_fields(command))
    if declared:
        available = sorted(set(available).union(declared))
    unknown = [item for item in selected if item not in available]
    if unknown:
        raise CliError(
            code="INVALID_FIELD_SELECTION",
            message=f"未声明或不存在的字段：{', '.join(unknown)}",
            exit_code=2,
            details={"requested_fields": list(selected), "available_fields": available},
        )
    projected_items = []
    for item in data[collection_key]:
        if not isinstance(item, dict):
            projected_items.append(item)
            continue
        projected = {key: item.get(key) for key in selected}
        # Protocol metadata that is present remains available even when the
        # Agent projects business fields down to a compact view. Collection
        # revisions are added only when explicitly requested.
        for metadata_field in (
            "revision",
            "available_actions",
            "blocked_actions",
            "blocked_reason",
        ):
            if metadata_field in item and metadata_field not in projected:
                projected[metadata_field] = item[metadata_field]
        projected_items.append(projected)
    result = {
        **data,
        collection_key: projected_items,
        "selected_fields": list(selected),
    }
    # Keep legacy aliases consistent when a page-shaped endpoint was normalized
    # from ``records`` to the common ``items`` view.
    if collection_key == "items" and isinstance(result.get("records"), list):
        result["records"] = projected_items
    return result


_DECLARED_COLLECTION_FIELDS: dict[str, frozenset[str]] = {
    "usage.records": frozenset(
        {
            "id",
            "feature_type",
            "feature_label",
            "title",
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "total_tokens",
            "model_name",
            "identity_name",
            "created_at",
            "status",
        },
    ),
    # ``professors.community.records`` and ``preview`` project each item in
    # their bounded ``records`` array.  Declare the comparison DTO fields so a
    # projection remains deterministic even when the selected unit is empty.
    "professors.community.records": frozenset(
        {
            "record",
            "comparison_token",
            "category",
            "local_professor_id",
            "local_professor_name",
            "local_archived",
            "linked",
            "identity_conflict",
            "match_reason",
            "import_blocked",
            "import_blocked_reason",
            "fields",
        },
    ),
    "professors.community.preview": frozenset(
        {
            "record",
            "comparison_token",
            "category",
            "local_professor_id",
            "local_professor_name",
            "local_archived",
            "linked",
            "identity_conflict",
            "match_reason",
            "import_blocked",
            "import_blocked_reason",
            "fields",
        },
    ),
}


def declared_collection_fields(command: str | None) -> frozenset[str]:
    fields = _DECLARED_COLLECTION_FIELDS.get(command or "", frozenset())
    # Revisions are added locally to every paged item, including an empty
    # collection where there is no concrete record from which to infer the
    # field. Keep ``--fields revision`` consistent with the published contract
    # in both cases.
    if command and supports_pagination(command):
        fields = fields | {"revision"}
    return fields
