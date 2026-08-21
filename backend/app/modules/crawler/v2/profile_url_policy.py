from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit

from .url_utils import normalize_url, recover_embedded_absolute_url


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
        normalized = normalize_profile_url(match.group(2), base_url=base_url)
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
    normalized_target = normalize_profile_url(target_url, base_url=base_url)
    for match in _MARKDOWN_LINK_PATTERN.finditer(content):
        raw_link_url = urljoin(base_url, match.group(2))
        if normalize_profile_url(raw_link_url) == normalized_target:
            return True
        for embedded_url in _extract_embedded_url_parameters(raw_link_url):
            if normalize_profile_url(embedded_url) == normalized_target:
                return True
    return False


def normalize_profile_url(url: str, *, base_url: str | None = None) -> str:
    """Normalize URL forms that are equivalent for profile provenance/cache.

    Faculty sites frequently emit ``/teacher`` in one place and
    ``/teacher/`` in another. This deliberately stays local to profile URL
    checks; the general crawler URL normalizer keeps slash-sensitive routes
    unchanged.
    """

    joined = urljoin(base_url or "", url.strip())
    normalized = normalize_url(recover_embedded_absolute_url(joined))
    parsed = urlsplit(normalized)
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def _extract_embedded_url_parameters(link_url: str) -> tuple[str, ...]:
    parsed = urlsplit(link_url)
    parameter_sections = [parsed.query]
    if "=" in parsed.fragment:
        parameter_sections.append(parsed.fragment)
    candidates: list[str] = []
    for section in parameter_sections:
        for _key, value in parse_qsl(section, keep_blank_values=True):
            decoded = _decode_url_parameter(value.strip())
            candidate = urlsplit(decoded)
            if candidate.scheme in {"http", "https"} and candidate.hostname:
                candidates.append(decoded)
    return tuple(candidates)


def _decode_url_parameter(value: str) -> str:
    decoded = value
    for _ in range(10):
        parsed = urlsplit(decoded)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            break
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded
