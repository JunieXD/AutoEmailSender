from __future__ import annotations

import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = REPOSITORY_ROOT / "scripts" / "packaging"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from package_crawl_mentors_skill import (  # noqa: E402
    CANONICAL_SKILL_FILES,
    SKILL_NAME,
    ZIP_TIMESTAMP,
    normalize_version,
    package_skill,
)


class CrawlMentorsSkillPackageTests(unittest.TestCase):
    def test_packages_a_directly_installable_deterministic_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            first_path = package_skill(
                REPOSITORY_ROOT,
                "v9.9.9",
                temporary_root / "first",
            )
            second_path = package_skill(
                REPOSITORY_ROOT,
                "9.9.9",
                temporary_root / "second",
            )

            self.assertEqual(first_path.name, f"{SKILL_NAME}-v9.9.9.zip")
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())

            with ZipFile(first_path) as archive:
                expected_names = [
                    f"{SKILL_NAME}/{relative_path}"
                    for relative_path in sorted(CANONICAL_SKILL_FILES)
                ]
                self.assertEqual(archive.namelist(), expected_names)
                for info in archive.infolist():
                    with self.subTest(path=info.filename):
                        self.assertEqual(info.date_time, ZIP_TIMESTAMP)
                        relative_path = info.filename.removeprefix(f"{SKILL_NAME}/")
                        source_path = (
                            REPOSITORY_ROOT
                            / ".agents"
                            / "skills"
                            / SKILL_NAME
                            / relative_path
                        )
                        self.assertEqual(archive.read(info), source_path.read_bytes())

    def test_rejects_invalid_versions_and_unregistered_files(self) -> None:
        with self.assertRaisesRegex(ValueError, "版本号"):
            normalize_version("latest")

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            copied_skill_root = (
                temporary_root / ".agents" / "skills" / SKILL_NAME
            )
            copied_skill_root.parent.mkdir(parents=True)
            shutil.copytree(
                REPOSITORY_ROOT / ".agents" / "skills" / SKILL_NAME,
                copied_skill_root,
            )
            (copied_skill_root / "unexpected.txt").write_text(
                "not part of the release payload",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "存在未登记文件"):
                package_skill(temporary_root, "1.0.0", temporary_root / "output")


if __name__ == "__main__":
    unittest.main()
