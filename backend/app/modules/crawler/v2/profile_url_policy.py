from __future__ import annotations

import re
from urllib.parse import urlsplit

from .url_utils import normalize_url


_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


class CandidateProfileUrlPolicyError(ValueError):
    """Raised when a profile URL is rejected by a deterministic crawl policy."""


def extract_normalized_markdown_links(
    content: str,
    *,
    base_url: str,
) -> tuple[tuple[str, str], ...]:
    links: list[tuple[str, str]] = []
    for match in _MARKDOWN_LINK_PATTERN.finditer(content):
        normalized = normalize_url(match.group(2), base_url=base_url)
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        links.append((match.group(1), normalized))
    return tuple(links)


def has_explicit_markdown_link(
    content: str,
    *,
    base_url: str,
    target_url: str,
) -> bool:
    normalized_target = normalize_url(target_url, base_url=base_url)
    return any(
        link_url == normalized_target
        for _label, link_url in extract_normalized_markdown_links(
            content,
            base_url=base_url,
        )
    )
