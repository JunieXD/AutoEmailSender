"""Compact, explicit result shaping for Agent-facing CLI output.

The Agent API deliberately returns rich records.  Printing every body, log,
and crawler evidence payload into a model context is both expensive and unsafe
to treat as instructions.  This module keeps the original command result
shape, but adds a small protocol envelope inside ``data`` that says exactly
what was summarized or remains to be fetched.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from auto_email_sender_cli.capabilities import supports_pagination

RESULT_PROTOCOL_VERSION = "2"
RESULT_PROTOCOL_FIELDS = frozenset(
    {
        "projection",
        "limit",
        "continuation",
        "truncated",
        "omitted_paths",
        "omitted_paths_total",
        "recovery_action",
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
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024
MIN_MAX_OUTPUT_BYTES = 1024
HARD_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_OUTPUT_ITEMS = 10_000
HARD_MAX_OUTPUT_ITEMS = 10_000
MAX_EXPANDED_PATHS = 32
MAX_EXPAND_SELECTOR_CHARS = 512
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
        "ui_effects",
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
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    max_items: int = DEFAULT_MAX_OUTPUT_ITEMS,
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
    if (
        not isinstance(max_output_bytes, int)
        or isinstance(max_output_bytes, bool)
        or not MIN_MAX_OUTPUT_BYTES <= max_output_bytes <= HARD_MAX_OUTPUT_BYTES
    ):
        raise ValueError("max_output_bytes is outside the supported safety range")
    if (
        not isinstance(max_items, int)
        or isinstance(max_items, bool)
        or not 1 <= max_items <= HARD_MAX_OUTPUT_ITEMS
    ):
        raise ValueError("max_items is outside the supported safety range")
    normalized_projection = "full" if projection == "full" else "summary"
    selectors = tuple(
        selector.strip()
        for selector in expanded_paths
        if isinstance(selector, str) and selector.strip()
    )
    if len(selectors) > MAX_EXPANDED_PATHS or any(
        len(selector) > MAX_EXPAND_SELECTOR_CHARS for selector in selectors
    ):
        raise ValueError("expanded_paths exceeds the supported safety range")
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
    projection_input, item_limit_omitted, original_item_count = _enforce_item_limit(
        projection_input,
        max_items=max_items,
    )
    summarized, omitted_paths = _summarize_value(
        projection_input,
        path="",
        projection=normalized_projection,
        selectors=selectors,
        preserve_collection_items=preserve_collection_items,
    )
    assert isinstance(summarized, dict)
    omitted_paths.extend(item_limit_omitted)
    summarized, budget_omitted_paths, budget_compacted, budget_input_bytes = (
        _enforce_result_budget(
            summarized,
            max_output_bytes=max_output_bytes,
        )
    )
    omitted_paths.extend(budget_omitted_paths)
    items_value = summarized.get("items")
    collection_records_omitted = isinstance(projection_input.get("items"), list) and (
        original_item_count is not None
        or budget_compacted
        or (
            isinstance(items_value, dict) and items_value.get("kind") == "array_summary"
        )
    )
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
    item_limit_compacted = original_item_count is not None
    if omitted_paths or selectors or normalized_projection == "full":
        result["projection"] = {
            "version": RESULT_PROTOCOL_VERSION,
            "mode": normalized_projection,
            "expanded_paths": list(selectors),
            "budget_bytes": max_output_bytes,
        }
        if budget_compacted:
            result["projection"]["budget_compacted"] = True
            result["projection"]["input_bytes"] = budget_input_bytes
        if item_limit_compacted:
            result["projection"]["max_items"] = max_items
            result["projection"]["input_items"] = original_item_count
        if budget_compacted or item_limit_compacted or collection_records_omitted:
            result["projection"]["recovery"] = (
                "add --output-file <path>.jsonl and --all for complete collection records"
                if supports_pagination(command)
                else "increase --max-output-bytes or request a narrower --expand path"
            )
    if collection_records_omitted and supports_pagination(command):
        result["recovery_action"] = {
            "action": "export_complete_collection",
            "command": command,
            "reuse_previous_input": True,
            "required_input": ["output_file"],
            "global_options": {"output_file": "<path>.jsonl"},
            "input": {"all_items": True},
        }
    if limit is not None:
        result["limit"] = limit
    if continuation is not None:
        result["continuation"] = continuation
    if omitted_paths:
        result["truncated"] = True
        result["omitted_paths"] = omitted_paths
        if omitted_paths_total > len(omitted_paths):
            result["omitted_paths_total"] = omitted_paths_total
    # Projection and continuation metadata can push an otherwise fitting
    # payload over budget, even when neither content pass had to compact it.
    if _json_size(result) > max_output_bytes:
        projection_metadata = result.setdefault(
            "projection",
            {
                "version": RESULT_PROTOCOL_VERSION,
                "mode": normalized_projection,
                "budget_bytes": max_output_bytes,
            },
        )
        assert isinstance(projection_metadata, dict)
        projection_metadata["budget_compacted"] = True
        result["truncated"] = True
        result.setdefault("omitted_paths", [])
        budget_compacted = True
    if budget_compacted or item_limit_compacted:
        _set_stable_output_bytes(result)
        _fit_final_result_to_budget(result, max_output_bytes=max_output_bytes)
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
            "recovery_action",
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


def _enforce_item_limit(
    data: Mapping[str, object],
    *,
    max_items: int,
) -> tuple[dict[str, object], list[str], int | None]:
    """Bound a top-level collection before recursively shaping every item."""

    result = dict(data)
    items = result.get("items")
    if not isinstance(items, list) or len(items) <= max_items:
        return result, [], None
    result["items"] = items[:max_items]
    if isinstance(result.get("records"), list) and result["records"] == items:
        result["records"] = result["items"]
    return result, ["/items/*"], len(items)


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
    max_output_bytes: int,
) -> tuple[dict[str, object], list[str], bool, int]:
    input_bytes = _json_size(data)
    if input_bytes <= max_output_bytes:
        return data, [], False, input_bytes

    result = dict(data)
    omitted: list[str] = []
    # Leave room for projection/truncation metadata added by
    # ``prepare_result_data``. The final fitting pass handles pathological
    # path names or expansion selectors without ever exceeding the hard
    # caller-visible budget.
    reserve = min(2 * 1024, max(384, max_output_bytes // 5))
    target_bytes = max(256, max_output_bytes - reserve)
    items = result.get("items")
    if isinstance(items, list) and all(isinstance(item, dict) for item in items):
        compact_items: list[object] = []
        per_item_bytes = min(
            _MAX_INLINE_ITEM_BYTES,
            max(128, target_bytes // max(1, min(len(items), 32))),
        )
        for index, item in enumerate(items):
            assert isinstance(item, dict)
            compact, item_omitted = _compact_item_to_budget(
                item,
                path=f"/items/{index}",
                max_bytes=per_item_bytes,
            )
            compact_items.append(compact)
            omitted.extend(item_omitted)
        result["items"] = compact_items

        if _json_size(result) > target_bytes:
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
                if _json_size(result) <= target_bytes:
                    break

    if _json_size(result) > target_bytes:
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
            if _json_size(result) <= target_bytes:
                break

    if _json_size(result) > target_bytes and isinstance(result.get("items"), list):
        current_items = result["items"]
        assert isinstance(current_items, list)
        retained = _largest_fitting_item_prefix(
            result,
            items=current_items,
            max_bytes=target_bytes,
        )
        if retained < len(current_items):
            result["items"] = current_items[:retained]
            omitted.append("/items/*")

    if _json_size(result) > target_bytes:
        # A single oversized identifier or an unusual top-level envelope can
        # still exceed the target. Summarize the largest remaining values as a
        # last content-level fallback; protocol metadata is attached later.
        candidates = []
        for key, value in result.items():
            summary = _budget_summary(value, path=f"/{key}")
            savings = _json_size(value) - _json_size(summary)
            if savings > 0:
                candidates.append((savings, key, summary))
        for _savings, key, summary in sorted(candidates, reverse=True):
            result[key] = summary
            omitted.append(f"/{_escape_pointer(key)}")
            if _json_size(result) <= target_bytes:
                break
    return result, omitted, True, input_bytes


def _largest_fitting_item_prefix(
    envelope: Mapping[str, object],
    *,
    items: list[object],
    max_bytes: int,
) -> int:
    low = 0
    high = len(items)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = {**envelope, "items": items[:middle]}
        if _json_size(candidate) <= max_bytes:
            low = middle
        else:
            high = middle - 1
    return low


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
        if _json_size(candidate) <= max_bytes:
            result[key] = item[key]
        else:
            summarized_value = _summary(item[key], path=_join_path(path, key))
            summarized_candidate = {**result, key: summarized_value}
            if not result and _json_size(summarized_candidate) <= max_bytes:
                result[key] = summarized_value
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
        omitted.extend(
            f"/items/*/{_escape_pointer(key)}" for key in item if key not in fields
        )
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


def _set_stable_output_bytes(result: dict[str, object]) -> None:
    projection = result.get("projection")
    if not isinstance(projection, dict):
        return
    projection["output_bytes"] = 0
    for _ in range(6):
        current = _json_size(result)
        if projection.get("output_bytes") == current:
            return
        projection["output_bytes"] = current


def _fit_final_result_to_budget(
    result: dict[str, object],
    *,
    max_output_bytes: int,
) -> None:
    """Keep result-protocol metadata inside the same advertised byte budget."""

    _set_stable_output_bytes(result)
    omitted_paths = result.get("omitted_paths")
    if isinstance(omitted_paths, list):
        while len(omitted_paths) > 1 and _json_size(result) > max_output_bytes:
            omitted_paths[:] = omitted_paths[: max(1, len(omitted_paths) // 2)]

    projection = result.get("projection")
    if isinstance(projection, dict):
        expanded_paths = projection.get("expanded_paths")
        if isinstance(expanded_paths, list):
            original_expanded_count = len(expanded_paths)
            while len(expanded_paths) > 1 and _json_size(result) > max_output_bytes:
                expanded_paths[:] = expanded_paths[: max(1, len(expanded_paths) // 2)]
            if len(expanded_paths) < original_expanded_count:
                projection["expanded_paths_total"] = original_expanded_count
        if _json_size(result) > max_output_bytes:
            projection.pop("recovery", None)

    protocol_keys = RESULT_PROTOCOL_FIELDS | {"projection"}
    if _json_size(result) > max_output_bytes:
        candidates: list[tuple[int, str, dict[str, object]]] = []
        for key, value in result.items():
            if key in protocol_keys:
                continue
            summary = _summary(value, path=f"/{key}")
            savings = _json_size(value) - _json_size(summary)
            if savings > 0:
                candidates.append((savings, key, summary))
        final_omissions: list[str] = []
        for _savings, key, summary in sorted(candidates, reverse=True):
            result[key] = summary
            final_omissions.append(f"/{_escape_pointer(key)}")
            if _json_size(result) <= max_output_bytes:
                break
        if final_omissions:
            paths = result.get("omitted_paths")
            if isinstance(paths, list):
                paths.extend(path for path in final_omissions if path not in paths)
                if _json_size(result) > max_output_bytes:
                    paths[:] = ["/*"]
                    result.pop("omitted_paths_total", None)

    if _json_size(result) > max_output_bytes and isinstance(projection, dict):
        minimal_projection = {
            "version": projection.get("version", RESULT_PROTOCOL_VERSION),
            "mode": projection.get("mode", "summary"),
            "budget_bytes": max_output_bytes,
            "budget_compacted": True,
        }
        if isinstance(projection.get("input_bytes"), int):
            minimal_projection["input_bytes"] = projection["input_bytes"]
        result["projection"] = minimal_projection
        projection = minimal_projection

    if _json_size(result) > max_output_bytes:
        identity_fields = (
            "id",
            "task_id",
            "job_id",
            "plan_id",
            "status",
        )
        minimal: dict[str, object] = {
            "projection": {
                "version": RESULT_PROTOCOL_VERSION,
                "mode": (
                    projection.get("mode", "summary")
                    if isinstance(projection, dict)
                    else "summary"
                ),
                "budget_bytes": max_output_bytes,
                "budget_compacted": True,
            },
            "truncated": True,
            "omitted_paths": ["/*"],
        }
        for key in identity_fields:
            value = result.get(key)
            if value is None or isinstance(value, dict | list):
                continue
            candidate = {**minimal, key: value}
            if _json_size(candidate) <= max_output_bytes:
                minimal[key] = value
        result.clear()
        result.update(minimal)

    _set_stable_output_bytes(result)


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


def _budget_summary(value: Any, *, path: str) -> dict[str, object]:
    if isinstance(value, dict) and isinstance(value.get("kind"), str):
        kind = value["kind"]
        if kind == "text_summary" and isinstance(value.get("preview"), str):
            preview = value["preview"][:80]
            return {
                **value,
                "preview": preview,
                "truncated": bool(value.get("truncated"))
                or len(value["preview"]) > len(preview),
            }
        if kind == "object_summary" and isinstance(value.get("keys"), list):
            return {**value, "keys": value["keys"][:6]}
        return dict(value)
    return _summary(value, path=path)


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
    return "/".join(
        "*" if component.isdigit() else component for component in components
    )
