from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a PyInstaller runtime hook with the current CLI build identity.",
    )
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()

    repo_root = arguments.repo_root.resolve()
    revision = _revision(repo_root)
    dirty = _dirty(repo_root)
    output = arguments.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "import os\n"
        f"os.environ['AUTO_EMAIL_SENDER_EMBEDDED_BUILD_REVISION'] = {json.dumps(revision)}\n"
        f"os.environ['AUTO_EMAIL_SENDER_EMBEDDED_BUILD_DIRTY'] = "
        f"{json.dumps('1' if dirty else '0')}\n",
        encoding="utf-8",
    )
    print(json.dumps({"revision": revision, "dirty": dirty, "output": output.as_posix()}))


def _revision(repo_root: Path) -> str:
    override = os.getenv("AUTO_EMAIL_SENDER_BUILD_REVISION")
    if override and override.strip():
        return override.strip()
    result = subprocess.run(
        ["git", "-C", repo_root.as_posix(), "rev-parse", "--verify", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    revision = result.stdout.strip().lower()
    if len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
        raise RuntimeError("git returned an invalid build revision")
    return revision


def _dirty(repo_root: Path) -> bool:
    result = subprocess.run(
        [
            "git",
            "-C",
            repo_root.as_posix(),
            "status",
            "--short",
            # Untracked source can affect a PyInstaller binary just as much as
            # a modified tracked file, so provenance must not call that build
            # clean. Git still excludes ignored build output here.
            "--untracked-files=normal",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


if __name__ == "__main__":
    main()
