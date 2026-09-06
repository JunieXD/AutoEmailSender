from __future__ import annotations

from .events import normalize_agent_trace_event


def latest_event_message(agent_trace: object) -> str | None:
    if not isinstance(agent_trace, list):
        return None
    trace_events = [item for item in agent_trace if isinstance(item, dict)]
    if not trace_events:
        return None
    latest_event = trace_events[-1]
    summary = latest_event.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    message = normalize_agent_trace_event(latest_event).get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return None
