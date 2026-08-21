from __future__ import annotations

import asyncio
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from ..pages.tools import CrawlToolContext, PageSnapshot, fetch_binary_resource


MAX_EMBEDDED_PROFILE_PDF_CANDIDATES = 4
_PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}
_PDF_QUERY_KEYS = {"file"}
_PROFILE_PDF_SECTION_LABEL = "嵌入的个人资料 PDF 正文："
_TRUNCATION_MARKER = "\n\n[内容过长，已保留开头和结尾]\n\n"


@dataclass(frozen=True, slots=True)
class EmbeddedProfilePdfText:
    source_url: str
    text: str


async def extract_primary_embedded_profile_pdf_text(
    ctx: CrawlToolContext,
    snapshot: PageSnapshot,
) -> EmbeddedProfilePdfText | None:
    """Extract the first readable PDF explicitly embedded as the profile body."""

    for pdf_url in discover_embedded_profile_pdf_urls(snapshot):
        try:
            final_url, content_type, content = await fetch_binary_resource(ctx, pdf_url)
            if not _looks_like_pdf(content_type, content):
                continue
            text = await asyncio.to_thread(_extract_pdf_text, content)
        except Exception:
            continue
        normalized_text = (text or "").replace("\ufeff", "").strip()
        if normalized_text:
            return EmbeddedProfilePdfText(
                source_url=final_url,
                text=normalized_text,
            )
    return None


def discover_embedded_profile_pdf_urls(snapshot: PageSnapshot) -> tuple[str, ...]:
    """Find direct or viewer-wrapped PDFs without following ordinary page links."""

    candidates: list[tuple[str, str]] = [(snapshot.url, snapshot.url)]
    soup = BeautifulSoup(snapshot.html or "", "html.parser")
    for tag in soup.find_all(["iframe", "object", "embed"]):
        if not isinstance(tag, Tag):
            continue
        raw_url = str(tag.get("src") or tag.get("data") or "").strip()
        if raw_url:
            candidates.append((_document_base_url(tag, snapshot.url), raw_url))
    for tag in soup.find_all(attrs={"data-crawl-frame-url": True}):
        raw_url = str(tag.get("data-crawl-frame-url") or "").strip()
        if raw_url:
            candidates.append((snapshot.url, raw_url))

    discovered: list[str] = []
    seen: set[str] = set()
    for base_url, candidate_url in candidates:
        pdf_url = _resolve_pdf_url(base_url, candidate_url)
        if not pdf_url or pdf_url in seen:
            continue
        seen.add(pdf_url)
        discovered.append(pdf_url)
        if len(discovered) >= MAX_EMBEDDED_PROFILE_PDF_CANDIDATES:
            break
    return tuple(discovered)


def merge_profile_text_with_embedded_pdf(
    page_text: str,
    pdf_text: str,
    *,
    max_chars: int,
) -> str:
    """Keep the profile identity plus both ends of a bounded PDF body."""

    if max_chars <= 0:
        return ""
    normalized_page_text = (page_text or "").replace("\ufeff", "").strip()
    normalized_pdf_text = (pdf_text or "").replace("\ufeff", "").strip()
    if not normalized_pdf_text:
        return _bound_head_and_tail(normalized_page_text, max_chars)
    if not normalized_page_text:
        return _bound_head_and_tail(normalized_pdf_text, max_chars)

    separator = f"\n\n{_PROFILE_PDF_SECTION_LABEL}\n"
    page_budget = min(len(normalized_page_text), max_chars // 3)
    bounded_page_text = _bound_head_and_tail(normalized_page_text, page_budget)
    pdf_budget = max(0, max_chars - len(bounded_page_text) - len(separator))
    bounded_pdf_text = _bound_head_and_tail(normalized_pdf_text, pdf_budget)
    if not bounded_pdf_text:
        return bounded_page_text[:max_chars]
    return f"{bounded_page_text}{separator}{bounded_pdf_text}"[:max_chars]


def _document_base_url(tag: Tag, fallback_url: str) -> str:
    container = tag.find_parent(attrs={"data-crawl-frame-url": True})
    if not isinstance(container, Tag):
        return fallback_url
    return str(container.get("data-crawl-frame-url") or fallback_url).strip()


def _resolve_pdf_url(base_url: str, raw_url: str) -> str | None:
    absolute_url = urljoin(base_url, raw_url)
    parsed = urlparse(absolute_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.path.lower().endswith(".pdf"):
        return parsed._replace(fragment="").geturl()

    for key, values in parse_qs(parsed.query, keep_blank_values=False).items():
        if key.casefold() not in _PDF_QUERY_KEYS:
            continue
        for value in values:
            decoded_value = value
            for _ in range(2):
                next_value = unquote(decoded_value)
                if next_value == decoded_value:
                    break
                decoded_value = next_value
            target_url = urljoin(absolute_url, decoded_value)
            target = urlparse(target_url)
            if (
                target.scheme in {"http", "https"}
                and target.hostname
                and target.path.lower().endswith(".pdf")
            ):
                return target._replace(fragment="").geturl()
    return None


def _looks_like_pdf(content_type: str, content: bytes) -> bool:
    return content_type.lower() in _PDF_CONTENT_TYPES or b"%PDF-" in content[:1024]


def _extract_pdf_text(content: bytes) -> str:
    from app.services.document_extraction.pdf_converter import PdfConverter, StreamInfo

    return (
        PdfConverter()
        .convert(
            BytesIO(content),
            StreamInfo(mimetype="application/pdf", extension=".pdf"),
        )
        .markdown
    )


def _bound_head_and_tail(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= len(_TRUNCATION_MARKER):
        return text[:max_chars]
    available = max_chars - len(_TRUNCATION_MARKER)
    head_chars = available * 3 // 5
    tail_chars = available - head_chars
    return f"{text[:head_chars]}{_TRUNCATION_MARKER}{text[-tail_chars:]}"
