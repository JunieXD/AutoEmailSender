from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE_ROOT = (
    Path(tempfile.gettempdir()) / "auto-email-sender-test-db-templates"
)


def create_migrated_sqlite_database(
    destination: Path,
    *,
    revision: str = "head",
    backend_dir: Path = BACKEND_DIR,
    template_root: Path = DEFAULT_TEMPLATE_ROOT,
) -> None:
    template_path = _template_database_path(backend_dir, template_root, revision)
    if not template_path.exists():
        _create_template_database(template_path, backend_dir, revision)

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_path, destination)


def _template_database_path(
    backend_dir: Path,
    template_root: Path,
    revision: str,
) -> Path:
    signature = _migration_signature(backend_dir)
    revision_signature = hashlib.sha256(revision.encode("utf-8")).hexdigest()[:12]
    return template_root / f"{signature}-{revision_signature}.db"


def _migration_signature(backend_dir: Path) -> str:
    hasher = hashlib.sha256()
    paths = [
        backend_dir / "alembic.ini",
        backend_dir / "alembic" / "env.py",
        *sorted((backend_dir / "alembic" / "versions").glob("*.py")),
    ]
    for path in paths:
        if not path.exists():
            continue
        hasher.update(path.relative_to(backend_dir).as_posix().encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()[:16]


def _create_template_database(
    template_path: Path,
    backend_dir: Path,
    revision: str,
) -> None:
    template_path.parent.mkdir(parents=True, exist_ok=True)
    if template_path.exists():
        return

    in_progress_path = template_path.with_suffix(".tmp")
    if in_progress_path.exists():
        in_progress_path.unlink()

    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{in_progress_path.as_posix()}"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        if in_progress_path.exists():
            in_progress_path.unlink()
        raise RuntimeError(
            "Alembic migration failed.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}",
        )
    if not in_progress_path.exists():
        raise RuntimeError(f"Alembic migration did not create {in_progress_path}")
    in_progress_path.replace(template_path)
