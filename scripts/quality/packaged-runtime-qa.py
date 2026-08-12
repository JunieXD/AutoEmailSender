#!/usr/bin/env python3
"""Cross-platform lifecycle and soak certification for the packaged desktop app.

The runner never uses the normal Electron userData directory.  It creates an
isolated, non-ASCII path containing the desktop startup gate marker, launches
the real packaged executable, and records machine-readable evidence without
persisting either desktop access token.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import importlib
import json
import math
import os
import platform
import random
import re
import signal
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar, Literal, TypeVar

import psutil
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

PROTOCOL_VERSION = "1"
QA_ENABLE_ENV = "AUTO_EMAIL_SENDER_PACKAGED_QA"
QA_ENABLE_VALUE = "enabled-for-release-certification"
QA_NONCE_ENV = "AUTO_EMAIL_SENDER_PACKAGED_QA_NONCE"
QA_USER_DATA_ENV = "AUTO_EMAIL_SENDER_PACKAGED_QA_USER_DATA"
QA_DIAGNOSTICS_EXPORT_ENV = "AUTO_EMAIL_SENDER_PACKAGED_QA_DIAGNOSTICS_EXPORT"
QA_DIAGNOSTICS_EXPORT_VALUE = "required"
QA_DIAGNOSTICS_EXPORT_NAME = "packaged-qa-beta-diagnostics.zip"
QA_PATH_MARKER = "auto-email-sender-packaged-qa"
QA_SENTINEL_NAME = ".auto-email-sender-packaged-qa.json"
QA_SENTINEL_PROTOCOL_VERSION = "1"
QA_GRACEFUL_QUIT_MESSAGE = 0x84A5
QA_CRAWL_HOST = "packaged-qa.test.invalid"
RUNTIME_DESCRIPTOR_RELATIVE_PATH = Path("agent") / "runtime.json"
DATABASE_NAME = "auto_email_sender.db"
SETTINGS_READ_ONLY_FIELDS = frozenset({"revision", "updated_at"})
REPORT_NAME = "report.json"
TRACE_NAME = "trace.jsonl"
RESOURCE_SAMPLES_NAME = "resource-samples.jsonl"
CERTIFICATION_MINIMUM_SECONDS = {
    "normal-soak": 24 * 60 * 60,
    "seeded-chaos": 8 * 60 * 60,
}
PRERELEASE_CERTIFICATION_MINIMUM_SECONDS = {
    "normal-soak": 5 * 60,
    "seeded-chaos": 5 * 60,
}
PRERELEASE_SAMPLE_INTERVAL_SECONDS = 10.0
PRERELEASE_ACTION_INTERVAL_SECONDS = 5.0
ROLE_STATUS_PROTOCOL_VERSION = "2"
AGENT_PROTOCOL_VERSION = "3"
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
T = TypeVar("T")


class QaFailure(RuntimeError):
    """A packaged QA contract or runtime invariant failed."""


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    started_at: str


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    protocol_version: str
    app_version: str
    runtime_id: str
    base_url: str
    access_token: str = field(repr=False)
    desktop: ProcessIdentity = field(default_factory=lambda: ProcessIdentity(0, ""))
    backend: ProcessIdentity = field(default_factory=lambda: ProcessIdentity(0, ""))
    worker: ProcessIdentity | None = None
    published_at: str = ""

    @classmethod
    def from_payload(cls, payload: object) -> RuntimeIdentity:
        if not isinstance(payload, dict):
            raise QaFailure("runtime descriptor must be a JSON object")
        required_strings = (
            "protocol_version",
            "app_version",
            "runtime_id",
            "base_url",
            "access_token",
            "published_at",
        )
        values: dict[str, str] = {}
        for name in required_strings:
            value = payload.get(name)
            if not isinstance(value, str) or not value.strip():
                raise QaFailure(f"runtime descriptor field {name} is invalid")
            values[name] = value.strip()
        worker_payload = payload.get("worker")
        return cls(
            desktop=_parse_process_identity(payload.get("desktop"), "desktop"),
            backend=_parse_process_identity(payload.get("backend"), "backend"),
            worker=(
                None
                if worker_payload is None
                else _parse_process_identity(worker_payload, "worker")
            ),
            **values,
        )

    def evidence_payload(self) -> dict[str, object]:
        """Return the descriptor identity with the credential intentionally absent."""

        return {
            "protocol_version": self.protocol_version,
            "app_version": self.app_version,
            "runtime_id": self.runtime_id,
            "base_url": self.base_url,
            "desktop": asdict(self.desktop),
            "backend": asdict(self.backend),
            "worker": asdict(self.worker) if self.worker is not None else None,
            "published_at": self.published_at,
        }


@dataclass(slots=True)
class DatabaseAudit:
    at: str
    integrity_check: list[str]
    quick_check: list[str]
    foreign_key_violations: list[list[object]]
    invariant_violations: list[str]
    wal_bytes: int
    shm_bytes: int

    @property
    def passed(self) -> bool:
        return (
            self.integrity_check == ["ok"]
            and self.quick_check == ["ok"]
            and not self.foreign_key_violations
            and not self.invariant_violations
        )


@dataclass(slots=True)
class QaPaths:
    run_root: Path
    user_data: Path
    fault_dir: Path
    clock_offset: Path
    logs_dir: Path
    report_path: Path
    trace_path: Path
    samples_path: Path


@dataclass(slots=True)
class LaunchHandle:
    process: subprocess.Popen[bytes]
    stdout_file: Any
    stderr_file: Any
    nonce: str
    mode: Literal["split", "combined"]
    launched_at_monotonic: float
    launched_at_wall: float

    def close_logs(self) -> None:
        self.stdout_file.close()
        self.stderr_file.close()


@dataclass(slots=True)
class BrowserProbe:
    server: ThreadingHTTPServer
    thread: threading.Thread
    llm_server: Any
    browser_request_started: threading.Event
    release_response: threading.Event
    port: int

    @property
    def url(self) -> str:
        return f"http://{QA_CRAWL_HOST}:{self.port}/directory"

    def stop(self) -> None:
        self.release_response.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.llm_server.stop()


@dataclass(frozen=True, slots=True)
class WorkloadSupport:
    fake_smtp_server: type[Any]
    fake_imap_server: type[Any]
    fake_imap_message: type[Any]
    fake_http_server: type[Any]
    fake_llm_server: type[Any]
    delivery_tests: type[Any]
    batch_tests: type[Any]
    match_tests: type[Any]
    imap_tests: type[Any]
    crawler_tests: type[Any]


@dataclass(slots=True)
class SeededWorkloadCycle:
    index: int
    chaos: bool
    network_flap: bool
    crawler_kind: str
    delivery_task_id: int
    batch_task_id: int
    batch_email_task_id: int
    match_job_id: int
    match_item_id: int
    imap_incremental_identity_id: int
    imap_incremental_professor_id: int
    imap_history_identity_id: int
    imap_history_professor_id: int
    crawler_work: Any
    smtp_accepted_before: int
    llm_requests_before: int
    http_requests_before: int
    incremental_server: Any
    history_server: Any


def _load_workload_support() -> WorkloadSupport:
    """Load QA-only fixtures without adding them to the packaged application."""

    repository_root = Path(__file__).resolve().parents[2]
    backend_root = repository_root / "backend"
    backend_root_text = str(backend_root)
    if backend_root_text not in sys.path:
        sys.path.insert(0, backend_root_text)
    process_harness = importlib.import_module("test.process_harness")
    delivery_module = importlib.import_module("test.test_email_delivery_process_safety")
    batch_module = importlib.import_module("test.test_batch_draft_process_safety")
    match_module = importlib.import_module("test.test_match_analysis_process_safety")
    imap_module = importlib.import_module("test.test_imap_process_safety")
    crawler_module = importlib.import_module("test.test_crawler_process_safety")
    return WorkloadSupport(
        fake_smtp_server=process_harness.FakeSMTPServer,
        fake_imap_server=process_harness.FakeIMAPServer,
        fake_imap_message=process_harness.FakeImapMessage,
        fake_http_server=process_harness.FakeHTTPServer,
        fake_llm_server=process_harness.FakeLLMServer,
        delivery_tests=delivery_module.EmailDeliveryProcessSafetyTests,
        batch_tests=batch_module.BatchDraftProcessSafetyTests,
        match_tests=match_module.MatchAnalysisProcessSafetyTests,
        imap_tests=imap_module.ImapProcessSafetyTests,
        crawler_tests=crawler_module.CrawlerProcessSafetyTests,
    )


class EvidenceRecorder:
    def __init__(self, paths: QaPaths, report: dict[str, Any]) -> None:
        self.paths = paths
        self.report = report
        self._lock = threading.Lock()

    def event(self, event_name: str, **details: object) -> None:
        payload = {
            "at": _utc_now(),
            "monotonic_seconds": time.monotonic(),
            "event": event_name,
            "details": _redact_payload(details),
        }
        with self._lock:
            _append_json_line(self.paths.trace_path, payload)
        print(f"[{payload['at']}] {event_name}", flush=True)

    def sample(self, payload: dict[str, object]) -> None:
        with self._lock:
            _append_json_line(self.paths.samples_path, _redact_payload(payload))

    def check(self, name: str, *, passed: bool, evidence: object = None) -> None:
        entry = {
            "at": _utc_now(),
            "name": name,
            "passed": bool(passed),
            "evidence": _redact_payload(evidence),
        }
        self.report.setdefault("checks", []).append(entry)
        self.event("check", name=name, passed=passed)
        if not passed:
            raise QaFailure(f"check failed: {name}")

    def write_report(self) -> None:
        _write_json_atomic(self.paths.report_path, _redact_payload(self.report))


class WorkloadHarness:
    """Continuously drive all six real Worker workload families.

    The harness only talks to loopback fake services and the isolated packaged
    SQLite database.  It deliberately records counts and state outcomes, not
    message bodies, prompts, passwords, or access tokens.
    """

    REQUIRED_WORKLOADS: ClassVar[tuple[str, ...]] = (
        "dispatcher",
        "imap_incremental",
        "imap_history",
        "batch_drafts",
        "matching",
        "crawler",
    )
    CRAWLER_KINDS: ClassVar[tuple[str, ...]] = ("page", "chunk", "enrichment")
    TERMINAL_CRAWLER_STATUSES: ClassVar[dict[str, str]] = {
        "page": "succeeded",
        "chunk": "completed",
        "enrichment": "succeeded",
    }

    def __init__(self, paths: QaPaths, recorder: EvidenceRecorder) -> None:
        self.paths = paths
        self.recorder = recorder
        self.database_path = paths.user_data / DATABASE_NAME
        self.support = _load_workload_support()
        service_root = paths.run_root / "fake-services"
        service_root.mkdir(parents=True, exist_ok=True)
        self.smtp_server = self.support.fake_smtp_server(service_root / "smtp").start()
        self.llm_server = self.support.fake_llm_server(
            response_factory=self._llm_response,
        ).start()
        self.http_server = self.support.fake_http_server().start()
        self.http_online = True
        self.summary: dict[str, Any] = {
            "protocol_version": "1",
            "required_workloads": list(self.REQUIRED_WORKLOADS),
            "cycles_started": 0,
            "cycles_completed": 0,
            "cycles_failed": 0,
            "workloads_completed": {name: 0 for name in self.REQUIRED_WORKLOADS},
            "crawler_kinds_completed": {name: 0 for name in self.CRAWLER_KINDS},
            "network_flaps_observed": 0,
            "smtp_data_accepted": 0,
            "llm_requests": 0,
            "http_requests": 0,
            "imap_fetches": 0,
        }
        recorder.report["workload_summary"] = self.summary
        recorder.event(
            "fake_workload_services_started",
            smtp_port=self.smtp_server.port,
            llm_port=self.llm_server.port,
            http_port=self.http_server.port,
            external_network=False,
        )

    def close(self) -> None:
        if self.http_online:
            with contextlib.suppress(Exception):
                self.http_server.stop()
            self.http_online = False
        with contextlib.suppress(Exception):
            self.llm_server.stop()
        with contextlib.suppress(Exception):
            self.smtp_server.stop()
        self.summary["smtp_data_accepted"] = self.smtp_server.accepted_count
        self.summary["llm_requests"] = self.llm_server.request_count
        self.summary["http_requests"] = self.http_server.request_count
        self.recorder.event(
            "fake_workload_services_stopped",
            smtp_data_accepted=self.smtp_server.accepted_count,
            llm_requests=self.llm_server.request_count,
            http_requests=self.http_server.request_count,
        )

    def run_cycle(
        self,
        *,
        index: int,
        chaos: bool,
        network_flap: bool,
        fault_callback: Callable[[], None] | None = None,
    ) -> dict[str, object]:
        cycle = self._seed_cycle(index=index, chaos=chaos, network_flap=network_flap)
        self.summary["cycles_started"] = int(self.summary["cycles_started"]) + 1
        try:
            if network_flap:
                outage = self._wait_for_network_outage(cycle)
                self.recorder.event(
                    "fake_service_network_outage_observed",
                    index=index,
                    **outage,
                )
                if fault_callback is not None:
                    fault_callback()
                    fault_callback = None
                self._restore_network_services(cycle)
                self.summary["network_flaps_observed"] = (
                    int(self.summary["network_flaps_observed"]) + 1
                )
            if fault_callback is not None:
                fault_callback()
            evidence = self._wait_for_cycle(cycle)
            self._disable_completed_imap_profiles(cycle)
            self.summary["cycles_completed"] = int(self.summary["cycles_completed"]) + 1
            completed = self.summary["workloads_completed"]
            for workload in self.REQUIRED_WORKLOADS:
                completed[workload] = int(completed[workload]) + 1
            crawler_kinds = self.summary["crawler_kinds_completed"]
            crawler_kinds[cycle.crawler_kind] = int(crawler_kinds[cycle.crawler_kind]) + 1
            self.summary["smtp_data_accepted"] = self.smtp_server.accepted_count
            self.summary["llm_requests"] = self.llm_server.request_count
            self.summary["http_requests"] = self.http_server.request_count
            self.summary["imap_fetches"] = int(self.summary["imap_fetches"]) + int(
                evidence["imap_fetches"]
            )
            self.recorder.event("workload_cycle_completed", index=index, **evidence)
            return evidence
        except Exception:
            self.summary["cycles_failed"] = int(self.summary["cycles_failed"]) + 1
            self.recorder.event("workload_cycle_failed", index=index)
            raise
        finally:
            if network_flap and not self.http_online:
                with contextlib.suppress(Exception):
                    self.http_server.start()
                    self.http_online = True
            for server in (cycle.incremental_server, cycle.history_server):
                with contextlib.suppress(Exception):
                    server.stop()

    def _seed_cycle(
        self,
        *,
        index: int,
        chaos: bool,
        network_flap: bool,
    ) -> SeededWorkloadCycle:
        if not self.database_path.is_file():
            raise QaFailure(f"packaged workload database is missing: {self.database_path}")
        suffix = f"{index}-{uuid.uuid4().hex[:12]}"
        crawler_kind = "page" if network_flap else self.CRAWLER_KINDS[index % 3]
        profile_path = f"/profile/{suffix}"
        self.http_server.set_page(profile_path, self._profile_html(suffix))
        profile_url = self.http_server.url(profile_path, hostname=QA_CRAWL_HOST)
        incremental_email = f"imap-incremental-{suffix}@example.edu"
        history_email = f"imap-history-{suffix}@example.edu"
        incremental_message = self.support.fake_imap_message(
            11,
            self.support.imap_tests._raw_message(
                f"<qa-incremental-{suffix}@example.edu>",
                professor_email=incremental_email,
            ),
        )
        history_message = self.support.fake_imap_message(
            11,
            self.support.imap_tests._raw_message(
                f"<qa-history-{suffix}@example.edu>",
                professor_email=history_email,
            ),
        )
        incremental_server = self.support.fake_imap_server(
            [incremental_message],
            uidvalidity=7001,
        )
        history_server = self.support.fake_imap_server(
            [history_message],
            uidvalidity=7001,
        )
        if network_flap:
            if self.http_online:
                self.http_server.stop()
                self.http_online = False
            self.recorder.event(
                "fake_service_network_flap_started",
                index=index,
                services=["crawler_http", "imap_incremental", "imap_history"],
                smtp_excluded_for_at_most_once=True,
            )
        else:
            incremental_server.start()
            history_server.start()

        smtp_before = self.smtp_server.accepted_count
        llm_before = self.llm_server.request_count
        http_before = self.http_server.request_count
        delivery_task_id = self.support.delivery_tests._seed_delivery_task(
            self.database_path,
            smtp_port=self.smtp_server.port,
            status="scheduled",
            scheduled_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        batch_task_id, batch_email_task_id = (
            self.support.batch_tests._seed_batch_draft_task(
                self.database_path,
                llm_base_url=self.llm_server.base_url,
                model_name=f"qa-batch-{suffix}",
            )
        )
        match_job_id, match_item_id = self.support.match_tests._seed_match_job(
            self.database_path,
            llm_base_url=self.llm_server.base_url,
            model_name=f"qa-match-{suffix}",
        )
        incremental_identity_id, incremental_professor_id = (
            self.support.imap_tests._seed_imap_workload(
                self.database_path,
                imap_port=incremental_server.port,
                workload="incremental",
                professor_email=incremental_email,
            )
        )
        history_identity_id, history_professor_id = (
            self.support.imap_tests._seed_imap_workload(
                self.database_path,
                imap_port=history_server.port,
                workload="history",
                professor_email=history_email,
            )
        )
        crawler_work = self.support.crawler_tests._seed_workload(
            self.database_path,
            kind=crawler_kind,
            llm_base_url=self.llm_server.base_url,
            profile_url=profile_url,
            model_name=f"qa-crawler-{suffix}",
        )
        cycle = SeededWorkloadCycle(
            index=index,
            chaos=chaos,
            network_flap=network_flap,
            crawler_kind=crawler_kind,
            delivery_task_id=delivery_task_id,
            batch_task_id=batch_task_id,
            batch_email_task_id=batch_email_task_id,
            match_job_id=match_job_id,
            match_item_id=match_item_id,
            imap_incremental_identity_id=incremental_identity_id,
            imap_incremental_professor_id=incremental_professor_id,
            imap_history_identity_id=history_identity_id,
            imap_history_professor_id=history_professor_id,
            crawler_work=crawler_work,
            smtp_accepted_before=smtp_before,
            llm_requests_before=llm_before,
            http_requests_before=http_before,
            incremental_server=incremental_server,
            history_server=history_server,
        )
        self.recorder.event(
            "workload_cycle_seeded",
            index=index,
            chaos=chaos,
            network_flap=network_flap,
            crawler_kind=crawler_kind,
            delivery_task_id=delivery_task_id,
            batch_task_id=batch_task_id,
            batch_email_task_id=batch_email_task_id,
            match_job_id=match_job_id,
            match_item_id=match_item_id,
            crawler_job_id=crawler_work.job_id,
            crawler_item_id=crawler_work.work_item_id,
            imap_incremental_identity_id=incremental_identity_id,
            imap_history_identity_id=history_identity_id,
        )
        return cycle

    def _wait_for_network_outage(
        self,
        cycle: SeededWorkloadCycle,
    ) -> dict[str, object]:
        def probe() -> dict[str, object] | None:
            connection = sqlite3.connect(self.database_path, timeout=2)
            connection.row_factory = sqlite3.Row
            try:
                crawler = connection.execute(
                    """
                    SELECT status, failure_count
                    FROM crawl_page_tasks WHERE id = ?
                    """,
                    (cycle.crawler_work.work_item_id,),
                ).fetchone()
                imap_errors = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM imap_mailbox_sync_states
                        WHERE identity_id IN (?, ?) AND folder_role = 'inbox'
                              AND last_error IS NOT NULL
                        """,
                        (
                            cycle.imap_incremental_identity_id,
                            cycle.imap_history_identity_id,
                        ),
                    ).fetchone()[0]
                )
            finally:
                connection.close()
            if (
                crawler is None
                or crawler["status"] != "failed_retryable"
                or int(crawler["failure_count"] or 0) < 1
                or imap_errors != 2
            ):
                return None
            return {
                "crawler_status": crawler["status"],
                "crawler_failure_count": int(crawler["failure_count"] or 0),
                "imap_error_count": imap_errors,
            }

        return _wait_until(
            probe,
            timeout_seconds=45,
            description="HTTP and IMAP network outage persistence",
        )

    def _restore_network_services(self, cycle: SeededWorkloadCycle) -> None:
        self.http_server.start()
        self.http_online = True
        cycle.incremental_server.start()
        cycle.history_server.start()
        self.recorder.event(
            "fake_service_network_flap_recovered",
            index=cycle.index,
            services=["crawler_http", "imap_incremental", "imap_history"],
        )

    def _wait_for_cycle(self, cycle: SeededWorkloadCycle) -> dict[str, object]:
        delivery = _wait_until(
            lambda: (
                state
                if (
                    state := self.support.delivery_tests._read_delivery_state(
                        self.database_path,
                        cycle.delivery_task_id,
                    )
                )["delivery_outcome"]
                in {"smtp_accepted", "assumed_sent_after_interruption"}
                else None
            ),
            timeout_seconds=180,
            description=f"dispatcher cycle {cycle.index} terminal state",
        )
        batch = _wait_until(
            lambda: (
                state
                if (
                    state := self.support.batch_tests._read_batch_draft_state(
                        self.database_path,
                        cycle.batch_email_task_id,
                    )
                )["status"]
                == "review_required"
                else None
            ),
            timeout_seconds=180,
            description=f"batch draft cycle {cycle.index} terminal state",
        )
        match = _wait_until(
            lambda: (
                state
                if (
                    state := self.support.match_tests._read_match_state(
                        self.database_path,
                        cycle.match_job_id,
                        cycle.match_item_id,
                    )
                )["job_status"]
                == "completed"
                else None
            ),
            timeout_seconds=180,
            description=f"matching cycle {cycle.index} terminal state",
        )
        incremental = _wait_until(
            lambda: self._imap_terminal_state(cycle, workload="incremental"),
            timeout_seconds=180,
            description=f"IMAP incremental cycle {cycle.index} terminal state",
        )
        history = _wait_until(
            lambda: self._imap_terminal_state(cycle, workload="history"),
            timeout_seconds=180,
            description=f"IMAP history cycle {cycle.index} terminal state",
        )
        crawler = _wait_until(
            lambda: (
                state
                if (
                    state := self.support.crawler_tests._read_state(
                        self.database_path,
                        cycle.crawler_work,
                    )
                )["item_status"]
                == self.TERMINAL_CRAWLER_STATUSES[cycle.crawler_kind]
                and state["job_status"] == "needs_review"
                else None
            ),
            timeout_seconds=180,
            description=f"crawler cycle {cycle.index} terminal state",
        )
        smtp_delta = self.smtp_server.accepted_count - cycle.smtp_accepted_before
        llm_delta = self.llm_server.request_count - cycle.llm_requests_before
        http_delta = self.http_server.request_count - cycle.http_requests_before
        imap_fetches = (
            cycle.incremental_server.fetch_count + cycle.history_server.fetch_count
        )
        violations: list[str] = []
        if delivery["status"] != "sent":
            violations.append(f"dispatcher status={delivery['status']!r}")
        if delivery["attempt_count"] != 1 or delivery["delivery_log_count"] != 1:
            violations.append("dispatcher did not retain exactly one attempt and delivery log")
        if delivery["operation_log_count"] != 1:
            violations.append("dispatcher did not retain exactly one terminal operation log")
        if smtp_delta > 1:
            violations.append(f"dispatcher SMTP DATA was accepted {smtp_delta} times")
        if not cycle.chaos and delivery["delivery_outcome"] != "smtp_accepted":
            violations.append(
                f"normal cycle used unexpected delivery outcome {delivery['delivery_outcome']!r}"
            )
        if delivery["delivery_outcome"] == "smtp_accepted" and smtp_delta != 1:
            violations.append(
                f"smtp_accepted outcome has SMTP DATA delta {smtp_delta}, expected 1"
            )
        if (
            batch["draft_claim_id"] is not None
            or not batch["generated_subject"]
            or not batch["generated_content_text"]
            or batch["draft_log_count"] != 1
            or batch["operation_log_count"] != 1
        ):
            violations.append("batch draft terminal state is incomplete or duplicated")
        if (
            match["item_status"] != "succeeded"
            or match["succeeded_count"] != 1
            or match["failed_count"] != 0
            or match["canonical_count"] != 1
            or match["running_run_count"] != 0
            or match["completion_log_count"] != 1
        ):
            violations.append("matching terminal state is incomplete or duplicated")
        for name, state in (("incremental", incremental), ("history", history)):
            imap_violations: list[str] = []
            if state["email_log_count"] != 1:
                imap_violations.append(f"email_log_count={state['email_log_count']!r}")
            if state["distinct_imap_location_count"] != 1:
                imap_violations.append(
                    "distinct_imap_location_count="
                    f"{state['distinct_imap_location_count']!r}"
                )
            if state["identity_claim_id"] is not None:
                imap_violations.append("identity claim was not released")
            if name == "history" and (
                state["history_claim_id"] is not None
                or state["history_lease_expires_at"] is not None
            ):
                imap_violations.append("history claim was not released")
            if imap_violations:
                violations.append(
                    f"IMAP {name} terminal state is incomplete or duplicated: "
                    + ", ".join(imap_violations)
                )
        if (
            crawler["worker_id"] is not None
            or crawler["lease_expires_at"] is not None
            or crawler["candidate_count"] != 1
            or crawler["canonical_candidate_count"] != 1
            or crawler["processing_count"] != 0
            or crawler["duplicate_identity_keys"] != 0
        ):
            violations.append("crawler terminal state is incomplete or duplicated")
        if llm_delta < 3:
            violations.append(f"only {llm_delta} fake LLM requests covered the cycle")
        expected_http_minimum = 0 if cycle.crawler_kind == "chunk" else 1
        if http_delta < expected_http_minimum:
            violations.append(
                f"crawler {cycle.crawler_kind} issued only {http_delta} fake HTTP requests"
            )
        if cycle.incremental_server.fetch_count < 1 or cycle.history_server.fetch_count < 1:
            violations.append("one or both IMAP workloads issued no FETCH command")
        if violations:
            raise QaFailure(
                f"workload cycle {cycle.index} violated terminal invariants: "
                + "; ".join(violations)
            )
        return {
            "chaos": cycle.chaos,
            "network_flap": cycle.network_flap,
            "crawler_kind": cycle.crawler_kind,
            "delivery_outcome": delivery["delivery_outcome"],
            "smtp_data_delta": smtp_delta,
            "llm_request_delta": llm_delta,
            "http_request_delta": http_delta,
            "imap_fetches": imap_fetches,
            "crawler_attempt_count": crawler["attempt_count"],
            "crawler_failure_count": crawler["failure_count"],
        }

    def _imap_terminal_state(
        self,
        cycle: SeededWorkloadCycle,
        *,
        workload: Literal["incremental", "history"],
    ) -> dict[str, Any] | None:
        if workload == "incremental":
            identity_id = cycle.imap_incremental_identity_id
            professor_id = cycle.imap_incremental_professor_id
        else:
            identity_id = cycle.imap_history_identity_id
            professor_id = cycle.imap_history_professor_id
        state = self.support.imap_tests._read_state(
            self.database_path,
            identity_id=identity_id,
            professor_id=professor_id,
        )
        if state["email_log_count"] != 1:
            return None
        # The mailbox cursor and received log commit before the outer identity
        # lease is released.  Treating that intermediate state as terminal makes
        # the invariant check race the Worker, especially after Windows network
        # recovery.  Wait for the lease release while still reporting duplicate
        # logs/locations as a real terminal violation below.
        if state["identity_claim_id"] is not None:
            return None
        if workload == "incremental":
            return state if state["incremental_cursor"] == 11 else None
        if state["history_status"] == "completed" and state["history_cursor"] == 11:
            return state
        return None

    def _disable_completed_imap_profiles(self, cycle: SeededWorkloadCycle) -> None:
        connection = sqlite3.connect(self.database_path, timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                UPDATE identity_profiles
                SET imap_host = NULL, imap_port = NULL,
                    imap_username = NULL, imap_password = NULL
                WHERE id IN (?, ?)
                """,
                (
                    cycle.imap_incremental_identity_id,
                    cycle.imap_history_identity_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _profile_html(marker: str) -> str:
        detail = "可靠分布式系统、数据库并发控制与软件工程。" * 30
        return (
            "<!doctype html><html><head><title>张三教授</title></head><body>"
            "<main><h1>张三</h1><p>教授，计算机系</p>"
            "<p>邮箱：zhang@example.edu</p>"
            f"<p data-marker='{marker}'>PROFILE_VERSION {marker}。{detail}</p>"
            "<p>代表论文：Deterministic Systems 2026</p></main>"
            "</body></html>"
        )

    @staticmethod
    def _llm_response(request_number: int, payload: dict[str, Any]) -> str:
        prompt = json.dumps(payload, ensure_ascii=False)
        candidate = {
            "name": "张三",
            "email": "zhang@example.edu",
            "title": "教授",
            "university": "示例大学",
            "school": "计算机学院",
            "department": "计算机系",
            "research_direction": "可靠分布式系统",
            "recent_papers": ["Deterministic Systems 2026"],
            "profile_url": WorkloadHarness._extract_profile_url(prompt),
            "source_url": WorkloadHarness._extract_profile_url(prompt),
            "confidence": 0.95,
            "field_confidence": [],
            "evidence_summary": "本地确定性 packaged QA 页面",
        }
        if "V2 详情页整页抽取 Worker" in prompt:
            return json.dumps(
                {"status": "candidate", "candidate": candidate},
                ensure_ascii=False,
            )
        if "V2 Chunk Worker" in prompt:
            return json.dumps(
                {"candidate_count": 1, "candidates": [candidate]},
                ensure_ascii=False,
            )
        if "你正在补全已发现的导师候选详情" in prompt:
            return json.dumps(
                {
                    "email": "zhang@example.edu",
                    "title": "教授",
                    "department": "计算机系",
                    "research_direction": "可靠分布式系统",
                    "recent_papers": ["Deterministic Systems 2026"],
                },
                ensure_ascii=False,
            )
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
        if "只回复 OK" in prompt:
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

    @staticmethod
    def _extract_profile_url(prompt: str) -> str:
        match = re.search(
            rf"http://{re.escape(QA_CRAWL_HOST)}:\d+/profile/[A-Za-z0-9-]+",
            prompt,
        )
        return match.group(0) if match is not None else f"http://{QA_CRAWL_HOST}/profile"


class PackagedApplication:
    def __init__(
        self,
        *,
        executable: Path,
        paths: QaPaths,
        recorder: EvidenceRecorder,
        extra_environment: dict[str, str],
    ) -> None:
        self.executable = executable.resolve()
        self.paths = paths
        self.recorder = recorder
        self.extra_environment = dict(extra_environment)
        self.handle: LaunchHandle | None = None
        self.identity: RuntimeIdentity | None = None
        self.launch_count = 0

    def launch(
        self,
        *,
        mode: Literal["split", "combined"],
        wait_ready: bool = True,
        previous_runtime_id: str | None = None,
    ) -> RuntimeIdentity | None:
        if self.handle is not None and self.handle.process.poll() is None:
            raise QaFailure("packaged application is already running")
        self.launch_count += 1
        nonce = f"qa_{uuid.uuid4().hex}"
        _authorize_user_data(self.paths.user_data, nonce)
        environment = os.environ.copy()
        environment.update(self.extra_environment)
        environment.update(
            {
                QA_ENABLE_ENV: QA_ENABLE_VALUE,
                QA_NONCE_ENV: nonce,
                QA_USER_DATA_ENV: str(self.paths.user_data),
                "AUTO_EMAIL_SENDER_BACKEND_MODE": mode,
            }
        )
        stdout_path = self.paths.logs_dir / f"desktop-{self.launch_count}-{mode}.stdout.log"
        stderr_path = self.paths.logs_dir / f"desktop-{self.launch_count}-{mode}.stderr.log"
        stdout_file = stdout_path.open("wb")
        stderr_file = stderr_path.open("wb")
        command = [
            str(self.executable),
            f"--auto-email-sender-packaged-qa={nonce}",
        ]
        launched_at_wall = time.time()
        try:
            process = subprocess.Popen(
                command,
                cwd=self.executable.parent,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
            )
        except Exception:
            stdout_file.close()
            stderr_file.close()
            raise
        self.handle = LaunchHandle(
            process=process,
            stdout_file=stdout_file,
            stderr_file=stderr_file,
            nonce=nonce,
            mode=mode,
            launched_at_monotonic=time.monotonic(),
            launched_at_wall=launched_at_wall,
        )
        self.identity = None
        self.recorder.event(
            "desktop_launched",
            pid=process.pid,
            mode=mode,
            executable=str(self.executable),
        )
        if not wait_ready:
            return None
        identity = self.wait_ready(previous_runtime_id=previous_runtime_id)
        self.identity = identity
        return identity

    def wait_ready(
        self,
        *,
        timeout_seconds: float = 180,
        previous_runtime_id: str | None = None,
    ) -> RuntimeIdentity:
        handle = self._require_handle()

        def probe() -> RuntimeIdentity | None:
            return _probe_ready_identity(
                descriptor_path=self.paths.user_data / RUNTIME_DESCRIPTOR_RELATIVE_PATH,
                expected_mode=handle.mode,
                launched_pid=handle.process.pid,
                launched_at_wall=handle.launched_at_wall,
                previous_runtime_id=previous_runtime_id,
            )

        identity = _wait_until(
            probe,
            timeout_seconds=timeout_seconds,
            description=f"packaged {handle.mode} runtime ready",
            process=handle.process,
            stderr_path=self.paths.logs_dir
            / f"desktop-{self.launch_count}-{handle.mode}.stderr.log",
        )
        self.identity = identity
        self.recorder.event("runtime_ready", mode=handle.mode, identity=identity.evidence_payload())
        return identity

    def wait_for_replacement(
        self,
        *,
        previous: RuntimeIdentity,
        replace_group: bool,
        timeout_seconds: float = 120,
    ) -> RuntimeIdentity:
        handle = self._require_handle()

        def probe() -> RuntimeIdentity | None:
            identity = _probe_ready_identity(
                descriptor_path=self.paths.user_data / RUNTIME_DESCRIPTOR_RELATIVE_PATH,
                expected_mode=handle.mode,
                launched_pid=handle.process.pid,
                launched_at_wall=handle.launched_at_wall,
                previous_runtime_id=(previous.runtime_id if replace_group else None),
            )
            if identity is None:
                return None
            if replace_group:
                if identity.runtime_id == previous.runtime_id:
                    return None
                if identity.backend.pid == previous.backend.pid:
                    return None
                if previous.worker is not None and _pid_is_running(previous.worker.pid):
                    return None
            else:
                if identity.runtime_id != previous.runtime_id:
                    raise QaFailure("Worker-only recovery unexpectedly replaced the runtime group")
                if identity.backend.pid != previous.backend.pid:
                    raise QaFailure("Worker-only recovery unexpectedly replaced the API")
                if (
                    identity.worker is None
                    or previous.worker is None
                    or identity.worker.pid == previous.worker.pid
                ):
                    return None
            return identity

        identity = _wait_until(
            probe,
            timeout_seconds=timeout_seconds,
            description="runtime replacement",
            process=handle.process,
        )
        self.identity = identity
        self.recorder.event(
            "runtime_replaced",
            replace_group=replace_group,
            previous=previous.evidence_payload(),
            current=identity.evidence_payload(),
        )
        return identity

    def probe_ready(self, *, timeout_seconds: float = 30) -> RuntimeIdentity:
        """Refresh the current identity without emitting a startup event."""

        handle = self._require_handle()

        def probe() -> RuntimeIdentity | None:
            return _probe_ready_identity(
                descriptor_path=self.paths.user_data / RUNTIME_DESCRIPTOR_RELATIVE_PATH,
                expected_mode=handle.mode,
                launched_pid=handle.process.pid,
                launched_at_wall=handle.launched_at_wall,
                previous_runtime_id=None,
            )

        identity = _wait_until(
            probe,
            timeout_seconds=timeout_seconds,
            description="current packaged runtime health",
            process=handle.process,
        )
        self.identity = identity
        return identity

    def graceful_stop(self, timeout_seconds: float = 20) -> set[int]:
        handle = self._require_handle()
        captured = _runtime_process_tree_pids(self.identity, handle.process.pid)
        self.recorder.event("desktop_graceful_stop_requested", pid=handle.process.pid)
        _request_desktop_stop(handle.process.pid)
        try:
            handle.process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            raise QaFailure("desktop did not exit after a graceful stop request")
        _wait_for_pids_gone(captured, timeout_seconds=15)
        self.recorder.event("desktop_stopped", pid=handle.process.pid, captured_pids=sorted(captured))
        self._close_handle()
        return captured

    def kill_desktop_only(self, timeout_seconds: float = 20) -> set[int]:
        handle = self._require_handle()
        captured = _runtime_process_tree_pids(self.identity, handle.process.pid)
        self.recorder.event("desktop_force_kill", pid=handle.process.pid, captured_pids=sorted(captured))
        psutil.Process(handle.process.pid).kill()
        try:
            handle.process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise QaFailure("force-killed desktop process did not exit") from exc
        _wait_for_pids_gone(captured, timeout_seconds=15)
        self._close_handle()
        return captured

    def cleanup(self) -> None:
        handle = self.handle
        if handle is None:
            return
        pids = _runtime_process_tree_pids(self.identity, handle.process.pid)
        _kill_pids(pids)
        with contextlib.suppress(subprocess.TimeoutExpired):
            handle.process.wait(timeout=10)
        self._close_handle()

    def _close_handle(self) -> None:
        handle = self.handle
        self.handle = None
        self.identity = None
        if handle is not None:
            handle.close_logs()

    def _require_handle(self) -> LaunchHandle:
        if self.handle is None:
            raise QaFailure("packaged application has not been launched")
        return self.handle


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Certify a real packaged Auto Email Sender runtime.",
    )
    parser.add_argument(
        "--scenario",
        choices=("lifecycle", "normal-soak", "seeded-chaos"),
        required=True,
    )
    parser.add_argument("--app-executable", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--package-file", type=Path)
    parser.add_argument("--previous-package-file", type=Path)
    parser.add_argument("--candidate-manifest-file", type=Path)
    parser.add_argument("--expected-candidate-run-id", type=int)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument("--sample-interval-seconds", type=float)
    parser.add_argument("--action-interval-seconds", type=float)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--expected-revision", default="")
    parser.add_argument("--expected-app-version", default="")
    parser.add_argument("--expected-package-sha256", default="")
    parser.add_argument("--repository-root", type=Path)
    parser.add_argument("--certification", action="store_true")
    parser.add_argument("--prerelease-certification", action="store_true")
    parser.add_argument("--candidate-admission", action="store_true")
    parser.add_argument("--harness-rehearsal", action="store_true")
    parser.add_argument("--development-smoke", action="store_true")
    parser.add_argument("--skip-browser-probe", action="store_true")
    parser.add_argument("--system-sleep-wake", action="store_true")
    parser.add_argument("--existing-user-data", type=Path)
    parser.add_argument("--upgrade-manifest", type=Path)
    parser.add_argument("--expected-previous-version", default="")
    parser.add_argument("--expected-previous-package-sha256", default="")
    args = parser.parse_args(argv)
    _validate_args(args, parser)
    return args


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    selected_tiers = sum(
        (
            args.certification,
            args.prerelease_certification,
            args.candidate_admission,
            args.harness_rehearsal,
            args.development_smoke,
        )
    )
    if selected_tiers != 1:
        parser.error(
            "select exactly one of --certification, "
            "--prerelease-certification, --candidate-admission, "
            "--harness-rehearsal, or --development-smoke"
        )
    formal_certification = args.certification or args.prerelease_certification
    packaged_preflight = args.candidate_admission or args.harness_rehearsal
    executable = args.app_executable.expanduser().resolve()
    if not executable.is_file():
        parser.error(f"packaged app executable is missing: {executable}")
    args.app_executable = executable
    artifact_root = (
        executable if args.artifact_root is None else args.artifact_root.expanduser().resolve()
    )
    if not artifact_root.exists():
        parser.error(f"packaged artifact root is missing: {artifact_root}")
    try:
        executable.relative_to(artifact_root if artifact_root.is_dir() else artifact_root.parent)
    except ValueError:
        parser.error("app executable must be inside --artifact-root")
    args.artifact_root = artifact_root
    for attribute in ("package_file", "previous_package_file"):
        value = getattr(args, attribute)
        if value is None:
            continue
        package_path = value.expanduser().resolve()
        if not package_path.is_file():
            parser.error(f"{attribute.replace('_', '-')} is missing: {package_path}")
        setattr(args, attribute, package_path)
    if (args.candidate_manifest_file is None) != (
        args.expected_candidate_run_id is None
    ):
        parser.error(
            "--candidate-manifest-file and --expected-candidate-run-id "
            "must be provided together"
        )
    if args.candidate_manifest_file is not None:
        candidate_manifest = args.candidate_manifest_file.expanduser().resolve()
        if not candidate_manifest.is_file():
            parser.error(f"candidate manifest is missing: {candidate_manifest}")
        if args.expected_candidate_run_id is None or args.expected_candidate_run_id <= 0:
            parser.error("--expected-candidate-run-id must be a positive integer")
        args.candidate_manifest_file = candidate_manifest
    if args.sample_interval_seconds is None:
        args.sample_interval_seconds = (
            PRERELEASE_SAMPLE_INTERVAL_SECONDS
            if args.prerelease_certification
            else 30.0
        )
    if args.action_interval_seconds is None:
        args.action_interval_seconds = (
            PRERELEASE_ACTION_INTERVAL_SECONDS
            if args.prerelease_certification
            else 60.0
        )
    if args.sample_interval_seconds <= 0 or args.action_interval_seconds <= 0:
        parser.error("sample and action intervals must be positive")
    if (
        args.prerelease_certification
        and args.sample_interval_seconds > PRERELEASE_SAMPLE_INTERVAL_SECONDS
    ):
        parser.error("prerelease certification samples must be at most 10 seconds apart")
    if (
        args.prerelease_certification
        and args.action_interval_seconds > PRERELEASE_ACTION_INTERVAL_SECONDS
    ):
        parser.error("prerelease certification actions must be at most 5 seconds apart")
    default_minimums = (
        PRERELEASE_CERTIFICATION_MINIMUM_SECONDS
        if args.prerelease_certification
        else CERTIFICATION_MINIMUM_SECONDS
    )
    default_duration = {
        "lifecycle": 0.0,
        "normal-soak": float(default_minimums["normal-soak"]),
        "seeded-chaos": float(default_minimums["seeded-chaos"]),
    }[args.scenario]
    args.duration_seconds = (
        default_duration if args.duration_seconds is None else args.duration_seconds
    )
    if args.duration_seconds < 0:
        parser.error("duration must not be negative")
    if args.scenario == "seeded-chaos" and args.seed is None:
        parser.error("seeded-chaos requires --seed")
    if packaged_preflight and args.scenario != "lifecycle":
        parser.error("candidate admission and harness rehearsal only support lifecycle")
    args.expected_app_version = args.expected_app_version.strip()
    args.expected_package_sha256 = args.expected_package_sha256.strip().lower()
    args.expected_previous_package_sha256 = (
        args.expected_previous_package_sha256.strip().lower()
    )
    if args.repository_root is not None:
        repository_root = args.repository_root.expanduser().resolve()
        if not repository_root.is_dir():
            parser.error(f"repository root is missing: {repository_root}")
        args.repository_root = repository_root
    if (args.existing_user_data is None) != (args.upgrade_manifest is None):
        parser.error("--existing-user-data and --upgrade-manifest must be provided together")
    if args.existing_user_data is not None:
        if args.repository_root is None:
            parser.error("upgrade verification requires --repository-root")
        if not args.expected_previous_version.strip():
            parser.error("upgrade verification requires --expected-previous-version")
        if args.previous_package_file is None:
            parser.error("upgrade verification requires --previous-package-file")
        if not re.fullmatch(
            r"[0-9a-f]{64}",
            args.expected_previous_package_sha256,
        ):
            parser.error(
                "upgrade verification requires a 64-character "
                "--expected-previous-package-sha256"
            )
        args.expected_previous_version = args.expected_previous_version.strip()
        if args.scenario != "lifecycle":
            parser.error("existing upgrade data is only valid for the lifecycle scenario")
        try:
            args.existing_user_data = _resolve_existing_qa_user_data(
                args.existing_user_data
            )
        except QaFailure as exc:
            parser.error(str(exc))
        manifest = args.upgrade_manifest.expanduser().resolve()
        if not manifest.is_file():
            parser.error(f"upgrade manifest is missing: {manifest}")
        args.upgrade_manifest = manifest
    elif args.expected_previous_version.strip():
        parser.error("--expected-previous-version requires an upgrade manifest")
    elif args.previous_package_file is not None:
        parser.error("--previous-package-file requires an upgrade manifest")
    elif args.expected_previous_package_sha256:
        parser.error("--expected-previous-package-sha256 requires an upgrade manifest")
    if formal_certification:
        if args.skip_browser_probe:
            parser.error("certification cannot skip the real browser process probe")
        minimums = (
            PRERELEASE_CERTIFICATION_MINIMUM_SECONDS
            if args.prerelease_certification
            else CERTIFICATION_MINIMUM_SECONDS
        )
        minimum = minimums.get(args.scenario)
        if minimum is not None and args.duration_seconds < minimum:
            label = "prerelease certification" if args.prerelease_certification else "certification"
            parser.error(
                f"{args.scenario} {label} requires at least {minimum} seconds"
            )
        revision = args.expected_revision.lower()
        if not REVISION_PATTERN.fullmatch(revision):
            parser.error("certification requires a 40-character --expected-revision")
        args.expected_revision = revision
        if args.repository_root is None:
            parser.error("certification requires --repository-root")
        if args.package_file is None:
            parser.error("certification requires --package-file")
        if not args.expected_app_version:
            parser.error("certification requires --expected-app-version")
        prerelease_version = re.fullmatch(
            r"\d+\.\d+\.\d+-(alpha|beta|rc)\.[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*",
            args.expected_app_version,
        )
        if args.prerelease_certification and prerelease_version is None:
            parser.error("prerelease certification requires an alpha, beta, or rc app version")
        if args.certification and prerelease_version is not None:
            parser.error("stable certification does not accept a prerelease app version")
        if not re.fullmatch(r"[0-9a-f]{64}", args.expected_package_sha256):
            parser.error("certification requires a 64-character --expected-package-sha256")
        if args.candidate_manifest_file is None:
            parser.error(
                "certification requires --candidate-manifest-file and "
                "--expected-candidate-run-id"
            )
        if args.scenario in {"lifecycle", "seeded-chaos"} and not args.system_sleep_wake:
            parser.error(
                f"{args.scenario} certification requires --system-sleep-wake"
            )
        if args.scenario == "lifecycle" and args.upgrade_manifest is None:
            parser.error(
                "lifecycle certification requires a previous-stable "
                "--existing-user-data and --upgrade-manifest"
            )
    if packaged_preflight:
        if args.skip_browser_probe:
            parser.error("packaged preflight cannot skip the real browser process probe")
        if args.package_file is None:
            parser.error("packaged preflight requires --package-file")
        if not re.fullmatch(r"[0-9a-f]{64}", args.expected_package_sha256):
            parser.error(
                "packaged preflight requires a 64-character "
                "--expected-package-sha256"
            )
        if args.upgrade_manifest is None:
            parser.error(
                "packaged preflight requires previous-stable upgrade data"
            )
    if args.harness_rehearsal and args.candidate_manifest_file is not None:
        parser.error(
            "harness rehearsal must not bind an invalidated candidate manifest; "
            "use --candidate-admission for exact candidate evidence"
        )
    if args.candidate_admission:
        revision = args.expected_revision.lower()
        if not REVISION_PATTERN.fullmatch(revision):
            parser.error(
                "candidate admission requires a 40-character --expected-revision"
            )
        args.expected_revision = revision
        if args.repository_root is None:
            parser.error("candidate admission requires --repository-root")
        if not args.expected_app_version:
            parser.error("candidate admission requires --expected-app-version")
        if args.candidate_manifest_file is None:
            parser.error(
                "candidate admission requires --candidate-manifest-file and "
                "--expected-candidate-run-id"
            )
        if not args.system_sleep_wake:
            parser.error("candidate admission requires --system-sleep-wake")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    formal_certification = args.certification or args.prerelease_certification
    certification_tier = (
        "stable"
        if args.certification
        else "prerelease"
        if args.prerelease_certification
        else "candidate-admission"
        if args.candidate_admission
        else "harness-rehearsal"
        if args.harness_rehearsal
        else "development"
    )
    paths = _create_paths(
        args.artifacts_dir,
        existing_user_data=args.existing_user_data,
    )
    artifact_sha256 = _sha256_file(args.app_executable)
    artifact_tree = _sha256_tree(args.artifact_root)
    package_sha256 = _sha256_file(args.package_file) if args.package_file else None
    previous_package_sha256 = (
        _sha256_file(args.previous_package_file) if args.previous_package_file else None
    )
    candidate_manifest_sha256 = (
        _sha256_file(args.candidate_manifest_file)
        if args.candidate_manifest_file
        else None
    )
    report: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "scenario": args.scenario,
        "status": "running",
        "certification_requested": formal_certification,
        "certification_tier": certification_tier,
        "certification_eligible": False,
        "evidence_purpose": (
            "formal-certification"
            if formal_certification
            else "non-certifying-candidate-admission"
            if args.candidate_admission
            else "non-certifying-harness-rehearsal"
            if args.harness_rehearsal
            else "development-smoke"
        ),
        "started_at": _utc_now(),
        "finished_at": None,
        "duration_seconds_requested": args.duration_seconds,
        "duration_seconds_observed": None,
        "sample_interval_seconds": args.sample_interval_seconds,
        "action_interval_seconds": args.action_interval_seconds,
        "seed": args.seed,
        "system_sleep_wake_requested": args.system_sleep_wake,
        "expected_revision": args.expected_revision or None,
        "expected_app_version": args.expected_app_version or None,
        "expected_package_sha256": args.expected_package_sha256 or None,
        "candidate_manifest_file": (
            str(args.candidate_manifest_file) if args.candidate_manifest_file else None
        ),
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "expected_candidate_run_id": args.expected_candidate_run_id,
        "app_executable": str(args.app_executable),
        "app_executable_sha256": artifact_sha256,
        "artifact_root": str(args.artifact_root),
        "artifact_tree_sha256": artifact_tree["sha256"],
        "artifact_file_count": artifact_tree["file_count"],
        "artifact_bytes": artifact_tree["bytes"],
        "package_file": str(args.package_file) if args.package_file else None,
        "package_sha256": package_sha256,
        "previous_package_file": (
            str(args.previous_package_file) if args.previous_package_file else None
        ),
        "previous_package_sha256": previous_package_sha256,
        "run_root": str(paths.run_root),
        "user_data_path": str(paths.user_data),
        "upgrade_manifest": str(args.upgrade_manifest) if args.upgrade_manifest else None,
        "expected_previous_version": args.expected_previous_version or None,
        "expected_previous_package_sha256": (
            args.expected_previous_package_sha256 or None
        ),
        "host": {
            "platform": sys.platform,
            "platform_release": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "psutil": psutil.__version__,
        },
        "checks": [],
        "events": {"trace": str(paths.trace_path)},
        "resource_samples": str(paths.samples_path),
        "database_audits": [],
        "resource_summary": None,
        "failure": None,
    }
    recorder = EvidenceRecorder(paths, report)
    start = time.monotonic()
    recorder.write_report()
    try:
        if args.candidate_manifest_file is not None:
            candidate_evidence = _verify_candidate_asset_manifest(
                args.candidate_manifest_file,
                package_file=args.package_file,
                expected_package_sha256=args.expected_package_sha256,
                expected_revision=args.expected_revision,
                expected_app_version=args.expected_app_version,
                expected_run_id=args.expected_candidate_run_id,
            )
            expected_candidate_kind = (
                "auto-email-sender-prerelease-candidate"
                if args.prerelease_certification
                else "auto-email-sender-release-candidate"
                if args.certification
                else None
            )
            if (
                expected_candidate_kind is not None
                and candidate_evidence["candidate_kind"] != expected_candidate_kind
            ):
                raise QaFailure(
                    f"{certification_tier} certification received the wrong candidate kind"
                )
            recorder.check(
                "candidate_manifest_binds_package_revision_and_run",
                passed=True,
                evidence=candidate_evidence,
            )
        if args.expected_package_sha256:
            recorder.check(
                "package_digest_matches_expected_candidate",
                passed=package_sha256 == args.expected_package_sha256,
                evidence={
                    "expected_sha256": args.expected_package_sha256,
                    "actual_sha256": package_sha256,
                },
            )
        if args.expected_previous_package_sha256:
            recorder.check(
                "previous_package_digest_matches_expected_stable_asset",
                passed=(
                    previous_package_sha256
                    == args.expected_previous_package_sha256
                ),
                evidence={
                    "expected_sha256": args.expected_previous_package_sha256,
                    "actual_sha256": previous_package_sha256,
                },
            )
        if formal_certification or args.candidate_admission:
            _assert_clean_revision(
                args.repository_root.resolve(),
                args.expected_revision,
            )
            recorder.check(
                (
                    "clean_committed_revision"
                    if formal_certification
                    else "exact_candidate_clean_committed_revision"
                ),
                passed=True,
                evidence={"revision": args.expected_revision},
            )
        if args.scenario == "lifecycle":
            _run_lifecycle(
                args,
                paths,
                recorder,
                previous_package_sha256=previous_package_sha256,
            )
        elif args.scenario == "normal-soak":
            _run_normal_soak(args, paths, recorder)
        else:
            _run_seeded_chaos(args, paths, recorder)
        artifact_tree_after = _sha256_tree(args.artifact_root)
        package_sha256_after = (
            _sha256_file(args.package_file) if args.package_file else None
        )
        previous_package_sha256_after = (
            _sha256_file(args.previous_package_file)
            if args.previous_package_file
            else None
        )
        candidate_manifest_sha256_after = (
            _sha256_file(args.candidate_manifest_file)
            if args.candidate_manifest_file
            else None
        )
        recorder.check(
            "packaged_artifact_immutable_during_scenario",
            passed=(
                artifact_tree_after["sha256"] == artifact_tree["sha256"]
                and package_sha256_after == package_sha256
                and previous_package_sha256_after == previous_package_sha256
                and candidate_manifest_sha256_after == candidate_manifest_sha256
            ),
            evidence={
                "artifact_tree_sha256_before": artifact_tree["sha256"],
                "artifact_tree_sha256_after": artifact_tree_after["sha256"],
                "package_sha256_before": package_sha256,
                "package_sha256_after": package_sha256_after,
                "previous_package_sha256_before": previous_package_sha256,
                "previous_package_sha256_after": previous_package_sha256_after,
                "candidate_manifest_sha256_before": candidate_manifest_sha256,
                "candidate_manifest_sha256_after": candidate_manifest_sha256_after,
            },
        )
        report["status"] = "passed"
        report["certification_eligible"] = formal_certification
        return_code = 0
    except BaseException as exc:  # noqa: BLE001 - always persist interrupted QA evidence
        report["status"] = "failed"
        report["certification_eligible"] = False
        report["failure"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        recorder.event("qa_failed", error_type=type(exc).__name__, message=str(exc))
        return_code = 1
    finally:
        report["finished_at"] = _utc_now()
        report["duration_seconds_observed"] = time.monotonic() - start
        recorder.write_report()
        print(f"PACKAGED_QA_REPORT={paths.report_path}", flush=True)
    return return_code


def _verify_packaged_diagnostics_export(
    export_path: Path,
    *,
    forbidden_values: tuple[str, ...],
) -> dict[str, object]:
    required_entries = {
        "manifest.json",
        "summary.json",
        "checksums.sha256",
        "README.txt",
    }

    def probe() -> dict[str, object] | None:
        if not export_path.is_file():
            return None
        try:
            with zipfile.ZipFile(export_path) as archive:
                corrupt_entry = archive.testzip()
                if corrupt_entry is not None:
                    raise QaFailure(
                        f"packaged diagnostics ZIP has a corrupt entry: {corrupt_entry}"
                    )
                entries = archive.infolist()
                names = {entry.filename for entry in entries}
                missing = required_entries - names
                if missing:
                    raise QaFailure(
                        "packaged diagnostics ZIP is missing entries: "
                        f"{sorted(missing)}"
                    )
                total_uncompressed_bytes = sum(entry.file_size for entry in entries)
                if total_uncompressed_bytes > 80 * 1024 * 1024:
                    raise QaFailure("packaged diagnostics ZIP exceeds its bounded size")
                contents = b"".join(archive.read(entry) for entry in entries)
                manifest = json.loads(
                    archive.read("manifest.json").decode("utf-8")
                )
        except (OSError, zipfile.BadZipFile):
            return None
        for value in forbidden_values:
            if value and value.encode("utf-8") in contents:
                raise QaFailure("packaged diagnostics ZIP leaked a QA runtime credential")
        return {
            "path": str(export_path),
            "sha256": _sha256_file(export_path),
            "archive_bytes": export_path.stat().st_size,
            "uncompressed_bytes": total_uncompressed_bytes,
            "entry_count": len(entries),
            "report_id": manifest.get("report_id"),
        }

    return _wait_until(
        probe,
        timeout_seconds=90,
        description="packaged Beta diagnostics ZIP export",
    )


def _run_lifecycle(
    args: argparse.Namespace,
    paths: QaPaths,
    recorder: EvidenceRecorder,
    *,
    previous_package_sha256: str | None,
) -> None:
    environment = _qa_backend_environment(paths)
    diagnostics_probe_required = args.candidate_admission
    if diagnostics_probe_required:
        environment[QA_DIAGNOSTICS_EXPORT_ENV] = QA_DIAGNOSTICS_EXPORT_VALUE
    application = PackagedApplication(
        executable=args.app_executable,
        paths=paths,
        recorder=recorder,
        extra_environment=environment,
    )
    browser_probe: BrowserProbe | None = None
    lifecycle_started = time.monotonic()
    try:
        first = application.launch(mode="split")
        assert first is not None
        _validate_expected_app_version(first, args.expected_app_version)
        _validate_runtime_roles(first, paths.user_data, expected_mode="split")
        recorder.check(
            "split_runtime_identity",
            passed=True,
            evidence=first.evidence_payload(),
        )
        if diagnostics_probe_required:
            diagnostics_evidence = _verify_packaged_diagnostics_export(
                paths.user_data / QA_DIAGNOSTICS_EXPORT_NAME,
                forbidden_values=(first.access_token, application._require_handle().nonce),
            )
            recorder.check(
                "packaged_beta_diagnostics_export",
                passed=True,
                evidence=diagnostics_evidence,
            )
            application.extra_environment.pop(QA_DIAGNOSTICS_EXPORT_ENV, None)
        _assert_worker_has_no_listener(first)
        recorder.check("worker_has_no_listening_socket", passed=True)
        recorder.sample(_collect_resource_sample(first, paths.user_data, lifecycle_started))

        if args.upgrade_manifest is not None:
            upgrade_evidence = _verify_upgrade_manifest(
                args.upgrade_manifest,
                first,
                paths.user_data,
                repository_root=args.repository_root,
                expected_previous_version=args.expected_previous_version,
                expected_previous_package_sha256=previous_package_sha256,
                require_repository_head=not args.harness_rehearsal,
            )
            recorder.check(
                "previous_stable_in_place_upgrade",
                passed=True,
                evidence=upgrade_evidence,
            )

        write_evidence = _exercise_api_read_write(first, marker="lifecycle-first")
        recorder.check("authenticated_api_read_write", passed=True, evidence=write_evidence)

        if args.system_sleep_wake:
            _exercise_system_sleep_wake(
                application=application,
                paths=paths,
                recorder=recorder,
                marker="lifecycle",
            )

        _exercise_second_instance(application, first, recorder)

        assert first.worker is not None
        psutil.Process(first.worker.pid).kill()
        _wait_for_pids_gone({first.worker.pid}, timeout_seconds=10)
        _exercise_api_read_write(first, marker="worker-degraded-api-write")
        after_worker = application.wait_for_replacement(
            previous=first,
            replace_group=False,
        )
        recorder.check(
            "worker_only_restart",
            passed=True,
            evidence=after_worker.evidence_payload(),
        )
        recorder.sample(
            _collect_resource_sample(after_worker, paths.user_data, lifecycle_started)
        )
        _assert_database_audit(paths, recorder, phase="after-worker-restart")

        psutil.Process(after_worker.backend.pid).kill()
        _wait_for_pids_gone({after_worker.backend.pid}, timeout_seconds=10)
        after_api = application.wait_for_replacement(
            previous=after_worker,
            replace_group=True,
        )
        recorder.check(
            "whole_group_restart_after_api_exit",
            passed=True,
            evidence=after_api.evidence_payload(),
        )
        recorder.sample(_collect_resource_sample(after_api, paths.user_data, lifecycle_started))
        _assert_database_audit(paths, recorder, phase="after-api-restart")

        if not args.skip_browser_probe:
            browser_probe = _start_browser_probe()
            browser_pids = _exercise_real_browser_descendant(
                after_api,
                browser_probe,
                recorder,
            )
            captured = application.kill_desktop_only()
            recorder.check(
                "electron_api_worker_playwright_tree_cleanup",
                passed=browser_pids.issubset(captured),
                evidence={
                    "browser_pids": sorted(browser_pids),
                    "captured_pids": sorted(captured),
                },
            )
            browser_probe.stop()
            browser_probe = None
        else:
            application.kill_desktop_only()

        restarted = application.launch(
            mode="split",
            previous_runtime_id=after_api.runtime_id,
        )
        assert restarted is not None
        recorder.check(
            "restart_after_force_kill_releases_locks",
            passed=True,
            evidence=restarted.evidence_payload(),
        )
        recorder.sample(_collect_resource_sample(restarted, paths.user_data, lifecycle_started))
        _assert_database_audit(paths, recorder, phase="restart-after-force-kill")
        application.graceful_stop()

        combined = application.launch(mode="combined")
        assert combined is not None
        _validate_runtime_roles(combined, paths.user_data, expected_mode="combined")
        _exercise_api_read_write(combined, marker="combined-fallback")
        recorder.check(
            "combined_fallback_same_database",
            passed=combined.worker is None,
            evidence=combined.evidence_payload(),
        )
        recorder.sample(_collect_resource_sample(combined, paths.user_data, lifecycle_started))
        _assert_database_audit(paths, recorder, phase="combined-fallback")
        application.graceful_stop()

        application.launch(mode="split", wait_ready=False)
        time.sleep(0.1)
        application.graceful_stop()
        recorder.check("rapid_exit_process_tree_cleanup", passed=True)
        _assert_database_audit(paths, recorder, phase="lifecycle-final")
        recorder.report["resource_summary"] = _summarize_resource_samples(
            _read_json_lines(paths.samples_path)
        )
    finally:
        if browser_probe is not None:
            browser_probe.stop()
        application.cleanup()


def _run_normal_soak(
    args: argparse.Namespace,
    paths: QaPaths,
    recorder: EvidenceRecorder,
) -> None:
    application = PackagedApplication(
        executable=args.app_executable,
        paths=paths,
        recorder=recorder,
        extra_environment=_qa_backend_environment(paths),
    )
    workloads: WorkloadHarness | None = None
    try:
        identity = application.launch(mode="split")
        assert identity is not None
        _validate_expected_app_version(identity, args.expected_app_version)
        _validate_runtime_roles(identity, paths.user_data, expected_mode="split")
        workloads = WorkloadHarness(paths, recorder)
        _run_soak_loop(
            args=args,
            paths=paths,
            recorder=recorder,
            application=application,
            workloads=workloads,
            chaos=False,
        )
        application.graceful_stop()
    finally:
        application.cleanup()
        if workloads is not None:
            workloads.close()
    _finalize_soak_evidence(args, paths, recorder)


def _run_seeded_chaos(
    args: argparse.Namespace,
    paths: QaPaths,
    recorder: EvidenceRecorder,
) -> None:
    application = PackagedApplication(
        executable=args.app_executable,
        paths=paths,
        recorder=recorder,
        extra_environment=_qa_backend_environment(paths),
    )
    workloads: WorkloadHarness | None = None
    try:
        identity = application.launch(mode="split")
        assert identity is not None
        _validate_expected_app_version(identity, args.expected_app_version)
        _validate_runtime_roles(identity, paths.user_data, expected_mode="split")
        workloads = WorkloadHarness(paths, recorder)
        _run_soak_loop(
            args=args,
            paths=paths,
            recorder=recorder,
            application=application,
            workloads=workloads,
            chaos=True,
        )
        application.graceful_stop()
    finally:
        application.cleanup()
        if workloads is not None:
            workloads.close()
        _set_clock_offset(paths.clock_offset, 0)
    _finalize_soak_evidence(args, paths, recorder)


def _run_soak_loop(
    *,
    args: argparse.Namespace,
    paths: QaPaths,
    recorder: EvidenceRecorder,
    application: PackagedApplication,
    workloads: WorkloadHarness,
    chaos: bool,
) -> None:
    rng = random.Random(args.seed)
    started = time.monotonic()
    started_wall = time.time()
    deadline = started + args.duration_seconds
    next_sample = started
    next_action = started
    action_index = 0
    expected_runtime_id = application.identity.runtime_id if application.identity else None
    base_actions = (
        "worker-kill",
        "api-kill",
        "network-flap",
        "sqlite-lock",
        "worker-suspend-resume",
        "clock-forward",
        "clock-backward",
    )
    actions = (
        (*base_actions, "system-sleep-wake")
        if args.system_sleep_wake
        else base_actions
    )
    action_schedule: list[str] = []
    system_sleep_pending = args.system_sleep_wake
    chaos_counts = {action: 0 for action in actions}
    recorder.report["chaos_summary"] = {
        "seed": args.seed,
        "action_counts": chaos_counts,
        "required_actions": list(actions),
    }
    while time.monotonic() < deadline:
        now = time.monotonic()
        identity = application.probe_ready(timeout_seconds=30)
        if not chaos and identity.runtime_id != expected_runtime_id:
            raise QaFailure("normal soak observed an unexpected runtime group replacement")
        if now >= next_sample:
            sample = _collect_resource_sample(identity, paths.user_data, started)
            recorder.sample(sample)
            _exercise_api_read(identity)
            next_sample = now + args.sample_interval_seconds
        if now >= next_action:
            if chaos:
                if not action_schedule:
                    action_schedule = list(base_actions)
                    rng.shuffle(action_schedule)
                    if system_sleep_pending:
                        # pop() selects the final entry.  Native sleep goes
                        # first so a freshly authorized macOS sudo ticket
                        # cannot expire behind seven randomized actions.
                        action_schedule.append("system-sleep-wake")
                action = action_schedule.pop()
                recorder.event(
                    "chaos_action_selected",
                    seed=args.seed,
                    index=action_index,
                    action=action,
                )
                if action == "network-flap":
                    fault_callback = partial(
                        _exercise_api_read_write,
                        application.identity or application.wait_ready(timeout_seconds=30),
                        marker=f"chaos-network-flap-{action_index}",
                    )
                else:
                    fault_callback = partial(
                        _execute_chaos_action,
                        action,
                        application=application,
                        paths=paths,
                        recorder=recorder,
                        index=action_index,
                    )
                workloads.run_cycle(
                    index=action_index,
                    chaos=True,
                    network_flap=action == "network-flap",
                    fault_callback=fault_callback,
                )
                chaos_counts[action] += 1
                if action == "system-sleep-wake":
                    system_sleep_pending = False
                if action == "network-flap":
                    recorder.event(
                        "chaos_action_completed",
                        index=action_index,
                        action=action,
                    )
            else:
                workloads.run_cycle(
                    index=action_index,
                    chaos=False,
                    network_flap=False,
                    fault_callback=partial(
                        _exercise_api_read_write,
                        identity,
                        marker=f"normal-soak-{action_index}",
                    ),
                )
                recorder.event("normal_soak_work_cycle", index=action_index)
            action_index += 1
            _assert_database_audit(paths, recorder, phase=f"cycle-{action_index}")
            next_action = time.monotonic() + args.action_interval_seconds
        time.sleep(min(0.5, max(0.05, deadline - time.monotonic())))
    recorder.report["soak_duration"] = {
        "monotonic_seconds": time.monotonic() - started,
        "wall_seconds": time.time() - started_wall,
    }


def _execute_chaos_action(
    action: str,
    *,
    application: PackagedApplication,
    paths: QaPaths,
    recorder: EvidenceRecorder,
    index: int,
) -> None:
    identity = application.identity or application.wait_ready(timeout_seconds=30)
    if identity.worker is None:
        raise QaFailure("chaos action requires split Worker identity")
    if action == "worker-kill":
        retired_pids = _process_tree_pids({identity.worker.pid})
        psutil.Process(identity.worker.pid).kill()
        _wait_for_pids_gone({identity.worker.pid}, timeout_seconds=10)
        _exercise_api_read_write(identity, marker=f"chaos-worker-kill-{index}")
        application.wait_for_replacement(previous=identity, replace_group=False)
        _require_retired_process_tree_gone(
            retired_pids,
            recorder=recorder,
            action=action,
            index=index,
        )
    elif action == "api-kill":
        retired_pids = _process_tree_pids(
            {
                identity.backend.pid,
                identity.worker.pid,
            }
        )
        psutil.Process(identity.backend.pid).kill()
        _wait_for_pids_gone({identity.backend.pid}, timeout_seconds=10)
        application.wait_for_replacement(previous=identity, replace_group=True)
        _require_retired_process_tree_gone(
            retired_pids,
            recorder=recorder,
            action=action,
            index=index,
        )
    elif action == "sqlite-lock":
        _exercise_sqlite_lock(
            paths.user_data / DATABASE_NAME,
            lambda: _exercise_api_read_write(identity, marker=f"chaos-lock-{index}"),
        )
    elif action == "worker-suspend-resume":
        worker = psutil.Process(identity.worker.pid)
        worker.suspend()
        try:
            _exercise_api_read_write(identity, marker=f"chaos-suspend-{index}")
            time.sleep(3)
        finally:
            with contextlib.suppress(psutil.Error):
                worker.resume()
        application.wait_ready(timeout_seconds=30)
    elif action in {"clock-forward", "clock-backward"}:
        offset = 2 * 60 * 60 if action == "clock-forward" else -(2 * 60 * 60)
        _set_clock_offset(paths.clock_offset, offset)
        try:
            _exercise_api_read_write(identity, marker=f"chaos-{action}-{index}")
            time.sleep(2)
        finally:
            _set_clock_offset(paths.clock_offset, 0)
    elif action == "system-sleep-wake":
        _exercise_system_sleep_wake(
            application=application,
            paths=paths,
            recorder=recorder,
            marker=f"seeded-chaos-{index}",
        )
    else:  # pragma: no cover - guarded by the fixed action table
        raise QaFailure(f"unknown chaos action: {action}")
    recorder.event("chaos_action_completed", index=index, action=action)


def _exercise_system_sleep_wake(
    *,
    application: PackagedApplication,
    paths: QaPaths,
    recorder: EvidenceRecorder,
    marker: str,
) -> dict[str, object]:
    before = application.identity or application.wait_ready(timeout_seconds=30)
    if before.worker is None:
        raise QaFailure("native system sleep/wake requires split mode")
    before_status = _read_json(paths.user_data / "runtime" / "worker.json")
    before_heartbeat = before_status.get("heartbeat_at")
    if not isinstance(before_heartbeat, str) or not before_heartbeat:
        raise QaFailure("Worker status has no heartbeat before native system sleep")
    recorder.event(
        "native_system_sleep_requested",
        marker=marker,
        runtime_id=before.runtime_id,
        backend_pid=before.backend.pid,
        worker_pid=before.worker.pid,
    )
    if sys.platform == "darwin":
        native_evidence = _exercise_macos_system_sleep_wake()
    elif sys.platform == "win32":
        native_evidence = _exercise_windows_system_sleep_wake()
    else:
        raise QaFailure(f"native system sleep/wake is unsupported on {sys.platform}")

    after = application.probe_ready(timeout_seconds=120)
    if (
        after.runtime_id != before.runtime_id
        or after.backend.pid != before.backend.pid
        or after.worker is None
        or after.worker.pid != before.worker.pid
    ):
        raise QaFailure(
            "native system sleep/wake unexpectedly replaced the runtime group or Worker"
        )
    after_status = _wait_until(
        lambda: (
            status
            if (
                (status := _read_json(paths.user_data / "runtime" / "worker.json"))
                .get("heartbeat_at")
                != before_heartbeat
                and status.get("state") == "ready"
            )
            else None
        ),
        timeout_seconds=30,
        description="Worker heartbeat to advance after native system wake",
    )
    api_evidence = _exercise_api_read_write(after, marker=f"sleep-wake-{marker}")
    evidence = {
        **native_evidence,
        "runtime_id": after.runtime_id,
        "backend_pid": after.backend.pid,
        "worker_pid": after.worker.pid,
        "heartbeat_before": before_heartbeat,
        "heartbeat_after": after_status.get("heartbeat_at"),
        "api_state": api_evidence.get("runtime_state"),
    }
    recorder.check("native_system_sleep_wake", passed=True, evidence=evidence)
    return evidence


def _exercise_macos_system_sleep_wake(*, wake_after_seconds: int = 20) -> dict[str, object]:
    before = _read_macos_power_counts()
    started = time.time()
    _run_native_command(
        ["/usr/bin/sudo", "-n", "/usr/bin/pmset", "relative", "wake", str(wake_after_seconds)],
        description="schedule macOS RTC wake",
        timeout_seconds=15,
    )
    _run_native_command(
        ["/usr/bin/sudo", "-n", "/usr/bin/pmset", "sleepnow"],
        description="enter macOS system sleep",
        timeout_seconds=wake_after_seconds + 180,
    )
    after = _wait_until(
        lambda: (
            counts
            if (
                (counts := _read_macos_power_counts())["sleep_count"]
                > before["sleep_count"]
                and counts["wake_count"] > before["wake_count"]
            )
            else None
        ),
        timeout_seconds=60,
        description="macOS pmset sleep and wake counters to advance",
    )
    elapsed = time.time() - started
    if elapsed < 5:
        raise QaFailure(
            f"macOS sleep/wake returned after only {elapsed:.2f}s; no real suspend interval"
        )
    return {
        "platform": "darwin",
        "wall_elapsed_seconds": elapsed,
        "wake_after_seconds": wake_after_seconds,
        "power_counts_before": before,
        "power_counts_after": after,
    }


def _read_macos_power_counts() -> dict[str, int]:
    completed = subprocess.run(
        ["/usr/bin/pmset", "-g", "stats"],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise QaFailure(
            "cannot read macOS power counters: "
            f"{completed.stderr.strip()[-500:]}"
        )
    values = {
        name: int(value)
        for name, value in re.findall(
            r"(Sleep Count|Dark Wake Count|User Wake Count):\s*(\d+)",
            completed.stdout,
        )
    }
    required = {"Sleep Count", "Dark Wake Count", "User Wake Count"}
    if set(values) != required:
        raise QaFailure("macOS pmset stats did not contain all sleep/wake counters")
    return {
        "sleep_count": values["Sleep Count"],
        "wake_count": values["Dark Wake Count"] + values["User Wake Count"],
        "dark_wake_count": values["Dark Wake Count"],
        "user_wake_count": values["User Wake Count"],
    }


def _exercise_windows_system_sleep_wake(
    *,
    wake_after_seconds: int = 20,
) -> dict[str, object]:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    powrprof = ctypes.WinDLL("PowrProf", use_last_error=True)
    create_timer = kernel32.CreateWaitableTimerW
    create_timer.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    create_timer.restype = wintypes.HANDLE
    set_timer = kernel32.SetWaitableTimer
    set_timer.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.LONG,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
    ]
    set_timer.restype = wintypes.BOOL
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    wait_for_single_object.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    set_suspend_state = powrprof.SetSuspendState
    set_suspend_state.argtypes = [wintypes.BOOLEAN, wintypes.BOOLEAN, wintypes.BOOLEAN]
    set_suspend_state.restype = wintypes.BOOLEAN

    timer = create_timer(None, True, f"AutoEmailSenderQaWake-{uuid.uuid4()}")
    if not timer:
        raise QaFailure(f"CreateWaitableTimerW failed with error {ctypes.get_last_error()}")
    started_at = datetime.now(UTC)
    started = time.time()
    try:
        due_time = ctypes.c_longlong(-wake_after_seconds * 10_000_000)
        if not set_timer(timer, ctypes.byref(due_time), 0, None, None, True):
            raise QaFailure(
                "Windows cannot arm a resume-capable wake timer: "
                f"error {ctypes.get_last_error()}"
            )
        if not set_suspend_state(False, False, False):
            raise QaFailure(
                "Windows SetSuspendState failed; sleep may be disabled by the VM or policy: "
                f"error {ctypes.get_last_error()}"
            )
        wait_result = int(
            wait_for_single_object(timer, (wake_after_seconds + 180) * 1000)
        )
        if wait_result != 0:
            raise QaFailure(
                f"Windows resume timer did not signal after wake (wait result {wait_result})"
            )
    finally:
        close_handle(timer)
    elapsed = time.time() - started
    if elapsed < 5:
        raise QaFailure(
            f"Windows sleep/wake returned after only {elapsed:.2f}s; no real suspend interval"
        )
    events = _wait_until(
        lambda: (
            value
            if (
                (value := _read_windows_power_events(started_at))["sleep_events"] >= 1
                and value["wake_events"] >= 1
            )
            else None
        ),
        timeout_seconds=60,
        description="Windows native sleep and wake events",
    )
    return {
        "platform": "win32",
        "wall_elapsed_seconds": elapsed,
        "wake_after_seconds": wake_after_seconds,
        **events,
    }


def _read_windows_power_events(started_at: datetime) -> dict[str, int]:
    timestamp = started_at.astimezone(UTC).isoformat()
    script = (
        f"$start=[DateTimeOffset]::Parse('{timestamp}').LocalDateTime;"
        "$events=@(Get-WinEvent -FilterHashtable "
        "@{LogName='System';StartTime=$start} -ErrorAction SilentlyContinue);"
        "$sleep=@($events|Where-Object {$_.ProviderName -eq "
        "'Microsoft-Windows-Kernel-Power' -and $_.Id -eq 42}).Count;"
        "$wake=@($events|Where-Object {$_.ProviderName -eq "
        "'Microsoft-Windows-Power-Troubleshooter' -and $_.Id -eq 1}).Count;"
        "[ordered]@{sleep_events=$sleep;wake_events=$wake}|ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", script],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise QaFailure(
            "cannot query Windows sleep/wake events: "
            f"{completed.stderr.strip()[-500:]}"
        )
    try:
        payload = json.loads(completed.stdout.strip())
        return {
            "sleep_events": int(payload["sleep_events"]),
            "wake_events": int(payload["wake_events"]),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise QaFailure("Windows power event query returned invalid JSON") from exc


def _run_native_command(
    command: list[str],
    *,
    description: str,
    timeout_seconds: float,
) -> None:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise QaFailure(f"timed out while attempting to {description}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-500:]
        raise QaFailure(f"failed to {description}: {detail}")


def _finalize_soak_evidence(
    args: argparse.Namespace,
    paths: QaPaths,
    recorder: EvidenceRecorder,
) -> None:
    _assert_database_audit(paths, recorder, phase="soak-final")
    samples = _read_json_lines(paths.samples_path)
    summary = _summarize_resource_samples(samples)
    recorder.report["resource_summary"] = summary
    recorder.check(
        "resource_growth_within_thresholds",
        passed=not summary.get("violations"),
        evidence=summary,
    )
    workload_summary = recorder.report.get("workload_summary")
    completed = (
        workload_summary.get("workloads_completed", {})
        if isinstance(workload_summary, dict)
        else {}
    )
    recorder.check(
        "six_real_worker_workloads_completed",
        passed=(
            isinstance(workload_summary, dict)
            and int(workload_summary.get("cycles_completed", 0)) >= 1
            and int(workload_summary.get("cycles_failed", 0)) == 0
            and all(int(completed.get(name, 0)) >= 1 for name in WorkloadHarness.REQUIRED_WORKLOADS)
        ),
        evidence=workload_summary,
    )
    if args.scenario == "seeded-chaos":
        chaos_summary = recorder.report.get("chaos_summary")
        action_counts = (
            chaos_summary.get("action_counts", {})
            if isinstance(chaos_summary, dict)
            else {}
        )
        recorder.check(
            "seeded_chaos_action_coverage",
            passed=(
                isinstance(chaos_summary, dict)
                and (
                    all(
                        int(action_counts.get(action, 0)) >= 1
                        for action in chaos_summary.get("required_actions", [])
                    )
                    if args.certification or args.prerelease_certification
                    else sum(int(value) for value in action_counts.values()) >= 1
                )
            ),
            evidence=chaos_summary,
        )
    if args.certification or args.prerelease_certification:
        minimums = (
            PRERELEASE_CERTIFICATION_MINIMUM_SECONDS
            if args.prerelease_certification
            else CERTIFICATION_MINIMUM_SECONDS
        )
        minimum = minimums[args.scenario]
        soak_duration = recorder.report.get("soak_duration")
        monotonic_seconds = (
            float(soak_duration.get("monotonic_seconds", 0.0))
            if isinstance(soak_duration, dict)
            else 0.0
        )
        wall_seconds = (
            float(soak_duration.get("wall_seconds", 0.0))
            if isinstance(soak_duration, dict)
            else 0.0
        )
        recorder.check(
            "minimum_continuous_duration",
            passed=(
                args.duration_seconds >= minimum
                and monotonic_seconds >= minimum
                and wall_seconds >= minimum
            ),
            evidence={
                "required_seconds": minimum,
                "requested_seconds": args.duration_seconds,
                "monotonic_seconds": monotonic_seconds,
                "wall_seconds": wall_seconds,
            },
        )


def _create_paths(
    artifacts_dir: Path,
    *,
    existing_user_data: Path | None = None,
) -> QaPaths:
    artifacts_root = artifacts_dir.expanduser().resolve()
    marker_root = artifacts_root / QA_PATH_MARKER
    run_root = marker_root / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid.uuid4().hex[:10]}"
    )
    user_data = (
        run_root / "用户 数据 Ω"
        if existing_user_data is None
        else _resolve_existing_qa_user_data(existing_user_data)
    )
    fault_dir = run_root / "fault-controls"
    logs_dir = run_root / "logs"
    for directory in (user_data, fault_dir, logs_dir):
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        with contextlib.suppress(OSError):
            directory.chmod(0o700)
    canonical_run_root = run_root.resolve(strict=True)
    canonical_user_data = user_data.resolve(strict=True)
    if QA_PATH_MARKER not in canonical_user_data.parts:
        raise QaFailure("isolated QA path marker was lost during path canonicalization")
    clock_offset = fault_dir / "clock-offset-seconds.txt"
    _set_clock_offset(clock_offset, 0)
    return QaPaths(
        run_root=canonical_run_root,
        user_data=canonical_user_data,
        fault_dir=fault_dir.resolve(strict=True),
        clock_offset=clock_offset.resolve(strict=True),
        logs_dir=logs_dir.resolve(strict=True),
        report_path=canonical_run_root / REPORT_NAME,
        trace_path=canonical_run_root / TRACE_NAME,
        samples_path=canonical_run_root / RESOURCE_SAMPLES_NAME,
    )


def _resolve_existing_qa_user_data(path: Path) -> Path:
    expanded = path.expanduser().absolute()
    if not expanded.is_dir():
        raise QaFailure(f"existing QA userData is missing: {expanded}")
    marker_seen = False
    current = Path(expanded.anchor)
    for part in expanded.parts[1:]:
        current /= part
        marker_seen = marker_seen or part == QA_PATH_MARKER
        if marker_seen and current.is_symlink():
            raise QaFailure("existing QA userData must not contain symbolic links")
    canonical = expanded.resolve(strict=True)
    if QA_PATH_MARKER not in canonical.parts:
        raise QaFailure(
            f"existing QA userData must contain the exact {QA_PATH_MARKER!r} path marker"
        )
    if sys.platform != "win32" and canonical.stat().st_mode & 0o077:
        raise QaFailure("existing QA userData must not be group- or world-accessible")
    return canonical


def _authorize_user_data(user_data: Path, nonce: str) -> None:
    canonical = user_data.resolve(strict=True)
    sentinel = canonical / QA_SENTINEL_NAME
    _write_json_atomic(
        sentinel,
        {
            "protocol_version": QA_SENTINEL_PROTOCOL_VERSION,
            "purpose": "packaged-release-qa",
            "nonce": nonce,
            "user_data_path": str(canonical),
        },
        mode=0o600,
    )
    with contextlib.suppress(OSError):
        canonical.chmod(0o700)
        sentinel.chmod(0o600)


def _qa_backend_environment(paths: QaPaths) -> dict[str, str]:
    return {
        "AUTO_EMAIL_SENDER_TEST_FAULTS": "enabled-for-tests-only",
        "AUTO_EMAIL_SENDER_TEST_FAULT_DIR": str(paths.fault_dir),
        "AUTO_EMAIL_SENDER_TEST_CRAWL_LOOPBACK_HOSTS": QA_CRAWL_HOST,
        "AUTO_EMAIL_SENDER_TEST_CLOCK_OFFSET_FILE": str(paths.clock_offset),
        "DRAFT_WORKER_INTERVAL_SECONDS": "1",
        "DISPATCHER_INTERVAL_SECONDS": "1",
        "IMAP_POLL_INTERVAL_SECONDS": "1",
        "IMAP_IDENTITY_LEASE_SECONDS": "60",
        "IMAP_IDENTITY_SYNC_TIMEOUT_SECONDS": "45",
        "IMAP_HISTORY_BATCH_SIZE": "10",
        "IMAP_HISTORY_COMMAND_BUDGET_PER_MINUTE": "10000",
        "IMAP_HISTORY_COMMAND_RATE_PER_MINUTE": "10000",
        "IMAP_HISTORY_COMMAND_BURST": "10000",
        "IMAP_HISTORY_QUEUE_SETTLE_SECONDS": "0",
        "IMAP_FETCH_BATCH_SIZE": "20",
        "IMAP_SENT_FOLDER_FAILURE_TTL_SECONDS": "3600",
        "MATCH_ANALYSIS_JOB_INTERVAL_SECONDS": "1",
        "MATCH_ANALYSIS_JOB_ITEM_CONCURRENCY": "1",
        "LLM_REQUEST_TIMEOUT_SECONDS": "10",
        "CRAWLER_DEBUG": "0",
    }


def _probe_ready_identity(
    *,
    descriptor_path: Path,
    expected_mode: Literal["split", "combined"],
    launched_pid: int,
    launched_at_wall: float,
    previous_runtime_id: str | None,
) -> RuntimeIdentity | None:
    try:
        payload = json.loads(descriptor_path.read_text(encoding="utf-8"))
        identity = RuntimeIdentity.from_payload(payload)
    except (FileNotFoundError, OSError, json.JSONDecodeError, QaFailure):
        return None
    if identity.protocol_version != AGENT_PROTOCOL_VERSION:
        raise QaFailure(
            f"packaged runtime protocol {identity.protocol_version!r} is not v{AGENT_PROTOCOL_VERSION}"
        )
    if previous_runtime_id is not None and identity.runtime_id == previous_runtime_id:
        return None
    if identity.desktop.pid != launched_pid:
        return None
    if not _process_started_after(identity.desktop.pid, launched_at_wall):
        return None
    if not _pid_is_running(identity.backend.pid):
        return None
    if expected_mode == "split":
        if identity.worker is None or not _pid_is_running(identity.worker.pid):
            return None
        if len({identity.desktop.pid, identity.backend.pid, identity.worker.pid}) != 3:
            raise QaFailure("split runtime did not publish three distinct process identities")
    elif identity.worker is not None:
        raise QaFailure("combined runtime unexpectedly published a separate Worker")
    try:
        runtime = _request_json(
            "GET",
            f"{identity.base_url.rstrip('/')}/api/agent/v1/runtime",
            token=identity.access_token,
            timeout_seconds=2,
        )
    except (OSError, QaFailure):
        return None
    if not isinstance(runtime, dict) or runtime.get("state") != "ready":
        return None
    expected = {
        "runtime_id": identity.runtime_id,
        "protocol_version": identity.protocol_version,
        "app_version": identity.app_version,
        "backend_pid": identity.backend.pid,
        "desktop_pid": identity.desktop.pid,
    }
    if any(runtime.get(name) != value for name, value in expected.items()):
        raise QaFailure("authenticated runtime handshake does not match the descriptor")
    return identity


def _validate_runtime_roles(
    identity: RuntimeIdentity,
    user_data: Path,
    *,
    expected_mode: Literal["split", "combined"],
) -> None:
    backend_cmdline = _safe_cmdline(identity.backend.pid)
    if expected_mode == "split":
        if "--role" not in backend_cmdline or "api" not in backend_cmdline:
            raise QaFailure(f"API command line does not identify its role: {backend_cmdline}")
        if identity.worker is None:
            raise QaFailure("split runtime has no Worker")
        worker_cmdline = _safe_cmdline(identity.worker.pid)
        if "--role" not in worker_cmdline or "worker" not in worker_cmdline:
            raise QaFailure(f"Worker command line does not identify its role: {worker_cmdline}")
        for role, expected_pid in (
            ("api", identity.backend.pid),
            ("worker", identity.worker.pid),
        ):
            status = _read_json(user_data / "runtime" / f"{role}.json")
            if (
                status.get("protocol_version") != ROLE_STATUS_PROTOCOL_VERSION
                or status.get("runtime_id") != identity.runtime_id
                or status.get("role") != role
                or status.get("pid") != expected_pid
                or status.get("state") != "ready"
            ):
                raise QaFailure(f"{role} status file does not match the runtime identity")
    else:
        if "--role" not in backend_cmdline or "combined" not in backend_cmdline:
            raise QaFailure(
                f"combined backend command line does not identify its role: {backend_cmdline}"
            )


def _validate_expected_app_version(
    identity: RuntimeIdentity,
    expected_app_version: str,
) -> None:
    if expected_app_version and identity.app_version != expected_app_version:
        raise QaFailure(
            "packaged runtime version does not match the repository candidate: "
            f"runtime={identity.app_version}, expected={expected_app_version}"
        )


def _exercise_second_instance(
    application: PackagedApplication,
    first: RuntimeIdentity,
    recorder: EvidenceRecorder,
) -> None:
    handle = application._require_handle()
    environment = os.environ.copy()
    environment.update(application.extra_environment)
    environment.update(
        {
            QA_ENABLE_ENV: QA_ENABLE_VALUE,
            QA_NONCE_ENV: handle.nonce,
            QA_USER_DATA_ENV: str(application.paths.user_data),
            "AUTO_EMAIL_SENDER_BACKEND_MODE": handle.mode,
        }
    )
    second_stdout = application.paths.logs_dir / "second-instance.stdout.log"
    second_stderr = application.paths.logs_dir / "second-instance.stderr.log"
    with second_stdout.open("wb") as stdout_file, second_stderr.open("wb") as stderr_file:
        second = subprocess.Popen(
            [
                str(application.executable),
                f"--auto-email-sender-packaged-qa={handle.nonce}",
            ],
            cwd=application.executable.parent,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
        )
        try:
            second.wait(timeout=15)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(psutil.Error):
                psutil.Process(second.pid).kill()
            raise QaFailure("second packaged desktop instance did not fail fast")
    still_ready = application.wait_ready(timeout_seconds=15)
    if still_ready.runtime_id != first.runtime_id:
        raise QaFailure("second desktop launch disturbed the active runtime group")
    recorder.check(
        "single_instance_lock",
        passed=True,
        evidence={"second_exit_code": second.returncode, "runtime_id": first.runtime_id},
    )


def _exercise_api_read(identity: RuntimeIdentity) -> dict[str, object]:
    runtime = _request_json(
        "GET",
        f"{identity.base_url.rstrip('/')}/api/agent/v1/runtime",
        token=identity.access_token,
    )
    professors = _request_json(
        "GET",
        f"{identity.base_url.rstrip('/')}/api/agent/v1/professors?limit=1",
        token=identity.access_token,
    )
    if not isinstance(runtime, dict) or runtime.get("runtime_id") != identity.runtime_id:
        raise QaFailure("runtime read returned a different identity")
    if not isinstance(professors, dict) or not isinstance(professors.get("items"), list):
        raise QaFailure("professor read did not return the Agent page contract")
    return {"runtime_state": runtime.get("state"), "professor_count": len(professors["items"])}


def _exercise_api_read_write(identity: RuntimeIdentity, *, marker: str) -> dict[str, object]:
    read_evidence = _exercise_api_read(identity)
    settings_url = f"{identity.base_url.rstrip('/')}/api/agent/v1/settings"
    current = _request_json("GET", settings_url, token=identity.access_token)
    if not isinstance(current, dict):
        raise QaFailure("settings read did not return an object")
    revision = current.get("revision")
    value = f"packaged-qa:{marker}:{uuid.uuid4().hex[:12]}"
    payload = _build_settings_update_payload(current, value)
    updated = _request_json(
        "PATCH",
        settings_url,
        token=identity.access_token,
        payload=payload,
        headers={
            "Idempotency-Key": f"packaged-qa-{uuid.uuid4()}",
            **({"If-Revision": revision} if isinstance(revision, str) else {}),
        },
        timeout_seconds=20,
    )
    if not isinstance(updated, dict) or updated.get("draft_custom_instruction") != value:
        raise QaFailure("authenticated settings write did not commit")
    return {**read_evidence, "settings_revision": updated.get("revision"), "marker": marker}


def _build_settings_update_payload(
    settings: dict[str, object],
    marker: str,
) -> dict[str, object]:
    payload = {
        key: value
        for key, value in settings.items()
        if key not in SETTINGS_READ_ONLY_FIELDS
    }
    payload["draft_custom_instruction"] = marker
    return payload


def _verify_candidate_asset_manifest(
    manifest_path: Path,
    *,
    package_file: Path | None,
    expected_package_sha256: str,
    expected_revision: str,
    expected_app_version: str,
    expected_run_id: int | None,
) -> dict[str, object]:
    if package_file is None:
        raise QaFailure("candidate manifest verification requires a package file")
    if not expected_revision or not expected_app_version or expected_run_id is None:
        raise QaFailure("candidate manifest verification requires revision, version, and run id")
    platform_name = {"darwin": "macos", "win32": "windows"}.get(sys.platform)
    if platform_name is None:
        raise QaFailure(f"candidate packaged QA is unsupported on {sys.platform}")
    manifest = _read_json(manifest_path)
    expected_tag = f"v{expected_app_version}"
    candidate_kind = manifest.get("kind")
    is_prerelease = candidate_kind == "auto-email-sender-prerelease-candidate"
    if candidate_kind not in {
        "auto-email-sender-release-candidate",
        "auto-email-sender-prerelease-candidate",
    }:
        raise QaFailure(f"candidate manifest kind is unsupported: {candidate_kind!r}")
    expected_identity: dict[str, object] = {
        "schemaVersion": 1,
        "kind": candidate_kind,
        "releaseTag": expected_tag,
        "version": expected_app_version,
        "releaseSha": expected_revision,
        "candidateRunId": expected_run_id,
    }
    for field_name, expected in expected_identity.items():
        if manifest.get(field_name) != expected:
            raise QaFailure(
                f"candidate manifest {field_name} does not match: "
                f"actual={manifest.get(field_name)!r}, expected={expected!r}"
            )
    repository = manifest.get("repository")
    if not isinstance(repository, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ):
        raise QaFailure("candidate manifest repository identity is invalid")
    prerelease_channel: str | None = None
    source_branch: str | None = None
    if is_prerelease:
        version_match = re.fullmatch(
            r"\d+\.\d+\.\d+-(alpha|beta|rc)\.[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*",
            expected_app_version,
        )
        prerelease_channel = version_match.group(1) if version_match else None
        source_branch_value = manifest.get("sourceBranch")
        source_branch = source_branch_value if isinstance(source_branch_value, str) else None
        source_branch_is_safe = bool(
            source_branch
            and re.fullmatch(r"[A-Za-z0-9._/+@-]{1,200}", source_branch)
            and not source_branch.startswith(("refs/", "-", "/"))
            and not source_branch.endswith(("/", "."))
            and ".." not in source_branch
            and "@{" not in source_branch
            and "//" not in source_branch
            and not any(part.endswith(".lock") for part in source_branch.split("/"))
        )
        if prerelease_channel is None or not source_branch_is_safe:
            raise QaFailure("prerelease candidate channel or sourceBranch is invalid")
        prerelease_identity: dict[str, object] = {
            "channel": prerelease_channel,
            "sourceBranch": source_branch,
            "defaultBackendMode": "split",
            "diagnosticsSchemaVersion": 1,
        }
        for field_name, expected in prerelease_identity.items():
            if manifest.get(field_name) != expected:
                raise QaFailure(
                    f"prerelease candidate manifest {field_name} does not match: "
                    f"actual={manifest.get(field_name)!r}, expected={expected!r}"
                )
        stable_isolation = manifest.get("stableIsolation")
        if (
            not isinstance(stable_isolation, dict)
            or stable_isolation.get("kind")
            != "auto-email-sender-stable-isolation-snapshot"
            or stable_isolation.get("repository") != repository
        ):
            raise QaFailure("prerelease candidate stable isolation identity is invalid")
    platforms = manifest.get("platforms")
    evidence = platforms.get(platform_name) if isinstance(platforms, dict) else None
    if not isinstance(evidence, dict):
        raise QaFailure(f"candidate manifest has no {platform_name} platform evidence")
    expected_platform_identity: dict[str, object] = {
        "schemaVersion": 1,
        "kind": (
            "auto-email-sender-prerelease-platform-evidence"
            if is_prerelease
            else "auto-email-sender-platform-evidence"
        ),
        "platform": platform_name,
        "releaseTag": expected_tag,
        "version": expected_app_version,
        "releaseSha": expected_revision,
        "candidateRunId": expected_run_id,
    }
    for field_name, expected in expected_platform_identity.items():
        if evidence.get(field_name) != expected:
            raise QaFailure(
                f"candidate {platform_name} evidence {field_name} does not match: "
                f"actual={evidence.get(field_name)!r}, expected={expected!r}"
            )
    expected_asset_name = (
        f"AutoEmailSender-Setup-{expected_app_version}.exe"
        if platform_name == "windows"
        else f"AutoEmailSender-{expected_app_version}-arm64.dmg"
    )
    if is_prerelease:
        prerelease_platform_identity: dict[str, object] = {
            "channel": prerelease_channel,
            "sourceBranch": source_branch,
            "defaultBackendMode": "split",
            "diagnosticsSchemaVersion": 1,
        }
        for field_name, expected in prerelease_platform_identity.items():
            if evidence.get(field_name) != expected:
                raise QaFailure(
                    f"prerelease candidate {platform_name} evidence {field_name} does not match: "
                    f"actual={evidence.get(field_name)!r}, expected={expected!r}"
                )
        expected_build_identity: dict[str, object] = {
            "schema_version": 1,
            "release_kind": "prerelease",
            "version": expected_app_version,
            "channel": prerelease_channel,
            "source_branch": source_branch,
            "release_sha": expected_revision,
            "candidate_run_id": str(expected_run_id),
            "candidate_asset_name": expected_asset_name,
            "candidate_asset_sha256": None,
            "default_backend_mode": "split",
            "diagnostics_schema_version": 1,
        }
        if evidence.get("buildIdentity") != expected_build_identity:
            raise QaFailure(
                f"prerelease candidate {platform_name} build identity does not match"
            )
        artifact = evidence.get("artifact")
        records = (
            [artifact]
            if isinstance(artifact, dict) and artifact.get("name") == expected_asset_name
            else []
        )
    else:
        artifacts = evidence.get("artifacts")
        records = (
            [
                item
                for item in artifacts
                if isinstance(item, dict) and item.get("name") == expected_asset_name
            ]
            if isinstance(artifacts, list)
            else []
        )
    if len(records) != 1:
        raise QaFailure(
            f"candidate manifest must contain exactly one {expected_asset_name} record"
        )
    record = records[0]
    recorded_size = record.get("size")
    recorded_sha256 = record.get("sha256")
    if (
        not isinstance(recorded_size, int)
        or isinstance(recorded_size, bool)
        or recorded_size <= 0
        or not isinstance(recorded_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", recorded_sha256) is None
    ):
        raise QaFailure("candidate package record has an invalid size or SHA-256")
    actual_size = package_file.stat().st_size
    actual_sha256 = _sha256_file(package_file)
    if actual_size != recorded_size or actual_sha256 != recorded_sha256:
        raise QaFailure("candidate package bytes do not match the candidate manifest")
    if expected_package_sha256 and expected_package_sha256 != recorded_sha256:
        raise QaFailure("expected package SHA-256 does not match the candidate manifest")
    return {
        "repository": repository,
        "candidate_kind": candidate_kind,
        "release_tag": expected_tag,
        "release_sha": expected_revision,
        "candidate_run_id": expected_run_id,
        "channel": prerelease_channel,
        "source_branch": source_branch,
        "platform": platform_name,
        "asset_name": expected_asset_name,
        "asset_size": actual_size,
        "asset_sha256": actual_sha256,
        "manifest_sha256": _sha256_file(manifest_path),
    }


def _verify_upgrade_manifest(
    manifest_path: Path,
    identity: RuntimeIdentity,
    user_data: Path,
    *,
    repository_root: Path,
    expected_previous_version: str,
    expected_previous_package_sha256: str | None,
    require_repository_head: bool = True,
) -> dict[str, object]:
    manifest = _read_json(manifest_path)
    if (
        manifest.get("protocol_version") != "1"
        or manifest.get("purpose") != "previous-stable-packaged-upgrade"
    ):
        raise QaFailure("previous-stable upgrade manifest has an unsupported contract")
    if manifest.get("user_data_path") != str(user_data):
        raise QaFailure("upgrade manifest userData path does not match this QA run")
    previous_app_version = manifest.get("previous_app_version")
    if not isinstance(previous_app_version, str) or not previous_app_version.strip():
        raise QaFailure("upgrade manifest has no previous app version")
    if previous_app_version.strip() != expected_previous_version:
        raise QaFailure(
            "previous packaged app version does not match the expected stable version: "
            f"manifest={previous_app_version.strip()}, expected={expected_previous_version}"
        )
    if previous_app_version.strip() == identity.app_version:
        raise QaFailure("upgrade candidate has the same version as the previous packaged app")
    manifest_previous_package_sha256 = manifest.get("previous_package_sha256")
    if (
        not isinstance(manifest_previous_package_sha256, str)
        or expected_previous_package_sha256 is None
        or manifest_previous_package_sha256 != expected_previous_package_sha256
    ):
        raise QaFailure("previous package digest does not match the upgrade manifest")
    previous_revision = manifest.get("alembic_revision")
    if not isinstance(previous_revision, str) or not previous_revision.strip():
        raise QaFailure("upgrade manifest has no previous Alembic revision")
    pre_upgrade_backups = manifest.get("pre_upgrade_schema_backups")
    if not isinstance(pre_upgrade_backups, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("relative_path"), str)
        or not isinstance(item.get("sha256"), str)
        for item in pre_upgrade_backups
    ):
        raise QaFailure("upgrade manifest has an invalid pre-upgrade backup inventory")
    marker = manifest.get("draft_custom_instruction")
    if not isinstance(marker, str) or not marker.startswith("packaged-upgrade:"):
        raise QaFailure("upgrade manifest settings marker is invalid")
    settings = _request_json(
        "GET",
        f"{identity.base_url.rstrip('/')}/api/agent/v1/settings",
        token=identity.access_token,
    )
    if not isinstance(settings, dict) or settings.get("draft_custom_instruction") != marker:
        raise QaFailure("current packaged app did not preserve the previous settings marker")

    professor = manifest.get("professor")
    if not isinstance(professor, dict):
        raise QaFailure("upgrade manifest professor evidence is invalid")
    professor_id = professor.get("id")
    if not isinstance(professor_id, int) or isinstance(professor_id, bool):
        raise QaFailure("upgrade manifest professor id is invalid")
    current_professor = _request_json(
        "GET",
        f"{identity.base_url.rstrip('/')}/api/agent/v1/professors/{professor_id}",
        token=identity.access_token,
    )
    if (
        not isinstance(current_professor, dict)
        or current_professor.get("name") != professor.get("name")
        or current_professor.get("email") != professor.get("email")
    ):
        raise QaFailure("current packaged app did not preserve the previous professor")

    material = manifest.get("material")
    if not isinstance(material, dict):
        raise QaFailure("upgrade manifest material evidence is invalid")
    material_id = material.get("id")
    relative_path = material.get("relative_path")
    expected_sha256 = material.get("sha256")
    expected_bytes = material.get("bytes")
    if (
        not isinstance(material_id, int)
        or isinstance(material_id, bool)
        or not isinstance(relative_path, str)
        or not isinstance(expected_sha256, str)
        or not isinstance(expected_bytes, int)
    ):
        raise QaFailure("upgrade manifest material fields are invalid")
    material_path = (user_data / relative_path).resolve(strict=True)
    try:
        material_path.relative_to(user_data)
    except ValueError as exc:
        raise QaFailure("upgrade manifest material escaped the isolated userData") from exc
    if material_path.is_symlink():
        raise QaFailure("upgrade material must not be a symbolic link")
    actual_sha256 = _sha256_file(material_path)
    actual_bytes = material_path.stat().st_size
    if actual_sha256 != expected_sha256 or actual_bytes != expected_bytes:
        raise QaFailure("previous packaged material bytes changed during in-place upgrade")

    database_path = user_data / DATABASE_NAME
    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        current_revision_row = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        material_row = connection.execute(
            """
            SELECT identity_id, file_path, sha256, size_bytes
            FROM identity_materials WHERE id = ?
            """,
            (material_id,),
        ).fetchone()
    finally:
        connection.close()
    if current_revision_row is None:
        raise QaFailure("upgraded database has no Alembic revision")
    if material_row is None:
        raise QaFailure("upgraded database lost the previous identity material record")
    if (
        int(material_row["identity_id"]) != material.get("identity_id")
        or str(material_row["sha256"]) != expected_sha256
        or int(material_row["size_bytes"]) != expected_bytes
        or Path(str(material_row["file_path"])).resolve() != material_path
    ):
        raise QaFailure("upgraded identity material record no longer matches its file")

    current_revision = str(current_revision_row["version_num"])
    expected_current_revision = (
        _repository_alembic_head(repository_root) if require_repository_head else None
    )
    if expected_current_revision is not None and current_revision != expected_current_revision:
        raise QaFailure(
            "upgraded database revision does not match repository head: "
            f"database={current_revision}, repository={expected_current_revision}"
        )
    backup_evidence: dict[str, object] | None = None
    if previous_revision != current_revision:
        backups = sorted((user_data / "backups" / "schema").glob("*.db"))
        previous_backup_signatures = {
            (str(item["relative_path"]), str(item["sha256"]))
            for item in pre_upgrade_backups
        }
        new_backups = [
            backup
            for backup in backups
            if backup.is_file()
            and not backup.is_symlink()
            and (
                backup.relative_to(user_data).as_posix(),
                _sha256_file(backup),
            )
            not in previous_backup_signatures
        ]
        if not new_backups:
            raise QaFailure(
                "schema-changing packaged upgrade created no new migration backup"
            )
        backup_path = new_backups[-1]
        backup_connection = _open_sqlite_read_only(backup_path, timeout=10)
        try:
            integrity = str(backup_connection.execute("PRAGMA integrity_check").fetchone()[0])
            backup_revision_row = backup_connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
        finally:
            backup_connection.close()
        if integrity != "ok":
            raise QaFailure("packaged upgrade migration backup is not readable")
        backup_revision = (
            str(backup_revision_row[0]) if backup_revision_row is not None else None
        )
        if backup_revision != previous_revision:
            raise QaFailure(
                "packaged upgrade backup is not the previous schema revision"
            )
        backup_evidence = {
            "path": str(backup_path),
            "sha256": _sha256_file(backup_path),
            "integrity_check": integrity,
            "backup_count": len(backups),
            "new_backup_count": len(new_backups),
            "alembic_revision": backup_revision,
        }
    audit = audit_database(database_path)
    if not audit.passed:
        raise QaFailure("upgraded database failed integrity or business invariant audit")
    return {
        "previous_app_version": previous_app_version,
        "current_app_version": identity.app_version,
        "previous_runtime_id": manifest.get("previous_runtime_id"),
        "previous_artifact_sha256": manifest.get("previous_artifact_sha256"),
        "previous_package_sha256": manifest_previous_package_sha256,
        "previous_database_sha256": manifest.get("database_sha256"),
        "current_database_sha256": _sha256_file(database_path),
        "previous_alembic_revision": previous_revision,
        "current_alembic_revision": current_revision,
        "expected_alembic_revision": expected_current_revision,
        "repository_head_required": require_repository_head,
        "settings_marker_preserved": True,
        "professor_id": professor_id,
        "material_id": material_id,
        "material_sha256": actual_sha256,
        "material_bytes": actual_bytes,
        "migration_backup": backup_evidence,
    }


def _repository_alembic_head(repository_root: Path) -> str:
    config_path = repository_root / "backend" / "alembic.ini"
    if not config_path.is_file():
        raise QaFailure(f"repository Alembic configuration is missing: {config_path}")
    config = AlembicConfig(str(config_path))
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()
    if len(heads) != 1:
        raise QaFailure(f"repository must have exactly one Alembic head; found {heads}")
    return str(heads[0])


def _start_browser_probe() -> BrowserProbe:
    browser_request_started = threading.Event()
    release_response = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            browser_request_started.set()
            release_response.wait(timeout=180)
            self._write(
                200,
                "<html><body><main><div class='teacher-list'>"
                "<a href='/profile'>Professor QA</a></div></main></body></html>",
                "text/html; charset=utf-8",
            )

        def log_message(self, format: str, *args: object) -> None:
            _ = format, args

        def _write(
            self,
            status: int,
            body_text: str,
            content_type: str = "text/plain; charset=utf-8",
        ) -> None:
            body = body_text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    port = int(server.server_address[1])
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        name=f"packaged-qa-browser-probe-{port}",
        daemon=True,
    )
    thread.start()
    llm_server = _load_workload_support().fake_llm_server(
        response_factory=WorkloadHarness._llm_response,
    ).start()
    return BrowserProbe(
        server=server,
        thread=thread,
        llm_server=llm_server,
        browser_request_started=browser_request_started,
        release_response=release_response,
        port=port,
    )


def _exercise_real_browser_descendant(
    identity: RuntimeIdentity,
    probe: BrowserProbe,
    recorder: EvidenceRecorder,
) -> set[int]:
    if identity.worker is None:
        raise QaFailure("browser process probe requires split mode")
    profile_id = _seed_browser_probe_llm_profile(
        recorder.paths.user_data / DATABASE_NAME,
        llm_base_url=probe.llm_server.base_url,
    )
    payload = {
        "university": "Packaged QA University",
        "school": "Process Tree School",
        "start_url": probe.url,
        "start_urls": [probe.url],
        "entry_type": "list",
        "llm_profile_id": profile_id,
    }
    created = _request_json(
        "POST",
        f"{identity.base_url.rstrip('/')}/api/agent/v1/crawler/jobs",
        token=identity.access_token,
        payload=payload,
        headers={"Idempotency-Key": f"packaged-browser-{uuid.uuid4()}"},
        timeout_seconds=20,
    )
    if not isinstance(created, dict) or not isinstance(created.get("id"), int):
        raise QaFailure("browser probe crawler job was not created")
    def find_browser_pids() -> set[int] | None:
        pids = _browser_descendant_pids(identity.worker.pid)
        return pids or None

    browser_pids = _wait_until(
        find_browser_pids,
        timeout_seconds=120,
        description="packaged Chromium descendants",
    )
    recorder.event(
        "packaged_browser_descendants_started",
        job_id=created["id"],
        browser_pids=sorted(browser_pids),
        probe_request_seen=probe.browser_request_started.is_set(),
    )
    return browser_pids


def _seed_browser_probe_llm_profile(database_path: Path, *, llm_base_url: str) -> int:
    connection = sqlite3.connect(database_path, timeout=10)
    try:
        suffix = uuid.uuid4().hex
        now = datetime.now(UTC).replace(tzinfo=None)
        future = now + timedelta(days=1)
        model_name = "packaged-browser-probe-model"
        profile_id = int(
            connection.execute(
                """
                INSERT INTO llm_profiles (
                    name, provider, api_base_url, api_key, model_name, is_default
                ) VALUES (?, 'openai', ?, 'packaged-browser-probe-key', ?, 0)
                """,
                (f"Packaged browser probe {suffix}", llm_base_url, model_name),
            ).lastrowid
        )
        connection.execute(
            """
            INSERT INTO llm_endpoint_adaptation_cache (
                api_base_url, model_name, learned_endpoint_kind, probed_at
            ) VALUES (?, ?, 'chat_completions', ?)
            """,
            (llm_base_url, model_name, now.isoformat(sep=" ")),
        )
        connection.execute(
            """
            INSERT INTO thinking_adaptation_cache (
                api_base_url, model_name, endpoint_kind,
                learned_extra_body, probed_at
            ) VALUES (?, ?, 'chat_completions', 'null', ?)
            """,
            (llm_base_url, model_name, now.isoformat(sep=" ")),
        )
        connection.execute(
            """
            INSERT INTO llm_structured_output_adaptation_cache (
                api_base_url, model_name, endpoint_kind, probe_version,
                learned_mode, probed_at, expires_at
            ) VALUES (?, ?, 'chat_completions', 3, 'prompt_only', ?, ?)
            """,
            (
                llm_base_url,
                model_name,
                now.isoformat(sep=" "),
                future.isoformat(sep=" "),
            ),
        )
        connection.commit()
        return profile_id
    finally:
        connection.close()


def audit_database(database_path: Path) -> DatabaseAudit:
    if not database_path.is_file():
        raise QaFailure(f"SQLite database is missing: {database_path}")
    connection = sqlite3.connect(database_path, timeout=30)
    try:
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        quick = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
        foreign_keys = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        violations: list[str] = []
        nonnegative_candidates = {
            "email_tasks": ("retry_count",),
            "batch_tasks": ("progress_current", "progress_total"),
            "crawl_jobs": ("progress_current", "progress_total"),
            "match_analysis_jobs": ("progress_current", "progress_total"),
        }
        for table, candidates in nonnegative_candidates.items():
            if table not in tables:
                continue
            columns = {
                str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
            }
            for column in candidates:
                if column not in columns:
                    continue
                count = int(
                    connection.execute(
                        f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" < 0'
                    ).fetchone()[0]
                )
                if count:
                    violations.append(f"{table}.{column} has {count} negative rows")
        if "email_delivery_attempts" in tables:
            duplicate_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM ("
                    "SELECT email_task_id FROM email_delivery_attempts "
                    "WHERE outcome != 'pre_submission_failed' "
                    "GROUP BY email_task_id HAVING COUNT(*) > 1"
                    ")"
                ).fetchone()[0]
            )
            if duplicate_count:
                violations.append(
                    "email_delivery_attempts has "
                    f"{duplicate_count} tasks with multiple irreversible claims"
                )
        if "crawl_page_tasks" in tables:
            duplicate_owner_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM ("
                    "SELECT job_id, normalized_url FROM crawl_page_tasks "
                    "GROUP BY job_id, normalized_url HAVING COUNT(*) > 1"
                    ")"
                ).fetchone()[0]
            )
            if duplicate_owner_count:
                violations.append(
                    f"crawl_page_tasks has {duplicate_owner_count} duplicate URL claims"
                )
    finally:
        connection.close()
    return DatabaseAudit(
        at=_utc_now(),
        integrity_check=integrity,
        quick_check=quick,
        foreign_key_violations=foreign_keys,
        invariant_violations=violations,
        wal_bytes=_file_size(database_path.with_name(f"{database_path.name}-wal")),
        shm_bytes=_file_size(database_path.with_name(f"{database_path.name}-shm")),
    )


def _assert_database_audit(
    paths: QaPaths,
    recorder: EvidenceRecorder,
    *,
    phase: str,
) -> None:
    audit = audit_database(paths.user_data / DATABASE_NAME)
    payload = {"phase": phase, **asdict(audit), "passed": audit.passed}
    recorder.report.setdefault("database_audits", []).append(payload)
    recorder.check(f"database_audit:{phase}", passed=audit.passed, evidence=payload)


def _collect_resource_sample(
    identity: RuntimeIdentity,
    user_data: Path,
    started_monotonic: float,
) -> dict[str, object]:
    role_pids = {
        "desktop": identity.desktop.pid,
        "api": identity.backend.pid,
        **({"worker": identity.worker.pid} if identity.worker is not None else {}),
    }
    roles = {
        role: _process_resource_payload(pid, user_data)
        for role, pid in role_pids.items()
    }
    browser_pids = (
        sorted(_browser_descendant_pids(identity.worker.pid))
        if identity.worker is not None
        else []
    )
    payload: dict[str, object] = {
        "at": _utc_now(),
        "elapsed_seconds": time.monotonic() - started_monotonic,
        "runtime_id": identity.runtime_id,
        "roles": roles,
        "browser_pids": browser_pids,
        "runtime_file_count": _file_count(user_data / "runtime"),
        "status_bytes": _directory_size(user_data / "runtime"),
        "log_bytes": _directory_size(user_data / "logs"),
        "database_bytes": _file_size(user_data / DATABASE_NAME),
        "wal_bytes": _file_size(user_data / f"{DATABASE_NAME}-wal"),
    }
    return payload


def _process_resource_payload(pid: int, user_data: Path) -> dict[str, object]:
    process = psutil.Process(pid)
    with process.oneshot():
        memory = process.memory_info()
        try:
            handles = process.num_handles() if sys.platform == "win32" else process.num_fds()
        except (psutil.AccessDenied, AttributeError):
            handles = None
        try:
            connections = process.net_connections(kind="inet")
            connection_count: int | None = len(connections)
            listening_count: int | None = sum(
                connection.status == psutil.CONN_LISTEN for connection in connections
            )
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            connection_count = None
            listening_count = None
        try:
            open_files = process.open_files()
            database_file_count: int | None = sum(
                Path(item.path).name.startswith(DATABASE_NAME) for item in open_files
            )
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            database_file_count = None
        return {
            "pid": pid,
            "rss_bytes": memory.rss,
            "vms_bytes": memory.vms,
            "handles": handles,
            "inet_connections": connection_count,
            "listening_sockets": listening_count,
            "database_open_files": database_file_count,
            "child_count": len(process.children(recursive=True)),
            "user_data_bytes": _directory_size(user_data),
        }


def _summarize_resource_samples(samples: list[dict[str, Any]]) -> dict[str, object]:
    summary: dict[str, object] = {
        "sample_count": len(samples),
        "roles": {},
        "files": {},
        "violations": [],
        "thresholds": {
            "rss_growth_bytes": 128 * 1024 * 1024,
            "handle_growth": 64,
            "connection_growth": 16,
            "minimum_r_squared": 0.80,
            "maximum_log_bytes": 512 * 1024 * 1024,
            "maximum_status_bytes": 16 * 1024 * 1024,
        },
    }
    if not samples:
        summary["violations"] = ["no resource samples were recorded"]
        return summary
    elapsed = [float(sample.get("elapsed_seconds", 0)) for sample in samples]
    violations: list[str] = []
    role_names = sorted(
        {
            role
            for sample in samples
            for role in sample.get("roles") or {}
        }
    )
    role_summary: dict[str, object] = {}
    for role in role_names:
        metrics: dict[str, object] = {}
        for metric, growth_threshold in (
            ("rss_bytes", 128 * 1024 * 1024),
            ("handles", 64),
            ("inet_connections", 16),
            ("database_open_files", 8),
            ("child_count", 0),
        ):
            values = [
                (sample.get("roles") or {}).get(role, {}).get(metric)
                for sample in samples
            ]
            numeric_pairs = [
                (x, float(value))
                for x, value in zip(elapsed, values, strict=True)
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            ]
            if not numeric_pairs:
                metrics[metric] = {"available": False}
                continue
            x_values = [pair[0] for pair in numeric_pairs]
            y_values = [pair[1] for pair in numeric_pairs]
            trend = _linear_trend(x_values, y_values)
            growth = y_values[-1] - y_values[0]
            metric_summary = {
                "available": True,
                "minimum": min(y_values),
                "maximum": max(y_values),
                "first": y_values[0],
                "last": y_values[-1],
                "growth": growth,
                **trend,
            }
            metrics[metric] = metric_summary
            if (
                len(y_values) >= 30
                and growth > growth_threshold
                and trend["slope_per_hour"] > 0
                and trend["r_squared"] >= 0.80
            ):
                violations.append(
                    f"{role}.{metric} grew monotonically by {growth:.0f} "
                    f"with r_squared={trend['r_squared']:.3f}"
                )
        role_summary[role] = metrics
    summary["roles"] = role_summary
    log_values = [int(sample.get("log_bytes", 0)) for sample in samples]
    status_values = [int(sample.get("status_bytes", 0)) for sample in samples]
    runtime_file_counts = [int(sample.get("runtime_file_count", 0)) for sample in samples]
    browser_counts = [len(sample.get("browser_pids") or []) for sample in samples]
    summary["files"] = {
        "log_bytes_min": min(log_values),
        "log_bytes_max": max(log_values),
        "log_bytes_trend": _linear_trend(elapsed, [float(value) for value in log_values]),
        "status_bytes_min": min(status_values),
        "status_bytes_max": max(status_values),
        "status_bytes_trend": _linear_trend(
            elapsed,
            [float(value) for value in status_values],
        ),
        "runtime_file_count_min": min(runtime_file_counts),
        "runtime_file_count_max": max(runtime_file_counts),
        "runtime_file_count_last": runtime_file_counts[-1],
        "browser_descendant_count_max": max(browser_counts),
        "browser_descendant_count_last": browser_counts[-1],
    }
    if max(log_values) > 512 * 1024 * 1024:
        violations.append("production logs exceeded 512 MiB")
    if max(status_values) > 16 * 1024 * 1024:
        violations.append("runtime status files exceeded 16 MiB")
    if max(runtime_file_counts) > 8 or runtime_file_counts[-1] - runtime_file_counts[0] > 2:
        violations.append("runtime status file count accumulated across the soak")
    if max(browser_counts) > 0:
        violations.append("Playwright browser descendants remained at a resource sample")
    summary["violations"] = violations
    return summary


def _linear_trend(x_values: list[float], y_values: list[float]) -> dict[str, float]:
    if len(x_values) < 2 or math.isclose(max(x_values), min(x_values)):
        return {"slope_per_hour": 0.0, "r_squared": 0.0}
    x_mean = statistics.fmean(x_values)
    y_mean = statistics.fmean(y_values)
    denominator = sum((value - x_mean) ** 2 for value in x_values)
    slope_per_second = sum(
        (x_value - x_mean) * (y_value - y_mean)
        for x_value, y_value in zip(x_values, y_values, strict=True)
    ) / denominator
    intercept = y_mean - slope_per_second * x_mean
    residual = sum(
        (y_value - (intercept + slope_per_second * x_value)) ** 2
        for x_value, y_value in zip(x_values, y_values, strict=True)
    )
    total = sum((value - y_mean) ** 2 for value in y_values)
    r_squared = 0.0 if math.isclose(total, 0.0) else max(0.0, 1 - residual / total)
    return {
        "slope_per_hour": slope_per_second * 3600,
        "r_squared": r_squared,
    }


def _exercise_sqlite_lock(database_path: Path, action: Callable[[], T]) -> T:
    connection = sqlite3.connect(database_path, timeout=5, isolation_level=None)
    result: list[T] = []
    failure: list[BaseException] = []

    def run_action() -> None:
        try:
            result.append(action())
        except BaseException as exc:  # noqa: BLE001 - propagated after releasing the lock
            failure.append(exc)

    try:
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("BEGIN IMMEDIATE")
        thread = threading.Thread(target=run_action, name="packaged-qa-lock-write")
        thread.start()
        time.sleep(2)
        connection.execute("COMMIT")
        thread.join(timeout=30)
        if thread.is_alive():
            raise QaFailure("API write did not settle after releasing the SQLite lock")
        if failure:
            raise failure[0]
        if not result:
            raise QaFailure("SQLite lock action produced no result")
        return result[0]
    finally:
        with contextlib.suppress(sqlite3.Error):
            connection.execute("ROLLBACK")
        connection.close()


def _assert_worker_has_no_listener(identity: RuntimeIdentity) -> None:
    if identity.worker is None:
        raise QaFailure("Worker listener audit requires split mode")
    try:
        connections = psutil.Process(identity.worker.pid).net_connections(kind="inet")
    except psutil.AccessDenied as exc:
        raise QaFailure("cannot inspect Worker network sockets") from exc
    listeners = [connection for connection in connections if connection.status == psutil.CONN_LISTEN]
    if listeners:
        raise QaFailure(f"Worker unexpectedly listens on {len(listeners)} socket(s)")


def _runtime_process_tree_pids(
    identity: RuntimeIdentity | None,
    desktop_pid: int,
) -> set[int]:
    roots = {desktop_pid}
    if identity is not None:
        roots.add(identity.backend.pid)
        if identity.worker is not None:
            roots.add(identity.worker.pid)
    return _process_tree_pids(roots)


def _process_tree_pids(roots: set[int]) -> set[int]:
    pids = set(roots)
    for root in roots:
        try:
            pids.update(child.pid for child in psutil.Process(root).children(recursive=True))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return pids


def _require_retired_process_tree_gone(
    pids: set[int],
    *,
    recorder: EvidenceRecorder,
    action: str,
    index: int,
) -> None:
    try:
        _wait_for_pids_gone(pids, timeout_seconds=15)
    except QaFailure:
        _kill_pids(pids)
        raise
    recorder.check(
        f"retired_process_tree_cleanup:{action}:{index}",
        passed=True,
        evidence={"action": action, "index": index, "retired_pids": sorted(pids)},
    )


def _browser_descendant_pids(worker_pid: int) -> set[int]:
    try:
        descendants = psutil.Process(worker_pid).children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return set()
    result: set[int] = set()
    for process in descendants:
        try:
            name = process.name().lower()
            cmdline = " ".join(process.cmdline()).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if (
            "chrom" in name
            or "headless_shell" in name
            or "ms-playwright" in cmdline
            or "chromium" in cmdline
        ):
            result.add(process.pid)
    return result


def _request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    payload: object | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 10,
) -> object:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise QaFailure(f"QA HTTP client refuses non-loopback URL: {url}")
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {
        "Accept": "application/json",
        **({"Authorization": f"Bearer {token}"} if token else {}),
        **({"Content-Type": "application/json"} if body is not None else {}),
        **(headers or {}),
    }
    connection = http.client.HTTPConnection(
        parsed.hostname,
        parsed.port,
        timeout=timeout_seconds,
    )
    try:
        path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
    finally:
        connection.close()
    try:
        decoded = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QaFailure(f"{method} {parsed.path} returned non-JSON HTTP {response.status}") from exc
    if response.status < 200 or response.status >= 300:
        raise QaFailure(
            f"{method} {parsed.path} returned HTTP {response.status}: "
            f"{_redact_payload(decoded)}"
        )
    return decoded


def _wait_until(
    probe: Callable[[], T | None],
    *,
    timeout_seconds: float,
    description: str,
    process: subprocess.Popen[bytes] | None = None,
    stderr_path: Path | None = None,
) -> T:
    deadline = time.monotonic() + timeout_seconds
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            stderr = _tail_text(stderr_path) if stderr_path is not None else ""
            raise QaFailure(
                f"process exited with code {process.returncode} while waiting for {description}; "
                f"stderr tail={stderr!r}"
            )
        try:
            value = probe()
            if value is not None:
                return value
        except QaFailure:
            raise
        except BaseException as exc:  # noqa: BLE001 - retry probe and preserve last cause
            last_error = exc
        time.sleep(0.1)
    suffix = f"; last error={last_error}" if last_error is not None else ""
    raise QaFailure(f"timed out waiting for {description}{suffix}")


def _wait_for_pids_gone(pids: set[int], *, timeout_seconds: float) -> None:
    remaining = _wait_until(
        lambda: True if not {pid for pid in pids if _pid_is_running(pid)} else None,
        timeout_seconds=timeout_seconds,
        description=f"processes to exit: {sorted(pids)}",
    )
    _ = remaining


def _pid_is_running(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied:
        return True


def _process_started_after(pid: int, launched_at_wall: float) -> bool:
    try:
        return psutil.Process(pid).create_time() >= launched_at_wall - 2
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _request_desktop_stop(
    pid: int,
    *,
    timeout_seconds: float = 20.0,
    retry_interval_seconds: float = 0.05,
) -> None:
    if sys.platform == "win32":
        deadline = time.monotonic() + timeout_seconds
        posted_to_target_window = False
        while _pid_is_running(pid):
            posted_to_target_window = (
                _post_windows_graceful_quit(pid) or posted_to_target_window
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if posted_to_target_window:
                    raise QaFailure(
                        "desktop did not exit after packaged QA posted a graceful quit message"
                    )
                raise QaFailure("packaged QA could not find a target desktop window for graceful exit")
            time.sleep(min(retry_interval_seconds, remaining))
        return
    os.kill(pid, signal.SIGTERM)


def _post_windows_graceful_quit(pid: int) -> bool:
    posted = False
    for window_handle in _windows_top_level_windows():
        if _windows_window_process_id(window_handle) != pid:
            continue
        if _post_windows_message(window_handle, QA_GRACEFUL_QUIT_MESSAGE):
            posted = True
    return posted


def _windows_top_level_windows() -> list[int]:
    import ctypes
    from ctypes import wintypes

    handles: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )

    @callback_type
    def collect(window_handle: int, _parameter: int) -> bool:
        handles.append(int(window_handle))
        return True

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    if not user32.EnumWindows(collect, 0):
        error_code = ctypes.get_last_error()
        raise QaFailure(f"EnumWindows failed with Windows error {error_code}")
    return handles


def _windows_window_process_id(window_handle: int) -> int | None:
    import ctypes
    from ctypes import wintypes

    process_id = wintypes.DWORD()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    if user32.GetWindowThreadProcessId(window_handle, ctypes.byref(process_id)) == 0:
        return None
    return int(process_id.value)


def _post_windows_message(window_handle: int, message: int) -> bool:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL
    return bool(user32.PostMessageW(window_handle, message, 0, 0))


def _kill_pids(pids: set[int]) -> None:
    processes: list[psutil.Process] = []
    for pid in sorted(pids, reverse=True):
        try:
            processes.append(psutil.Process(pid))
        except psutil.NoSuchProcess:
            continue
    for process in processes:
        with contextlib.suppress(psutil.Error):
            process.kill()
    psutil.wait_procs(processes, timeout=10)


def _safe_cmdline(pid: int) -> list[str]:
    try:
        return psutil.Process(pid).cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        raise QaFailure(f"cannot read process command line for pid {pid}") from exc


def _assert_clean_revision(repository_root: Path, expected_revision: str) -> None:
    if not (repository_root / ".git").exists():
        # Worktrees use a .git file, which still satisfies exists().
        raise QaFailure(f"repository root is invalid: {repository_root}")
    revision = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip().lower()
    if revision != expected_revision:
        raise QaFailure(
            f"repository revision {revision} does not match {expected_revision}"
        )
    status = subprocess.run(
        ["git", "-C", str(repository_root), "status", "--porcelain"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    if status:
        raise QaFailure("certification requires a completely clean worktree")


def _set_clock_offset(path: Path, seconds: float) -> None:
    temporary = path.with_suffix(f".tmp-{uuid.uuid4().hex}")
    temporary.write_text(str(float(seconds)), encoding="utf-8")
    temporary.replace(path)


def _parse_process_identity(value: object, field_name: str) -> ProcessIdentity:
    if not isinstance(value, dict):
        raise QaFailure(f"runtime descriptor {field_name} must be an object")
    pid = value.get("pid")
    started_at = value.get("started_at")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise QaFailure(f"runtime descriptor {field_name}.pid is invalid")
    if not isinstance(started_at, str) or not started_at.strip():
        raise QaFailure(f"runtime descriptor {field_name}.started_at is invalid")
    return ProcessIdentity(pid=pid, started_at=started_at.strip())


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise QaFailure(f"cannot read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise QaFailure(f"JSON file is not an object: {path}")
    return value


def _write_json_atomic(path: Path, payload: object, *, mode: int = 0o600) -> None:
    filesystem_path = _extended_length_path(path) if os.name == "nt" else path
    filesystem_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = filesystem_path.with_name(f".tmp-{uuid.uuid4().hex[:16]}")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
        with contextlib.suppress(OSError):
            temporary.chmod(mode)
        temporary.replace(filesystem_path)
    finally:
        temporary.unlink(missing_ok=True)


def _append_json_line(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        file.write("\n")


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    result: list[dict[str, Any]] = []
    for line in lines:
        value = json.loads(line)
        if isinstance(value, dict):
            result.append(value)
    return result


def _redact_payload(value: object) -> object:
    secret_names = {
        "access_token",
        "agent_token",
        "ui_token",
        "authorization",
        "password",
        "api_key",
    }
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if str(key).lower() in secret_names
                else _redact_payload(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_redact_payload(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    filesystem_path = _extended_length_path(path) if os.name == "nt" else path
    with filesystem_path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_tree(path: Path) -> dict[str, object]:
    """Hash a packaged artifact using relative names, types, modes, and bytes."""

    resolved = path.resolve()
    root = _extended_length_path(resolved) if os.name == "nt" else resolved
    candidates = [root] if root.is_file() else sorted(root.rglob("*"))
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for candidate in candidates:
        relative = candidate.name if root.is_file() else candidate.relative_to(root).as_posix()
        stat = candidate.lstat()
        if candidate.is_symlink():
            kind = "symlink"
            content = os.readlink(candidate).encode("utf-8")
        elif candidate.is_file():
            kind = "file"
            file_count += 1
            total_bytes += stat.st_size
            content_digest = hashlib.sha256()
            with candidate.open("rb") as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b""):
                    content_digest.update(chunk)
            content = content_digest.digest()
        elif candidate.is_dir():
            kind = "directory"
            content = b""
        else:
            kind = "other"
            content = b""
        header = f"{kind}\0{relative}\0{stat.st_mode & 0o7777:o}\0".encode()
        digest.update(header)
        digest.update(content)
        digest.update(b"\0")
    return {
        "sha256": digest.hexdigest(),
        "file_count": file_count,
        "bytes": total_bytes,
    }


def _extended_length_path(path: Path) -> Path:
    if sys.platform != "win32":
        return path
    raw = str(path)
    if raw.startswith("\\\\?\\"):
        return path
    if raw.startswith("\\\\"):
        return Path(f"\\\\?\\UNC\\{raw[2:]}")
    return Path(f"\\\\?\\{raw}")


def _open_sqlite_read_only(path: Path, *, timeout: float) -> sqlite3.Connection:
    filesystem_path = _extended_length_path(path) if sys.platform == "win32" else path
    connection = sqlite3.connect(filesystem_path, timeout=timeout)
    try:
        connection.execute("PRAGMA query_only = ON")
    except BaseException:
        connection.close()
        raise
    return connection


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for candidate in path.rglob("*"):
        try:
            if candidate.is_file():
                total += candidate.stat().st_size
        except OSError:
            continue
    return total


def _file_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for candidate in path.rglob("*") if candidate.is_file())


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _tail_text(path: Path | None, maximum_bytes: int = 8192) -> str:
    if path is None:
        return ""
    try:
        with path.open("rb") as file:
            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(max(0, size - maximum_bytes))
            return file.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
