from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings


logger = logging.getLogger(__name__)


async def run_sqlite_maintenance_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Run bounded, online SQLite maintenance without blocking normal startup."""

    settings = get_settings()
    incremental_vacuum_pages = settings.sqlite_incremental_vacuum_pages
    async with session_factory() as session:
        bind = session.get_bind()
        if bind.dialect.name != "sqlite":
            return 0
        connection = await session.connection()
        optimize_result = await connection.exec_driver_sql("PRAGMA optimize")
        if optimize_result.returns_rows:
            optimize_result.fetchall()

        checkpoint_result = await connection.exec_driver_sql(
            "PRAGMA wal_checkpoint(PASSIVE)",
        )
        if checkpoint_result.returns_rows:
            checkpoint_result.fetchall()

        if incremental_vacuum_pages:
            vacuum_result = await connection.exec_driver_sql(
                f"PRAGMA incremental_vacuum({incremental_vacuum_pages})",
            )
            if vacuum_result.returns_rows:
                vacuum_result.fetchall()
        await session.commit()

    logger.debug("SQLite 在线维护完成")
    return 0
