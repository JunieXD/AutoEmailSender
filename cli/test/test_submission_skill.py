from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPTS = (
    REPO_ROOT / ".agents" / "skills" / "submit-mentors-to-community" / "scripts"
)
CRAWL_SCRIPTS = REPO_ROOT / ".agents" / "skills" / "crawl-mentors-to-xlsx" / "scripts"
for path in (SKILL_SCRIPTS, CRAWL_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import submit_submissions
import xlsx_support
from audit_submissions import audit
from prepare_submissions import prepare
from submit_submissions import SubmissionError, _blob_id, _branch, _payload, submit
from xlsx_contract import COMMUNITY_COLUMNS


def write_community_xlsx(
    path: Path, *, university: str = "示例大学", school: str = "计算机学院"
) -> None:
    records = [
        {
            "name": "张三",
            "email": "zhangsan@example.edu.cn",
            "title": "教授",
            "university": university,
            "school": school,
            "department": "计算机科学系",
            "research_direction": "人工智能",
            "recent_papers": "",
            "profile_url": "https://example.edu.cn/zhangsan",
            "source_url": "https://example.edu.cn/faculty",
        }
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_names = ("community-share",)
    with ZipFile(path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("[Content_Types].xml", xlsx_support._content_types_xml(1))
        archive.writestr("_rels/.rels", xlsx_support._root_relationships_xml())
        archive.writestr("docProps/core.xml", xlsx_support._core_properties_xml())
        archive.writestr(
            "docProps/app.xml", xlsx_support._app_properties_xml(sheet_names)
        )
        archive.writestr("xl/workbook.xml", xlsx_support._workbook_xml(sheet_names))
        archive.writestr(
            "xl/_rels/workbook.xml.rels", xlsx_support._workbook_relationships_xml(1)
        )
        archive.writestr("xl/styles.xml", xlsx_support._styles_xml())
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            xlsx_support._worksheet_xml(
                columns=COMMUNITY_COLUMNS,
                rows=records,
                widths=(18, 30, 16, 24, 24, 24, 34, 54, 48, 48),
                header_style_id=1,
                url_fields=frozenset({"profile_url", "source_url"}),
            ),
        )


class SubmissionSkillTests(unittest.TestCase):
    def test_prepare_and_audit_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.xlsx"
            write_community_xlsx(source)
            input_path = root / "submissions.json"
            input_path.write_text(
                json.dumps({"submissions": [{"file": source.name}]}), encoding="utf-8"
            )
            batch_dir = root / "batch"

            result = prepare(
                input_path,
                batch_dir,
                repository="owner/repo",
                license_name="CC BY 4.0",
                dry_run=False,
            )
            self.assertRegex(str(result["batch_id"]), re.compile(r"^[0-9a-f]{16}$"))
            manifest_path = batch_dir / "manifest.json"
            audited = audit(manifest_path)
            self.assertTrue(audited["ok"], audited)
            self.assertEqual(audited["batch_id"], result["batch_id"])
            self.assertEqual((batch_dir / "files" / "001.xlsx").exists(), True)

            with (batch_dir / "files" / "001.xlsx").open("ab") as stream:
                stream.write(b"changed")
            changed = audit(manifest_path)
            self.assertFalse(changed["ok"])
            self.assertTrue(any("sha256" in error for error in changed["errors"]))

    def test_prepare_rejects_duplicate_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "one.xlsx"
            second = root / "two.xlsx"
            write_community_xlsx(first)
            write_community_xlsx(second)
            input_path = root / "submissions.json"
            input_path.write_text(
                json.dumps(
                    {"submissions": [{"file": first.name}, {"file": second.name}]}
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                prepare(
                    input_path,
                    root / "batch",
                    repository="owner/repo",
                    license_name="CC BY 4.0",
                    dry_run=True,
                )

    def test_submit_defaults_to_a_non_mutating_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.xlsx"
            write_community_xlsx(source)
            input_path = root / "submissions.json"
            input_path.write_text(
                json.dumps({"submissions": [{"file": source.name}]}), encoding="utf-8"
            )
            batch_dir = root / "batch"
            prepare(
                input_path,
                batch_dir,
                repository="owner/repo",
                license_name="CC BY 4.0",
                dry_run=False,
            )
            with patch("submit_submissions._gh_search", return_value=([], None)):
                result = submit(
                    batch_dir / "manifest.json",
                    repo=None,
                    worktree=None,
                    base="main",
                    execute=False,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "planned")
            self.assertFalse((root / ".maintainer-submissions").exists())
            self.assertEqual(result["preflight"], "incomplete")
            self.assertEqual(
                json.loads((batch_dir / "manifest.json").read_text())["submission"][
                    "status"
                ],
                "prepared",
            )

    def test_execute_refuses_a_dirty_worktree_before_external_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.xlsx"
            write_community_xlsx(source)
            input_path = root / "submissions.json"
            input_path.write_text(
                json.dumps({"submissions": [{"file": source.name}]}), encoding="utf-8"
            )
            batch_dir = root / "batch"
            prepare(
                input_path,
                batch_dir,
                repository="owner/repo",
                license_name="CC BY 4.0",
                dry_run=False,
            )
            worktree = root / "community"
            worktree.mkdir()
            subprocess.run(["git", "init", "--quiet"], cwd=worktree, check=True)
            (worktree / "uncommitted.txt").write_text("keep", encoding="utf-8")
            with patch("submit_submissions._gh_search", return_value=([], None)):
                result = submit(
                    batch_dir / "manifest.json",
                    repo=None,
                    worktree=worktree,
                    base="main",
                    execute=True,
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["phase"], "precondition")
            manifest = json.loads(
                (batch_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["submission"]["status"], "prepared")

    def test_execute_stops_when_dedupe_query_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.xlsx"
            write_community_xlsx(source)
            input_path = root / "submissions.json"
            input_path.write_text(
                json.dumps({"submissions": [{"file": source.name}]}), encoding="utf-8"
            )
            batch_dir = root / "batch"
            prepare(
                input_path,
                batch_dir,
                repository="owner/repo",
                license_name="CC BY 4.0",
                dry_run=False,
            )
            with patch(
                "submit_submissions._gh_search", return_value=([], "not logged in")
            ):
                result = submit(
                    batch_dir / "manifest.json",
                    repo=None,
                    worktree=root,
                    base="main",
                    execute=True,
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["phase"], "dedupe")
            self.assertEqual(result["status"], "unknown")

    def make_batch(self, root: Path) -> Path:
        source = root / "source.xlsx"
        write_community_xlsx(source)
        input_path = root / "input.json"
        input_path.write_text(json.dumps({"submissions": [{"file": source.name}]}))
        prepare(
            input_path,
            root / "batch",
            repository="owner/repo",
            license_name="CC BY 4.0",
            dry_run=False,
        )
        return root / "batch" / "manifest.json"

    def git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
        ).stdout.strip()

    def make_repository(self, root: Path) -> tuple[Path, Path]:
        remote = root / "remote.git"
        self.git(root, "init", "--bare", "--initial-branch=main", str(remote))
        checkout = root / "community"
        self.git(root, "clone", str(remote), str(checkout))
        self.git(checkout, "config", "user.name", "Test Maintainer")
        self.git(checkout, "config", "user.email", "maintainer@example.org")
        (checkout / "README.md").write_text("community\n")
        self.git(checkout, "add", ".")
        self.git(checkout, "commit", "-m", "initial")
        self.git(checkout, "push", "origin", "main")
        # The configured remote is checked as GitHub; transport alone is redirected
        # by the test command adapter, so no test can push to the network.
        self.git(
            checkout, "remote", "set-url", "origin", "https://github.com/owner/repo.git"
        )
        return checkout, remote

    def fake_remote(
        self, remote: Path, manifest_path: Path, *, fail_push=False, fail_create=False
    ):
        real_command = submit_submissions._command
        manifest = json.loads(manifest_path.read_text())
        payload = _payload(manifest, manifest_path)
        branch = _branch(manifest["batch_id"])
        state = {
            "pr": None,
            "creates": 0,
            "pushes": 0,
            "fail_push": fail_push,
            "fail_create": fail_create,
        }

        def command(args, *, cwd=None):
            if args[0] == "gh":
                if args[1:3] == ["repo", "view"]:
                    output = {
                        "nameWithOwner": "owner/repo",
                        "viewerPermission": "WRITE",
                    }
                elif args[1:3] == ["pr", "list"]:
                    self.assertIn("--state", args)
                    self.assertIn("all", args)
                    self.assertEqual(args[args.index("--head") + 1], branch)
                    output = [state["pr"]] if state["pr"] else []
                elif args[1] == "api":
                    output = [
                        [
                            {"filename": name, "sha": _blob_id(data), "status": "added"}
                            for name, data in payload.items()
                        ]
                    ]
                elif args[1:3] == ["pr", "create"]:
                    state["creates"] += 1
                    state["pr"] = {
                        "number": 42,
                        "url": "https://github.com/owner/repo/pull/42",
                        "state": "OPEN",
                        "headRefName": branch,
                        "baseRefName": "main",
                        "isCrossRepository": False,
                        "body": f"<!-- batch:{manifest['batch_id']} -->",
                    }
                    if state["fail_create"]:
                        raise SubmissionError("COMMAND_TIMEOUT", "query remote")
                    return subprocess.CompletedProcess(
                        args, 0, state["pr"]["url"] + "\n", ""
                    )
                else:
                    self.fail(f"Unexpected gh command: {args}")
                return subprocess.CompletedProcess(args, 0, json.dumps(output), "")
            self.assertEqual(args[0], "git")
            if "origin" in args and any(
                part in args for part in ("fetch", "push", "ls-remote")
            ):
                args = [str(remote) if arg == "origin" else arg for arg in args]
            result = real_command(args, cwd=cwd)
            if "push" in args:
                state["pushes"] += 1
                if state["fail_push"]:
                    state["fail_push"] = False
                    raise SubmissionError("COMMAND_TIMEOUT", "query remote")
            return result

        return state, command

    def test_success_from_base_preserves_dirty_unrelated_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.make_batch(root)
            checkout, remote = self.make_repository(root)
            self.git(checkout, "switch", "-c", "unrelated")
            (checkout / "unrelated.txt").write_text("not for upload")
            self.git(checkout, "add", ".")
            self.git(checkout, "commit", "-m", "unrelated")
            (checkout / "local.txt").write_text("keep me")
            before = self.git(checkout, "status", "--porcelain")
            head = self.git(checkout, "rev-parse", "HEAD")
            state, command = self.fake_remote(remote, manifest)
            with patch("submit_submissions._command", side_effect=command):
                planned = submit(
                    manifest, repo=None, worktree=checkout, base="main", execute=False
                )
                self.assertEqual(planned["preflight"], "passed", planned)
                result = submit(
                    manifest, repo=None, worktree=checkout, base="main", execute=True
                )
                self.assertEqual(result["status"], "submitted", result)
                repeated = submit(
                    manifest, repo=None, worktree=checkout, base="main", execute=True
                )
                self.assertEqual(repeated["pr_url"], result["pr_url"])
            self.assertEqual((state["creates"], state["pushes"]), (1, 1))
            self.assertEqual(self.git(checkout, "status", "--porcelain"), before)
            self.assertEqual(self.git(checkout, "rev-parse", "HEAD"), head)
            branch = result["branch"]
            files = self.git(
                remote, "ls-tree", "-r", "--name-only", branch
            ).splitlines()
            self.assertNotIn("unrelated.txt", files)
            self.assertNotIn("local.txt", files)
            self.assertEqual(
                len(self.git(checkout, "worktree", "list").splitlines()), 1
            )

    def test_resume_after_push_and_create_response_loss(self):
        for failure in ("fail_push", "fail_create"):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                manifest = self.make_batch(root)
                checkout, remote = self.make_repository(root)
                state, command = self.fake_remote(remote, manifest, **{failure: True})
                with patch("submit_submissions._command", side_effect=command):
                    first = submit(
                        manifest,
                        repo=None,
                        worktree=checkout,
                        base="main",
                        execute=True,
                    )
                    self.assertEqual(first["status"], "unknown", first)
                    second = submit(
                        manifest,
                        repo=None,
                        worktree=checkout,
                        base="main",
                        execute=True,
                    )
                    self.assertEqual(second["status"], "submitted", second)
                self.assertEqual(state["creates"], 1)

    def test_existing_pr_all_states_and_content_conflict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.make_batch(root)
            checkout, remote = self.make_repository(root)
            state, command = self.fake_remote(remote, manifest)
            with patch("submit_submissions._command", side_effect=command):
                self.assertTrue(
                    submit(
                        manifest,
                        repo=None,
                        worktree=checkout,
                        base="main",
                        execute=True,
                    )["ok"]
                )
                before = manifest.read_bytes()
                for remote_state, status in (
                    ("OPEN", "submitted"),
                    ("MERGED", "verified"),
                    ("CLOSED", "closed"),
                ):
                    state["pr"]["state"] = remote_state
                    result = submit(
                        manifest, repo=None, worktree=None, base="main", execute=False
                    )
                    self.assertEqual(result["status"], status, result)
                    self.assertEqual(manifest.read_bytes(), before)
                state["pr"]["baseRefName"] = "other"
                result = submit(
                    manifest, repo=None, worktree=None, base="main", execute=True
                )
                self.assertEqual(result["code"], "PR_CONFLICT", result)
                state["pr"]["baseRefName"] = "main"
                with (
                    patch(
                        "submit_submissions._json_command",
                        return_value=[
                            [
                                {
                                    "filename": "private.txt",
                                    "sha": "bad",
                                    "status": "added",
                                }
                            ]
                        ],
                    ),
                    patch(
                        "submit_submissions._gh_search",
                        return_value=([state["pr"]], None),
                    ),
                ):
                    result = submit(
                        manifest, repo=None, worktree=None, base="main", execute=False
                    )
                    self.assertEqual(result["code"], "PR_CONTENT_MISMATCH", result)

    def test_wrong_push_target_and_override_are_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.make_batch(root)
            checkout, remote = self.make_repository(root)
            self.git(
                checkout,
                "remote",
                "set-url",
                "--push",
                "origin",
                "https://github.com/other/repo.git",
            )
            state, command = self.fake_remote(remote, manifest)
            with patch("submit_submissions._command", side_effect=command):
                for repo in (None, "other/repo"):
                    result = submit(
                        manifest,
                        repo=repo,
                        worktree=checkout,
                        base="main",
                        execute=True,
                    )
                    self.assertEqual(result["code"], "REPOSITORY_MISMATCH", result)
            self.assertEqual((state["pushes"], state["creates"]), (0, 0))

    def test_unknown_creation_requires_remote_check_before_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.make_batch(root)
            manifest = json.loads(path.read_text())
            manifest["submission"] = {"status": "unknown", "stage": "creating_pr"}
            path.write_text(json.dumps(manifest))
            with patch("submit_submissions._gh_search", return_value=([], None)):
                result = submit(
                    path, repo=None, worktree=None, base="main", execute=True
                )
            self.assertEqual(result["code"], "CREATE_RESULT_UNKNOWN")

    def test_audit_rejects_extra_files_symlinks_and_invalid_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.make_batch(root)
            extra = path.parent / "files" / "private.txt"
            extra.write_text("private")
            self.assertFalse(audit(path)["ok"])
            extra.unlink()
            data = path.parent / "files" / "001.xlsx"
            data.unlink()
            data.symlink_to(root / "source.xlsx")
            self.assertFalse(audit(path)["ok"])
            data.unlink()
            shutil.copyfile(root / "source.xlsx", data)
            original = json.loads(path.read_text())
            for field, value in (
                ("repository", []),
                ("license", "unknown"),
                ("submission", {"status": []}),
            ):
                path.write_text(json.dumps({**original, field: value}))
                self.assertFalse(audit(path)["ok"])

    def test_xlsx_rejects_extra_sheet_parts_invalid_xml_and_extreme_rows(self):
        from xlsx_contract import inspect_xlsx

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "source.xlsx"
            write_community_xlsx(original)
            with ZipFile(original) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
            for change in ("extra_sheet", "xml", "rows", "hidden", "extra_column"):
                changed = dict(entries)
                if change == "extra_sheet":
                    changed["xl/worksheets/sheet2.xml"] = entries[
                        "xl/worksheets/sheet1.xml"
                    ]
                elif change == "xml":
                    changed["xl/workbook.xml"] = b"<invalid"
                elif change == "rows":
                    changed["xl/worksheets/sheet1.xml"] = entries[
                        "xl/worksheets/sheet1.xml"
                    ].replace(b'<row r="2"', b'<row r="999999999"')
                elif change == "hidden":
                    changed["xl/workbook.xml"] = entries["xl/workbook.xml"].replace(
                        b"<sheet ", b'<sheet state="hidden" '
                    )
                else:
                    changed["xl/worksheets/sheet1.xml"] = entries[
                        "xl/worksheets/sheet1.xml"
                    ].replace(b"J2", b"K2")
                target = root / f"{change}.xlsx"
                with ZipFile(target, "w", ZIP_DEFLATED) as archive:
                    for name, data in changed.items():
                        archive.writestr(name, data)
                self.assertFalse(inspect_xlsx(target)["ok"], change)

    def test_skill_runs_without_sibling_skills_or_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / "standalone" / "scripts"
            shutil.copytree(
                SKILL_SCRIPTS, scripts, ignore=shutil.ignore_patterns("__pycache__")
            )
            source = root / "source.xlsx"
            write_community_xlsx(source)
            input_path = root / "input.json"
            input_path.write_text(json.dumps({"submissions": [{"file": source.name}]}))
            run = subprocess.run(
                [
                    sys.executable,
                    str(scripts / "prepare_submissions.py"),
                    "--input",
                    str(input_path),
                    "--output-dir",
                    str(root / "batch"),
                ],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                env={"PATH": "/usr/bin:/bin"},
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            output = json.loads(run.stdout)
            self.assertEqual(output["status"], "prepared")
            self.assertNotIn("items", output)
            self.assertTrue(Path(output["manifest"]).is_file())

    def test_dedupe_missing_tool_and_malformed_response_are_structured(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self.make_batch(Path(temporary))
            for error in (FileNotFoundError(), subprocess.TimeoutExpired("gh", 120)):
                with patch("submit_submissions.subprocess.run", side_effect=error):
                    result = submit(
                        path, repo=None, worktree=None, base="main", execute=False
                    )
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "unknown")
            with patch(
                "submit_submissions._command",
                return_value=subprocess.CompletedProcess([], 0, "not json", ""),
            ):
                result = submit(
                    path, repo=None, worktree=None, base="main", execute=False
                )
            self.assertEqual(result["code"], "INVALID_REMOTE_RESPONSE")

    def test_retry_create_after_confirmed_absence_and_conflicting_remote_branch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.make_batch(root)
            checkout, remote = self.make_repository(root)
            state, command = self.fake_remote(remote, manifest, fail_create=True)
            with patch("submit_submissions._command", side_effect=command):
                first = submit(
                    manifest, repo=None, worktree=checkout, base="main", execute=True
                )
                self.assertEqual(first["status"], "unknown", first)
                state["pr"] = None
                state["fail_create"] = False
                blocked = submit(
                    manifest, repo=None, worktree=checkout, base="main", execute=True
                )
                self.assertEqual(blocked["code"], "CREATE_RESULT_UNKNOWN", blocked)
                resumed = submit(
                    manifest,
                    repo=None,
                    worktree=checkout,
                    base="main",
                    execute=True,
                    retry_create=True,
                )
                self.assertEqual(resumed["status"], "submitted", resumed)
                # A conflicting branch is never overwritten, even on explicit retry.
                state["pr"] = None
                branch = resumed["branch"]
                self.git(checkout, "fetch", str(remote), branch)
                self.git(checkout, "switch", "--detach", "FETCH_HEAD")
                (checkout / "unexpected.txt").write_text("unreviewed")
                self.git(checkout, "add", ".")
                self.git(checkout, "commit", "-m", "conflict")
                self.git(checkout, "push", str(remote), f"HEAD:refs/heads/{branch}")
                before = self.git(remote, "rev-parse", branch)
                blocked = submit(
                    manifest,
                    repo=None,
                    worktree=checkout,
                    base="main",
                    execute=True,
                    retry_create=True,
                )
                self.assertEqual(blocked["code"], "BRANCH_CONTENT_MISMATCH", blocked)
                self.assertEqual(self.git(remote, "rev-parse", branch), before)

    def test_prepare_detects_changed_bytes_during_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.xlsx"
            write_community_xlsx(source)
            input_path = root / "input.json"
            input_path.write_text(json.dumps([{"file": source.name}]))
            original_copy = shutil.copyfile

            def changed_copy(source, destination):
                original_copy(source, destination)
                with Path(destination).open("ab") as stream:
                    stream.write(b"changed")

            with (
                patch("prepare_submissions.shutil.copyfile", side_effect=changed_copy),
                self.assertRaises(ValueError),
            ):
                prepare(
                    input_path,
                    root / "batch",
                    repository="owner/repo",
                    license_name="CC BY 4.0",
                    dry_run=False,
                )
            self.assertFalse((root / "batch").exists())

    def test_oversize_rejected_before_hash_or_workbook_read(self):
        from xlsx_contract import MAX_BYTES, inspect_xlsx

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "large.xlsx"
            with path.open("wb") as stream:
                stream.truncate(MAX_BYTES + 1)
            with (
                patch("xlsx_contract.read_workbook") as reader,
                patch("xlsx_contract.sha256_file") as hasher,
            ):
                self.assertFalse(inspect_xlsx(path)["ok"])
                reader.assert_not_called()
                hasher.assert_not_called()


if __name__ == "__main__":
    unittest.main()
