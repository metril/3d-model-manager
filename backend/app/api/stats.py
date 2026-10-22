"""Dashboard stats API (R11-B item 13): one cheap aggregate endpoint the
frontend's `/dashboard` page polls. See `app.services.stats` for the
30s in-process cache and the actual queries.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_session, visible_printer_ids
from app.config import Settings, get_settings
from app.db import get_db
from app.schemas.stats import FilamentStatsOut, StatsOut
from app.services import stats as stats_service

router = APIRouter(tags=["stats"])


@router.get("/stats", response_model=StatsOut)
async def get_stats(
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> StatsOut:
    return await stats_service.get_stats(db, settings)


@router.get("/stats/filament", response_model=FilamentStatsOut)
async def get_filament_stats(
    days: int = Query(90, ge=1, le=730),
    bucket: Literal["day", "week", "month"] = "week",
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> FilamentStatsOut:
    """P5 Task 8: per-material and per-spool filament usage over time, from
    ``spool_usage``. Any logged-in user may read it; per-run data is scoped
    to printers the caller can see, same as the archive routes (manual
    usage, which isn't tied to a printer, is always included)."""
    return await stats_service.filament_stats(
        db, settings, days=days, bucket=bucket, visible=visible_printer_ids(ctx)
    )
