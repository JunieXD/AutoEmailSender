from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from math import ceil
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag


_STRUCTURE_BOUNDARY_SENTINEL = "\u241eAES_BLOCK_BOUNDARY\u241e"


@dataclass(frozen=True)
class ChunkingConfig:
    target_tokens: int = 2000
    soft_max_tokens: int = 2800
    hard_max_tokens: int = 3200
    overlap_tokens: int = 180
    min_split_tokens: int = 100
    max_split_depth: int = 7
    retry_split_target_tokens: int = 200
    retry_split_max_parts: int = 10
    retry_split_overlap_tokens: int = 15
    single_chunk_max_tokens: int = 2200
    min_balanced_target_tokens: int = 1200
    max_balanced_target_tokens: int = 2200
    preserve_structure_boundaries: bool = True
    structure_block_max_tokens: int = 600
    structure_block_max_links: int = 12


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
        elif tag == "iframe":
            attributes = dict(attrs)
            src = attributes.get("src")
            if src:
                label = _normalize_space(
                    attributes.get("title")
                    or attributes.get("name")
                    or "嵌入页面"
                )
                self.parts.append(f"\n[iframe: {label}]({urljoin(self.base_url, src)})\n")

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
        return _normalize_enriched_text("".join(self.parts))


def _normalize_space(value: str) -> str:
    return " ".join(value.split())


def _normalize_lines(value: str) -> str:
    lines = [_normalize_space(line) for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def _normalize_enriched_text(value: str) -> str:
    sections = [_normalize_lines(section) for section in value.split(_STRUCTURE_BOUNDARY_SENTINEL)]
    return "\n\n".join(section for section in sections if section)


def _normalize_chunk_content(value: str) -> str:
    paragraphs = [_normalize_lines(part) for part in re.split(r"\n\s*\n", value)]
    return "\n\n".join(part for part in paragraphs if part)


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


def html_to_link_enriched_text(
    source_url: str,
    html: str,
    fallback_text: str,
    *,
    preserve_structure_boundaries: bool = True,
    structure_block_max_tokens: int = 600,
    structure_block_max_links: int = 12,
) -> str:
    prepared_html = html or ""
    if preserve_structure_boundaries and prepared_html:
        prepared_html = _inject_structure_boundaries(
            prepared_html,
            max_tokens=structure_block_max_tokens,
            max_links=structure_block_max_links,
        )
    parser = _LinkTextHTMLParser(source_url)
    parser.feed(prepared_html)
    enriched = parser.text()
    return enriched or _normalize_lines(fallback_text)


def _inject_structure_boundaries(html: str, *, max_tokens: int, max_links: int) -> str:
    """Add temporary separators around small, reliable DOM blocks.

    The separators become blank lines in the link-enriched text. No tags,
    attributes, or other HTML metadata are exposed to the model.
    """

    soup = BeautifulSoup(html, "html.parser")
    candidates: list[tuple[int, Tag]] = []

    for tag in soup.find_all("tr"):
        if _is_eligible_structure_block(tag, max_tokens=max_tokens, max_links=max_links):
            candidates.append((0, tag))

    for tag in soup.find_all(["div", "section", "dl"]):
        if _is_repeated_card(tag) and _is_eligible_structure_block(
            tag,
            max_tokens=max_tokens,
            max_links=max_links,
        ):
            candidates.append((1, tag))

    for tag in soup.find_all("article"):
        if _is_eligible_structure_block(tag, max_tokens=max_tokens, max_links=max_links):
            candidates.append((2, tag))

    for tag in soup.find_all("li"):
        if tag.find("li") is None and _is_eligible_structure_block(
            tag,
            max_tokens=max_tokens,
            max_links=max_links,
        ):
            candidates.append((3, tag))

    selected: list[Tag] = []
    for _priority, tag in sorted(candidates, key=lambda item: item[0]):
        if any(_tags_overlap(tag, existing) for existing in selected):
            continue
        selected.append(tag)

    for tag in selected:
        boundary = f"\n{_STRUCTURE_BOUNDARY_SENTINEL}\n"
        tag.insert_before(NavigableString(boundary))
        tag.insert_after(NavigableString(boundary))
    return str(soup)


def _is_eligible_structure_block(tag: Tag, *, max_tokens: int, max_links: int) -> bool:
    if any(parent.name in _LinkTextHTMLParser._SKIPPED_TAGS for parent in tag.parents if isinstance(parent, Tag)):
        return False
    text = _normalize_space(tag.get_text(" ", strip=True))
    if not text or estimate_tokens(text) > max_tokens:
        return False
    return len(tag.find_all("a", href=True)) <= max_links


def _is_repeated_card(tag: Tag) -> bool:
    classes = tuple(tag.get("class") or ())
    if not classes or tag.parent is None:
        return False
    signature = (tag.name, classes)
    matching_siblings = 0
    for sibling in tag.parent.find_all(recursive=False):
        if not isinstance(sibling, Tag):
            continue
        if (sibling.name, tuple(sibling.get("class") or ())) == signature:
            matching_siblings += 1
            if matching_siblings >= 3:
                return True
    return False


def _tags_overlap(left: Tag, right: Tag) -> bool:
    if left is right:
        return True
    return any(parent is right for parent in left.parents) or any(parent is left for parent in right.parents)


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
    enriched = html_to_link_enriched_text(
        source_url,
        html,
        text,
        preserve_structure_boundaries=selected_config.preserve_structure_boundaries,
        structure_block_max_tokens=selected_config.structure_block_max_tokens,
        structure_block_max_links=selected_config.structure_block_max_links,
    )
    page_fingerprint = fingerprint_page(enriched)
    target_tokens = balanced_target_tokens(enriched, selected_config)
    units, separator, strict_overlap = _content_units(enriched, selected_config)
    chunks: list[str] = []
    current: list[str] = []
    for unit in units:
        candidate = separator.join([*current, unit]) if current else unit
        if current and estimate_tokens(candidate) > target_tokens:
            chunks.append(separator.join(current))
            current = _select_overlap_tail(
                current,
                selected_config.overlap_tokens,
                strict=strict_overlap,
            )
        current.append(unit)
        while estimate_tokens(separator.join(current)) > selected_config.hard_max_tokens and len(current) > 1:
            midpoint = max(1, len(current) // 2)
            chunks.append(separator.join(current[:midpoint]))
            current = _select_overlap_tail(
                current[:midpoint],
                selected_config.overlap_tokens,
                strict=strict_overlap,
            ) + current[midpoint:]
    if current:
        chunks.append(separator.join(current))
    chunks = _merge_small_tail_chunks(chunks, selected_config)

    drafts: list[PageChunkDraft] = []
    for index, content in enumerate(chunks):
        normalized_content = _normalize_chunk_content(content)
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

def _merge_small_tail_chunks(chunks: list[str], config: ChunkingConfig) -> list[str]:
    merged = list(chunks)
    while len(merged) > 1:
        tail_tokens = estimate_tokens(merged[-1])
        combined = "\n\n".join([merged[-2], merged[-1]])
        if tail_tokens >= config.min_balanced_target_tokens:
            break
        if estimate_tokens(combined) > config.hard_max_tokens:
            break
        merged[-2:] = [combined]
    return merged

def balanced_target_tokens(content: str, config: ChunkingConfig | None = None) -> int:
    selected_config = config or ChunkingConfig()
    total_tokens = estimate_tokens(content)
    if total_tokens <= selected_config.single_chunk_max_tokens:
        return max(total_tokens, 1)
    chunk_count = max(1, ceil(total_tokens / selected_config.target_tokens))
    balanced = ceil(total_tokens / chunk_count)
    return min(
        selected_config.max_balanced_target_tokens,
        max(selected_config.min_balanced_target_tokens, balanced),
    )


def _content_units(content: str, config: ChunkingConfig) -> tuple[list[str], str, bool]:
    paragraphs = [_normalize_lines(part) for part in re.split(r"\n\s*\n", content)]
    paragraphs = [part for part in paragraphs if part]
    if len(paragraphs) <= 1:
        return content.splitlines(), "\n", False

    units: list[str] = []
    for paragraph in paragraphs:
        if estimate_tokens(paragraph) <= config.hard_max_tokens:
            units.append(paragraph)
            continue
        units.extend(_pack_oversized_lines(paragraph.splitlines(), config.hard_max_tokens))
    return units, "\n\n", True


def _pack_oversized_lines(lines: list[str], hard_max_tokens: int) -> list[str]:
    packed: list[str] = []
    current: list[str] = []
    for line in lines:
        candidate = "\n".join([*current, line]) if current else line
        if current and estimate_tokens(candidate) > hard_max_tokens:
            packed.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        packed.append("\n".join(current))
    return packed


def _select_overlap_tail(lines: list[str], overlap_tokens: int, *, strict: bool) -> list[str]:
    if strict:
        return _strict_overlap_tail(lines, overlap_tokens)
    return _overlap_tail(lines, overlap_tokens)


def split_chunk_content(
    *,
    source_url: str,
    content: str,
    parent_chunk_id: str,
    page_fingerprint: str,
    split_depth: int,
    config: ChunkingConfig | None = None,
    split_reason: str | None = None,
) -> list[PageChunkDraft]:
    selected_config = config or ChunkingConfig()
    if estimate_tokens(content) <= selected_config.min_split_tokens:
        return []
    lines, separator, _strict_overlap = _content_units(content, selected_config)
    if len(lines) < 2:
        lines = content.splitlines()
        separator = "\n"
    if len(lines) < 2:
        return []
    child_groups = (
        _split_retry_candidate_dense_lines(lines, content, selected_config)
        if _is_candidate_dense_split(split_reason)
        else _split_binary_lines(lines, content, selected_config)
    )
    drafts: list[PageChunkDraft] = []
    for index, child_lines in enumerate(child_groups):
        normalized = _normalize_chunk_content(separator.join(child_lines))
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



def _is_candidate_dense_split(split_reason: str | None) -> bool:
    return split_reason in {"too_many_candidates", "candidate_count_exceeded"}


def _split_binary_lines(lines: list[str], content: str, config: ChunkingConfig) -> list[list[str]]:
    midpoint = max(1, len(lines) // 2)
    left_lines = lines[:midpoint]
    overlap_tokens = min(
        _dynamic_overlap_tokens(content, config),
        config.retry_split_overlap_tokens,
    )
    return [left_lines, [*_strict_overlap_tail(left_lines, overlap_tokens), *lines[midpoint:]]]


def _split_retry_candidate_dense_lines(lines: list[str], content: str, config: ChunkingConfig) -> list[list[str]]:
    content_tokens = estimate_tokens(content)
    target_tokens = max(config.min_split_tokens, config.retry_split_target_tokens)
    part_count = min(
        max(2, config.retry_split_max_parts),
        max(2, ceil(content_tokens / target_tokens)),
        len(lines),
    )
    overlap_tokens = min(config.overlap_tokens, config.retry_split_overlap_tokens)
    groups: list[list[str]] = []
    bounds = [
        (round(index * len(lines) / part_count), round((index + 1) * len(lines) / part_count))
        for index in range(part_count)
    ]
    for index, (start, end) in enumerate(bounds):
        child_lines = lines[start:end]
        if index > 0:
            previous_start, previous_end = bounds[index - 1]
            child_lines = [
                *_strict_overlap_tail(lines[previous_start:previous_end], overlap_tokens),
                *child_lines,
            ]
        if child_lines:
            groups.append(child_lines)
    return groups

def _dynamic_overlap_tokens(content: str, config: ChunkingConfig) -> int:
    content_tokens = estimate_tokens(content)
    if content_tokens <= config.min_split_tokens * 2:
        return min(config.overlap_tokens, max(0, content_tokens // 8))
    if content_tokens <= config.min_split_tokens * 4:
        return min(config.overlap_tokens, max(0, content_tokens // 6))
    return config.overlap_tokens

def _strict_overlap_tail(lines: list[str], overlap_tokens: int) -> list[str]:
    selected: list[str] = []
    total = 0
    for line in reversed(lines):
        line_tokens = estimate_tokens(line)
        if total + line_tokens > overlap_tokens:
            break
        total += line_tokens
        selected.append(line)
    return list(reversed(selected))

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
