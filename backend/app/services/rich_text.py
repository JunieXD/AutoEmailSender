from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any
from urllib.parse import urlparse

from app.services.beautiful_soup import parse_html

ALLOWED_LINK_SCHEMES = {"http", "https", "mailto"}
ALLOWED_HTML_TAGS = {
    "a",
    "b",
    "br",
    "font",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "i",
    "li",
    "ol",
    "p",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}

ALLOWED_NON_LINK_ATTRS = {
    "align",
    "cellpadding",
    "cellspacing",
    "colspan",
    "color",
    "face",
    "rowspan",
    "size",
    "style",
}


@dataclass(frozen=True)
class RichTextRenderResult:
    html: str
    text: str


def render_rich_text_document(value: dict[str, Any]) -> RichTextRenderResult:
    if value.get("type") != "doc":
        raise ValueError("富文本根节点必须是 doc")

    blocks = value.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("富文本正文不能为空")

    html_parts: list[str] = []
    text_parts: list[str] = []
    for block in blocks:
        block_html, block_text = _render_block(block)
        if block_text.strip():
            html_parts.append(block_html)
            text_parts.append(block_text)

    html = "".join(html_parts).strip()
    text = "\n".join(text_parts).strip()
    if not text:
        raise ValueError("富文本正文缺少可见文本")
    return RichTextRenderResult(html=html, text=text)


def normalize_email_html(value: str) -> RichTextRenderResult:
    html = sanitize_email_html(value)
    text = html_to_text(html)
    if not text:
        raise ValueError("HTML 正文缺少可见文本")
    return RichTextRenderResult(html=html, text=text)


def text_to_email_html(value: str) -> RichTextRenderResult:
    text = value.strip()
    if not text:
        raise ValueError("纯文本正文不能为空")
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    html = "".join(f"<p>{escape(paragraph)}</p>" for paragraph in paragraphs)
    return RichTextRenderResult(html=html, text=text)


def sanitize_email_html(value: str) -> str:
    soup = parse_html(value.strip())
    for tag in list(soup.find_all(True)):
        if tag.name in {"script", "style"}:
            tag.decompose()
            continue

        if tag.name not in ALLOWED_HTML_TAGS:
            tag.unwrap()
            continue

        original_attrs = dict(tag.attrs)
        tag.attrs.clear()
        if tag.name == "a":
            href = str(original_attrs.get("href", "")).strip()
            _validate_href(href)
            tag.attrs["href"] = href
            tag.attrs["target"] = "_blank"
        else:
            for attr_name in ALLOWED_NON_LINK_ATTRS:
                attr_value = original_attrs.get(attr_name)
                if attr_value is not None and str(attr_value).strip():
                    tag.attrs[attr_name] = str(attr_value).strip()

    normalized = str(soup).strip()
    if not normalized:
        raise ValueError("HTML 正文不能为空")
    return normalized


def html_to_text(value: str) -> str:
    soup = parse_html(value)
    lines: list[str] = []
    for element in soup.find_all(["p", "li", "td", "th"]):
        if element.name in {"td", "th"} and element.find(["p", "li"]):
            continue
        text = element.get_text(" ", strip=True)
        if not text:
            continue
        if element.name == "li":
            lines.append(f"- {text}")
        else:
            lines.append(text)

    if lines:
        return "\n\n".join(lines).strip()
    return soup.get_text(" ", strip=True)


def _render_block(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise ValueError("富文本块必须是对象")

    node_type = value.get("type")
    if node_type == "paragraph":
        html, text = _render_inline_children(value.get("children", []))
        return f"<p>{html}</p>", text

    if node_type in {"bullet_list", "numbered_list"}:
        items = value.get("items")
        if not isinstance(items, list):
            raise ValueError("列表不能为空")
        if not items:
            return "", ""

        tag = "ul" if node_type == "bullet_list" else "ol"
        html_items: list[str] = []
        text_items: list[str] = []
        for index, item in enumerate(items, start=1):
            item_html, item_text = _render_inline_children(item)
            if not item_text.strip():
                continue
            html_items.append(f"<li>{item_html}</li>")
            prefix = "-" if node_type == "bullet_list" else f"{index}."
            text_items.append(f"{prefix} {item_text}")
        if not text_items:
            return "", ""
        return f"<{tag}>{''.join(html_items)}</{tag}>", "\n".join(text_items)

    raise ValueError(f"不支持的富文本块类型: {node_type}")


def _render_inline_children(children: Any) -> tuple[str, str]:
    if not isinstance(children, list):
        raise ValueError("富文本子节点必须是数组")

    html_parts: list[str] = []
    text_parts: list[str] = []
    for child in children:
        html, text = _render_inline(child)
        html_parts.append(html)
        text_parts.append(text)
    return "".join(html_parts), "".join(text_parts)


def _render_inline(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise ValueError("富文本内联节点必须是对象")

    node_type = value.get("type")
    if node_type == "text":
        text = str(value.get("text", ""))
        return escape(text), text

    if node_type in {"strong", "emphasis", "link"}:
        html, text = _render_inline_children(value.get("children", []))
        if node_type == "strong":
            return f"<strong>{html}</strong>", text
        if node_type == "emphasis":
            return f"<em>{html}</em>", text

        href = str(value.get("href", "")).strip()
        _validate_href(href)
        return f'<a href="{escape(href, quote=True)}" target="_blank">{html}</a>', text

    if node_type == "line_break":
        return "<br>", "\n"

    raise ValueError(f"不支持的富文本内联类型: {node_type}")


def _validate_href(href: str) -> None:
    parsed = urlparse(href)
    if parsed.scheme not in ALLOWED_LINK_SCHEMES:
        raise ValueError("不支持的链接协议")
