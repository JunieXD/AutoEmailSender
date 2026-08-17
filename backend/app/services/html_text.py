from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.services.beautiful_soup import (
    is_comment,
    is_navigable_string,
    is_tag,
    parse_html,
)

if TYPE_CHECKING:
    from bs4 import Tag


BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "body",
    "div",
    "dl",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "ul",
}
SKIP_TAGS = {"head", "link", "meta", "script", "style", "title"}
TABLE_CELL_TAGS = ("td", "th")


def html_to_text(value: str) -> str:
    text = value.strip()
    if not text:
        return ""

    soup = parse_html(text)
    root = soup.body or soup
    return _join_blocks(_extract_blocks(root))


def normalize_plain_text(value: str) -> str:
    if not value:
        return ""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    normalized = re.sub(r"[ \t\f\v]+", " ", normalized)
    normalized = re.sub(r" *\n *", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _extract_blocks(node: Tag) -> list[str]:
    blocks: list[str] = []
    inline_parts: list[str] = []

    def flush_inline_parts() -> None:
        text = normalize_plain_text("".join(inline_parts))
        inline_parts.clear()
        if text:
            blocks.append(text)

    for child in node.children:
        if is_comment(child):
            continue

        if is_navigable_string(child):
            inline_parts.append(str(child))
            continue

        if not is_tag(child) or child.name in SKIP_TAGS:
            continue

        if child.name == "br":
            inline_parts.append("\n")
            continue

        if child.name == "table":
            flush_inline_parts()
            table_text = _extract_table_text(child)
            if table_text:
                blocks.append(table_text)
            continue

        if child.name in BLOCK_TAGS:
            flush_inline_parts()
            blocks.extend(_extract_blocks(child))
            continue

        inline_parts.append(_extract_inline_text(child))

    flush_inline_parts()
    return blocks


def _extract_inline_text(node: Tag) -> str:
    parts: list[str] = []

    for child in node.children:
        if is_comment(child):
            continue

        if is_navigable_string(child):
            parts.append(str(child))
            continue

        if not is_tag(child) or child.name in SKIP_TAGS:
            continue

        if child.name == "br":
            parts.append("\n")
            continue

        if child.name == "table":
            table_text = _extract_table_text(child)
            if table_text:
                parts.append(table_text)
            continue

        if child.name in BLOCK_TAGS:
            block_text = _join_blocks(_extract_blocks(child), separator="\n")
            if block_text:
                parts.append(block_text)
            continue

        parts.append(_extract_inline_text(child))

    return normalize_plain_text("".join(parts))


def _extract_table_text(table: Tag) -> str:
    rows: list[str] = []

    for row in table.find_all("tr"):
        cells: list[str] = []
        for cell in row.find_all(TABLE_CELL_TAGS, recursive=False):
            cell_text = _join_blocks(_extract_blocks(cell), separator="\n")
            if cell_text:
                cells.append(cell_text)

        if cells:
            rows.append("\t".join(cells))

    return normalize_plain_text("\n".join(rows))


def _join_blocks(blocks: list[str], *, separator: str = "\n\n") -> str:
    filtered_blocks = [block for block in blocks if block]
    if not filtered_blocks:
        return ""
    return separator.join(filtered_blocks)
