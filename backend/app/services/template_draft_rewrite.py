from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from html import escape

from bs4 import BeautifulSoup, NavigableString, Tag

from app.services.rich_text import RichTextRenderResult, normalize_email_html

PLACEHOLDER_PATTERN = re.compile(r"\{\{[a-zA-Z0-9_]+\}\}")
PROTECTED_VALUE_PATTERN = re.compile(
    r"\{\{(?:year|month|day)\}\}|"
    r"(?<!\d)(?:19|20)\d{2}\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)?|"
    r"(?<!\d)(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}(?!\d)|"
    r"(?<!\d)\d{1,2}\s*月\s*\d{1,2}\s*日|"
    r"(?<!\d)(?:19|20)\d{2}(?:\s*[-–—]\s*(?:(?:19|20)?\d{2}))?(?!\d)|"
    r"(?<!\d)\d{1,2}:\d{2}(?!\d)",
)
STYLE_MARKER_PATTERN = re.compile(r"\[\[(/?)S(\d+)\]\]")
STYLE_REGION_PATTERN = re.compile(r"\[\[S(\d+)\]\](.*?)\[\[/S\1\]\]", re.DOTALL)
PROTECTED_TOKEN_PATTERN = re.compile(r"\[\[P\d+\]\]")
FACT_TOKEN_PATTERN = re.compile(r"\[\[F\d+\]\]")
DRAFT_RESEARCH_PERSONALIZATION_ERROR = (
    "AI 没有正确将导师研究方向融入草稿。请重新生成；"
    "若仍失败，请检查该导师的研究方向是否填写完整。"
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
class DraftRewriteFactToken:
    token: str
    value: str
    description: str


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
    fact_tokens: list[DraftRewriteFactToken] = field(default_factory=list)


def build_draft_rewrite_document(html: str, context: dict[str, str]) -> DraftRewriteDocument:
    soup = BeautifulSoup(html.strip(), "html.parser")
    rendered_context = dict(context)
    fact_tokens: list[DraftRewriteFactToken] = []
    research_direction = rendered_context.get("research_direction", "").strip()
    if research_direction:
        fact_token = DraftRewriteFactToken(
            token="[[F1]]",
            value=research_direction,
            description="导师研究方向原文；该系统占位不得改写、解释或展开",
        )
        fact_tokens.append(fact_token)
        rendered_context["research_direction"] = fact_token.token

    _render_template_text_nodes(soup, rendered_context)
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
            fact_tokens,
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
                locked=_should_lock_segment(index, element, plain_text),
                html_fragment=html_fragment,
            ),
        )

    return DraftRewriteDocument(
        html=str(soup),
        blocks=blocks,
        protected_tokens=protected_tokens,
        fact_tokens=fact_tokens,
    )


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
        if tag.find_parent("table") is not None:
            continue
        if tag.name == "li" and tag.find(["p", "h1", "h2", "h3", "h4", "h5", "h6"]):
            continue
        elements.append(tag)
    return elements


def _build_rewrite_text(
    element: Tag,
    protected_tokens: list[DraftRewriteProtectedToken],
    fact_tokens: list[DraftRewriteFactToken],
) -> tuple[str, str, list[DraftRewriteStyleRegion], list[DraftRewriteStyleSpan]]:
    runs: list[tuple[str, str, dict[str, object]]] = []
    for text_node in element.find_all(string=True, recursive=True):
        if not isinstance(text_node, NavigableString):
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
                    text=_restore_fact_tokens(raw_text, fact_tokens),
                    marks=[str(mark) for mark in marks],
                ),
            )

    return (
        "".join(parts),
        _restore_fact_tokens("".join(plain_parts), fact_tokens),
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


def _restore_fact_tokens(text: str, fact_tokens: list[DraftRewriteFactToken]) -> str:
    restored = text
    for fact in fact_tokens:
        restored = restored.replace(fact.token, fact.value)
    return restored


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

    base_style = _parse_css(_select_base_inline_style(container) or str(container.get("style", "")))
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
        if not isinstance(parent, Tag) or parent is container:
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
        if not isinstance(text_node, NavigableString):
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
    return max(weighted_styles, key=lambda item: (weighted_styles[item], -first_seen[item]))


def _nearest_inline_style(text_node: NavigableString, container: Tag) -> str | None:
    for parent in text_node.parents:
        if not isinstance(parent, Tag) or parent is container:
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
        if not isinstance(parent, Tag):
            continue
        color = _resolve_tag_color(parent)
        if color:
            return color
        if parent is container:
            break
    return None

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
    element_map = {block.segment_id: element for block, element in zip(document.blocks, elements)}
    editable_blocks = [
        block
        for block in document.blocks
        if block.type != "table" and not block.locked
    ]
    validated_replacements = _validate_replacements(
        document,
        editable_blocks,
        replacements,
    )

    for block, replacement in zip(editable_blocks, validated_replacements):
        element = element_map.get(block.segment_id)
        if element is None:
            raise ValueError(f"找不到模板段落: {block.segment_id}")
        fragment_html = _render_rewrite_text(
            block,
            str(replacement["text"]),
            document,
        )
        fragment = BeautifulSoup(f"<div>{fragment_html}</div>", "html.parser")
        element.clear()
        for child in list(fragment.div.contents if fragment.div else []):
            element.append(child)

    for block, element in zip(document.blocks, elements):
        if block.type != "table" and not block.locked:
            continue
        if not block.html_fragment or element is None:
            continue
        original_fragment = BeautifulSoup(block.html_fragment, "html.parser")
        original_root = next((node for node in original_fragment.contents if isinstance(node, Tag)), None)
        if original_root is not None:
            element.replace_with(original_root)

    _restore_document_fact_tokens(soup, document.fact_tokens)
    return normalize_email_html(str(soup))


def _validate_replacements(
    document: DraftRewriteDocument,
    editable_blocks: list[DraftRewriteSourceBlock],
    replacements: list[dict[str, object]],
) -> list[dict[str, object]]:
    expected_ids = [block.segment_id for block in editable_blocks]
    actual_ids: list[str] = []
    validated: list[dict[str, object]] = []
    for index, replacement in enumerate(replacements):
        if not isinstance(replacement, dict):
            raise ValueError(f"第 {index + 1} 个段落改写不是对象")
        if set(replacement) != {"segment_id", "text"}:
            raise ValueError(f"第 {index + 1} 个段落改写字段无效")
        segment_id = replacement.get("segment_id")
        text = replacement.get("text")
        if not isinstance(segment_id, str) or not isinstance(text, str) or not text.strip():
            raise ValueError(f"第 {index + 1} 个段落改写内容无效")
        actual_ids.append(segment_id)
        validated.append(replacement)
    if actual_ids != expected_ids:
        raise ValueError(
            f"模型返回的段落集合或顺序无效: expected={expected_ids}, actual={actual_ids}",
        )

    expected_protected: list[str] = []
    actual_protected: list[str] = []
    expected_facts_from_source: list[str] = []
    actual_facts: list[str] = []
    for block, replacement in zip(editable_blocks, validated):
        text = str(replacement["text"])
        expected_markers = [
            marker
            for region in block.style_regions
            for marker in (("", region.style_id[1:]), ("/", region.style_id[1:]))
        ]
        actual_markers = STYLE_MARKER_PATTERN.findall(text)
        if actual_markers != expected_markers:
            raise ValueError(f"样式区域缺失、重复或顺序错误: {block.segment_id}")
        matches = list(STYLE_REGION_PATTERN.finditer(text))
        if len(matches) != len(block.style_regions) or any(
            not match.group(2).strip() for match in matches
        ):
            raise ValueError(f"样式区域内容无效: {block.segment_id}")
        expected_protected.extend(PROTECTED_TOKEN_PATTERN.findall(block.rewrite_text))
        actual_protected.extend(PROTECTED_TOKEN_PATTERN.findall(text))
        expected_facts_from_source.extend(FACT_TOKEN_PATTERN.findall(block.rewrite_text))
        actual_facts.extend(FACT_TOKEN_PATTERN.findall(text))

    if actual_protected != expected_protected:
        raise ValueError("日期、时间或延迟占位符被修改")

    source_literal_fact_counts = {
        fact.value: sum(
            (
                (block.html_fragment or "")
                if block.type == "table"
                else block.rewrite_text
            ).count(fact.value)
            for block in document.blocks
        )
        for fact in document.fact_tokens
        if fact.value.strip()
        and any(
            fact.value
            in (
                (block.html_fragment or "")
                if block.type == "table"
                else block.rewrite_text
            )
            for block in document.blocks
        )
    }
    if source_literal_fact_counts:
        rendered_texts = [
            str(replacement["text"])
            for replacement in validated
        ]
        rendered_texts.extend(
            (block.html_fragment or "") if block.type == "table" else block.rewrite_text
            for block in document.blocks
            if block.type == "table" or block.locked
        )
        rendered_text = "\n".join(rendered_texts)
        if any(
            rendered_text.count(value) != expected_count
            for value, expected_count in source_literal_fact_counts.items()
        ):
            raise ValueError(DRAFT_RESEARCH_PERSONALIZATION_ERROR)

    retained_source_facts = [
        token
        for block in document.blocks
        if block.type == "table" or block.locked
        for token in FACT_TOKEN_PATTERN.findall(block.html_fragment or block.rewrite_text)
    ]
    if expected_facts_from_source:
        expected_facts = expected_facts_from_source
    elif retained_source_facts or source_literal_fact_counts or not editable_blocks:
        expected_facts = []
    else:
        expected_facts = [fact.token for fact in document.fact_tokens]
    if actual_facts != expected_facts:
        raise ValueError(DRAFT_RESEARCH_PERSONALIZATION_ERROR)
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
    for fact in document.fact_tokens:
        restored = restored.replace(fact.token, fact.value)
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


def _restore_document_fact_tokens(
    soup: BeautifulSoup,
    facts: list[DraftRewriteFactToken],
) -> None:
    if not facts:
        return
    for text_node in list(soup.find_all(string=True)):
        if not isinstance(text_node, NavigableString):
            continue
        restored = str(text_node)
        for fact in facts:
            restored = restored.replace(fact.token, fact.value)
        if restored != str(text_node):
            text_node.replace_with(restored)


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


def _apply_dominant_font_style(soup: BeautifulSoup, style: DraftRewriteFontStyle) -> None:
    for tag in soup.find_all(True):
        if _is_within_table(tag):
            continue
        current_style = str(tag.get("style", ""))
        updated_style = _merge_font_style(current_style, style)
        if updated_style:
            tag["style"] = updated_style
        elif "style" in tag.attrs:
            del tag.attrs["style"]


def _is_within_table(tag: Tag) -> bool:
    if tag.name == "table":
        return True
    for parent in tag.parents:
        if isinstance(parent, Tag) and parent.name == "table":
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
        if not isinstance(parent, Tag):
            continue
        families = _extract_font_family_candidates(parent)
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
