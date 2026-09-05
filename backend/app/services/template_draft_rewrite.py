from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from html import escape
from typing import TYPE_CHECKING

from app.services.beautiful_soup import is_navigable_string, is_tag, parse_html
from app.services.rich_text import RichTextRenderResult, normalize_email_html

if TYPE_CHECKING:
    from bs4 import BeautifulSoup, NavigableString, Tag

PLACEHOLDER_PATTERN = re.compile(r"\{\{[a-zA-Z0-9_]+\}\}")
# Only unresolved runtime placeholders are structural data. Literal dates, years,
# times, names, and research directions are ordinary draft text that the user may
# ask the model to edit.
PROTECTED_VALUE_PATTERN = PLACEHOLDER_PATTERN
STYLE_MARKER_PATTERN = re.compile(r"\[\[(/?)S(\d+)\]\]")
STYLE_REGION_PATTERN = re.compile(r"\[\[S(\d+)\]\](.*?)\[\[/S\1\]\]", re.DOTALL)
PROTECTED_TOKEN_PATTERN = re.compile(r"\[\[P\d+\]\]")
INVISIBLE_SEGMENT_TEXT_PATTERN = re.compile(
    r"[\s\u00ad\u200b\u200c\u200d\u2060\ufeff]+",
)

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


@dataclass(slots=True)
class DraftRewriteFontStyle:
    font_family: str | None
    font_size: str | None


@dataclass(slots=True)
class DraftRewriteStyleSpan:
    text: str
    marks: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DraftRewriteStyleRegion:
    style_id: str
    style: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class DraftRewriteProtectedToken:
    token: str
    value: str


@dataclass(slots=True)
class DraftRewriteSourceBlock:
    segment_id: str
    type: str
    text: str
    rewrite_text: str = ""
    style_spans: list[DraftRewriteStyleSpan] = field(default_factory=list)
    style_regions: list[DraftRewriteStyleRegion] = field(default_factory=list)
    base_inline_style: str | None = None
    locked: bool = False
    html_fragment: str | None = None


@dataclass(slots=True)
class DraftRewriteDocument:
    html: str
    blocks: list[DraftRewriteSourceBlock]
    protected_tokens: list[DraftRewriteProtectedToken] = field(default_factory=list)


def build_draft_rewrite_document(
    html: str, context: dict[str, str]
) -> DraftRewriteDocument:
    soup = parse_html(html.strip())
    _render_template_text_nodes(soup, context)
    blocks: list[DraftRewriteSourceBlock] = []
    protected_tokens: list[DraftRewriteProtectedToken] = []

    for index, element in enumerate(_iter_segment_elements(soup), start=1):
        segment_id = f"seg_{index}"
        html_fragment = str(element)
        if element.name == "table":
            blocks.append(
                DraftRewriteSourceBlock(
                    segment_id=segment_id,
                    type="table",
                    text=element.get_text(" ", strip=True),
                    rewrite_text="表格块原样保留，不参与改写。",
                    locked=True,
                    html_fragment=html_fragment,
                ),
            )
            continue

        rewrite_text, plain_text, style_regions, style_spans = _build_rewrite_text(
            element,
            protected_tokens,
        )

        blocks.append(
            DraftRewriteSourceBlock(
                segment_id=segment_id,
                type=_segment_type(element),
                text=plain_text,
                rewrite_text=rewrite_text,
                style_spans=style_spans,
                style_regions=style_regions,
                base_inline_style=_select_base_inline_style(element),
                # Rich-text editors preserve visual spacing with blocks such as
                # <p><br></p>. Keep those blocks in place, but never ask the
                # model to invent content for them.
                locked=not _has_visible_segment_text(plain_text),
                html_fragment=html_fragment,
            ),
        )

    return DraftRewriteDocument(
        html=str(soup),
        blocks=blocks,
        protected_tokens=protected_tokens,
    )


def render_draft_template_text(value: str | None, context: dict[str, str]) -> str:
    return _render_template_text(value or "", context)


def _iter_segment_elements(soup: BeautifulSoup) -> list[Tag]:
    elements: list[Tag] = []
    for tag in soup.find_all(SEGMENT_TAG_NAMES):
        if not is_tag(tag):
            continue
        if tag.name == "table":
            elements.append(tag)
            continue
        if tag.find_parent("table") is not None:
            continue
        if tag.name == "li" and tag.find(["p", "h1", "h2", "h3", "h4", "h5", "h6"]):
            continue
        elements.append(tag)
    return elements


def _build_rewrite_text(
    element: Tag,
    protected_tokens: list[DraftRewriteProtectedToken],
) -> tuple[str, str, list[DraftRewriteStyleRegion], list[DraftRewriteStyleSpan]]:
    runs: list[tuple[str, str, dict[str, object]]] = []
    for text_node in element.find_all(string=True, recursive=True):
        if not is_navigable_string(text_node):
            continue
        raw_text = str(text_node)
        if not raw_text.strip():
            continue
        protected_text = _protect_values(raw_text, protected_tokens)
        style = _relative_inline_style(text_node, element, raw_text)
        if runs and runs[-1][2] == style:
            previous_protected, previous_plain, _ = runs[-1]
            runs[-1] = (
                previous_protected + protected_text,
                previous_plain + raw_text,
                style,
            )
        else:
            runs.append((protected_text, raw_text, style))

    parts: list[str] = []
    plain_parts: list[str] = []
    style_regions: list[DraftRewriteStyleRegion] = []
    style_spans: list[DraftRewriteStyleSpan] = []
    for protected_text, raw_text, style in runs:
        plain_parts.append(raw_text)
        if not style:
            parts.append(protected_text)
            continue
        style_id = f"S{len(style_regions) + 1}"
        parts.append(f"[[{style_id}]]{protected_text}[[/{style_id}]]")
        style_regions.append(DraftRewriteStyleRegion(style_id=style_id, style=style))
        marks = style.get("marks")
        if isinstance(marks, list) and marks:
            style_spans.append(
                DraftRewriteStyleSpan(
                    text=raw_text,
                    marks=[str(mark) for mark in marks],
                ),
            )

    return (
        "".join(parts),
        "".join(plain_parts),
        style_regions,
        style_spans,
    )


def _protect_values(
    text: str,
    protected_tokens: list[DraftRewriteProtectedToken],
) -> str:
    def replace(match: re.Match[str]) -> str:
        token = f"[[P{len(protected_tokens) + 1}]]"
        protected_tokens.append(
            DraftRewriteProtectedToken(token=token, value=match.group(0)),
        )
        return token

    return PROTECTED_VALUE_PATTERN.sub(replace, text)


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
        if not is_tag(parent):
            continue
        if parent is container:
            break
        if parent.name in {"strong", "b"} and "strong" not in marks:
            marks.append("strong")
        if parent.name in {"u"} and "underline" not in marks:
            marks.append("underline")
        if parent.name in {"em", "i"} and "emphasis" not in marks:
            marks.append("emphasis")
        style = _parse_css(str(parent.get("style", "")))
        if style.get("font-weight", "").lower() in {"bold", "600", "700", "800", "900"}:
            if "strong" not in marks:
                marks.append("strong")
        if "underline" in style.get("text-decoration", "").lower():
            if "underline" not in marks:
                marks.append("underline")
        if style.get("font-style", "").lower() == "italic":
            if "emphasis" not in marks:
                marks.append("emphasis")
    return marks


def _relative_inline_style(
    text_node: NavigableString,
    container: Tag,
    text: str,
) -> dict[str, object]:
    style: dict[str, object] = {}
    marks = _collect_marks(text_node, container)
    if marks:
        style["marks"] = marks

    base_style = _parse_css(
        _select_base_inline_style(container) or str(container.get("style", ""))
    )
    base_families = _split_font_family_stack(base_style.get("font-family", ""))
    base_family = _choose_font_family_for_text(text, base_families)
    effective_family = _resolve_effective_font_family(text_node)
    if effective_family and effective_family != base_family:
        style["font_family"] = effective_family

    base_size = base_style.get("font-size") or _extract_font_size(container)
    effective_size = _resolve_effective_font_size(text_node)
    if effective_size and effective_size != base_size:
        style["font_size"] = effective_size

    base_color = base_style.get("color") or _resolve_tag_color(container)
    effective_color = _resolve_effective_color(text_node, container)
    if effective_color and effective_color.lower() != str(base_color or "").lower():
        style["color"] = effective_color

    for parent in text_node.parents:
        if not is_tag(parent) or parent is container:
            break
        if parent.name == "a":
            href = str(parent.get("href", "")).strip()
            if href:
                style["link_href"] = href
            break
    return style


def _select_base_inline_style(element: Tag) -> str | None:
    weighted_styles: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    order = 0
    for text_node in element.find_all(string=True, recursive=True):
        if not is_navigable_string(text_node):
            continue
        text = str(text_node)
        if not text.strip():
            continue
        style = _nearest_inline_style(text_node, element)
        if not style:
            continue
        weighted_styles[style] += len(text)
        if style not in first_seen:
            first_seen[style] = order
        order += 1
    if not weighted_styles:
        return None
    return max(
        weighted_styles, key=lambda item: (weighted_styles[item], -first_seen[item])
    )


def _nearest_inline_style(text_node: NavigableString, container: Tag) -> str | None:
    for parent in text_node.parents:
        if not is_tag(parent) or parent is container:
            break
        style = str(parent.get("style", "")).strip()
        if style:
            return style
        if parent.name == "font":
            declarations: list[str] = []
            if parent.get("face"):
                declarations.append(f"font-family:{parent.get('face')}")
            if parent.get("size"):
                declarations.append(f"font-size:{parent.get('size')}")
            if parent.get("color"):
                declarations.append(f"color:{parent.get('color')}")
            if declarations:
                return ";".join(declarations) + ";"
    style = str(container.get("style", "")).strip()
    return style or None


def _parse_css(value: str) -> dict[str, str]:
    declarations: dict[str, str] = {}
    for item in value.split(";"):
        if ":" not in item:
            continue
        key, raw_value = item.split(":", 1)
        key = key.strip().lower()
        raw_value = raw_value.strip()
        if key and raw_value:
            declarations[key] = raw_value
    return declarations


def _resolve_tag_color(tag: Tag) -> str | None:
    if tag.name == "font":
        color = str(tag.get("color", "")).strip()
        if color:
            return color
    return _parse_css(str(tag.get("style", ""))).get("color")


def _resolve_effective_color(text_node: NavigableString, container: Tag) -> str | None:
    for parent in text_node.parents:
        if not is_tag(parent):
            continue
        color = _resolve_tag_color(parent)
        if color:
            return color
        if parent is container:
            break
    return None


def _has_visible_segment_text(text: str) -> bool:
    return bool(INVISIBLE_SEGMENT_TEXT_PATTERN.sub("", text))


def select_dominant_font_and_size(html: str) -> DraftRewriteFontStyle:
    soup = parse_html(html.strip())
    counts: dict[tuple[str | None, str | None], int] = {}
    first_seen: dict[tuple[str | None, str | None], int] = {}
    order = 0

    for text_node in soup.find_all(string=True):
        if not is_navigable_string(text_node):
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
    soup = parse_html(document.html)
    elements = _iter_segment_elements(soup)
    element_map = {
        block.segment_id: element for block, element in zip(document.blocks, elements)
    }
    editable_blocks = [
        block for block in document.blocks if block.type != "table" and not block.locked
    ]
    validated_replacements = _validate_replacements(editable_blocks, replacements)

    for block in editable_blocks:
        replacement = validated_replacements.get(block.segment_id)
        if replacement is None:
            continue
        element = element_map.get(block.segment_id)
        if element is None:
            raise ValueError(f"找不到模板段落: {block.segment_id}")
        fragment_html = _render_rewrite_text(
            block,
            str(replacement["text"]),
            document,
        )
        fragment = parse_html(f"<div>{fragment_html}</div>")
        element.clear()
        for child in list(fragment.div.contents if fragment.div else []):
            element.append(child)

    for block, element in zip(document.blocks, elements):
        if block.type != "table" and not block.locked:
            continue
        if not block.html_fragment or element is None:
            continue
        original_fragment = parse_html(block.html_fragment)
        original_root = next(
            (node for node in original_fragment.contents if is_tag(node)),
            None,
        )
        if original_root is not None:
            element.replace_with(original_root)

    return normalize_email_html(str(soup))


def _validate_replacements(
    editable_blocks: list[DraftRewriteSourceBlock],
    replacements: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    block_map = {block.segment_id: block for block in editable_blocks}
    validated: dict[str, dict[str, object]] = {}
    for replacement in replacements:
        if not isinstance(replacement, dict) or set(replacement) != {
            "segment_id",
            "text",
        }:
            continue
        segment_id = replacement.get("segment_id")
        text = replacement.get("text")
        if not isinstance(segment_id, str) or not isinstance(text, str):
            continue
        block = block_map.get(segment_id)
        if block is None or segment_id in validated:
            continue
        if text and not _has_visible_segment_text(text):
            continue

        expected_markers = [
            marker
            for region in block.style_regions
            for marker in (("", region.style_id[1:]), ("/", region.style_id[1:]))
        ]
        actual_markers = STYLE_MARKER_PATTERN.findall(text)
        if text and actual_markers != expected_markers:
            continue
        if text:
            matches = list(STYLE_REGION_PATTERN.finditer(text))
            if len(matches) != len(block.style_regions):
                continue
        if PROTECTED_TOKEN_PATTERN.findall(text) != PROTECTED_TOKEN_PATTERN.findall(
            block.rewrite_text,
        ):
            continue
        validated[segment_id] = replacement
    return validated


def _render_rewrite_text(
    block: DraftRewriteSourceBlock,
    text: str,
    document: DraftRewriteDocument,
) -> str:
    region_map = {region.style_id: region.style for region in block.style_regions}
    parts: list[str] = []
    cursor = 0
    for match in STYLE_REGION_PATTERN.finditer(text):
        parts.append(
            _render_text_fragment(
                text[cursor : match.start()],
                base_inline_style=block.base_inline_style,
                style=None,
                document=document,
            ),
        )
        style_id = f"S{match.group(1)}"
        parts.append(
            _render_text_fragment(
                match.group(2),
                base_inline_style=block.base_inline_style,
                style=region_map[style_id],
                document=document,
            ),
        )
        cursor = match.end()
    parts.append(
        _render_text_fragment(
            text[cursor:],
            base_inline_style=block.base_inline_style,
            style=None,
            document=document,
        ),
    )
    return "".join(parts)


def _render_text_fragment(
    text: str,
    *,
    base_inline_style: str | None,
    style: dict[str, object] | None,
    document: DraftRewriteDocument,
) -> str:
    restored = text
    for token in document.protected_tokens:
        restored = restored.replace(token.token, token.value)
    rendered = escape(restored)
    if not rendered:
        return ""

    css = _parse_css(base_inline_style or "")
    style = style or {}
    for source_name, css_name in (
        ("font_family", "font-family"),
        ("font_size", "font-size"),
        ("color", "color"),
    ):
        value = style.get(source_name)
        if isinstance(value, str) and value:
            css[css_name] = value
    if css:
        css_text = ";".join(f"{key}:{value}" for key, value in css.items()) + ";"
        rendered = f'<span style="{escape(css_text, quote=True)}">{rendered}</span>'

    marks = style.get("marks")
    for mark in marks if isinstance(marks, list) else []:
        if mark == "strong":
            rendered = f"<strong>{rendered}</strong>"
        elif mark == "underline":
            rendered = f"<u>{rendered}</u>"
        elif mark == "emphasis":
            rendered = f"<em>{rendered}</em>"
    link_href = style.get("link_href")
    if isinstance(link_href, str) and link_href:
        rendered = f'<a href="{escape(link_href, quote=True)}">{rendered}</a>'
    return rendered


def _render_template_text_nodes(soup: BeautifulSoup, context: dict[str, str]) -> None:
    for text_node in list(soup.find_all(string=True)):
        if not is_navigable_string(text_node):
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


def _is_within_table(tag: Tag) -> bool:
    if tag.name == "table":
        return True
    for parent in tag.parents:
        if is_tag(parent) and parent.name == "table":
            return True
    return False


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
        if normalized_key in {"font-family", "font-size"}:
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
        if not is_tag(parent):
            continue
        families = _extract_font_family_candidates(parent)
        if families:
            return _choose_font_family_for_text(text, families)
    return None


def _resolve_effective_font_size(text_node: NavigableString) -> str | None:
    for parent in text_node.parents:
        if not is_tag(parent):
            continue
        size = _extract_font_size(parent)
        if size:
            return size
    return None


def _extract_font_family_candidates(tag: Tag) -> list[str]:
    candidates: list[str] = []
    if tag.name == "font":
        face = str(tag.get("face", "")).strip()
        if face:
            candidates.extend(_split_font_family_stack(face))

    style = str(tag.get("style", ""))
    match = re.search(r"font-family\s*:\s*([^;]+)", style, re.I)
    if match:
        candidates.extend(_split_font_family_stack(match.group(1)))

    deduped: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped


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
    match = re.search(r"font-size\s*:\s*([^;]+)", style, re.I)
    if match:
        size = match.group(1).strip()
        if size:
            return size
    return None
