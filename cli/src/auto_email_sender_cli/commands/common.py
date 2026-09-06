from __future__ import annotations

import json
import os
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer

from auto_email_sender_cli.capabilities import (
    supports_if_revision,
    supports_pagination,
)
from auto_email_sender_cli.client import AgentApiClient
from auto_email_sender_cli.errors import CliError
from auto_email_sender_cli.output import (
    CliContext,
    OutputFormat,
    emit_error,
    emit_success,
)
from auto_email_sender_cli.result_protocol import (
    MAX_EXPAND_SELECTOR_CHARS,
    MAX_EXPANDED_PATHS,
)

from .collection_filters import (
    _DECLARED_COLLECTION_FIELDS as _DECLARED_COLLECTION_FIELDS,
    _FILTER_OPERATORS as _FILTER_OPERATORS,
    _SERVER_FIELD_PROJECTION_COMMANDS as _SERVER_FIELD_PROJECTION_COMMANDS,
    _SERVER_FILTER_PARAMETERS as _SERVER_FILTER_PARAMETERS,
    _SERVER_OPERATOR_FILTER_PARAMETERS as _SERVER_OPERATOR_FILTER_PARAMETERS,
    _UNICODE_SCRIPTS as _UNICODE_SCRIPTS,
    _annotate_filter_execution as _annotate_filter_execution,
    _contains_unicode_script as _contains_unicode_script,
    _matches_filter as _matches_filter,
    _merge_server_filter_params as _merge_server_filter_params,
    _server_filter_value_is_safe as _server_filter_value_is_safe,
    apply_structured_filter as apply_structured_filter,
    declared_collection_fields as declared_collection_fields,
    project_fields as project_fields,
    server_field_params as server_field_params,
    server_filter_params as server_filter_params,
)
from .exports import (
    _publish_export_temporary as _publish_export_temporary,
    _rename_export_noreplace as _rename_export_noreplace,
    export_collection_if_requested as export_collection_if_requested,
    write_export_bytes as write_export_bytes,
)
from .mutation_receipts import (
    _RECEIPT_IDENTIFIER_KEYS as _RECEIPT_IDENTIFIER_KEYS,
    _SECRET_KEY_PARTS as _SECRET_KEY_PARTS,
    _first_receipt_identifier as _first_receipt_identifier,
    _redact_receipt_value as _redact_receipt_value,
    add_mutation_receipt as add_mutation_receipt,
)
from .state_metadata import (
    _TERMINAL_STATES as _TERMINAL_STATES,
    _augment_state_item as _augment_state_item,
    _augment_state_value as _augment_state_value,
    _collection_revisions_requested as _collection_revisions_requested,
    _draft_state_actions as _draft_state_actions,
    _normalize_existing_action_metadata as _normalize_existing_action_metadata,
    _plan_state_actions as _plan_state_actions,
    _positive_state_identifier as _positive_state_identifier,
    _state_actions_for_item as _state_actions_for_item,
    _supports_present_in_app as _supports_present_in_app,
    _with_revision as _with_revision,
    add_revisions as add_revisions,
    augment_state_metadata as augment_state_metadata,
    compact_collection_action_metadata as compact_collection_action_metadata,
)

HumanFormatter = Callable[[Any], str]
_MAX_FETCH_PAGES = 10_000
_MAX_STDOUT_ALL_ITEMS = 10_000
_MAX_STDOUT_ALL_BYTES = 16 * 1024 * 1024
_MAX_FILTER_SCAN_ITEMS = 1_000_000
_MAX_FILTER_SCAN_BYTES = 512 * 1024 * 1024


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
            details={
                "hint": "先读取对象的 revision，再在写入命令上使用 --if-revision。"
            },
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
    if context.include_revisions and not supports_filter:
        raise CliError(
            code="INCLUDE_REVISIONS_REQUIRES_COLLECTION",
            message="--include-revisions 只能用于集合读取命令。",
            exit_code=2,
        )
    if len(context.expand) > MAX_EXPANDED_PATHS or any(
        len(selector) > MAX_EXPAND_SELECTOR_CHARS for selector in context.expand
    ):
        raise CliError(
            code="INVALID_EXPANSION",
            message=(
                f"--expand 最多重复 {MAX_EXPANDED_PATHS} 次，"
                f"每个选择器最多 {MAX_EXPAND_SELECTOR_CHARS} 个字符。"
            ),
            exit_code=2,
        )
    if "max_items" in context.specified_options and not supports_filter:
        raise CliError(
            code="MAX_ITEMS_REQUIRES_COLLECTION",
            message="--max-items 只能用于集合读取命令。",
            exit_code=2,
        )
    if not supports_projection and (
        "projection" in context.specified_options
        or context.expand
        or "max_output_bytes" in context.specified_options
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
        request_params = _cap_request_page_size(request_params, context)
        pushed_filter_params: dict[str, object] = {}
        if fields:
            # Validate against the locally published DTO contract before any
            # runtime discovery or network request. A projected backend
            # response may be empty and cannot be used to infer field names.
            project_fields({"items": []}, fields, command=command)
            request_params = _merge_server_filter_params(
                request_params,
                server_field_params(
                    fields,
                    expression=context.filter_expression,
                    command=command,
                    include_revisions=_collection_revisions_requested(context, fields),
                ),
            )
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
            pushed_filter_params = server_filter_params(
                context.filter_expression,
                command=command,
            )
            request_params = _merge_server_filter_params(
                request_params, pushed_filter_params
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
            data = _annotate_filter_execution(
                data,
                expression=context.filter_expression,
                server_params=pushed_filter_params,
            )
            emit_success(
                context,
                command=command,
                data=data,
                human_text=f"已将完整集合逐页导出到：{exported_file}",
                app_version=client.descriptor.app_version,
                request_id=getattr(client, "last_request_id", None),
                continuation_input=_without_none(params),
            )
            return data
        streamed_filter = bool(context.filter_expression) and fetch_everything
        data = (
            fetch_filtered_pages(
                client,
                path,
                params=request_params,
                expression=context.filter_expression or "",
                command=command,
                max_items=context.max_items,
                max_bytes=_MAX_STDOUT_ALL_BYTES,
            )
            if streamed_filter
            else fetch_all_pages(
                client,
                path,
                params=request_params,
                max_items=min(context.max_items, _MAX_STDOUT_ALL_ITEMS),
                max_bytes=_MAX_STDOUT_ALL_BYTES,
            )
            if fetch_everything
            else client.request("GET", path, params=request_params)
        )
        data = normalize_collection_response(data, command=command)
        if not streamed_filter:
            data = apply_structured_filter(
                data, context.filter_expression, command=command
            )
        data = _annotate_filter_execution(
            data,
            expression=context.filter_expression,
            server_params=pushed_filter_params,
        )
        # Compute the optimistic-concurrency token from the complete record
        # before applying a caller's field projection.  Otherwise
        # ``--fields id,name`` would produce a different revision from the
        # server's full object and a subsequent ``--if-revision`` write would
        # be rejected even when nobody changed the record.
        include_collection_revisions = _collection_revisions_requested(context, fields)
        data = augment_state_metadata(
            add_revisions(data, include_collection=include_collection_revisions),
            command=command,
        )
        data = compact_collection_action_metadata(data, command=command)
        data = project_fields(data, fields, command=command)
        data = annotate_collection_limit(
            data,
            params=request_params,
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
            app_version=client.descriptor.app_version,
            request_id=getattr(client, "last_request_id", None),
            continuation_input=_without_none(params),
        )
        return data
    except CliError as error:
        emit_error(context, command=command, error=error)
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
            app_version=client.descriptor.app_version,
            request_id=getattr(client, "last_request_id", None) or request_id,
        )
        return data
    except CliError as error:
        emit_error(context, command=command, error=error)
        raise typer.Exit(error.exit_code) from error


def fetch_all_pages(
    client: AgentApiClient,
    path: str,
    *,
    params: dict[str, object] | None = None,
    page_consumer: Callable[[dict[str, object]], int] | None = None,
    max_items: int | None = None,
    max_bytes: int | None = None,
    max_scanned_items: int | None = None,
    max_scanned_bytes: int | None = None,
) -> dict[str, object]:
    request_params = _without_none(params)
    page_mode = "page" in request_params or "page_size" in request_params
    offset_mode = (
        "offset" in request_params and not page_mode and "cursor" not in request_params
    )
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
    cursor: str | None = str(initial_cursor) if initial_cursor is not None else None
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
    scanned_item_count = 0
    scanned_bytes = 0
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
        payload = normalize_collection_response(
            client.request("GET", path, params=request_params)
        )
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
        encoded_item_sizes: list[int] | None = None
        if page_consumer is None or max_scanned_bytes is not None:
            encoded_item_sizes = [
                len(
                    json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode(
                        "utf-8"
                    )
                )
                for item in payload["items"]
            ]
        scanned_item_count += len(payload["items"])
        if encoded_item_sizes is not None:
            scanned_bytes += sum(encoded_item_sizes)
        if (
            max_scanned_items is not None and scanned_item_count > max_scanned_items
        ) or (max_scanned_bytes is not None and scanned_bytes > max_scanned_bytes):
            raise CliError(
                code="FILTER_SCAN_LIMIT_EXCEEDED",
                message="本地筛选扫描量超过安全上限，已停止继续读取。",
                exit_code=8,
                details={
                    "max_scanned_items": max_scanned_items,
                    "max_scanned_bytes": max_scanned_bytes,
                    "observed_items": scanned_item_count,
                    "observed_bytes": scanned_bytes,
                },
                suggested_command="优先使用可下推的等值筛选，或缩小查询范围后重试",
            )
        if page_consumer is not None:
            consumed = page_consumer(payload)
            if (
                not isinstance(consumed, int)
                or isinstance(consumed, bool)
                or consumed < 0
            ):
                raise CliError(
                    code="INVALID_PAGE_CONSUMER",
                    message="分页消费器返回了无效的条目计数。",
                    exit_code=8,
                )
            consumed_item_count += consumed
        else:
            assert encoded_item_sizes is not None
            for item, item_bytes in zip(
                payload["items"], encoded_item_sizes, strict=True
            ):
                accumulated_bytes += item_bytes
                if (max_items is not None and len(items) + 1 > max_items) or (
                    max_bytes is not None and accumulated_bytes > max_bytes
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
        result["scanned_item_count"] = scanned_item_count
        if max_scanned_bytes is not None:
            result["scanned_bytes"] = scanned_bytes
    if had_records_alias:
        # Keep the legacy alias useful after an all-pages fetch instead of
        # returning only the first page's records array.
        result["records"] = items
    if pagination_mode is not None:
        result["pagination_mode"] = pagination_mode
    if total is not None:
        result["total"] = total
    return result


def fetch_filtered_pages(
    client: AgentApiClient,
    path: str,
    *,
    params: dict[str, object] | None,
    expression: str,
    command: str,
    max_items: int,
    max_bytes: int,
) -> dict[str, object]:
    """Filter one page at a time and retain only bounded matching records."""

    filtered_items: list[object] = []
    filtered_bytes = 0
    parsed_filter: object | None = None
    usage_summary = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "record_count": 0,
    }

    def consume_page(page: dict[str, object]) -> int:
        nonlocal filtered_bytes, parsed_filter
        transformed = apply_structured_filter(page, expression, command=command)
        parsed_filter = transformed.get("filter")
        page_items = transformed.get("items")
        if not isinstance(page_items, list):
            raise CliError(
                code="INVALID_API_RESPONSE",
                message="本地服务返回了无法识别的分页结果。",
                exit_code=8,
            )
        for item in page_items:
            filtered_bytes += len(
                json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                ),
            )
            if len(filtered_items) + 1 > max_items or filtered_bytes > max_bytes:
                raise CliError(
                    code="RESULT_TOO_LARGE",
                    message="筛选结果超过 stdout 安全上限；请使用 --output-file <path>.jsonl 逐页导出。",
                    exit_code=8,
                    details={
                        "max_items": max_items,
                        "max_bytes": max_bytes,
                        "observed_items": len(filtered_items) + 1,
                        "observed_bytes": filtered_bytes,
                    },
                    suggested_command="在原命令的根选项中添加 --output-file <path>.jsonl",
                )
            filtered_items.append(item)
        if command == "usage.records":
            page_summary = transformed.get("summary")
            if isinstance(page_summary, dict):
                for key in usage_summary:
                    value = page_summary.get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        usage_summary[key] += value
        return len(page_items)

    pagination = fetch_all_pages(
        client,
        path,
        params=params,
        page_consumer=consume_page,
        max_scanned_items=_MAX_FILTER_SCAN_ITEMS,
        max_scanned_bytes=_MAX_FILTER_SCAN_BYTES,
    )
    result = {
        **pagination,
        "items": filtered_items,
        "next_cursor": None,
        "has_more": False,
        "fetched_all": True,
        "filter": parsed_filter,
        "filtered_count": len(filtered_items),
    }
    if isinstance(pagination.get("records"), list):
        result["records"] = filtered_items
    result["summary"] = (
        usage_summary
        if command == "usage.records"
        else {"record_count": len(filtered_items)}
    )
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
    try:
        page_size = int(pagination.get("page_size", len(records)))
    except (TypeError, ValueError):
        page_size = len(records)
    total_records = pagination.get("total_records")
    return {
        **data,
        "items": records,
        "next_cursor": str(page + 1) if has_more else None,
        "has_more": has_more,
        "pagination_mode": "page",
        "offset": max(0, (page - 1) * page_size),
        "limit": page_size,
        **(
            {"total": total_records}
            if isinstance(total_records, int) and not isinstance(total_records, bool)
            else {}
        ),
    }


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
        limit = (
            requested
            if isinstance(requested, int) and not isinstance(requested, bool)
            else len(data["items"])
        )
    return {**data, "limit": limit}


def _display_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return ", ".join(
            str(item.get("name", item.get("id", item)))
            if isinstance(item, dict)
            else str(item)
            for item in value
        )
    return str(value)


def _without_none(params: dict[str, object] | None) -> dict[str, object]:
    return {key: value for key, value in (params or {}).items() if value is not None}


def _cap_request_page_size(
    params: dict[str, object],
    context: CliContext,
) -> dict[str, object]:
    """Push an explicit stdout item cap into the current backend page."""

    if "max_items" not in context.specified_options:
        return params
    result = dict(params)
    parameter = "page_size" if "page_size" in result or "page" in result else "limit"
    current = result.get(parameter)
    if isinstance(current, int) and not isinstance(current, bool):
        result[parameter] = min(current, context.max_items)
    else:
        result[parameter] = context.max_items
    return result


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
                    add_revisions(
                        transformed,
                        include_collection=_collection_revisions_requested(
                            context, fields
                        ),
                    ),
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
                            if (
                                isinstance(key, str)
                                and isinstance(value, int)
                                and not isinstance(value, bool)
                            ):
                                filtered_summary[key] = (
                                    filtered_summary.get(key, 0) + value
                                )
                for item in page_items:
                    output.write(
                        json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                    )
                    output.write("\n")
                item_count += len(page_items)
                return len(page_items)

            pagination = fetch_all_pages(
                client,
                path,
                params=params,
                page_consumer=consume_page,
                max_scanned_items=(
                    _MAX_FILTER_SCAN_ITEMS if context.filter_expression else None
                ),
                max_scanned_bytes=(
                    _MAX_FILTER_SCAN_BYTES if context.filter_expression else None
                ),
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
