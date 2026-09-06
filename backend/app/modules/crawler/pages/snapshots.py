from __future__ import annotations

from collections.abc import Sequence
from html import escape
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

from app.services.beautiful_soup import is_comment, parse_html
from app.services.html_text import html_to_text

from .chunking import MAX_CRAWL_HTML_CHARS
from .payloads import PageSnapshot as PageSnapshot, _clean_optional as _clean_optional

if TYPE_CHECKING:
    from bs4 import BeautifulSoup

MAX_TEXT_CHARS = 12000


MAX_LINKS = 200


INVALID_PROFILE_PAGE_MARKERS = (
    "{{name}}",
    "{{email}}",
    "{{data}}",
    "FineCMS error",
    "SQL syntax",
)


CLIENT_ENCRYPTED_PROFILE_FIELD_MARKERS = ("_tsites_encrypt_field",)


DYNAMIC_TEACHER_DIRECTORY_MARKERS = (
    "search_teacher.js",
    "_wp3services/generalquery?queryobj=articles",
    "queryobj=articles",
    "_wp3services/generalquery?queryobj=teacherhome",
    "queryobj=teacherhome",
)


def html_to_snapshot(
    url: str,
    html: str,
    fetch_method: str,
    *,
    embedded_documents: Sequence[tuple[str, str]] = (),
) -> PageSnapshot:
    """Turn one page and optional frame documents into a bounded snapshot.

    The old implementation sliced raw HTML before parsing. Excel-exported
    faculty pages put their contact row after a large amount of invisible
    style/VML markup, so that slice could remove the only email. We now remove
    non-visible markup first, extract text and links from the complete cleaned
    documents, and only then apply the existing snapshot budget.
    """

    documents = [(url, html)] + [
        (document_url, document_html)
        for document_url, document_html in embedded_documents
        if document_html
    ]
    cleaned_documents = [
        (document_url, _clean_snapshot_soup(document_html))
        for document_url, document_html in documents
    ]
    has_client_encrypted_profile_fields = any(
        any(
            marker in document_html for marker in CLIENT_ENCRYPTED_PROFILE_FIELD_MARKERS
        )
        for _document_url, document_html in documents
    )
    has_dynamic_teacher_directory_markers = any(
        any(
            marker in document_html.lower()
            for marker in DYNAMIC_TEACHER_DIRECTORY_MARKERS
        )
        for _document_url, document_html in documents
    )
    has_invalid_profile_page_markers = any(
        any(
            marker.lower() in document_html.lower()
            for marker in INVALID_PROFILE_PAGE_MARKERS
        )
        for _document_url, document_html in documents
    )
    main_soup = cleaned_documents[0][1]
    title = _clean_optional(
        main_soup.title.get_text(" ", strip=True) if main_soup.title else None
    )
    if not title:
        for _document_url, document_soup in cleaned_documents[1:]:
            if document_soup.title:
                title = _clean_optional(document_soup.title.get_text(" ", strip=True))
                if title:
                    break

    text_parts = [
        html_to_text(str(document_soup))
        for _document_url, document_soup in cleaned_documents
    ]
    text = "\n\n".join(part for part in text_parts if part)
    text = text.replace("\ufeff", "").strip()[:MAX_TEXT_CHARS]

    links: list[str] = []
    seen_links: set[str] = set()
    for document_url, document_soup in cleaned_documents:
        for tag in document_soup.find_all("a", href=True):
            link = urljoin(document_url, str(tag["href"]).strip())
            parsed = urlparse(link)
            if parsed.scheme not in {"http", "https"} or link in seen_links:
                continue
            seen_links.add(link)
            links.append(link)
            if len(links) >= MAX_LINKS:
                break
        if len(links) >= MAX_LINKS:
            break

    serialized_documents = [str(main_soup)]
    for document_url, document_soup in cleaned_documents[1:]:
        serialized_documents.append(
            '<section data-crawl-frame-url="{}">{}</section>'.format(
                escape(document_url, quote=True),
                document_soup,
            )
        )
    bounded_html = _bound_snapshot_html("\n".join(serialized_documents))

    return PageSnapshot(
        url=url,
        title=title,
        text=text,
        html=bounded_html,
        links=links,
        fetch_method=fetch_method,
        status="succeeded",
        suspicious_empty=not text,
        has_client_encrypted_profile_fields=has_client_encrypted_profile_fields,
        has_dynamic_teacher_directory_markers=has_dynamic_teacher_directory_markers,
        has_invalid_profile_page_markers=has_invalid_profile_page_markers,
    )


def _clean_snapshot_soup(html: str) -> BeautifulSoup:
    soup = parse_html(html or "")
    for tag in soup.find_all(True):
        attributes = " ".join(
            f"{attribute}={attribute_value}"
            for attribute, attribute_value in tag.attrs.items()
        )
        if any(
            marker in attributes for marker in CLIENT_ENCRYPTED_PROFILE_FIELD_MARKERS
        ):
            tag.decompose()
    for tag in soup(["script", "style", "noscript", "template", "noframes"]):
        tag.decompose()
    for comment in soup.find_all(string=is_comment):
        comment.extract()
    return soup


def _bound_snapshot_html(html: str) -> str:
    if len(html) <= MAX_CRAWL_HTML_CHARS:
        return html
    marker = '\n<div data-crawl-truncated="true"></div>\n'
    available = max(0, MAX_CRAWL_HTML_CHARS - len(marker))
    head_size = available // 2
    tail_size = available - head_size
    return html[:head_size] + marker + (html[-tail_size:] if tail_size else "")
