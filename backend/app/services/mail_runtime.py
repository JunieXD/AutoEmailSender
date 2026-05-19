from __future__ import annotations

import asyncio
import time
import mimetypes
import smtplib
import imaplib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formataddr, make_msgid, parseaddr, parsedate_to_datetime
from html import escape
from html.parser import HTMLParser
from imaplib import IMAP4, IMAP4_SSL
from pathlib import Path
from socket import timeout as SocketTimeout
from typing import Any

from app.core.config import get_settings
from app.models import IdentityProfile, Professor
from app.services.imap_message_fetcher import (
    ImapFetchedMessage,
    fetch_message_headers_payload_by_uid,
    fetch_text_body_parts_by_uid,
    parse_text_parts_from_message,
    search_uids_from_sender,
    search_uids_since,
)


IMAP_CLIENT_ID_NAME = "AutoEmailSender"
IMAP_CLIENT_ID_VERSION = "3.0.0"
IMAP_CLIENT_ID_VENDOR = "AutoEmailSender"
REPLY_QUOTE_TEXT_MARKERS = (
    "---- 回复的原邮件 ----",
    "----- 回复的原邮件 -----",
    "---- 原始邮件 ----",
    "----- 原始邮件 -----",
    "-----Original Message-----",
    "-------- Original Message --------",
)
REPLY_QUOTE_HTML_PATTERNS = (
    re.compile(r"<[^>]*>\s*-{2,}\s*(回复的原邮件|原始邮件)\s*-{2,}", re.IGNORECASE),
    re.compile(r"-{2,}\s*(回复的原邮件|原始邮件)\s*-{2,}", re.IGNORECASE),
    re.compile(r"<[^>]*>\s*-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"<blockquote\b", re.IGNORECASE),
)
HTML_TEXT_BLOCK_TAGS = {
    "br",
    "div",
    "li",
    "p",
    "tr",
    "table",
    "ul",
    "ol",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}


class MailRuntimeError(RuntimeError):
    pass


@dataclass(slots=True)
class SendMailResult:
    message_id: str
    provider_payload: dict[str, Any]


@dataclass(slots=True)
class MailAttachment:
    file_path: str
    download_name: str


@dataclass(slots=True)
class ReceivedEmail:
    from_email: str
    subject: str | None
    content: str
    content_html: str | None
    message_id: str | None
    in_reply_to: str | None
    references: str | None
    sent_at: datetime
    headers: dict[str, str]
    received_at: datetime | None = None

class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in HTML_TEXT_BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in HTML_TEXT_BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def get_text(self) -> str:
        return re.sub(r"[ \t\r\f\v]+", " ", "".join(self.parts)).strip()


async def test_smtp_connection(identity: IdentityProfile) -> tuple[bool, str]:
    try:
        await asyncio.to_thread(_test_smtp_connection_sync, identity)
    except MailRuntimeError as exc:
        return False, str(exc)
    return True, "SMTP 连接测试成功"


async def test_imap_connection(identity: IdentityProfile) -> tuple[bool, str]:
    if not identity.imap_host or not identity.imap_username or not identity.imap_password:
        return False, "当前身份未完整配置 IMAP"
    try:
        await asyncio.to_thread(_test_imap_connection_sync, identity)
    except MailRuntimeError as exc:
        return False, str(exc)
    return True, "IMAP 连接测试成功"


async def send_email(
    *,
    identity: IdentityProfile,
    professor: Professor,
    subject: str,
    body_text: str,
    body_html: str | None,
    attachments: list[MailAttachment],
) -> SendMailResult:
    if not professor.email:
        raise MailRuntimeError("导师没有可用邮箱，无法发送")

    message = build_email_message(
        identity=identity,
        professor=professor,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        attachments=attachments,
    )
    await asyncio.to_thread(_send_email_sync, identity, message)
    return SendMailResult(
        message_id=message["Message-ID"],
        provider_payload={
            "smtp_host": identity.smtp_host,
            "smtp_port": identity.smtp_port,
            "to": professor.email,
        },
    )


async def send_email_to_recipient(
    *,
    identity: IdentityProfile,
    recipient_name: str,
    recipient_email: str,
    subject: str,
    body_text: str,
    body_html: str | None,
    attachments: list[MailAttachment],
) -> SendMailResult:
    recipient = Professor(
        name=recipient_name or recipient_email,
        email=recipient_email,
    )
    message = build_email_message(
        identity=identity,
        professor=recipient,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        attachments=attachments,
    )
    await asyncio.to_thread(_send_email_sync, identity, message)
    return SendMailResult(
        message_id=message["Message-ID"],
        provider_payload={
            "smtp_host": identity.smtp_host,
            "smtp_port": identity.smtp_port,
            "to": recipient_email,
        },
    )


async def fetch_incremental_inbox_messages(
    identity: IdentityProfile,
    last_seen_uid: int | None,
) -> tuple[int | None, list[ImapFetchedMessage]]:
    if not identity.imap_host or not identity.imap_username or not identity.imap_password:
        return last_seen_uid, []
    return await asyncio.to_thread(_fetch_incremental_inbox_messages_sync, identity, last_seen_uid)


async def fetch_professor_history_inbox_messages(
    identity: IdentityProfile,
    professor_email: str,
) -> list[ImapFetchedMessage]:
    if not identity.imap_host or not identity.imap_username or not identity.imap_password:
        return []
    return await asyncio.to_thread(_fetch_professor_history_inbox_messages_sync, identity, professor_email)


async def fetch_inbox_messages_from_sender(
    identity: IdentityProfile,
    from_email: str,
) -> list[ReceivedEmail]:
    if not identity.imap_host or not identity.imap_username or not identity.imap_password:
        return []
    if not from_email.strip():
        return []
    messages = await fetch_professor_history_inbox_messages(identity, from_email.strip().lower())
    return [_imap_fetched_to_received(message) for message in messages]


def build_email_message(
    *,
    identity: IdentityProfile,
    professor: Professor,
    subject: str,
    body_text: str,
    body_html: str | None,
    attachments: list[MailAttachment],
) -> EmailMessage:
    from app.services.outreach_templates import get_identity_sender_name

    message = EmailMessage()
    message["From"] = formataddr((get_identity_sender_name(identity), identity.email_address))
    message["To"] = professor.email or ""
    message["Subject"] = subject
    message["Message-ID"] = make_msgid(domain=identity.email_address.split("@")[-1])
    message["Date"] = email_datetime_now()
    message.set_content(body_text)
    message.add_alternative(body_html or text_to_html(body_text), subtype="html")

    for attachment in attachments:
        path = Path(attachment.file_path)
        if not path.exists() or not path.is_file():
            continue
        mime_type, _ = mimetypes.guess_type(attachment.download_name)
        maintype, subtype = (mime_type or "application/octet-stream").split("/", 1)
        message.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=attachment.download_name,
        )

    return message


def text_to_html(body_text: str) -> str:
    paragraphs = [segment.strip() for segment in body_text.split("\n\n") if segment.strip()]
    if not paragraphs:
        return "<p></p>"
    return "".join(f"<p>{escape(paragraph).replace(chr(10), '<br/>')}</p>" for paragraph in paragraphs)


def email_datetime_now() -> str:
    return datetime.now(UTC).astimezone().strftime("%a, %d %b %Y %H:%M:%S %z")


def _test_smtp_connection_sync(identity: IdentityProfile) -> None:
    server = None
    try:
        server = _open_smtp_client(identity)
        server.login(identity.smtp_username, identity.smtp_password)
    except (OSError, smtplib.SMTPException, SocketTimeout) as exc:
        raise MailRuntimeError(f"SMTP 连接失败: {exc}") from exc
    finally:
        if server is not None:
            try:
                server.quit()
            except OSError:
                pass


def _test_imap_connection_sync(identity: IdentityProfile) -> None:
    client: IMAP4 | IMAP4_SSL | None = None
    try:
        client = _open_imap_client(identity)
        client.login(identity.imap_username or "", identity.imap_password or "")
        _send_imap_client_id(client, identity)
        _select_inbox_or_raise(client)
    except OSError as exc:
        raise MailRuntimeError(format_imap_login_error(identity, exc)) from exc
    finally:
        if client is not None:
            try:
                client.logout()
            except OSError:
                pass


def _send_email_sync(identity: IdentityProfile, message: EmailMessage) -> None:
    server = None
    try:
        server = _open_smtp_client(identity)
        server.login(identity.smtp_username, identity.smtp_password)
        server.send_message(message)
    except (OSError, smtplib.SMTPException, SocketTimeout) as exc:
        raise MailRuntimeError(f"SMTP 发信失败: {exc}") from exc
    finally:
        if server is not None:
            try:
                server.quit()
            except OSError:
                pass


def format_imap_login_error(identity: IdentityProfile, detail: object) -> str:
    host = (identity.imap_host or "").lower()
    base = f"IMAP 登录失败: {detail}"
    if any(
        provider in host
        for provider in [
            "imap.qq.com",
            "imap.163.com",
            "imap.126.com",
            "imap.yeah.net",
        ]
    ):
        return f"{base}。请确认已开启 IMAP/SMTP 服务，并使用邮箱客户端授权码而不是网页登录密码。"
    return base


def _fetch_incremental_inbox_messages_sync(
    identity: IdentityProfile,
    last_seen_uid: int | None,
) -> tuple[int | None, list[ImapFetchedMessage]]:
    client: IMAP4 | IMAP4_SSL | None = None
    messages: list[ImapFetchedMessage] = []
    max_seen_uid = last_seen_uid
    try:
        client = _open_logged_in_imap_client(identity)
        uids = search_uids_since(client, last_seen_uid)
        for uid in uids:
            max_seen_uid = max(max_seen_uid or 0, uid)
            message = _fetch_message_by_uid_sync(client, uid)
            if message is not None:
                messages.append(message)
    except MailRuntimeError:
        raise
    except OSError as exc:
        raise MailRuntimeError(f"IMAP 增量同步失败: {exc}") from exc
    finally:
        _logout_imap_client(client)
    return max_seen_uid, messages


def _fetch_professor_history_inbox_messages_sync(
    identity: IdentityProfile,
    professor_email: str,
) -> list[ImapFetchedMessage]:
    client: IMAP4 | IMAP4_SSL | None = None
    messages: list[ImapFetchedMessage] = []
    try:
        client = _open_logged_in_imap_client(identity)
        for uid in search_uids_from_sender(client, professor_email):
            message = _fetch_message_by_uid_sync(client, uid)
            if message is not None:
                messages.append(message)
    except MailRuntimeError:
        raise
    except OSError as exc:
        raise MailRuntimeError(f"IMAP 导师历史同步失败: {exc}") from exc
    finally:
        _logout_imap_client(client)
    return messages


def _imap_fetched_to_received(message: ImapFetchedMessage) -> ReceivedEmail:
    return ReceivedEmail(
        from_email=message.from_email,
        subject=message.subject,
        content=message.body_text,
        content_html=message.body_html,
        message_id=message.message_id,
        in_reply_to=message.in_reply_to,
        references=message.references,
        sent_at=message.sent_at,
        headers=message.headers,
        received_at=message.received_at,
    )


def _open_logged_in_imap_client(identity: IdentityProfile) -> IMAP4 | IMAP4_SSL:
    client = _open_imap_client(identity)
    try:
        client.login(identity.imap_username or "", identity.imap_password or "")
    except OSError as exc:
        raise MailRuntimeError(format_imap_login_error(identity, exc)) from exc
    _send_imap_client_id(client, identity)
    _select_inbox_or_raise(client)
    return client


def _fetch_message_by_uid_sync(
    client: IMAP4 | IMAP4_SSL,
    uid: int,
) -> ImapFetchedMessage | None:
    header_payload = fetch_message_headers_payload_by_uid(client, uid)
    raw_headers = _extract_message_bytes_from_fetch_payload(header_payload)
    if not raw_headers:
        return None
    parsed_parts = fetch_text_body_parts_by_uid(client, uid)
    if parsed_parts.body_text or parsed_parts.body_html:
        body_text = strip_quoted_reply_text(parsed_parts.body_text or "")
        body_html = strip_quoted_reply_html(parsed_parts.body_html or "") or None
        if not body_text and body_html:
            body_text = strip_quoted_reply_text(convert_html_to_text(body_html))
    else:
        raw_body = _fetch_message_body_by_uid(client, uid)
        body_text, body_html = _parse_fetched_body(raw_headers, raw_body)
    received_at = _extract_received_at_from_fetch_payload(header_payload)
    return _parse_fetched_headers(uid, raw_headers, body_text, body_html, received_at)


def _fetch_message_header_payload_by_uid(
    client: IMAP4 | IMAP4_SSL,
    uid: int,
) -> list[object]:
    status, payload = client.uid(
        "FETCH",
        str(uid),
        "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM TO CC SUBJECT DATE IN-REPLY-TO REFERENCES)] INTERNALDATE)",
    )
    if status != "OK" or not payload:
        return []
    return list(payload)


def _extract_message_bytes_from_fetch_payload(payload: list[object]) -> bytes:
    for item in payload:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return b""


def _fetch_message_body_by_uid(client: IMAP4 | IMAP4_SSL, uid: int) -> bytes:
    status, payload = client.uid("FETCH", str(uid), "(BODY.PEEK[TEXT])")
    if status != "OK" or not payload:
        return b""
    return _extract_message_bytes_from_fetch_payload(list(payload))


def _parse_fetched_body(raw_headers: bytes, raw_body: bytes) -> tuple[str | None, str | None]:
    if not raw_body:
        return None, None
    parsed = BytesParser(policy=policy.default).parsebytes(raw_headers + b"\r\n" + raw_body)
    parsed_parts = parse_text_parts_from_message(parsed)
    body_text = strip_quoted_reply_text(parsed_parts.body_text or "")
    body_html = strip_quoted_reply_html(parsed_parts.body_html or "") or None
    if not body_text and body_html:
        body_text = strip_quoted_reply_text(convert_html_to_text(body_html))
    if not body_text:
        fallback_charset = parsed.get_content_charset() or "utf-8"
        body_text = strip_quoted_reply_text(raw_body.decode(fallback_charset, errors="replace"))
    return body_text, body_html


def _headers_indicate_multipart(raw_headers: bytes) -> bool:
    parsed = BytesParser(policy=policy.default).parsebytes(raw_headers)
    return parsed.get_content_maintype().lower() == "multipart"


def _parse_fetched_headers(
    uid: int,
    raw_headers: bytes,
    body_text: str | None,
    body_html: str | None,
    received_at: datetime | None,
) -> ImapFetchedMessage | None:
    parsed = BytesParser(policy=policy.default).parsebytes(raw_headers)
    from_email = parseaddr(parsed.get("From", ""))[1].strip().lower()
    if not from_email:
        return None
    subject = decode_mime_header(parsed.get("Subject"))
    message_id = parsed.get("Message-ID")
    in_reply_to = parsed.get("In-Reply-To")
    references = parsed.get("References")
    sent_at = datetime.now(UTC)
    if parsed.get("Date"):
        try:
            parsed_at = parsedate_to_datetime(parsed.get("Date"))
            sent_at = parsed_at.astimezone(UTC) if parsed_at.tzinfo else parsed_at.replace(tzinfo=UTC)
        except (TypeError, ValueError, IndexError):
            pass
    headers = {
        "from": parsed.get("From", ""),
        "to": parsed.get("To", ""),
        "subject": subject or "",
        "message_id": message_id or "",
        "in_reply_to": in_reply_to or "",
        "references": references or "",
    }
    return ImapFetchedMessage(
        uid=uid,
        from_email=from_email,
        subject=subject,
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        sent_at=sent_at,
        received_at=received_at,
        headers=headers,
        body_text=body_text or "",
        body_html=body_html,
    )


def _logout_imap_client(client: IMAP4 | IMAP4_SSL | None) -> None:
    if client is None:
        return
    try:
        client.logout()
    except OSError:
        pass


def _send_imap_client_id(client: IMAP4 | IMAP4_SSL, identity: IdentityProfile) -> None:
    simple_command = getattr(client, "_simple_command", None)
    untagged_response = getattr(client, "_untagged_response", None)
    if not callable(simple_command) or not callable(untagged_response):
        return

    imaplib.Commands.setdefault("ID", ("AUTH", "SELECTED"))
    args = (
        "name",
        IMAP_CLIENT_ID_NAME,
        "contact",
        identity.email_address,
        "version",
        IMAP_CLIENT_ID_VERSION,
        "vendor",
        IMAP_CLIENT_ID_VENDOR,
    )
    payload = '("' + '" "'.join(_escape_imap_id_value(item) for item in args) + '")'
    try:
        status, data = simple_command("ID", payload)
        untagged_response(status, data, "ID")
    except Exception:
        return


def _escape_imap_id_value(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', r"\"")


def _escape_imap_search_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', r"\"")


def _extract_received_at_from_fetch_payload(payload: list[object]) -> datetime | None:
    for item in payload:
        if not isinstance(item, tuple) or not item:
            continue
        response = item[0]
        if not isinstance(response, (bytes, bytearray)):
            continue
        internaldate = imaplib.Internaldate2tuple(bytes(response))
        if internaldate is not None:
            return datetime.fromtimestamp(time.mktime(internaldate), tz=UTC)
    return None


def _select_inbox_or_raise(client: IMAP4 | IMAP4_SSL) -> None:
    status, data = client.select("INBOX")
    if status == "OK":
        return
    detail = _format_imap_response(data)
    raise MailRuntimeError(f"IMAP 选择收件箱失败: {detail}")


def _format_imap_response(data: object) -> str:
    if isinstance(data, (list, tuple)):
        parts = data
    else:
        parts = [data]

    text_parts: list[str] = []
    for part in parts:
        if isinstance(part, bytes):
            text_parts.append(part.decode("utf-8", errors="replace"))
        elif part is not None:
            text_parts.append(str(part))
    return "; ".join(text_parts) or "服务商未返回原因"


def parse_received_email(raw_message: bytes) -> ReceivedEmail:
    parsed = BytesParser(policy=policy.default).parsebytes(raw_message)
    subject = decode_mime_header(parsed.get("Subject"))
    from_email = parseaddr(parsed.get("From", ""))[1].strip().lower()
    message_id = parsed.get("Message-ID")
    in_reply_to = parsed.get("In-Reply-To")
    references = parsed.get("References")

    sent_at = datetime.now(UTC)
    if parsed.get("Date"):
        try:
            parsed_at = parsedate_to_datetime(parsed.get("Date"))
            sent_at = parsed_at.astimezone(UTC) if parsed_at.tzinfo else parsed_at.replace(tzinfo=UTC)
        except (TypeError, ValueError, IndexError):
            pass

    body_text, body_html = extract_message_content(parsed)
    headers = {
        "from": parsed.get("From", ""),
        "to": parsed.get("To", ""),
        "subject": subject or "",
        "message_id": message_id or "",
        "in_reply_to": in_reply_to or "",
        "references": references or "",
    }
    return ReceivedEmail(
        from_email=from_email,
        subject=subject,
        content=body_text,
        content_html=body_html,
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        sent_at=sent_at,
        headers=headers,
    )


def extract_message_content(message: EmailMessage) -> tuple[str, str | None]:
    text_parts: list[str] = []
    html_parts: list[str] = []

    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition = part.get_content_disposition()
            if disposition == "attachment":
                continue
            content_type = part.get_content_type()
            payload = part.get_content()
            if content_type == "text/plain":
                text_parts.append(str(payload))
            elif content_type == "text/html":
                html_parts.append(str(payload))
    else:
        payload = message.get_content()
        if message.get_content_type() == "text/html":
            html_parts.append(str(payload))
        else:
            text_parts.append(str(payload))

    text_content = strip_quoted_reply_text(
        "\n".join(part.strip() for part in text_parts if part.strip()).strip(),
    )
    html_content = strip_quoted_reply_html(
        "\n".join(part.strip() for part in html_parts if part.strip()).strip(),
    ) or None
    if not text_content and html_content:
        text_content = strip_quoted_reply_text(convert_html_to_text(html_content))
    return text_content or "", html_content


def convert_html_to_text(content: str) -> str:
    parser = _HtmlTextExtractor()
    parser.feed(content)
    parser.close()
    return "\n".join(
        line.strip()
        for line in parser.get_text().splitlines()
        if line.strip()
    )


def strip_quoted_reply_text(content: str) -> str:
    next_content = content.strip()
    for marker in REPLY_QUOTE_TEXT_MARKERS:
        marker_index = next_content.find(marker)
        if marker_index >= 0:
            next_content = next_content[:marker_index]
            break
    return next_content.strip()


def strip_quoted_reply_html(content: str) -> str:
    next_content = content.strip()
    marker_index: int | None = None
    for pattern in REPLY_QUOTE_HTML_PATTERNS:
        match = pattern.search(next_content)
        if match and (marker_index is None or match.start() < marker_index):
            marker_index = match.start()
    if marker_index is not None:
        last_tag_start = next_content.rfind("<", 0, marker_index)
        last_tag_end = next_content.rfind(">", 0, marker_index)
        if last_tag_start > last_tag_end:
            marker_index = last_tag_start
        next_content = next_content[:marker_index]
    return next_content.strip()


def decode_mime_header(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, ValueError):
        return value


def _open_smtp_client(identity: IdentityProfile) -> smtplib.SMTP:
    timeout = get_settings().smtp_send_timeout_seconds
    if identity.smtp_port == 465:
        return smtplib.SMTP_SSL(identity.smtp_host, identity.smtp_port, timeout=timeout)

    server = smtplib.SMTP(identity.smtp_host, identity.smtp_port, timeout=timeout)
    server.ehlo()
    server.starttls()
    server.ehlo()
    return server


def _open_imap_client(identity: IdentityProfile) -> IMAP4 | IMAP4_SSL:
    timeout = get_settings().smtp_send_timeout_seconds
    if identity.imap_port == 993:
        return IMAP4_SSL(identity.imap_host or "", identity.imap_port or 993, timeout=timeout)
    return IMAP4(identity.imap_host or "", identity.imap_port or 143, timeout=timeout)
