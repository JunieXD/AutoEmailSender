from __future__ import annotations

from email.utils import getaddresses, parseaddr
from typing import Iterable


def normalize_email_address(value: str | None) -> str:
    if value is None:
        return ""
    _, address = parseaddr(value)
    return address.strip().lower()


def normalize_email_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    if not values:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for _, address in getaddresses(values):
        email_address = address.strip().lower()
        if email_address and email_address not in seen:
            seen.add(email_address)
            normalized.append(email_address)
    return normalized


def email_matches(value: str | None, candidates: Iterable[str | None]) -> bool:
    normalized = normalize_email_address(value)
    if not normalized:
        return False
    return any(normalized == normalize_email_address(candidate) for candidate in candidates)
