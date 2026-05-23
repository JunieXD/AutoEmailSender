from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin


@dataclass(frozen=True)
class ChunkingConfig:
    target_tokens: int = 2000
    soft_max_tokens: int = 2800
    hard_max_tokens: int = 3200
    overlap_tokens: int = 180
    min_split_tokens: int = 500
    max_split_depth: int = 4


@dataclass(frozen=True)
class PageChunkDraft:
    chunk_id: str
    source_url: str
    page_fingerprint: str
    chunk_index: int
    chunk_hash: str
    content: str
    token_estimate: int
    text_start_offset: int | None
    text_end_offset: int | None
    overlap_prefix: bool
    overlap_suffix: bool
    split_depth: int = 0
    parent_chunk_id: str | None = None


class _LinkTextHTMLParser(HTMLParser):
    _SKIPPED_TAGS = {"script", "style", "noscript", "svg"}
    _BLOCK_TAGS = {"p", "div", "li", "tr", "section", "article", "h1", "h2", "h3", "h4", "td", "th", "main"}

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts: list[str] = []
        self.skip_depth = 0
        self.current_href: str | None = None
        self.current_anchor: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIPPED_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            self.current_href = urljoin(self.base_url, href) if href else None
            self.current_anchor = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIPPED_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == "a" and self.current_href:
            label = _normalize_space("".join(self.current_anchor))
            if label:
                self.parts.append(f"[{label}]({self.current_href})")
            self.current_href = None
            self.current_anchor = []
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.current_href is not None:
            self.current_anchor.append(data)
        else:
            self.parts.append(data)

    def text(self) -> str:
        return _normalize_lines("".join(self.parts))


def _normalize_space(value: str) -> str:
    return " ".join(value.split())


def _normalize_lines(value: str) -> str:
    lines = [_normalize_space(line) for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def estimate_tokens(value: str) -> int:
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", value))
    ascii_words = len(re.findall(r"[A-Za-z0-9_@./:-]+", value))
    other_chars = max(len(value) - chinese_chars, 0)
    return max(1, chinese_chars + ascii_words + other_chars // 4)


def fingerprint_page(value: str) -> str:
    normalized = _normalize_space(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def chunk_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def html_to_link_enriched_text(source_url: str, html: str, fallback_text: str) -> str:
    parser = _LinkTextHTMLParser(source_url)
    parser.feed(html or "")
    enriched = parser.text()
    return enriched or _normalize_lines(fallback_text)


def build_page_chunks(
    *,
    source_url: str,
    html: str,
    text: str,
    config: ChunkingConfig | None = None,
    parent_chunk_id: str | None = None,
    split_depth: int = 0,
) -> list[PageChunkDraft]:
    selected_config = config or ChunkingConfig()
    enriched = html_to_link_enriched_text(source_url, html, text)
    page_fingerprint = fingerprint_page(enriched)
    lines = enriched.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    for line in lines:
        candidate = "\n".join([*current, line]) if current else line
        if current and estimate_tokens(candidate) > selected_config.target_tokens:
            chunks.append("\n".join(current))
            current = _overlap_tail(current, selected_config.overlap_tokens)
        current.append(line)
        while estimate_tokens("\n".join(current)) > selected_config.hard_max_tokens and len(current) > 1:
            midpoint = max(1, len(current) // 2)
            chunks.append("\n".join(current[:midpoint]))
            current = _overlap_tail(current[:midpoint], selected_config.overlap_tokens) + current[midpoint:]
    if current:
        chunks.append("\n".join(current))

    drafts: list[PageChunkDraft] = []
    for index, content in enumerate(chunks):
        normalized_content = _normalize_lines(content)
        drafts.append(
            PageChunkDraft(
                chunk_id=_build_chunk_id(page_fingerprint, index, parent_chunk_id),
                source_url=source_url,
                page_fingerprint=page_fingerprint,
                chunk_index=index,
                chunk_hash=chunk_hash(normalized_content),
                content=normalized_content,
                token_estimate=estimate_tokens(normalized_content),
                text_start_offset=None,
                text_end_offset=None,
                overlap_prefix=index > 0,
                overlap_suffix=index < len(chunks) - 1,
                split_depth=split_depth,
                parent_chunk_id=parent_chunk_id,
            )
        )
    return drafts


def split_chunk_content(
    *,
    source_url: str,
    content: str,
    parent_chunk_id: str,
    page_fingerprint: str,
    split_depth: int,
    config: ChunkingConfig | None = None,
) -> list[PageChunkDraft]:
    selected_config = config or ChunkingConfig()
    if estimate_tokens(content) <= selected_config.min_split_tokens:
        return []
    lines = content.splitlines()
    midpoint = max(1, len(lines) // 2)
    left_lines = lines[:midpoint]
    right_lines = [*_overlap_tail(left_lines, selected_config.overlap_tokens), *lines[midpoint:]]
    drafts: list[PageChunkDraft] = []
    for index, child_lines in enumerate((left_lines, right_lines)):
        normalized = _normalize_lines("\n".join(child_lines))
        if not normalized:
            continue
        drafts.append(
            PageChunkDraft(
                chunk_id=f"{parent_chunk_id}.{index + 1}",
                source_url=source_url,
                page_fingerprint=page_fingerprint,
                chunk_index=index,
                chunk_hash=chunk_hash(normalized),
                content=normalized,
                token_estimate=estimate_tokens(normalized),
                text_start_offset=None,
                text_end_offset=None,
                overlap_prefix=index > 0,
                overlap_suffix=index == 0,
                split_depth=split_depth,
                parent_chunk_id=parent_chunk_id,
            )
        )
    return drafts


def _overlap_tail(lines: list[str], overlap_tokens: int) -> list[str]:
    selected: list[str] = []
    total = 0
    for line in reversed(lines):
        total += estimate_tokens(line)
        selected.append(line)
        if total >= overlap_tokens:
            break
    return list(reversed(selected))


def _build_chunk_id(page_fingerprint: str, index: int, parent_chunk_id: str | None) -> str:
    prefix = parent_chunk_id or page_fingerprint[:16]
    return f"{prefix}.{index + 1}"
