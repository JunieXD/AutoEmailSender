"""Compact, explicit result shaping for Agent-facing CLI output.

The Agent API deliberately returns rich records.  Printing every body, log,
and crawler evidence payload into a model context is both expensive and unsafe
to treat as instructions.  This module keeps the original command result
shape, but adds a small protocol envelope inside ``data`` that says exactly
what was summarized or remains to be fetched.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any


RESULT_PROTOCOL_VERSION = "2"
RESULT_PROTOCOL_FIELDS = frozenset(
    {
        "projection",
        "limit",
        "continuation",
        "truncated",
        "omitted_paths",
        "omitted_paths_total",
    },
)

_NON_BUSINESS_COMMANDS = frozenset(
    {"version", "status", "doctor", "guide", "capabilities", "describe", "invoke"},
)
_TEXT_FIELDS = frozenset(
    {
        "body",
        "body_text",
        "body_html",
        "content",
        "content_html",
        "generated_body_text",
        "generated_body_html",
        "approved_body_text",
        "approved_body_html",
        "generated_content_text",
        "generated_content_html",
        "extracted_text",
        "text_excerpt",
        "message",
        "last_error",
        "error_message",
        "failure_summary",
        "raw",
    },
)
_STRUCTURED_FIELDS = frozenset(
    {
        "after",
        "blocked_actions",
        "evidence",
        "history",
        "logs",
        "messages",
        "metadata",
        "records",
        "result",
    },
)
_MAX_DETAIL_PREVIEW_CHARS = 480
_MAX_COLLECTION_PREVIEW_CHARS = 120
# A collection that is not an explicitly paged top-level ``items`` page is
# otherwise unbounded (for example ``summary.items`` in a change plan). Keep
# the default representation bounded even when every item is a short scalar.
_MAX_INLINE_NESTED_ARRAY_ITEMS = 50
_MAX_INLINE_PAGE_ITEMS = 500
_MAX_INLINE_STRUCTURAL_ITEMS = 100
_MAX_OMITTED_PATHS = 32
_MAX_INLINE_NESTED_BYTES = 8 * 1024
_MAX_INLINE_ITEM_BYTES = 4 * 1024
_MAX_DEFAULT_RESULT_BYTES = 64 * 1024
_DEFAULT_RESULT_TARGET_BYTES = 60 * 1024
_SMALL_STRUCTURAL_ARRAY_FIELDS = frozenset(
    {
        "selected_fields",
        "warnings",
        "required_input",
        "untrusted_fields",
        "external_services",
        "filter_fields",
        "filter_operators",
        "terminal_states",
        "key_fields",
        "available_actions",
    },
)


def is_business_result(command: str) -> bool:
    return command.strip().lower() not in _NON_BUSINESS_COMMANDS


def prepare_result_data(
    data: Any,
    *,
    command: str,
    projection: str = "summary",
    expanded_paths: Sequence[str] = (),
    continuation_input: Mapping[str, object] | None = None,
    invoke_input: Mapping[str, object] | None = None,
) -> Any:
    """Add the result protocol and summarize content unless explicitly expanded.

    ``projection=full`` is an explicit opt-in for complete rich content.
    ``--expand`` may instead name a field (``body_text``) or JSON pointer
    (``/messages/0/body_text``) so one focused value can be expanded without
    making a whole response large again.
    """

    if not is_business_result(command) or not isinstance(data, dict):
        return data
    normalized_projection = "full" if projection == "full" else "summary"
    selectors = tuple(
        selector.strip()
        for selector in expanded_paths
        if isinstance(selector, str) and selector.strip()
    )
    preserve_collection_items = frozenset(
        {"/items"}
        if isinstance(data.get("items"), list)
        and any(key in data for key in ("next_cursor", "has_more", "pagination_mode"))
        else set()
    )
    projection_input = _drop_redundant_collection_aliases(
        data,
        projection=normalized_projection,
    )
    summarized, omitted_paths = _summarize_value(
        projection_input,
        path="",
        projection=normalized_projection,
        selectors=selectors,
        preserve_collection_items=preserve_collection_items,
    )
    assert isinstance(summarized, dict)
    summarized, budget_omitted_paths, budget_compacted = _enforce_result_budget(
        summarized,
        projection=normalized_projection,
        selectors=selectors,
    )
    omitted_paths.extend(budget_omitted_paths)
    limit = _result_limit(summarized)
    continuation = _continuation(
        command,
        summarized,
        limit=limit,
        continuation_input=continuation_input,
        invoke_input=invoke_input,
    )
    if continuation is not None:
        omitted_paths.append("/items/*")
    omitted_paths, omitted_paths_total = _compact_omitted_paths(omitted_paths)
    result: dict[str, object] = dict(summarized)
    if omitted_paths or selectors or normalized_projection == "full":
        result["projection"] = {
            "version": RESULT_PROTOCOL_VERSION,
            "mode": normalized_projection,
            "expanded_paths": list(selectors),
        }
        if budget_compacted:
            result["projection"]["budget_bytes"] = _MAX_DEFAULT_RESULT_BYTES
            result["projection"]["budget_compacted"] = True
    if limit is not None:
        result["limit"] = limit
    if continuation is not None:
        result["continuation"] = continuation
    if omitted_paths:
        result["truncated"] = True
        result["omitted_paths"] = omitted_paths
        if omitted_paths_total > len(omitted_paths):
            result["omitted_paths_total"] = omitted_paths_total
    return result


def result_protocol_metadata(data: Any) -> dict[str, object] | None:
    """Extract metadata that JSONL cannot carry beside flattened items."""

    if not isinstance(data, dict):
        return None
    metadata = {
        key: data[key]
        for key in (
            "projection",
            "limit",
            "continuation",
            "truncated",
            "omitted_paths",
            "omitted_paths_total",
            "action_groups",
        )
        if key in data
    }
    return metadata or None


def _summarize_value(
    value: Any,
    *,
    path: str,
    projection: str,
    selectors: tuple[str, ...],
    preserve_collection_items: frozenset[str],
) -> tuple[Any, list[str]]:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        omitted: list[str] = []
        for key, nested in value.items():
            key_text = str(key)
            nested_path = _join_path(path, key_text)
            if (
                projection != "full"
                and key_text == "after"
                and "/mutation_receipt/changed_resources/" in nested_path
            ):
                omitted.append(nested_path)
                continue
            if _should_summarize(key_text, nested, projection, selectors, nested_path):
                result[key_text] = _summary(nested, path=nested_path)
                omitted.append(nested_path)
                continue
            compact, nested_omitted = _summarize_value(
                nested,
                path=nested_path,
                projection=projection,
                selectors=selectors,
                preserve_collection_items=preserve_collection_items,
            )
            result[key_text] = compact
            omitted.extend(nested_omitted)
        return result, omitted
    if isinstance(value, list):
        field_name = path.rsplit("/", 1)[-1]
        preserve_small_structural_array = (
            field_name in _SMALL_STRUCTURAL_ARRAY_FIELDS
            and len(value) <= _MAX_INLINE_STRUCTURAL_ITEMS
        )
        if (
            projection != "full"
            and path not in preserve_collection_items
            and not preserve_small_structural_array
            and _json_size(value) > _MAX_INLINE_NESTED_BYTES
            and not _is_expanded(field_name, path, selectors)
            and not _selector_targets_descendant(path, value, selectors)
        ):
            return _summary(value, path=path), [path]
        if (
            projection != "full"
            and path not in preserve_collection_items
            and not preserve_small_structural_array
            and len(value) > _MAX_INLINE_NESTED_ARRAY_ITEMS
            and not _is_expanded(field_name, path, selectors)
            and not _selector_targets_descendant(path, value, selectors)
        ):
            return _summary(value, path=path), [path]
        if (
            projection != "full"
            and path in preserve_collection_items
            and len(value) > _MAX_INLINE_PAGE_ITEMS
            and not _is_expanded(field_name, path, selectors)
        ):
            return _summary(value, path=path), [path]
        result: list[Any] = []
        omitted: list[str] = []
        for index, nested in enumerate(value):
            compact, nested_omitted = _summarize_value(
                nested,
                path=_join_path(path, str(index)),
                projection=projection,
                selectors=selectors,
                preserve_collection_items=preserve_collection_items,
            )
            result.append(compact)
            omitted.extend(nested_omitted)
        return result, omitted
    if (
        isinstance(value, str)
        and projection != "full"
        and len(value)
        > (
            _MAX_COLLECTION_PREVIEW_CHARS
            if _is_collection_path(path)
            else _MAX_DETAIL_PREVIEW_CHARS
        )
        and not _path_has_expanded_ancestor(path, selectors)
    ):
        return _summary(value, path=path), [path]
    return value, []


def _should_summarize(
    key: str,
    value: Any,
    projection: str,
    selectors: tuple[str, ...],
    path: str,
) -> bool:
    if projection == "full" or _is_expanded(key, path, selectors):
        return False
    if key in _TEXT_FIELDS:
        return isinstance(value, str)
    if key in _STRUCTURED_FIELDS:
        if _selector_targets_descendant(path, value, selectors):
            return False
        return isinstance(value, dict | list) and bool(value)
    return False


def _drop_redundant_collection_aliases(
    data: Mapping[str, object],
    *,
    projection: str,
) -> dict[str, object]:
    result = dict(data)
    if projection == "full" or not isinstance(result.get("items"), list):
        return result
    if isinstance(result.get("records"), list) and result["records"] == result["items"]:
        result.pop("records", None)
    if (
        isinstance(result.get("pagination"), dict)
        and isinstance(result.get("has_more"), bool)
        and "next_cursor" in result
    ):
        result.pop("pagination", None)
    return result


def _is_expanded(key: str, path: str, selectors: tuple[str, ...]) -> bool:
    for selector in selectors:
        if selector == key or selector == path:
            return True
        if "*" in selector:
            expected = selector.replace("*", "[^/]+")
            # Avoid a regex dependency for an input that is intentionally tiny
            # and declarative: compare path components instead.
            selector_parts = selector.strip("/").split("/")
            path_parts = path.strip("/").split("/")
            if len(selector_parts) == len(path_parts) and all(
                left == "*" or left == right
                for left, right in zip(selector_parts, path_parts, strict=True)
            ):
                return True
            _ = expected
    return False


def _selector_targets_descendant(
    path: str,
    value: Any,
    selectors: tuple[str, ...],
) -> bool:
    """Keep a summarized container traversable when a requested child exists.

    Bare selectors (for example ``--expand message``) apply at any depth, but
    should not prevent unrelated structured fields from being summarized.  We
    therefore inspect the small JSON-like subtree before deciding to descend.
    Pointer selectors can be matched from path components without walking the
    value, including ``*`` list wildcards.
    """

    path_parts = path.strip("/").split("/")
    for selector in selectors:
        if not selector.startswith("/"):
            if _contains_field(value, selector):
                return True
            continue
        selector_parts = selector.strip("/").split("/")
        if len(selector_parts) <= len(path_parts):
            continue
        if all(
            expected == "*" or expected == actual
            for expected, actual in zip(selector_parts, path_parts, strict=False)
        ):
            return True
    return False


def _contains_field(value: Any, field: str) -> bool:
    """Return whether a bare expansion field occurs in a JSON-like subtree."""

    if isinstance(value, dict):
        if field in value:
            return True
        return any(_contains_field(child, field) for child in value.values())
    if isinstance(value, list):
        return any(_contains_field(child, field) for child in value)
    return False


def _path_has_expanded_ancestor(path: str, selectors: tuple[str, ...]) -> bool:
    path_parts = path.strip("/").split("/")
    for selector in selectors:
        if not selector:
            continue
        if selector.startswith("/"):
            selector_parts = selector.strip("/").split("/")
            if len(selector_parts) <= len(path_parts) and all(
                expected == "*" or expected == actual
                for expected, actual in zip(selector_parts, path_parts, strict=False)
            ):
                return True
        elif selector in path_parts:
            return True
    return False


def _enforce_result_budget(
    data: dict[str, object],
    *,
    projection: str,
    selectors: tuple[str, ...],
) -> tuple[dict[str, object], list[str], bool]:
    if projection == "full" or selectors or _json_size(data) <= _MAX_DEFAULT_RESULT_BYTES:
        return data, [], False

    result = dict(data)
    omitted: list[str] = []
    items = result.get("items")
    if isinstance(items, list) and all(isinstance(item, dict) for item in items):
        compact_items: list[object] = []
        for index, item in enumerate(items):
            assert isinstance(item, dict)
            compact, item_omitted = _compact_item_to_budget(
                item,
                path=f"/items/{index}",
                max_bytes=_MAX_INLINE_ITEM_BYTES,
            )
            compact_items.append(compact)
            omitted.extend(item_omitted)
        result["items"] = compact_items

        if _json_size(result) > _DEFAULT_RESULT_TARGET_BYTES:
            for fields in (
                _collection_identity_fields(compact_items, include_names=True),
                _collection_identity_fields(compact_items, include_names=False),
                _collection_identifier_fields(compact_items),
            ):
                projected, projected_omitted = _project_collection_items(
                    compact_items,
                    fields=fields,
                )
                result["items"] = projected
                omitted.extend(projected_omitted)
                if _json_size(result) <= _DEFAULT_RESULT_TARGET_BYTES:
                    break

    if _json_size(result) > _DEFAULT_RESULT_TARGET_BYTES:
        protected = {
            "id",
            "task_id",
            "job_id",
            "plan_id",
            "status",
            "items",
            "next_cursor",
            "has_more",
            "continuation",
        }
        candidates: list[tuple[int, str, dict[str, object]]] = []
        for key, value in result.items():
            if key in protected:
                continue
            summary = _summary(value, path=f"/{key}")
            savings = _json_size(value) - _json_size(summary)
            if savings > 0:
                candidates.append((savings, key, summary))
        for _savings, key, summary in sorted(candidates, reverse=True):
            result[key] = summary
            omitted.append(f"/{key}")
            if _json_size(result) <= _DEFAULT_RESULT_TARGET_BYTES:
                break
    return result, omitted, True


def _compact_item_to_budget(
    item: dict[str, object],
    *,
    path: str,
    max_bytes: int,
) -> tuple[dict[str, object], list[str]]:
    if _json_size(item) <= max_bytes:
        return item, []
    priority = _ordered_item_fields(item)
    result: dict[str, object] = {}
    omitted: list[str] = []
    for key in priority:
        candidate = {**result, key: item[key]}
        if not result or _json_size(candidate) <= max_bytes:
            result[key] = item[key]
        else:
            omitted.append(_join_path(path, key))
    for key in item:
        if key not in result and _join_path(path, key) not in omitted:
            omitted.append(_join_path(path, key))
    return result, omitted


def _ordered_item_fields(item: Mapping[str, object]) -> list[str]:
    priority = (
        "id",
        "task_id",
        "job_id",
        "plan_id",
        "candidate_id",
        "campaign_id",
        "item_id",
        "professor_id",
        "identity_id",
        "status",
        "review_status",
        "state_category",
        "name",
        "email",
        "title",
        "university",
        "school",
        "profile_url",
    )
    selected = [key for key in priority if key in item]
    selected.extend(
        key
        for key in item
        if key not in selected and (key.endswith("_id") or key.endswith("_count"))
    )
    selected.extend(key for key in item if key not in selected)
    return selected


def _collection_identity_fields(
    items: list[object],
    *,
    include_names: bool,
) -> frozenset[str]:
    fields = set(_collection_identifier_fields(items))
    fields.update({"status", "review_status", "state_category"})
    if include_names:
        fields.update({"name", "email"})
    return frozenset(fields)


def _collection_identifier_fields(items: list[object]) -> frozenset[str]:
    return frozenset(
        key
        for item in items
        if isinstance(item, dict)
        for key in item
        if key == "id" or key.endswith("_id")
    )


def _project_collection_items(
    items: list[object],
    *,
    fields: frozenset[str],
) -> tuple[list[object], list[str]]:
    result: list[object] = []
    omitted: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            result.append(item)
            continue
        result.append({key: value for key, value in item.items() if key in fields})
        omitted.extend(f"/items/*/{_escape_pointer(key)}" for key in item if key not in fields)
    return result, omitted


def _escape_pointer(component: str) -> str:
    return component.replace("~", "~0").replace("/", "~1")


def _json_size(value: object) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8"),
    )


def _summary(value: Any, *, path: str) -> dict[str, object]:
    if isinstance(value, str):
        max_chars = (
            _MAX_COLLECTION_PREVIEW_CHARS
            if _is_collection_path(path)
            else _MAX_DETAIL_PREVIEW_CHARS
        )
        preview = value[:max_chars]
        return {
            "kind": "text_summary",
            "characters": len(value),
            "preview": preview,
            "truncated": len(value) > len(preview),
        }
    if isinstance(value, list):
        return {
            "kind": "array_summary",
            "item_count": len(value),
            "truncated": bool(value),
        }
    if isinstance(value, dict):
        return {
            "kind": "object_summary",
            "keys": sorted(str(key) for key in value)[:12],
            "key_count": len(value),
            "truncated": bool(value),
        }
    return {"kind": "value_summary", "type": type(value).__name__, "truncated": False}


def _is_collection_path(path: str) -> bool:
    parts = set(path.strip("/").split("/"))
    return bool(parts & {"items", "records", "messages", "history", "buckets"})


def _result_limit(data: Mapping[str, object]) -> int | None:
    items = data.get("items")
    if not isinstance(items, list):
        return None
    value = data.get("limit")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return len(items)


def _continuation(
    command: str,
    data: Mapping[str, object],
    *,
    limit: int | None,
    continuation_input: Mapping[str, object] | None,
    invoke_input: Mapping[str, object] | None,
) -> dict[str, object] | None:
    if not data.get("has_more"):
        return None
    next_cursor = data.get("next_cursor")
    if not isinstance(next_cursor, str) or not next_cursor:
        return None
    mode = data.get("pagination_mode")
    page_mode = mode == "page"
    offset_mode = mode == "offset"
    cursor_name = "page" if page_mode else ("offset" if offset_mode else "cursor")
    cursor_value: object = next_cursor
    if page_mode or offset_mode:
        try:
            cursor_value = int(next_cursor)
        except ValueError:
            cursor_value = next_cursor
    base_input = dict(invoke_input or continuation_input or {})
    base_input.pop("all_items", None)
    base_input[cursor_name] = cursor_value
    if limit is not None:
        base_input["limit" if cursor_name != "page" else "page_size"] = limit
    return {
        "command": command,
        "input": base_input,
        "cursor": next_cursor,
        "mode": "page" if page_mode else ("offset" if offset_mode else "cursor"),
        "reuse_previous_input": not bool(invoke_input),
    }


def _join_path(prefix: str, component: str) -> str:
    escaped = component.replace("~", "~0").replace("/", "~1")
    return f"{prefix}/{escaped}" if prefix else f"/{escaped}"


def _compact_omitted_paths(paths: Sequence[str]) -> tuple[list[str], int]:
    unique_paths: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique_paths.append(path)

    groups: dict[str, list[str]] = {}
    for path in unique_paths:
        wildcard = _wildcard_array_indexes(path)
        groups.setdefault(wildcard, []).append(path)

    compact: list[str] = []
    emitted: set[str] = set()
    for path in unique_paths:
        wildcard = _wildcard_array_indexes(path)
        output_path = wildcard if len(groups[wildcard]) > 1 else path
        if output_path in emitted:
            continue
        emitted.add(output_path)
        compact.append(output_path)
    return compact[:_MAX_OMITTED_PATHS], len(unique_paths)


def _wildcard_array_indexes(path: str) -> str:
    components = path.split("/")
    return "/".join("*" if component.isdigit() else component for component in components)
