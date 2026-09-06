"""Audit, plan and resume a maintainer PR using Python, Git and gh only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from audit_submissions import audit


class SubmissionError(Exception):
    def __init__(self, code: str, action: str):
        self.code = code
        self.action = action
        super().__init__(code)


def _command(
    args: list[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"},
        )
    except FileNotFoundError:
        raise SubmissionError("DEPENDENCY_MISSING", f"安装 {args[0]} 后重试") from None
    except subprocess.TimeoutExpired:
        raise SubmissionError(
            "COMMAND_TIMEOUT", "检查网络；重新运行同一 manifest 查询远端状态"
        ) from None
    except (OSError, subprocess.CalledProcessError):
        raise SubmissionError(
            "COMMAND_FAILED", f"检查 {args[0]} {args[1]} 的认证、权限和本地配置后重试"
        ) from None


def _json_command(args: list[str]) -> object:
    try:
        return json.loads(_command(args).stdout)
    except json.JSONDecodeError:
        raise SubmissionError(
            "INVALID_REMOTE_RESPONSE", "检查 gh 版本及网络后重试"
        ) from None


def _gh_search(
    repo: str, batch_id: str, kind: str = "pr"
) -> tuple[list[dict], str | None]:
    try:
        payload = _json_command(
            [
                "gh",
                kind,
                "list",
                "--repo",
                repo,
                "--head",
                _branch(batch_id),
                "--state",
                "all",
                "--limit",
                "100",
                "--json",
                "number,url,state,body,headRefName,baseRefName,isCrossRepository",
            ]
        )
        if not isinstance(payload, list) or any(
            not isinstance(item, dict) for item in payload
        ):
            return [], "INVALID_REMOTE_RESPONSE"
        if len(payload) >= 100:
            return [], "DEDUPE_LIMIT_REACHED"
        return payload, None
    except SubmissionError as exc:
        return [], exc.code


def _branch(batch_id: str) -> str:
    return f"maintainer/community-batch-{batch_id}"


def _public_manifest(manifest: dict) -> dict:
    # Local progress/errors and arbitrary input keys must never enter the PR.
    result = {
        key: manifest[key]
        for key in ("schema_version", "batch_id", "repository", "license", "totals")
    }
    fields = (
        "index",
        "university",
        "school",
        "department",
        "file",
        "sha256",
        "size_bytes",
        "professor_count",
    )
    result["items"] = [{key: item[key] for key in fields} for item in manifest["items"]]
    result["totals"] = {
        key: manifest["totals"][key] for key in ("submission_count", "professor_count")
    }
    result["submission"] = {
        "status": "planned",
        "issue_urls": [],
        "pr_url": None,
        "error": None,
    }
    return result


def _encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _payload(manifest: dict, manifest_path: Path) -> dict[str, bytes]:
    prefix = f".maintainer-submissions/{manifest['batch_id']}/"
    result = {
        prefix + "manifest.json": _encoded(_public_manifest(manifest)),
        prefix + "pr-body.md": _body(manifest).encode("utf-8"),
    }
    for item in manifest["items"]:
        data = (manifest_path.parent / item["file"]).read_bytes()
        if (
            len(data) != item["size_bytes"]
            or hashlib.sha256(data).hexdigest() != item["sha256"]
        ):
            raise SubmissionError(
                "FILES_CHANGED", "重新审计本地批次；文件已在准备后变化"
            )
        result[prefix + item["file"]] = data
    return result


def _blob_id(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def _verify_pr(
    repo: str, existing: dict, batch_id: str, base: str, payload: dict[str, bytes]
) -> None:
    number = existing.get("number")
    if (
        type(number) is not int
        or existing.get("headRefName") != _branch(batch_id)
        or existing.get("baseRefName") != base
        or existing.get("isCrossRepository") is not False
        or f"<!-- batch:{batch_id} -->" not in str(existing.get("body", ""))
        or existing.get("state") not in ("OPEN", "CLOSED", "MERGED")
        or existing.get("url") != f"https://github.com/{repo}/pull/{number}"
    ):
        raise SubmissionError(
            "PR_CONFLICT", "核对同名分支 PR 的目标、批次和状态；不要新建重复投稿"
        )
    pages = _json_command(
        ["gh", "api", f"repos/{repo}/pulls/{number}/files", "--paginate", "--slurp"]
    )
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise SubmissionError("INVALID_REMOTE_RESPONSE", "重新查询 PR 文件")
    files = [item for page in pages for item in page]
    expected = {name: _blob_id(data) for name, data in payload.items()}
    if (
        len(files) != len(expected)
        or any(
            not isinstance(item, dict)
            or item.get("status") != "added"
            or not isinstance(item.get("filename"), str)
            or not isinstance(item.get("sha"), str)
            for item in files
        )
        or {item.get("filename"): item.get("sha") for item in files} != expected
    ):
        raise SubmissionError(
            "PR_CONTENT_MISMATCH",
            "远端 PR 文件与本地审计批次不同；人工核对，不自动覆盖",
        )


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
        lines.append(
            f"- {item['university']} / {item['school']}: {item['professor_count']} 位导师"
        )
    return "\n".join(lines) + "\n"


def _write_status(
    path: Path, *, status: str, stage: str, pr_url: str | None = None
) -> None:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["submission"] = {
        "status": status,
        "stage": stage,
        "pr_url": pr_url,
        "issue_urls": [],
        "error": None,
    }
    descriptor, temporary = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_encoded(manifest))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _origin_repository(url: str) -> str | None:
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?",
        url,
    )
    return match.group(1).casefold() if match else None


def _preflight(worktree: Path, repo: str, base: str) -> None:
    _command(["git", "check-ref-format", "--branch", base], cwd=worktree)
    for mode in ([], ["--push"]):
        urls = _command(
            ["git", "remote", "get-url", *mode, "--all", "origin"], cwd=worktree
        ).stdout.splitlines()
        if not urls or any(_origin_repository(url) != repo.casefold() for url in urls):
            raise SubmissionError(
                "REPOSITORY_MISMATCH",
                "使用 origin 的 fetch/push 地址均匹配 manifest.repository 的社区仓库 checkout",
            )
    permission = _json_command(
        ["gh", "repo", "view", repo, "--json", "nameWithOwner,viewerPermission"]
    )
    if (
        not isinstance(permission, dict)
        or str(permission.get("nameWithOwner", "")).casefold() != repo.casefold()
        or permission.get("viewerPermission") not in ("ADMIN", "MAINTAIN", "WRITE")
    ):
        raise SubmissionError(
            "PUSH_PERMISSION_REQUIRED",
            "使用有目标仓库写入权限的 gh 账号；普通贡献者使用应用 Issue Form",
        )
    if not _command(
        ["git", "ls-remote", "--heads", "origin", f"refs/heads/{base}"], cwd=worktree
    ).stdout.strip():
        raise SubmissionError("BASE_NOT_FOUND", "指定目标仓库现有的 --base 分支")


def _verify_tree(checkout: Path, baseline: str, payload: dict[str, bytes]) -> None:
    # Compare Git blobs as well as the entire diff: filters/hooks must not alter reviewed bytes.
    changes = _command(
        ["git", "diff", "--name-status", "--no-renames", baseline, "HEAD"], cwd=checkout
    ).stdout.splitlines()
    if set(changes) != {f"A\t{name}" for name in payload}:
        raise SubmissionError(
            "BRANCH_CONTENT_MISMATCH",
            "远端投稿分支包含不同文件或其他改动；人工核对后恢复",
        )
    for name, data in payload.items():
        blob = _command(
            ["git", "rev-parse", f"HEAD:{name}"], cwd=checkout
        ).stdout.strip()
        mode = _command(
            ["git", "ls-tree", "HEAD", "--", name], cwd=checkout
        ).stdout.split()[0]
        if blob != _blob_id(data) or mode != "100644":
            raise SubmissionError(
                "BRANCH_CONTENT_MISMATCH",
                "Git 文件内容与审计结果不一致；检查远端分支或 Git 过滤器",
            )


def submit(
    manifest_path: Path,
    *,
    repo: str | None,
    worktree: Path | None,
    base: str,
    execute: bool,
    retry_create: bool = False,
) -> dict[str, object]:
    try:
        manifest_path = manifest_path.expanduser().resolve()
        local_audit = audit(manifest_path)
    except (OSError, ValueError, RuntimeError):
        return {
            "ok": False,
            "status": "blocked",
            "code": "LOCAL_IO_ERROR",
            "next_action": "检查 manifest 路径和读取权限",
        }
    if not local_audit["ok"]:
        return {
            "ok": False,
            "status": "blocked",
            "phase": "audit",
            "code": "AUDIT_FAILED",
            "errors": local_audit["errors"],
            "next_action": "修复文件/manifest 后重新审计",
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {
            "ok": False,
            "status": "blocked",
            "code": "LOCAL_IO_ERROR",
            "next_action": "manifest 在审计后无法读取；重新运行审计",
        }
    repository = manifest["repository"]
    batch_id = manifest["batch_id"]
    result = {
        "batch_id": batch_id,
        "repository": repository,
        "license": manifest["license"],
        "totals": manifest["totals"],
        "manifest": str(manifest_path),
        "base": base,
        "branch": _branch(batch_id),
    }
    phase = "precondition"
    external_started = False
    stage = manifest["submission"].get("stage", "prepared")
    try:
        if repo and repo.casefold() != repository.casefold():
            raise SubmissionError(
                "REPOSITORY_MISMATCH",
                "目标与 manifest 不同；使用 prepare --repository 准备正确的投稿计划",
            )
        if (
            not base
            or base.startswith("-")
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", base)
        ):
            raise SubmissionError("INVALID_BASE", "指定有效的目标分支 --base")
        payload = _payload(manifest, manifest_path)
        phase = "dedupe"
        existing, error = _gh_search(repository, batch_id)
        if error:
            return {
                **result,
                "ok": False,
                "status": "unknown",
                "phase": phase,
                "code": error,
                "next_action": "检查 gh auth status 和网络后重新运行同一命令；查重失败，尚未执行投稿",
            }
        if existing:
            if len(existing) != 1:
                raise SubmissionError(
                    "PR_CONFLICT", "同名分支有多个 PR；人工核对，不自动选择"
                )
            _verify_pr(repository, existing[0], batch_id, base, payload)
            state = {"OPEN": "submitted", "MERGED": "verified", "CLOSED": "closed"}[
                existing[0]["state"]
            ]
            if execute:
                _write_status(
                    manifest_path,
                    status=state,
                    stage="complete",
                    pr_url=existing[0]["url"],
                )
            return {
                **result,
                "ok": True,
                "status": state,
                "pr_url": existing[0]["url"],
                "next_action": "投稿已关闭，需人工核对处理结果"
                if state == "closed"
                else "none",
            }
        if (
            (
                stage in {"creating_pr", "complete"}
                or manifest["submission"]["status"]
                in {"unknown", "submitted", "verified", "closed"}
            )
            and not retry_create
            and stage != "pushing"
        ):
            return {
                **result,
                "ok": False,
                "status": "unknown",
                "phase": "dedupe",
                "code": "CREATE_RESULT_UNKNOWN",
                "next_action": "按 references/recovery.md 核对远端；确认没有 PR 后用 --retry-create --execute 继续",
            }
        phase = "precondition"
        if worktree is None:
            if execute:
                raise SubmissionError(
                    "WORKTREE_REQUIRED", "提供 --worktree <社区仓库 checkout> 后重试"
                )
            return {
                **result,
                "ok": True,
                "status": "planned",
                "preflight": "incomplete",
                "next_action": "加 --worktree <社区仓库 checkout> 完成目标、权限和基线检查",
            }
        worktree = worktree.expanduser().resolve()
        _preflight(worktree, repository, base)
        if not execute:
            return {
                **result,
                "ok": True,
                "status": "planned",
                "preflight": "passed",
                "next_action": "已获该批次、仓库和许可证授权时，以相同参数加 --execute 投稿 draft PR",
            }
        phase = "prepare_branch"
        _command(
            ["git", "fetch", "--no-tags", "origin", f"refs/heads/{base}"], cwd=worktree
        )
        baseline = _command(
            ["git", "rev-parse", "FETCH_HEAD"], cwd=worktree
        ).stdout.strip()
        branch = _branch(batch_id)
        remote = _command(
            ["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
            cwd=worktree,
        ).stdout.strip()
        start = baseline
        if remote:
            _command(
                ["git", "fetch", "--no-tags", "origin", f"refs/heads/{branch}"],
                cwd=worktree,
            )
            start = _command(
                ["git", "rev-parse", "FETCH_HEAD"], cwd=worktree
            ).stdout.strip()
        with tempfile.TemporaryDirectory(prefix="community-submit-") as temporary:
            checkout = Path(temporary).resolve() / "checkout"
            _command(
                ["git", "worktree", "add", "--detach", str(checkout), start],
                cwd=worktree,
            )
            try:
                if not remote:
                    for name, data in payload.items():
                        target = checkout / name
                        if target.exists() or any(
                            parent.is_symlink() for parent in target.parents
                        ):
                            raise SubmissionError(
                                "TARGET_EXISTS",
                                "目标批次目录已存在或经过符号链接；核对已合并投稿",
                            )
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)
                    copied = audit(
                        checkout / f".maintainer-submissions/{batch_id}/manifest.json"
                    )
                    if not copied["ok"]:
                        raise SubmissionError(
                            "COPIED_AUDIT_FAILED", "临时工作区审计失败；检查本地文件"
                        )
                    _command(["git", "add", "--", *payload], cwd=checkout)
                    _command(
                        [
                            "git",
                            "-c",
                            "core.hooksPath=/dev/null",
                            "commit",
                            "--no-gpg-sign",
                            "-m",
                            f"data: intake community mentor batch {batch_id}",
                        ],
                        cwd=checkout,
                    )
                comparison = _command(
                    ["git", "merge-base", baseline, "HEAD"], cwd=checkout
                ).stdout.strip()
                _verify_tree(checkout, comparison, payload)
                phase = "push"
                stage = "pushing"
                _write_status(manifest_path, status="planned", stage=stage)
                external_started = True
                # No force: concurrent changes fail rather than overwrite another submission.
                _command(
                    [
                        "git",
                        "-c",
                        "core.hooksPath=/dev/null",
                        "push",
                        "origin",
                        f"HEAD:refs/heads/{branch}",
                    ],
                    cwd=checkout,
                )
                phase = "create_pr"
                # Recheck after push, including races with another submitter.
                found, error = _gh_search(repository, batch_id)
                if error:
                    raise SubmissionError(
                        error, "恢复网络后重新运行同一 manifest 查询 PR"
                    )
                if found:
                    if len(found) != 1:
                        raise SubmissionError(
                            "PR_CONFLICT", "同名分支有多个 PR；人工核对"
                        )
                    _verify_pr(repository, found[0], batch_id, base, payload)
                    state = {
                        "OPEN": "submitted",
                        "MERGED": "verified",
                        "CLOSED": "closed",
                    }[found[0]["state"]]
                    url = found[0]["url"]
                else:
                    stage = "creating_pr"
                    _write_status(manifest_path, status="unknown", stage=stage)
                    body_path = Path(temporary) / "pr-body.md"
                    body_path.write_bytes(
                        payload[f".maintainer-submissions/{batch_id}/pr-body.md"]
                    )
                    created = _command(
                        [
                            "gh",
                            "pr",
                            "create",
                            "--repo",
                            repository,
                            "--head",
                            branch,
                            "--base",
                            base,
                            "--draft",
                            "--title",
                            f"[batch:{batch_id}] data: community mentor intake",
                            "--body-file",
                            str(body_path),
                        ],
                        cwd=checkout,
                    )
                    url = created.stdout.strip()
                    if not re.fullmatch(
                        rf"https://github.com/{re.escape(repository)}/pull/[0-9]+", url
                    ):
                        raise SubmissionError(
                            "INVALID_REMOTE_RESPONSE", "查询远端 PR，创建结果未知"
                        )
                    state = "submitted"
                _write_status(manifest_path, status=state, stage="complete", pr_url=url)
                return {
                    **result,
                    "ok": True,
                    "status": state,
                    "pr_url": url,
                    "next_action": "none",
                }
            finally:
                # Only remove the disposable worktree created by this invocation.
                try:
                    _command(
                        ["git", "worktree", "remove", "--force", str(checkout)],
                        cwd=worktree,
                    )
                except SubmissionError:
                    pass
    except SubmissionError as exc:
        return {
            **result,
            "ok": False,
            "status": "unknown" if external_started else "blocked",
            "phase": phase,
            "code": exc.code,
            "next_action": exc.action,
        }
    except (OSError, ValueError):
        return {
            **result,
            "ok": False,
            "status": "unknown" if external_started else "blocked",
            "phase": phase,
            "code": "LOCAL_IO_ERROR",
            "next_action": "检查本地文件及写入权限；重新运行同一 manifest 查询状态",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="校验并通过外部 Git/gh 投稿 draft PR；默认只读计划/查询"
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--repo", help="核对目标（必须匹配 manifest.repository）")
    parser.add_argument(
        "--worktree", type=Path, help="社区仓库 checkout；投稿使用独立临时工作区"
    )
    parser.add_argument("--base", default="main")
    parser.add_argument(
        "--execute", action="store_true", help="执行投稿或恢复，并更新本地状态"
    )
    parser.add_argument(
        "--retry-create",
        action="store_true",
        help="已核实上次未知结果没有创建 PR 时允许继续",
    )
    args = parser.parse_args(argv)
    result = submit(
        args.manifest,
        repo=args.repo,
        worktree=args.worktree,
        base=args.base,
        execute=args.execute,
        retry_create=args.retry_create,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else (3 if result.get("status") == "unknown" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
