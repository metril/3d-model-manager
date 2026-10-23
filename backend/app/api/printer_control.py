"""Q2 printer controls: command bus v2 over Redis + objects / skip-objects
+ Developer-Mode warnings. Every control route validates args
(``app.printers.commands``), gates on capabilities and Developer Mode,
refuses motion while printing, publishes ``{command,args,request_id}`` and
waits <=3 s for printerd's ``cmd_result``: 200 {ok} | 409 | 422 | 202."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import anyio
import redis.asyncio as aioredis
from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    require_printer_access,
    require_printer_enabled,
    require_session,
    visible_printer_ids,
)
from app.api.files import _spool_to_temp_file
from app.api.printers import _get_enabled_or_404, _get_or_404, _require_visible
from app.config import Settings, get_settings
from app.db import get_db
from app.models import AuditLog, File, Printer, PrintJob
from app.models.enums import PrintJobState
from app.printers import commands as cmds
from app.printers.base import cmd_result_key, command_channel
from app.printers.capabilities import capabilities_for
from app.printers.gcode3mf import printable_objects
from app.services.printer_state import read_state_async
from app.services.storage_backends import resolve_backend_for_file

router = APIRouter(
    prefix="/printers",
    tags=["printer-control"],
    dependencies=[Depends(require_printer_enabled)],
)

RESULT_WAIT_S = 3.0
_POLL_S = 0.05
_BODY = Body(default_factory=dict)
_AUDITED = {"gcode", "jog", "extrude", "skip_objects"}
_TERMINAL = (PrintJobState.FINISHED, PrintJobState.FAILED, PrintJobState.CANCELED)


def _err(http: int, code: str, detail: str = "") -> JSONResponse:
    return JSONResponse(status_code=http, content={"code": code, "detail": detail or code})


def _gate(command: str, clean: dict, printer: Printer, state: dict) -> JSONResponse | None:
    """Capability, limit, Developer-Mode and motion guards (API side)."""
    caps = capabilities_for(printer.model, printer.serial)
    unsupported = (
        (command == "set_temperature" and clean["target"] == "chamber" and not caps.chamber_heater)
        or (command == "set_fan" and clean["fan"] == "aux" and not caps.aux_fan)
        or (command == "set_fan" and clean["fan"] == "chamber" and not caps.chamber_fan)
        or (command == "set_airduct" and not caps.airduct)
    )
    if unsupported:
        return _err(422, "unsupported", f"{command} is not supported on this model")
    if command == "set_temperature":
        cap = {"nozzle": caps.max_nozzle, "bed": caps.max_bed}.get(clean["target"])
        if cap is not None and clean["value"] > cap:
            return _err(422, "invalid", f"{clean['target']} target exceeds {cap} C")
    if state.get("dev_mode") is False and command not in cmds.ALWAYS_ALLOWED:
        return _err(409, "dev_mode_off", "Developer Mode is off on this printer")
    spec = cmds.DISPATCH[command]
    printing = state.get("gcode_state") in cmds.PRINTING_STATES
    if spec.refuse_printing and printing:
        return _err(409, "printing", f"{command} is refused while printing")
    if spec.needs_printing and not printing:
        return _err(409, "not_printing", f"{command} requires an active print")
    if spec.cold_check:
        nozzle = state.get("nozzle_temper")
        if not isinstance(nozzle, int | float) or nozzle < cmds.COLD_EXTRUDE_C:
            return _err(409, "cold_extrude", "nozzle must be at least 170 C")
    if command == "jog" and printer.build_volume_mm:
        limit = printer.build_volume_mm.get(clean["axis"].lower())
        if isinstance(limit, int | float) and abs(clean["mm"]) > limit:
            return _err(422, "invalid", "step exceeds the axis length")
    return None


async def run_command(
    db: AsyncSession,
    settings: Settings,
    ctx: AuthContext,
    printer: Printer,
    command: str,
    args: dict | None,
    *,
    audit_extra: dict | None = None,
) -> JSONResponse:
    try:
        clean = cmds.validate(command, args)
    except cmds.CommandError as e:
        return _err(cmds.HTTP_STATUS.get(e.code, 422), e.code, e.message)
    client = aioredis.Redis.from_url(settings.redis_url)
    try:
        state = await read_state_async(client, printer.id)
        if state is None:
            return _err(409, "offline", "printer is offline (no state from printerd)")
        if (denied := _gate(command, clean, printer, state)) is not None:
            return denied
        if command in _AUDITED:
            db.add(
                AuditLog(
                    user_id=ctx.user.id,
                    created_by_user_id=ctx.user.id,
                    printer_id=printer.id,
                    action=command,
                    payload={**clean, **(audit_extra or {})},
                )
            )
            await db.commit()
        request_id = uuid.uuid4().hex
        await client.publish(
            command_channel(printer.id),
            json.dumps({"command": command, "args": clean, "request_id": request_id}),
        )
        deadline = time.monotonic() + RESULT_WAIT_S
        key = cmd_result_key(printer.id, request_id)
        while time.monotonic() < deadline:
            raw = await client.get(key)
            if raw:
                result = json.loads(raw)
                if result.get("ok"):
                    return JSONResponse(status_code=200, content={"ok": True})
                code = result.get("code", "error")
                return _err(cmds.HTTP_STATUS.get(code, 409), code, result.get("message", ""))
            await asyncio.sleep(_POLL_S)
        return JSONResponse(
            status_code=202, content={"status": "pending", "request_id": request_id}
        )
    finally:
        await client.aclose()


def _make_endpoint(command: str, *, admin_only: bool = False):
    async def endpoint(
        printer_id: int,
        body: dict = _BODY,
        db: AsyncSession = Depends(get_db),
        settings: Settings = Depends(get_settings),
        ctx: AuthContext = Depends(require_session),
    ) -> JSONResponse:
        if admin_only and not ctx.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
        printer = await _get_enabled_or_404(db, printer_id)
        return await run_command(db, settings, ctx, printer, command, body)

    endpoint.__name__ = f"control_{command}"
    return endpoint


_ROUTES = (
    ("temperature", "set_temperature", False),
    ("fan", "set_fan", False),
    ("light", "set_light", False),
    ("speed", "set_speed", False),
    ("home", "home", False),
    ("jog", "jog", False),
    ("extrude", "extrude", False),
    ("gcode", "gcode", True),
    ("airduct", "set_airduct", False),
    ("calibrate", "calibrate", False),
    ("hms/clear", "clear_hms", False),
    ("hms/action", "hms_action", False),
)
for _path, _command, _admin in _ROUTES:
    router.add_api_route(
        f"/{{printer_id}}/control/{_path}",
        _make_endpoint(_command, admin_only=_admin),
        methods=["POST"],
        dependencies=[Depends(require_printer_access("control"))],
    )
    if _path.startswith("hms/"):  # Q5: `/printers/{id}/hms/...` alias of the control route
        router.add_api_route(
            f"/{{printer_id}}/{_path}",
            _make_endpoint(_command, admin_only=_admin),
            methods=["POST"],
            dependencies=[Depends(require_printer_access("control"))],
        )


@router.post(
    "/{printer_id}/control/filament",
    dependencies=[Depends(require_printer_access("control"))],
)
async def control_filament(
    printer_id: int,
    body: dict = _BODY,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> JSONResponse:
    """``{"action": "load", "tray": N}`` or ``{"action": "unload"}``."""
    printer = await _get_enabled_or_404(db, printer_id)
    action = body.get("action")
    if action not in ("load", "unload"):
        return _err(422, "invalid", "action must be load or unload")
    return await run_command(db, settings, ctx, printer, f"{action}_filament", body)


# -- objects / skip ----------------------------------------------------------


async def _active_objects(db: AsyncSession, settings: Settings, printer_id: int) -> dict:
    job = (
        (
            await db.execute(
                select(PrintJob)
                .where(
                    PrintJob.printer_id == printer_id,
                    PrintJob.state.notin_([s.value for s in _TERMINAL]),
                    PrintJob.file_id.is_not(None),
                )
                .order_by(PrintJob.id.desc())
            )
        )
        .scalars()
        .first()
    )
    if job is None:
        return {"job_id": None, "plate": None, "objects": []}
    file = await db.get(File, job.file_id)
    if file is None:
        return {"job_id": job.id, "plate": job.plate, "objects": []}
    backend = await resolve_backend_for_file(db, settings, file)
    try:
        tmp = await anyio.to_thread.run_sync(_spool_to_temp_file, backend, file.storage_path)
    except Exception:  # noqa: BLE001 -- missing/unreadable file => no object list
        return {"job_id": job.id, "plate": job.plate, "objects": []}
    try:
        objects = await anyio.to_thread.run_sync(printable_objects, tmp, job.plate)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {"job_id": job.id, "plate": job.plate, "objects": objects}


@router.get("/developer-mode-warnings")
async def developer_mode_warnings(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> list[dict]:
    """Enabled, visible printers whose Developer Mode is known to be off."""
    visible = visible_printer_ids(ctx)
    rows = (await db.execute(select(Printer).where(Printer.enabled.is_(True)))).scalars().all()
    client = aioredis.Redis.from_url(settings.redis_url)
    try:
        out: list[dict] = []
        for p in rows:
            if visible is not None and p.id not in visible:
                continue
            state = await read_state_async(client, p.id)
            if state and state.get("dev_mode") is False:
                out.append(
                    {"printer_id": p.id, "name": p.name, "reason": state.get("dev_mode_reason")}
                )
        return out
    finally:
        await client.aclose()


@router.get("/{printer_id}/objects")
async def printer_objects(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> dict:
    """Printable objects of the active print's archived 3MF (empty when the
    job has no stored file, e.g. one started from the printer's own UI)."""
    _require_visible(ctx, printer_id)
    await _get_or_404(db, printer_id)
    return await _active_objects(db, settings, printer_id)


@router.post(
    "/{printer_id}/skip-objects",
    dependencies=[Depends(require_printer_access("control"))],
)
async def skip_objects(
    printer_id: int,
    body: dict = _BODY,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> JSONResponse:
    printer = await _get_enabled_or_404(db, printer_id)
    known = {o["id"] for o in (await _active_objects(db, settings, printer_id))["objects"]}
    ids = body.get("object_ids")
    if not known:
        return _err(409, "objects_unknown", "the active print has no stored 3MF object list")
    if isinstance(ids, list) and not set(ids) <= known:
        return _err(422, "invalid", "object_ids contains an unknown object")
    return await run_command(db, settings, ctx, printer, "skip_objects", body)
