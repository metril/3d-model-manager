"""T2 K-profiles (pressure advance): printer-side profile table (command bus
v2 + Redis cache), profile notes, and per-spool stored profile links."""

from __future__ import annotations

import asyncio
import json
import re
import time
from decimal import Decimal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    require_perm,
    require_printer_access,
    require_printer_enabled,
    require_session,
)
from app.api.printer_control import _err, run_command
from app.api.printers import _get_enabled_or_404, _get_or_404, _require_visible
from app.config import Settings, get_settings
from app.db import get_db
from app.models import AuditLog, Printer, Spool
from app.models.kprofiles import KProfileNote, SpoolKProfile
from app.printers import commands as cmds
from app.printers.base import cmd_result_key
from app.printers.capabilities import capabilities_for
from app.schemas.kprofiles import (
    KProfileIn,
    KProfileNoteIn,
    KProfileSelectIn,
    SpoolKProfileIO,
)
from app.services import kprofiles

router = APIRouter(
    prefix="/printers",
    tags=["kprofiles"],
    dependencies=[Depends(require_printer_enabled)],
)
spool_router = APIRouter(prefix="/spools", tags=["kprofiles"])

LOCK_TTL_S = 5
RESULT_WAIT_S = 3.0
_REQUEST_ID = re.compile(r"[0-9a-f]{32}")
_CONTROL = Depends(require_printer_access("control"))


def _nozzle_or_422(value: str) -> str:
    try:
        return cmds._nozzle_diameter({"nozzle_diameter": value})
    except cmds.CommandError as e:
        raise HTTPException(422, e.message) from e


def _require_cap(printer: Printer) -> None:
    if not capabilities_for(printer.model, printer.serial).kprofiles:
        raise HTTPException(422, "K-profiles are not supported")


async def _shape(db: AsyncSession, printer_id: int, entry: dict, extruder: int | None) -> dict:
    notes = {
        n.setting_id: n.note
        for n in (
            await db.execute(select(KProfileNote).where(KProfileNote.printer_id == printer_id))
        ).scalars()
    }
    profiles = [
        {**p, "note": notes.get(p.get("setting_id") or "")}
        for p in entry["profiles"]
        if extruder is None or p.get("extruder_id") == extruder
    ]
    return {
        "profiles": profiles,
        "nozzle_diameter": entry["nozzle_diameter"],
        "fetched_at": entry["fetched_at"],
    }


@router.get("/{printer_id}/kprofiles", dependencies=[_CONTROL])
async def list_kprofiles(
    printer_id: int,
    nozzle: str = "0.4",
    extruder: int | None = Query(default=None, ge=0, le=1),
    refresh: bool = False,
    request_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> JSONResponse:
    printer = await _get_enabled_or_404(db, printer_id)
    _require_cap(printer)
    nozzle = _nozzle_or_422(nozzle)
    client = aioredis.Redis.from_url(settings.redis_url)
    lock = f"printer:{printer_id}:kprofiles_lock:{nozzle}"
    held = False
    try:
        if request_id is not None:
            if not _REQUEST_ID.fullmatch(request_id):
                raise HTTPException(422, "bad request_id")
            raw = await client.get(cmd_result_key(printer_id, request_id))
            if not raw:
                return JSONResponse(
                    status_code=202, content={"status": "pending", "request_id": request_id}
                )
            result = json.loads(raw)
            if not result.get("ok"):
                code = result.get("code", "error")
                return _err(cmds.HTTP_STATUS.get(code, 409), code, result.get("message", ""))
            entry = result.get("payload")
        else:
            entry = (
                None if refresh else await kprofiles.read_cache_async(client, printer_id, nozzle)
            )
            if entry is None:
                # One in-flight list request per printer + nozzle; followers
                # wait for the leader's table to land in the cache.
                held = bool(await client.set(lock, "1", nx=True, ex=LOCK_TTL_S))
                if not held:
                    deadline = time.monotonic() + RESULT_WAIT_S
                    while time.monotonic() < deadline:
                        await asyncio.sleep(0.1)
                        entry = await kprofiles.read_cache_async(client, printer_id, nozzle)
                        if entry is not None:
                            break
                    if entry is None:
                        return JSONResponse(status_code=202, content={"status": "pending"})
        if entry is None:
            resp = await run_command(
                db, settings, ctx, printer, "kprofile_list", {"nozzle_diameter": nozzle}
            )
            if resp.status_code != 200:
                if resp.status_code != 202:  # 202: printerd is still working; lock expires
                    await client.delete(lock)
                return resp
            await client.delete(lock)
            entry = json.loads(resp.body).get("payload")
    finally:
        await client.aclose()
    if not isinstance(entry, dict) or "profiles" not in entry:
        return _err(502, "error", "the printer returned no K-profile table")
    return JSONResponse(content=await _shape(db, printer_id, entry, extruder))


async def _cached(settings: Settings, printer_id: int, nozzle: str) -> dict | None:
    client = aioredis.Redis.from_url(settings.redis_url)
    try:
        return await kprofiles.read_cache_async(client, printer_id, nozzle)
    finally:
        await client.aclose()


@router.post("/{printer_id}/kprofiles", dependencies=[_CONTROL])
async def save_kprofile(
    printer_id: int,
    body: KProfileIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> JSONResponse:
    """Create (no ``setting_id``) or edit (``setting_id`` [+ ``cali_idx``]) a profile."""
    printer = await _get_enabled_or_404(db, printer_id)
    _require_cap(printer)
    args = body.model_dump(exclude_none=True)
    if body.setting_id and body.cali_idx is None:
        nozzle = _nozzle_or_422(body.nozzle_diameter)
        hit = kprofiles.find_in_cache(await _cached(settings, printer_id, nozzle), body.setting_id)
        if hit is None:
            return _err(404, "kprofile_not_found", "profile not in the cached list; refresh it")
        args["cali_idx"] = hit["cali_idx"]
    resp = await run_command(db, settings, ctx, printer, "kprofile_set", args)
    if resp.status_code == 200 and body.setting_id:  # keep linked spool profiles in sync
        await db.execute(
            update(SpoolKProfile)
            .where(
                SpoolKProfile.printer_id == printer_id, SpoolKProfile.setting_id == body.setting_id
            )
            .values(k_value=body.k_value, n_coef=body.n_coef, name=body.name)
        )
        await db.commit()
    return resp


@router.delete("/{printer_id}/kprofiles/{setting_id}", dependencies=[_CONTROL])
async def delete_kprofile(
    printer_id: int,
    setting_id: str,
    nozzle: str = "0.4",
    extruder: int = Query(default=0, ge=0, le=1),
    cali_idx: int | None = Query(default=None, ge=0, le=4095),
    filament_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> JSONResponse:
    printer = await _get_enabled_or_404(db, printer_id)
    _require_cap(printer)
    nozzle = _nozzle_or_422(nozzle)
    hit = kprofiles.find_in_cache(await _cached(settings, printer_id, nozzle), setting_id) or {}
    cali = cali_idx if cali_idx is not None else hit.get("cali_idx")
    filament = filament_id or hit.get("filament_id")
    if cali is None or not filament:
        return _err(404, "kprofile_not_found", "profile not in the cached list; refresh it")
    resp = await run_command(
        db,
        settings,
        ctx,
        printer,
        "kprofile_delete",
        {
            "setting_id": setting_id,
            "cali_idx": cali,
            "filament_id": filament,
            "nozzle_diameter": nozzle,
            "extruder_id": hit.get("extruder_id", extruder),
            "nozzle_id": hit.get("nozzle_id", ""),
        },
    )
    if resp.status_code == 200:
        await db.execute(
            delete(KProfileNote).where(
                KProfileNote.printer_id == printer_id, KProfileNote.setting_id == setting_id
            )
        )
        await db.execute(  # the stored links lose their (now dangling) printer slot
            update(SpoolKProfile)
            .where(SpoolKProfile.printer_id == printer_id, SpoolKProfile.setting_id == setting_id)
            .values(setting_id=None, cali_idx=None)
        )
        await db.commit()
    return resp


@router.post("/{printer_id}/kprofiles/select", dependencies=[_CONTROL])
async def select_kprofile(
    printer_id: int,
    body: KProfileSelectIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> JSONResponse:
    printer = await _get_enabled_or_404(db, printer_id)
    _require_cap(printer)
    return await run_command(db, settings, ctx, printer, "kprofile_select", body.model_dump())


@router.put("/{printer_id}/kprofiles/{setting_id}/note", dependencies=[_CONTROL])
async def put_note(
    printer_id: int,
    setting_id: str,
    body: KProfileNoteIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> dict:
    await _get_or_404(db, printer_id)
    row = (
        await db.execute(
            select(KProfileNote).where(
                KProfileNote.printer_id == printer_id, KProfileNote.setting_id == setting_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(
            KProfileNote(
                printer_id=printer_id,
                setting_id=setting_id,
                note=body.note,
                created_by_user_id=ctx.user.id,
            )
        )
    else:
        row.note = body.note
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            printer_id=printer_id,
            action="kprofile_note_set",
            payload={"setting_id": setting_id},
        )
    )
    await db.commit()
    return {"setting_id": setting_id, "note": body.note}


@router.delete(
    "/{printer_id}/kprofiles/{setting_id}/note",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[_CONTROL],
)
async def delete_note(
    printer_id: int,
    setting_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> Response:
    await _get_or_404(db, printer_id)
    await db.execute(
        delete(KProfileNote).where(
            KProfileNote.printer_id == printer_id, KProfileNote.setting_id == setting_id
        )
    )
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            printer_id=printer_id,
            action="kprofile_note_delete",
            payload={"setting_id": setting_id},
        )
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- spool side --------------------------------------------------------------


def _row_out(r: SpoolKProfile) -> SpoolKProfileIO:
    return SpoolKProfileIO(
        printer_id=r.printer_id,
        extruder=r.extruder,
        nozzle_diameter=float(r.nozzle_diameter),
        nozzle_type=r.nozzle_type,
        k_value=float(r.k_value),
        n_coef=float(r.n_coef) if r.n_coef is not None else None,
        name=r.name,
        cali_idx=r.cali_idx,
        setting_id=r.setting_id,
    )


async def _spool_or_404(db: AsyncSession, spool_id: int) -> Spool:
    spool = await db.get(Spool, spool_id)
    if spool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "spool not found")
    return spool


@spool_router.get("/{spool_id}/kprofiles", response_model=list[SpoolKProfileIO])
async def get_spool_kprofiles(
    spool_id: int, db: AsyncSession = Depends(get_db)
) -> list[SpoolKProfileIO]:
    await _spool_or_404(db, spool_id)
    rows = (
        await db.execute(
            select(SpoolKProfile)
            .where(SpoolKProfile.spool_id == spool_id)
            .order_by(
                SpoolKProfile.printer_id, SpoolKProfile.extruder, SpoolKProfile.nozzle_diameter
            )
        )
    ).scalars()
    return [_row_out(r) for r in rows]


@spool_router.put(
    "/{spool_id}/kprofiles",
    response_model=list[SpoolKProfileIO],
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def put_spool_kprofiles(
    spool_id: int,
    body: list[SpoolKProfileIO],
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[SpoolKProfileIO]:
    """Replace the spool's stored profile list."""
    await _spool_or_404(db, spool_id)
    seen: set[tuple] = set()
    for item in body:
        key = (item.printer_id, item.extruder, round(item.nozzle_diameter, 2))
        if key in seen:
            raise HTTPException(
                422,
                "duplicate printer / extruder / nozzle_diameter entry",
            )
        seen.add(key)
        _require_visible(ctx, item.printer_id)
        await _get_or_404(db, item.printer_id)
    await db.execute(delete(SpoolKProfile).where(SpoolKProfile.spool_id == spool_id))
    for item in body:
        db.add(
            SpoolKProfile(
                spool_id=spool_id,
                printer_id=item.printer_id,
                extruder=item.extruder,
                nozzle_diameter=Decimal(str(round(item.nozzle_diameter, 2))),
                nozzle_type=item.nozzle_type,
                k_value=Decimal(str(round(item.k_value, 6))),
                n_coef=None if item.n_coef is None else Decimal(str(round(item.n_coef, 6))),
                name=item.name,
                cali_idx=item.cali_idx,
                setting_id=item.setting_id,
                created_by_user_id=ctx.user.id,
            )
        )
    await db.commit()
    flags = aioredis.Redis.from_url(get_settings().redis_url)
    try:
        for pid in {i.printer_id for i in body}:
            await kprofiles.clear_printer_skips_async(flags, pid)
    except Exception:  # noqa: BLE001 -- best effort
        pass
    finally:
        await flags.aclose()
    return await get_spool_kprofiles(spool_id, db)


@spool_router.delete(
    "/{spool_id}/kprofiles/{printer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def delete_spool_kprofile(
    spool_id: int,
    printer_id: int,
    extruder: int = Query(default=0, ge=0, le=1),
    nozzle: str = "0.4",
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> Response:
    _require_visible(ctx, printer_id)
    await _spool_or_404(db, spool_id)
    await db.execute(
        delete(SpoolKProfile).where(
            SpoolKProfile.spool_id == spool_id,
            SpoolKProfile.printer_id == printer_id,
            SpoolKProfile.extruder == extruder,
            SpoolKProfile.nozzle_diameter == Decimal(_nozzle_or_422(nozzle)),
        )
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
