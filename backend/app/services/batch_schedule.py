from __future__ import annotations

import random
import re
from datetime import UTC, date, datetime, time, timedelta


DATE_FORMAT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_scheduled_dates(values: list[str] | None) -> list[str]:
    if not values:
        return []

    normalized: set[str] = set()
    for value in values:
        if not DATE_FORMAT_PATTERN.fullmatch(value):
            raise ValueError("发送日期必须使用 YYYY-MM-DD 格式")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("发送日期必须使用 YYYY-MM-DD 格式") from exc
        normalized.add(parsed.isoformat())

    return sorted(normalized)


def is_datetime_in_batch_window(
    now: datetime,
    *,
    scheduled_dates: list[str] | None,
    window_start_time: str | None,
    window_end_time: str | None,
) -> bool:
    dates = set(normalize_scheduled_dates(scheduled_dates))
    if now.date().isoformat() not in dates:
        return False
    if not window_start_time or not window_end_time:
        return False

    current = now.strftime("%H:%M")
    return window_start_time <= current < window_end_time


def has_future_batch_window(
    now: datetime,
    *,
    scheduled_dates: list[str] | None,
    window_end_time: str | None,
) -> bool:
    if not window_end_time:
        return False

    dates = normalize_scheduled_dates(scheduled_dates)
    if not dates:
        return False

    current = now.replace(tzinfo=None)
    end_clock = time.fromisoformat(window_end_time)
    for value in dates:
        if datetime.combine(date.fromisoformat(value), end_clock) > current:
            return True
    return False


def is_batch_window_expired(
    now: datetime,
    *,
    scheduled_dates: list[str] | None,
    window_end_time: str | None,
) -> bool:
    if not scheduled_dates or not window_end_time:
        return False
    return not has_future_batch_window(
        now,
        scheduled_dates=scheduled_dates,
        window_end_time=window_end_time,
    )


def build_jittered_batch_schedule(
    *,
    task_count: int,
    scheduled_dates: list[str],
    window_start_time: str,
    window_end_time: str,
    emails_per_window: int,
    now: datetime,
    jitter_ratio: float = 0.3,
    max_jitter: timedelta = timedelta(minutes=10),
    random_source: random.Random | None = None,
) -> list[datetime]:
    if task_count <= 0:
        return []
    if emails_per_window <= 0:
        raise ValueError("每天发送数量必须大于 0")

    dates = normalize_scheduled_dates(scheduled_dates)
    start_clock = time.fromisoformat(window_start_time)
    end_clock = time.fromisoformat(window_end_time)
    if end_clock <= start_clock:
        raise ValueError("结束时间必须晚于开始时间")

    local_now = now.replace(tzinfo=None)
    timezone = now.tzinfo or UTC
    remaining = task_count
    scheduled: list[datetime] = []
    rng = random_source or random.Random()

    for value in dates:
        if remaining <= 0:
            break
        current_date = date.fromisoformat(value)
        window_start = datetime.combine(current_date, start_clock)
        window_end = datetime.combine(current_date, end_clock)
        if current_date == local_now.date():
            window_start = max(window_start, local_now)
        if window_start >= window_end:
            continue

        count_for_day = min(remaining, emails_per_window)
        scheduled.extend(
            _build_day_schedule(
                window_start.replace(tzinfo=timezone).astimezone(UTC),
                window_end.replace(tzinfo=timezone).astimezone(UTC),
                count_for_day,
                jitter_ratio=jitter_ratio,
                max_jitter=max_jitter,
                random_source=rng,
            ),
        )
        remaining -= count_for_day

    if remaining > 0:
        raise ValueError("选中的发送日期和每天发送数量不足以覆盖全部任务")
    return scheduled


def _build_day_schedule(
    window_start: datetime,
    window_end: datetime,
    count: int,
    *,
    jitter_ratio: float,
    max_jitter: timedelta,
    random_source: random.Random,
) -> list[datetime]:
    if count <= 0:
        return []
    total_seconds = (window_end - window_start).total_seconds()
    if total_seconds <= 0:
        return []

    slot_seconds = total_seconds / count
    jitter_seconds = min(slot_seconds * jitter_ratio, max_jitter.total_seconds())
    values: list[datetime] = []
    for index in range(count):
        base_offset = slot_seconds * (index + 0.5)
        jitter = random_source.uniform(-jitter_seconds, jitter_seconds) if jitter_seconds > 0 else 0
        scheduled_at = window_start + timedelta(seconds=base_offset + jitter)
        scheduled_at = max(window_start, min(scheduled_at, window_end))
        values.append(scheduled_at)
    return sorted(values)
