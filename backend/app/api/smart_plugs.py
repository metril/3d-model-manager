"""Smart plugs (Q7, standalone since v4). A plug has a name and an optional
printer link; several plugs may share a printer. Reads need a session (linked
plugs are filtered by printer visibility); on/off need ``printers:control``
(plus the per-printer ACL for a linked plug); creating/replacing/removing a
plug needs ``settings:write`` (it names arbitrary hosts/URLs and credentials,
so it is an admin-grade surface, not a control one)."""

from __future__ import annotations

import json
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    require_perm,
    require_printer_enabled,
    require_session,
    visible_printer_ids,
)
from app.config import Settings, get_settings
from app.crypto import decrypt_secret, encrypt_secret
from app.db import get_db
from app.models import Printer, SmartPlug
from app.schemas.ops import PlugIn, PlugOut
from app.services.smart_plugs import manager
from app.services.smart_plugs.drivers import PlugError, build_driver
from app.tasks import base

router = APIRouter(
    prefix="/plugs", tags=["smart-plugs"], dependencies=[Depends(require_printer_enabled)]
)
printer_router = APIRouter(
    prefix="/printers/{printer_id}/plugs",
    tags=["smart-plugs"],
    dependencies=[Depends(require_printer_enabled)],
)
summary_router = APIRouter(
    prefix="/printers/plugs", tags=["smart-plugs"], dependencies=[Depends(require_printer_enabled)]
)
_write = Depends(require_perm("settings:write"))
_KEEP = "***"
_SECRET_KEYS = {"password", "token", "headers", "body", "authorization"}


def _mask(cfg: dict) -> dict:
    return {k: (_KEEP if k in _SECRET_KEYS and v else v) for k, v in cfg.items()}


def _out(settings: Settings, p: SmartPlug) -> PlugOut:
    cfg = json.loads(decrypt_secret(settings, p.config_enc))
    return PlugOut(
        id=p.id,
        name=p.name,
        printer_id=p.printer_id,
        controls_printer_power=p.controls_printer_power,
        kind=p.kind,
        config=_mask(cfg),
        auto_off=p.auto_off,
        auto_off_after_min=p.auto_off_after_min,
        off_delay_mode=p.off_delay_mode,
        off_temp_threshold=p.off_temp_threshold,
        auto_on_before_print=p.auto_on_before_print,
        auto_off_after_drying=p.auto_off_after_drying,
        drying_off_delay_min=p.drying_off_delay_min,
        power_alert_enabled=p.power_alert_enabled,
        power_alert_high_w=p.power_alert_high_w,
        power_alert_low_w=p.power_alert_low_w,
        schedule_enabled=p.schedule_enabled,
        schedule_on_time=p.schedule_on_time,
        schedule_off_time=p.schedule_off_time,
        schedule_tz=p.schedule_tz,
        show_in_switchbar=p.show_in_switchbar,
        energy_kwh=p.energy_kwh or 0.0,
        last_state=p.last_state,
        last_power_w=p.last_power_w,
        last_polled_at=p.last_polled_at,
        last_error=p.last_error,
    )


def _visible(ctx: AuthContext, p: SmartPlug) -> bool:
    visible = visible_printer_ids(ctx)
    return visible is None or p.printer_id is None or p.printer_id in visible


async def _plug(db: AsyncSession, plug_id: int, ctx: AuthContext | None = None) -> SmartPlug:
    p = await db.get(SmartPlug, plug_id)
    if p is None or (ctx is not None and not _visible(ctx, p)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such smart plug")
    return p


def _scope(stmt, ctx: AuthContext):
    visible = visible_printer_ids(ctx)
    if visible is not None:
        stmt = stmt.where(or_(SmartPlug.printer_id.is_(None), SmartPlug.printer_id.in_(visible)))
    return stmt


@router.get("", response_model=list[PlugOut])
async def list_plugs(
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[PlugOut]:
    rows = (await db.scalars(_scope(select(SmartPlug), ctx).order_by(SmartPlug.id))).all()
    return [_out(settings, p) for p in rows]


@printer_router.get("", response_model=list[PlugOut])
async def printer_plugs(
    printer_id: int,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[PlugOut]:
    visible = visible_printer_ids(ctx)
    if visible is not None and printer_id not in visible:
        return []
    stmt = select(SmartPlug).where(SmartPlug.printer_id == printer_id).order_by(SmartPlug.id)
    return [_out(settings, p) for p in (await db.scalars(stmt)).all()]


@router.get("/{plug_id}", response_model=PlugOut)
async def get_plug(
    plug_id: int,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlugOut:
    return _out(settings, await _plug(db, plug_id, ctx))


def _validate(kind: str, cfg: dict, settings: Settings) -> None:
    with base.sync_session() as s:
        build_driver(kind, cfg, s, settings)


async def _apply(db: AsyncSession, row: SmartPlug, payload: PlugIn, settings: Settings) -> None:
    if payload.printer_id is not None and await db.get(Printer, payload.printer_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found")
    try:
        ZoneInfo(payload.schedule_tz)
    except Exception:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown timezone") from None
    if payload.schedule_enabled and not (payload.schedule_on_time or payload.schedule_off_time):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "schedule needs an on or off time"
        )
    cfg = dict(payload.config)
    if row.id is not None and row.kind == payload.kind:
        old = json.loads(decrypt_secret(settings, row.config_enc))
        for k, v in cfg.items():
            if v == _KEEP and k in old:
                cfg[k] = old[k]
    if any(v == _KEEP for v in cfg.values()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "no stored secret to keep")
    try:
        await run_in_threadpool(_validate, payload.kind, cfg, settings)
    except PlugError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    row.config_enc = encrypt_secret(settings, json.dumps(cfg))
    row.kind = payload.kind
    row.name = payload.name.strip()
    row.printer_id = payload.printer_id
    row.controls_printer_power = payload.controls_printer_power
    row.auto_off = (
        payload.auto_off if payload.auto_off is not None else payload.auto_off_after_min is not None
    )
    row.auto_off_after_min = payload.auto_off_after_min
    row.off_delay_mode = payload.off_delay_mode
    row.off_temp_threshold = payload.off_temp_threshold
    row.auto_on_before_print = payload.auto_on_before_print
    row.auto_off_after_drying = payload.auto_off_after_drying
    row.drying_off_delay_min = payload.drying_off_delay_min
    row.power_alert_enabled = payload.power_alert_enabled
    row.power_alert_high_w = payload.power_alert_high_w
    row.power_alert_low_w = payload.power_alert_low_w
    row.schedule_enabled = payload.schedule_enabled
    row.schedule_on_time = payload.schedule_on_time
    row.schedule_off_time = payload.schedule_off_time
    row.schedule_tz = payload.schedule_tz
    row.show_in_switchbar = payload.show_in_switchbar


@router.post("", response_model=PlugOut, status_code=status.HTTP_201_CREATED)
async def create_plug(
    payload: PlugIn,
    ctx: AuthContext = _write,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlugOut:
    row = SmartPlug(created_by_user_id=ctx.user.id, kind=payload.kind, config_enc="")
    await _apply(db, row, payload, settings)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _out(settings, row)


@router.put(
    "/{plug_id}", response_model=PlugOut, dependencies=[Depends(require_perm("settings:write"))]
)
async def put_plug(
    plug_id: int,
    payload: PlugIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlugOut:
    row = await _plug(db, plug_id)
    await _apply(db, row, payload, settings)
    await db.commit()
    await db.refresh(row)
    return _out(settings, row)


@router.delete(
    "/{plug_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("settings:write"))],
)
async def delete_plug(plug_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await db.delete(await _plug(db, plug_id))
    await db.commit()


def _command(plug_id: int, action: str, settings: Settings) -> None:
    with base.sync_session() as s:
        plug = s.get(SmartPlug, plug_id)
        if plug is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such smart plug")
        try:
            manager.command(s, settings, plug, action)
        except PlugError as exc:
            plug.last_error = str(exc)[:200]
            s.commit()
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None


async def _act(
    plug_id: int, action: str, ctx: AuthContext, db: AsyncSession, settings: Settings
) -> PlugOut:
    if not ctx.can("printers:control"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing permission: printers:control")
    p = await _plug(db, plug_id, ctx)
    if p.printer_id is not None:
        ctx.require_printer(p.printer_id, "control")
    elif not ctx.can("settings:write"):  # unlinked plug: no printer ACL to gate on
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing permission: settings:write")
    await run_in_threadpool(_command, plug_id, action, settings)
    db.expire_all()
    return _out(settings, await _plug(db, plug_id))


@router.post("/{plug_id}/on", response_model=PlugOut)
async def plug_on(
    plug_id: int,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlugOut:
    return await _act(plug_id, "on", ctx, db, settings)


@router.post("/{plug_id}/off", response_model=PlugOut)
async def plug_off(
    plug_id: int,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlugOut:
    return await _act(plug_id, "off", ctx, db, settings)


class PlugSummaryOut(BaseModel):
    plugs: dict[int, PlugOut]


@summary_router.get("/summary", response_model=PlugSummaryOut)
async def plugs_summary(
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlugSummaryOut:
    """Each visible printer's main plug (the one cutting its power, else the
    first) in one query -- dashboard cards share it."""
    stmt = _scope(select(SmartPlug).where(SmartPlug.printer_id.is_not(None)), ctx)
    rows = (await db.scalars(stmt.order_by(SmartPlug.id))).all()
    chosen: dict[int, SmartPlug] = {}
    for p in rows:
        cur = chosen.get(p.printer_id)
        if cur is None or (p.controls_printer_power and not cur.controls_printer_power):
            chosen[p.printer_id] = p
    return PlugSummaryOut(plugs={pid: _out(settings, p) for pid, p in chosen.items()})
