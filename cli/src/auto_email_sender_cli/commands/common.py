from __future__ import annotations

from collections.abc import Callable
import ctypes
import errno
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

import typer

from auto_email_sender_cli.action_links import resolve_action_links
from auto_email_sender_cli.client import AgentApiClient
from auto_email_sender_cli.capabilities import (
    collection_filter_fields,
    collection_filter_operators,
    supports_dynamic_action_links,
    supports_if_revision,
    supports_pagination,
)
from auto_email_sender_cli.errors import CliError
from auto_email_sender_cli.output import CliContext, OutputFormat, emit_error, emit_success


HumanFormatter = Callable[[Any], str]
_MAX_FETCH_PAGES = 10_000
_MAX_STDOUT_ALL_ITEMS = 10_000
_MAX_STDOUT_ALL_BYTES = 16 * 1024 * 1024

_SERVER_FILTER_PARAMETERS: dict[str, dict[str, str]] = {
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
    "diagnostics.logs": {
        "level": "level",
        "category": "category",
        "event_name": "event_name",
        "request_id": "request_id",
        "entity_type": "entity_type",
        "entity_id": "entity_id",
    },
    "usage.records": {
        "model_name": "model_name",
    },
}


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
    supports_projection: bool = True,
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
    if not supports_projection and (
        "projection" in context.specified_options or context.expand
    ):
        raise CliError(
            code="PROJECTION_NOT_SUPPORTED",
            message="当前命令不返回可展开的业务内容，不能使用 --projection 或 --expand。",
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
        request_params = _without_none(params)
        if context.filter_expression:
            # Filter syntax and the command's field/operator whitelist are
            # fully local contracts. Validate them before runtime discovery or
            # network I/O so an invalid invocation never depends on whether the
            # desktop app happens to be running.
            apply_structured_filter(
                {"items": []},
                context.filter_expression,
                command=command,
            )
            request_params = _merge_server_filter_params(
                request_params,
                server_filter_params(context.filter_expression, command=command),
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
        if context.output_file and fetch_everything:
            data, exported_file = stream_collection_pages_to_file(
                client,
                path,
                params=request_params,
                context=context,
                command=command,
                fields=fields,
            )
            emit_success(
                context,
                command=command,
                data=data,
                human_text=f"已将完整集合逐页导出到：{exported_file}",
                guide_topic=guide_topic,
                app_version=client.descriptor.app_version,
                request_id=getattr(client, "last_request_id", None),
                continuation_input=_without_none(params),
            )
            return data
        data = (
            fetch_all_pages(
                client,
                path,
                params=request_params,
                max_items=_MAX_STDOUT_ALL_ITEMS,
                max_bytes=_MAX_STDOUT_ALL_BYTES,
            )
            if fetch_everything
            else client.request("GET", path, params=request_params)
        )
        data = normalize_collection_response(data, command=command)
        data = apply_structured_filter(data, context.filter_expression, command=command)
        # Compute the optimistic-concurrency token from the complete record
        # before applying a caller's field projection.  Otherwise
        # ``--fields id,name`` would produce a different revision from the
        # server's full object and a subsequent ``--if-revision`` write would
        # be rejected even when nobody changed the record.
        data = augment_state_metadata(add_revisions(data), command=command)
        data = compact_collection_action_metadata(data, command=command)
        data = project_fields(data, fields, command=command)
        data = annotate_collection_limit(
            data,
            params=params,
            fetched_all=fetch_everything,
        )
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
            continuation_input=_without_none(params),
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
        request_id = (
            cli_context(ctx).request_id or f"cli_{secrets.token_urlsafe(24)}"
            if use_idempotency_key
            else None
        )
        if request_id is not None:
            # Persist the final generated identifier before any network I/O so
            # every error path can return the exact key required for a safe
            # retry, not only successful mutation receipts.
            context.request_id = request_id
        client = AgentApiClient(timeout=timeout)
        data = client.request(
            method,
            path,
            params=_without_none(params),
            json_body=json_body,
            idempotency_key=request_id,
            if_revision=cli_context(ctx).if_revision,
        )
        data = project_fields(
            augment_state_metadata(add_revisions(data), command=command),
            fields,
            command=command,
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
    page_consumer: Callable[[dict[str, object]], int] | None = None,
    max_items: int | None = None,
    max_bytes: int | None = None,
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
    consumed_item_count = 0
    accumulated_bytes = 0
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
        if page_consumer is not None:
            consumed_item_count += page_consumer(payload)
        else:
            for item in payload["items"]:
                accumulated_bytes += len(
                    json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                )
                if (
                    (max_items is not None and len(items) + 1 > max_items)
                    or (max_bytes is not None and accumulated_bytes > max_bytes)
                ):
                    raise CliError(
                        code="RESULT_TOO_LARGE",
                        message=(
                            "完整集合超过 stdout 安全上限；请使用 "
                            "--output-file <path>.jsonl 逐页导出。"
                        ),
                        exit_code=8,
                        details={
                            "max_items": max_items,
                            "max_bytes": max_bytes,
                            "observed_items": len(items) + 1,
                            "observed_bytes": accumulated_bytes,
                        },
                        suggested_command=(
                            "在原命令的根选项中添加 --output-file <path>.jsonl"
                        ),
                    )
                items.append(item)
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
        "page_count": page_count,
    }
    if page_consumer is not None:
        result["item_count"] = consumed_item_count
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
        parameter = mapping.get(field) if isinstance(field, str) else None
        if parameter is None:
            continue
        if isinstance(condition, dict):
            allowed_operator = (
                "contains"
                if command == "crawler.jobs.list" and field == "effective_models"
                else "eq"
            )
            if set(condition) != {allowed_operator}:
                continue
            expected = condition[allowed_operator]
        else:
            expected = condition
        if expected is None or isinstance(expected, dict | list):
            continue
        if not _server_filter_value_is_safe(command, field, expected):
            continue
        result[parameter] = expected
    return result


def _server_filter_value_is_safe(command: str, field: str, value: object) -> bool:
    if field in {"identity_id", "llm_profile_id", "professor_id"}:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0
    if field in {"has_sent", "has_reply"}:
        return isinstance(value, bool)
    if command == "communications.messages.list" and field == "direction":
        return value in {"sent", "received", "draft"}
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
        # These are protocol metadata needed for safe follow-up writes and
        # executable state transitions. They remain available even when the
        # Agent projects business fields down to a compact view.
        for metadata_field in (
            "revision",
            "available_actions",
            "blocked_actions",
            "blocked_reason",
        ):
            if metadata_field in item and metadata_field not in projected:
                projected[metadata_field] = item[metadata_field]
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
    if not supports_dynamic_action_links(command):
        return data
    return _augment_state_value(data, command=command)


def compact_collection_action_metadata(data: Any, *, command: str) -> Any:
    """Group repeated lifecycle actions on top-level resource lists."""

    if not command.endswith(".list") or not isinstance(data, dict):
        return data
    items = data.get("items")
    if not isinstance(items, list):
        return data

    grouped: dict[str, dict[str, object]] = {}
    compact_items: list[object] = []
    for item in items:
        if not isinstance(item, dict):
            compact_items.append(item)
            continue
        resource_id = item.get("id")
        status = item.get("status")
        actions = item.get("available_actions")
        if (
            isinstance(resource_id, bool)
            or not isinstance(resource_id, str | int)
            or not isinstance(status, str)
            or not isinstance(actions, list)
        ):
            compact_items.append(item)
            continue
        compact_actions = [
            {key: value for key, value in action.items() if key != "arguments"}
            for action in actions
            if isinstance(action, dict)
        ]
        blocked_actions = (
            item.get("blocked_actions")
            if isinstance(item.get("blocked_actions"), dict)
            else {}
        )
        signature = json.dumps(
            {
                "status": status,
                "available_actions": compact_actions,
                "blocked_actions": blocked_actions,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        group = grouped.setdefault(
            signature,
            {
                "status": status,
                "ids": [],
                "available_actions": compact_actions,
                **({"blocked_actions": blocked_actions} if blocked_actions else {}),
            },
        )
        group_ids = group["ids"]
        assert isinstance(group_ids, list)
        group_ids.append(resource_id)
        compact_items.append(
            {
                key: value
                for key, value in item.items()
                if key not in {"available_actions", "blocked_actions", "blocked_reason"}
            },
        )

    if not grouped:
        return data
    action_groups = sorted(
        grouped.values(),
        key=lambda group: (str(group["status"]), str(group["ids"][0])),
    )
    return {**data, "items": compact_items, "action_groups": action_groups}


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
    existing_actions = result.get("available_actions")
    existing_blocked = result.get("blocked_actions")
    if isinstance(existing_actions, list):
        actions, blocked_actions = _normalize_existing_action_metadata(
            existing_actions,
            existing_blocked,
        )
    else:
        actions, blocked_actions = _state_actions_for_item(
            command,
            status_value,
            result,
        )
    action_links, resolved_blocked_actions = resolve_action_links(
        command,
        result,
        actions=actions,
        blocked_actions=blocked_actions,
    )
    result["available_actions"] = action_links
    result["blocked_actions"] = resolved_blocked_actions
    if "blocked_reason" not in result:
        result["blocked_reason"] = (
            None if result["available_actions"] else "当前状态没有可执行动作"
        )
    return result


def _normalize_existing_action_metadata(
    actions: list[object],
    blocked_actions: object,
) -> tuple[list[str], dict[str, str]]:
    """Accept legacy backend action tokens without trusting their arguments.

    Some older desktop versions may still return ``[{action, allowed}]``. The
    CLI uses only their declarative action names, then rebuilds target commands
    and identifiers from its own manifest and the current structured DTO.
    """

    allowed: list[str] = []
    blocked: dict[str, str] = {}
    for raw_action in actions:
        if isinstance(raw_action, str):
            allowed.append(raw_action)
            continue
        if not isinstance(raw_action, dict):
            continue
        action = raw_action.get("action")
        if not isinstance(action, str):
            continue
        if raw_action.get("allowed", True):
            allowed.append(action)
        else:
            blocked[action] = str(raw_action.get("reason") or "当前状态不允许该动作。")
    if isinstance(blocked_actions, dict):
        for action, reason in blocked_actions.items():
            if isinstance(action, str):
                blocked[action] = str(reason or "当前状态不允许该动作。")
    return allowed, blocked


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
    if "plan_id" in item:
        return _plan_state_actions(status)
    if normalized.startswith(("drafts.", "tasks.", "workspaces.")) or any(
        key in item for key in ("task_id", "approved_body_text", "generated_body_text")
    ):
        return _draft_state_actions(status, item)

    if status in {"partial_failed", "partially_completed", "failed"}:
        actions = ["read", "retry"]
        if normalized.startswith("crawler.jobs.") and status == "partially_completed":
            actions.append("enrich")
        return actions, {
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
            actions.extend(["resume-review", "approve", "enrich"])
        if normalized.startswith("campaigns."):
            actions.append("prepare-send")
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


def _plan_state_actions(status: str) -> tuple[list[str], dict[str, str]]:
    if status in {"pending", "ready", "awaiting_confirmation", "confirmed"}:
        return ["read", "execute", "cancel"], {
            "retry": "计划尚未进入可重试终态",
        }
    if status in {"executed", "completed", "canceled", "cancelled", "expired", "failed"}:
        return ["read"], {
            "execute": "计划已进入终态，不能再次执行",
            "cancel": "计划已进入终态，不能取消",
        }
    return ["read"], {
        "execute": f"计划状态 {status} 未声明为可执行状态",
        "cancel": f"计划状态 {status} 未声明为可取消状态",
    }


def annotate_collection_limit(
    data: Any,
    *,
    params: dict[str, object] | None,
    fetched_all: bool,
) -> Any:
    """Attach the effective page size before the shared result protocol runs."""

    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return data
    if isinstance(data.get("limit"), int):
        return data
    if fetched_all or bool(data.get("fetched_all")):
        limit = len(data["items"])
    else:
        requested = (params or {}).get("limit", (params or {}).get("page_size"))
        limit = requested if isinstance(requested, int) and not isinstance(requested, bool) else len(data["items"])
    return {**data, "limit": limit}


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
    response_status = (response_headers or {}).get("x-agent-mutation-status")
    mutation_status = (
        response_status
        if response_status in {"pending", "applied", "replayed"}
        else "applied"
    )
    receipt: dict[str, object] = {
        "request_id": request_id,
        "status": mutation_status,
        "changed_resources": changed_resources,
        "warnings": [],
        "audit_reference": (response_headers or {}).get("x-audit-reference") or request_id,
    }
    header_receipt = (response_headers or {}).get("x-agent-mutation-receipt")
    if header_receipt:
        receipt["backend_receipt_id"] = header_receipt
    header_command = (response_headers or {}).get("x-agent-mutation-command")
    if header_command:
        receipt["backend_command"] = header_command
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


def stream_collection_pages_to_file(
    client: AgentApiClient,
    path: str,
    *,
    params: dict[str, object] | None,
    context: CliContext,
    command: str,
    fields: str | None,
) -> tuple[dict[str, object], str]:
    """Transform and write each page without retaining the complete collection."""

    destination_value = context.output_file
    if not destination_value:
        raise CliError(
            code="OUTPUT_FILE_REQUIRES_COLLECTION",
            message="缺少 --output-file 导出路径。",
            exit_code=2,
        )
    destination = Path(destination_value).expanduser().resolve()
    temporary = destination.with_name(
        f".{destination.name}.{secrets.token_hex(8)}.tmp",
    )
    if destination.exists() and not context.force_output:
        raise CliError(
            code="OUTPUT_EXISTS",
            message=f"输出文件已存在：{destination}；如确实要覆盖请加 --force-output。",
            exit_code=2,
        )

    item_count = 0
    selected_fields: list[str] | None = None
    filtered_summary: dict[str, int] = {}
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(temporary_fd, "w", encoding="utf-8", newline="\n") as output:

            def consume_page(page: dict[str, object]) -> int:
                nonlocal item_count, selected_fields
                transformed = apply_structured_filter(
                    page,
                    context.filter_expression,
                    command=command,
                )
                transformed = augment_state_metadata(
                    add_revisions(transformed),
                    command=command,
                )
                transformed = project_fields(transformed, fields, command=command)
                page_items = transformed.get("items")
                if not isinstance(page_items, list):
                    raise CliError(
                        code="INVALID_API_RESPONSE",
                        message="本地服务返回了无法识别的分页结果。",
                        exit_code=8,
                    )
                page_selected_fields = transformed.get("selected_fields")
                if isinstance(page_selected_fields, list):
                    selected_fields = [
                        str(field)
                        for field in page_selected_fields
                        if isinstance(field, str)
                    ]
                if context.filter_expression and command == "usage.records":
                    summary = transformed.get("summary")
                    if isinstance(summary, dict):
                        for key, value in summary.items():
                            if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool):
                                filtered_summary[key] = filtered_summary.get(key, 0) + value
                for item in page_items:
                    output.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
                    output.write("\n")
                item_count += len(page_items)
                return len(page_items)

            pagination = fetch_all_pages(
                client,
                path,
                params=params,
                page_consumer=consume_page,
            )

        _publish_export_temporary(temporary, destination, force=context.force_output)
    except FileExistsError as exc:
        raise CliError(
            code="OUTPUT_EXISTS",
            message=f"输出文件已存在：{destination}；如确实要覆盖请加 --force-output。",
            exit_code=2,
        ) from exc
    except CliError:
        raise
    except OSError as exc:
        raise CliError(
            code="OUTPUT_WRITE_FAILED",
            message=f"无法写入输出文件：{destination}。",
            exit_code=8,
            details={"reason": type(exc).__name__},
        ) from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass

    if context.filter_expression:
        # Only usage.records defines a summary that the local filter can
        # safely recompute and add across pages. Other backend summaries
        # describe the unfiltered source set, so replace them with an exact
        # filtered record count instead of publishing misleading totals.
        summary_value: object = (
            filtered_summary
            if command == "usage.records" and filtered_summary
            else {"record_count": item_count}
        )
    else:
        summary_value = pagination.get("summary")
    result: dict[str, object] = {
        "output_file": destination.as_posix(),
        "item_count": item_count,
        "source_total": pagination.get("total"),
        "page_count": pagination.get("page_count"),
        "next_cursor": None,
        "has_more": False,
        "fetched_all": True,
        "selected_fields": selected_fields,
        "filter_applied": bool(context.filter_expression),
    }
    if summary_value is not None:
        result["summary"] = summary_value
    return result, destination.as_posix()


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
    temporary: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{secrets.token_hex(8)}.tmp",
        )
        temporary_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(temporary_fd, "w", encoding="utf-8", newline="\n") as file:
            for item in data["items"]:
                file.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
                file.write("\n")
        _publish_export_temporary(temporary, destination, force=context.force_output)
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
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    summary = {
        "output_file": destination.as_posix(),
        "item_count": len(data["items"]),
        "next_cursor": data.get("next_cursor"),
        "has_more": bool(data.get("has_more")),
        "selected_fields": data.get("selected_fields"),
    }
    return summary, destination.as_posix()


def _publish_export_temporary(
    temporary: Path,
    destination: Path,
    *,
    force: bool,
) -> None:
    """Publish a mode-0600 export atomically, with a cross-platform fallback.

    ``os.link`` is the simplest no-overwrite primitive on POSIX, but it is
    unavailable on some Windows, network, and sync-backed filesystems.  The
    fallback uses each supported platform's atomic no-replace rename instead
    of exposing an empty reservation or opening a check-then-replace race.
    """

    if force:
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
        return
    try:
        os.link(temporary, destination)
    except (AttributeError, NotImplementedError, OSError):
        _rename_export_noreplace(temporary, destination)
    else:
        temporary.unlink()
    os.chmod(destination, 0o600)


def _rename_export_noreplace(temporary: Path, destination: Path) -> None:
    """Atomically rename without overwriting an existing destination."""

    if os.name == "nt":
        # Windows' os.rename fails with FileExistsError when dst exists.
        os.rename(temporary, destination)
        return

    source_bytes = os.fsencode(temporary)
    destination_bytes = os.fsencode(destination)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename_exclusive = getattr(libc, "renamex_np", None)
        if rename_exclusive is None:
            raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
        rename_exclusive.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(source_bytes, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        rename_exclusive = getattr(libc, "renameat2", None)
        if rename_exclusive is None:
            raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
        rename_exclusive.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(
            -100,  # AT_FDCWD
            source_bytes,
            -100,
            destination_bytes,
            0x00000001,  # RENAME_NOREPLACE
        )
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")

    if result != 0:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(error_number, os.strerror(error_number), destination)


def write_export_bytes(destination: Path, content: bytes, *, force: bool) -> Path:
    """Write a non-collection export with the same secure publish contract."""

    resolved = destination.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(
        f".{resolved.name}.{secrets.token_hex(8)}.tmp",
    )
    try:
        temporary_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(temporary_fd, "wb") as output:
            output.write(content)
        _publish_export_temporary(temporary, resolved, force=force)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return resolved


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
