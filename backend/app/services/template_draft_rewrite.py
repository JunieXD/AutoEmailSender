from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape

from bs4 import BeautifulSoup, NavigableString, Tag

from app.services.rich_text import RichTextRenderResult, normalize_email_html

PLACEHOLDER_PATTERN = re.compile(r"\{\{[a-zA-Z0-9_]+\}\}")

SEGMENT_TAG_NAMES = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "table"}
CJK_FONT_HINTS = (
    "宋",
    "仿宋",
    "楷",
    "黑体",
    "Hei",
    "SimSun",
    "NSimSun",
    "Songti",
    "STSong",
    "MingLiU",
    "PMingLiU",
    "Noto Serif SC",
    "Noto Sans CJK",
    "Source Han Serif",
    "Source Han Sans",
    "PingFang",
    "Hiragino Sans GB",
    "Microsoft YaHei",
)
FONT_FAMILY_STYLE_KEYS = {
    "font-family",
    "mso-fareast-font-family",
    "mso-ascii-font-family",
    "mso-hansi-font-family",
    "mso-bidi-font-family",
}


@dataclass(slots=True)
class DraftRewriteFontStyle:
    font_family: str | None
    font_size: str | None


@dataclass(slots=True)
class DraftRewriteStyleSpan:
    text: str
    marks: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DraftRewriteLocalStyleSpan:
    text: str
    marks: list[str] = field(default_factory=list)
    font_family: str | None = None
    font_size: str | None = None


@dataclass(slots=True)
class DraftRewriteSegmentStyle:
    base_style: DraftRewriteFontStyle
    local_spans: list[DraftRewriteLocalStyleSpan] = field(default_factory=list)


@dataclass(slots=True)
class DraftRewriteSourceBlock:
    segment_id: str
    type: str
    text: str
    style_spans: list[DraftRewriteStyleSpan] = field(default_factory=list)
    locked: bool = False
    html_fragment: str | None = None


@dataclass(slots=True)
class DraftRewriteDocument:
    html: str
    blocks: list[DraftRewriteSourceBlock]


def build_draft_rewrite_document(html: str, context: dict[str, str]) -> DraftRewriteDocument:
    soup = BeautifulSoup(html.strip(), "html.parser")
    _render_template_text_nodes(soup, context)
    blocks: list[DraftRewriteSourceBlock] = []

    for index, element in enumerate(_iter_segment_elements(soup), start=1):
        segment_id = f"seg_{index}"
        html_fragment = str(element)
        if element.name == "table":
            blocks.append(
                DraftRewriteSourceBlock(
                    segment_id=segment_id,
                    type="table",
                    text=element.get_text(" ", strip=True),
                    locked=True,
                    html_fragment=html_fragment,
                ),
            )
            continue

        text_parts: list[str] = []
        style_spans: list[DraftRewriteStyleSpan] = []
        for text_node in list(element.find_all(string=True, recursive=True)):
            if not isinstance(text_node, NavigableString):
                continue
            rendered_text = str(text_node)
            if not rendered_text.strip():
                continue
            text_parts.append(rendered_text)
            marks = _collect_marks(text_node, element)
            if marks:
                style_spans.append(
                    DraftRewriteStyleSpan(
                        text=rendered_text,
                        marks=marks,
                    ),
                )

        blocks.append(
            DraftRewriteSourceBlock(
                segment_id=segment_id,
                type=_segment_type(element),
                text="".join(text_parts),
                style_spans=style_spans,
                locked=_should_lock_segment(index, element, "".join(text_parts)),
                html_fragment=html_fragment,
            ),
        )

    return DraftRewriteDocument(html=str(soup), blocks=blocks)


def render_draft_template_text(value: str | None, context: dict[str, str]) -> str:
    return _render_template_text(value or "", context)


def _iter_segment_elements(soup: BeautifulSoup) -> list[Tag]:
    elements: list[Tag] = []
    for tag in soup.find_all(SEGMENT_TAG_NAMES):
        if not isinstance(tag, Tag):
            continue
        if tag.name == "table":
            elements.append(tag)
            continue
        if tag.name == "li" and tag.find(["p", "h1", "h2", "h3", "h4", "h5", "h6"]):
            continue
        elements.append(tag)
    return elements


def _segment_type(element: Tag) -> str:
    if element.name == "table":
        return "table"
    if element.name == "li":
        return "list_item"
    if element.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return "heading"
    return "paragraph"


def _collect_marks(text_node: NavigableString, container: Tag) -> list[str]:
    marks: list[str] = []
    for parent in text_node.parents:
        if not isinstance(parent, Tag):
            continue
        if parent is container:
            continue
        if parent.name in {"strong", "b"} and "strong" not in marks:
            marks.append("strong")
        if parent.name in {"u"} and "underline" not in marks:
            marks.append("underline")
        if parent.name in {"em", "i"} and "emphasis" not in marks:
            marks.append("emphasis")
    return marks

def _should_lock_segment(index: int, element: Tag, text: str) -> bool:
    if index != 1:
        return False
    if element.name not in {"p", "h1", "h2", "h3", "h4", "h5", "h6"}:
        return False
    normalized = re.sub(r"\s+", "", text)
    if len(normalized) > 40:
        return False
    return (
        (normalized.startswith("尊敬的") or normalized.startswith("敬爱的") or normalized.startswith("亲爱的"))
        and (normalized.endswith("：") or normalized.endswith(":") or normalized.endswith("！") or normalized.endswith("!"))
    )


def select_dominant_font_and_size(html: str) -> DraftRewriteFontStyle:
    soup = BeautifulSoup(html.strip(), "html.parser")
    counts: dict[tuple[str | None, str | None], int] = {}
    first_seen: dict[tuple[str | None, str | None], int] = {}
    order = 0

    for text_node in soup.find_all(string=True):
        if not isinstance(text_node, NavigableString):
            continue
        text = str(text_node).strip()
        if not text:
            continue
        family = _resolve_effective_font_family(text_node)
        size = _resolve_effective_font_size(text_node)
        key = (family, size)
        if key == (None, None):
            continue
        counts[key] = counts.get(key, 0) + len(text)
        if key not in first_seen:
            first_seen[key] = order
        order += 1

    if not counts:
        return DraftRewriteFontStyle(font_family=None, font_size=None)

    winner = max(counts, key=lambda key: (counts[key], -first_seen[key]))
    return DraftRewriteFontStyle(font_family=winner[0], font_size=winner[1])


def apply_draft_rewrite_replacements(
    document: DraftRewriteDocument,
    replacements: list[dict[str, object]],
) -> RichTextRenderResult:
    soup = BeautifulSoup(document.html, "html.parser")
    elements = _iter_segment_elements(soup)
    block_map = {block.segment_id: block for block in document.blocks}
    element_map = {block.segment_id: element for block, element in zip(document.blocks, elements)}
    applied_count = 0

    for replacement in replacements:
        if not isinstance(replacement, dict):
            continue
        segment_id = replacement.get("segment_id")
        runs = replacement.get("runs")
        if not isinstance(segment_id, str) or not isinstance(runs, list):
            continue
        block = block_map.get(segment_id)
        element = element_map.get(segment_id)
        if block is None or element is None or block.type == "table" or block.locked:
            continue

        segment_style = _resolve_segment_style(element)
        fragment_html = "".join(
            _render_draft_run(run, segment_style.local_spans)
            for run in runs
            if isinstance(run, dict)
        )
        fragment = BeautifulSoup(f"<div>{fragment_html}</div>", "html.parser")
        element.clear()
        _apply_segment_base_font_style(element, segment_style.base_style)
        for child in list(fragment.div.contents if fragment.div else []):
            element.append(child)
        applied_count += 1

    if applied_count == 0:
        raise ValueError("模型未返回可用改写内容")

    for block, element in zip(document.blocks, elements):
        if block.type != "table" and not block.locked:
            continue
        if not block.html_fragment or element is None:
            continue
        original_fragment = BeautifulSoup(block.html_fragment, "html.parser")
        original_root = next((node for node in original_fragment.contents if isinstance(node, Tag)), None)
        if original_root is not None:
            element.replace_with(original_root)

    return normalize_email_html(str(soup))


def _render_template_text_nodes(soup: BeautifulSoup, context: dict[str, str]) -> None:
    for text_node in list(soup.find_all(string=True)):
        if not isinstance(text_node, NavigableString):
            continue
        rendered_text = _render_template_text(str(text_node), context)
        if rendered_text != str(text_node):
            text_node.replace_with(rendered_text)


def _render_template_text(text: str, context: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(0)[2:-2].strip()
        value = context.get(key)
        if value is None or value == "":
            return match.group(0)
        return value

    return PLACEHOLDER_PATTERN.sub(replace, text)


def _render_draft_run(
    run: dict[str, object],
    local_spans: list[DraftRewriteLocalStyleSpan] | None = None,
) -> str:
    raw_text = str(run.get("text", ""))
    raw_marks = run.get("marks")
    marks = raw_marks if isinstance(raw_marks, list) else []
    local_spans = [
        span
        for span in (local_spans or [])
        if span.font_family or span.font_size or any(mark not in marks for mark in span.marks)
    ]
    if local_spans:
        return "".join(
            _render_text_piece(text, marks, local_style)
            for text, local_style in _split_text_by_local_styles(raw_text, local_spans)
        )
    return _render_text_piece(raw_text, marks, None)


def _render_text_piece(
    text: str,
    marks: list[object],
    local_style: DraftRewriteLocalStyleSpan | None,
) -> str:
    rendered = escape(text)
    merged_marks = _merge_marks(marks, local_style.marks if local_style else [])
    for mark in merged_marks:
        if mark == "strong":
            rendered = f"<strong>{rendered}</strong>"
        elif mark == "underline":
            rendered = f"<u>{rendered}</u>"
        elif mark == "emphasis":
            rendered = f"<em>{rendered}</em>"

    if local_style and (local_style.font_family or local_style.font_size):
        style = _merge_font_style(
            "",
            DraftRewriteFontStyle(
                font_family=local_style.font_family,
                font_size=local_style.font_size,
            ),
        )
        if style:
            rendered = f'<span style="{escape(style, quote=True)}">{rendered}</span>'

    return rendered


def _merge_marks(primary: list[object], secondary: list[str]) -> list[str]:
    merged: list[str] = []
    for mark in [*primary, *secondary]:
        if mark in {"strong", "underline", "emphasis"} and mark not in merged:
            merged.append(str(mark))
    return merged


def _split_text_by_local_styles(
    text: str,
    local_spans: list[DraftRewriteLocalStyleSpan],
) -> list[tuple[str, DraftRewriteLocalStyleSpan | None]]:
    usable_spans = [
        span
        for span in sorted(local_spans, key=lambda item: len(item.text), reverse=True)
        if span.text
    ]
    if not usable_spans:
        return [(text, None)]

    pieces: list[tuple[str, DraftRewriteLocalStyleSpan | None]] = []
    cursor = 0
    while cursor < len(text):
        next_index: int | None = None
        next_span: DraftRewriteLocalStyleSpan | None = None
        for span in usable_spans:
            index = text.find(span.text, cursor)
            if index < 0:
                continue
            if (
                next_index is None
                or index < next_index
                or (index == next_index and len(span.text) > len(next_span.text if next_span else ""))
            ):
                next_index = index
                next_span = span

        if next_index is None or next_span is None:
            pieces.append((text[cursor:], None))
            break

        if next_index > cursor:
            pieces.append((text[cursor:next_index], None))
        pieces.append((text[next_index : next_index + len(next_span.text)], next_span))
        cursor = next_index + len(next_span.text)

    if not pieces:
        return [(text, None)]
    return pieces


def _resolve_segment_style(element: Tag) -> DraftRewriteSegmentStyle:
    base_style = _resolve_declared_block_font_style(element)
    if base_style.font_family is None or base_style.font_size is None:
        inferred_style = _infer_segment_base_font_style(element)
        base_style = DraftRewriteFontStyle(
            font_family=base_style.font_family or inferred_style.font_family,
            font_size=base_style.font_size or inferred_style.font_size,
        )
    return DraftRewriteSegmentStyle(
        base_style=base_style,
        local_spans=_collect_local_style_spans(element, base_style),
    )


def _resolve_declared_block_font_style(element: Tag) -> DraftRewriteFontStyle:
    family: str | None = None
    size: str | None = None
    text = element.get_text("", strip=True)

    for tag in _iter_segment_style_scope(element):
        if family is None:
            families = _extract_font_family_candidates(
                tag,
                prefer_fareast=_contains_cjk_char(text),
            )
            if families:
                family = _choose_font_family_for_text(text, families)
        if size is None:
            size = _extract_font_size(tag)
        if family is not None and size is not None:
            break

    return DraftRewriteFontStyle(font_family=family, font_size=size)


def _iter_segment_style_scope(element: Tag) -> list[Tag]:
    tags = [element]
    for parent in element.parents:
        if not isinstance(parent, Tag):
            continue
        if parent.name == "table":
            break
        tags.append(parent)
    return tags


def _infer_segment_base_font_style(element: Tag) -> DraftRewriteFontStyle:
    spans = _collect_text_style_spans(element)
    styled_spans = [
        span
        for span in spans
        if span.font_family is not None or span.font_size is not None
    ]
    if not styled_spans:
        return DraftRewriteFontStyle(font_family=None, font_size=None)

    first_span = styled_spans[0]
    last_span = styled_spans[-1]
    if first_span.font_family and first_span.font_family == last_span.font_family:
        return DraftRewriteFontStyle(
            font_family=first_span.font_family,
            font_size=(
                first_span.font_size
                if first_span.font_size == last_span.font_size
                else _select_dominant_size_for_family(styled_spans, first_span.font_family)
            ),
        )

    return _select_dominant_font_style_from_spans(styled_spans)


def _select_dominant_size_for_family(
    spans: list[DraftRewriteLocalStyleSpan],
    font_family: str,
) -> str | None:
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for index, span in enumerate(spans):
        if span.font_family != font_family or not span.font_size:
            continue
        counts[span.font_size] = counts.get(span.font_size, 0) + len(span.text.strip())
        first_seen.setdefault(span.font_size, index)
    if not counts:
        return None
    return max(counts, key=lambda key: (counts[key], -first_seen[key]))


def _select_dominant_font_style_from_spans(
    spans: list[DraftRewriteLocalStyleSpan],
) -> DraftRewriteFontStyle:
    counts: dict[tuple[str | None, str | None], int] = {}
    first_seen: dict[tuple[str | None, str | None], int] = {}
    for index, span in enumerate(spans):
        key = (span.font_family, span.font_size)
        if key == (None, None):
            continue
        counts[key] = counts.get(key, 0) + len(span.text.strip())
        first_seen.setdefault(key, index)

    if not counts:
        return DraftRewriteFontStyle(font_family=None, font_size=None)

    winner = max(counts, key=lambda key: (counts[key], -first_seen[key]))
    return DraftRewriteFontStyle(font_family=winner[0], font_size=winner[1])


def _collect_local_style_spans(
    element: Tag,
    base_style: DraftRewriteFontStyle,
) -> list[DraftRewriteLocalStyleSpan]:
    local_spans: list[DraftRewriteLocalStyleSpan] = []
    for span in _collect_text_style_spans(element):
        text = span.text.strip()
        if not text:
            continue

        local_family = span.font_family if span.font_family != base_style.font_family else None
        local_size = span.font_size if span.font_size != base_style.font_size else None
        if not span.marks and local_family is None and local_size is None:
            continue

        local_spans.append(
            DraftRewriteLocalStyleSpan(
                text=text,
                marks=span.marks,
                font_family=local_family,
                font_size=local_size,
            ),
        )

    return local_spans


def _collect_text_style_spans(element: Tag) -> list[DraftRewriteLocalStyleSpan]:
    spans: list[DraftRewriteLocalStyleSpan] = []
    for text_node in list(element.find_all(string=True, recursive=True)):
        if not isinstance(text_node, NavigableString):
            continue
        text = str(text_node)
        if not text.strip():
            continue
        spans.append(
            DraftRewriteLocalStyleSpan(
                text=text,
                marks=_collect_marks(text_node, element),
                font_family=_resolve_effective_font_family(text_node),
                font_size=_resolve_effective_font_size(text_node),
            ),
        )
    return spans


def _apply_segment_base_font_style(element: Tag, style: DraftRewriteFontStyle) -> None:
    if style.font_family is None and style.font_size is None:
        return
    current_style = str(element.get("style", ""))
    updated_style = _merge_font_style(current_style, style)
    if updated_style:
        element["style"] = updated_style
    elif "style" in element.attrs:
        del element.attrs["style"]


def _is_font_family_style_key(key: str) -> bool:
    return key in FONT_FAMILY_STYLE_KEYS


def _merge_font_style(style: str, dominant: DraftRewriteFontStyle) -> str:
    declarations: list[tuple[str, str]] = []
    for item in style.split(";"):
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        normalized_key = key.strip().lower()
        normalized_value = value.strip()
        if not normalized_key or not normalized_value:
            continue
        if _is_font_family_style_key(normalized_key) or normalized_key == "font-size":
            continue
        declarations.append((normalized_key, normalized_value))

    if dominant.font_family:
        declarations.append(("font-family", dominant.font_family))
    if dominant.font_size:
        declarations.append(("font-size", dominant.font_size))

    return ";".join(f"{key}:{value}" for key, value in declarations)


def _resolve_effective_font_family(text_node: NavigableString) -> str | None:
    text = str(text_node)
    for parent in text_node.parents:
        if not isinstance(parent, Tag):
            continue
        families = _extract_font_family_candidates(
            parent,
            prefer_fareast=_contains_cjk_char(text),
        )
        if families:
            return _choose_font_family_for_text(text, families)
    return None


def _resolve_effective_font_size(text_node: NavigableString) -> str | None:
    for parent in text_node.parents:
        if not isinstance(parent, Tag):
            continue
        size = _extract_font_size(parent)
        if size:
            return size
    return None


def _extract_font_family_candidates(
    tag: Tag,
    *,
    prefer_fareast: bool = False,
) -> list[str]:
    style_families: dict[str, list[str]] = {}
    for key, value in _iter_style_declarations(str(tag.get("style", ""))):
        if key in FONT_FAMILY_STYLE_KEYS:
            style_families.setdefault(key, []).extend(_split_font_family_stack(value))

    if prefer_fareast and style_families.get("mso-fareast-font-family"):
        return _dedupe_font_families(style_families["mso-fareast-font-family"])

    candidates = list(style_families.get("font-family", []))
    if tag.name == "font":
        candidates.extend(_split_font_family_stack(str(tag.get("face", ""))))
    for key in (
        "mso-ascii-font-family",
        "mso-hansi-font-family",
        "mso-bidi-font-family",
        "mso-fareast-font-family",
    ):
        candidates.extend(style_families.get(key, []))

    return _dedupe_font_families(candidates)


def _dedupe_font_families(candidates: list[str]) -> list[str]:
    deduped: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _iter_style_declarations(style: str) -> list[tuple[str, str]]:
    declarations: list[tuple[str, str]] = []
    for item in style.split(";"):
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        normalized_key = key.strip().lower()
        normalized_value = value.strip()
        if normalized_key and normalized_value:
            declarations.append((normalized_key, normalized_value))
    return declarations


def _split_font_family_stack(value: str) -> list[str]:
    families: list[str] = []
    for item in value.split(","):
        family = item.strip().strip("'\"")
        if family:
            families.append(family)
    return families

def _choose_font_family_for_text(text: str, candidates: list[str]) -> str | None:
    if not candidates:
        return None

    if _contains_cjk_char(text):
        for family in candidates:
            if _looks_like_cjk_font_family(family):
                return family

    return candidates[0]

def _contains_cjk_char(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))

def _looks_like_cjk_font_family(font_family: str) -> bool:
    return any(hint in font_family for hint in CJK_FONT_HINTS)


def _extract_font_size(tag: Tag) -> str | None:
    if tag.name == "font":
        size = str(tag.get("size", "")).strip()
        if size:
            return size

    style = str(tag.get("style", ""))
    for key, value in _iter_style_declarations(style):
        if key == "font-size":
            return value
    return None
