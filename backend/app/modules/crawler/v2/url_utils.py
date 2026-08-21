from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from ..pages.domain_policy import is_same_registrable_domain

_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "yclid", "mc_cid", "mc_eid"}
_ABSOLUTE_URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)


def recover_embedded_absolute_url(url: str) -> str:
    """Recover an absolute URL accidentally embedded in another URL's path."""

    raw = str(url or "").strip()
    try:
        outer = urlsplit(raw)
    except ValueError:
        return raw
    if outer.scheme.lower() not in {"http", "https"} or not outer.hostname:
        return raw

    decoded_url = raw
    for _ in range(4):
        matches = tuple(_ABSOLUTE_URL_PATTERN.finditer(decoded_url))
        if len(matches) >= 2:
            embedded_start = matches[1].start()
            path_end = min(
                (
                    position
                    for separator in ("?", "#")
                    if (position := decoded_url.find(separator)) >= 0
                ),
                default=len(decoded_url),
            )
            candidate = decoded_url[embedded_start:]
            try:
                parsed_candidate = urlsplit(candidate)
            except ValueError:
                parsed_candidate = None
            if (
                embedded_start < path_end
                and parsed_candidate is not None
                and parsed_candidate.scheme.lower() in {"http", "https"}
                and parsed_candidate.hostname
            ):
                return candidate
        next_url = unquote(decoded_url)
        if next_url == decoded_url:
            break
        decoded_url = next_url
    return raw


def normalize_url(url: str, *, base_url: str | None = None) -> str:
    joined = urljoin(base_url or "", url.strip())
    parsed = urlsplit(joined)
    scheme = (parsed.scheme or "https").lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = hostname
    if port is not None and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        netloc = f"{hostname}:{port}"

    path = parsed.path or ""
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_query_key(key)
    ]
    query = urlencode(sorted(query_items), doseq=True)
    fragment = parsed.fragment if is_spa_route_fragment(parsed.fragment) else ""
    return urlunsplit((scheme, netloc, path, query, fragment))


def is_same_domain(url: str, reference_url: str) -> bool:
    return is_same_registrable_domain(normalize_url(url), normalize_url(reference_url))


def is_spa_route_fragment(fragment: str) -> bool:
    return fragment.startswith("/") or fragment.startswith("!/")


def has_spa_route_fragment(url: str) -> bool:
    return is_spa_route_fragment(urlsplit(url).fragment)


def task_dedupe_key(job_id: int, url: str, *, base_url: str | None = None) -> str:
    return f"{job_id}:{normalize_url(url, base_url=base_url)}"


def _is_tracking_query_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _TRACKING_QUERY_KEYS or any(
        lowered.startswith(prefix) for prefix in _TRACKING_QUERY_PREFIXES
    )
