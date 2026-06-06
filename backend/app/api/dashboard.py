from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.schemas.dashboard import DashboardOverviewRead
from app.services.dashboard_stats import build_dashboard_overview


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/overview", response_model=DashboardOverviewRead)
async def get_dashboard_overview(
    identity_id: int = Query(..., ge=1),
    llm_profile_id: int | None = Query(default=None, ge=1),
    university: str | None = Query(default=None),
    school: str | None = Query(default=None),
    email_university: str | None = Query(default=None),
    email_school: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> DashboardOverviewRead:
    try:
        return await build_dashboard_overview(
            session,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            university=university,
            school=school,
            email_university=email_university,
            email_school=email_school,
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
