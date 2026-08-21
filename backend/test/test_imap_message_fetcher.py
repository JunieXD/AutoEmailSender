from __future__ import annotations

import unittest
from datetime import date
from email.message import EmailMessage
from typing import cast

from app.modules.communications.imap.fetcher import (
    fetch_message_headers_by_uid,
    fetch_text_part_sections_by_uid,
    parse_text_parts_from_message,
    search_uids_combined_sent_recipient,
    search_uids_from_sender,
    search_uids_from_sender_since,
    search_uids_since,
    search_uids_since_date,
)


class ImapMessageFetcherTestCase(unittest.TestCase):
    def test_parse_text_parts_ignores_attachment_payload(self) -> None:
        message = EmailMessage()
        message["From"] = "prof@example.edu"
        message["To"] = "student@example.com"
        message["Subject"] = "Re: Hello"
        message["Message-ID"] = "<reply@example.edu>"
        message.set_content("plain body")
        message.add_alternative("<p>html body</p>", subtype="html")
        message.add_attachment(
            b"large attachment bytes",
            maintype="application",
            subtype="pdf",
            filename="cv.pdf",
        )

        parsed = parse_text_parts_from_message(message)

        self.assertEqual(parsed.body_text, "plain body\n")
        self.assertEqual(parsed.body_html, "<p>html body</p>\n")
        self.assertTrue(parsed.has_attachments)
        self.assertEqual(parsed.attachment_names, ["cv.pdf"])

    def test_fetch_headers_command_does_not_request_rfc822(self) -> None:
        client = FakeImapClient(search_payload=b"1")

        fetch_message_headers_by_uid(client, 1)

        serialized = " ".join(str(item) for item in client.commands)
        self.assertIn("HEADER.FIELDS", serialized)
        self.assertIn("X-AUTOEMAILSENDER-DELIVERY-ID", serialized)
        self.assertNotIn("RFC822", serialized)

    def test_fetch_text_part_sections_ignores_attachment_parts(self) -> None:
        client = FakeImapClient(search_payload=b"1")

        sections = fetch_text_part_sections_by_uid(client, 1)

        self.assertEqual([section.section for section in sections], ["1", "2"])
        self.assertEqual(
            [section.content_type for section in sections], ["text/plain", "text/html"]
        )

    def test_search_incremental_uses_next_uid(self) -> None:
        client = FakeImapClient(search_payload=b"11 12")

        result = search_uids_since(client, 10)

        self.assertEqual(result, [11, 12])
        serialized = " ".join(str(item) for item in client.commands)
        self.assertIn("11:*", serialized)

    def test_search_incremental_filters_servers_that_echo_last_uid(self) -> None:
        client = FakeImapClient(search_payload=b"10 11 12")

        result = search_uids_since(client, 10)

        self.assertEqual(result, [11, 12])

    def test_search_from_sender_uses_professor_email(self) -> None:
        client = FakeImapClient(search_payload=b"5")

        result = search_uids_from_sender(client, "prof@example.edu")

        self.assertEqual(result, [5])
        serialized = " ".join(str(item) for item in client.commands)
        self.assertIn("FROM", serialized)
        self.assertIn("prof@example.edu", serialized)

    def test_search_uids_since_date_uses_imap_since_criterion(self) -> None:
        client = FakeImapClient(search_payload=b"5 7")

        result = search_uids_since_date(client, date(2025, 1, 1))

        self.assertEqual(result, [5, 7])
        serialized = " ".join(str(item) for item in client.commands)
        self.assertIn("SINCE 01-Jan-2025", serialized)
        self.assertNotIn("1:*", serialized)

    def test_search_uids_since_date_uses_fixed_english_month_names(self) -> None:
        client = FakeImapClient(search_payload=b"5 7")

        result = search_uids_since_date(client, cast(date, LocaleSensitiveDate()))

        self.assertEqual(result, [5, 7])
        serialized = " ".join(str(item) for item in client.commands)
        self.assertIn("SINCE 01-Jan-2025", serialized)
        self.assertNotIn("1月", serialized)

    def test_search_from_sender_since_combines_sender_and_date(self) -> None:
        client = FakeImapClient(search_payload=b"8 9")

        result = search_uids_from_sender_since(
            client, "Prof <prof@example.edu>", date(2025, 1, 1)
        )

        self.assertEqual(result, [8, 9])
        serialized = " ".join(str(item) for item in client.commands)
        self.assertIn('FROM "prof@example.edu"', serialized)
        self.assertIn("SINCE 01-Jan-2025", serialized)

    def test_search_sent_recipient_since_combines_addresses_and_date(self) -> None:
        client = FakeImapClient(search_payload=b"10 11")

        result = search_uids_combined_sent_recipient(
            client,
            "prof@example.edu",
            date(2025, 1, 1),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.uids, [10, 11])
        serialized = " ".join(str(item) for item in client.commands)
        self.assertIn("SINCE 01-Jan-2025", serialized)
        self.assertIn('TO "prof@example.edu"', serialized)
        self.assertIn('CC "prof@example.edu"', serialized)
        self.assertIn('BCC "prof@example.edu"', serialized)


class LocaleSensitiveDate:
    year = 2025
    month = 1
    day = 1

    def strftime(self, fmt: str) -> str:
        return "01-1月-2025"


class FakeImapClient:
    def __init__(self, *, search_payload: bytes) -> None:
        self.commands: list[tuple[str, tuple[object, ...]]] = []
        self.search_payload = search_payload

    def uid(self, command: str, *args: object):
        self.commands.append((command, args))
        if command == "SEARCH":
            return "OK", [self.search_payload]
        if command == "FETCH":
            query = str(args[-1])
            if "BODYSTRUCTURE" in query:
                return "OK", [
                    (
                        b'1 (BODYSTRUCTURE (("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL "BASE64" 12 1 NIL NIL NIL NIL)'
                        b'("TEXT" "HTML" ("CHARSET" "utf-8") NIL NIL "QUOTED-PRINTABLE" 24 1 NIL NIL NIL NIL)'
                        b'("APPLICATION" "PDF" NIL NIL NIL "BASE64" 999 NIL ("ATTACHMENT" ("FILENAME" "cv.pdf")) NIL NIL) '
                        b'"MIXED" ("BOUNDARY" "mix") NIL NIL))',
                    ),
                ]
            return "OK", [
                (
                    b"1 (BODY[HEADER.FIELDS] {84}",
                    b"From: prof@example.edu\r\nMessage-ID: <reply@example.edu>\r\nSubject: Re: Hello\r\n\r\n",
                ),
            ]
        return "NO", []
