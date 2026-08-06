from __future__ import annotations

import asyncio
import imaplib
import re
import unittest
from datetime import UTC, date, datetime
from unittest.mock import patch

from app.models import IdentityProfile
from app.modules.communications.transport import (
    MailRuntimeError,
    SMTP_CREDENTIAL_ENCODING_ERROR_MESSAGE,
    discover_sent_folder,
    fetch_inbox_messages_from_sender,
    fetch_incremental_inbox_messages,
    fetch_incremental_mailbox_messages,
    fetch_incremental_mailbox_messages_with_uidvalidity,
    fetch_history_mailbox_message_headers_before_uid,
    fetch_professor_history_inbox_messages,
    fetch_professor_history_mailbox_message_headers,
    fetch_professor_history_mailbox_message_headers_with_command_count,
    fetch_professor_history_mailbox_messages,
    fetch_professor_history_mailbox_messages_by_uid,
    fetch_recent_mailbox_message_headers_since,
    format_imap_login_error,
    _test_imap_connection_sync,
    parse_received_email,
    search_mailbox_uids_since_date,
    send_email_to_recipient,
    test_smtp_connection,
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
        login_error: Exception | None = None,
        logout_error: Exception | None = None,
        select_status_by_mailbox: dict[str, str] | None = None,
        select_data: list[bytes] | None = None,
        search_status_by_criterion: dict[str, str] | None = None,
        append_status: str = "OK",
    ) -> None:
        self.select_status = select_status
        self.search_data = search_data
        self.fetch_payload = fetch_payload
        self.list_status = list_status
        self.list_payload = list_payload if list_payload is not None else []
        self.search_data_by_criterion = search_data_by_criterion or {}
        self.headers_by_uid = headers_by_uid or {}
        self.login_error = login_error
        self.logout_error = logout_error
        self.select_status_by_mailbox = select_status_by_mailbox or {}
        self.select_data = select_data
        self.search_status_by_criterion = search_status_by_criterion or {}
        self.append_status = append_status
        self.search_called = False
        self.search_criteria: list[str] = []
        self.commands: list[str] = []
        self.append_calls: list[tuple[str, str | None, object | None, bytes]] = []
        self._search_counts_by_criterion: dict[str, int] = {}
        self._appended_message_ids: dict[str, bytes] = {}

    def login(self, username: str, password: str):
        self.commands.append("login")
        if self.login_error is not None:
            raise self.login_error
        return "OK", [b"logged in"]

    def _simple_command(self, command: str, payload: str):
        self.commands.append(command)
        return "OK", [b"id accepted"]

    def _untagged_response(self, status: str, data, command: str):
        return status, data

    def select(self, mailbox: str):
        self.commands.append(f"select:{mailbox}")
        status = self.select_status_by_mailbox.get(mailbox, self.select_status)
        return status, self.select_data or [b"EXAMINE Unsafe Login. Please contact kefu@188.com for help"]

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
            self._search_counts_by_criterion[criterion] = self._search_counts_by_criterion.get(criterion, 0) + 1
            search_data = self.search_data_by_criterion.get(
                criterion,
                self._appended_message_ids.get(criterion, self.search_data),
            )
            if isinstance(search_data, list):
                index = min(self._search_counts_by_criterion[criterion] - 1, len(search_data) - 1)
                search_data = search_data[index]
            return (
                self.search_status_by_criterion.get(criterion, "OK"),
                [search_data],
            )
        if command == "FETCH":
            uid = int(str(args[0]))
            query = str(args[-1])
            if "HEADER" in query:
                header_response = (
                    f'{uid} (UID {uid} INTERNALDATE "08-May-2026 20:30:00 +0800" BODY[HEADER] {{128}}'
                ).encode("ascii")
                return "OK", [
                    (
                        header_response,
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

    def append(self, mailbox: str, flags: str | None, date_time: object | None, message: bytes):
        self.commands.append(f"append:{mailbox}")
        self.append_calls.append((mailbox, flags, date_time, message))
        message_id_match = re.search(br"(?im)^Message-ID:\s*(<[^>\r\n]+>)", message)
        if message_id_match:
            message_id = message_id_match.group(1).decode("utf-8", errors="ignore")
            self._appended_message_ids[f'(HEADER Message-ID "{message_id}")'] = b"99"
        return self.append_status, [b"appended"]

    def logout(self):
        self.commands.append("logout")
        if self.logout_error is not None:
            raise self.logout_error
        return "OK", [b"logout"]


class _FakeSmtpClient:
    def __init__(self, login_error: Exception | None = None) -> None:
        self.commands: list[str] = []
        self.messages: list[object] = []
        self.login_error = login_error

    def login(self, username: str, password: str):
        self.commands.append("login")
        if self.login_error is not None:
            raise self.login_error
        return "OK", [b"logged in"]

    def send_message(self, message: object):
        self.commands.append("send_message")
        self.messages.append(message)
        return {}

    def quit(self):
        self.commands.append("quit")
        return "OK", [b"quit"]


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


class _DuplicateTextPartImapClient(_FakeImapClient):
    def uid(self, command: str, *args):
        self.commands.append(f"uid:{command}:{args}")
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
                    b"Message-ID: <reply-duplicate@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:00:00 +0800\r\n"
                    b"Content-Type: multipart/mixed; boundary=\"mix\"\r\n\r\n",
                ),
            ]
        if "BODYSTRUCTURE" in query:
            return "OK", [
                (
                    b'1 (BODYSTRUCTURE (("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL "7BIT" 5 1 NIL NIL NIL NIL)'
                    b'("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL "7BIT" 9 1 NIL NIL NIL NIL) '
                    b'"MIXED" ("BOUNDARY" "mix") NIL NIL))',
                ),
            ]
        if "BODY.PEEK[1.MIME]" in query:
            return "OK", [(b"1 (BODY[1.MIME] {48}", b"Content-Type: text/plain; charset=utf-8\r\n\r\n")]
        if "BODY.PEEK[1]" in query:
            return "OK", [(b"1 (BODY[1] {5}", b"first")]
        if "BODY.PEEK[2" in query:
            raise AssertionError("duplicate plain text part should not be fetched")
        return "OK", []


class _BatchHeaderImapClient(_FakeImapClient):
    def uid(self, command: str, *args):
        self.commands.append(f"uid:{command}:{args}")
        if command == "SEARCH":
            self.search_called = True
            self.search_criteria.append(str(args[-1]))
            return "OK", [self.search_data]
        if command != "FETCH":
            return "NO", []

        uid_set = str(args[0])
        query = str(args[-1])
        if uid_set == "1,2" and "HEADER" in query:
            return "OK", [
                (
                    b'1 (UID 1 INTERNALDATE "08-May-2026 20:30:00 +0800" BODY[HEADER] {128}',
                    b"From: teacher@example.com\r\n"
                    b"To: sender@example.com\r\n"
                    b"Subject: first\r\n"
                    b"Message-ID: <first@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:00:00 +0800\r\n\r\n",
                ),
                (
                    b'2 (UID 2 INTERNALDATE "08-May-2026 20:31:00 +0800" BODY[HEADER] {128}',
                    b"From: teacher@example.com\r\n"
                    b"To: sender@example.com\r\n"
                    b"Subject: second\r\n"
                    b"Message-ID: <second@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:01:00 +0800\r\n\r\n",
                ),
            ]
        return super().uid(command, *args)


class _MissingUidBatchHeaderImapClient(_FakeImapClient):
    def uid(self, command: str, *args):
        self.commands.append(f"uid:{command}:{args}")
        if command == "SEARCH":
            self.search_called = True
            self.search_criteria.append(str(args[-1]))
            return "OK", [self.search_data]
        if command != "FETCH":
            return "NO", []

        uid_set = str(args[0])
        query = str(args[-1])
        if uid_set == "1,2" and "HEADER" in query:
            return "OK", [
                (
                    b'1 (UID 1 INTERNALDATE "08-May-2026 20:30:00 +0800" BODY[HEADER] {128}',
                    b"From: teacher@example.com\r\n"
                    b"To: sender@example.com\r\n"
                    b"Subject: first\r\n"
                    b"Message-ID: <first@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:00:00 +0800\r\n\r\n",
                ),
                (
                    b'2 (INTERNALDATE "08-May-2026 20:31:00 +0800" BODY[HEADER] {128}',
                    b"From: teacher@example.com\r\n"
                    b"To: sender@example.com\r\n"
                    b"Subject: missing uid\r\n"
                    b"Message-ID: <missing-uid@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:01:00 +0800\r\n\r\n",
                ),
            ]
        return super().uid(command, *args)


class _PartialBatchRecentHeaderImapClient(_FakeImapClient):
    def uid(self, command: str, *args):
        if command == "FETCH" and str(args[0]) == "7,9":
            self.commands.append(f"uid:{command}:{args}")
            return "OK", [
                (
                    b'1 (UID 7 INTERNALDATE "08-May-2026 20:30:00 +0800" BODY[HEADER] {128}',
                    self.headers_by_uid[7],
                ),
            ]
        return super().uid(command, *args)


class _RangeHeaderImapClient(_FakeImapClient):
    def uid(self, command: str, *args):
        self.commands.append(f"uid:{command}:{args}")
        if command == "SEARCH":
            self.search_called = True
            self.search_criteria.append(str(args[-1]))
            return "OK", [self.search_data]
        if command != "FETCH":
            return "NO", []

        uid_set = str(args[0])
        query = str(args[-1])
        if uid_set == "41:50" and "HEADER" in query:
            return "OK", [
                (
                    b'42 (UID 42 INTERNALDATE "08-May-2026 20:30:00 +0800" BODY[HEADER] {128}',
                    b"From: other@example.edu\r\n"
                    b"To: sender@example.com\r\n"
                    b"Subject: other\r\n"
                    b"Message-ID: <other@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:00:00 +0800\r\n\r\n",
                ),
                (
                    b'49 (UID 49 INTERNALDATE "08-May-2026 20:31:00 +0800" BODY[HEADER] {128}',
                    b"From: teacher@example.com\r\n"
                    b"To: sender@example.com\r\n"
                    b"Subject: teacher\r\n"
                    b"Message-ID: <teacher@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:01:00 +0800\r\n\r\n",
                ),
            ]
        return "OK", []


class _FailingRangeHeaderImapClient(_FakeImapClient):
    def uid(self, command: str, *args):
        self.commands.append(f"uid:{command}:{args}")
        if command == "SEARCH":
            self.search_called = True
            self.search_criteria.append(str(args[-1]))
            return "OK", [self.search_data]
        if command == "FETCH":
            return "NO", [b"Fetch volume limit exceed"]
        return "NO", []


class _FailingHighWaterSearchImapClient(_FakeImapClient):
    def uid(self, command: str, *args):
        self.commands.append(f"uid:{command}:{args}")
        if command == "SEARCH":
            self.search_called = True
            self.search_criteria.append(str(args[-1]))
            return "NO", [b"Fetch volume limit exceed"]
        return super().uid(command, *args)


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


class MailRuntimeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._history_rate_limiter_patcher = patch(
            "app.modules.communications.transport.acquire_history_imap_command_slot_sync",
        )
        self._history_rate_limiter_patcher.start()
        self.addCleanup(self._history_rate_limiter_patcher.stop)

    def test_imap_login_failure_mentions_authorization_code_for_qq_or_163(self) -> None:
        identity = _build_identity()
        identity.imap_host = "imap.qq.com"

        message = format_imap_login_error(identity, "AUTHENTICATIONFAILED")

        self.assertIn("授权码", message)
        self.assertIn("IMAP/SMTP", message)

    def test_smtp_test_reports_and_logs_invalid_authorization_code_characters(self) -> None:
        client = _FakeSmtpClient(
            UnicodeEncodeError(
                "ascii",
                "错误",
                0,
                2,
                "ordinal not in range(128)",
            ),
        )

        with (
            patch("app.modules.communications.transport._open_smtp_client", return_value=client),
            patch("app.modules.communications.transport.logger.exception") as log_exception,
        ):
            ok, message = asyncio.run(test_smtp_connection(_build_identity()))

        self.assertFalse(ok)
        self.assertEqual(message, SMTP_CREDENTIAL_ENCODING_ERROR_MESSAGE)
        log_exception.assert_called_once()
        self.assertEqual(client.commands, ["login", "quit"])

    def test_imap_connection_sends_client_id_before_selecting_inbox(self) -> None:
        client = _FakeImapClient(select_status="OK")
        previous_id_command = imaplib.Commands.pop("ID", None)
        self.addCleanup(
            lambda: imaplib.Commands.__setitem__("ID", previous_id_command)
            if previous_id_command is not None
            else imaplib.Commands.pop("ID", None),
        )

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            _test_imap_connection_sync(_build_identity())

        self.assertEqual(client.commands[:3], ["login", "ID", "select:INBOX"])
        self.assertIn("ID", imaplib.Commands)

    def test_imap_connection_fails_when_inbox_select_is_rejected(self) -> None:
        client = _FakeImapClient(select_status="NO")

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            with self.assertRaisesRegex(MailRuntimeError, "IMAP 选择收件箱失败"):
                _test_imap_connection_sync(_build_identity())

    def test_fetch_messages_from_sender_uses_from_search_without_rfc822(self) -> None:
        client = _FakeImapClient(search_data=b"1")

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            messages = asyncio.run(
                fetch_inbox_messages_from_sender(_build_identity(), "teacher@example.com"),
            )

        self.assertEqual(client.search_criteria[-1], '(FROM "teacher@example.com")')
        self.assertNotIn("RFC822", " ".join(client.commands))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].from_email, "teacher@example.com")

    def test_send_email_disables_post_send_sent_folder_sync_even_when_imap_is_configured(self) -> None:
        with (
            patch("app.modules.communications.transport._open_smtp_client", return_value=_FakeSmtpClient()),
            patch("app.modules.communications.transport._open_imap_client") as open_imap,
        ):
            result = asyncio.run(
                send_email_to_recipient(
                    identity=_build_identity(),
                    recipient_name="Teacher",
                    recipient_email="teacher@example.com",
                    subject="hello",
                    body_text="hello",
                    body_html=None,
                    attachments=[],
                ),
            )

        open_imap.assert_not_called()
        self.assertEqual(result.provider_payload["sent_folder_sync"]["status"], "sent_folder_sync_disabled")

    def test_send_email_reports_invalid_authorization_code_characters(self) -> None:
        client = _FakeSmtpClient(
            UnicodeEncodeError(
                "ascii",
                "错误",
                0,
                2,
                "ordinal not in range(128)",
            ),
        )

        with (
            patch("app.modules.communications.transport._open_smtp_client", return_value=client),
            patch("app.modules.communications.transport.logger.exception") as log_exception,
        ):
            with self.assertRaisesRegex(MailRuntimeError, "授权码格式不正确"):
                asyncio.run(
                    send_email_to_recipient(
                        identity=_build_identity(),
                        recipient_name="Teacher",
                        recipient_email="teacher@example.com",
                        subject="hello",
                        body_text="hello",
                        body_html=None,
                        attachments=[],
                    ),
                )

        log_exception.assert_called_once()
        self.assertEqual(client.commands, ["login", "quit"])

    def test_send_email_skips_sent_folder_sync_when_imap_is_not_configured(self) -> None:
        identity = _build_identity()
        identity.imap_host = None
        identity.imap_port = None
        identity.imap_username = None
        identity.imap_password = None

        with (
            patch("app.modules.communications.transport._open_smtp_client", return_value=_FakeSmtpClient()),
            patch("app.modules.communications.transport._open_imap_client") as open_imap,
        ):
            result = asyncio.run(
                send_email_to_recipient(
                    identity=identity,
                    recipient_name="Teacher",
                    recipient_email="teacher@example.com",
                    subject="hello",
                    body_text="hello",
                    body_html=None,
                    attachments=[],
                ),
            )

        open_imap.assert_not_called()
        self.assertEqual(result.provider_payload["sent_folder_sync"]["status"], "sent_folder_sync_disabled")

    def test_discover_sent_folder_prefers_special_use_sent(self) -> None:
        client = _FakeImapClient(
            list_payload=[
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren \\Sent) "/" "Sent Items"',
            ],
        )

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            folder = asyncio.run(discover_sent_folder(_build_identity()))

        self.assertEqual(folder, "Sent Items")
        self.assertIn("list", client.commands)
        self.assertIn("select:Sent Items", client.commands)

    def test_discover_sent_folder_accepts_lowercase_special_use_sent(self) -> None:
        client = _FakeImapClient(
            list_payload=[
                b'(\\HasNoChildren \\sent) "/" "Sent Items"',
            ],
        )

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            folder = asyncio.run(discover_sent_folder(_build_identity()))

        self.assertEqual(folder, "Sent Items")
        self.assertIn("select:Sent Items", client.commands)

    def test_discover_sent_folder_falls_back_without_list_support(self) -> None:
        client = _FakeImapClient()
        client.list = None

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            folder = asyncio.run(discover_sent_folder(_build_identity()))

        self.assertEqual(folder, "Sent")
        self.assertIn("select:Sent", client.commands)

    def test_discover_sent_folder_falls_back_to_sent_items_candidate(self) -> None:
        client = _FakeImapClient(
            list_status="NO",
            select_status_by_mailbox={
                "Sent": "NO",
                "Sent Items": "OK",
            },
        )

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            folder = asyncio.run(discover_sent_folder(_build_identity()))

        self.assertEqual(folder, "Sent Items")
        self.assertIn("select:Sent", client.commands)
        self.assertIn("select:Sent Items", client.commands)

    def test_discover_sent_folder_returns_none_when_login_raises_imap_error(self) -> None:
        client = _FakeImapClient(login_error=imaplib.IMAP4.error("login failed"))

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            folder = asyncio.run(discover_sent_folder(_build_identity()))

        self.assertIsNone(folder)
        self.assertEqual(client.commands, ["login", "logout"])

    def test_discover_sent_folder_returns_none_when_login_and_logout_raise_imap_errors(self) -> None:
        client = _FakeImapClient(
            login_error=imaplib.IMAP4.error("login failed"),
            logout_error=imaplib.IMAP4.error("logout failed"),
        )

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            folder = asyncio.run(discover_sent_folder(_build_identity()))

        self.assertIsNone(folder)
        self.assertEqual(client.commands, ["login", "logout"])

    def test_discover_sent_folder_reraises_provider_throttle(self) -> None:
        client = _FakeImapClient(login_error=RuntimeError("Too many requests"))

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "Too many requests"):
                asyncio.run(discover_sent_folder(_build_identity()))

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

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
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

    def test_incremental_fetch_records_mailbox_uidvalidity(self) -> None:
        client = _FakeImapClient(
            search_data=b"5",
            select_data=[b"1"],
        )
        client.response = lambda code: ("UIDVALIDITY", [b"777"])

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            _, messages = asyncio.run(
                fetch_incremental_mailbox_messages(_build_identity(), "Sent", None),
            )

        self.assertEqual(messages[0].uidvalidity, 777)

    def test_incremental_fetch_resets_search_cursor_when_uidvalidity_changes(self) -> None:
        client = _FakeImapClient(
            search_data=b"1",
            select_data=[b"1"],
        )
        client.response = lambda code: ("UIDVALIDITY", [b"222"])

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            max_seen_uid, messages, uidvalidity = asyncio.run(
                fetch_incremental_mailbox_messages_with_uidvalidity(
                    _build_identity(),
                    "INBOX",
                    99,
                    expected_uidvalidity=111,
                ),
            )

        self.assertEqual(uidvalidity, 222)
        self.assertEqual(max_seen_uid, 1)
        self.assertEqual(messages[0].uid, 1)
        self.assertIn("UID 1:*", client.search_criteria)

    def test_incremental_fetch_resets_legacy_cursor_when_uidvalidity_becomes_known(self) -> None:
        client = _FakeImapClient(
            search_data=b"1",
            select_data=[b"1"],
        )
        client.response = lambda code: ("UIDVALIDITY", [b"222"])

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            max_seen_uid, messages, uidvalidity = asyncio.run(
                fetch_incremental_mailbox_messages_with_uidvalidity(
                    _build_identity(),
                    "INBOX",
                    99,
                    expected_uidvalidity=None,
                ),
            )

        self.assertEqual(uidvalidity, 222)
        self.assertEqual(max_seen_uid, 1)
        self.assertEqual(messages[0].uid, 1)
        self.assertIn("UID 1:*", client.search_criteria)

    def test_incremental_fetch_legacy_api_keeps_cursor_when_uidvalidity_becomes_known(self) -> None:
        client = _FakeImapClient(
            search_data=b"",
            select_data=[b"1"],
        )
        client.response = lambda code: ("UIDVALIDITY", [b"222"])

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            max_seen_uid, messages = asyncio.run(
                fetch_incremental_mailbox_messages(
                    _build_identity(),
                    "INBOX",
                    99,
                ),
            )

        self.assertEqual(max_seen_uid, 99)
        self.assertEqual(messages, [])
        self.assertIn("UID 100:*", client.search_criteria)
        self.assertNotIn("UID 1:*", client.search_criteria)

    def test_sent_history_searches_to_and_cc_and_deduplicates_uids(self) -> None:
        client = _FakeImapClient(
            search_data_by_criterion={
                '(OR (OR (TO "teacher@example.com") (CC "teacher@example.com")) (BCC "teacher@example.com"))': b"7 8 9 10",
            },
            headers_by_uid={
                7: (
                    b"From: sender@example.com\r\n"
                    b"To: teacher@example.com\r\n"
                    b"Subject: first\r\n"
                    b"Message-ID: <sent-7@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:00:00 +0800\r\n\r\n"
                ),
                10: (
                    b"From: sender@example.com\r\n"
                    b"To: teacher@example.com\r\n"
                    b"Subject: later\r\n"
                    b"Message-ID: <sent-10@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:03:00 +0800\r\n\r\n"
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

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            messages = asyncio.run(
                fetch_professor_history_mailbox_messages(
                    _build_identity(),
                    "Sent",
                    "teacher@example.com",
                    folder_role="sent",
                ),
            )

        self.assertEqual(
            client.search_criteria,
            ['(OR (OR (TO "teacher@example.com") (CC "teacher@example.com")) (BCC "teacher@example.com"))'],
        )
        self.assertIn("select:Sent", client.commands)
        self.assertEqual([message.uid for message in messages], [7, 8, 9, 10])

    def test_sent_history_search_falls_back_when_combined_or_search_fails(self) -> None:
        combined = '(OR (OR (TO "teacher@example.com") (CC "teacher@example.com")) (BCC "teacher@example.com"))'
        client = _FakeImapClient(
            search_status_by_criterion={combined: "NO"},
            search_data_by_criterion={
                '(TO "teacher@example.com")': b"7",
                '(CC "teacher@example.com")': b"8",
                '(BCC "teacher@example.com")': b"9",
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
                    b"Cc: teacher@example.com\r\n"
                    b"Subject: copy\r\n"
                    b"Message-ID: <sent-8@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:01:00 +0800\r\n\r\n"
                ),
                9: (
                    b"From: sender@example.com\r\n"
                    b"Bcc: teacher@example.com\r\n"
                    b"Subject: hidden\r\n"
                    b"Message-ID: <sent-9@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:02:00 +0800\r\n\r\n"
                ),
            },
        )

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            messages = asyncio.run(
                fetch_professor_history_mailbox_messages(
                    _build_identity(),
                    "Sent",
                    "teacher@example.com",
                    folder_role="sent",
                ),
            )

        self.assertEqual(
            client.search_criteria,
            [
                combined,
                '(TO "teacher@example.com")',
                '(CC "teacher@example.com")',
                '(BCC "teacher@example.com")',
            ],
        )
        self.assertEqual([message.uid for message in messages], [7, 8, 9])

    def test_sent_history_does_not_fallback_when_combined_search_succeeds_empty(self) -> None:
        combined = '(OR (OR (TO "teacher@example.com") (CC "teacher@example.com")) (BCC "teacher@example.com"))'
        client = _FakeImapClient(
            search_data_by_criterion={
                combined: b"",
                '(TO "teacher@example.com")': b"7",
            },
        )

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            messages = asyncio.run(
                fetch_professor_history_mailbox_messages(
                    _build_identity(),
                    "Sent",
                    "teacher@example.com",
                    folder_role="sent",
                ),
            )

        self.assertEqual(client.search_criteria, [combined])
        self.assertEqual(messages, [])

    def test_sent_history_searches_bcc_recipients(self) -> None:
        combined = '(OR (OR (TO "teacher@example.com") (CC "teacher@example.com")) (BCC "teacher@example.com"))'
        client = _FakeImapClient(
            search_status_by_criterion={combined: "NO"},
            search_data_by_criterion={
                '(TO "teacher@example.com")': b"",
                '(CC "teacher@example.com")': b"",
                '(BCC "teacher@example.com")': b"11",
            },
            headers_by_uid={
                11: (
                    b"From: sender@example.com\r\n"
                    b"Bcc: teacher@example.com\r\n"
                    b"Subject: hidden recipient\r\n"
                    b"Message-ID: <sent-bcc@example.com>\r\n"
                    b"Date: Fri, 08 May 2026 20:04:00 +0800\r\n\r\n"
                ),
            },
        )

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            messages = asyncio.run(
                fetch_professor_history_mailbox_messages(
                    _build_identity(),
                    "Sent",
                    "teacher@example.com",
                    folder_role="sent",
                ),
            )

        self.assertEqual(
            client.search_criteria,
            [
                combined,
                '(TO "teacher@example.com")',
                '(CC "teacher@example.com")',
                '(BCC "teacher@example.com")',
            ],
        )
        self.assertEqual([message.uid for message in messages], [11])
        self.assertEqual(messages[0].bcc_emails, ["teacher@example.com"])

    def test_mailbox_history_rejects_unknown_folder_role(self) -> None:
        client = _FakeImapClient()

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            with self.assertRaisesRegex(MailRuntimeError, "folder_role|unsupported|Unsupported"):
                asyncio.run(
                    fetch_professor_history_mailbox_messages(
                        _build_identity(),
                        "Archive",
                        "teacher@example.com",
                        folder_role="archive",
                    ),
                )

        self.assertEqual(client.search_criteria, [])

    def test_incremental_fetch_reads_body_and_internaldate(self) -> None:
        client = _FakeImapClient(search_data=b"1")

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
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

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            messages = asyncio.run(
                fetch_professor_history_inbox_messages(_build_identity(), "teacher@example.com"),
            )

        self.assertEqual(client.search_criteria[-1], '(FROM "teacher@example.com")')
        self.assertIn("select:INBOX", client.commands)
        self.assertEqual([message.uid for message in messages], [1])

    def test_professor_history_header_fetch_batches_uids_without_fetching_body(self) -> None:
        client = _BatchHeaderImapClient(search_data=b"1 2")

        with (
            patch("app.modules.communications.transport._open_imap_client", return_value=client),
            patch("app.modules.communications.transport.get_settings") as settings_mock,
        ):
            settings_mock.return_value.imap_fetch_batch_size = 20
            messages = asyncio.run(
                fetch_professor_history_mailbox_message_headers(
                    _build_identity(),
                    "INBOX",
                    "teacher@example.com",
                    folder_role="inbox",
                ),
            )

        serialized_commands = " ".join(client.commands)
        self.assertIn("uid:FETCH:('1,2'", serialized_commands)
        self.assertNotIn("BODYSTRUCTURE", serialized_commands)
        self.assertNotIn("BODY.PEEK[TEXT]", serialized_commands)
        self.assertEqual([message.message_id for message in messages], ["<first@example.com>", "<second@example.com>"])

    def test_professor_history_header_fetch_falls_back_for_batch_items_without_uid(self) -> None:
        client = _MissingUidBatchHeaderImapClient(search_data=b"1 2")

        with (
            patch("app.modules.communications.transport._open_imap_client", return_value=client),
            patch("app.modules.communications.transport.get_settings") as settings_mock,
        ):
            settings_mock.return_value.imap_fetch_batch_size = 20
            messages = asyncio.run(
                fetch_professor_history_mailbox_message_headers(
                    _build_identity(),
                    "INBOX",
                    "teacher@example.com",
                    folder_role="inbox",
                ),
            )

        self.assertEqual([message.uid for message in messages], [1, 2])
        self.assertEqual([message.message_id for message in messages], ["<first@example.com>", "<reply-from-sender@example.com>"])

    def test_professor_history_header_fetch_falls_back_when_batch_payload_omits_uid(self) -> None:
        client = _MissingUidBatchHeaderImapClient(search_data=b"1 2")

        with (
            patch("app.modules.communications.transport._open_imap_client", return_value=client),
            patch("app.modules.communications.transport.get_settings") as settings_mock,
        ):
            settings_mock.return_value.imap_fetch_batch_size = 20
            result = asyncio.run(
                fetch_professor_history_mailbox_message_headers_with_command_count(
                    _build_identity(),
                    "INBOX",
                    "teacher@example.com",
                    folder_role="inbox",
                ),
            )

        self.assertEqual(
            [message.message_id for message in result.messages],
            ["<first@example.com>", "<reply-from-sender@example.com>"],
        )
        serialized_commands = " ".join(client.commands)
        self.assertIn("uid:FETCH:('1,2'", serialized_commands)
        self.assertIn("uid:FETCH:('2'", serialized_commands)
        self.assertEqual(result.command_count, 3)

    def test_professor_history_header_fetch_uses_history_rate_limiter_per_imap_command(self) -> None:
        client = _MissingUidBatchHeaderImapClient(search_data=b"1 2")
        identity = _build_identity()
        seen: list[tuple[str, str]] = []

        with (
            patch("app.modules.communications.transport._open_imap_client", return_value=client),
            patch("app.modules.communications.transport.get_settings") as settings_mock,
            patch("app.modules.communications.transport.acquire_history_imap_command_slot_sync") as acquire_mock,
        ):
            settings_mock.return_value.imap_fetch_batch_size = 20
            acquire_mock.side_effect = lambda current_identity, command: seen.append(
                (current_identity.email_address, command),
            )
            result = asyncio.run(
                fetch_professor_history_mailbox_message_headers_with_command_count(
                    identity,
                    "INBOX",
                    "teacher@example.com",
                    folder_role="inbox",
                ),
            )

        self.assertEqual(result.command_count, 3)
        self.assertEqual(
            seen,
            [
                ("sender@example.com", "SEARCH"),
                ("sender@example.com", "FETCH"),
                ("sender@example.com", "FETCH"),
            ],
        )

    def test_recent_mailbox_headers_searches_real_uids_since_date(self) -> None:
        client = _FakeImapClient(
            search_data_by_criterion={"SINCE 01-Jan-2025": b"7 9"},
            headers_by_uid={
                7: (
                    b"From: sender@example.com\r\n"
                    b"To: teacher@example.com\r\n"
                    b"Subject: first recent\r\n"
                    b"Message-ID: <recent-7@example.com>\r\n"
                    b"Date: Fri, 03 Jan 2025 20:00:00 +0800\r\n\r\n"
                ),
                9: (
                    b"From: sender@example.com\r\n"
                    b"To: teacher@example.com\r\n"
                    b"Subject: second recent\r\n"
                    b"Message-ID: <recent-9@example.com>\r\n"
                    b"Date: Sat, 04 Jan 2025 20:00:00 +0800\r\n\r\n"
                ),
            },
        )
        client.response = lambda code: ("UIDVALIDITY", [b"777"])

        with (
            patch("app.modules.communications.transport._open_imap_client", return_value=client),
            patch("app.modules.communications.transport.get_settings") as settings_mock,
        ):
            settings_mock.return_value.imap_fetch_batch_size = 1
            result = asyncio.run(
                fetch_recent_mailbox_message_headers_since(
                    _build_identity(),
                    "Sent",
                    date(2025, 1, 1),
                    min_uid=None,
                    max_fetch_batches=None,
                ),
            )

        self.assertEqual([message.uid for message in result.messages], [7, 9])
        self.assertEqual([message.uidvalidity for message in result.messages], [777, 777])
        self.assertIn("select:Sent", client.commands)
        self.assertIn("SINCE 01-Jan-2025", client.search_criteria)
        self.assertNotIn("UID 1:*", client.search_criteria)
        self.assertFalse(
            any(re.fullmatch(r"\d+:\*|\d+:\d+", criterion) for criterion in client.search_criteria),
        )

    def test_recent_mailbox_headers_reuse_probe_uids_when_uidvalidity_matches(self) -> None:
        client = _FakeImapClient(
            headers_by_uid={
                7: (
                    b"From: sender@example.com\r\n"
                    b"To: teacher@example.com\r\n"
                    b"Subject: first cached uid\r\n"
                    b"Message-ID: <cached-7@example.com>\r\n\r\n"
                ),
                9: (
                    b"From: sender@example.com\r\n"
                    b"To: teacher@example.com\r\n"
                    b"Subject: second cached uid\r\n"
                    b"Message-ID: <cached-9@example.com>\r\n\r\n"
                ),
            },
        )
        client.response = lambda code: ("UIDVALIDITY", [b"777"])

        with (
            patch("app.modules.communications.transport._open_imap_client", return_value=client),
            patch("app.modules.communications.transport.get_settings") as settings_mock,
        ):
            settings_mock.return_value.imap_fetch_batch_size = 1
            result = asyncio.run(
                fetch_recent_mailbox_message_headers_since(
                    _build_identity(),
                    "Sent",
                    date(2025, 1, 1),
                    min_uid=None,
                    max_fetch_batches=None,
                    known_uids=(7, 9),
                    known_uidvalidity=777,
                ),
            )

        self.assertEqual([message.uid for message in result.messages], [7, 9])
        self.assertEqual(result.command_count, 2)
        self.assertEqual(client.search_criteria, [])

    def test_recent_mailbox_headers_reset_min_uid_when_uidvalidity_changes(self) -> None:
        client = _FakeImapClient(
            search_data_by_criterion={"SINCE 01-Jan-2025": b"7"},
            headers_by_uid={
                7: (
                    b"From: sender@example.com\r\n"
                    b"To: teacher@example.com\r\n"
                    b"Subject: low uid after reset\r\n"
                    b"Message-ID: <recent-reset-7@example.com>\r\n"
                    b"Date: Fri, 03 Jan 2025 20:00:00 +0800\r\n\r\n"
                ),
            },
        )
        client.response = lambda code: ("UIDVALIDITY", [b"222"])

        with (
            patch("app.modules.communications.transport._open_imap_client", return_value=client),
            patch("app.modules.communications.transport.get_settings") as settings_mock,
        ):
            settings_mock.return_value.imap_fetch_batch_size = 20
            result = asyncio.run(
                fetch_recent_mailbox_message_headers_since(
                    _build_identity(),
                    "Sent",
                    date(2025, 1, 1),
                    min_uid=900,
                    max_fetch_batches=None,
                    expected_uidvalidity=111,
                    known_uids=(900,),
                    known_uidvalidity=111,
                ),
            )

        self.assertEqual([message.uid for message in result.messages], [7])
        self.assertEqual(result.uidvalidity, 222)
        self.assertTrue(result.uidvalidity_changed)
        self.assertEqual(result.messages[0].uidvalidity, 222)
        self.assertIn("SINCE 01-Jan-2025", client.search_criteria)

    def test_recent_mailbox_headers_fallback_when_batch_omits_uid(self) -> None:
        client = _PartialBatchRecentHeaderImapClient(
            search_data_by_criterion={"SINCE 01-Jan-2025": b"7 9"},
            headers_by_uid={
                7: (
                    b"From: sender@example.com\r\n"
                    b"To: teacher@example.com\r\n"
                    b"Subject: first recent\r\n"
                    b"Message-ID: <recent-7@example.com>\r\n"
                    b"Date: Fri, 03 Jan 2025 20:00:00 +0800\r\n\r\n"
                ),
                9: (
                    b"From: sender@example.com\r\n"
                    b"To: teacher@example.com\r\n"
                    b"Subject: second recent\r\n"
                    b"Message-ID: <recent-9@example.com>\r\n"
                    b"Date: Sat, 04 Jan 2025 20:00:00 +0800\r\n\r\n"
                ),
            },
        )

        with (
            patch("app.modules.communications.transport._open_imap_client", return_value=client),
            patch("app.modules.communications.transport.get_settings") as settings_mock,
        ):
            settings_mock.return_value.imap_fetch_batch_size = 20
            result = asyncio.run(
                fetch_recent_mailbox_message_headers_since(
                    _build_identity(),
                    "Sent",
                    date(2025, 1, 1),
                    min_uid=None,
                    max_fetch_batches=None,
                ),
            )

        self.assertEqual([message.uid for message in result.messages], [7, 9])
        self.assertFalse(result.exhausted)
        self.assertEqual(result.command_count, 3)
        serialized_commands = " ".join(client.commands)
        self.assertIn("uid:FETCH:('7,9'", serialized_commands)
        self.assertIn("uid:FETCH:('9'", serialized_commands)

    def test_recent_mailbox_headers_marks_exhausted_when_fallback_would_exceed_budget(self) -> None:
        client = _PartialBatchRecentHeaderImapClient(
            search_data_by_criterion={"SINCE 01-Jan-2025": b"7 9"},
            headers_by_uid={
                7: (
                    b"From: sender@example.com\r\n"
                    b"To: teacher@example.com\r\n"
                    b"Subject: first recent\r\n"
                    b"Message-ID: <recent-7@example.com>\r\n"
                    b"Date: Fri, 03 Jan 2025 20:00:00 +0800\r\n\r\n"
                ),
                9: (
                    b"From: sender@example.com\r\n"
                    b"To: teacher@example.com\r\n"
                    b"Subject: second recent\r\n"
                    b"Message-ID: <recent-9@example.com>\r\n"
                    b"Date: Sat, 04 Jan 2025 20:00:00 +0800\r\n\r\n"
                ),
            },
        )

        with (
            patch("app.modules.communications.transport._open_imap_client", return_value=client),
            patch("app.modules.communications.transport.get_settings") as settings_mock,
        ):
            settings_mock.return_value.imap_fetch_batch_size = 20
            result = asyncio.run(
                fetch_recent_mailbox_message_headers_since(
                    _build_identity(),
                    "Sent",
                    date(2025, 1, 1),
                    min_uid=None,
                    max_fetch_batches=1,
                ),
            )

        self.assertEqual([message.uid for message in result.messages], [7])
        self.assertTrue(result.exhausted)
        self.assertEqual(result.command_count, 2)
        serialized_commands = " ".join(client.commands)
        self.assertIn("uid:FETCH:('7,9'", serialized_commands)
        self.assertNotIn("uid:FETCH:('9'", serialized_commands)

    def test_professor_history_headers_accept_since_date_for_inbox_search(self) -> None:
        client = _FakeImapClient(
            search_data_by_criterion={'(FROM "teacher@example.edu" SINCE 01-Jan-2025)': b"4"},
            headers_by_uid={
                4: (
                    b"From: teacher@example.edu\r\n"
                    b"To: sender@example.com\r\n"
                    b"Subject: recent reply\r\n"
                    b"Message-ID: <recent-inbox-4@example.edu>\r\n"
                    b"Date: Fri, 03 Jan 2025 20:00:00 +0800\r\n\r\n"
                ),
            },
        )
        client.response = lambda code: ("UIDVALIDITY", [b"888"])

        with (
            patch("app.modules.communications.transport._open_imap_client", return_value=client),
            patch("app.modules.communications.transport.get_settings") as settings_mock,
        ):
            settings_mock.return_value.imap_fetch_batch_size = 1
            result = asyncio.run(
                fetch_professor_history_mailbox_message_headers_with_command_count(
                    _build_identity(),
                    "INBOX",
                    "teacher@example.edu",
                    folder_role="inbox",
                    min_uid=None,
                    max_fetch_batches=None,
                    since_date=date(2025, 1, 1),
                ),
            )

        self.assertEqual([message.uid for message in result.messages], [4])
        self.assertEqual(result.messages[0].uidvalidity, 888)
        self.assertIn('(FROM "teacher@example.edu" SINCE 01-Jan-2025)', client.search_criteria)

    def test_professor_history_headers_accept_since_date_for_sent_search(self) -> None:
        combined = (
            '(SINCE 01-Jan-2025 OR (OR (TO "teacher@example.edu") '
            '(CC "teacher@example.edu")) (BCC "teacher@example.edu"))'
        )
        client = _FakeImapClient(search_data_by_criterion={combined: b""})

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            result = asyncio.run(
                fetch_professor_history_mailbox_message_headers_with_command_count(
                    _build_identity(),
                    "Sent",
                    "teacher@example.edu",
                    folder_role="sent",
                    since_date=date(2025, 1, 1),
                ),
            )

        self.assertEqual(result.messages, [])
        self.assertEqual(client.search_criteria, [combined])

    def test_professor_history_sent_fallback_keeps_since_date(self) -> None:
        combined = (
            '(SINCE 01-Jan-2025 OR (OR (TO "teacher@example.edu") '
            '(CC "teacher@example.edu")) (BCC "teacher@example.edu"))'
        )
        fallback_criteria = [
            '(TO "teacher@example.edu" SINCE 01-Jan-2025)',
            '(CC "teacher@example.edu" SINCE 01-Jan-2025)',
            '(BCC "teacher@example.edu" SINCE 01-Jan-2025)',
        ]
        client = _FakeImapClient(
            search_status_by_criterion={combined: "NO"},
            search_data_by_criterion={criterion: b"" for criterion in fallback_criteria},
        )

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            result = asyncio.run(
                fetch_professor_history_mailbox_message_headers_with_command_count(
                    _build_identity(),
                    "Sent",
                    "teacher@example.edu",
                    folder_role="sent",
                    since_date=date(2025, 1, 1),
                ),
            )

        self.assertEqual(result.messages, [])
        self.assertEqual(result.command_count, 4)
        self.assertEqual(client.search_criteria, [combined, *fallback_criteria])

    def test_recent_uid_probe_does_not_fetch_headers(self) -> None:
        client = _FakeImapClient(
            search_data_by_criterion={"SINCE 01-Jan-2025": b"7 9 11"},
        )
        client.response = lambda code: ("UIDVALIDITY", [b"777"])

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            result = asyncio.run(
                search_mailbox_uids_since_date(
                    _build_identity(),
                    "Sent",
                    date(2025, 1, 1),
                ),
            )

        self.assertEqual(result.uid_count, 3)
        self.assertEqual(result.command_count, 1)
        self.assertEqual(result.uidvalidity, 777)
        self.assertEqual(result.uids, (7, 9, 11))
        self.assertFalse(any("FETCH" in command for command in client.commands))

    def test_mailbox_history_header_fetch_scans_uid_window_without_search_or_body(self) -> None:
        client = _RangeHeaderImapClient()
        identity = _build_identity()
        seen: list[str] = []

        with (
            patch("app.modules.communications.transport._open_imap_client", return_value=client),
            patch("app.modules.communications.transport.acquire_history_imap_command_slot_sync") as acquire_mock,
        ):
            acquire_mock.side_effect = lambda _identity, command: seen.append(command)
            result = asyncio.run(
                fetch_history_mailbox_message_headers_before_uid(
                    identity,
                    "INBOX",
                    before_uid=51,
                    limit=10,
                ),
            )

        serialized_commands = " ".join(client.commands)
        self.assertIn("uid:FETCH:('41:50'", serialized_commands)
        self.assertEqual(client.search_criteria, [])
        self.assertNotIn("BODYSTRUCTURE", serialized_commands)
        self.assertNotIn("BODY.PEEK[TEXT]", serialized_commands)
        self.assertEqual([message.uid for message in result.messages], [42, 49])
        self.assertEqual(result.next_before_uid, 41)
        self.assertEqual(result.scanned_count, 10)
        self.assertEqual(result.command_count, 1)
        self.assertEqual(seen, ["FETCH"])

    def test_mailbox_history_header_fetch_raises_when_uid_range_fetch_fails(self) -> None:
        client = _FailingRangeHeaderImapClient()

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "Fetch volume limit exceed"):
                asyncio.run(
                    fetch_history_mailbox_message_headers_before_uid(
                        _build_identity(),
                        "INBOX",
                        before_uid=51,
                        limit=10,
                    ),
                )

    def test_mailbox_history_header_fetch_raises_when_high_water_search_fails(self) -> None:
        client = _FailingHighWaterSearchImapClient()

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "Fetch volume limit exceed"):
                asyncio.run(
                    fetch_history_mailbox_message_headers_before_uid(
                        _build_identity(),
                        "INBOX",
                        before_uid=None,
                        limit=0,
                        max_fetch_batches=0,
                    ),
                )

    def test_mailbox_history_header_fetch_resets_window_when_uidvalidity_changes(self) -> None:
        client = _RangeHeaderImapClient()
        client.response = lambda code: ("UIDNEXT", [b"51"]) if code == "UIDNEXT" else ("UIDVALIDITY", [b"222"])
        identity = _build_identity()

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
            result = asyncio.run(
                fetch_history_mailbox_message_headers_before_uid(
                    identity,
                    "INBOX",
                    before_uid=801,
                    limit=10,
                    expected_uidvalidity=111,
                ),
            )

        serialized_commands = " ".join(client.commands)
        self.assertIn("uid:FETCH:('41:50'", serialized_commands)
        self.assertNotIn("uid:FETCH:('791:800'", serialized_commands)
        self.assertEqual(result.uidvalidity, 222)
        self.assertEqual(result.high_water_uid, 50)
        self.assertEqual(result.next_before_uid, 41)
        self.assertEqual(result.scanned_count, 10)

    def test_history_body_fetch_by_uid_uses_history_rate_limiter_per_imap_command(self) -> None:
        client = _MultipartFallbackImapClient(search_data=b"1")
        identity = _build_identity()
        seen: list[str] = []

        with (
            patch("app.modules.communications.transport._open_imap_client", return_value=client),
            patch("app.modules.communications.transport.get_settings") as settings_mock,
            patch("app.modules.communications.transport.acquire_history_imap_command_slot_sync") as acquire_mock,
        ):
            settings_mock.return_value.imap_fetch_batch_size = 20
            acquire_mock.side_effect = lambda _identity, command: seen.append(command)
            messages = asyncio.run(
                fetch_professor_history_mailbox_messages_by_uid(
                    identity,
                    "INBOX",
                    [1],
                ),
            )

        self.assertEqual(len(messages), 1)
        self.assertEqual(
            seen,
            ["FETCH", "FETCH", "FETCH"],
        )

    def test_incremental_fetch_does_not_use_history_rate_limiter(self) -> None:
        client = _MultipartFallbackImapClient(search_data=b"1")

        with (
            patch("app.modules.communications.transport._open_imap_client", return_value=client),
            patch("app.modules.communications.transport.acquire_history_imap_command_slot_sync") as acquire_mock,
        ):
            _, messages = asyncio.run(fetch_incremental_inbox_messages(_build_identity(), None))

        self.assertEqual(len(messages), 1)
        acquire_mock.assert_not_called()

    def test_incremental_fetch_decodes_base64_text_part_without_fetching_attachment(self) -> None:
        client = _MultipartBase64ImapClient(search_data=b"1")

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
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

        with patch("app.modules.communications.transport._open_imap_client", return_value=client):
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

    def test_history_body_fetch_by_uid_falls_back_when_bodystructure_finds_no_text(self) -> None:
        client = _MultipartFallbackImapClient(search_data=b"1")

        with (
            patch("app.modules.communications.transport._open_imap_client", return_value=client),
            patch("app.modules.communications.transport.get_settings") as settings_mock,
        ):
            settings_mock.return_value.imap_fetch_batch_size = 20
            messages = asyncio.run(
                fetch_professor_history_mailbox_messages_by_uid(
                    _build_identity(),
                    "INBOX",
                    [1],
                ),
            )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].body_text, "\u4f60\u597d")
        serialized_commands = " ".join(client.commands)
        self.assertIn("uid:FETCH:('1'", serialized_commands)
        self.assertIn("BODYSTRUCTURE", serialized_commands)
        self.assertIn("BODY.PEEK[TEXT]", serialized_commands)
        self.assertNotIn("ignored attachment", messages[0].body_text)

    def test_history_body_fetch_by_uid_decodes_text_part_without_fetching_attachment(self) -> None:
        client = _MultipartBase64ImapClient(search_data=b"1")
        seen: list[str] = []

        with (
            patch("app.modules.communications.transport._open_imap_client", return_value=client),
            patch("app.modules.communications.transport.get_settings") as settings_mock,
            patch("app.modules.communications.transport.acquire_history_imap_command_slot_sync") as acquire_mock,
        ):
            settings_mock.return_value.imap_fetch_batch_size = 20
            acquire_mock.side_effect = lambda _identity, command: seen.append(command)
            messages = asyncio.run(
                fetch_professor_history_mailbox_messages_by_uid(
                    _build_identity(),
                    "INBOX",
                    [1],
                ),
            )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].body_text, "\u4f60\u597d")
        serialized_commands = " ".join(client.commands)
        self.assertIn("BODYSTRUCTURE", serialized_commands)
        self.assertIn("BODY.PEEK[1.MIME]", serialized_commands)
        self.assertIn("BODY.PEEK[1]", serialized_commands)
        self.assertNotIn("BODY.PEEK[2", serialized_commands)
        self.assertEqual(seen, ["FETCH", "FETCH", "FETCH", "FETCH"])

    def test_history_body_fetch_by_uid_skips_duplicate_text_parts(self) -> None:
        client = _DuplicateTextPartImapClient(search_data=b"1")

        with (
            patch("app.modules.communications.transport._open_imap_client", return_value=client),
            patch("app.modules.communications.transport.get_settings") as settings_mock,
        ):
            settings_mock.return_value.imap_fetch_batch_size = 20
            messages = asyncio.run(
                fetch_professor_history_mailbox_messages_by_uid(
                    _build_identity(),
                    "INBOX",
                    [1],
                ),
            )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].body_text, "first")
        serialized_commands = " ".join(client.commands)
        self.assertNotIn("BODY.PEEK[2", serialized_commands)

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
