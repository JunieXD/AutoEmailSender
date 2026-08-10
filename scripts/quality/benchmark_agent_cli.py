#!/usr/bin/env python3
"""Cold-process latency and intent-routing benchmark for the Agent CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from typing import Any


INTENT_CASES = (
    ("导入导师", "professors.import"),
    ("查看回信", "communications.threads.list"),
    ("generate email draft", "drafts.generate"),
    ("professers improt", "professors.import"),
    ("修改发送时间", "deliveries.reschedule"),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--executable",
        type=Path,
        required=True,
        help="Packaged auto-email-sender executable to launch for every sample.",
    )
    parser.add_argument("--samples", type=int, default=15)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--capabilities-p95-ms", type=float, default=1000.0)
    parser.add_argument("--describe-p95-ms", type=float, default=1000.0)
    parser.add_argument("--intent-p95-ms", type=float, default=1000.0)
    parser.add_argument("--skip-thresholds", action="store_true")
    return parser


def _invoke(executable: Path, arguments: list[str]) -> tuple[float, dict[str, Any], int]:
    started = time.perf_counter()
    completed = subprocess.run(
        [executable.as_posix(), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    if completed.returncode != 0:
        raise RuntimeError(
            f"CLI exited {completed.returncode}: {completed.stderr or completed.stdout}",
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("CLI did not emit one JSON envelope") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(f"CLI returned an unsuccessful envelope: {payload!r}")
    return elapsed_ms, payload, len(completed.stdout.encode("utf-8"))


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) * percentile) + 0.999999) - 1))
    return ordered[index]


def _summary(values: list[float], output_bytes: list[int]) -> dict[str, object]:
    return {
        "samples": len(values),
        "p50_ms": round(statistics.median(values), 2),
        "p95_ms": round(_percentile(values, 0.95), 2),
        "max_ms": round(max(values), 2),
        "mean_ms": round(statistics.fmean(values), 2),
        "output_bytes_max": max(output_bytes),
    }


def run_benchmark(
    executable: Path,
    *,
    samples: int,
    warmup: int,
) -> dict[str, object]:
    if samples < 1 or warmup < 0:
        raise ValueError("samples must be positive and warmup cannot be negative")
    if not executable.is_file():
        raise FileNotFoundError(f"CLI executable does not exist: {executable}")

    commands = {
        "capabilities": ["--format", "json", "capabilities"],
        "describe": [
            "--format",
            "json",
            "describe",
            "--command",
            "professors.list",
        ],
    }
    for _ in range(warmup):
        for arguments in commands.values():
            _invoke(executable, arguments)

    measurements: dict[str, dict[str, object]] = {}
    for name, arguments in commands.items():
        durations: list[float] = []
        sizes: list[int] = []
        for _ in range(samples):
            elapsed, _payload, output_size = _invoke(executable, arguments)
            durations.append(elapsed)
            sizes.append(output_size)
        measurements[name] = _summary(durations, sizes)

    intent_durations: list[float] = []
    intent_sizes: list[int] = []
    intent_results: list[dict[str, object]] = []
    for query, expected in INTENT_CASES:
        case_durations: list[float] = []
        observed: str | None = None
        for _ in range(samples):
            elapsed, payload, output_size = _invoke(
                executable,
                [
                    "--format",
                    "json",
                    "capabilities",
                    "--query",
                    query,
                    "--limit",
                    "1",
                    "--minimal",
                ],
            )
            data = payload.get("data")
            items = data.get("items") if isinstance(data, dict) else None
            first = items[0] if isinstance(items, list) and items else None
            observed = first.get("command") if isinstance(first, dict) else None
            case_durations.append(elapsed)
            intent_durations.append(elapsed)
            intent_sizes.append(output_size)
        intent_results.append(
            {
                "query": query,
                "expected": expected,
                "observed": observed,
                "correct": observed == expected,
                "p95_ms": round(_percentile(case_durations, 0.95), 2),
            },
        )
    measurements["intent_routing"] = {
        **_summary(intent_durations, intent_sizes),
        "accuracy": sum(bool(item["correct"]) for item in intent_results) / len(intent_results),
        "cases": intent_results,
    }
    return {
        "schema_version": "1",
        "method": "fresh OS process per sample",
        "executable": executable.resolve().as_posix(),
        "measurements": measurements,
    }


def main() -> int:
    _configure_standard_stream_encoding()
    args = _parser().parse_args()
    try:
        result = run_benchmark(
            args.executable,
            samples=args.samples,
            warmup=args.warmup,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2

    measurements = result["measurements"]
    assert isinstance(measurements, dict)
    failures: list[str] = []
    thresholds = {
        "capabilities": args.capabilities_p95_ms,
        "describe": args.describe_p95_ms,
        "intent_routing": args.intent_p95_ms,
    }
    for name, threshold in thresholds.items():
        measurement = measurements[name]
        assert isinstance(measurement, dict)
        if float(measurement["p95_ms"]) > threshold:
            failures.append(f"{name} p95 exceeds {threshold} ms")
    intent = measurements["intent_routing"]
    assert isinstance(intent, dict)
    if float(intent["accuracy"]) != 1.0:
        failures.append("intent routing accuracy is below 100%")
    result["thresholds_ms"] = thresholds
    result["ok"] = not failures
    if failures:
        result["failures"] = failures
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if args.skip_thresholds or not failures else 1


def _configure_standard_stream_encoding() -> None:
    """Keep human-readable JSON portable across redirected Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
