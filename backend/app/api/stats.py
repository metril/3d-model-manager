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
from app.schemas.stats import (
    FailureAnalysisOut,
    FilamentStatsOut,
    FilamentTrendPointOut,
    HeatmapOut,
    PrinterStatsOut,
    RecordsOut,
    StatsDashboardOut,
    StatsOut,
    SuccessRateOut,
    TimeAccuracyOut,
)
from app.services import stats as stats_service
from app.services import stats_widgets as w
from app.services.tz import parse_tz

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
    tz: str | None = None,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> FilamentStatsOut:
    """P5 Task 8: per-material and per-spool filament usage over time, from
    ``spool_usage``. Any logged-in user may read it; per-run data is scoped
    to printers the caller can see, same as the archive routes (manual
    usage, which isn't tied to a printer, is always included)."""
    return await stats_service.filament_stats(
        db,
        settings,
        days=days,
        bucket=bucket,
        visible=visible_printer_ids(ctx),
        tz=parse_tz(tz).key,
    )


# Q8b widgets: windowed by ``days``, scoped to printers the caller can see.
Days = Query(30, ge=1, le=365)


@router.get("/stats/dashboard", response_model=StatsDashboardOut)
async def stats_dashboard(
    days: int = Days,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> StatsDashboardOut:
    return await w.dashboard(db, days=days, visible=visible_printer_ids(ctx))


async def _rows(db: AsyncSession, ctx: AuthContext, days: int) -> list[w.Row]:
    return await w.load_rows(db, days=days, visible=visible_printer_ids(ctx))


@router.get("/stats/success-rate", response_model=SuccessRateOut)
async def stats_success_rate(
    days: int = Days,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> SuccessRateOut:
    return w.success_rate(await _rows(db, ctx, days))


@router.get("/stats/time-accuracy", response_model=TimeAccuracyOut)
async def stats_time_accuracy(
    days: int = Days,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> TimeAccuracyOut:
    return w.time_accuracy(await _rows(db, ctx, days))


@router.get("/stats/failures", response_model=FailureAnalysisOut)
async def stats_failures(
    days: int = Days,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> FailureAnalysisOut:
    return w.failure_analysis(await _rows(db, ctx, days))


@router.get("/stats/heatmap", response_model=HeatmapOut)
async def stats_heatmap(
    days: int = Days,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> HeatmapOut:
    return w.heatmap(await _rows(db, ctx, days))


@router.get("/stats/records", response_model=RecordsOut)
async def stats_records(
    days: int = Days,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> RecordsOut:
    return w.records(await _rows(db, ctx, days))


@router.get("/stats/printers", response_model=list[PrinterStatsOut])
async def stats_printers(
    days: int = Days,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[PrinterStatsOut]:
    return w.printer_stats(await _rows(db, ctx, days))


@router.get("/stats/filament-trends", response_model=list[FilamentTrendPointOut])
async def stats_filament_trends(
    days: int = Query(90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[FilamentTrendPointOut]:
    return await w.filament_trends(db, days=days, visible=visible_printer_ids(ctx))
