"""T1 AMS drying: ``/settings/drying``, ``/drying/presets`` and scheduled
drying runs. Immediate start/stop live in ``printer_control`` (command bus)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    require_perm,
    require_printer_access,
    require_session,
)
from app.api.printers import _get_enabled_or_404, _require_visible
from app.config import Settings, get_settings
from app.db import get_db
from app.models import AuditLog, ScheduledDrying
from app.printers.capabilities import capabilities_for
from app.services import drying
from app.services.drying_settings import (
    DryingConfig,
    DryingPreset,
    get_drying_config,
    set_drying_config,
)

router = APIRouter(tags=["drying"])
_control = Depends(require_printer_access("control"))
_STATUS_Q = Query(default=None, alias="status")

Status = Literal["pending", "waiting", "running", "done", "failed", "cancelled"]


class PresetsOut(BaseModel):
    presets: dict[str, DryingPreset]


@router.get("/settings/drying", response_model=DryingConfig)
async def get_drying_settings(db: AsyncSession = Depends(get_db)) -> DryingConfig:
    return await get_drying_config(db)


@router.put(
    "/settings/drying",
    response_model=DryingConfig,
    dependencies=[Depends(require_perm("settings:write"))],
)
async def put_drying_settings(
    payload: DryingConfig, db: AsyncSession = Depends(get_db)
) -> DryingConfig:
    return await set_drying_config(db, payload.model_dump())


@router.get("/drying/presets", response_model=PresetsOut)
async def drying_presets(
    db: AsyncSession = Depends(get_db), _ctx: AuthContext = Depends(require_session)
) -> PresetsOut:
    return PresetsOut(presets=(await get_drying_config(db)).presets)


class ScheduledDryingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    printer_id: int
    ams_id: int
    temp: int
    duration_hours: int
    filament: str
    rotate_tray: bool
    start_after: datetime | None
    status: str
    waiting_reason: str | None
    error_code: str | None
    started_at: datetime | None
    finished_at: datetime | None
    source: str
    created_by_user_id: int | None
    created_at: datetime


class ScheduledDryingIn(BaseModel):
    ams_id: int
    temp: int = Field(ge=drying.DRY_TEMP_RANGE[0], le=drying.DRY_TEMP_RANGE[1])
    duration_hours: int = Field(ge=drying.DRY_HOURS_RANGE[0], le=drying.DRY_HOURS_RANGE[1])
    filament: str = Field(default="", max_length=32)
    rotate_tray: bool = False
    start_after: datetime | None = None

    @field_validator("ams_id")
    @classmethod
    def _ams(cls, v: int) -> int:
        if not (0 <= v <= 3 or 128 <= v <= 135):
            raise ValueError("ams_id must be 0-3 or 128-135")
        return v

    @field_validator("start_after")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return v.replace(tzinfo=UTC) if v is not None and v.tzinfo is None else v


@router.get("/printers/{printer_id}/drying/schedules", response_model=list[ScheduledDryingOut])
async def list_schedules(
    printer_id: int,
    status_: Status | None = _STATUS_Q,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[ScheduledDrying]:
    _require_visible(ctx, printer_id)
    stmt = select(ScheduledDrying).where(ScheduledDrying.printer_id == printer_id)
    if status_ is not None:
        stmt = stmt.where(ScheduledDrying.status == status_)
    return list((await db.execute(stmt.order_by(ScheduledDrying.id.desc()))).scalars().all())


@router.post(
    "/printers/{printer_id}/drying/schedules",
    response_model=ScheduledDryingOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_control],
)
async def create_schedule(
    printer_id: int,
    payload: ScheduledDryingIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> ScheduledDrying:
    printer = await _get_enabled_or_404(db, printer_id)
    caps = capabilities_for(printer.model, printer.serial)
    ok, screen_only, _ = drying.check_drying_supported(caps.model_key, None, require_firmware=False)
    if screen_only:
        raise HTTPException(status.HTTP_409_CONFLICT, drying.SCREEN_ONLY_DETAIL)
    if not ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, drying.UNSUPPORTED_DETAIL)
    row = ScheduledDrying(
        printer_id=printer_id,
        source="manual",
        status="pending",
        created_by_user_id=ctx.user.id,
        **payload.model_dump(),
    )
    db.add(row)
    await db.flush()
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            printer_id=printer_id,
            action="drying_schedule_create",
            payload={"schedule_id": row.id, **payload.model_dump(mode="json")},
        )
    )
    await db.commit()
    await db.refresh(row)
    return row


@router.delete(
    "/drying/schedules/{sid}",
    response_model=ScheduledDryingOut,
    dependencies=[Depends(require_perm("printers:control"))],
)
async def cancel_schedule(
    sid: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> ScheduledDrying:
    row = await db.get(ScheduledDrying, sid)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "schedule not found")
    _require_visible(ctx, row.printer_id)
    ctx.require_printer(row.printer_id, "control")
    if row.status not in ("pending", "waiting"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "only pending/waiting schedules can be cancelled"
            + ("; stop a running cycle instead" if row.status == "running" else ""),
        )
    row.status = "cancelled"
    row.waiting_reason = None
    row.finished_at = datetime.now(UTC)
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            printer_id=row.printer_id,
            action="drying_schedule_cancel",
            payload={"schedule_id": row.id},
        )
    )
    await db.commit()
    await db.refresh(row)
    return row
