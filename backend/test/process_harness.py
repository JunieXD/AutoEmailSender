from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Sequence


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def reserve_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_until(
    predicate: Callable[[], Any],
    *,
    timeout_seconds: float = 15.0,
    poll_seconds: float = 0.05,
    description: str = "condition",
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
        except Exception as exc:  # noqa: BLE001 - the last probe error is diagnostic
            last_error = exc
        else:
            if value:
                return value
        time.sleep(poll_seconds)
    detail = f"; last error: {last_error}" if last_error is not None else ""
    raise TimeoutError(f"Timed out waiting for {description}{detail}")


def fetch_json(url: str, *, timeout_seconds: float = 0.5) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"Expected JSON object from {url}")
    return payload


def post_json(
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw_payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    result = json.loads(raw_payload.decode("utf-8"))
    if not isinstance(result, dict):
        raise TypeError(f"Expected JSON object from {url}")
    return result


def patch_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw_payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    result = json.loads(raw_payload.decode("utf-8"))
    if not isinstance(result, dict):
        raise TypeError(f"Expected JSON object from {url}")
    return result


class FakeSMTPServer:
    """Small STARTTLS SMTP server that counts accepted DATA transactions."""

    def __init__(self, root: Path, *, drop_data_response: bool = False) -> None:
        self.root = root.resolve()
        self.port = reserve_tcp_port()
        self.drop_data_response = drop_data_response
        self._accepted_messages: list[bytes] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._listener: socket.socket | None = None
        self._server_thread: threading.Thread | None = None
        self._client_threads: list[threading.Thread] = []
        self._tls_context: ssl.SSLContext | None = None

    @property
    def accepted_count(self) -> int:
        with self._lock:
            return len(self._accepted_messages)

    @property
    def accepted_messages(self) -> tuple[bytes, ...]:
        with self._lock:
            return tuple(self._accepted_messages)

    def start(self) -> FakeSMTPServer:
        self.root.mkdir(parents=True, exist_ok=True)
        certificate_path, private_key_path = self._create_certificate()
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.load_cert_chain(certificate_path, private_key_path)
        self._tls_context = tls_context

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", self.port))
        listener.listen()
        listener.settimeout(0.1)
        self._listener = listener
        self._server_thread = threading.Thread(
            target=self._serve,
            name=f"fake-smtp-{self.port}",
            daemon=True,
        )
        self._server_thread.start()
        return self

    def wait_for_accepted_count(
        self,
        count: int,
        *,
        timeout_seconds: float = 10.0,
    ) -> int:
        return int(
            wait_until(
                lambda: self.accepted_count if self.accepted_count >= count else 0,
                timeout_seconds=timeout_seconds,
                description=f"fake SMTP DATA count {count}",
            )
        )

    def stop(self) -> None:
        self._stop_event.set()
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        if self._server_thread is not None:
            self._server_thread.join(timeout=2)
        for client_thread in self._client_threads:
            client_thread.join(timeout=2)

    def __enter__(self) -> FakeSMTPServer:
        return self.start()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()

    def _serve(self) -> None:
        while not self._stop_event.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                connection, _address = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            client_thread = threading.Thread(
                target=self._handle_client,
                args=(connection,),
                name=f"fake-smtp-client-{self.port}",
                daemon=True,
            )
            self._client_threads.append(client_thread)
            client_thread.start()

    def _handle_client(self, connection: socket.socket) -> None:
        connection.settimeout(10)
        stream = connection.makefile("rb")
        try:
            connection.sendall(b"220 localhost test SMTP ready\r\n")
            tls_active = False
            while not self._stop_event.is_set():
                line = stream.readline()
                if not line:
                    return
                command = line.decode("ascii", errors="replace").strip()
                verb = command.split(" ", 1)[0].upper()
                if verb in {"EHLO", "HELO"}:
                    if tls_active:
                        connection.sendall(
                            b"250-localhost\r\n250 AUTH PLAIN LOGIN\r\n"
                        )
                    else:
                        connection.sendall(
                            b"250-localhost\r\n"
                            b"250-STARTTLS\r\n"
                            b"250 AUTH PLAIN LOGIN\r\n"
                        )
                elif verb == "STARTTLS":
                    connection.sendall(b"220 Ready to start TLS\r\n")
                    stream.close()
                    assert self._tls_context is not None
                    plaintext_connection = connection
                    connection = self._tls_context.wrap_socket(
                        plaintext_connection,
                        server_side=True,
                    )
                    plaintext_connection.close()
                    connection.settimeout(10)
                    stream = connection.makefile("rb")
                    tls_active = True
                elif verb == "AUTH":
                    connection.sendall(b"235 2.7.0 Authentication successful\r\n")
                elif verb in {"MAIL", "RCPT", "RSET", "NOOP"}:
                    connection.sendall(b"250 2.0.0 OK\r\n")
                elif verb == "DATA":
                    connection.sendall(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                    message_lines: list[bytes] = []
                    while True:
                        data_line = stream.readline()
                        if not data_line:
                            return
                        if data_line == b".\r\n":
                            break
                        message_lines.append(
                            data_line[1:] if data_line.startswith(b"..") else data_line
                        )
                    with self._lock:
                        self._accepted_messages.append(b"".join(message_lines))
                    if self.drop_data_response:
                        return
                    connection.sendall(b"250 2.0.0 queued\r\n")
                elif verb == "QUIT":
                    connection.sendall(b"221 2.0.0 bye\r\n")
                    return
                else:
                    connection.sendall(b"250 2.0.0 OK\r\n")
        except (OSError, ssl.SSLError, TimeoutError):
            return
        finally:
            try:
                stream.close()
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass

    def _create_certificate(self) -> tuple[Path, Path]:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        certificate_path = self.root / "smtp-cert.pem"
        private_key_path = self.root / "smtp-key.pem"
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]
        )
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
            .sign(private_key, hashes.SHA256())
        )
        certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        private_key_path.write_bytes(
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        return certificate_path, private_key_path


@dataclass(frozen=True, slots=True)
class FakeImapMessage:
    uid: int
    raw_message: bytes
    folder: str = "INBOX"


class FakeIMAPServer:
    """Small plaintext IMAP4rev1 server for real-process synchronization tests."""

    def __init__(
        self,
        messages: Sequence[FakeImapMessage],
        *,
        uidvalidity: int = 7001,
        port: int | None = None,
    ) -> None:
        self.port = port or reserve_tcp_port()
        self.uidvalidity = uidvalidity
        self._messages = tuple(messages)
        self._commands: list[str] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._listener: socket.socket | None = None
        self._server_thread: threading.Thread | None = None
        self._client_threads: list[threading.Thread] = []
        self._connections: list[socket.socket] = []

    @property
    def commands(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._commands)

    @property
    def connection_count(self) -> int:
        with self._lock:
            return sum(command == "CONNECT" for command in self._commands)

    @property
    def search_count(self) -> int:
        with self._lock:
            return sum(command.startswith("UID SEARCH") for command in self._commands)

    @property
    def fetch_count(self) -> int:
        with self._lock:
            return sum(command.startswith("UID FETCH") for command in self._commands)

    def start(self) -> FakeIMAPServer:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", self.port))
        listener.listen()
        listener.settimeout(0.1)
        self._listener = listener
        self._server_thread = threading.Thread(
            target=self._serve,
            name=f"fake-imap-{self.port}",
            daemon=True,
        )
        self._server_thread.start()
        return self

    def wait_for_fetch_count(
        self,
        count: int,
        *,
        timeout_seconds: float = 10.0,
    ) -> int:
        return int(
            wait_until(
                lambda: self.fetch_count if self.fetch_count >= count else 0,
                timeout_seconds=timeout_seconds,
                description=f"fake IMAP FETCH count {count}",
            )
        )

    def stop(self) -> None:
        self._stop_event.set()
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        with self._lock:
            connections = tuple(self._connections)
        for connection in connections:
            try:
                connection.close()
            except OSError:
                pass
        if self._server_thread is not None:
            self._server_thread.join(timeout=2)
        for client_thread in self._client_threads:
            client_thread.join(timeout=2)

    def __enter__(self) -> FakeIMAPServer:
        return self.start()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()

    def _serve(self) -> None:
        while not self._stop_event.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                connection, _address = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with self._lock:
                self._commands.append("CONNECT")
                self._connections.append(connection)
            client_thread = threading.Thread(
                target=self._handle_client,
                args=(connection,),
                name=f"fake-imap-client-{self.port}",
                daemon=True,
            )
            self._client_threads.append(client_thread)
            client_thread.start()

    def _handle_client(self, connection: socket.socket) -> None:
        connection.settimeout(10)
        stream = connection.makefile("rb")
        selected_folder = "INBOX"
        try:
            connection.sendall(b"* OK fake IMAP4rev1 ready\r\n")
            while not self._stop_event.is_set():
                line = stream.readline()
                if not line:
                    return
                command_line = line.decode("utf-8", errors="replace").strip()
                pieces = command_line.split(" ", 2)
                if len(pieces) < 2:
                    continue
                tag = pieces[0]
                verb = pieces[1].upper()
                arguments = pieces[2] if len(pieces) > 2 else ""
                self._record_command(
                    f"{verb} {arguments}".strip(),
                )
                if verb == "CAPABILITY":
                    connection.sendall(
                        b"* CAPABILITY IMAP4rev1 ID UIDPLUS\r\n"
                        + f"{tag} OK CAPABILITY completed\r\n".encode("ascii")
                    )
                elif verb == "LOGIN":
                    self._send_tagged(connection, tag, "OK", "LOGIN completed")
                elif verb == "ID":
                    connection.sendall(b"* ID NIL\r\n")
                    self._send_tagged(connection, tag, "OK", "ID completed")
                elif verb == "LIST":
                    connection.sendall(
                        b'* LIST (\\HasNoChildren) "/" "INBOX"\r\n'
                        b'* LIST (\\HasNoChildren \\Sent) "/" "Sent"\r\n'
                    )
                    self._send_tagged(connection, tag, "OK", "LIST completed")
                elif verb in {"SELECT", "EXAMINE"}:
                    candidate = self._decode_mailbox(arguments)
                    if candidate.lower() not in {"inbox", "sent"}:
                        self._send_tagged(connection, tag, "NO", "no such mailbox")
                        continue
                    selected_folder = "INBOX" if candidate.lower() == "inbox" else "Sent"
                    selected = self._messages_for_folder(selected_folder)
                    uidnext = max((message.uid for message in selected), default=0) + 1
                    connection.sendall(
                        b"* FLAGS ()\r\n"
                        + f"* {len(selected)} EXISTS\r\n".encode("ascii")
                        + f"* OK [UIDVALIDITY {self.uidvalidity}] UIDs valid\r\n".encode(
                            "ascii"
                        )
                        + f"* OK [UIDNEXT {uidnext}] Predicted next UID\r\n".encode(
                            "ascii"
                        )
                    )
                    self._send_tagged(
                        connection,
                        tag,
                        "OK",
                        f"[{ 'READ-ONLY' if verb == 'EXAMINE' else 'READ-WRITE' }] SELECT completed",
                    )
                elif verb == "UID":
                    self._handle_uid(connection, tag, arguments, selected_folder)
                elif verb in {"NOOP", "CHECK", "CLOSE", "UNSELECT"}:
                    self._send_tagged(connection, tag, "OK", f"{verb} completed")
                elif verb == "LOGOUT":
                    connection.sendall(b"* BYE fake IMAP logging out\r\n")
                    self._send_tagged(connection, tag, "OK", "LOGOUT completed")
                    return
                else:
                    self._send_tagged(connection, tag, "BAD", "unsupported command")
        except (OSError, TimeoutError):
            return
        finally:
            try:
                stream.close()
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
            with self._lock:
                try:
                    self._connections.remove(connection)
                except ValueError:
                    pass

    def _handle_uid(
        self,
        connection: socket.socket,
        tag: str,
        arguments: str,
        selected_folder: str,
    ) -> None:
        pieces = arguments.split(" ", 1)
        subcommand = pieces[0].upper() if pieces else ""
        remainder = pieces[1] if len(pieces) > 1 else ""
        selected = self._messages_for_folder(selected_folder)
        if subcommand == "SEARCH":
            matches = self._search_messages(selected, remainder)
            payload = " ".join(str(message.uid) for message in matches)
            connection.sendall(f"* SEARCH {payload}\r\n".encode("ascii"))
            self._send_tagged(connection, tag, "OK", "UID SEARCH completed")
            return
        if subcommand != "FETCH":
            self._send_tagged(connection, tag, "BAD", "unsupported UID command")
            return
        selector, _, fetch_spec = remainder.partition(" ")
        messages = self._select_messages(selected, selector)
        upper_spec = fetch_spec.upper()
        if "BODYSTRUCTURE" in upper_spec:
            self._send_tagged(connection, tag, "OK", "UID FETCH completed")
            return
        for sequence, message in enumerate(selected, start=1):
            if message not in messages:
                continue
            headers, body = self._split_message(message.raw_message)
            if "BODY.PEEK[TEXT]" in upper_spec or "BODY[TEXT]" in upper_spec:
                payload = body
                section = "TEXT"
            else:
                payload = headers
                section = "HEADER"
            prefix = (
                f'* {sequence} FETCH (UID {message.uid} '
                'INTERNALDATE "09-Aug-2026 12:00:00 +0000" '
                f"BODY[{section}] {{{len(payload)}}}\r\n"
            ).encode("ascii")
            connection.sendall(prefix + payload + b")\r\n")
        self._send_tagged(connection, tag, "OK", "UID FETCH completed")

    def _messages_for_folder(self, folder: str) -> tuple[FakeImapMessage, ...]:
        return tuple(
            sorted(
                (
                    message
                    for message in self._messages
                    if message.folder.lower() == folder.lower()
                ),
                key=lambda message: message.uid,
            )
        )

    @staticmethod
    def _search_messages(
        messages: Sequence[FakeImapMessage],
        criterion: str,
    ) -> tuple[FakeImapMessage, ...]:
        uid_range = re.search(r"\bUID\s+(\d+):(?:\*|(\d+))", criterion, re.I)
        if uid_range is None:
            return tuple(messages)
        start_uid = int(uid_range.group(1))
        end_uid = int(uid_range.group(2)) if uid_range.group(2) else None
        return tuple(
            message
            for message in messages
            if message.uid >= start_uid
            and (end_uid is None or message.uid <= end_uid)
        )

    @staticmethod
    def _select_messages(
        messages: Sequence[FakeImapMessage],
        selector: str,
    ) -> tuple[FakeImapMessage, ...]:
        selected_uids: set[int] = set()
        max_uid = max((message.uid for message in messages), default=0)
        for component in selector.split(","):
            component = component.strip()
            if not component:
                continue
            if ":" not in component:
                if component.isdigit():
                    selected_uids.add(int(component))
                continue
            start_text, end_text = component.split(":", 1)
            if not start_text.isdigit():
                continue
            start_uid = int(start_text)
            end_uid = max_uid if end_text == "*" else int(end_text)
            selected_uids.update(range(start_uid, end_uid + 1))
        return tuple(message for message in messages if message.uid in selected_uids)

    @staticmethod
    def _split_message(raw_message: bytes) -> tuple[bytes, bytes]:
        normalized = raw_message.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        headers, separator, body = normalized.partition(b"\n\n")
        if not separator:
            headers, body = normalized, b""
        normalized_headers = headers.replace(b"\n", b"\r\n") + b"\r\n\r\n"
        normalized_body = body.replace(b"\n", b"\r\n")
        return normalized_headers, normalized_body

    @staticmethod
    def _decode_mailbox(arguments: str) -> str:
        value = arguments.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        return value.replace(r'\"', '"').replace(r"\\", "\\")

    def _record_command(self, command: str) -> None:
        with self._lock:
            self._commands.append(command)

    @staticmethod
    def _send_tagged(
        connection: socket.socket,
        tag: str,
        status: str,
        detail: str,
    ) -> None:
        connection.sendall(f"{tag} {status} {detail}\r\n".encode("utf-8"))


class FakeHTTPServer:
    """Deterministic local HTML server used by real crawler processes."""

    def __init__(self, pages: dict[str, str] | None = None) -> None:
        self.port = reserve_tcp_port()
        self._pages = dict(pages or {})
        self._requests: list[str] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None

    @property
    def request_count(self) -> int:
        with self._lock:
            return len(self._requests)

    @property
    def requests(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._requests)

    def url(self, path: str = "/", *, hostname: str = "crawler.test.invalid") -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"http://{hostname}:{self.port}{normalized_path}"

    def set_page(self, path: str, html: str) -> None:
        normalized_path = path if path.startswith("/") else f"/{path}"
        with self._lock:
            self._pages[normalized_path] = html

    def start(self) -> FakeHTTPServer:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
                path = self.path.split("?", 1)[0] or "/"
                with owner._lock:
                    owner._requests.append(path)
                    body_text = owner._pages.get(path)
                if body_text is None:
                    self._write(404, "not found", "text/plain; charset=utf-8")
                    return
                self._write(200, body_text, "text/html; charset=utf-8")

            def log_message(self, format: str, *args: object) -> None:
                _ = format, args

            def _write(self, status: int, body_text: str, content_type: str) -> None:
                body = body_text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    return

        server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        server.daemon_threads = True
        self._server = server
        self._server_thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name=f"fake-http-{self.port}",
            daemon=True,
        )
        self._server_thread.start()
        return self

    def wait_for_request_count(
        self,
        count: int,
        *,
        timeout_seconds: float = 10.0,
    ) -> int:
        return int(
            wait_until(
                lambda: self.request_count if self.request_count >= count else 0,
                timeout_seconds=timeout_seconds,
                description=f"fake HTTP request count {count}",
            )
        )

    def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._server_thread is not None:
            self._server_thread.join(timeout=2)
            self._server_thread = None

    def __enter__(self) -> FakeHTTPServer:
        return self.start()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()


class FakeLLMServer:
    """Local OpenAI-compatible HTTP server with deterministic request accounting."""

    def __init__(
        self,
        *,
        response_factory: Callable[[int, dict[str, Any]], str] | None = None,
    ) -> None:
        self.port = reserve_tcp_port()
        self._response_factory = response_factory or self._default_response_content
        self._requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def request_count(self) -> int:
        with self._lock:
            return len(self._requests)

    @property
    def requests(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(dict(request) for request in self._requests)

    def start(self) -> FakeLLMServer:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
                if not self.path.rstrip("/").endswith("/models"):
                    self._write_json(404, {"error": {"message": "not found"}})
                    return
                self._write_json(
                    200,
                    {"object": "list", "data": [{"id": "test-model"}]},
                )

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._write_json(400, {"error": {"message": "bad length"}})
                    return
                raw_body = self.rfile.read(max(0, content_length))
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._write_json(400, {"error": {"message": "invalid json"}})
                    return
                if not isinstance(payload, dict):
                    self._write_json(400, {"error": {"message": "object required"}})
                    return

                with owner._lock:
                    owner._requests.append(
                        {
                            "path": self.path,
                            "headers": {
                                key.lower(): value for key, value in self.headers.items()
                            },
                            "payload": payload,
                        }
                    )
                    request_number = len(owner._requests)
                content = owner._response_factory(request_number, payload)
                if self.path.rstrip("/").endswith("/responses"):
                    response_payload: dict[str, Any] = {
                        "id": f"fake-response-{request_number}",
                        "object": "response",
                        "status": "completed",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [
                                    {"type": "output_text", "text": content},
                                ],
                            }
                        ],
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "total_tokens": 15,
                        },
                    }
                elif self.path.rstrip("/").endswith("/chat/completions"):
                    response_payload = {
                        "id": f"fake-chat-{request_number}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": str(payload.get("model") or "test-model"),
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": content,
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                            "total_tokens": 15,
                        },
                    }
                else:
                    self._write_json(404, {"error": {"message": "not found"}})
                    return
                self._write_json(200, response_payload)

            def log_message(self, format: str, *args: object) -> None:
                _ = format, args

            def _write_json(self, status_code: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    return

        server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        server.daemon_threads = True
        self._server = server
        self._server_thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name=f"fake-llm-{self.port}",
            daemon=True,
        )
        self._server_thread.start()
        return self

    def wait_for_request_count(
        self,
        count: int,
        *,
        timeout_seconds: float = 10.0,
    ) -> int:
        return int(
            wait_until(
                lambda: self.request_count if self.request_count >= count else 0,
                timeout_seconds=timeout_seconds,
                description=f"fake LLM request count {count}",
            )
        )

    def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._server_thread is not None:
            self._server_thread.join(timeout=2)
            self._server_thread = None

    def __enter__(self) -> FakeLLMServer:
        return self.start()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()

    @staticmethod
    def _default_response_content(
        request_number: int,
        payload: dict[str, Any],
    ) -> str:
        messages = payload.get("messages")
        message_text = ""
        if isinstance(messages, list):
            message_text = "\n".join(
                str(message.get("content") or "")
                for message in messages
                if isinstance(message, dict)
            )
        if "match_score" in message_text:
            return json.dumps(
                {
                    "match_score": 85,
                    "match_reason": f"fake match {request_number}",
                    "fit_points": ["deterministic"],
                    "risk_points": [],
                    "keywords": ["test"],
                },
                ensure_ascii=False,
            )
        if "replacements" in message_text:
            return json.dumps({"replacements": []})
        if "只回复 OK" in message_text:
            return "OK"
        return json.dumps(
            {
                "subject": f"fake draft {request_number}",
                "blocks": [
                    {
                        "type": "paragraph",
                        "items": [
                            {
                                "runs": [
                                    {
                                        "text": f"fake body {request_number}",
                                        "strong": False,
                                        "emphasis": False,
                                        "href": "",
                                        "line_break_after": False,
                                    }
                                ]
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )


@dataclass(slots=True)
class ManagedProcess:
    process: subprocess.Popen[bytes]
    stdout_path: Path
    stderr_path: Path

    @property
    def pid(self) -> int:
        return self.process.pid

    def read_stdout(self) -> str:
        return self.stdout_path.read_text(encoding="utf-8", errors="replace")

    def read_stderr(self) -> str:
        return self.stderr_path.read_text(encoding="utf-8", errors="replace")

    def wait(self, *, timeout_seconds: float = 10.0) -> int:
        return self.process.wait(timeout=timeout_seconds)

    def stop(self, *, timeout_seconds: float = 10.0) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=timeout_seconds)


def spawn_managed_process(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_dir: Path,
    name: str,
) -> ManagedProcess:
    log_dir.mkdir(parents=True, exist_ok=True)
    suffix = uuid.uuid4().hex
    stdout_path = log_dir / f"{name}-{suffix}.stdout.log"
    stderr_path = log_dir / f"{name}-{suffix}.stderr.log"
    with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
        )
    return ManagedProcess(process, stdout_path, stderr_path)


class FaultController:
    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def environment(
        self,
        *fault_points: str,
        process_id: str = "test-process",
        timeout_seconds: float = 30.0,
    ) -> dict[str, str]:
        return {
            "AUTO_EMAIL_SENDER_TEST_FAULTS": "enabled-for-tests-only",
            "AUTO_EMAIL_SENDER_TEST_FAULT_DIR": str(self.directory),
            "AUTO_EMAIL_SENDER_TEST_FAULT_POINTS": ",".join(fault_points),
            "AUTO_EMAIL_SENDER_TEST_PROCESS_ID": process_id,
            "AUTO_EMAIL_SENDER_TEST_FAULT_TIMEOUT_SECONDS": str(timeout_seconds),
        }

    def wait_for_reached(
        self,
        fault_point: str,
        *,
        timeout_seconds: float = 10.0,
    ) -> Path:
        def find_reached() -> Path | None:
            matches = sorted(self.directory.glob(f"*--{fault_point}--*.reached"))
            return matches[0] if matches else None

        return wait_until(
            find_reached,
            timeout_seconds=timeout_seconds,
            description=f"fault point {fault_point!r}",
        )

    def release(self, reached_path: Path) -> Path:
        if reached_path.suffix != ".reached":
            raise ValueError(f"Not a reached fault marker: {reached_path}")
        release_path = reached_path.with_suffix(".release")
        release_path.touch(exist_ok=False)
        return release_path


class TestClockController:
    """Atomically move the canonical application clock in child processes."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.offset_path = self.directory / "clock-offset-seconds.txt"
        self.set_offset_seconds(0)

    def environment(self) -> dict[str, str]:
        return {
            "AUTO_EMAIL_SENDER_TEST_FAULTS": "enabled-for-tests-only",
            "AUTO_EMAIL_SENDER_TEST_FAULT_DIR": str(self.directory),
            "AUTO_EMAIL_SENDER_TEST_CLOCK_OFFSET_FILE": str(self.offset_path),
        }

    def set_offset_seconds(self, offset_seconds: float) -> None:
        temporary_path = self.offset_path.with_suffix(".tmp")
        temporary_path.write_text(str(float(offset_seconds)), encoding="utf-8")
        temporary_path.replace(self.offset_path)


class DesktopBackendProcess:
    def __init__(
        self,
        *,
        data_dir: Path,
        port: int | None = None,
        name: str = "desktop-backend",
        role: str = "combined",
        runtime_id: str | None = None,
        api_pid: int | None = None,
        worker_generation: str | None = None,
        extra_env: dict[str, str] | None = None,
        entry_script: Path | None = None,
    ) -> None:
        self.data_dir = data_dir.resolve()
        self.port = port or reserve_tcp_port()
        self.name = name
        self.role = role
        self.runtime_id = runtime_id or f"test-{uuid.uuid4()}"
        self.api_pid = api_pid
        self.worker_generation = worker_generation or f"worker-{uuid.uuid4()}"
        self.extra_env = dict(extra_env or {})
        self.entry_script = (entry_script or (BACKEND_ROOT / "desktop_entry.py")).resolve()
        self.managed: ManagedProcess | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def process(self) -> subprocess.Popen[bytes]:
        if self.managed is None:
            raise RuntimeError("Desktop backend has not been started")
        return self.managed.process

    def start(self) -> DesktopBackendProcess:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        database_path = self.data_dir / "auto_email_sender.db"
        env = os.environ.copy()
        env.update(
            {
                "PYTHONUNBUFFERED": "1",
                "AUTO_EMAIL_SENDER_DATA_DIR": str(self.data_dir),
                "DATABASE_URL": f"sqlite+aiosqlite:///{database_path.as_posix()}",
                "AUTO_EMAIL_SENDER_DESKTOP_PID": str(os.getpid()),
                "AUTO_EMAIL_SENDER_RUNTIME_ID": self.runtime_id,
                "AUTO_EMAIL_SENDER_APP_VERSION": "test",
                "ENABLE_BACKGROUND_WORKERS": "0",
            }
        )
        if self.api_pid is not None:
            env["AUTO_EMAIL_SENDER_API_PID"] = str(self.api_pid)
        if self.role == "worker":
            env["AUTO_EMAIL_SENDER_WORKER_GENERATION"] = self.worker_generation
        env.update(self.extra_env)
        self.managed = spawn_managed_process(
            [
                sys.executable,
                str(self.entry_script),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--role",
                self.role,
            ],
            cwd=BACKEND_ROOT,
            env=env,
            log_dir=self.data_dir / "test-process-logs",
            name=self.name,
        )
        return self

    def wait_ready(self, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
        def probe() -> dict[str, Any] | None:
            if self.process.poll() is not None:
                stderr = self.managed.read_stderr() if self.managed is not None else ""
                raise RuntimeError(
                    f"Desktop backend exited with {self.process.returncode}: {stderr[-2000:]}"
                )
            try:
                status = fetch_json(f"{self.base_url}/startup-status")
            except (OSError, urllib.error.URLError, json.JSONDecodeError):
                return None
            if status.get("state") == "error":
                raise RuntimeError(f"Desktop backend startup failed: {status}")
            return status if status.get("state") == "ready" else None

        return wait_until(
            probe,
            timeout_seconds=timeout_seconds,
            description=f"{self.name} readiness",
        )

    def wait_worker_ready(self, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
        status_path = self.data_dir / "runtime" / "worker.json"

        def probe() -> dict[str, Any] | None:
            if self.process.poll() is not None:
                stderr = self.managed.read_stderr() if self.managed is not None else ""
                raise RuntimeError(
                    f"Desktop Worker exited with {self.process.returncode}: {stderr[-2000:]}"
                )
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                return None
            if not isinstance(status, dict):
                return None
            if status.get("state") == "error":
                raise RuntimeError(f"Desktop Worker startup failed: {status}")
            if (
                status.get("state") == "ready"
                and status.get("runtime_id") == self.runtime_id
                and status.get("generation") == self.worker_generation
                and status.get("pid") == self.process.pid
            ):
                return status
            return None

        return wait_until(
            probe,
            timeout_seconds=timeout_seconds,
            description=f"{self.name} readiness",
        )

    def stop(self) -> None:
        if self.managed is not None:
            self.managed.stop()

    def __enter__(self) -> DesktopBackendProcess:
        return self.start()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()


__all__ = [
    "BACKEND_ROOT",
    "DesktopBackendProcess",
    "FakeHTTPServer",
    "FakeIMAPServer",
    "FakeImapMessage",
    "FakeLLMServer",
    "FakeSMTPServer",
    "FaultController",
    "ManagedProcess",
    "TestClockController",
    "fetch_json",
    "patch_json",
    "post_json",
    "reserve_tcp_port",
    "spawn_managed_process",
    "wait_until",
]
