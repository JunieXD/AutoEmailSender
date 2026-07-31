#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import re
import sys
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


SKILL_NAME = "crawl-mentors-to-xlsx"
CANONICAL_SKILL_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "assets/candidates.example.json",
    "assets/professor-import-contract.v1.json",
    "references/crawling-policy.md",
    "references/import-contract.md",
    "scripts/build_professors_xlsx.py",
    "scripts/professor_import_contract.py",
    "scripts/validate_professors_xlsx.py",
    "scripts/xlsx_support.py",
)
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def normalize_version(version: str) -> str:
    normalized = version.strip().removeprefix("v")
    if not VERSION_PATTERN.fullmatch(normalized):
        raise ValueError("版本号必须是 x.y.z 或 x.y.z-suffix")
    return normalized


def package_skill(repo_root: Path, version: str, output_directory: Path) -> Path:
    normalized_version = normalize_version(version)
    resolved_repo_root = repo_root.resolve()
    skill_root = resolved_repo_root / ".agents" / "skills" / SKILL_NAME
    if not skill_root.is_dir():
        raise ValueError(f"Skill 目录不存在：{skill_root}")

    skill_entries = sorted(skill_root.rglob("*"))
    symlinks = [
        path.relative_to(skill_root).as_posix()
        for path in skill_entries
        if path.is_symlink()
    ]
    if symlinks:
        raise ValueError(f"Skill ZIP 不允许符号链接：{', '.join(symlinks)}")

    actual_files = tuple(
        path.relative_to(skill_root).as_posix()
        for path in skill_entries
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    expected_files = tuple(sorted(CANONICAL_SKILL_FILES))
    if actual_files != expected_files:
        missing = sorted(set(expected_files) - set(actual_files))
        unexpected = sorted(set(actual_files) - set(expected_files))
        details = []
        if missing:
            details.append(f"缺少文件：{', '.join(missing)}")
        if unexpected:
            details.append(f"存在未登记文件：{', '.join(unexpected)}")
        raise ValueError("；".join(details))

    resolved_output_directory = output_directory.resolve()
    resolved_output_directory.mkdir(parents=True, exist_ok=True)
    output_path = (
        resolved_output_directory / f"{SKILL_NAME}-v{normalized_version}.zip"
    )

    with ZipFile(output_path, "w") as archive:
        for relative_path in expected_files:
            source_path = skill_root / relative_path
            archive_path = PurePosixPath(SKILL_NAME, relative_path).as_posix()
            info = ZipInfo(archive_path, date_time=ZIP_TIMESTAMP)
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source_path.read_bytes())

    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package crawl-mentors-to-xlsx as a standalone Release ZIP.",
    )
    parser.add_argument("--version", required=True, help="Release version or tag")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="AutoEmailSender repository root",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for the generated ZIP",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        output_path = package_skill(args.repo_root, args.version, args.output_dir)
    except (OSError, ValueError) as error:
        print(f"[fail] {error}", file=sys.stderr)
        return 1
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
