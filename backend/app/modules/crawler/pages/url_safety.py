from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpcore
import httpx

from .domain_policy import registrable_domain_from_hostname


UNSAFE_CRAWL_URL_MESSAGE = "URL 不允许指向本机、内网或不可解析地址"
TEMPORARY_DNS_RESOLUTION_MESSAGE = "页面地址暂时无法解析，稍后将自动重试"
TEMPORARY_FINAL_DNS_RESOLUTION_MESSAGE = "浏览器最终地址暂时无法解析，稍后将自动重试"


@dataclass(frozen=True)
class SafeCrawlUrl:
    hostname: str
    resolved_ips: tuple[str, ...]


class TemporaryCrawlDNSResolutionError(ValueError):
    """The hostname could not be resolved for this attempt."""


def is_allowed_crawl_url(start_url: str, candidate_url: str) -> bool:
    start = urlparse(start_url)
    candidate = urlparse(urljoin(start_url, candidate_url))
    absolute_candidate_url = candidate.geturl()
    if not is_safe_public_crawl_url(start_url):
        return False
    if not is_safe_public_crawl_url(absolute_candidate_url):
        return False
    start_domain = registrable_domain_from_hostname((start.hostname or "").lower())
    candidate_domain = registrable_domain_from_hostname(
        (candidate.hostname or "").lower()
    )
    return bool(start_domain and start_domain == candidate_domain)


def is_resolved_allowed_crawl_url(
    start_url: str,
    candidate_url: str,
    *,
    allow_public_dns_fallback: bool = False,
) -> bool:
    return (
        resolved_allowed_crawl_url_error(
            start_url,
            candidate_url,
            allow_public_dns_fallback=allow_public_dns_fallback,
        )
        is None
    )


def resolved_allowed_crawl_url_error(
    start_url: str,
    candidate_url: str,
    *,
    allow_public_dns_fallback: bool = False,
) -> str | None:
    absolute_candidate_url = urljoin(start_url, candidate_url)
    if not is_allowed_crawl_url(start_url, absolute_candidate_url):
        return UNSAFE_CRAWL_URL_MESSAGE
    try:
        resolve_safe_public_crawl_url(
            start_url,
            allow_public_dns_fallback=allow_public_dns_fallback,
        )
        resolve_safe_public_crawl_url(
            absolute_candidate_url,
            allow_public_dns_fallback=allow_public_dns_fallback,
        )
    except TemporaryCrawlDNSResolutionError:
        return TEMPORARY_DNS_RESOLUTION_MESSAGE
    except ValueError:
        return UNSAFE_CRAWL_URL_MESSAGE
    return None


def is_safe_public_crawl_url(url: str) -> bool:
    try:
        validate_safe_public_crawl_url(url)
    except ValueError:
        return False
    return True


def validate_safe_public_crawl_url(url: str) -> None:
    validate_safe_crawl_url_literal(url)


def validate_safe_crawl_url_literal(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)

    host = parsed.hostname
    if not host:
        raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)

    normalized_host = host.rstrip(".").lower()
    if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
        raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)

    try:
        ip_address = ipaddress.ip_address(normalized_host)
    except ValueError:
        return (
            normalized_host,
            parsed.scheme,
            parsed.port or _default_port_for_scheme(parsed.scheme),
        )

    if _is_unsafe_ip_address(ip_address):
        raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)
    return (
        normalized_host,
        parsed.scheme,
        parsed.port or _default_port_for_scheme(parsed.scheme),
    )


def resolve_safe_public_crawl_url(
    url: str,
    *,
    allow_public_dns_fallback: bool = False,
) -> SafeCrawlUrl:
    normalized_host, _scheme, port = validate_safe_crawl_url_literal(url)
    try:
        ip_address = ipaddress.ip_address(normalized_host)
    except ValueError:
        try:
            resolved_ips = _resolve_system_host_ips(normalized_host, port)
        except ValueError:
            if not allow_public_dns_fallback:
                raise
            resolved_ips = resolve_public_dns_host_ips(normalized_host)
        return SafeCrawlUrl(hostname=normalized_host, resolved_ips=resolved_ips)
    return SafeCrawlUrl(hostname=normalized_host, resolved_ips=(str(ip_address),))


def _resolve_system_host_ips(host: str, port: int) -> tuple[str, ...]:
    try:
        address_infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise TemporaryCrawlDNSResolutionError(
            TEMPORARY_DNS_RESOLUTION_MESSAGE
        ) from exc

    if not address_infos:
        raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)

    resolved_ips: list[str] = []
    for address_info in address_infos:
        sockaddr = address_info[4]
        if not sockaddr:
            raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)
        ip_text = str(sockaddr[0])
        try:
            ip_address = ipaddress.ip_address(ip_text)
        except ValueError as exc:
            raise ValueError(UNSAFE_CRAWL_URL_MESSAGE) from exc
        if _is_unsafe_ip_address(ip_address):
            raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)
        normalized_ip = str(ip_address)
        if normalized_ip not in resolved_ips:
            resolved_ips.append(normalized_ip)
    return tuple(resolved_ips)


@lru_cache(maxsize=256)
def resolve_public_dns_host_ips(host: str) -> tuple[str, ...]:
    resolved_ips: list[str] = []
    for record_type in ("A", "AAAA"):
        try:
            response = httpx.get(
                "https://cloudflare-dns.com/dns-query",
                params={"name": host, "type": record_type},
                headers={"Accept": "application/dns-json"},
                timeout=5.0,
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise TemporaryCrawlDNSResolutionError(
                TEMPORARY_DNS_RESOLUTION_MESSAGE
            ) from exc
        if payload.get("Status") != 0:
            raise TemporaryCrawlDNSResolutionError(TEMPORARY_DNS_RESOLUTION_MESSAGE)
        for answer in payload.get("Answer") or []:
            if not isinstance(answer, dict) or answer.get("type") not in {1, 28}:
                continue
            try:
                ip_address = ipaddress.ip_address(str(answer.get("data") or ""))
            except ValueError as exc:
                raise TemporaryCrawlDNSResolutionError(
                    TEMPORARY_DNS_RESOLUTION_MESSAGE
                ) from exc
            if _is_unsafe_ip_address(ip_address):
                raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)
            normalized_ip = str(ip_address)
            if normalized_ip not in resolved_ips:
                resolved_ips.append(normalized_ip)
    if not resolved_ips:
        raise TemporaryCrawlDNSResolutionError(TEMPORARY_DNS_RESOLUTION_MESSAGE)
    return tuple(resolved_ips)


class PinnedCrawlNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        *,
        hostname: str,
        resolved_ip: str,
        network_backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._hostname = hostname.rstrip(".").lower()
        self._resolved_ip = resolved_ip
        self._network_backend = network_backend or _default_async_network_backend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        if host.rstrip(".").lower() != self._hostname:
            raise httpcore.ConnectError("crawl transport attempted an unvalidated host")
        return await self._network_backend.connect_tcp(
            self._resolved_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._network_backend.connect_unix_socket(
            path,
            timeout=timeout,
            socket_options=socket_options,
        )

    async def sleep(self, seconds: float) -> None:
        await self._network_backend.sleep(seconds)


def _default_async_network_backend() -> httpcore.AsyncNetworkBackend:
    return httpcore.AnyIOBackend()


def build_safe_crawl_transport(
    *,
    hostname: str,
    resolved_ip: str,
    network_backend: httpcore.AsyncNetworkBackend | None = None,
) -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport(
        trust_env=False,
        proxy=None,
        http2=False,
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
    )
    transport._pool._network_backend = PinnedCrawlNetworkBackend(  # type: ignore[attr-defined]
        hostname=hostname,
        resolved_ip=resolved_ip,
        network_backend=network_backend,
    )
    return transport


def _default_port_for_scheme(scheme: str) -> int:
    return 80 if scheme == "http" else 443


def _is_unsafe_ip_address(
    ip_address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return any(
        (
            not ip_address.is_global,
            ip_address.is_private,
            ip_address.is_loopback,
            ip_address.is_link_local,
            ip_address.is_multicast,
            ip_address.is_unspecified,
            ip_address.is_reserved,
        )
    )


__all__ = [
    "TEMPORARY_DNS_RESOLUTION_MESSAGE",
    "TEMPORARY_FINAL_DNS_RESOLUTION_MESSAGE",
    "UNSAFE_CRAWL_URL_MESSAGE",
    "SafeCrawlUrl",
    "TemporaryCrawlDNSResolutionError",
    "build_safe_crawl_transport",
    "is_allowed_crawl_url",
    "is_resolved_allowed_crawl_url",
    "is_safe_public_crawl_url",
    "resolve_public_dns_host_ips",
    "resolve_safe_public_crawl_url",
    "resolved_allowed_crawl_url_error",
    "validate_safe_crawl_url_literal",
    "validate_safe_public_crawl_url",
]
