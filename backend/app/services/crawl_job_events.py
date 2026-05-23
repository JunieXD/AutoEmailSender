from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any


STATUS_MESSAGES = {
    "queued": "任务已排队",
    "running": "任务正在运行",
    "paused": "任务已暂停",
    "needs_review": "任务进入待审核",
    "partially_completed": "任务部分候选已导入",
    "completed": "任务已完成",
    "failed": "任务失败",
    "canceled": "任务已取消",
}
TOOL_MESSAGES = {
    "crawl_page": "Agent 调用 crawl_page 抓取页面",
    "investigate_with_browser": "Agent 调用浏览器调查页面",
    "claim_next_page_chunk": "Agent 领取待处理页面片段",
    "submit_chunk_candidates": "Agent 提交页面片段候选",
    "save_professor_candidates": "Agent 保存候选导师",
}
KNOWN_TOOL_NAMES = frozenset(TOOL_MESSAGES)
EVENT_TYPE_MESSAGES = {
    "chunk_split_required": "页面片段候选过密，已触发拆分",
    "duplicate_loop": "候选重复提交循环，已要求停止当前保存",
}
GENERIC_AGENT_MESSAGES = {
    "Agent 事件：updates",
    "Agent 事件：dict",
    "Agent 更新了执行状态",
}
TOOL_NAME_PATTERN = re.compile(r"['\"]name['\"]\s*:\s*['\"]([^'\"]+)['\"]")


def build_crawl_job_events(
    job: Any,
    *,
    pages: list[Any],
    candidates: list[Any],
) -> list[dict[str, object]]:
    job_id = _get_attr(job, "id")
    events: list[dict[str, object]] = []

    status = _enum_value(_get_attr(job, "status"))
    events.append(
        {
            "id": f"job:{job_id}:status:{status or 'unknown'}",
            "job_id": job_id,
            "event_type": "job_status",
            "message": STATUS_MESSAGES.get(status, "任务状态已更新"),
            "created_at": _to_event_time(_get_attr(job, "updated_at") or _get_attr(job, "created_at")),
            "raw": {
                "status": status,
                "error_message": _get_attr(job, "error_message"),
            },
        },
    )

    for index, trace_event in enumerate(_iter_agent_trace(_get_attr(job, "agent_trace"))):
        normalized = normalize_agent_trace_event(trace_event)
        if not _should_include_agent_trace_event(normalized):
            continue
        event_id = normalized.get("id") or f"job:{job_id}:trace:{index}"
        events.append(
            {
                **normalized,
                "id": event_id,
                "job_id": job_id,
            },
        )

    for page in pages:
        page_id = _get_attr(page, "id")
        title = _get_attr(page, "title")
        url = _get_attr(page, "url")
        events.append(
            {
                "id": f"job:{job_id}:page:{page_id or len(events)}",
                "job_id": job_id,
                "event_type": "page",
                "message": f"已抓取页面：{title or url or '未知页面'}",
                "created_at": _to_event_time(_get_attr(page, "created_at")),
                "raw": {
                    "id": page_id,
                    "url": url,
                    "title": title,
                    "status": _enum_value(_get_attr(page, "status")),
                },
            },
        )

    for candidate in candidates:
        candidate_id = _get_attr(candidate, "id")
        name = _get_attr(candidate, "name") or "未知导师"
        events.append(
            {
                "id": f"job:{job_id}:candidate:{candidate_id or len(events)}",
                "job_id": job_id,
                "event_type": "candidate",
                "message": f"发现候选导师：{name}",
                "created_at": _to_event_time(_get_attr(candidate, "created_at")),
                "raw": {
                    "id": candidate_id,
                    "name": _get_attr(candidate, "name"),
                    "email": _get_attr(candidate, "email"),
                    "source_url": _get_attr(candidate, "source_url"),
                    "confidence": _get_attr(candidate, "confidence"),
                },
            },
        )

    return sorted(events, key=lambda event: str(event.get("created_at") or ""))


def normalize_agent_trace_event(event: dict[str, object]) -> dict[str, object]:
    raw = event if isinstance(event, dict) else {}
    event_type = _trace_event_type(raw)

    return {
        "id": raw.get("id") or raw.get("event_id") or "",
        "event_type": event_type,
        "message": summarize_agent_trace_event(raw),
        "created_at": _to_event_time(raw.get("created_at") or raw.get("timestamp") or raw.get("time")),
        "raw": raw,
    }


def summarize_agent_trace_event(event: dict[str, object]) -> str:
    if not isinstance(event, dict) or not event:
        return "Agent 更新了执行状态"

    raw_event = event.get("raw")
    if isinstance(raw_event, dict):
        raw_summary = summarize_agent_trace_event(raw_event)
        if raw_summary not in GENERIC_AGENT_MESSAGES:
            return raw_summary

    message = event.get("message")
    if isinstance(message, str) and message.strip() and message.strip() not in GENERIC_AGENT_MESSAGES:
        return message.strip()

    summary = event.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()

    name = _find_nested_tool_name(event)
    if name:
        return TOOL_MESSAGES.get(name, f"Agent 调用 {name}")

    event_type = _trace_event_type(event)
    if event_type in EVENT_TYPE_MESSAGES:
        return EVENT_TYPE_MESSAGES[event_type]
    if event_type:
        return f"Agent 事件：{event_type}"

    return "Agent 更新了执行状态"


def _iter_agent_trace(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    return list(value)


def _find_nested_name(value: object) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "name" and isinstance(item, str) and item.strip():
                return item.strip()

        for item in value.values():
            nested = _find_nested_name(item)
            if nested:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = _find_nested_name(item)
            if nested:
                return nested

    return None


def _find_nested_tool_name(value: object) -> str | None:
    if isinstance(value, str):
        match = TOOL_NAME_PATTERN.search(value)
        if match:
            name = match.group(1).strip()
            return name if name in KNOWN_TOOL_NAMES else None
        return None

    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str) and name.strip() in KNOWN_TOOL_NAMES:
            return name.strip()
        for item in value.values():
            nested = _find_nested_tool_name(item)
            if nested:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = _find_nested_tool_name(item)
            if nested:
                return nested

    return None


def _should_include_agent_trace_event(event: dict[str, object]) -> bool:
    message = event.get("message")
    if isinstance(message, str) and message.strip() in GENERIC_AGENT_MESSAGES:
        return False
    return True


def _trace_event_type(event: dict[str, object]) -> str:
    for key in ("event_type", "type"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _get_attr(value: object, name: str) -> Any:
    return getattr(value, name, None)


def _enum_value(value: object) -> str:
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, str):
        return value
    return ""


def _to_event_time(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
