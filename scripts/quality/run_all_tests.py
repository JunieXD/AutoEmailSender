from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HEARTBEAT_SECONDS = 10.0
QUALITY_EVIDENCE_SCHEMA_VERSION = 1
QUALITY_EVIDENCE_KIND = "auto-email-sender-quality-evidence"


@dataclass(frozen=True)
class Suite:
    name: str
    cwd: Path
    command: tuple[str, ...]
    streams_progress: bool = False


def _format_duration(seconds: float) -> str:
    rounded = max(0, int(seconds))
    minutes, remaining_seconds = divmod(rounded, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{remaining_seconds:02d}s"
    if minutes:
        return f"{minutes}m{remaining_seconds:02d}s"
    return f"{remaining_seconds}s"


def _progress_interval(total: int) -> int:
    return max(1, total // 10)


def _executable(name: str) -> str:
    return shutil.which(name) or name


def _concise_failure_output(lines: list[str]) -> str:
    for index, line in enumerate(lines):
        if "Failed Tests" in line or line.startswith("=" * 20):
            return "".join(lines[index:]).rstrip()
    return "".join(lines).rstrip()


class ProgressTestResult(unittest.TextTestResult):
    def __init__(
        self,
        stream: TextIO,
        descriptions: bool,
        verbosity: int,
        *,
        label: str,
        total: int,
        heartbeat_seconds: float,
    ) -> None:
        super().__init__(stream, descriptions, verbosity)
        self.label = label
        self.total = total
        self.heartbeat_seconds = heartbeat_seconds
        self.completed = 0
        self.started_at = time.monotonic()
        self.test_started_at = self.started_at
        self.current_test: unittest.case.TestCase | None = None
        self.durations: list[tuple[float, str]] = []
        self.progress_stream = sys.__stdout__
        self._last_reported = 0
        self._stop_heartbeat = threading.Event()
        self._heartbeat = threading.Thread(target=self._report_heartbeat, daemon=True)
        self._heartbeat.start()

    def startTest(self, test: unittest.case.TestCase) -> None:
        self.current_test = test
        self.test_started_at = time.monotonic()
        super().startTest(test)

    def stopTest(self, test: unittest.case.TestCase) -> None:
        self.durations.append((time.monotonic() - self.test_started_at, test.id()))
        self.completed += 1
        self.current_test = None
        if (
            self.completed == self.total
            or self.completed - self._last_reported >= _progress_interval(self.total)
        ):
            self._print_progress()
        super().stopTest(test)

    def addFailure(
        self,
        test: unittest.case.TestCase,
        err: tuple[type[BaseException], BaseException, object],
    ) -> None:
        print(f"[{self.label}] FAIL {test.id()}", file=self.progress_stream, flush=True)
        super().addFailure(test, err)

    def addError(
        self,
        test: unittest.case.TestCase,
        err: tuple[type[BaseException], BaseException, object],
    ) -> None:
        print(
            f"[{self.label}] ERROR {test.id()}", file=self.progress_stream, flush=True
        )
        super().addError(test, err)

    def addUnexpectedSuccess(self, test: unittest.case.TestCase) -> None:
        print(
            f"[{self.label}] UNEXPECTED SUCCESS {test.id()}",
            file=self.progress_stream,
            flush=True,
        )
        super().addUnexpectedSuccess(test)

    def stop_heartbeat(self) -> None:
        self._stop_heartbeat.set()
        self._heartbeat.join(timeout=max(1.0, self.heartbeat_seconds + 1.0))

    def _report_heartbeat(self) -> None:
        while not self._stop_heartbeat.wait(self.heartbeat_seconds):
            self._print_progress()

    def _print_progress(self) -> None:
        elapsed = _format_duration(time.monotonic() - self.started_at)
        percent = 100 if not self.total else int(self.completed * 100 / self.total)
        print(
            f"[{self.label}] {self.completed}/{self.total} ({percent}%) | {elapsed}",
            file=self.progress_stream,
            flush=True,
        )
        self._last_reported = self.completed


def _run_unittest(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(Path.cwd()))
    print(f"[{args.label}] collecting tests...", flush=True)
    suite = unittest.defaultTestLoader.discover(
        args.start_directory, pattern=args.pattern
    )
    total = suite.countTestCases()
    print(f"[{args.label}] collected {total} tests", flush=True)
    report = io.StringIO()
    result_holder: list[ProgressTestResult] = []

    def result_factory(
        stream: TextIO,
        descriptions: bool,
        verbosity: int,
    ) -> ProgressTestResult:
        result = ProgressTestResult(
            stream,
            descriptions,
            verbosity,
            label=args.label,
            total=total,
            heartbeat_seconds=args.heartbeat,
        )
        result_holder.append(result)
        return result

    runner = unittest.TextTestRunner(
        stream=report,
        verbosity=0,
        buffer=True,
        resultclass=result_factory,
    )
    result = runner.run(suite)
    progress_result = result_holder[0]
    progress_result.stop_heartbeat()
    if args.slowest:
        print(f"[{args.label}] slowest tests:", flush=True)
        for duration, test_id in sorted(progress_result.durations, reverse=True)[
            : args.slowest
        ]:
            print(
                f"[{args.label}]   {_format_duration(duration):>8}  {test_id}",
                flush=True,
            )
    if not result.wasSuccessful():
        print(report.getvalue().rstrip(), flush=True)
        return 1
    return 0


def _suite_definitions(
    script_path: Path, heartbeat: float, slowest: int
) -> dict[str, Suite]:
    unittest_args = ("--internal-unittest", "--heartbeat", str(heartbeat))
    if slowest:
        unittest_args += ("--slowest", str(slowest))
    return {
        "backend": Suite(
            "backend",
            REPO_ROOT / "backend",
            (sys.executable, str(script_path), *unittest_args, "--label", "backend"),
            streams_progress=True,
        ),
        "cli": Suite(
            "cli",
            REPO_ROOT / "cli",
            (
                _executable("uv"),
                "run",
                "--project",
                str(REPO_ROOT / "cli"),
                "--no-sync",
                "python",
                str(script_path),
                *unittest_args,
                "--label",
                "cli",
            ),
            streams_progress=True,
        ),
        "frontend": Suite(
            "frontend",
            REPO_ROOT / "frontend",
            (
                _executable("npm"),
                "run",
                "test",
                "--",
                "--reporter=dot",
                "--silent=passed-only",
            ),
        ),
        "desktop": Suite(
            "desktop",
            REPO_ROOT / "desktop",
            (
                _executable("npm"),
                "run",
                "test",
                "--",
                "--reporter=dot",
                "--silent=passed-only",
            ),
        ),
        "website": Suite(
            "website",
            REPO_ROOT / "website",
            (
                _executable("npm"),
                "run",
                "test",
                "--",
                "--reporter=dot",
                "--silent=passed-only",
            ),
        ),
    }


def _run_process_suite(suite: Suite, heartbeat_seconds: float) -> tuple[bool, float]:
    started_at = time.monotonic()
    print(f"[{suite.name}] starting", flush=True)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    if Path(suite.command[0]).stem.lower() == "uv":
        env.pop("VIRTUAL_ENV", None)
    process = subprocess.Popen(
        suite.command,
        cwd=suite.cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    lines: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    captured: list[str] = []
    next_heartbeat = time.monotonic() + heartbeat_seconds
    output_closed = False
    while process.poll() is None or not output_closed:
        timeout = max(0.1, next_heartbeat - time.monotonic())
        try:
            line = lines.get(timeout=timeout)
            if line is None:
                output_closed = True
            else:
                captured.append(line)
                if suite.streams_progress and line.startswith(f"[{suite.name}]"):
                    print(line, end="", flush=True)
        except queue.Empty:
            if not suite.streams_progress:
                elapsed = _format_duration(time.monotonic() - started_at)
                print(f"[{suite.name}] running | {elapsed}", flush=True)
            next_heartbeat = time.monotonic() + heartbeat_seconds

    return_code = process.wait()
    reader.join(timeout=1)
    elapsed_seconds = time.monotonic() - started_at
    if return_code != 0:
        print(_concise_failure_output(captured), flush=True)
        print(
            f"[{suite.name}] failed (exit {return_code}) | {_format_duration(elapsed_seconds)}",
            flush=True,
        )
        return False, elapsed_seconds
    print(f"[{suite.name}] passed | {_format_duration(elapsed_seconds)}", flush=True)
    return True, elapsed_seconds


def _run_all(args: argparse.Namespace) -> int:
    definitions = _suite_definitions(
        Path(__file__).resolve(), args.heartbeat, args.slowest
    )
    selected = args.suite or list(definitions)
    unknown = [name for name in selected if name not in definitions]
    if unknown:
        raise SystemExit(f"unknown suite(s): {', '.join(unknown)}")

    started_at = time.monotonic()
    results: list[tuple[str, bool, float]] = []
    for name in selected:
        passed, duration = _run_process_suite(definitions[name], args.heartbeat)
        results.append((name, passed, duration))
        if not passed and args.fail_fast:
            break

    print("\nTest summary", flush=True)
    for name, passed, duration in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {status:4}  {name:10} {_format_duration(duration):>8}", flush=True)
    total_duration = _format_duration(time.monotonic() - started_at)
    failures = sum(not passed for _, passed, _ in results)
    print(f"  total: {total_duration} | failures: {failures}", flush=True)
    if failures == 0 and args.write_evidence is not None:
        _write_quality_evidence(args.write_evidence, selected)
    return 1 if failures else 0


def _captured_command(arguments: list[str]) -> str:
    completed = subprocess.run(
        arguments,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _build_quality_evidence(
    suites: list[str],
    *,
    git_sha: str,
    toolchain: dict[str, str],
    generated_at: str | None = None,
) -> dict[str, object]:
    return {
        "schemaVersion": QUALITY_EVIDENCE_SCHEMA_VERSION,
        "kind": QUALITY_EVIDENCE_KIND,
        "gitSha": git_sha,
        "generatedAt": generated_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "toolchain": toolchain,
        "passedSuites": sorted(set(suites)),
    }


def _write_quality_evidence(output_path: Path, suites: list[str]) -> None:
    for arguments in (
        ["git", "-C", str(REPO_ROOT), "diff", "--quiet"],
        ["git", "-C", str(REPO_ROOT), "diff", "--cached", "--quiet"],
    ):
        if subprocess.run(arguments, check=False).returncode != 0:
            raise SystemExit("refusing to write quality evidence for tracked changes")

    evidence = _build_quality_evidence(
        suites,
        git_sha=_captured_command(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"]
        ),
        toolchain={
            "node": _captured_command([_executable("node"), "--version"]),
            "npm": _captured_command([_executable("npm"), "--version"]),
            "python": _captured_command(
                [
                    _executable("uv"),
                    "run",
                    "--project",
                    str(REPO_ROOT / "backend"),
                    "--no-sync",
                    "python",
                    "--version",
                ]
            ),
            "uv": _captured_command([_executable("uv"), "--version"]),
        },
    )
    resolved_output = output_path.expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[evidence] wrote {resolved_output}", flush=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run repository tests with concise live progress and failure-only details.",
    )
    parser.add_argument(
        "--suite", action="append", help="suite to run; repeat to select several"
    )
    parser.add_argument(
        "--fail-fast", action="store_true", help="stop after the first failed suite"
    )
    parser.add_argument("--heartbeat", type=float, default=DEFAULT_HEARTBEAT_SECONDS)
    parser.add_argument(
        "--slowest", type=int, default=0, help="show N slowest unittest cases"
    )
    parser.add_argument(
        "--write-evidence",
        type=Path,
        help="write short-lived SHA/toolchain-bound evidence after all selected suites pass",
    )
    parser.add_argument(
        "--internal-unittest", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument("--label", default="unittest", help=argparse.SUPPRESS)
    parser.add_argument("--start-directory", default="test", help=argparse.SUPPRESS)
    parser.add_argument("--pattern", default="test_*.py", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")
    args = _parse_args()
    if args.heartbeat <= 0:
        raise SystemExit("--heartbeat must be greater than zero")
    if args.slowest < 0:
        raise SystemExit("--slowest cannot be negative")
    if args.internal_unittest:
        return _run_unittest(args)
    return _run_all(args)


if __name__ == "__main__":
    raise SystemExit(main())
