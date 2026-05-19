from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.message import Message
from email.parser import BytesParser
from typing import Any


@dataclass(slots=True)
class ParsedTextParts:
    body_text: str | None
    body_html: str | None
    has_attachments: bool
    attachment_names: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TextBodyPart:
    section: str
    content_type: str


@dataclass(slots=True)
class ImapFetchedMessage:
    uid: int
    from_email: str
    subject: str | None
    message_id: str | None
    in_reply_to: str | None
    references: str | None
    sent_at: datetime
    received_at: datetime | None
    headers: dict[str, str]
    body_text: str
    body_html: str | None
    has_attachments: bool = False
    attachment_names: list[str] = field(default_factory=list)


def parse_text_parts_from_message(message: Message) -> ParsedTextParts:
    text_part: str | None = None
    html_part: str | None = None
    has_attachments = False
    attachment_names: list[str] = []

    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        content_type = part.get_content_type().lower()
        if disposition == "attachment" or filename:
            has_attachments = True
            if filename:
                attachment_names.append(filename)
            continue
        if content_type not in {"text/plain", "text/html"}:
            continue
        content = _get_part_content(part)
        if content_type == "text/plain" and text_part is None:
            text_part = content
        if content_type == "text/html" and html_part is None:
            html_part = content

    return ParsedTextParts(
        body_text=text_part,
        body_html=html_part,
        has_attachments=has_attachments,
        attachment_names=attachment_names,
    )


def fetch_message_headers_by_uid(client: object, uid: int) -> bytes:
    status, payload = client.uid(
        "FETCH",
        str(uid),
        "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM TO CC SUBJECT DATE IN-REPLY-TO REFERENCES)] INTERNALDATE)",
    )
    if status != "OK" or not payload:
        return b""
    for item in payload:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return b""


def fetch_message_headers_payload_by_uid(client: object, uid: int) -> list[object]:
    status, payload = client.uid(
        "FETCH",
        str(uid),
        "(BODY.PEEK[HEADER] INTERNALDATE)",
    )
    if status != "OK" or not payload:
        return []
    return list(payload)


def fetch_text_body_parts_by_uid(client: object, uid: int) -> ParsedTextParts:
    parts = fetch_text_part_sections_by_uid(client, uid)
    text_part: str | None = None
    html_part: str | None = None
    for part in parts:
        mime = _fetch_body_section(client, uid, f"{part.section}.MIME")
        body = _fetch_body_section(client, uid, part.section)
        if not body:
            continue
        message = BytesParser(policy=policy.default).parsebytes(mime + b"\r\n" + body)
        content = _get_part_content(message)
        if part.content_type == "text/plain" and text_part is None:
            text_part = content
        if part.content_type == "text/html" and html_part is None:
            html_part = content
    return ParsedTextParts(
        body_text=text_part,
        body_html=html_part,
        has_attachments=False,
        attachment_names=[],
    )


def fetch_text_part_sections_by_uid(client: object, uid: int) -> list[TextBodyPart]:
    status, payload = client.uid("FETCH", str(uid), "(BODYSTRUCTURE)")
    if status != "OK" or not payload:
        return []
    raw = _extract_bodystructure_text(payload)
    if not raw:
        return []
    parsed = _BodyStructureParser(raw).parse()
    sections = _collect_text_body_parts(parsed)
    sections.sort(key=lambda part: 0 if part.content_type == "text/plain" else 1)
    return sections


def search_uids_since(client: object, last_seen_uid: int | None) -> list[int]:
    start_uid = 1 if last_seen_uid is None else last_seen_uid + 1
    status, payload = client.uid("SEARCH", None, f"UID {start_uid}:*")
    if status != "OK" or not payload:
        return []
    raw = payload[0] if payload else b""
    return [int(item) for item in raw.split() if item.isdigit()]


def search_uids_from_sender(client: object, from_email: str) -> list[int]:
    escaped = from_email.replace('"', '\\"')
    status, payload = client.uid("SEARCH", None, f'(FROM "{escaped}")')
    if status != "OK" or not payload:
        return []
    raw = payload[0] if payload else b""
    return [int(item) for item in raw.split() if item.isdigit()]


def _get_part_content(part: Message) -> str:
    get_content = getattr(part, "get_content", None)
    if callable(get_content):
        try:
            return str(get_content())
        except Exception:
            pass
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _fetch_body_section(client: object, uid: int, section: str) -> bytes:
    status, payload = client.uid("FETCH", str(uid), f"(BODY.PEEK[{section}])")
    if status != "OK" or not payload:
        return b""
    for item in payload:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return b""


def _extract_bodystructure_text(payload: list[object]) -> str:
    for item in payload:
        values = item if isinstance(item, tuple) else (item,)
        for value in values:
            if isinstance(value, (bytes, bytearray)):
                text = bytes(value).decode("utf-8", errors="replace")
                marker = "BODYSTRUCTURE"
                marker_index = text.upper().find(marker)
                if marker_index < 0:
                    continue
                return text[marker_index + len(marker) :].strip().rstrip(")")
    return ""


def _collect_text_body_parts(parsed: Any, prefix: str = "") -> list[TextBodyPart]:
    if not isinstance(parsed, list) or not parsed:
        return []
    if _is_multipart_bodystructure(parsed):
        parts: list[TextBodyPart] = []
        part_number = 1
        for child in parsed:
            if not isinstance(child, list):
                break
            section = f"{prefix}.{part_number}" if prefix else str(part_number)
            parts.extend(_collect_text_body_parts(child, section))
            part_number += 1
        return parts

    if len(parsed) < 2 or not isinstance(parsed[0], str) or not isinstance(parsed[1], str):
        return []
    content_type = f"{parsed[0]}/{parsed[1]}".lower()
    if content_type not in {"text/plain", "text/html"}:
        return []
    if _bodystructure_has_attachment_disposition(parsed):
        return []
    return [TextBodyPart(section=prefix or "1", content_type=content_type)]


def _is_multipart_bodystructure(parsed: list[Any]) -> bool:
    return bool(parsed and isinstance(parsed[0], list))


def _bodystructure_has_attachment_disposition(parsed: list[Any]) -> bool:
    for item in parsed:
        if _contains_attachment_token(item):
            return True
    return False


def _contains_attachment_token(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() == "attachment"
    if isinstance(value, list):
        return any(_contains_attachment_token(item) for item in value)
    return False


class _BodyStructureParser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.index = 0

    def parse(self) -> Any:
        self._skip_spaces()
        if self._peek() == "(":
            return self._parse_list()
        return None

    def _parse_list(self) -> list[Any]:
        self._expect("(")
        values: list[Any] = []
        while self.index < len(self.source):
            self._skip_spaces()
            char = self._peek()
            if char == ")":
                self.index += 1
                break
            if char == "(":
                values.append(self._parse_list())
            elif char == '"':
                values.append(self._parse_quoted())
            else:
                values.append(self._parse_atom())
        return values

    def _parse_quoted(self) -> str:
        self._expect('"')
        chars: list[str] = []
        while self.index < len(self.source):
            char = self.source[self.index]
            self.index += 1
            if char == "\\" and self.index < len(self.source):
                chars.append(self.source[self.index])
                self.index += 1
            elif char == '"':
                break
            else:
                chars.append(char)
        return "".join(chars)

    def _parse_atom(self) -> Any:
        start = self.index
        while self.index < len(self.source) and self.source[self.index] not in " ()":
            self.index += 1
        token = self.source[start : self.index]
        if token.upper() == "NIL":
            return None
        if token.isdigit():
            return int(token)
        return token

    def _skip_spaces(self) -> None:
        while self.index < len(self.source) and self.source[self.index].isspace():
            self.index += 1

    def _peek(self) -> str | None:
        if self.index >= len(self.source):
            return None
        return self.source[self.index]

    def _expect(self, expected: str) -> None:
        if self._peek() != expected:
            raise ValueError(f"Expected {expected!r} in BODYSTRUCTURE")
        self.index += 1
