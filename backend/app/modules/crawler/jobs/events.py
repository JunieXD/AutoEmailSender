from __future__ import annotations

from datetime import date, datetime

from app.core.time import as_utc_aware

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
GENERIC_AGENT_MESSAGES = {
    "Agent 更新了执行状态",
}


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
            "created_at": _to_event_time(
                _get_attr(job, "updated_at") or _get_attr(job, "created_at")
            ),
            "raw": {
                "status": status,
                "error_message": _get_attr(job, "error_message"),
            },
        },
    )

    for index, trace_event in enumerate(
        _iter_agent_trace(_get_attr(job, "agent_trace"))
    ):
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

    for batch_index, candidate_batch in enumerate(
        _group_candidates_by_created_at(candidates)
    ):
        candidate_ids = [_get_attr(candidate, "id") for candidate in candidate_batch]
        names = [
            _get_attr(candidate, "name") or "未知导师" for candidate in candidate_batch
        ]
        first_candidate = candidate_batch[0]
        first_candidate_id = candidate_ids[0] if candidate_ids else None
        events.append(
            {
                "id": f"job:{job_id}:candidate:{first_candidate_id or batch_index}",
                "job_id": job_id,
                "event_type": "candidate",
                "message": (
                    f"发现候选导师 {len(candidate_batch)} 人：{'、'.join(names)}"
                ),
                "created_at": _to_event_time(_get_attr(first_candidate, "created_at")),
                "raw": {
                    "id": first_candidate_id,
                    "candidate_ids": candidate_ids,
                    "names": names,
                    "count": len(candidate_batch),
                    "candidates": [
                        {
                            "id": _get_attr(candidate, "id"),
                            "name": _get_attr(candidate, "name"),
                            "email": _get_attr(candidate, "email"),
                            "source_url": _get_attr(candidate, "source_url"),
                            "confidence": _get_attr(candidate, "confidence"),
                        }
                        for candidate in candidate_batch
                    ],
                },
            },
        )

    return sorted(events, key=lambda event: str(event.get("created_at") or ""))


def _group_candidates_by_created_at(candidates: list[Any]) -> list[list[Any]]:
    groups: list[list[Any]] = []
    group_by_time: dict[str, list[Any]] = {}
    for candidate in candidates:
        key = str(_to_event_time(_get_attr(candidate, "created_at")) or "")
        if key not in group_by_time:
            group_by_time[key] = []
            groups.append(group_by_time[key])
        group_by_time[key].append(candidate)
    return groups


def normalize_agent_trace_event(event: dict[str, object]) -> dict[str, object]:
    raw = event if isinstance(event, dict) else {}
    event_type = _trace_event_type(raw)

    return {
        "id": raw.get("id") or raw.get("event_id") or "",
        "event_type": event_type,
        "message": summarize_agent_trace_event(raw),
        "created_at": _to_event_time(
            raw.get("created_at") or raw.get("timestamp") or raw.get("time")
        ),
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
    if (
        isinstance(message, str)
        and message.strip()
        and message.strip() not in GENERIC_AGENT_MESSAGES
    ):
        return message.strip()

    summary = event.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()

    event_type = _trace_event_type(event)
    if event_type:
        return f"Agent 事件：{event_type}"

    return "Agent 更新了执行状态"


def _iter_agent_trace(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    return list(value)


def _should_include_agent_trace_event(event: dict[str, object]) -> bool:
    message = event.get("message")
    if not isinstance(message, str):
        return False
    normalized_message = message.strip()
    if not normalized_message:
        return False
    if normalized_message in GENERIC_AGENT_MESSAGES or normalized_message.startswith(
        "Agent 事件："
    ):
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
            value = as_utc_aware(value)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
