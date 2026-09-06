from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from xlsx_contract import inspect_xlsx


def valid_repository(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", value))
        and value.split("/")[1] not in {".", ".."}
    )


def _batch_id(items: list[dict[str, object]]) -> str:
    canonical = [
        {
            key: item[key]
            for key in (
                "university",
                "school",
                "department",
                "file",
                "sha256",
                "professor_count",
            )
        }
        for item in items
    ]
    payload = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _audit(manifest_path: Path) -> dict[str, object]:
    manifest_path = manifest_path.expanduser().resolve()
    errors: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "manifest": str(manifest_path), "errors": [str(exc)]}
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        return {
            "ok": False,
            "manifest": str(manifest_path),
            "errors": ["manifest schema_version 必须为 1"],
        }
    if not valid_repository(manifest.get("repository")):
        errors.append("repository 必须是 GitHub owner/repo")
    if manifest.get("license") != "CC BY 4.0":
        errors.append("license 必须为 CC BY 4.0")
    if "generated_at" in manifest and not isinstance(manifest["generated_at"], str):
        errors.append("generated_at 必须是字符串")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        errors.append("manifest.items 必须是非空数组")
        items = []
    normalized: list[dict[str, object]] = []
    identities: set[tuple[str, str]] = set()
    paths: set[str] = set()
    hashes: set[str] = set()
    files_dir = manifest_path.parent / "files"
    if files_dir.is_symlink() or not files_dir.is_dir():
        errors.append("files 必须是普通目录")
    inventory = set()
    if files_dir.is_dir():
        for path in files_dir.iterdir():
            if path.is_symlink() or not path.is_file():
                errors.append(f"files 不允许符号链接或子目录：{path.name}")
            inventory.add(f"files/{path.name}")
    for position, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"items[{position}] 必须是对象")
            continue
        relative = item.get("file")
        if not isinstance(relative, str) or not re.fullmatch(
            r"files/[0-9]{3,}\.xlsx", relative
        ):
            errors.append(f"items[{position}].file 必须是安全的相对路径")
            continue
        if relative in paths or str(item.get("sha256")) in hashes:
            errors.append(f"重复文件路径或 SHA-256：{relative}")
        paths.add(relative)
        if isinstance(item.get("sha256"), str):
            hashes.add(item["sha256"])
        if not isinstance(item.get("department"), str) or item.get("index") != position:
            errors.append(f"{relative}: department/index 无效")
        file_path = (manifest_path.parent / relative).resolve()
        if manifest_path.parent not in file_path.parents:
            errors.append(f"items[{position}].file 越出批次目录")
            continue
        summary = inspect_xlsx(file_path)
        if not summary.get("ok"):
            errors.extend(f"{relative}: {error}" for error in summary.get("errors", []))
        expected = {
            "university": summary.get("university", ""),
            "school": summary.get("school", ""),
            "department": item.get("department", ""),
            "file": relative,
            "sha256": summary.get("sha256", ""),
            "professor_count": summary.get("professor_count", 0),
        }
        for key in ("sha256", "size_bytes", "professor_count", "university", "school"):
            if item.get(key) != summary.get(key):
                errors.append(f"{relative}: {key} 与工作表/文件不一致")
        identity = (
            str(expected["university"]).casefold(),
            str(expected["school"]).casefold(),
        )
        if identity in identities:
            errors.append(
                f"重复投稿单位：{expected['university']} / {expected['school']}"
            )
        identities.add(identity)
        normalized.append(expected)
    if inventory != paths:
        errors.append("files 目录必须与 manifest.items 文件清单完全一致")
    recomputed = _batch_id(normalized) if normalized else ""
    if manifest.get("batch_id") != recomputed:
        errors.append(
            f"batch_id 不匹配：manifest={manifest.get('batch_id')!r} recomputed={recomputed!r}"
        )
    totals = manifest.get("totals")
    if isinstance(totals, dict):
        if totals.get("submission_count") != len(normalized):
            errors.append("totals.submission_count 不匹配")
        if totals.get("professor_count") != sum(
            int(item["professor_count"]) for item in normalized
        ):
            errors.append("totals.professor_count 不匹配")
    else:
        errors.append("manifest.totals 必须是对象")
    submission = manifest.get("submission")
    if not isinstance(submission, dict) or str(submission.get("status")) not in {
        "prepared",
        "planned",
        "submitted",
        "unknown",
        "failed",
        "verified",
        "closed",
    }:
        errors.append("submission.status 不是支持的状态")
    if (
        isinstance(submission, dict)
        and "stage" in submission
        and submission["stage"]
        not in ("prepared", "pushing", "creating_pr", "complete")
    ):
        errors.append("submission.stage 不是支持的检查点")
    return {
        "ok": not errors,
        "manifest": str(manifest_path),
        "batch_id": recomputed,
        "item_count": len(normalized),
        "professor_count": sum(int(item["professor_count"]) for item in normalized),
        "status": submission.get("status") if isinstance(submission, dict) else None,
        "errors": errors,
    }


def audit(manifest_path: Path) -> dict[str, object]:
    try:
        result = _audit(manifest_path)
    except (OSError, ValueError, RuntimeError):
        result = {
            "ok": False,
            "manifest": str(manifest_path),
            "errors": ["无法读取批次文件；检查路径、权限和 JSON 格式"],
        }
    if not result["ok"]:
        result["code"] = "AUDIT_FAILED"
        result["next_action"] = "修复报告的文件或 manifest 字段后重新审计"
        result["error_count"] = len(result["errors"])
        result["errors"] = result["errors"][:10]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="审计社区导师投稿批次")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    result = audit(args.manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
