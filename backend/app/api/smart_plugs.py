"""Per-printer smart plug (Q7). Reads need printer visibility; on/off need
``printers:control``; creating/replacing/removing the plug config needs
``settings:write`` (it names arbitrary hosts/URLs and credentials, so it is
an admin-grade surface, not a control one)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    require_perm,
    require_printer_access,
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
    prefix="/printers/{printer_id}/plug",
    tags=["smart-plugs"],
    dependencies=[Depends(require_printer_enabled)],
)
_write = Depends(require_perm("settings:write"))
_KEEP = "***"
_SECRET_KEYS = {"password", "token", "headers", "body", "authorization"}


def _mask(cfg: dict) -> dict:
    return {k: (_KEEP if k in _SECRET_KEYS and v else v) for k, v in cfg.items()}


def _out(settings: Settings, p: SmartPlug) -> PlugOut:
    cfg = json.loads(decrypt_secret(settings, p.config_enc))
    return PlugOut(
        printer_id=p.printer_id,
        kind=p.kind,
        config=_mask(cfg),
        auto_off_after_min=p.auto_off_after_min,
        auto_on_before_print=p.auto_on_before_print,
        last_state=p.last_state,
        last_power_w=p.last_power_w,
        last_polled_at=p.last_polled_at,
        last_error=p.last_error,
    )


async def _plug(db: AsyncSession, printer_id: int) -> SmartPlug:
    p = await db.scalar(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no smart plug configured")
    return p


@router.get("", response_model=PlugOut)
async def get_plug(
    printer_id: int,
    ctx: AuthContext = Depends(require_session),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlugOut:
    visible = visible_printer_ids(ctx)
    if visible is not None and printer_id not in visible:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no smart plug configured")
    return _out(settings, await _plug(db, printer_id))


def _validate(kind: str, cfg: dict, settings: Settings) -> None:
    with base.sync_session() as s:
        build_driver(kind, cfg, s, settings)


@router.put("", response_model=PlugOut)
async def put_plug(
    printer_id: int,
    payload: PlugIn,
    ctx: AuthContext = _write,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlugOut:
    if await db.get(Printer, printer_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found")
    row = await db.scalar(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
    cfg = dict(payload.config)
    if row is not None and row.kind == payload.kind:
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
    enc = encrypt_secret(settings, json.dumps(cfg))
    if row is None:
        row = SmartPlug(printer_id=printer_id, created_by_user_id=ctx.user.id)
        db.add(row)
    row.kind, row.config_enc = payload.kind, enc
    row.auto_off_after_min = payload.auto_off_after_min
    row.auto_on_before_print = payload.auto_on_before_print
    await db.commit()
    await db.refresh(row)
    return _out(settings, row)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("settings:write"))],
)
async def delete_plug(printer_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await db.delete(await _plug(db, printer_id))
    await db.commit()


def _command(printer_id: int, action: str, settings: Settings) -> None:
    with base.sync_session() as s:
        plug = s.scalar(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
        if plug is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no smart plug configured")
        try:
            manager.command(s, settings, plug, action)
        except PlugError as exc:
            plug.last_error = str(exc)[:200]
            s.commit()
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None


async def _act(printer_id: int, action: str, db: AsyncSession, settings: Settings) -> PlugOut:
    await _plug(db, printer_id)
    await run_in_threadpool(_command, printer_id, action, settings)
    db.expire_all()
    return _out(settings, await _plug(db, printer_id))


@router.post(
    "/on", response_model=PlugOut, dependencies=[Depends(require_printer_access("control"))]
)
async def plug_on(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlugOut:
    return await _act(printer_id, "on", db, settings)


@router.post(
    "/off", response_model=PlugOut, dependencies=[Depends(require_printer_access("control"))]
)
async def plug_off(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlugOut:
    return await _act(printer_id, "off", db, settings)
