from __future__ import annotations

from datetime import UTC, datetime, tzinfo


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def as_utc_naive(value: datetime) -> datetime:
    return as_utc_aware(value).replace(tzinfo=None)


def local_now(local_timezone: tzinfo | None = None) -> datetime:
    timezone = local_timezone or datetime.now().astimezone().tzinfo or UTC
    return utc_now().astimezone(timezone)


def parse_api_datetime(value: str) -> datetime:
    normalized = value.strip()
    if len(normalized) == 10 and normalized[4] == "-" and normalized[7] == "-":
        raise ValueError("date-only value is Civil Time, not an Instant")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    return as_utc_aware(parsed)


def serialize_api_datetime(value: datetime) -> str:
    utc_value = as_utc_aware(value).replace(microsecond=0)
    return utc_value.isoformat().replace("+00:00", "Z")