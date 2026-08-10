from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session

from .schemas import RestartSafetyRead
from .service import get_restart_safety


router = APIRouter(prefix="/api/desktop", tags=["desktop"])


@router.get("/restart-safety", response_model=RestartSafetyRead)
async def read_restart_safety(
    session: AsyncSession = Depends(get_async_session),
) -> RestartSafetyRead:
    return await get_restart_safety(session)
