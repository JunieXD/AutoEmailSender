from __future__ import annotations

import asyncio
import imaplib
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from app.models import IdentityProfile
from app.services.mail_runtime import (
    MailRuntimeError,
    discover_sent_folder,
    fetch_inbox_messages_from_sender,
    fetch_incremental_inbox_messages,
    fetch_incremental_mailbox_messages,
    fetch_professor_history_inbox_messages,
    fetch_professor_history_mailbox_messages,
    format_imap_login_error,
    _test_imap_connection_sync,
    parse_received_email,
)


class _FakeImapClient:
    def __init__(
        self,
        select_status: str = "OK",
        search_data: bytes = b"",
        fetch_payload=None,
        list_status: str = "OK",
        list_payload=None,
        search_data_by_criterion: dict[str, bytes] | None = None,
        headers_by_uid: dict[int, bytes] | None = None,
    ) -> None:
        self.select_status = select_status
        self.search_data = search_data
        self.fetch_payload = fetch_payload
        self.list_status = list_status
        self.list_payload = list_payload if list_payload is not None else []
        self.search_data_by_criterion = search_data_by_criterion or {}
        self.headers_by_uid = headers_by_uid or {}
        self.search_called = False
        self.search_criteria: list[str] = []
        self.commands: list[str] = []

    def login(self, username: str, password: str):
        self.commands.append("login")
        return "OK", [b"logged in"]

    def _simple_command(self, command: str, payload: str):
        self.commands.append(command)
        return "OK", [b"id accepted"]

    def _untagged_response(self, status: str, data, command: str):
        return status, data

    def select(self, mailbox: str):
        self.commands.append(f"select:{mailbox}")
        return self.select_status, [b"EXAMINE Unsafe Login. Please contact kefu@188.com for help"]

    def list(self):
        self.commands.append("list")
        return self.list_status, self.list_payload

    def search(self, charset, criterion: str):
        self.search_called = True
        self.search_criteria.append(criterion)
        return "OK", [self.search_data]

    def uid(self, command: str, *args):
        self.commands.append(f"uid:{command}:{args}")
        if command == "SEARCH":
            self.search_called = True
            self.search_criteria.append(str(args[-1]))
            criterion = str(args[-1])
            return "OK", [self.search_data_by_criterion.get(criterion, self.search_data)]
        if command == "FETCH":
            uid = int(str(args[0]))
            query = str(args[-1])
            if "HEADER" in query:
                return "OK", [
                    (
                        b'1 (UID 1 INTERNALDATE "08-May-2026 20:30:00 +0800" BODY[HEADER] {128}',
                        self.headers_by_uid.get(
                            uid,
                            b"From: teacher@example.com\r\n"
                            b"To: sender@example.com\r\n"
                            b"Subject: Re: hello\r\n"
                            b"Message-ID: <reply-from-sender@example.com>\r\n"
                            b"Date: Fri, 08 May 2026 20:00:00 +0800\r\n\r\n",
                        ),
                    ),
                ]
            if "TEXT" in query:
                return "OK", [(b"1 (BODY[TEXT] {12}", b"reply body")]
            return "OK", []
        return "NO", []

    def fetch(self, message_id: bytes, query: str):
        self.commands.append(f"fetch:{query}")
        return "OK", self.fetch_payload or []

    def logout(self):
        return "OK", [b"logout"]


class _MultipartBase64ImapClient(_FakeImapClient):
    def uid(self, command: str, *args):
        self.commands.append(f"uid:{command}:{args}")
        if command == "SEARCH":
            self.search_called = True
            self.search_criteria.append(str(args[-1]))
            return "OK", [self.search_data]
        if command != "FETCH":
            return "NO", []

        query = str(args[-1])
        if "HEADER" in query:
            return "OK", [
                (
                    b'1 (UID 1 INTERNALDATE "08-May-2026 20:30:00 +0800" BODY[HEADER] {256}',
                    b"From: teacher@example.com\r\n"
                    b"To: sender@example.com\r\n"
                    b"Subject: Re: hello\r\n"
                    b"Message-ID: <reply-base64@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:00:00 +0800\r\n"
                    b"Content-Type: multipart/mixed; boundary=\"mix\"\r\n\r\n",
                ),
            ]
        if "BODYSTRUCTURE" in query:
            return "OK", [
                (
                    b'1 (BODYSTRUCTURE (("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL "BASE64" 12 1 NIL NIL NIL NIL)'
                    b'("APPLICATION" "PDF" NIL NIL NIL "BASE64" 999 NIL ("ATTACHMENT" ("FILENAME" "cv.pdf")) NIL NIL) '
                    b'"MIXED" ("BOUNDARY" "mix") NIL NIL))',
                ),
            ]
        if "BODY.PEEK[1.MIME]" in query:
            return "OK", [
                (
                    b"1 (BODY[1.MIME] {96}",
                    b"Content-Type: text/plain; charset=utf-8\r\n"
                    b"Content-Transfer-Encoding: base64\r\n\r\n",
                ),
            ]
        if "BODY.PEEK[1]" in query:
            return "OK", [(b"1 (BODY[1] {12}", b"5L2g5aW9\r\n")]
        if "BODY.PEEK[2" in query:
            raise AssertionError("attachment part should not be fetched")
        return "OK", []


class _MultipartFallbackImapClient(_FakeImapClient):
    def uid(self, command: str, *args):
        self.commands.append(f"uid:{command}:{args}")
        if command == "SEARCH":
            self.search_called = True
            self.search_criteria.append(str(args[-1]))
            return "OK", [self.search_data]
        if command != "FETCH":
            return "NO", []

        query = str(args[-1])
        if "HEADER" in query:
            return "OK", [
                (
                    b'1 (UID 1 INTERNALDATE "08-May-2026 20:30:00 +0800" BODY[HEADER] {256}',
                    b"From: teacher@example.com\r\n"
                    b"To: sender@example.com\r\n"
                    b"Subject: Re: hello\r\n"
                    b"Message-ID: <reply-fallback@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:00:00 +0800\r\n"
                    b"Content-Type: multipart/mixed; boundary=\"mix\"\r\n\r\n",
                ),
            ]
        if "BODYSTRUCTURE" in query:
            return "OK", [(b'1 (BODYSTRUCTURE ("APPLICATION" "OCTET-STREAM" NIL NIL NIL "BASE64" 8 NIL NIL NIL))',)]
        if "BODY.PEEK[TEXT]" in query:
            return "OK", [
                (
                    b"1 (BODY[TEXT] {256}",
                    b"--mix\r\n"
                    b"Content-Type: text/plain; charset=utf-8\r\n"
                    b"Content-Transfer-Encoding: base64\r\n\r\n"
                    b"5L2g5aW9\r\n"
                    b"--mix\r\n"
                    b"Content-Type: application/pdf\r\n"
                    b"Content-Disposition: attachment; filename=\"cv.pdf\"\r\n\r\n"
                    b"ignored attachment\r\n"
                    b"--mix--\r\n",
                ),
            ]
        return "OK", []


def _build_identity() -> IdentityProfile:
    return IdentityProfile(
        name="测试身份",
        profile_name="测试身份",
        sender_name="测试同学",
        email_address="sender@example.com",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_username="sender@example.com",
        smtp_password="secret",
        imap_host="imap.example.com",
        imap_port=993,
        imap_username="sender@example.com",
        imap_password="secret",
    )


class MailRuntimeTest(unittest.TestCase):
    def test_imap_login_failure_mentions_authorization_code_for_qq_or_163(self) -> None:
        identity = _build_identity()
        identity.imap_host = "imap.qq.com"

        message = format_imap_login_error(identity, "AUTHENTICATIONFAILED")

        self.assertIn("授权码", message)
        self.assertIn("IMAP/SMTP", message)

    def test_imap_connection_sends_client_id_before_selecting_inbox(self) -> None:
        client = _FakeImapClient(select_status="OK")
        previous_id_command = imaplib.Commands.pop("ID", None)
        self.addCleanup(
            lambda: imaplib.Commands.__setitem__("ID", previous_id_command)
            if previous_id_command is not None
            else imaplib.Commands.pop("ID", None),
        )

        with patch("app.services.mail_runtime._open_imap_client", return_value=client):
            _test_imap_connection_sync(_build_identity())

        self.assertEqual(client.commands[:3], ["login", "ID", "select:INBOX"])
        self.assertIn("ID", imaplib.Commands)

    def test_imap_connection_fails_when_inbox_select_is_rejected(self) -> None:
        client = _FakeImapClient(select_status="NO")

        with patch("app.services.mail_runtime._open_imap_client", return_value=client):
            with self.assertRaisesRegex(MailRuntimeError, "IMAP 选择收件箱失败"):
                _test_imap_connection_sync(_build_identity())

    def test_fetch_messages_from_sender_uses_from_search_without_rfc822(self) -> None:
        client = _FakeImapClient(search_data=b"1")

        with patch("app.services.mail_runtime._open_imap_client", return_value=client):
            messages = asyncio.run(
                fetch_inbox_messages_from_sender(_build_identity(), "teacher@example.com"),
            )

        self.assertEqual(client.search_criteria[-1], '(FROM "teacher@example.com")')
        self.assertNotIn("RFC822", " ".join(client.commands))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].from_email, "teacher@example.com")

    def test_discover_sent_folder_prefers_special_use_sent(self) -> None:
        client = _FakeImapClient(
            list_payload=[
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren \\Sent) "/" "Sent Items"',
            ],
        )

        with patch("app.services.mail_runtime._open_imap_client", return_value=client):
            folder = asyncio.run(discover_sent_folder(_build_identity()))

        self.assertEqual(folder, "Sent Items")
        self.assertIn("list", client.commands)
        self.assertIn("select:Sent Items", client.commands)

    def test_discover_sent_folder_falls_back_without_list_support(self) -> None:
        client = _FakeImapClient()
        client.list = None

        with patch("app.services.mail_runtime._open_imap_client", return_value=client):
            folder = asyncio.run(discover_sent_folder(_build_identity()))

        self.assertEqual(folder, "Sent")
        self.assertIn("select:Sent", client.commands)

    def test_incremental_fetch_sent_mailbox_selects_folder_and_parses_recipients(self) -> None:
        client = _FakeImapClient(
            search_data=b"5",
            headers_by_uid={
                5: (
                    b"From: sender@example.com\r\n"
                    b"To: Teacher <Teacher@Example.com>, other@example.com\r\n"
                    b"Cc: Copy <COPY@example.com>\r\n"
                    b"Bcc: Hidden <hidden@example.com>\r\n"
                    b"Subject: hello\r\n"
                    b"Message-ID: <sent-message@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:00:00 +0800\r\n\r\n"
                ),
            },
        )

        with patch("app.services.mail_runtime._open_imap_client", return_value=client):
            max_seen_uid, messages = asyncio.run(
                fetch_incremental_mailbox_messages(_build_identity(), "Sent", None),
            )

        self.assertEqual(max_seen_uid, 5)
        self.assertIn("select:Sent", client.commands)
        self.assertEqual(messages[0].to_emails, ["teacher@example.com", "other@example.com"])
        self.assertEqual(messages[0].cc_emails, ["copy@example.com"])
        self.assertEqual(messages[0].bcc_emails, ["hidden@example.com"])
        self.assertEqual(messages[0].raw_to, "Teacher <Teacher@Example.com>, other@example.com")
        self.assertEqual(messages[0].headers["bcc"], "Hidden <hidden@example.com>")

    def test_sent_history_searches_to_and_cc_and_deduplicates_uids(self) -> None:
        client = _FakeImapClient(
            search_data_by_criterion={
                '(TO "teacher@example.com")': b"7 8",
                '(CC "teacher@example.com")': b"8 9",
            },
            headers_by_uid={
                7: (
                    b"From: sender@example.com\r\n"
                    b"To: teacher@example.com\r\n"
                    b"Subject: first\r\n"
                    b"Message-ID: <sent-7@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:00:00 +0800\r\n\r\n"
                ),
                8: (
                    b"From: sender@example.com\r\n"
                    b"To: teacher@example.com\r\n"
                    b"Cc: teacher@example.com\r\n"
                    b"Subject: duplicate\r\n"
                    b"Message-ID: <sent-8@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:01:00 +0800\r\n\r\n"
                ),
                9: (
                    b"From: sender@example.com\r\n"
                    b"Cc: teacher@example.com\r\n"
                    b"Subject: copy\r\n"
                    b"Message-ID: <sent-9@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:02:00 +0800\r\n\r\n"
                ),
            },
        )

        with patch("app.services.mail_runtime._open_imap_client", return_value=client):
            messages = asyncio.run(
                fetch_professor_history_mailbox_messages(
                    _build_identity(),
                    "Sent",
                    "teacher@example.com",
                    folder_role="sent",
                ),
            )

        self.assertEqual(client.search_criteria, ['(TO "teacher@example.com")', '(CC "teacher@example.com")'])
        self.assertIn("select:Sent", client.commands)
        self.assertEqual([message.uid for message in messages], [7, 8, 9])

    def test_incremental_fetch_reads_body_and_internaldate(self) -> None:
        client = _FakeImapClient(search_data=b"1")

        with patch("app.services.mail_runtime._open_imap_client", return_value=client):
            max_seen_uid, messages = asyncio.run(
                fetch_incremental_inbox_messages(_build_identity(), None),
            )

        self.assertEqual(max_seen_uid, 1)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].body_text, "reply body")
        self.assertEqual(messages[0].received_at, datetime(2026, 5, 8, 12, 30, tzinfo=UTC))
        serialized_commands = " ".join(client.commands)
        self.assertIn("BODY.PEEK[TEXT]", serialized_commands)
        self.assertNotIn("RFC822", serialized_commands)

    def test_professor_history_inbox_wrapper_still_searches_from_sender(self) -> None:
        client = _FakeImapClient(search_data=b"1")

        with patch("app.services.mail_runtime._open_imap_client", return_value=client):
            messages = asyncio.run(
                fetch_professor_history_inbox_messages(_build_identity(), "teacher@example.com"),
            )

        self.assertEqual(client.search_criteria[-1], '(FROM "teacher@example.com")')
        self.assertIn("select:INBOX", client.commands)
        self.assertEqual([message.uid for message in messages], [1])

    def test_incremental_fetch_decodes_base64_text_part_without_fetching_attachment(self) -> None:
        client = _MultipartBase64ImapClient(search_data=b"1")

        with patch("app.services.mail_runtime._open_imap_client", return_value=client):
            _, messages = asyncio.run(
                fetch_incremental_inbox_messages(_build_identity(), None),
            )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].body_text, "\u4f60\u597d")
        serialized_commands = " ".join(client.commands)
        self.assertIn("BODYSTRUCTURE", serialized_commands)
        self.assertIn("BODY.PEEK[1.MIME]", serialized_commands)
        self.assertIn("BODY.PEEK[1]", serialized_commands)
        self.assertNotIn("BODY.PEEK[2", serialized_commands)
        self.assertNotIn("RFC822", serialized_commands)

    def test_incremental_fetch_falls_back_to_decoded_body_when_bodystructure_finds_no_text(self) -> None:
        client = _MultipartFallbackImapClient(search_data=b"1")

        with patch("app.services.mail_runtime._open_imap_client", return_value=client):
            _, messages = asyncio.run(
                fetch_incremental_inbox_messages(_build_identity(), None),
            )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].body_text, "\u4f60\u597d")
        serialized_commands = " ".join(client.commands)
        self.assertIn("BODYSTRUCTURE", serialized_commands)
        self.assertIn("BODY.PEEK[TEXT]", serialized_commands)
        self.assertNotIn("ignored attachment", messages[0].body_text)
        self.assertNotIn("RFC822", serialized_commands)

    def test_parse_received_email_strips_quoted_original_message_from_plain_text(self) -> None:
        raw_message = (
            "From: juniexd <juniexd@qq.com>\r\n"
            "To: juniexd <juniexd@163.com>\r\n"
            "Subject: =?utf-8?b?5Zue5aSNOltmYWtlXQ==?=\r\n"
            "Message-ID: <reply@example.com>\r\n"
            "In-Reply-To: <sent@example.com>\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "\r\n"
            "欢迎报考\r\n"
            "---- 回复的原邮件 ----\r\n"
            "发件人 王俊杰<juniexd@163.com>\r\n"
            "尊敬的老师：这部分是原邮件正文\r\n"
        ).encode("utf-8")

        received = parse_received_email(raw_message)

        self.assertEqual(received.content, "欢迎报考")
        self.assertNotIn("回复的原邮件", received.content)
        self.assertNotIn("尊敬的老师", received.content)

    def test_parse_received_email_strips_quoted_original_message_from_html(self) -> None:
        raw_message = (
            "From: teacher@example.com\r\n"
            "To: sender@example.com\r\n"
            "Subject: Re: hello\r\n"
            "Message-ID: <reply-html@example.com>\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<p>欢迎报考</p>"
            "<div>---- 回复的原邮件 ----</div>"
            "<p>尊敬的老师：这部分是原邮件正文</p>\r\n"
        ).encode("utf-8")

        received = parse_received_email(raw_message)

        self.assertEqual(received.content, "欢迎报考")
        self.assertIn("欢迎报考", received.content_html or "")
        self.assertNotIn("回复的原邮件", received.content_html or "")
        self.assertNotIn("尊敬的老师", received.content_html or "")

    def test_parse_received_email_ignores_style_when_deriving_text_from_html(self) -> None:
        raw_message = (
            "From: teacher@example.com\r\n"
            "To: sender@example.com\r\n"
            "Subject: Re: hello\r\n"
            "Message-ID: <reply-html-style@example.com>\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<html><head><style>#outlook a { padding:0; } body { margin:0; }</style></head>"
            "<body><p>欢迎加入课题组</p></body></html>\r\n"
        ).encode("utf-8")

        received = parse_received_email(raw_message)

        self.assertEqual(received.content, "欢迎加入课题组")
        self.assertNotIn("#outlook", received.content)
        self.assertNotIn("padding", received.content)

    def test_parse_received_email_keeps_body_text_after_head_meta_tags(self) -> None:
        raw_message = (
            "From: teacher@example.com\r\n"
            "To: sender@example.com\r\n"
            "Subject: Re: hello\r\n"
            "Message-ID: <reply-html-meta@example.com>\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<html><head><meta charset=\"utf-8\"><link href=\"mail.css\">"
            "<style>#outlook a { padding:0; }</style></head>"
            "<body><p>欢迎加入课题组</p></body></html>\r\n"
        ).encode("utf-8")

        received = parse_received_email(raw_message)

        self.assertEqual(received.content, "欢迎加入课题组")

    def test_parse_received_email_strips_chinese_reply_header_from_html(self) -> None:
        raw_message = (
            "From: teacher@example.com\r\n"
            "To: sender@example.com\r\n"
            "Subject: Re: hello\r\n"
            "Message-ID: <reply-html-chinese-quote@example.com>\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<html><body>"
            "<p>欢迎加入课题组</p>"
            "<p>发自我的手机</p>"
            "<div><span>发件人：</span><span>student@example.com</span></div>"
            "<div><span>发件时间：</span><span>2026年6月1日</span></div>"
            "<div><span>收件人：</span><span>teacher@example.com</span></div>"
            "<div><span>主题：</span><span>推免研究生自荐信</span></div>"
            "<p>尊敬的老师：这部分是原邮件正文</p>"
            "</body></html>\r\n"
        ).encode("utf-8")

        received = parse_received_email(raw_message)

        self.assertEqual(received.content, "欢迎加入课题组\n发自我的手机")
        self.assertIn("欢迎加入课题组", received.content_html or "")
        self.assertNotIn("发件人", received.content)
        self.assertNotIn("尊敬的老师", received.content)
        self.assertNotIn("发件人", received.content_html or "")
        self.assertNotIn("尊敬的老师", received.content_html or "")

    def test_parse_received_email_keeps_standalone_sender_label_in_reply_body(self) -> None:
        raw_message = (
            "From: teacher@example.com\r\n"
            "To: sender@example.com\r\n"
            "Subject: Re: hello\r\n"
            "Message-ID: <reply-html-standalone-sender-label@example.com>\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<html><body>"
            "<p>请在表格里补充发件人：导师本人。</p>"
            "<p>然后再发我。</p>"
            "</body></html>\r\n"
        ).encode("utf-8")

        received = parse_received_email(raw_message)

        self.assertIn("发件人：导师本人", received.content)
        self.assertIn("然后再发我", received.content)



    def test_parse_received_email_keeps_body_sender_label_before_chinese_quote(self) -> None:
        raw_message = (
            "From: teacher@example.com\r\n"
            "To: sender@example.com\r\n"
            "Subject: Re: hello\r\n"
            "Message-ID: <reply-html-body-sender-before-quote@example.com>\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<html><body>"
            "<p>请在表格里补充发件人：导师本人。</p>"
            "<p>然后再发我。</p>"
            "<div><span>发件人：</span><span>student@example.com</span></div>"
            "<div><span>发件时间：</span><span>2026年6月1日</span></div>"
            "<p>尊敬的老师：这部分是原邮件正文</p>"
            "</body></html>\r\n"
        ).encode("utf-8")

        received = parse_received_email(raw_message)

        self.assertEqual(received.content, "请在表格里补充发件人：导师本人。\n然后再发我。")
        self.assertIn("发件人：导师本人", received.content_html or "")
        self.assertIn("然后再发我", received.content_html or "")
        self.assertNotIn("student@example.com", received.content)
        self.assertNotIn("尊敬的老师", received.content_html or "")

    def test_parse_received_email_strips_uppercase_chinese_quote_block_from_html(self) -> None:
        raw_message = (
            "From: teacher@example.com\r\n"
            "To: sender@example.com\r\n"
            "Subject: Re: hello\r\n"
            "Message-ID: <reply-html-uppercase-chinese-quote@example.com>\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<html><body>"
            "<p>欢迎加入课题组</p>"
            "<DIV><span>发件人：</span><span>student@example.com</span></DIV>"
            "<DIV><span>发件时间：</span><span>2026年6月1日</span></DIV>"
            "<p>尊敬的老师：这部分是原邮件正文</p>"
            "</body></html>\r\n"
        ).encode("utf-8")

        received = parse_received_email(raw_message)

        self.assertEqual(received.content, "欢迎加入课题组")
        self.assertIn("欢迎加入课题组", received.content_html or "")
        self.assertNotIn("student@example.com", received.content)
        self.assertNotIn("尊敬的老师", received.content_html or "")

    def test_parse_received_email_keeps_text_sender_label_before_chinese_quote(self) -> None:
        raw_message = (
            "From: teacher@example.com\r\n"
            "To: sender@example.com\r\n"
            "Subject: Re: hello\r\n"
            "Message-ID: <reply-text-body-sender-before-quote@example.com>\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "\r\n"
            "请在表格里补充发件人：导师本人。\r\n"
            "然后再发我。\r\n"
            "发件人：student@example.com\r\n"
            "发件时间：2026年6月1日\r\n"
            "尊敬的老师：这部分是原邮件正文\r\n"
        ).encode("utf-8")

        received = parse_received_email(raw_message)

        normalized_content = "\n".join(received.content.splitlines())

        self.assertEqual(normalized_content, "请在表格里补充发件人：导师本人。\n然后再发我。")
        self.assertNotIn("student@example.com", received.content)
        self.assertNotIn("尊敬的老师", received.content)

if __name__ == "__main__":
    unittest.main()
