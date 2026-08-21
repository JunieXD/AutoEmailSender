from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
import logging
from time import perf_counter

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
    foreign_keys_enabled = bool(
        getattr(settings, "sqlite_foreign_keys_enabled", True),
    )
    synchronous = str(getattr(settings, "sqlite_synchronous", "NORMAL")).upper()
    if synchronous not in {"OFF", "NORMAL", "FULL", "EXTRA"}:
        synchronous = "NORMAL"
    cache_size_kib = max(1, int(getattr(settings, "sqlite_cache_size_mib", 64))) * 1024
    mmap_size_bytes = (
        max(0, int(getattr(settings, "sqlite_mmap_size_mib", 256))) * 1024 * 1024
    )
    wal_autocheckpoint_pages = max(
        1,
        int(getattr(settings, "sqlite_wal_autocheckpoint_pages", 1000)),
    )
    journal_size_limit_bytes = (
        max(1, int(getattr(settings, "sqlite_journal_size_limit_mib", 64)))
        * 1024
        * 1024
    )
    slow_query_seconds = (
        max(
            0,
            int(getattr(settings, "sqlite_slow_query_ms", 250)),
        )
        / 1000
    )

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
            cursor.execute(
                f"PRAGMA foreign_keys={'ON' if foreign_keys_enabled else 'OFF'}",
            )
            cursor.execute(f"PRAGMA synchronous={synchronous}")
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.execute(f"PRAGMA cache_size=-{cache_size_kib}")
            cursor.execute(f"PRAGMA mmap_size={mmap_size_bytes}")
            cursor.execute(f"PRAGMA wal_autocheckpoint={wal_autocheckpoint_pages}")
            cursor.execute(f"PRAGMA journal_size_limit={journal_size_limit_bytes}")
        finally:
            cursor.close()

    if slow_query_seconds <= 0:
        return

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def start_query_timer(
        _connection,
        _cursor,
        _statement,
        _parameters,
        context,
        _executemany,
    ) -> None:  # type: ignore[no-untyped-def]
        context._auto_email_sender_query_started_at = perf_counter()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def log_slow_query(
        _connection,
        _cursor,
        statement,
        _parameters,
        context,
        executemany,
    ) -> None:  # type: ignore[no-untyped-def]
        started_at = getattr(
            context,
            "_auto_email_sender_query_started_at",
            None,
        )
        if started_at is None:
            return
        elapsed_seconds = perf_counter() - started_at
        if elapsed_seconds < slow_query_seconds:
            return
        normalized_statement = " ".join(str(statement).split())[:2_000]
        logger.warning(
            "slow_sqlite_query elapsed_ms=%.1f executemany=%s sql=%s",
            elapsed_seconds * 1000,
            bool(executemany),
            normalized_statement,
        )


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
