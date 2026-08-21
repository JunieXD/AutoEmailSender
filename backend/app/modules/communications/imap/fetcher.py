from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parseaddr
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


@dataclass(frozen=True, slots=True)
class ImapSearchResult:
    ok: bool
    uids: list[int]


class ImapFetchCommandError(RuntimeError):
    pass


_IMAP_SEARCH_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


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
    uidvalidity: int | None = None
    to_emails: list[str] = field(default_factory=list)
    cc_emails: list[str] = field(default_factory=list)
    bcc_emails: list[str] = field(default_factory=list)
    raw_from: str = ""
    raw_to: str = ""
    raw_cc: str = ""
    raw_bcc: str = ""
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
        "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM TO CC BCC SUBJECT DATE IN-REPLY-TO REFERENCES X-AUTOEMAILSENDER-DELIVERY-ID)] INTERNALDATE)",
    )
    if status != "OK" or not payload:
        return b""
    for item in payload:
        if (
            isinstance(item, tuple)
            and len(item) >= 2
            and isinstance(item[1], (bytes, bytearray))
        ):
            return bytes(item[1])
    return b""


def fetch_message_headers_payload_by_uid(client: object, uid: int) -> list[object]:
    status, payload = client.uid(
        "FETCH",
        str(uid),
        "(UID BODY.PEEK[HEADER] INTERNALDATE)",
    )
    if status != "OK" or not payload:
        return []
    return list(payload)


def fetch_message_headers_payloads_by_uid_batch(
    client: object,
    uids: list[int],
) -> list[tuple[int, list[object]]]:
    if not uids:
        return []
    uid_set = ",".join(str(uid) for uid in uids)
    status, payload = client.uid(
        "FETCH",
        uid_set,
        "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM TO CC BCC SUBJECT DATE IN-REPLY-TO REFERENCES X-AUTOEMAILSENDER-DELIVERY-ID)] INTERNALDATE)",
    )
    if status != "OK" or not payload:
        return []
    return _split_header_fetch_payload_by_uid(list(payload), uids)


def fetch_message_headers_payloads_by_uid_range(
    client: object,
    start_uid: int,
    end_uid: int,
) -> list[tuple[int, list[object]]]:
    if start_uid <= 0 or end_uid < start_uid:
        return []
    status, payload = client.uid(
        "FETCH",
        f"{start_uid}:{end_uid}",
        "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM TO CC BCC SUBJECT DATE IN-REPLY-TO REFERENCES X-AUTOEMAILSENDER-DELIVERY-ID)] INTERNALDATE)",
    )
    if _imap_status_text(status).upper() != "OK":
        detail = _format_imap_response_detail(status, payload)
        raise ImapFetchCommandError(f"IMAP header range fetch failed: {detail}")
    if not payload:
        return []
    return _split_header_fetch_payload_by_uid_range(list(payload), start_uid, end_uid)


def _imap_status_text(status: object) -> str:
    if isinstance(status, (bytes, bytearray)):
        return bytes(status).decode("utf-8", errors="ignore")
    return str(status)


def _format_imap_response_detail(status: object, payload: object) -> str:
    status_text = _imap_status_text(status)
    payload_text = _format_imap_payload_text(payload)
    if payload_text:
        return f"{status_text}: {payload_text}"
    return status_text


def _format_imap_payload_text(payload: object) -> str:
    if not payload:
        return ""
    items = payload if isinstance(payload, (list, tuple)) else [payload]
    parts: list[str] = []
    for item in items:
        if isinstance(item, (bytes, bytearray)):
            text = bytes(item).decode("utf-8", errors="ignore")
        elif isinstance(item, tuple):
            text = " ".join(_format_imap_payload_text(part) for part in item)
        else:
            text = str(item)
        text = text.strip()
        if text:
            parts.append(text)
    return " ".join(parts)[:500]


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
    return [
        int(item) for item in raw.split() if item.isdigit() and int(item) >= start_uid
    ]


def search_uids_since_date(client: object, since_date: date) -> list[int]:
    criterion = f"SINCE {_format_imap_search_date(since_date)}"
    status, payload = client.uid("SEARCH", None, criterion)
    return _parse_uid_search_payload(status, payload)


def search_uids_from_sender(client: object, from_email: str) -> list[int]:
    escaped = _escape_imap_search_value(from_email)
    status, payload = client.uid("SEARCH", None, f'(FROM "{escaped}")')
    return _parse_uid_search_payload(status, payload)


def search_uids_from_sender_since(
    client: object, from_email: str, since_date: date
) -> list[int]:
    normalized = parseaddr(from_email)[1] or from_email
    escaped = _escape_imap_search_value(normalized)
    criterion = f'(FROM "{escaped}" SINCE {_format_imap_search_date(since_date)})'
    status, payload = client.uid("SEARCH", None, criterion)
    return _parse_uid_search_payload(status, payload)


def search_uids_to_recipient(
    client: object,
    to_email: str,
    since_date: date | None = None,
) -> list[int]:
    escaped = _escape_imap_search_value(to_email)
    criterion = _recipient_search_criterion("TO", escaped, since_date)
    status, payload = client.uid("SEARCH", None, criterion)
    return _parse_uid_search_payload(status, payload)


def search_uids_cc_recipient(
    client: object,
    cc_email: str,
    since_date: date | None = None,
) -> list[int]:
    escaped = _escape_imap_search_value(cc_email)
    criterion = _recipient_search_criterion("CC", escaped, since_date)
    status, payload = client.uid("SEARCH", None, criterion)
    return _parse_uid_search_payload(status, payload)


def search_uids_bcc_recipient(
    client: object,
    bcc_email: str,
    since_date: date | None = None,
) -> list[int]:
    escaped = _escape_imap_search_value(bcc_email)
    criterion = _recipient_search_criterion("BCC", escaped, since_date)
    status, payload = client.uid("SEARCH", None, criterion)
    return _parse_uid_search_payload(status, payload)


def search_uids_combined_sent_recipient(
    client: object,
    recipient_email: str,
    since_date: date | None = None,
) -> ImapSearchResult:
    escaped = _escape_imap_search_value(recipient_email)
    recipient_criterion = f'OR (OR (TO "{escaped}") (CC "{escaped}")) (BCC "{escaped}")'
    criterion = (
        f"({recipient_criterion})"
        if since_date is None
        else f"(SINCE {_format_imap_search_date(since_date)} {recipient_criterion})"
    )
    status, payload = client.uid("SEARCH", None, criterion)
    if status != "OK":
        return ImapSearchResult(ok=False, uids=[])
    return ImapSearchResult(
        ok=True, uids=sorted(set(_parse_uid_search_payload(status, payload)))
    )


def _recipient_search_criterion(
    field: str,
    escaped_email: str,
    since_date: date | None,
) -> str:
    field_criterion = f'{field} "{escaped_email}"'
    if since_date is None:
        return f"({field_criterion})"
    return f"({field_criterion} SINCE {_format_imap_search_date(since_date)})"


def _escape_imap_search_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', r"\"")


def _format_imap_search_date(value: date) -> str:
    return f"{value.day:02d}-{_IMAP_SEARCH_MONTHS[value.month - 1]}-{value.year:04d}"


def _parse_uid_search_payload(status: str, payload: object) -> list[int]:
    if status != "OK" or not payload:
        return []
    raw = payload[0] if payload else b""
    return [int(item) for item in raw.split() if item.isdigit()]


def _split_header_fetch_payload_by_uid(
    payload: list[object],
    requested_uids: list[int],
) -> list[tuple[int, list[object]]]:
    results: list[tuple[int, list[object]]] = []
    requested_uid_set = set(requested_uids)
    for item in payload:
        if not isinstance(item, tuple):
            continue
        uid = _extract_uid_from_fetch_response(item[0])
        if uid is None or uid not in requested_uid_set:
            continue
        results.append((uid, [item]))
    return results


def _split_header_fetch_payload_by_uid_range(
    payload: list[object],
    start_uid: int,
    end_uid: int,
) -> list[tuple[int, list[object]]]:
    results: list[tuple[int, list[object]]] = []
    for item in payload:
        if not isinstance(item, tuple):
            continue
        uid = _extract_uid_from_fetch_response(item[0])
        if uid is None or uid < start_uid or uid > end_uid:
            continue
        results.append((uid, [item]))
    results.sort(key=lambda item: item[0])
    return results


def _extract_uid_from_fetch_response(response: object) -> int | None:
    text = (
        response.decode("utf-8", errors="ignore")
        if isinstance(response, (bytes, bytearray))
        else str(response)
    )
    parts = text.replace("(", " ").replace(")", " ").split()
    for index, part in enumerate(parts):
        if (
            part.upper() == "UID"
            and index + 1 < len(parts)
            and parts[index + 1].isdigit()
        ):
            return int(parts[index + 1])
    return None


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
        if (
            isinstance(item, tuple)
            and len(item) >= 2
            and isinstance(item[1], (bytes, bytearray))
        ):
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

    if (
        len(parsed) < 2
        or not isinstance(parsed[0], str)
        or not isinstance(parsed[1], str)
    ):
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
