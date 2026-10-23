"""Q8b printer sensor history: downsampled temperature/fan series and AMS
humidity/temperature history, from ``printer_sensor_samples``."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_printer_access
from app.db import get_db
from app.models.sensors import PrinterSensorSample as S

router = APIRouter(tags=["sensors"])

MAX_POINTS = 500


class SensorPointOut(BaseModel):
    ts: datetime
    nozzle: float | None
    nozzle_target: float | None
    bed: float | None
    bed_target: float | None
    chamber: float | None
    fan_part: float | None


class SensorSeriesOut(BaseModel):
    step_s: int
    points: list[SensorPointOut]


class AmsPointOut(BaseModel):
    ts: datetime
    humidity: int | None
    temp: float | None


class AmsHistoryOut(BaseModel):
    unit: int
    step_s: int
    points: list[AmsPointOut]


def _window(
    frm: datetime | None, to: datetime | None, step: int | None
) -> tuple[datetime, datetime, int]:
    to = to or datetime.now(UTC)
    frm = frm or to - timedelta(hours=24)
    if frm.tzinfo is None:
        frm = frm.replace(tzinfo=UTC)
    if to.tzinfo is None:
        to = to.replace(tzinfo=UTC)
    if frm >= to:
        raise HTTPException(422, "'from' must be before 'to'")
    # Never more than MAX_POINTS buckets, whatever step the caller asked for.
    floor = math.ceil((to - frm).total_seconds() / MAX_POINTS)
    return frm, to, max(step or 1, floor, 1)


def _bucketed(printer_id: int, frm: datetime, to: datetime, step: int, *cols):
    bucket = func.floor(func.extract("epoch", S.ts) / step)
    return (
        select(func.max(S.ts).label("ts"), *cols)
        .where(S.printer_id == printer_id, S.ts >= frm, S.ts <= to)
        .group_by(bucket)
        .order_by(func.max(S.ts))
    )


@router.get("/printers/{printer_id}/sensors", response_model=SensorSeriesOut)
async def get_sensors(
    printer_id: int,
    _ctx: Annotated[AuthContext, Depends(require_printer_access("print"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    frm: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    step: Annotated[int | None, Query(ge=1)] = None,
) -> SensorSeriesOut:
    frm, to, step = _window(frm, to, step)
    stmt = _bucketed(
        printer_id,
        frm,
        to,
        step,
        func.avg(S.nozzle),
        func.avg(S.nozzle_target),
        func.avg(S.bed),
        func.avg(S.bed_target),
        func.avg(S.chamber),
        func.avg(S.fan_part),
    )
    rows = (await db.execute(stmt)).all()
    return SensorSeriesOut(
        step_s=step,
        points=[
            SensorPointOut(
                ts=r[0],
                nozzle=_r(r[1]),
                nozzle_target=_r(r[2]),
                bed=_r(r[3]),
                bed_target=_r(r[4]),
                chamber=_r(r[5]),
                fan_part=_r(r[6]),
            )
            for r in rows
        ],
    )


def _r(v: object) -> float | None:
    return None if v is None else round(float(v), 1)


@router.get("/printers/{printer_id}/ams-history", response_model=AmsHistoryOut)
async def get_ams_history(
    printer_id: int,
    _ctx: Annotated[AuthContext, Depends(require_printer_access("print"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    unit: Annotated[int, Query(ge=0)] = 0,
    frm: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    step: Annotated[int | None, Query(ge=1)] = None,
) -> AmsHistoryOut:
    frm, to, step = _window(frm, to, step)
    # Last AMS snapshot in each bucket (humidity is a slow signal).
    stmt = _bucketed(
        printer_id, frm, to, step, func.array_agg(aggregate_order_by(S.ams, S.ts.desc()))
    )
    points: list[AmsPointOut] = []
    for ts, snaps in (await db.execute(stmt)).all():
        latest = next((u for u in (snaps[0] or []) if u.get("id") == unit), None)
        if latest is not None:
            points.append(
                AmsPointOut(ts=ts, humidity=latest.get("humidity"), temp=latest.get("temp"))
            )
    return AmsHistoryOut(unit=unit, step_s=step, points=points)
