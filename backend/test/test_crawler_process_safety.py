from __future__ import annotations

import ipaddress
import json
import os
import socket
import sqlite3
import tempfile
import time
import unittest
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psutil

from test.process_harness import (
    DesktopBackendProcess,
    FakeHTTPServer,
    FakeLLMServer,
    FaultController,
    fetch_json,
    patch_json,
    post_json,
    wait_until,
)


_REPETITIONS_ENV = "AUTO_EMAIL_SENDER_CRAWLER_CHAOS_REPETITIONS"
_RESTART_REPETITIONS_ENV = "AUTO_EMAIL_SENDER_WORKER_RESTART_REPETITIONS"
_CRAWL_HOSTNAME = "crawler.test.invalid"
_WORK_TABLES = {
    "page": "crawl_page_tasks",
    "chunk": "crawl_page_chunks",
    "enrichment": "crawl_candidate_enrichment_tasks",
}
_PROCESSING_STATUS = "processing"
_TERMINAL_STATUSES = {
    "page": "succeeded",
    "chunk": "completed",
    "enrichment": "succeeded",
}


@dataclass(frozen=True, slots=True)
class _SeededCrawlerWork:
    kind: str
    job_id: int
    work_item_id: int
    candidate_id: int | None
    run_id: int
    profile_url: str


@dataclass(frozen=True, slots=True)
class _ProcessResourceSample:
    rss_bytes: int
    handle_count: int
    open_file_count: int
    database_file_count: int
    external_inet_connection_count: int


@dataclass(frozen=True, slots=True)
class _SyntheticInetConnection:
    family: int
    type: int
    laddr: object
    raddr: object
    status: str


def _inet_endpoint(
    value: object,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, int] | None:
    if not value:
        return None
    if hasattr(value, "ip") and hasattr(value, "port"):
        host = getattr(value, "ip")
        port = getattr(value, "port")
    elif isinstance(value, (tuple, list)) and len(value) >= 2:
        host, port = value[0], value[1]
    else:
        return None
    try:
        address = ipaddress.ip_address(str(host).split("%", maxsplit=1)[0])
        normalized_port = int(port)
    except (TypeError, ValueError):
        return None
    return address, normalized_port


def _is_internal_loopback_pair(first: Any, second: Any) -> bool:
    if (
        int(first.family) != int(second.family)
        or int(first.type) != int(socket.SOCK_STREAM)
        or int(second.type) != int(socket.SOCK_STREAM)
        or first.status != psutil.CONN_ESTABLISHED
        or second.status != psutil.CONN_ESTABLISHED
    ):
        return False
    first_local = _inet_endpoint(first.laddr)
    first_remote = _inet_endpoint(first.raddr)
    second_local = _inet_endpoint(second.laddr)
    second_remote = _inet_endpoint(second.raddr)
    if None in (first_local, first_remote, second_local, second_remote):
        return False
    assert first_local is not None
    assert first_remote is not None
    assert second_local is not None
    assert second_remote is not None
    if not all(
        endpoint[0].is_loopback
        for endpoint in (
            first_local,
            first_remote,
            second_local,
            second_remote,
        )
    ):
        return False
    return first_local == second_remote and first_remote == second_local


def _external_inet_connection_count(connections: Sequence[Any]) -> int:
    internal_connection_indexes: set[int] = set()
    for first_index, first in enumerate(connections):
        if first_index in internal_connection_indexes:
            continue
        for second_index in range(first_index + 1, len(connections)):
            if second_index in internal_connection_indexes:
                continue
            if _is_internal_loopback_pair(first, connections[second_index]):
                internal_connection_indexes.update((first_index, second_index))
                break
    return len(connections) - len(internal_connection_indexes)


class CrawlerProcessSafetyTests(unittest.TestCase):
    def test_windows_proactor_loopback_pair_is_not_external(self) -> None:
        connections = [
            self._synthetic_connection(
                local=("127.0.0.1", 56846),
                remote=("127.0.0.1", 56845),
            ),
            self._synthetic_connection(
                local=("127.0.0.1", 56845),
                remote=("127.0.0.1", 56846),
            ),
        ]
        self.assertEqual(_external_inet_connection_count(connections), 0)

    def test_one_way_loopback_connection_remains_external(self) -> None:
        connections = [
            self._synthetic_connection(
                local=("127.0.0.1", 56846),
                remote=("127.0.0.1", 8010),
            )
        ]
        self.assertEqual(_external_inet_connection_count(connections), 1)

    def test_listener_and_non_loopback_pair_remain_external(self) -> None:
        connections = [
            self._synthetic_connection(
                local=("127.0.0.1", 8010),
                remote=(),
                status=psutil.CONN_LISTEN,
            ),
            self._synthetic_connection(
                local=("192.0.2.10", 56846),
                remote=("192.0.2.20", 56845),
            ),
            self._synthetic_connection(
                local=("192.0.2.20", 56845),
                remote=("192.0.2.10", 56846),
            ),
        ]
        self.assertEqual(_external_inet_connection_count(connections), 3)

    def test_page_worker_kill_matrix_converges_with_one_candidate(self) -> None:
        self._exercise_kill_matrix("page")

    def test_chunk_worker_kill_matrix_converges_with_one_candidate(self) -> None:
        self._exercise_kill_matrix("chunk")

    def test_enrichment_worker_kill_matrix_reuses_persisted_profile(self) -> None:
        self._exercise_kill_matrix("enrichment")

    def test_api_cancel_fences_all_returned_crawler_results(self) -> None:
        for repetition in range(1, self._chaos_repetitions() + 1):
            for kind in _WORK_TABLES:
                with self.subTest(repetition=repetition, kind=kind):
                    self._exercise_api_cancel_case(kind)

    def test_replaced_owner_fences_all_late_crawler_results(self) -> None:
        for repetition in range(1, self._chaos_repetitions() + 1):
            for kind in _WORK_TABLES:
                with self.subTest(repetition=repetition, kind=kind):
                    self._exercise_replaced_owner_case(kind)

    def test_enrichment_input_change_rejects_stale_result_and_refetches(self) -> None:
        for repetition in range(1, self._chaos_repetitions() + 1):
            with self.subTest(repetition=repetition):
                self._exercise_enrichment_input_change_case()

    def test_worker_restart_stress_preserves_one_owner_and_bounded_resources(self) -> None:
        self._exercise_worker_restart_stress(self._restart_repetitions())

    def test_http_and_llm_outages_degrade_without_restart_and_recover(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            controller = FaultController(root / "faults")
            http_server = FakeHTTPServer(
                {"/profile": self._profile_html("network-recovery")}
            )
            profile_url = http_server.url("/profile", hostname=_CRAWL_HOSTNAME)
            llm_server = FakeLLMServer(
                response_factory=self._crawler_response_factory(profile_url)
            )
            api = DesktopBackendProcess(
                data_dir=data_dir,
                role="api",
                runtime_id=f"crawler-network-{uuid.uuid4()}",
            )
            worker: DesktopBackendProcess | None = None
            try:
                # Reserve and prove both endpoints first, then remove their
                # listeners before the real Worker starts.
                http_server.start()
                llm_server.start()
                api.start()
                api.wait_ready()
                seeded = self._seed_workload(
                    data_dir / "auto_email_sender.db",
                    kind="page",
                    llm_base_url=llm_server.base_url,
                    profile_url=profile_url,
                )
                http_server.stop()
                llm_server.stop()

                worker = DesktopBackendProcess(
                    data_dir=data_dir,
                    role="worker",
                    runtime_id=api.runtime_id,
                    api_pid=api.process.pid,
                    worker_generation=f"network-{uuid.uuid4()}",
                    extra_env={
                        **self._worker_environment(
                            controller,
                            process_id="crawler-network-worker",
                        ),
                        "LLM_REQUEST_TIMEOUT_SECONDS": "3",
                    },
                ).start()
                worker_status = worker.wait_worker_ready()
                worker_pid = worker.process.pid

                first_failure = wait_until(
                    lambda: self._crawler_retry_state(
                        data_dir / "auto_email_sender.db",
                        seeded,
                        minimum_failures=1,
                    ),
                    timeout_seconds=30,
                    description="crawler HTTP outage retry state",
                )
                self.assertEqual(first_failure["candidate_count"], 0)
                first_degraded = wait_until(
                    lambda: self._worker_subsystem_state(
                        data_dir,
                        prefix="crawler-worker-",
                        degraded=True,
                    ),
                    timeout_seconds=15,
                    description="crawler HTTP outage degraded status",
                )
                self.assertEqual(first_degraded["pid"], worker_pid)

                settings_payload = fetch_json(
                    f"{api.base_url}/api/runtime-settings"
                )
                settings_payload.pop("revision", None)
                settings_payload.pop("updated_at", None)
                settings_payload["draft_custom_instruction"] = (
                    "api-write-during-crawler-network-outage"
                )
                updated = patch_json(
                    f"{api.base_url}/api/runtime-settings",
                    settings_payload,
                )
                self.assertEqual(
                    updated["draft_custom_instruction"],
                    "api-write-during-crawler-network-outage",
                )

                http_server.start()
                second_failure = wait_until(
                    lambda: self._crawler_retry_state(
                        data_dir / "auto_email_sender.db",
                        seeded,
                        minimum_failures=2,
                    ),
                    timeout_seconds=30,
                    description="crawler LLM outage retry state",
                )
                self.assertGreaterEqual(http_server.request_count, 1)
                self.assertEqual(second_failure["candidate_count"], 0)
                second_degraded = wait_until(
                    lambda: self._worker_subsystem_state(
                        data_dir,
                        prefix="crawler-worker-",
                        degraded=True,
                    ),
                    timeout_seconds=15,
                    description="crawler LLM outage degraded status",
                )
                self.assertEqual(second_degraded["pid"], worker_pid)

                llm_server.start()
                final_state = self._wait_for_final_state(
                    data_dir / "auto_email_sender.db",
                    seeded,
                )
                self._assert_final_state(
                    "page",
                    final_state,
                    expected_failure_count=2,
                )
                self.assertGreaterEqual(final_state["attempt_count"], 3)
                recovered_status = wait_until(
                    lambda: self._worker_subsystem_state(
                        data_dir,
                        prefix="crawler-worker-",
                        degraded=False,
                    ),
                    timeout_seconds=20,
                    description="crawler network recovery health",
                )
                self.assertEqual(recovered_status["pid"], worker_pid)
                self.assertEqual(recovered_status["generation"], worker_status["generation"])
                self.assertIsNone(worker.process.poll())
                self.assertEqual(
                    fetch_json(f"{api.base_url}/startup-status")["state"],
                    "ready",
                )
            finally:
                if worker is not None:
                    worker.stop()
                api.stop()
                llm_server.stop()
                http_server.stop()

    def _exercise_kill_matrix(self, kind: str) -> None:
        fault_points = tuple(
            f"crawler_{kind}.{stage}"
            for stage in (
                "before_claim",
                "claim_committed",
                "before_external_call",
                "external_call_returned",
                "before_final_commit",
                "after_final_commit",
            )
        )
        for repetition in range(1, self._chaos_repetitions() + 1):
            for fault_point in fault_points:
                with self.subTest(
                    repetition=repetition,
                    kind=kind,
                    fault_point=fault_point,
                ):
                    self._exercise_kill_case(kind=kind, fault_point=fault_point)

    def _exercise_kill_case(self, *, kind: str, fault_point: str) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            controller = FaultController(root / "faults")
            with FakeHTTPServer({"/profile": self._profile_html("first")}) as http_server:
                profile_url = http_server.url("/profile", hostname=_CRAWL_HOSTNAME)
                with FakeLLMServer(
                    response_factory=self._crawler_response_factory(profile_url)
                ) as llm_server:
                    api = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="api",
                        runtime_id=f"crawler-{kind}-{uuid.uuid4()}",
                    )
                    fault_worker: DesktopBackendProcess | None = None
                    recovery_worker: DesktopBackendProcess | None = None
                    try:
                        api.start()
                        api.wait_ready()
                        seeded = self._seed_workload(
                            data_dir / "auto_email_sender.db",
                            kind=kind,
                            llm_base_url=llm_server.base_url,
                            profile_url=profile_url,
                        )
                        fault_worker = DesktopBackendProcess(
                            data_dir=data_dir,
                            role="worker",
                            runtime_id=api.runtime_id,
                            api_pid=api.process.pid,
                            worker_generation=f"fault-{uuid.uuid4()}",
                            extra_env=self._worker_environment(
                                controller,
                                fault_point=fault_point,
                                process_id=f"crawler-{kind}-fault",
                            ),
                        ).start()
                        fault_worker.wait_worker_ready()
                        controller.wait_for_reached(
                            fault_point,
                            timeout_seconds=30,
                        )

                        fault_state = self._read_state(
                            data_dir / "auto_email_sender.db",
                            seeded,
                        )
                        self._assert_fault_boundary(
                            kind=kind,
                            fault_point=fault_point,
                            state=fault_state,
                            http_count=http_server.request_count,
                            llm_count=llm_server.request_count,
                        )

                        fault_worker.process.kill()
                        fault_worker.process.wait(timeout=10)
                        self.assertEqual(
                            fetch_json(f"{api.base_url}/startup-status")["state"],
                            "ready",
                        )
                        self._move_claim_far_into_future(
                            data_dir / "auto_email_sender.db",
                            seeded,
                        )

                        recovery_worker = DesktopBackendProcess(
                            data_dir=data_dir,
                            role="worker",
                            runtime_id=api.runtime_id,
                            api_pid=api.process.pid,
                            worker_generation=f"recovery-{uuid.uuid4()}",
                            extra_env=self._worker_environment(
                                controller,
                                process_id=f"crawler-{kind}-recovery",
                            ),
                        ).start()
                        recovery_worker.wait_worker_ready()
                        final_state = self._wait_for_final_state(
                            data_dir / "auto_email_sender.db",
                            seeded,
                        )
                        self._assert_final_state(kind, final_state)
                        self._assert_external_counts(
                            kind=kind,
                            fault_point=fault_point,
                            http_count=http_server.request_count,
                            llm_count=llm_server.request_count,
                        )
                        api_job = fetch_json(
                            f"{api.base_url}/api/crawl-jobs/{seeded.job_id}"
                        )
                        self.assertEqual(api_job["status"], "needs_review")
                        self.assertEqual(
                            fetch_json(f"{api.base_url}/startup-status")["state"],
                            "ready",
                        )
                        time.sleep(0.15)
                        stable = self._read_state(
                            data_dir / "auto_email_sender.db",
                            seeded,
                        )
                        self._assert_final_state(kind, stable)
                    finally:
                        if recovery_worker is not None:
                            recovery_worker.stop()
                        if fault_worker is not None:
                            fault_worker.stop()
                        api.stop()

    def _exercise_api_cancel_case(self, kind: str) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            controller = FaultController(root / "faults")
            fault_point = f"crawler_{kind}.external_call_returned"
            with FakeHTTPServer({"/profile": self._profile_html("cancel")}) as http_server:
                profile_url = http_server.url("/profile", hostname=_CRAWL_HOSTNAME)
                with FakeLLMServer(
                    response_factory=self._crawler_response_factory(profile_url)
                ) as llm_server:
                    api = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="api",
                        runtime_id=f"crawler-cancel-{kind}-{uuid.uuid4()}",
                    )
                    worker: DesktopBackendProcess | None = None
                    try:
                        api.start()
                        api.wait_ready()
                        seeded = self._seed_workload(
                            data_dir / "auto_email_sender.db",
                            kind=kind,
                            llm_base_url=llm_server.base_url,
                            profile_url=profile_url,
                        )
                        worker = DesktopBackendProcess(
                            data_dir=data_dir,
                            role="worker",
                            runtime_id=api.runtime_id,
                            api_pid=api.process.pid,
                            worker_generation=f"cancel-{uuid.uuid4()}",
                            extra_env=self._worker_environment(
                                controller,
                                fault_point=fault_point,
                                process_id=f"crawler-{kind}-cancel",
                            ),
                        ).start()
                        worker.wait_worker_ready()
                        reached_path = controller.wait_for_reached(
                            fault_point,
                            timeout_seconds=30,
                        )

                        canceled = post_json(
                            f"{api.base_url}/api/crawl-jobs/{seeded.job_id}/cancel"
                        )
                        self.assertEqual(canceled["status"], "canceled")
                        controller.release(reached_path)
                        wait_until(
                            reached_path.with_suffix(".completed").exists,
                            timeout_seconds=10,
                            description=f"released {kind} cancel fault point",
                        )
                        canceled_state = wait_until(
                            lambda: self._canceled_state_or_none(
                                data_dir / "auto_email_sender.db",
                                seeded,
                            ),
                            timeout_seconds=10,
                            description=f"canceled crawler {kind}",
                        )
                        self.assertEqual(canceled_state["item_status"], "pending")
                        self.assertIsNone(canceled_state["worker_id"])
                        self.assertIsNone(canceled_state["claimed_at"])
                        self.assertIsNone(canceled_state["lease_expires_at"])
                        if kind == "enrichment":
                            self.assertEqual(canceled_state["candidate_count"], 1)
                            self.assertIsNone(canceled_state["candidate_email"])
                            self.assertEqual(http_server.request_count, 1)
                            self.assertEqual(llm_server.request_count, 1)
                        elif kind == "page":
                            self.assertEqual(canceled_state["candidate_count"], 0)
                            self.assertEqual(http_server.request_count, 1)
                            self.assertEqual(llm_server.request_count, 0)
                        else:
                            self.assertEqual(canceled_state["candidate_count"], 0)
                            self.assertEqual(http_server.request_count, 0)
                            self.assertEqual(llm_server.request_count, 1)
                        self.assertEqual(canceled_state["integrity_check"], "ok")
                        self.assertEqual(canceled_state["foreign_key_errors"], 0)
                        self.assertEqual(
                            fetch_json(f"{api.base_url}/startup-status")["state"],
                            "ready",
                        )
                    finally:
                        if worker is not None:
                            worker.stop()
                        api.stop()

    def _exercise_replaced_owner_case(self, kind: str) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            controller = FaultController(root / "faults")
            fault_point = f"crawler_{kind}.external_call_returned"
            with FakeHTTPServer({"/profile": self._profile_html("owner")}) as http_server:
                profile_url = http_server.url("/profile", hostname=_CRAWL_HOSTNAME)
                with FakeLLMServer(
                    response_factory=self._crawler_response_factory(profile_url)
                ) as llm_server:
                    api = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="api",
                        runtime_id=f"crawler-owner-{kind}-{uuid.uuid4()}",
                    )
                    stale_worker: DesktopBackendProcess | None = None
                    recovery_worker: DesktopBackendProcess | None = None
                    try:
                        api.start()
                        api.wait_ready()
                        seeded = self._seed_workload(
                            data_dir / "auto_email_sender.db",
                            kind=kind,
                            llm_base_url=llm_server.base_url,
                            profile_url=profile_url,
                        )
                        stale_worker = DesktopBackendProcess(
                            data_dir=data_dir,
                            role="worker",
                            runtime_id=api.runtime_id,
                            api_pid=api.process.pid,
                            worker_generation=f"stale-{uuid.uuid4()}",
                            extra_env=self._worker_environment(
                                controller,
                                fault_point=fault_point,
                                process_id=f"crawler-{kind}-stale",
                            ),
                        ).start()
                        stale_worker.wait_worker_ready()
                        reached_path = controller.wait_for_reached(
                            fault_point,
                            timeout_seconds=30,
                        )
                        before_replace = self._read_state(
                            data_dir / "auto_email_sender.db",
                            seeded,
                        )
                        old_owner = before_replace["worker_id"]
                        self.assertIsInstance(old_owner, str)
                        replacement_owner = f"manual-replacement:{uuid.uuid4()}"
                        self._replace_owner(
                            data_dir / "auto_email_sender.db",
                            seeded,
                            expected_owner=str(old_owner),
                            replacement_owner=replacement_owner,
                        )

                        controller.release(reached_path)
                        wait_until(
                            reached_path.with_suffix(".completed").exists,
                            timeout_seconds=10,
                            description=f"released stale {kind} result",
                        )
                        time.sleep(0.15)
                        rejected = self._read_state(
                            data_dir / "auto_email_sender.db",
                            seeded,
                        )
                        self.assertEqual(rejected["item_status"], _PROCESSING_STATUS)
                        self.assertEqual(rejected["worker_id"], replacement_owner)
                        if kind == "enrichment":
                            self.assertEqual(rejected["candidate_count"], 1)
                            self.assertIsNone(rejected["candidate_email"])
                            self.assertEqual(rejected["page_count"], 1)
                        else:
                            self.assertEqual(rejected["candidate_count"], 0)
                        self.assertEqual(rejected["integrity_check"], "ok")
                        self.assertEqual(rejected["foreign_key_errors"], 0)

                        stale_worker.process.kill()
                        stale_worker.process.wait(timeout=10)
                        self._move_claim_far_into_future(
                            data_dir / "auto_email_sender.db",
                            seeded,
                        )
                        recovery_worker = DesktopBackendProcess(
                            data_dir=data_dir,
                            role="worker",
                            runtime_id=api.runtime_id,
                            api_pid=api.process.pid,
                            worker_generation=f"replacement-{uuid.uuid4()}",
                            extra_env=self._worker_environment(
                                controller,
                                process_id=f"crawler-{kind}-replacement",
                            ),
                        ).start()
                        recovery_worker.wait_worker_ready()
                        final_state = self._wait_for_final_state(
                            data_dir / "auto_email_sender.db",
                            seeded,
                        )
                        self._assert_final_state(kind, final_state)
                        self._assert_external_counts(
                            kind=kind,
                            fault_point=fault_point,
                            http_count=http_server.request_count,
                            llm_count=llm_server.request_count,
                        )
                    finally:
                        if recovery_worker is not None:
                            recovery_worker.stop()
                        if stale_worker is not None:
                            stale_worker.stop()
                        api.stop()

    def _exercise_enrichment_input_change_case(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            controller = FaultController(root / "faults")
            fault_point = "crawler_enrichment.external_call_returned"
            pages = {
                "/old-profile": self._profile_html("OLD_PROFILE_BODY"),
                "/new-profile": self._profile_html("NEW_PROFILE_BODY"),
            }
            with FakeHTTPServer(pages) as http_server:
                old_url = http_server.url(
                    "/old-profile",
                    hostname=_CRAWL_HOSTNAME,
                )
                new_url = http_server.url(
                    "/new-profile",
                    hostname=_CRAWL_HOSTNAME,
                )
                with FakeLLMServer(
                    response_factory=self._versioned_enrichment_response
                ) as llm_server:
                    api = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="api",
                        runtime_id=f"crawler-input-change-{uuid.uuid4()}",
                    )
                    stale_worker: DesktopBackendProcess | None = None
                    recovery_worker: DesktopBackendProcess | None = None
                    try:
                        api.start()
                        api.wait_ready()
                        seeded = self._seed_workload(
                            data_dir / "auto_email_sender.db",
                            kind="enrichment",
                            llm_base_url=llm_server.base_url,
                            profile_url=old_url,
                        )
                        stale_worker = DesktopBackendProcess(
                            data_dir=data_dir,
                            role="worker",
                            runtime_id=api.runtime_id,
                            api_pid=api.process.pid,
                            worker_generation=f"old-input-{uuid.uuid4()}",
                            extra_env=self._worker_environment(
                                controller,
                                fault_point=fault_point,
                                process_id="crawler-enrichment-old-input",
                            ),
                        ).start()
                        stale_worker.wait_worker_ready()
                        reached_path = controller.wait_for_reached(
                            fault_point,
                            timeout_seconds=30,
                        )
                        old_state = self._read_state(
                            data_dir / "auto_email_sender.db",
                            seeded,
                        )
                        self.assertEqual(old_state["candidate_profile_url"], old_url)
                        self.assertIsNone(old_state["candidate_email"])
                        self.assertEqual(http_server.requests, ("/old-profile",))
                        self.assertEqual(llm_server.request_count, 1)

                        replacement_owner = f"new-input-owner:{uuid.uuid4()}"
                        self._replace_enrichment_input(
                            data_dir / "auto_email_sender.db",
                            seeded,
                            expected_owner=str(old_state["worker_id"]),
                            replacement_owner=replacement_owner,
                            profile_url=new_url,
                        )
                        controller.release(reached_path)
                        wait_until(
                            reached_path.with_suffix(".completed").exists,
                            timeout_seconds=10,
                            description="released stale enrichment input result",
                        )
                        time.sleep(0.15)
                        rejected = self._read_state(
                            data_dir / "auto_email_sender.db",
                            seeded,
                        )
                        self.assertEqual(rejected["worker_id"], replacement_owner)
                        self.assertEqual(rejected["candidate_profile_url"], new_url)
                        self.assertIsNone(rejected["candidate_email"])

                        stale_worker.process.kill()
                        stale_worker.process.wait(timeout=10)
                        self._move_claim_far_into_future(
                            data_dir / "auto_email_sender.db",
                            seeded,
                        )
                        recovery_worker = DesktopBackendProcess(
                            data_dir=data_dir,
                            role="worker",
                            runtime_id=api.runtime_id,
                            api_pid=api.process.pid,
                            worker_generation=f"new-input-{uuid.uuid4()}",
                            extra_env=self._worker_environment(
                                controller,
                                process_id="crawler-enrichment-new-input",
                            ),
                        ).start()
                        recovery_worker.wait_worker_ready()
                        final_state = self._wait_for_final_state(
                            data_dir / "auto_email_sender.db",
                            seeded,
                        )
                        self._assert_final_state(
                            "enrichment",
                            final_state,
                            expected_email="new-profile@example.edu",
                        )
                        self.assertEqual(
                            final_state["candidate_profile_url"],
                            new_url,
                        )
                        self.assertEqual(
                            http_server.requests,
                            ("/old-profile", "/new-profile"),
                        )
                        self.assertEqual(llm_server.request_count, 2)
                    finally:
                        if recovery_worker is not None:
                            recovery_worker.stop()
                        if stale_worker is not None:
                            stale_worker.stop()
                        api.stop()

    def _exercise_worker_restart_stress(self, repetitions: int) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            controller = FaultController(root / "faults")
            fault_point = "crawler_page.before_external_call"
            with FakeHTTPServer({"/profile": self._profile_html("RESTART")}) as http_server:
                profile_url = http_server.url("/profile", hostname=_CRAWL_HOSTNAME)
                with FakeLLMServer(
                    response_factory=self._crawler_response_factory(profile_url)
                ) as llm_server:
                    api = DesktopBackendProcess(
                        data_dir=data_dir,
                        role="api",
                        runtime_id=f"worker-restart-stress-{uuid.uuid4()}",
                    )
                    current_worker: DesktopBackendProcess | None = None
                    final_worker: DesktopBackendProcess | None = None
                    worker_samples: list[_ProcessResourceSample] = []
                    api_samples: list[_ProcessResourceSample] = []
                    worker_pid_sequence: list[int] = []
                    claim_owners: set[str] = set()
                    try:
                        api.start()
                        api.wait_ready()
                        seeded = self._seed_workload(
                            data_dir / "auto_email_sender.db",
                            kind="page",
                            llm_base_url=llm_server.base_url,
                            profile_url=profile_url,
                        )
                        baseline_api = self._process_resource_sample(
                            api.process.pid,
                            data_dir / "auto_email_sender.db",
                        )
                        baseline_runtime_files = self._bounded_file_count(
                            data_dir / "runtime"
                        )
                        runtime_settings_payload = fetch_json(
                            f"{api.base_url}/api/runtime-settings"
                        )
                        runtime_settings_payload.pop("revision", None)
                        runtime_settings_payload.pop("updated_at", None)

                        for generation_index in range(1, repetitions + 1):
                            settings = patch_json(
                                f"{api.base_url}/api/runtime-settings",
                                runtime_settings_payload,
                            )
                            self.assertEqual(settings["crawler_worker_count"], 1)
                            generation = (
                                f"restart-{generation_index}-{uuid.uuid4()}"
                            )
                            current_worker = DesktopBackendProcess(
                                data_dir=data_dir,
                                role="worker",
                                runtime_id=api.runtime_id,
                                api_pid=api.process.pid,
                                worker_generation=generation,
                                extra_env=self._worker_environment(
                                    controller,
                                    fault_point=fault_point,
                                    process_id=f"restart-{generation_index}",
                                ),
                            ).start()
                            worker_status = current_worker.wait_worker_ready()
                            reached_path = controller.wait_for_reached(
                                fault_point,
                                timeout_seconds=30,
                            )
                            worker_pid = current_worker.process.pid
                            worker_pid_sequence.append(worker_pid)
                            self.assertEqual(worker_status["pid"], worker_pid)
                            self.assertEqual(worker_status["generation"], generation)
                            self.assertEqual(worker_status["runtime_id"], api.runtime_id)

                            state = self._read_state(
                                data_dir / "auto_email_sender.db",
                                seeded,
                            )
                            self.assertEqual(state["item_status"], "processing")
                            self.assertEqual(state["processing_count"], 1)
                            self.assertEqual(state["attempt_count"], generation_index)
                            self.assertEqual(state["candidate_count"], 0)
                            claim_owner = str(state["worker_id"])
                            self.assertIn(f":{api.runtime_id}:", claim_owner)
                            self.assertNotIn(claim_owner, claim_owners)
                            claim_owners.add(claim_owner)
                            self.assertEqual(state["integrity_check"], "ok")
                            self.assertEqual(state["foreign_key_errors"], 0)
                            self.assertEqual(
                                fetch_json(f"{api.base_url}/startup-status")["state"],
                                "ready",
                            )

                            worker_samples.append(
                                self._process_resource_sample(
                                    worker_pid,
                                    data_dir / "auto_email_sender.db",
                                )
                            )
                            api_samples.append(
                                self._process_resource_sample(
                                    api.process.pid,
                                    data_dir / "auto_email_sender.db",
                                )
                            )
                            self.assertLess(
                                (data_dir / "runtime" / "worker.json").stat().st_size,
                                64 * 1024,
                            )

                            current_worker.process.kill()
                            current_worker.process.wait(timeout=10)
                            wait_until(
                                lambda: not psutil.pid_exists(worker_pid),
                                timeout_seconds=5,
                                description=f"worker PID {worker_pid} exit",
                            )
                            reached_path.unlink(missing_ok=True)
                            self._move_claim_far_into_future(
                                data_dir / "auto_email_sender.db",
                                seeded,
                            )
                            current_worker = None

                        final_worker = DesktopBackendProcess(
                            data_dir=data_dir,
                            role="worker",
                            runtime_id=api.runtime_id,
                            api_pid=api.process.pid,
                            worker_generation=f"final-{uuid.uuid4()}",
                            extra_env=self._worker_environment(
                                controller,
                                process_id="restart-final",
                            ),
                        ).start()
                        final_worker.wait_worker_ready()
                        final_state = self._wait_for_final_state(
                            data_dir / "auto_email_sender.db",
                            seeded,
                        )
                        self._assert_final_state("page", final_state)
                        self.assertEqual(
                            final_state["attempt_count"],
                            repetitions + 1,
                        )
                        self.assertEqual(http_server.request_count, 1)
                        self.assertEqual(llm_server.request_count, 1)
                        self.assertEqual(len(worker_pid_sequence), repetitions)
                        self.assertEqual(len(claim_owners), repetitions)

                        final_api = self._process_resource_sample(
                            api.process.pid,
                            data_dir / "auto_email_sender.db",
                        )
                        self._assert_resource_series_bounded(
                            baseline=baseline_api,
                            samples=api_samples,
                            final_sample=final_api,
                            repetitions=repetitions,
                        )
                        self._assert_worker_generations_bounded(worker_samples)
                        runtime_file_count = self._bounded_file_count(
                            data_dir / "runtime"
                        )
                        self.assertLessEqual(
                            runtime_file_count,
                            baseline_runtime_files + 2,
                        )
                        self.assertFalse(
                            any(
                                path.name.endswith(".tmp")
                                for path in (data_dir / "runtime").glob("*")
                            )
                        )
                        self.assertLess(
                            self._directory_size_bytes(data_dir / "logs"),
                            5 * 1024 * 1024,
                        )
                        resource_summary = {
                            "repetitions": repetitions,
                            "unique_worker_pids": len(set(worker_pid_sequence)),
                            "worker_pid_reuses": (
                                len(worker_pid_sequence)
                                - len(set(worker_pid_sequence))
                            ),
                            "unique_claim_owners": len(claim_owners),
                            "api_rss_baseline_bytes": baseline_api.rss_bytes,
                            "api_rss_peak_bytes": max(
                                sample.rss_bytes for sample in api_samples
                            ),
                            "api_rss_final_bytes": final_api.rss_bytes,
                            "api_rss_slope_bytes_per_restart": self._linear_slope(
                                [sample.rss_bytes for sample in api_samples]
                            ),
                            "api_handles_baseline": baseline_api.handle_count,
                            "api_handles_peak": max(
                                sample.handle_count for sample in api_samples
                            ),
                            "api_handles_final": final_api.handle_count,
                            "api_handles_slope_per_restart": self._linear_slope(
                                [sample.handle_count for sample in api_samples]
                            ),
                            "api_database_fds_peak": max(
                                sample.database_file_count for sample in api_samples
                            ),
                            "api_external_inet_connections_peak": max(
                                sample.external_inet_connection_count
                                for sample in api_samples
                            ),
                            "worker_rss_min_bytes": min(
                                sample.rss_bytes for sample in worker_samples
                            ),
                            "worker_rss_max_bytes": max(
                                sample.rss_bytes for sample in worker_samples
                            ),
                            "worker_handles_min": min(
                                sample.handle_count for sample in worker_samples
                            ),
                            "worker_handles_max": max(
                                sample.handle_count for sample in worker_samples
                            ),
                            "worker_external_inet_connections_peak": max(
                                sample.external_inet_connection_count
                                for sample in worker_samples
                            ),
                            "worker_database_fds_min": min(
                                sample.database_file_count
                                for sample in worker_samples
                            ),
                            "worker_database_fds_max": max(
                                sample.database_file_count
                                for sample in worker_samples
                            ),
                            "runtime_files_baseline": baseline_runtime_files,
                            "runtime_files_final": runtime_file_count,
                            "production_log_bytes": self._directory_size_bytes(
                                data_dir / "logs"
                            ),
                        }
                        print(
                            "WORKER_RESTART_RESOURCE_SUMMARY="
                            + json.dumps(resource_summary, sort_keys=True)
                        )
                    finally:
                        if final_worker is not None:
                            final_worker.stop()
                        if current_worker is not None:
                            current_worker.stop()
                        api.stop()

    @staticmethod
    def _worker_environment(
        controller: FaultController,
        *,
        process_id: str,
        fault_point: str | None = None,
    ) -> dict[str, str]:
        fault_points = (fault_point,) if fault_point is not None else ()
        return {
            "ENABLE_BACKGROUND_WORKERS": "1",
            "DISPATCHER_INTERVAL_SECONDS": "3600",
            "IMAP_POLL_INTERVAL_SECONDS": "3600",
            "MATCH_ANALYSIS_JOB_INTERVAL_SECONDS": "3600",
            "LLM_REQUEST_TIMEOUT_SECONDS": "10",
            "CRAWLER_DEBUG": "0",
            "AUTO_EMAIL_SENDER_TEST_CRAWL_LOOPBACK_HOSTS": _CRAWL_HOSTNAME,
            **controller.environment(
                *fault_points,
                process_id=process_id,
                timeout_seconds=60,
            ),
        }

    @classmethod
    def _crawler_retry_state(
        cls,
        database_path: Path,
        seeded: _SeededCrawlerWork,
        *,
        minimum_failures: int,
    ) -> dict[str, Any] | None:
        state = cls._read_state(database_path, seeded)
        if (
            state["item_status"] == "failed_retryable"
            and state["failure_count"] >= minimum_failures
            and state["worker_id"] is None
            and state["processing_count"] == 0
        ):
            return state
        return None

    @staticmethod
    def _worker_subsystem_state(
        data_dir: Path,
        *,
        prefix: str,
        degraded: bool,
    ) -> dict[str, Any] | None:
        try:
            status = json.loads(
                (data_dir / "runtime" / "worker.json").read_text(
                    encoding="utf-8"
                )
            )
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        subsystems = status.get("subsystems")
        if not isinstance(subsystems, dict):
            return None
        relevant = [
            subsystem
            for name, subsystem in subsystems.items()
            if str(name).startswith(prefix) and isinstance(subsystem, dict)
        ]
        if not relevant:
            return None
        has_failure = any(
            int(subsystem.get("consecutive_failures") or 0) > 0
            for subsystem in relevant
        )
        expected_health = "degraded" if degraded else "healthy"
        if status.get("health") != expected_health:
            return None
        if degraded != has_failure:
            return None
        return status

    @staticmethod
    def _seed_workload(
        database_path: Path,
        *,
        kind: str,
        llm_base_url: str,
        profile_url: str,
        model_name: str = "test-model",
    ) -> _SeededCrawlerWork:
        if kind not in _WORK_TABLES:
            raise ValueError(f"Unsupported crawler work kind: {kind}")
        connection = sqlite3.connect(database_path, timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            suffix = uuid.uuid4().hex
            now = datetime.now(UTC).replace(tzinfo=None)
            future = now + timedelta(days=1)
            connection.execute("INSERT OR IGNORE INTO app_settings (id) VALUES (1)")
            connection.execute(
                """
                UPDATE app_settings
                SET crawler_worker_count = 1,
                    crawler_profile_enrichment_concurrency = 1,
                    crawler_host_concurrency = 1
                WHERE id = 1
                """
            )
            llm_profile_id = int(
                connection.execute(
                    """
                    INSERT INTO llm_profiles (
                        name, provider, api_base_url, api_key, model_name, is_default
                    ) VALUES (?, 'openai', ?, 'test-key', ?, 1)
                    """,
                    (f"Crawler model {suffix}", llm_base_url, model_name),
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
                    ) VALUES (?, ?, 'chat_completions', 3,
                          'prompt_only', ?, ?)
                    """,
                    (
                        llm_base_url,
                        model_name,
                        now.isoformat(sep=" "),
                        future.isoformat(sep=" "),
                    ),
            )
            entry_type = "profile" if kind == "page" else "list"
            start_url = profile_url
            job_id = int(
                connection.execute(
                    """
                    INSERT INTO crawl_jobs (
                        university, school, start_url, entry_type, job_kind,
                        trigger_mode, llm_profile_id, status
                    ) VALUES (?, ?, ?, ?, 'faculty_crawl', 'crawl', ?, 'running')
                    """,
                    (
                        "示例大学",
                        "计算机学院",
                        start_url,
                        entry_type,
                        llm_profile_id,
                    ),
                ).lastrowid
            )
            run_id = int(
                connection.execute(
                    """
                    INSERT INTO crawl_job_runs (
                        job_id, attempt_number, status, started_at,
                        active_started_at, created_at, updated_at
                    ) VALUES (?, 1, 'running', ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        now.isoformat(sep=" "),
                        now.isoformat(sep=" "),
                        now.isoformat(sep=" "),
                        now.isoformat(sep=" "),
                    ),
                ).lastrowid
            )
            connection.execute(
                "UPDATE crawl_jobs SET current_run_id = ? WHERE id = ?",
                (run_id, job_id),
            )

            candidate_id: int | None = None
            if kind == "page":
                work_item_id = int(
                    connection.execute(
                        """
                        INSERT INTO crawl_page_tasks (
                            job_id, normalized_url, original_url,
                            discovery_reason, expansion_mode, status
                        ) VALUES (?, ?, ?, 'start', 'no_expansion', 'pending')
                        """,
                        (job_id, profile_url, profile_url),
                    ).lastrowid
                )
            elif kind == "chunk":
                work_item_id = int(
                    connection.execute(
                        """
                        INSERT INTO crawl_page_chunks (
                            job_id, page_id, source_url, page_fingerprint,
                            chunk_id, chunk_index, chunk_hash, content, status
                        ) VALUES (?, NULL, ?, ?, ?, 0, ?, ?, 'pending')
                        """,
                        (
                            job_id,
                            profile_url,
                            f"fingerprint-{suffix}",
                            f"chunk-{suffix}",
                            f"hash-{suffix}",
                            f"[张三]({profile_url}) 教授 邮箱 zhang@example.edu",
                        ),
                    ).lastrowid
                )
            else:
                candidate_id = int(
                    connection.execute(
                        """
                        INSERT INTO crawl_candidates (
                            job_id, name, profile_url, source_url,
                            university, school, confidence, review_status
                        ) VALUES (?, '张三', ?, ?, ?, ?, 0.9, 'pending')
                        """,
                        (
                            job_id,
                            profile_url,
                            profile_url,
                            "示例大学",
                            "计算机学院",
                        ),
                    ).lastrowid
                )
                work_item_id = int(
                    connection.execute(
                        """
                        INSERT INTO crawl_candidate_enrichment_tasks (
                            job_id, candidate_id, status
                        ) VALUES (?, ?, 'pending')
                        """,
                        (job_id, candidate_id),
                    ).lastrowid
                )
            connection.commit()
            return _SeededCrawlerWork(
                kind=kind,
                job_id=job_id,
                work_item_id=work_item_id,
                candidate_id=candidate_id,
                run_id=run_id,
                profile_url=profile_url,
            )
        finally:
            connection.close()

    @staticmethod
    def _move_claim_far_into_future(
        database_path: Path,
        seeded: _SeededCrawlerWork,
    ) -> None:
        table = _WORK_TABLES[seeded.kind]
        connection = sqlite3.connect(database_path, timeout=10)
        try:
            future_lease = (
                datetime.now(UTC).replace(tzinfo=None) + timedelta(days=365)
            ).isoformat(sep=" ")
            connection.execute(
                f"""
                UPDATE {table}
                SET lease_expires_at = ?
                WHERE id = ? AND status = 'processing'
                """,  # noqa: S608 - table comes from the fixed mapping above
                (future_lease, seeded.work_item_id),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _replace_owner(
        database_path: Path,
        seeded: _SeededCrawlerWork,
        *,
        expected_owner: str,
        replacement_owner: str,
    ) -> None:
        table = _WORK_TABLES[seeded.kind]
        connection = sqlite3.connect(database_path, timeout=10)
        try:
            future = (
                datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5)
            ).isoformat(sep=" ")
            result = connection.execute(
                f"""
                UPDATE {table}
                SET worker_id = ?, lease_expires_at = ?
                WHERE id = ? AND status = 'processing' AND worker_id = ?
                """,  # noqa: S608 - table comes from the fixed mapping above
                (
                    replacement_owner,
                    future,
                    seeded.work_item_id,
                    expected_owner,
                ),
            )
            if result.rowcount != 1:
                raise AssertionError(
                    f"Could not replace crawler {seeded.kind} owner "
                    f"for item {seeded.work_item_id}"
                )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _replace_enrichment_input(
        database_path: Path,
        seeded: _SeededCrawlerWork,
        *,
        expected_owner: str,
        replacement_owner: str,
        profile_url: str,
    ) -> None:
        if seeded.kind != "enrichment" or seeded.candidate_id is None:
            raise AssertionError("Expected a seeded enrichment candidate")
        connection = sqlite3.connect(database_path, timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            future = (
                datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5)
            ).isoformat(sep=" ")
            result = connection.execute(
                """
                UPDATE crawl_candidate_enrichment_tasks
                SET worker_id = ?, lease_expires_at = ?
                WHERE id = ? AND status = 'processing' AND worker_id = ?
                """,
                (
                    replacement_owner,
                    future,
                    seeded.work_item_id,
                    expected_owner,
                ),
            )
            if result.rowcount != 1:
                raise AssertionError("Could not replace enrichment input owner")
            connection.execute(
                """
                UPDATE crawl_candidates
                SET profile_url = ?, source_url = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    profile_url,
                    profile_url,
                    datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "),
                    seeded.candidate_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    @classmethod
    def _wait_for_final_state(
        cls,
        database_path: Path,
        seeded: _SeededCrawlerWork,
    ) -> dict[str, Any]:
        expected_item_status = _TERMINAL_STATUSES[seeded.kind]
        return wait_until(
            lambda: (
                state
                if (
                    (state := cls._read_state(database_path, seeded))[
                        "item_status"
                    ]
                    == expected_item_status
                    and state["job_status"] == "needs_review"
                )
                else None
            ),
            timeout_seconds=30,
            description=f"crawler {seeded.kind} terminal state",
        )

    @classmethod
    def _canceled_state_or_none(
        cls,
        database_path: Path,
        seeded: _SeededCrawlerWork,
    ) -> dict[str, Any] | None:
        state = cls._read_state(database_path, seeded)
        if state["job_status"] != "canceled":
            return None
        if state["item_status"] != "pending" or state["worker_id"] is not None:
            return None
        return state

    @staticmethod
    def _read_state(
        database_path: Path,
        seeded: _SeededCrawlerWork,
    ) -> dict[str, Any]:
        table = _WORK_TABLES[seeded.kind]
        connection = sqlite3.connect(database_path, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            item = connection.execute(
                f"""
                SELECT status, worker_id, claimed_at, lease_expires_at,
                       attempt_count, failure_count, last_error
                FROM {table}
                WHERE id = ?
                """,  # noqa: S608 - table comes from the fixed mapping above
                (seeded.work_item_id,),
            ).fetchone()
            if item is None:
                raise AssertionError(
                    f"Missing crawler {seeded.kind} item {seeded.work_item_id}"
                )
            job = connection.execute(
                "SELECT status, current_run_id FROM crawl_jobs WHERE id = ?",
                (seeded.job_id,),
            ).fetchone()
            if job is None:
                raise AssertionError(f"Missing crawl job {seeded.job_id}")
            candidates = connection.execute(
                """
                SELECT id, name, email, title, department,
                       research_direction, profile_url,
                       merged_into_candidate_id
                FROM crawl_candidates
                WHERE job_id = ?
                ORDER BY id
                """,
                (seeded.job_id,),
            ).fetchall()
            canonical_candidates = [
                candidate
                for candidate in candidates
                if candidate["merged_into_candidate_id"] is None
            ]
            duplicate_identity_keys = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT key_type, normalized_value
                        FROM crawl_candidate_identity_keys
                        WHERE job_id = ?
                        GROUP BY key_type, normalized_value
                        HAVING COUNT(*) > 1
                    )
                    """,
                    (seeded.job_id,),
                ).fetchone()[0]
            )
            page_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM crawl_pages WHERE job_id = ?",
                    (seeded.job_id,),
                ).fetchone()[0]
            )
            processing_count = sum(
                int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {candidate_table} "
                        "WHERE job_id = ? AND status = 'processing'",
                        (seeded.job_id,),
                    ).fetchone()[0]
                )
                for candidate_table in _WORK_TABLES.values()
            )
            run = connection.execute(
                "SELECT status FROM crawl_job_runs WHERE id = ?",
                (seeded.run_id,),
            ).fetchone()
            integrity_check = str(
                connection.execute("PRAGMA integrity_check").fetchone()[0]
            )
            foreign_key_errors = len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            )
            candidate_email = (
                canonical_candidates[0]["email"] if canonical_candidates else None
            )
            candidate_profile_url = (
                canonical_candidates[0]["profile_url"]
                if canonical_candidates
                else None
            )
            return {
                "job_status": job["status"],
                "current_run_id": job["current_run_id"],
                "run_status": run["status"] if run is not None else None,
                "item_status": item["status"],
                "worker_id": item["worker_id"],
                "claimed_at": item["claimed_at"],
                "lease_expires_at": item["lease_expires_at"],
                "attempt_count": int(item["attempt_count"] or 0),
                "failure_count": int(item["failure_count"] or 0),
                "last_error": item["last_error"],
                "candidate_count": len(candidates),
                "canonical_candidate_count": len(canonical_candidates),
                "candidate_email": candidate_email,
                "candidate_profile_url": candidate_profile_url,
                "page_count": page_count,
                "processing_count": processing_count,
                "duplicate_identity_keys": duplicate_identity_keys,
                "integrity_check": integrity_check,
                "foreign_key_errors": foreign_key_errors,
            }
        finally:
            connection.close()

    def _assert_fault_boundary(
        self,
        *,
        kind: str,
        fault_point: str,
        state: dict[str, Any],
        http_count: int,
        llm_count: int,
    ) -> None:
        stage = fault_point.rsplit(".", 1)[-1]
        self.assertEqual(state["integrity_check"], "ok")
        self.assertEqual(state["foreign_key_errors"], 0)
        if stage == "before_claim":
            self.assertEqual(state["item_status"], "pending")
            self.assertIsNone(state["worker_id"])
            self.assertEqual(state["attempt_count"], 0)
        elif stage == "after_final_commit":
            self.assertEqual(state["item_status"], _TERMINAL_STATUSES[kind])
            self.assertIsNone(state["worker_id"])
            self.assertEqual(state["attempt_count"], 1)
        else:
            self.assertEqual(state["item_status"], _PROCESSING_STATUS)
            self.assertIsNotNone(state["worker_id"])
            self.assertEqual(state["attempt_count"], 1)

        expected_http, expected_llm = self._fault_boundary_external_counts(
            kind,
            stage,
        )
        self.assertEqual(http_count, expected_http)
        self.assertEqual(llm_count, expected_llm)
        if kind in {"page", "chunk"}:
            expected_candidate_count = int(
                stage in {"before_final_commit", "after_final_commit"}
            )
            self.assertEqual(state["candidate_count"], expected_candidate_count)
        else:
            self.assertEqual(state["candidate_count"], 1)
            expected_email = (
                "zhang@example.edu" if stage == "after_final_commit" else None
            )
            self.assertEqual(state["candidate_email"], expected_email)

    def _assert_final_state(
        self,
        kind: str,
        state: dict[str, Any],
        *,
        expected_email: str = "zhang@example.edu",
        expected_failure_count: int = 0,
    ) -> None:
        self.assertEqual(state["job_status"], "needs_review")
        self.assertEqual(state["run_status"], "needs_review")
        self.assertEqual(state["item_status"], _TERMINAL_STATUSES[kind])
        self.assertIsNone(state["worker_id"])
        self.assertIsNone(state["claimed_at"])
        self.assertIsNone(state["lease_expires_at"])
        self.assertEqual(state["failure_count"], expected_failure_count)
        self.assertIsNone(state["last_error"])
        self.assertEqual(state["candidate_count"], 1)
        self.assertEqual(state["canonical_candidate_count"], 1)
        self.assertEqual(state["candidate_email"], expected_email)
        self.assertEqual(state["processing_count"], 0)
        self.assertEqual(state["duplicate_identity_keys"], 0)
        self.assertEqual(state["integrity_check"], "ok")
        self.assertEqual(state["foreign_key_errors"], 0)

    def _assert_external_counts(
        self,
        *,
        kind: str,
        fault_point: str,
        http_count: int,
        llm_count: int,
    ) -> None:
        stage = fault_point.rsplit(".", 1)[-1]
        if kind == "page":
            expected_http = 2 if stage in {
                "external_call_returned",
                "before_final_commit",
            } else 1
            expected_llm = 2 if stage == "before_final_commit" else 1
        elif kind == "chunk":
            expected_http = 0
            expected_llm = 2 if stage in {
                "external_call_returned",
                "before_final_commit",
            } else 1
        else:
            expected_http = 1
            expected_llm = 2 if stage in {
                "external_call_returned",
                "before_final_commit",
            } else 1
        self.assertEqual(http_count, expected_http)
        self.assertEqual(llm_count, expected_llm)

    @staticmethod
    def _fault_boundary_external_counts(kind: str, stage: str) -> tuple[int, int]:
        if kind == "page":
            return (
                int(
                    stage
                    in {
                        "external_call_returned",
                        "before_final_commit",
                        "after_final_commit",
                    }
                ),
                int(stage in {"before_final_commit", "after_final_commit"}),
            )
        if kind == "chunk":
            return (0, int(stage in {
                "external_call_returned",
                "before_final_commit",
                "after_final_commit",
            }))
        return (
            int(
                stage
                in {
                    "external_call_returned",
                    "before_final_commit",
                    "after_final_commit",
                }
            ),
            int(
                stage
                in {
                    "external_call_returned",
                    "before_final_commit",
                    "after_final_commit",
                }
            ),
        )

    @staticmethod
    def _crawler_response_factory(profile_url: str):
        candidate = {
            "name": "张三",
            "email": "zhang@example.edu",
            "title": "教授",
            "university": "示例大学",
            "school": "计算机学院",
            "department": "计算机系",
            "research_direction": "可靠分布式系统",
            "recent_papers": ["Deterministic Systems 2026"],
            "profile_url": profile_url,
            "source_url": profile_url,
            "confidence": 0.95,
            "field_confidence": [],
            "evidence_summary": "本地确定性测试页面",
        }

        def respond(_request_number: int, payload: dict[str, Any]) -> str:
            prompt = json.dumps(payload, ensure_ascii=False)
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
            if "只回复 OK" in prompt:
                return "OK"
            return json.dumps(
                {
                    "email": "",
                    "title": "",
                    "department": "",
                    "research_direction": "",
                    "recent_papers": [],
                },
                ensure_ascii=False,
            )

        return respond

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
    def _versioned_enrichment_response(
        _request_number: int,
        payload: dict[str, Any],
    ) -> str:
        prompt = json.dumps(payload, ensure_ascii=False)
        email = (
            "new-profile@example.edu"
            if "NEW_PROFILE_BODY" in prompt
            else "old-profile@example.edu"
        )
        return json.dumps(
            {
                "email": email,
                "title": "教授",
                "department": "计算机系",
                "research_direction": "可靠分布式系统",
                "recent_papers": ["Deterministic Systems 2026"],
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _chaos_repetitions() -> int:
        raw_value = os.getenv(_REPETITIONS_ENV, "1")
        try:
            repetitions = int(raw_value)
        except ValueError as exc:
            raise AssertionError(
                f"{_REPETITIONS_ENV} must be an integer, got {raw_value!r}"
            ) from exc
        if not 1 <= repetitions <= 100:
            raise AssertionError(
                f"{_REPETITIONS_ENV} must be between 1 and 100, got {repetitions}"
            )
        return repetitions

    @staticmethod
    def _restart_repetitions() -> int:
        raw_value = os.getenv(_RESTART_REPETITIONS_ENV, "1")
        try:
            repetitions = int(raw_value)
        except ValueError as exc:
            raise AssertionError(
                f"{_RESTART_REPETITIONS_ENV} must be an integer, "
                f"got {raw_value!r}"
            ) from exc
        if not 1 <= repetitions <= 500:
            raise AssertionError(
                f"{_RESTART_REPETITIONS_ENV} must be between 1 and 500, "
                f"got {repetitions}"
            )
        return repetitions

    @staticmethod
    def _process_resource_sample(
        pid: int,
        database_path: Path,
    ) -> _ProcessResourceSample:
        process = psutil.Process(pid)
        with process.oneshot():
            rss_bytes = int(process.memory_info().rss)
            if hasattr(process, "num_handles"):
                handle_count = int(process.num_handles())
            else:
                handle_count = int(process.num_fds())
            open_files = process.open_files()
            inet_connections = process.net_connections(kind="inet")
        resolved_database_path = database_path.resolve()
        database_files = {
            resolved_database_path.as_posix(),
            f"{resolved_database_path.as_posix()}-wal",
            f"{resolved_database_path.as_posix()}-shm",
        }
        return _ProcessResourceSample(
            rss_bytes=rss_bytes,
            handle_count=handle_count,
            open_file_count=len(open_files),
            database_file_count=sum(
                Path(open_file.path).resolve().as_posix() in database_files
                for open_file in open_files
            ),
            external_inet_connection_count=_external_inet_connection_count(
                inet_connections
            ),
        )

    def _assert_resource_series_bounded(
        self,
        *,
        baseline: _ProcessResourceSample,
        samples: list[_ProcessResourceSample],
        final_sample: _ProcessResourceSample,
        repetitions: int,
    ) -> None:
        self.assertEqual(len(samples), repetitions)
        rss_values = [sample.rss_bytes for sample in samples]
        handle_values = [sample.handle_count for sample in samples]
        self.assertLessEqual(
            max(rss_values),
            baseline.rss_bytes + 64 * 1024 * 1024,
        )
        self.assertLessEqual(
            final_sample.rss_bytes,
            baseline.rss_bytes + 48 * 1024 * 1024,
        )
        self.assertLessEqual(max(handle_values), baseline.handle_count + 16)
        self.assertLessEqual(final_sample.handle_count, baseline.handle_count + 8)
        self.assertLessEqual(
            max(sample.database_file_count for sample in samples),
            baseline.database_file_count + 3,
        )
        self.assertLessEqual(
            max(sample.external_inet_connection_count for sample in samples),
            baseline.external_inet_connection_count + 3,
        )
        if repetitions >= 20:
            self.assertLess(
                self._linear_slope(rss_values),
                256 * 1024,
            )
            self.assertLess(self._linear_slope(handle_values), 0.1)

    def _assert_worker_generations_bounded(
        self,
        samples: list[_ProcessResourceSample],
    ) -> None:
        self.assertTrue(samples)
        self.assertLessEqual(
            max(sample.rss_bytes for sample in samples)
            - min(sample.rss_bytes for sample in samples),
            48 * 1024 * 1024,
        )
        self.assertLessEqual(
            max(sample.handle_count for sample in samples)
            - min(sample.handle_count for sample in samples),
            12,
        )
        self.assertLessEqual(
            max(sample.database_file_count for sample in samples),
            24,
        )
        self.assertLessEqual(
            max(sample.database_file_count for sample in samples)
            - min(sample.database_file_count for sample in samples),
            6,
        )
        self.assertEqual(
            max(sample.external_inet_connection_count for sample in samples),
            0,
        )

    @staticmethod
    def _synthetic_connection(
        *,
        local: object,
        remote: object,
        status: str = psutil.CONN_ESTABLISHED,
    ) -> _SyntheticInetConnection:
        return _SyntheticInetConnection(
            family=int(socket.AF_INET),
            type=int(socket.SOCK_STREAM),
            laddr=local,
            raddr=remote,
            status=status,
        )

    @staticmethod
    def _linear_slope(values: list[int]) -> float:
        if len(values) < 2:
            return 0.0
        mean_x = (len(values) - 1) / 2
        mean_y = sum(values) / len(values)
        numerator = sum(
            (index - mean_x) * (value - mean_y)
            for index, value in enumerate(values)
        )
        denominator = sum(
            (index - mean_x) ** 2
            for index in range(len(values))
        )
        return numerator / denominator if denominator else 0.0

    @staticmethod
    def _bounded_file_count(directory: Path) -> int:
        if not directory.is_dir():
            return 0
        return sum(1 for path in directory.iterdir() if path.is_file())

    @staticmethod
    def _directory_size_bytes(directory: Path) -> int:
        if not directory.is_dir():
            return 0
        return sum(
            path.stat().st_size
            for path in directory.rglob("*")
            if path.is_file()
        )


if __name__ == "__main__":
    unittest.main()
