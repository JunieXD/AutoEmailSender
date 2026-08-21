from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from app.models import Base

DEFAULT_TEMPLATE_ROOT = (
    Path(tempfile.gettempdir()) / "auto-email-sender-test-schema-templates"
)


def create_schema_sqlite_database(
    destination: Path,
    *,
    template_root: Path = DEFAULT_TEMPLATE_ROOT,
) -> None:
    template_path = _template_database_path(template_root)
    if not template_path.exists():
        _create_template_database(template_path)

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_path, destination)


def _template_database_path(template_root: Path) -> Path:
    signature = _schema_signature()
    return template_root / f"{signature}.db"


def _schema_signature() -> str:
    dialect = create_engine("sqlite+pysqlite:///:memory:").dialect
    hasher = hashlib.sha256()
    for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name):
        hasher.update(str(CreateTable(table).compile(dialect=dialect)).encode("utf-8"))
        for index in sorted(table.indexes, key=lambda item: item.name or ""):
            hasher.update(
                str(CreateIndex(index).compile(dialect=dialect)).encode("utf-8")
            )
    return hasher.hexdigest()[:16]


def _create_template_database(template_path: Path) -> None:
    template_path.parent.mkdir(parents=True, exist_ok=True)
    if template_path.exists():
        return

    in_progress_path = template_path.with_suffix(".tmp")
    if in_progress_path.exists():
        in_progress_path.unlink()

    engine = create_engine(f"sqlite+pysqlite:///{in_progress_path.as_posix()}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    in_progress_path.replace(template_path)
