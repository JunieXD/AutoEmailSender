from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from app.modules.professors.public import (
    is_valid_professor_email,
    normalize_professor_email,
)
from app.services.html_text import html_to_text
from ..pages.tools import (
    PageSnapshot,
    normalize_navigable_url,
    normalize_obfuscated_email_tokens,
)


_EMAIL_CANDIDATE_PATTERN = re.compile(
    r"[A-Za-z0-9._%+-]+\s*@\s*[A-Za-z0-9-]+(?:\s*\.\s*[A-Za-z0-9-]+)+"
)
_EMBEDDED_DOCUMENT_URL_ATTRIBUTE = "data-crawl-frame-url"
_LINK_PRIORITY_TERMS = (
    "contact",
    "detail",
    "email",
    "information",
    "more",
    "personal",
    "profile",
    "个人",
    "信息",
    "更多",
    "简介",
    "联系",
    "详情",
)


@dataclass(frozen=True, slots=True)
class EmailEvidence:
    email: str
    context: str
    source_url: str
    source_kind: str


@dataclass(frozen=True, slots=True)
class ProfileLinkEvidence:
    link_id: int
    url: str
    label: str
    context: str


def extract_email_evidence(
    text: str,
    *,
    source_url: str,
    source_kind: str,
    context_chars: int = 180,
) -> tuple[EmailEvidence, ...]:
    normalized_text = normalize_obfuscated_email_tokens(text or "")
    evidence: list[EmailEvidence] = []
    seen: set[str] = set()
    for match in _EMAIL_CANDIDATE_PATTERN.finditer(normalized_text):
        raw_email = re.sub(r"\s+", "", match.group(0))
        email = normalize_professor_email(raw_email)
        if not email or not is_valid_professor_email(email) or email in seen:
            continue
        seen.add(email)
        start = max(0, match.start() - context_chars)
        end = min(len(normalized_text), match.end() + context_chars)
        context = " ".join(normalized_text[start:end].split())
        evidence.append(
            EmailEvidence(
                email=email,
                context=context,
                source_url=source_url,
                source_kind=source_kind,
            )
        )
    return tuple(evidence)


def extract_profile_document_email_evidence(
    snapshot: PageSnapshot,
) -> tuple[EmailEvidence, ...]:
    """Find readable email evidence in a browser-repaired profile document.

    Some profile templates put visible contact fields after ``</body>``.  The
    normal snapshot text intentionally follows the body, so inspect the
    already-cleaned/bounded HTML only as a late enrichment fallback.  The
    existing LLM selector still decides whether any discovered address belongs
    to the current teacher.
    """

    document_text = html_to_text(
        snapshot.html or "",
        include_document_fallback=True,
    )
    if not document_text:
        return ()
    return extract_email_evidence(
        document_text,
        source_url=snapshot.url,
        source_kind="profile_document",
    )


def extract_profile_link_evidence(
    snapshot: PageSnapshot,
    *,
    max_links: int = 80,
) -> tuple[ProfileLinkEvidence, ...]:
    soup = BeautifulSoup(snapshot.html or "", "html.parser")
    current_url = normalize_navigable_url(snapshot.url) or snapshot.url
    ranked: list[tuple[int, int, str, str, str]] = []
    seen: set[str] = set()
    for document_index, anchor in enumerate(soup.find_all("a", href=True)):
        raw_href = str(anchor.get("href") or "").strip()
        if not raw_href:
            continue
        absolute_url = normalize_navigable_url(
            raw_href,
            base_url=_document_base_url(anchor, snapshot.url),
        )
        if not absolute_url or absolute_url == current_url or absolute_url in seen:
            continue
        parsed = urlsplit(absolute_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        label = _anchor_label(anchor)
        context = _nearest_link_context(anchor, label)
        if not label and not context:
            continue
        seen.add(absolute_url)
        haystack = f"{label} {context} {parsed.path}".casefold()
        priority = sum(1 for term in _LINK_PRIORITY_TERMS if term in haystack)
        ranked.append((-priority, document_index, absolute_url, label, context))

    ranked.sort(key=lambda item: (item[0], item[1]))
    return tuple(
        ProfileLinkEvidence(
            link_id=index,
            url=url,
            label=label,
            context=context,
        )
        for index, (_priority, _document_index, url, label, context) in enumerate(
            ranked[:max_links],
            start=1,
        )
    )


def _anchor_label(anchor: Tag) -> str:
    label = " ".join(anchor.get_text(" ", strip=True).split())
    if label:
        return label[:160]
    image = anchor.find("img")
    if image is None:
        return ""
    return " ".join(str(image.get("alt") or image.get("title") or "").split())[:160]


def _nearest_link_context(anchor: Tag, label: str) -> str:
    node: Tag | None = anchor
    fallback = label
    for _ in range(5):
        parent = node.parent if node is not None else None
        if not isinstance(parent, Tag):
            break
        text = " ".join(parent.get_text(" ", strip=True).split())
        if text:
            fallback = text[:360]
        if len(text) >= max(12, len(label) + 4) and len(text) <= 360:
            return text
        node = parent
    return fallback


def resolve_profile_image_urls(
    snapshot: PageSnapshot, *, max_urls: int = 20
) -> tuple[tuple[str, str], ...]:
    soup = BeautifulSoup(snapshot.html or "", "html.parser")
    candidates: list[tuple[int, int, str, str]] = []
    seen: set[str] = set()
    for document_index, image in enumerate(soup.find_all("img")):
        raw_src = str(
            image.get("src")
            or image.get("data-src")
            or image.get("data-original")
            or ""
        ).strip()
        if not raw_src or raw_src.startswith("data:"):
            continue
        url = urljoin(_document_base_url(image, snapshot.url), raw_src)
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or url in seen:
            continue
        seen.add(url)
        context = _image_context(image)
        haystack = f"{url} {context}".casefold()
        priority = sum(1 for term in _LINK_PRIORITY_TERMS if term in haystack)
        candidates.append((-priority, document_index, url, context))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return tuple(
        (url, context) for _priority, _index, url, context in candidates[:max_urls]
    )


def _image_context(image: Tag) -> str:
    attributes = " ".join(
        str(image.get(name) or "") for name in ("alt", "title")
    ).strip()
    nearby_text = _nearby_image_text(image)
    return " ".join(part for part in (attributes, nearby_text) if part)[:320]


def _document_base_url(element: Tag, fallback_url: str) -> str:
    container = element.find_parent(attrs={_EMBEDDED_DOCUMENT_URL_ATTRIBUTE: True})
    if not isinstance(container, Tag):
        return fallback_url
    raw_url = str(container.get(_EMBEDDED_DOCUMENT_URL_ATTRIBUTE) or "").strip()
    return normalize_navigable_url(raw_url, base_url=fallback_url) or fallback_url


def _nearby_image_text(image: Tag) -> str:
    node: Tag | None = image
    for _ in range(4):
        parent = node.parent if node is not None else None
        if not isinstance(parent, Tag):
            break
        parent_text = " ".join(parent.get_text(" ", strip=True).split())
        if 0 < len(parent_text) <= 240:
            return parent_text
        previous = parent.find_previous_sibling()
        if isinstance(previous, Tag):
            previous_text = " ".join(previous.get_text(" ", strip=True).split())
            if 0 < len(previous_text) <= 240:
                return previous_text
        node = parent
    return ""
