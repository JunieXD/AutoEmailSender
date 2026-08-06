from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session

from .schemas import RuntimeSettingsRead, RuntimeSettingsUpdate
from .service import (
    get_runtime_settings,
    serialize_runtime_settings,
    update_runtime_settings,
)


router = APIRouter(prefix="/api/runtime-settings", tags=["runtime-settings"])


@router.get("", response_model=RuntimeSettingsRead)
async def read_runtime_settings(
    session: AsyncSession = Depends(get_async_session),
) -> RuntimeSettingsRead:
    settings = await get_runtime_settings(session)
    await session.commit()
    return serialize_runtime_settings(settings)


@router.patch("", response_model=RuntimeSettingsRead)
async def patch_runtime_settings(
    payload: RuntimeSettingsUpdate,
    session: AsyncSession = Depends(get_async_session),
) -> RuntimeSettingsRead:
    settings = await update_runtime_settings(session, payload)
    await session.commit()
    return serialize_runtime_settings(settings)
