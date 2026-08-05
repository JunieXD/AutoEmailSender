from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

import typer

from auto_email_sender_cli.client import AgentApiClient
from auto_email_sender_cli.capabilities import (
    capability_stateful,
    collection_filter_fields,
    collection_filter_operators,
    supports_if_revision,
    supports_pagination,
)
from auto_email_sender_cli.errors import CliError
from auto_email_sender_cli.output import CliContext, OutputFormat, emit_error, emit_success


HumanFormatter = Callable[[Any], str]
_MAX_FETCH_PAGES = 10_000


def cli_context(ctx: typer.Context) -> CliContext:
    value = ctx.find_root().obj
    if isinstance(value, CliContext):
        return value
    return CliContext(output_format=OutputFormat.HUMAN)


def validate_context_options(
    context: CliContext,
    *,
    supports_filter: bool,
    supports_output_file: bool,
    supports_if_revision: bool = False,
) -> None:
    """Reject root collection options a command cannot actually honor.

    Most commands go through :func:`run_read_command` or
    :func:`run_write_command`, but file uploads/downloads and a few settings
    operations have custom request handling.  Keeping this check in one place
    prevents those commands from silently ignoring an Agent's global
    ``--filter``, ``--output-file`` or ``--force-output`` flags.
    """

    if context.force_output and not context.output_file:
        raise CliError(
            code="FORCE_OUTPUT_REQUIRES_OUTPUT_FILE",
            message="--force-output 只能与 --output-file 一起使用。",
            exit_code=2,
        )
    if context.if_revision and not supports_if_revision:
        raise CliError(
            code="IF_REVISION_REQUIRES_WRITE",
            message="--if-revision 只能用于支持版本保护的写入命令。",
            exit_code=2,
            details={"hint": "先读取对象的 revision，再在写入命令上使用 --if-revision。"},
        )
    if context.filter_expression and not supports_filter:
        raise CliError(
            code="FILTER_NOT_SUPPORTED",
            message="当前命令不是可列表资源，不能使用 --filter。",
            exit_code=2,
        )
    if context.output_file and not supports_output_file:
        raise CliError(
            code="OUTPUT_FILE_REQUIRES_COLLECTION",
            message="--output-file 只能用于集合读取命令。",
            exit_code=2,
        )


def run_read_command(
    ctx: typer.Context,
    *,
    command: str,
    path: str,
    params: dict[str, object] | None = None,
    guide_topic: str = "overview",
    human_formatter: HumanFormatter | None = None,
    fetch_all: bool = False,
    fields: str | None = None,
    timeout: float = 30.0,
) -> Any:
    context = cli_context(ctx)
    try:
        supports_collection_options = supports_pagination(command)
        validate_context_options(
            context,
            supports_filter=supports_collection_options,
            supports_output_file=supports_collection_options,
            supports_if_revision=False,
        )
        client = AgentApiClient(timeout=timeout)
        # A structured filter is evaluated locally after the complete
        # collection has been read, but only paged collection commands can be
        # fetched that way.  For detail commands keep the normal single GET so
        # the caller receives the intentional FILTER_NOT_SUPPORTED error
        # instead of an accidental request with ``limit=500``.
        fetch_everything = fetch_all or (
            bool(context.filter_expression) and supports_pagination(command)
        )
        data = (
            fetch_all_pages(client, path, params=params)
            if fetch_everything
            else client.request("GET", path, params=_without_none(params))
        )
        data = normalize_collection_response(data, command=command)
        data = apply_structured_filter(data, context.filter_expression, command=command)
        # Compute the optimistic-concurrency token from the complete record
        # before applying a caller's field projection.  Otherwise
        # ``--fields id,name`` would produce a different revision from the
        # server's full object and a subsequent ``--if-revision`` write would
        # be rejected even when nobody changed the record.
        data = add_revisions(augment_state_metadata(data, command=command))
        data = project_fields(data, fields, command=command)
        data, exported_file = export_collection_if_requested(data, context)
        human_text = human_formatter(data) if human_formatter else None
        emit_success(
            context,
            command=command,
            data=data,
            human_text=(
                f"已将集合结果导出到：{exported_file}"
                if exported_file is not None
                else human_text
            ),
            guide_topic=guide_topic,
            app_version=client.descriptor.app_version,
            request_id=getattr(client, "last_request_id", None),
        )
        return data
    except CliError as error:
        emit_error(context, command=command, error=error, guide_topic=guide_topic)
        raise typer.Exit(error.exit_code) from error


def run_write_command(
    ctx: typer.Context,
    *,
    command: str,
    path: str,
    method: str = "POST",
    params: dict[str, object] | None = None,
    json_body: object | None = None,
    fields: str | None = None,
    guide_topic: str = "overview",
    human_formatter: HumanFormatter | None = None,
    timeout: float = 360.0,
    use_idempotency_key: bool = True,
) -> Any:
    context = cli_context(ctx)
    try:
        validate_context_options(
            context,
            supports_filter=False,
            supports_output_file=False,
            supports_if_revision=supports_if_revision(command),
        )
        client = AgentApiClient(timeout=timeout)
        request_id = (
            cli_context(ctx).request_id or f"cli_{secrets.token_urlsafe(24)}"
            if use_idempotency_key
            else None
        )
        data = client.request(
            method,
            path,
            params=_without_none(params),
            json_body=json_body,
            idempotency_key=request_id,
            if_revision=cli_context(ctx).if_revision,
        )
        data = add_revisions(
            augment_state_metadata(project_fields(data, fields, command=command), command=command),
        )
        if use_idempotency_key:
            data = add_mutation_receipt(
                data,
                command=command,
                request_id=request_id,
                json_body=json_body,
                response_headers=getattr(client, "last_response_headers", {}),
            )
        emit_success(
            context,
            command=command,
            data=data,
            human_text=human_formatter(data) if human_formatter else None,
            guide_topic=guide_topic,
            app_version=client.descriptor.app_version,
            request_id=getattr(client, "last_request_id", None) or request_id,
        )
        return data
    except CliError as error:
        emit_error(context, command=command, error=error, guide_topic=guide_topic)
        raise typer.Exit(error.exit_code) from error


def fetch_all_pages(
    client: AgentApiClient,
    path: str,
    *,
    params: dict[str, object] | None = None,
) -> dict[str, object]:
    request_params = _without_none(params)
    page_mode = "page" in request_params or "page_size" in request_params
    offset_mode = "offset" in request_params and not page_mode and "cursor" not in request_params
    # All current Agent collection endpoints expose an integer cursor, but it
    # is still a cursor contract rather than an offset contract. Preserve a
    # caller-provided starting cursor (especially ``--all --cursor N``) and
    # continue with the server's next_cursor values. Previously this value was
    # discarded, causing an all-pages read to restart at the beginning.
    initial_cursor = request_params.pop("cursor", None)
    # The Typer commands expose ``0`` as the default for cursor-based
    # endpoints.  Zero means "start at the beginning" and is also the
    # server-side default, so do not add a redundant cursor query parameter
    # for the first page.  Non-zero values remain significant and must be
    # preserved for ``--all --cursor N``.
    if initial_cursor in (0, "0"):
        initial_cursor = None
    if not page_mode and not offset_mode:
        request_params["limit"] = 500
    items: list[object] = []
    cursor: str | None = (
        str(initial_cursor)
        if initial_cursor is not None
        else None
    )
    page = int(request_params.get("page", 1) or 1)
    offset = int(request_params.get("offset", 0) or 0)
    seen_cursors: set[str] = set()
    page_count = 0
    total: int | None = None
    envelope: dict[str, object] = {}
    had_records_alias = False
    pagination_mode: str | None = None
    while True:
        page_count += 1
        if page_count > _MAX_FETCH_PAGES:
            raise CliError(
                code="PAGINATION_LIMIT_EXCEEDED",
                message="本地服务分页次数超过安全上限，已停止继续读取。",
                exit_code=8,
            )
        if page_mode:
            request_params["page"] = page
        elif offset_mode:
            request_params["offset"] = offset
        elif cursor is not None:
            if cursor in seen_cursors:
                raise CliError(
                    code="PAGINATION_LOOP",
                    message="本地服务返回了重复的分页游标，已停止继续读取。",
                    exit_code=8,
                )
            request_params["cursor"] = cursor
        if cursor is not None:
            seen_cursors.add(cursor)
        payload = normalize_collection_response(client.request("GET", path, params=request_params))
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise CliError(
                code="INVALID_API_RESPONSE",
                message="本地服务返回了无法识别的分页结果。",
                exit_code=8,
            )
        if isinstance(payload.get("total"), int):
            total = int(payload["total"])
        for key in ("summary", "pagination", "model_options"):
            if key in payload and key not in envelope:
                envelope[key] = payload[key]
        if isinstance(payload.get("records"), list):
            had_records_alias = True
        if isinstance(payload.get("pagination_mode"), str):
            pagination_mode = str(payload["pagination_mode"])
        items.extend(payload["items"])
        if not payload.get("has_more"):
            break
        next_cursor = payload.get("next_cursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            raise CliError(
                code="INVALID_API_RESPONSE",
                message="本地服务分页结果缺少 next_cursor。",
                exit_code=8,
            )
        if payload.get("pagination_mode") == "page":
            try:
                next_page = int(next_cursor)
            except (TypeError, ValueError) as exc:
                raise CliError(
                    code="INVALID_API_RESPONSE",
                    message="本地服务分页结果包含无效的页码游标。",
                    exit_code=8,
                ) from exc
            if next_page <= page:
                raise CliError(
                    code="PAGINATION_LOOP",
                    message="本地服务返回了不会前进的分页页码，已停止继续读取。",
                    exit_code=8,
                )
            page = next_page
            page_mode = True
        elif offset_mode or payload.get("pagination_mode") == "offset":
            try:
                next_offset = int(next_cursor)
            except (TypeError, ValueError) as exc:
                raise CliError(
                    code="INVALID_API_RESPONSE",
                    message="本地服务分页结果包含无效的 offset 游标。",
                    exit_code=8,
                ) from exc
            if next_offset <= offset:
                raise CliError(
                    code="PAGINATION_LOOP",
                    message="本地服务返回了不会前进的 offset 游标，已停止继续读取。",
                    exit_code=8,
                )
            offset = next_offset
            offset_mode = True
        else:
            if next_cursor in seen_cursors:
                raise CliError(
                    code="PAGINATION_LOOP",
                    message="本地服务返回了重复的分页游标，已停止继续读取。",
                    exit_code=8,
                )
            cursor = next_cursor
    result: dict[str, object] = {
        **envelope,
        "items": items,
        "next_cursor": None,
        "has_more": False,
        "fetched_all": True,
    }
    if had_records_alias:
        # Keep the legacy alias useful after an all-pages fetch instead of
        # returning only the first page's records array.
        result["records"] = items
    if pagination_mode is not None:
        result["pagination_mode"] = pagination_mode
    if total is not None:
        result["total"] = total
    return result


def normalize_collection_response(data: Any, *, command: str | None = None) -> Any:
    """Normalize legacy page-shaped collections to the common ``items`` form.

    Most Agent API endpoints already use ``items``/``next_cursor``.  A few
    read-only analytics endpoints historically return ``records`` plus a
    numeric ``pagination`` object.  Keeping their original fields while adding
    this additive view lets every CLI consumer use the same projection,
    JSONL-export and stdout-summary protocol.
    """

    if not isinstance(data, dict):
        return data
    # A few non-paged envelopes legitimately contain an ``items`` array (for
    # example campaigns.resend-context).  They are not cursor collections and
    # must not gain synthetic pagination fields or be fed to collection-only
    # projections.  fetch_all_pages calls this helper without a command and
    # therefore keeps the legacy page normalization behavior.
    if command is not None and not supports_pagination(command):
        return data
    if isinstance(data.get("items"), list):
        has_more_value = data.get("has_more")
        next_cursor_value = data.get("next_cursor")
        pagination_mode = data.get("pagination_mode")
        total = data.get("total")
        offset = data.get("offset")
        limit = data.get("limit")
        if (
            not isinstance(has_more_value, bool)
            and isinstance(total, int)
            and isinstance(offset, int)
            and isinstance(limit, int)
        ):
            has_more_value = offset + len(data["items"]) < total
            pagination_mode = "offset"
            if has_more_value:
                next_cursor_value = str(offset + len(data["items"]))
        return {
            **data,
            "next_cursor": next_cursor_value,
            "has_more": bool(has_more_value),
            **({"pagination_mode": pagination_mode} if pagination_mode else {}),
        }
    records = data.get("records")
    pagination = data.get("pagination")
    if not isinstance(records, list) or not isinstance(pagination, dict):
        return data
    try:
        page = int(pagination.get("page", 1))
        total_pages = int(pagination.get("total_pages", page))
    except (TypeError, ValueError):
        page = 1
        total_pages = 1
    has_more = page < total_pages
    return {
        **data,
        "items": records,
        "next_cursor": str(page + 1) if has_more else None,
        "has_more": has_more,
        "pagination_mode": "page",
    }


_FILTER_OPERATORS = {"eq", "ne", "in", "contains", "empty", "exists", "gt", "gte", "lt", "lte"}


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
    collection_key = "items" if isinstance(data, dict) and isinstance(data.get("items"), list) else None
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
        if not isinstance(field, str) or not field or not field.replace("_", "").isalnum():
            raise CliError(code="INVALID_FILTER", message="筛选字段名无效。", exit_code=2)
        if field not in declared_fields:
            raise CliError(
                code="INVALID_FILTER",
                message=f"字段 {field} 未在当前命令合同中声明。",
                exit_code=2,
                details={"allowed_fields": sorted(declared_fields)},
            )
        if isinstance(condition, dict):
            if len(condition) != 1:
                raise CliError(code="INVALID_FILTER", message=f"字段 {field} 只能指定一个运算符。", exit_code=2)
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
        filters.append((field, str(operator), expected))

    def matches(item: object) -> bool:
        if not isinstance(item, dict):
            return False
        return all(_matches_filter(item.get(field), operator, expected) for field, operator, expected in filters)

    filtered = [item for item in data[collection_key] if matches(item)]
    result = {**data, collection_key: filtered, "filter": parsed, "filtered_count": len(filtered)}
    if isinstance(result.get("records"), list):
        result["records"] = filtered
    if command == "usage.records":
        # The backend summary describes the unfiltered page set.  Once the
        # CLI applies a local Agent filter, recompute the token totals so the
        # summary cannot be mistaken for the filtered result.
        result["summary"] = {
            field: sum(
                int(item.get(field) or 0)
                for item in filtered
                if isinstance(item, dict)
            )
            for field in ("input_tokens", "output_tokens", "cached_tokens", "total_tokens")
        }
        result["summary"]["record_count"] = len(filtered)
    # Filtering is complete locally, so a cursor must not suggest another page.
    result["next_cursor"] = None
    result["has_more"] = False
    result["fetched_all"] = True
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


def optional_bool(value: str | None, *, option_name: str) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise typer.BadParameter(
        f"{option_name} 仅支持 true 或 false",
        param_hint=option_name,
    )


def format_page(
    data: Any,
    *,
    columns: tuple[tuple[str, str], ...],
    empty_message: str = "没有符合条件的记录。",
) -> str:
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return str(data)
    items = data["items"]
    if not items:
        return empty_message
    header = "\t".join(label for _, label in columns)
    lines = [header]
    for item in items:
        if not isinstance(item, dict):
            lines.append(str(item))
            continue
        lines.append(
            "\t".join(_display_value(item.get(key)) for key, _ in columns),
        )
    if data.get("has_more"):
        lines.append(f"还有更多结果；下一游标：{data.get('next_cursor')}")
    return "\n".join(lines)


def format_detail(data: Any) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, indent=2)


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
    collection_key = "items" if isinstance(data, dict) and isinstance(data.get("items"), list) else (
        "records" if isinstance(data, dict) and isinstance(data.get("records"), list) else None
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
        # A revision is protocol metadata needed for safe follow-up writes. It
        # remains available even when the Agent projects business fields.
        if "revision" in item and "revision" not in projected:
            projected["revision"] = item["revision"]
        projected_items.append(projected)
    result = {**data, collection_key: projected_items, "selected_fields": list(selected)}
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


_TERMINAL_STATES = {
    "succeeded",
    "partially_succeeded",
    "partial_failed",
    "partially_completed",
    "completed",
    "failed",
    "canceled",
    "cancelled",
    "stopped",
    "archived",
    "expired",
    "sent",
    "send_failed",
    "draft_failed",
    "reply_detected",
}


def augment_state_metadata(data: Any, *, command: str) -> Any:
    """Expose a uniform state/action view while preserving existing DTO fields."""

    if not isinstance(data, dict):
        return data
    # A ``status`` field also appears in ordinary analytics and communication
    # records (for example usage ``success``). Only lifecycle resources should
    # receive executable action metadata; otherwise a read-only record looks
    # like a task an Agent can cancel or retry.
    if not capability_stateful(command):
        return data
    return _augment_state_value(data, command=command)


def _augment_state_value(value: Any, *, command: str) -> Any:
    if isinstance(value, list):
        return [_augment_state_value(item, command=command) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _augment_state_value(item, command=command) for key, item in value.items()}
    if isinstance(result.get("status"), str):
        result = _augment_state_item(result, command=command)
    return result


def add_revisions(data: Any) -> Any:
    """Add deterministic optimistic-concurrency tokens to returned objects."""

    if not isinstance(data, dict):
        return data
    result = dict(data)
    if isinstance(result.get("items"), list):
        result["items"] = [
            _with_revision(item) if isinstance(item, dict) else item
            for item in result["items"]
        ]
    elif "revision" not in result and any(key in result for key in ("id", "task_id", "job_id", "plan_id")):
        result = _with_revision(result)
    return result


def _with_revision(value: dict[str, object]) -> dict[str, object]:
    if isinstance(value.get("revision"), str) and value["revision"]:
        return value
    payload = {
        key: item
        for key, item in value.items()
        if key not in {"revision", "updated_at", "created_at"}
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return {**value, "revision": hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]}


def _augment_state_item(item: dict[str, object], *, command: str) -> dict[str, object]:
    result = dict(item)
    status_value = str(result.get("status", "")).lower()
    if not status_value:
        return result
    if "available_actions" not in result:
        actions, blocked_actions = _state_actions_for_item(
            command,
            status_value,
            result,
        )
        result["available_actions"] = actions
        result["blocked_actions"] = blocked_actions
    if "blocked_reason" not in result:
        result["blocked_reason"] = None if result["available_actions"] else "当前状态没有可执行动作"
    return result


def _state_actions_for_item(
    command: str,
    status: str,
    item: dict[str, object],
) -> tuple[list[str], dict[str, str]]:
    """Map known product states to safe actions without claiming completion.

    The backend remains authoritative for business rules.  This projection is
    intentionally conservative: an action is advertised only when its state
    makes it a plausible next operation; every other known action is explained
    in ``blocked_actions`` so an Agent does not have to guess.
    """

    normalized = command.lower()
    if normalized.startswith(("drafts.", "tasks.", "workspaces.")) or any(
        key in item for key in ("task_id", "approved_body_text", "generated_body_text")
    ):
        return _draft_state_actions(status, item)

    if status in {"partial_failed", "partially_completed", "failed"}:
        return ["read", "retry"], {
            "wait": "对象已结束；请读取逐项结果后仅重试失败项",
            "cancel": "对象已结束，不能取消",
            "resume": "部分成功对象不能直接恢复",
        }

    if status in _TERMINAL_STATES:
        actions = ["read"]
        if normalized.endswith((".list", ".items")):
            actions.append("archive")
        return actions, {
            "wait": "对象已进入终态，不能继续等待",
            "cancel": "对象已进入终态，不能取消",
            "pause": "对象已进入终态，不能暂停",
            "resume": "对象已进入终态，不能恢复",
            "retry": "当前终态没有可重试项，需先读取逐项失败原因",
        }
    if status in {"queued", "running", "processing"}:
        actions = ["read", "wait", "cancel"]
        if normalized.startswith("crawler.jobs."):
            actions.append("pause")
        return actions, {
            "resume": "对象尚未暂停，不能恢复",
            "archive": "运行中的对象不能归档",
        }
    if status == "paused":
        return ["read", "resume", "cancel"], {
            "wait": "对象已暂停，请先恢复后再等待",
            "retry": "对象已暂停，不能直接重试",
        }
    if status in {"needs_review", "review_required"}:
        actions = ["read"]
        if normalized.startswith("crawler.jobs."):
            actions.extend(["resume-review", "approve"])
        return actions, {
            "wait": "对象正在等待人工审核，不是后台执行中",
            "cancel": "请使用该资源声明的取消动作",
            "resume": "请先完成审核或使用 resume-review",
        }
    return ["read"], {
        "wait": f"状态 {status} 未声明为可等待状态",
        "cancel": f"状态 {status} 未声明为可取消状态",
        "pause": f"状态 {status} 未声明为可暂停状态",
        "resume": f"状态 {status} 未声明为可恢复状态",
        "retry": f"状态 {status} 未声明为可重试状态",
    }


def _draft_state_actions(status: str, item: dict[str, object]) -> tuple[list[str], dict[str, str]]:
    if status == "generating_draft":
        return ["read", "wait"], {
            "save": "草稿正在生成，不能同时保存",
            "regenerate": "草稿正在生成，不能重复生成",
            "rewrite": "草稿正在生成，不能同时改写",
            "prepare-send": "草稿尚未完成，不能准备发送计划",
        }
    if status in {"discovered", "matched", "draft_failed", "review_required"}:
        actions = ["read", "save", "regenerate", "rewrite"]
        if item.get("approved_body_text") or item.get("approved_body_html"):
            actions.append("prepare-send")
        return actions, {
            "wait": "当前草稿不是后台运行任务",
            "cancel": "草稿取消需要使用对应任务或活动动作",
        }
    if status in {"approved", "scheduled"}:
        return ["read", "prepare-send"], {
            "save": "已批准或排程的草稿需先取消排程再编辑",
            "regenerate": "已批准或排程的草稿需先回到审核状态",
            "rewrite": "已批准或排程的草稿需先回到审核状态",
        }
    if status in {"sending", "sent", "reply_detected", "send_failed", "canceled"}:
        return ["read"], {
            "save": "当前任务已进入发送或结束状态，不能作为草稿修改",
            "regenerate": "当前任务已进入发送或结束状态，不能重新生成",
            "rewrite": "当前任务已进入发送或结束状态，不能改写",
            "prepare-send": "当前任务不处于可准备发送计划的状态",
        }
    return ["read"], {
        "save": f"状态 {status} 不允许保存草稿",
        "regenerate": f"状态 {status} 不允许重新生成",
        "rewrite": f"状态 {status} 不允许 AI 改写",
        "prepare-send": f"状态 {status} 不允许准备发送计划",
    }


def _display_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return ", ".join(
            str(item.get("name", item.get("id", item))) if isinstance(item, dict) else str(item)
            for item in value
        )
    return str(value)


def _without_none(params: dict[str, object] | None) -> dict[str, object]:
    return {
        key: value
        for key, value in (params or {}).items()
        if value is not None
    }


def add_mutation_receipt(
    data: Any,
    *,
    command: str,
    request_id: str,
    json_body: object | None,
    response_headers: dict[str, str] | None = None,
) -> Any:
    """Attach a small, stable receipt to legacy response DTOs.

    The backend keeps returning its established resource shape for backwards
    compatibility.  This additive field gives an Agent a deterministic way to
    identify the operation and affected IDs without requiring every DTO to be
    rewritten at once.  Backend-provided receipt/audit data wins when present.
    """

    if not isinstance(data, dict):
        return data
    if isinstance(data.get("mutation_receipt"), dict):
        return data
    changed_fields = sorted(
        str(key)
        for key in json_body
        if isinstance(json_body, dict) and key not in {"request_id", "idempotency_key"}
    ) if isinstance(json_body, dict) else []
    resource = command.rsplit(".", 1)[0]
    identifier = _first_receipt_identifier(data)
    changed_resources = [
        {
            "type": resource,
            "id": str(identifier) if identifier is not None else None,
            "changed_fields": changed_fields,
            "after": _redact_receipt_value(data),
        },
    ]
    receipt: dict[str, object] = {
        "request_id": request_id,
        "status": "applied",
        "changed_resources": changed_resources,
        "warnings": [],
        "audit_reference": (response_headers or {}).get("x-audit-reference") or request_id,
    }
    header_receipt = (response_headers or {}).get("x-agent-mutation-receipt")
    if header_receipt:
        receipt["backend_receipt_id"] = header_receipt
    return {**data, "mutation_receipt": receipt}


_RECEIPT_IDENTIFIER_KEYS = (
    "id",
    "task_id",
    "job_id",
    "plan_id",
    "professor_id",
    "identity_id",
    "group_id",
    "material_id",
    "template_id",
    "profile_id",
    "candidate_id",
    "campaign_id",
    "item_id",
)


def _first_receipt_identifier(value: Any) -> object | None:
    """Find a stable affected-object ID in common Agent response envelopes."""

    if isinstance(value, dict):
        for key in _RECEIPT_IDENTIFIER_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, (str, int)) and not isinstance(candidate, bool):
                return candidate
        for nested in value.values():
            identifier = _first_receipt_identifier(nested)
            if identifier is not None:
                return identifier
    elif isinstance(value, list):
        for nested in value:
            identifier = _first_receipt_identifier(nested)
            if identifier is not None:
                return identifier
    return None


def export_collection_if_requested(
    data: Any,
    context: CliContext,
) -> tuple[Any, str | None]:
    destination_value = context.output_file
    if not destination_value:
        return data, None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise CliError(
            code="OUTPUT_FILE_REQUIRES_COLLECTION",
            message="--output-file 只能用于返回 items 集合的读取命令。",
            exit_code=2,
        )
    destination = Path(destination_value).expanduser().resolve()
    mode = "w" if context.force_output else "x"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open(mode, encoding="utf-8", newline="\n") as file:
            for item in data["items"]:
                file.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
                file.write("\n")
    except FileExistsError as exc:
        raise CliError(
            code="OUTPUT_EXISTS",
            message=f"输出文件已存在：{destination}；如确实要覆盖请加 --force-output。",
            exit_code=2,
        ) from exc
    except OSError as exc:
        raise CliError(
            code="OUTPUT_WRITE_FAILED",
            message=f"无法写入输出文件：{destination}。",
            exit_code=8,
            details={"reason": type(exc).__name__},
        ) from exc
    summary = {
        "output_file": destination.as_posix(),
        "item_count": len(data["items"]),
        "next_cursor": data.get("next_cursor"),
        "has_more": bool(data.get("has_more")),
        "selected_fields": data.get("selected_fields"),
    }
    return summary, destination.as_posix()


_SECRET_KEY_PARTS = ("password", "api_key", "token", "secret", "credential")


def _redact_receipt_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(part in key.lower() for part in _SECRET_KEY_PARTS)
                else _redact_receipt_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_receipt_value(item) for item in value]
    return value
