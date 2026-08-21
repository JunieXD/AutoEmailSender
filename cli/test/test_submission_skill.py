from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPTS = REPO_ROOT / ".agents" / "skills" / "submit-mentors-to-community" / "scripts"
CRAWL_SCRIPTS = REPO_ROOT / ".agents" / "skills" / "crawl-mentors-to-xlsx" / "scripts"
for path in (SKILL_SCRIPTS, CRAWL_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import xlsx_support  # noqa: E402
from audit_submissions import audit  # noqa: E402
from prepare_submissions import prepare  # noqa: E402
from submit_submissions import submit  # noqa: E402
from xlsx_contract import COMMUNITY_COLUMNS  # noqa: E402


def write_community_xlsx(path: Path, *, university: str = "示例大学", school: str = "计算机学院") -> None:
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
        archive.writestr("docProps/app.xml", xlsx_support._app_properties_xml(sheet_names))
        archive.writestr("xl/workbook.xml", xlsx_support._workbook_xml(sheet_names))
        archive.writestr("xl/_rels/workbook.xml.rels", xlsx_support._workbook_relationships_xml(1))
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
            input_path.write_text(json.dumps({"submissions": [{"file": source.name}]}), encoding="utf-8")
            batch_dir = root / "batch"

            result = prepare(input_path, batch_dir, repository="owner/repo", license_name="CC BY 4.0", dry_run=False)
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
            input_path.write_text(json.dumps({"submissions": [{"file": first.name}, {"file": second.name}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                prepare(input_path, root / "batch", repository="owner/repo", license_name="CC BY 4.0", dry_run=True)

    def test_submit_defaults_to_a_non_mutating_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.xlsx"
            write_community_xlsx(source)
            input_path = root / "submissions.json"
            input_path.write_text(json.dumps({"submissions": [{"file": source.name}]}), encoding="utf-8")
            batch_dir = root / "batch"
            prepare(input_path, batch_dir, repository="owner/repo", license_name="CC BY 4.0", dry_run=False)
            with patch("submit_submissions._gh_search", return_value=[]):
                result = submit(batch_dir / "manifest.json", repo=None, worktree=None, base="main", execute=False)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "planned")
            self.assertFalse((root / ".maintainer-submissions").exists())
            self.assertTrue(any(command[0] == "gh" and command[1] == "pr" for command in result["commands"]))

    def test_submit_recovers_an_existing_pr_without_creating_another(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.xlsx"
            write_community_xlsx(source)
            input_path = root / "submissions.json"
            input_path.write_text(json.dumps({"submissions": [{"file": source.name}]}), encoding="utf-8")
            batch_dir = root / "batch"
            prepare(input_path, batch_dir, repository="owner/repo", license_name="CC BY 4.0", dry_run=False)
            with patch("submit_submissions._gh_search", return_value=[{"url": "https://github.com/owner/repo/pull/42", "number": 42}]):
                result = submit(batch_dir / "manifest.json", repo=None, worktree=None, base="main", execute=False)
            self.assertEqual(result["status"], "already_exists")
            manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["submission"]["pr_url"], "https://github.com/owner/repo/pull/42")
            self.assertEqual(manifest["submission"]["status"], "submitted")

    def test_execute_refuses_a_dirty_worktree_before_external_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.xlsx"
            write_community_xlsx(source)
            input_path = root / "submissions.json"
            input_path.write_text(json.dumps({"submissions": [{"file": source.name}]}), encoding="utf-8")
            batch_dir = root / "batch"
            prepare(input_path, batch_dir, repository="owner/repo", license_name="CC BY 4.0", dry_run=False)
            worktree = root / "community"
            worktree.mkdir()
            subprocess.run(["git", "init", "--quiet"], cwd=worktree, check=True)
            (worktree / "uncommitted.txt").write_text("keep", encoding="utf-8")
            with patch("submit_submissions._gh_search", return_value=[]):
                result = submit(batch_dir / "manifest.json", repo=None, worktree=worktree, base="main", execute=True)
            self.assertFalse(result["ok"])
            self.assertEqual(result["phase"], "precondition")
            manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["submission"]["status"], "prepared")


if __name__ == "__main__":
    unittest.main()
