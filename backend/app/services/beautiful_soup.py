from __future__ import annotations

from typing import TYPE_CHECKING, TypeGuard

if TYPE_CHECKING:
    from bs4 import BeautifulSoup, Comment, NavigableString, Tag


def parse_html(value: str) -> BeautifulSoup:
    from bs4 import BeautifulSoup

    return BeautifulSoup(value, "html.parser")


def is_comment(value: object) -> TypeGuard[Comment]:
    from bs4 import Comment

    return isinstance(value, Comment)


def is_navigable_string(value: object) -> TypeGuard[NavigableString]:
    from bs4 import NavigableString

    return isinstance(value, NavigableString)


def is_tag(value: object) -> TypeGuard[Tag]:
    from bs4 import Tag

    return isinstance(value, Tag)


def make_navigable_string(value: str) -> NavigableString:
    from bs4 import NavigableString

    return NavigableString(value)
