from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any

RECENT_PAPERS_MAX_ITEMS = 8
_RECENT_PAPERS_SPLIT_PATTERN = re.compile(r"[|；;\n]+")


def normalize_research_direction(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "；".join(parts) or None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    stripped = str(value).strip()
    return stripped or None


def normalize_recent_papers(value: Any, *, max_items: int = RECENT_PAPERS_MAX_ITEMS) -> list[str]:
    if max_items <= 0:
        return []

    raw_items: list[str]
    if value is None:
        raw_items = []
    elif isinstance(value, str):
        raw_items = [
            item.strip()
            for item in _RECENT_PAPERS_SPLIT_PATTERN.split(value)
            if item.strip()
        ]
    elif isinstance(value, Iterable):
        raw_items = [str(item).strip() for item in value if str(item).strip()]
    else:
        raw_items = []

    deduplicated: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if item in seen:
            continue
        seen.add(item)
        deduplicated.append(item)
        if len(deduplicated) >= max_items:
            break
    return deduplicated
