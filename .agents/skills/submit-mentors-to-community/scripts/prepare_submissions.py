#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from xlsx_contract import inspect_xlsx

DEFAULT_REPOSITORY = "JunieXD/AutoEmailSender-MentorData"


def _load_entries(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        entries = payload.get("submissions", payload.get("files", []))
    else:
        raise ValueError("输入必须是数组，或包含 submissions/files 数组的 JSON 对象")
    if not isinstance(entries, list) or not entries:
        raise ValueError("至少需要一个投稿文件")
    if not all(isinstance(item, dict) for item in entries):
        raise ValueError("submissions 中每一项必须是对象")
    return entries


def _canonical_batch_id(items: list[dict[str, object]]) -> str:
    canonical = [
        {
            key: item[key]
            for key in ("university", "school", "department", "file", "sha256", "professor_count")
        }
        for item in items
    ]
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def prepare(input_path: Path, output_dir: Path, *, repository: str, license_name: str, dry_run: bool) -> dict[str, object]:
    entries = _load_entries(input_path)
    prepared: list[dict[str, object]] = []
    identities: set[tuple[str, str]] = set()
    hashes: set[str] = set()
    errors: list[str] = []
    for index, entry in enumerate(entries, start=1):
        raw_path = entry.get("file") or entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            errors.append(f"第 {index} 项缺少 file")
            continue
        source = (input_path.parent / raw_path).expanduser().resolve()
        summary = inspect_xlsx(source)
        if not summary.get("ok"):
            errors.extend(f"{source}: {error}" for error in summary.get("errors", []))
            continue
        university = str(summary["university"])
        school = str(summary["school"])
        identity = (university.casefold(), school.casefold())
        if identity in identities:
            errors.append(f"重复投稿单位：{university} / {school}")
        identities.add(identity)
        file_hash = str(summary["sha256"])
        if file_hash in hashes:
            errors.append(f"重复文件 SHA-256：{file_hash}")
        hashes.add(file_hash)
        for field in ("university", "school"):
            if field in entry and str(entry[field]).strip() != str(summary[field]).strip():
                errors.append(f"{source}: 输入元数据 {field} 与工作表不一致")
        departments = list(summary.get("departments", []))
        prepared.append({
            "university": university,
            "school": school,
            "department": str(entry.get("department", departments[0] if len(departments) == 1 else "")),
            "source": source,
            "sha256": file_hash,
            "size_bytes": int(summary["size_bytes"]),
            "professor_count": int(summary["professor_count"]),
        })
    if errors:
        raise ValueError("；".join(errors))
    prepared.sort(key=lambda item: (str(item["university"]).casefold(), str(item["school"]).casefold(), str(item["department"]).casefold(), str(item["sha256"])))
    batch_id = _canonical_batch_id([
        {"university": item["university"], "school": item["school"], "department": item["department"], "file": f"files/{index:03d}.xlsx", "sha256": item["sha256"], "professor_count": item["professor_count"]}
        for index, item in enumerate(prepared, start=1)
    ])
    items = [
        {
            "index": index,
            "university": item["university"],
            "school": item["school"],
            "department": item["department"],
            "file": f"files/{index:03d}.xlsx",
            "sha256": item["sha256"],
            "size_bytes": item["size_bytes"],
            "professor_count": item["professor_count"],
        }
        for index, item in enumerate(prepared, start=1)
    ]
    result = {
        "schema_version": 1,
        "batch_id": batch_id,
        "license": license_name,
        "repository": repository,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "items": items,
        "totals": {"submission_count": len(items), "professor_count": sum(int(item["professor_count"]) for item in items)},
        "submission": {"status": "prepared", "issue_urls": [], "pr_url": None, "error": None},
    }
    if not dry_run:
        output_dir = output_dir.expanduser().resolve()
        if output_dir.exists() and any(output_dir.iterdir()):
            raise ValueError(f"输出目录非空：{output_dir}；请换目录或先人工确认后使用 --force（脚本不会覆盖已有批次）")
        parent = output_dir.parent
        parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=parent))
        try:
            (temporary / "files").mkdir()
            for index, item in enumerate(prepared, start=1):
                shutil.copyfile(item["source"], temporary / "files" / f"{index:03d}.xlsx")
            (temporary / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(output_dir)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="准备可审计的社区导师投稿批次")
    parser.add_argument("--input", type=Path, required=True, help="包含 submissions 数组的 JSON")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--license", dest="license_name", default="CC BY 4.0")
    parser.add_argument("--dry-run", action="store_true", help="只校验并输出 manifest，不写文件")
    args = parser.parse_args(argv)
    try:
        result = prepare(args.input.expanduser().resolve(), args.output_dir, repository=args.repository, license_name=args.license_name, dry_run=args.dry_run)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

