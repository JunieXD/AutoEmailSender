from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

import tldextract


_PUBLIC_SUFFIX_EXTRACTOR = tldextract.TLDExtract(
    cache_dir=None,
    suffix_list_urls=(),
    include_psl_private_domains=True,
)


def registrable_domain_from_hostname(hostname: str | None) -> str:
    """Return the PSL-backed registrable domain for a hostname."""

    normalized = _normalize_hostname(hostname)
    if not normalized:
        return ""
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        extracted = _PUBLIC_SUFFIX_EXTRACTOR(normalized)
        return extracted.top_domain_under_public_suffix or normalized
    return normalized


def registrable_domain_from_url(url: str) -> str:
    try:
        hostname = urlsplit(url.strip()).hostname
    except ValueError:
        return ""
    return registrable_domain_from_hostname(hostname)


def is_same_registrable_domain(url: str, reference_url: str) -> bool:
    domain = registrable_domain_from_url(url)
    reference_domain = registrable_domain_from_url(reference_url)
    return bool(domain and reference_domain and domain == reference_domain)


def _normalize_hostname(hostname: str | None) -> str:
    normalized = (hostname or "").strip().rstrip(".").lower()
    if not normalized:
        return ""
    try:
        return normalized.encode("idna").decode("ascii")
    except UnicodeError:
        return normalized
