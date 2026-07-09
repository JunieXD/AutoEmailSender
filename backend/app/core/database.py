from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        future=True,
    )
    if engine.sync_engine.dialect.name == "sqlite":
        _configure_sqlite_connection_pragmas(engine, settings)
    return engine


def _configure_sqlite_connection_pragmas(engine: AsyncEngine, settings: object) -> None:
    busy_timeout_ms = max(0, int(getattr(settings, "sqlite_busy_timeout_ms", 5000)))
    wal_enabled = bool(getattr(settings, "sqlite_wal_enabled", True))

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        _ = connection_record
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            requested_journal_mode = "WAL" if wal_enabled else "DELETE"
            try:
                cursor.execute(f"PRAGMA journal_mode={requested_journal_mode}")
            except Exception:
                logger.warning(
                    "设置 SQLite journal_mode=%s 失败，继续使用当前日志模式",
                    requested_journal_mode,
                    exc_info=True,
                )
        finally:
            cursor.close()


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(),
        autoflush=False,
        expire_on_commit=False,
    )


async def get_async_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session


async def dispose_engine() -> None:
    if not get_engine.cache_info().currsize:
        get_session_factory.cache_clear()
        return
    engine = get_engine()
    await engine.dispose()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
