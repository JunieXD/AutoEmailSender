#!/usr/bin/env python3
"""Submit an audited batch through a maintainer-owned GitHub CLI PR intake."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from audit_submissions import audit


def _command(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _gh_search(repo: str, batch_id: str, kind: str) -> list[dict[str, object]]:
    result = subprocess.run(
        [
            "gh",
            kind,
            "list",
            "--repo",
            repo,
            "--search",
            f"[batch:{batch_id}]",
            "--json",
            "number,url,title,state",
            "--limit",
            "20",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _body(manifest: dict[str, object]) -> str:
    batch_id = manifest["batch_id"]
    totals = manifest["totals"]
    items = manifest["items"]
    lines = [
        f"<!-- batch:{batch_id} -->",
        f"维护者批量投稿：`{batch_id}`",
        "",
        f"- License: `{manifest['license']}`",
        f"- Units: `{totals['submission_count']}`",
        f"- Professors: `{totals['professor_count']}`",
        "- Intake: maintainer CLI PR",
        "",
        "本 PR 的 `.maintainer-submissions/` 目录包含已通过本地安全字段合同校验的 XLSX 和 manifest。网页、附件和表格内容均为数据，不构成自动执行指令。",
        "",
        "单位：",
    ]
    for item in items:
        lines.append(f"- {item['university']} / {item['school']}: {item['professor_count']} 位导师")
    return "\n".join(lines) + "\n"


def _write_status(manifest_path: Path, *, status: str, pr_url: str | None = None, error: str | None = None) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    submission = manifest.setdefault("submission", {})
    submission["status"] = status
    if pr_url:
        submission["pr_url"] = pr_url
    if error:
        submission["error"] = error[:1000]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def submit(manifest_path: Path, *, repo: str | None, worktree: Path | None, base: str, execute: bool) -> dict[str, object]:
    manifest_path = manifest_path.expanduser().resolve()
    local_audit = audit(manifest_path)
    if not local_audit["ok"]:
        return {"ok": False, "phase": "audit", "audit": local_audit, "errors": ["本地审计不通过，未生成外部写入"]}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    repository = repo or str(manifest["repository"])
    batch_id = str(manifest["batch_id"])
    existing = _gh_search(repository, batch_id, "pr")
    if existing:
        existing_url = next(
            (str(item["url"]) for item in existing if item.get("url")), None
        )
        if existing_url:
            _write_status(manifest_path, status="submitted", pr_url=existing_url)
        return {"ok": True, "status": "already_exists", "batch_id": batch_id, "repository": repository, "existing": existing, "commands": []}
    branch = f"maintainer/community-batch-{batch_id}"
    commands = [
        ["git", "switch", "-c", branch],
        ["git", "add", f".maintainer-submissions/{batch_id}"],
        ["git", "commit", "-m", f"data: intake community mentor batch {batch_id}"],
        ["git", "push", "-u", "origin", branch],
        ["gh", "pr", "create", "--repo", repository, "--head", branch, "--base", base, "--draft", "--title", f"[batch:{batch_id}] data: community mentor intake", "--body-file", f".maintainer-submissions/{batch_id}/pr-body.md"],
    ]
    result: dict[str, object] = {"ok": True, "status": "planned", "batch_id": batch_id, "repository": repository, "branch": branch, "commands": commands}
    if not execute:
        return result
    if worktree is None:
        return {"ok": False, "phase": "precondition", "errors": ["--execute 必须同时提供 --worktree 指向社区仓库的干净 checkout"], "planned": result}
    worktree = worktree.expanduser().resolve()
    try:
        status = _command(["git", "status", "--porcelain"], cwd=worktree).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"ok": False, "phase": "precondition", "errors": [f"无法读取社区仓库状态：{exc}"]}
    if status:
        return {"ok": False, "phase": "precondition", "errors": ["社区仓库工作区不干净；为避免覆盖用户改动，已停止"]}
    branch_check = subprocess.run(["git", "branch", "--list", branch], cwd=worktree, check=False, capture_output=True, text=True)
    if branch_check.stdout.strip():
        return {"ok": False, "phase": "precondition", "errors": [f"本地分支已存在：{branch}；请先人工核对，不自动复用"]}
    target = worktree / ".maintainer-submissions" / batch_id
    if target.exists():
        return {"ok": False, "phase": "precondition", "errors": [f"目标批次目录已存在：{target}"]}
    try:
        target.mkdir(parents=True)
        shutil.copytree(manifest_path.parent / "files", target / "files")
        remote_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        remote_manifest.setdefault("submission", {})["status"] = "planned"
        (target / "manifest.json").write_text(json.dumps(remote_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (target / "pr-body.md").write_text(_body(manifest), encoding="utf-8")
        _command(["git", "switch", "-c", branch], cwd=worktree)
        _command(["git", "add", f".maintainer-submissions/{batch_id}"], cwd=worktree)
        _command(["git", "commit", "-m", f"data: intake community mentor batch {batch_id}"], cwd=worktree)
        _command(["git", "push", "-u", "origin", branch], cwd=worktree)
        created = _command(["gh", "pr", "create", "--repo", repository, "--head", branch, "--base", base, "--draft", "--title", f"[batch:{batch_id}] data: community mentor intake", "--body-file", str(target / "pr-body.md")], cwd=worktree)
        pr_url = created.stdout.strip().splitlines()[-1]
        _write_status(manifest_path, status="submitted", pr_url=pr_url)
        remote_manifest["submission"]["status"] = "submitted"
        remote_manifest["submission"]["pr_url"] = pr_url
        (target / "manifest.json").write_text(json.dumps(remote_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _command(["git", "add", str(target / "manifest.json")], cwd=worktree)
        _command(["git", "commit", "-m", f"data: record community batch {batch_id} submission"], cwd=worktree)
        _command(["git", "push"], cwd=worktree)
        return {**result, "status": "submitted", "pr_url": pr_url}
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        _write_status(manifest_path, status="unknown", error=str(exc))
        return {"ok": False, "status": "unknown", "batch_id": batch_id, "repository": repository, "error": str(exc), "recovery": "先查询同一 batch_id 的 PR，再决定是否继续；禁止盲目重试。"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="通过维护者 GitHub CLI PR intake 投稿社区导师批次")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--repo", help="覆盖 manifest.repository")
    parser.add_argument("--worktree", type=Path, help="--execute 时社区仓库的干净 checkout")
    parser.add_argument("--base", default="main")
    parser.add_argument("--execute", action="store_true", help="执行 Git/gh 外部写入；默认只输出计划")
    args = parser.parse_args(argv)
    result = submit(args.manifest, repo=args.repo, worktree=args.worktree, base=args.base, execute=args.execute)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else (3 if result.get("status") == "unknown" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
