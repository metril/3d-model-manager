"""T3 printer ops: AMS labels + SD-card browser/print. SD access is plain
FTPS straight from the API (like the connection test) -- stateless, no
printerd round trip; only the MQTT ``sd_print`` start goes over the command bus."""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import anyio
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_printer_access, require_session
from app.api.printer_control import run_command
from app.api.printers import _get_enabled_or_404, _get_or_404
from app.config import Settings, get_settings
from app.db import get_db
from app.models import AmsLabel, AuditLog, Printer, PrintJob
from app.models.enums import PrintJobOrigin, PrintJobState
from app.printers import commands as cmds
from app.printers.base import PrinterAdapter
from app.printers.capabilities import capabilities_for
from app.printers.connection import connection_from_printer
from app.printers.registry import build_adapter
from app.schemas.printer_ops import (
    AmsLabelIn,
    AmsLabelOut,
    SdEntryOut,
    SdPrintIn,
    SdStorageOut,
)
from app.services.printer_state import read_state_async

router = APIRouter(prefix="/printers", tags=["printer-ops"])
_control = Depends(require_printer_access("control"))


def ams_label_key(printer_serial: str, ams_id: int, unit_serial: str | None) -> str:
    """Label identity: the AMS unit's own serial, else printer-serial scoped."""
    return unit_serial or f"{printer_serial}:ams{ams_id}"


async def _state(settings: Settings, printer_id: int) -> dict | None:
    client = aioredis.Redis.from_url(settings.redis_url)
    try:
        return await read_state_async(client, printer_id)
    finally:
        await client.aclose()


async def label_map(db: AsyncSession, printer: Printer, state: dict | None) -> dict[int, str]:
    """``{ams_id: label}`` for the units in ``state``."""
    units = [u for u in (state or {}).get("ams_units") or [] if isinstance(u, dict)]
    keys = {
        ams_label_key(printer.serial, u["id"], u.get("serial")): u["id"]
        for u in units
        if u.get("id") is not None
    }
    if not keys:
        return {}
    rows = await db.scalars(select(AmsLabel).where(AmsLabel.ams_serial.in_(list(keys))))
    return {keys[r.ams_serial]: r.label for r in rows}


# -- AMS labels ---------------------------------------------------------------


@router.put("/{printer_id}/ams/{ams_id}/label", response_model=AmsLabelOut, dependencies=[_control])
async def put_ams_label(
    printer_id: int,
    ams_id: int,
    payload: AmsLabelIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> AmsLabelOut:
    printer = await _get_or_404(db, printer_id)
    label = payload.label.strip()
    if not label:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "label must not be blank")
    key = await _label_key(db, settings, printer, ams_id)
    row = await db.scalar(select(AmsLabel).where(AmsLabel.ams_serial == key))
    if row is None:
        db.add(AmsLabel(ams_serial=key, ams_id=ams_id, label=label, created_by_user_id=ctx.user.id))
    else:
        row.label, row.ams_id = label, ams_id
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            printer_id=printer_id,
            action="ams_label",
            payload={"ams_id": ams_id, "label": label},
        )
    )
    await db.commit()
    return AmsLabelOut(ams_id=ams_id, ams_serial=key, label=label)


@router.delete(
    "/{printer_id}/ams/{ams_id}/label",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[_control],
)
async def delete_ams_label(
    printer_id: int,
    ams_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> None:
    printer = await _get_or_404(db, printer_id)
    key = await _label_key(db, settings, printer, ams_id)
    row = await db.scalar(select(AmsLabel).where(AmsLabel.ams_serial == key))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no label")
    await db.delete(row)
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            printer_id=printer_id,
            action="ams_label_delete",
            payload={"ams_id": ams_id},
        )
    )
    await db.commit()


async def _label_key(db: AsyncSession, settings: Settings, printer: Printer, ams_id: int) -> str:
    state = await _state(settings, printer.id)
    unit = next(
        (
            u
            for u in (state or {}).get("ams_units") or []
            if isinstance(u, dict) and u.get("id") == ams_id
        ),
        None,
    )
    return ams_label_key(printer.serial, ams_id, (unit or {}).get("serial"))


# -- SD browser ---------------------------------------------------------------


def _err(http: int, code: str, detail: str = "") -> JSONResponse:
    return JSONResponse(status_code=http, content={"code": code, "detail": detail or code})


def _norm(path: str) -> str:
    try:
        return cmds.normalize_sd_path(path)
    except cmds.CommandError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, e.message) from e


async def _sd_adapter(
    db: AsyncSession, settings: Settings, printer_id: int
) -> tuple[Printer, PrinterAdapter]:
    printer = await _get_enabled_or_404(db, printer_id)
    if not capabilities_for(printer.model, printer.serial).sd_browser:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "SD browser not supported")
    conn = connection_from_printer(settings, printer)
    return printer, build_adapter(printer.kind, conn)


async def _ftp(adapter: PrinterAdapter, fn, *args):
    """Run a blocking adapter call in a thread, mapping failures to HTTP."""
    try:
        return await anyio.to_thread.run_sync(fn, *args)
    except NotImplementedError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unsupported") from e
    except FileNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found") from e
    except OSError as e:  # type only: text could echo the access code
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, type(e).__name__) from e


@router.get("/{printer_id}/files", response_model=list[SdEntryOut], dependencies=[_control])
async def list_sd(
    printer_id: int,
    path: str = Query("/"),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[SdEntryOut]:
    norm = _norm(path)
    _, adapter = await _sd_adapter(db, settings, printer_id)
    files = await _ftp(adapter, adapter.list_files, norm)
    base = "" if norm == "/" else norm
    entries = [
        SdEntryOut(
            name=f.name,
            path=f"{base}/{f.name}",
            is_dir=f.is_dir,
            size=f.size,
            mtime=f.mtime.isoformat() if f.mtime else None,
        )
        for f in files
    ]
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return entries


@router.get("/{printer_id}/files/storage", response_model=SdStorageOut, dependencies=[_control])
async def sd_storage(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SdStorageOut:
    _, adapter = await _sd_adapter(db, settings, printer_id)
    info = None
    with contextlib.suppress(HTTPException, OSError):
        info = await _ftp(adapter, adapter.storage_info)
    if info:
        return SdStorageOut(**info)
    sd = (await _state(settings, printer_id) or {}).get("sdcard")
    if isinstance(sd, bool):
        return SdStorageOut(source="sdcard", present=sd)
    return SdStorageOut()


def _stream_and_cleanup(path: Path) -> Iterator[bytes]:
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(256 * 1024):
                yield chunk
    finally:
        path.unlink(missing_ok=True)


@router.get("/{printer_id}/files/download", dependencies=[_control])
async def download_sd(
    printer_id: int,
    path: str = Query(...),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    norm = _norm(path)
    if norm == "/":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "path must name a file")
    _, adapter = await _sd_adapter(db, settings, printer_id)
    fd, tmp_name = tempfile.mkstemp(prefix="sd-")  # mode 0600
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        await _ftp(adapter, adapter.download_file, norm, tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    name = norm.rsplit("/", 1)[-1]
    return StreamingResponse(
        _stream_and_cleanup(tmp),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Content-Length": str(tmp.stat().st_size),
        },
    )


@router.delete(
    "/{printer_id}/files", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_control]
)
async def delete_sd(
    printer_id: int,
    path: str = Query(...),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> None:
    norm = _norm(path)
    if norm == "/":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "path must name a file")
    printer, adapter = await _sd_adapter(db, settings, printer_id)
    await _ftp(adapter, adapter.delete_file, norm)
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            printer_id=printer.id,
            action="sd_delete",
            payload={"path": norm},
        )
    )
    await db.commit()


@router.post("/{printer_id}/files/print", dependencies=[_control])
async def print_sd(
    printer_id: int,
    payload: SdPrintIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> JSONResponse:
    """Start a print of ``.3mf`` / ``.gcode.3mf`` already on the SD card and
    record an EXTERNAL ``print_jobs`` row (no library file behind it)."""
    printer = await _get_enabled_or_404(db, printer_id)
    args = payload.model_dump()
    try:
        clean = cmds.validate("sd_print", args)
    except cmds.CommandError as e:
        return _err(400, e.code, e.message)
    resp = await run_command(db, settings, ctx, printer, "sd_print", args)
    if resp.status_code not in (200, 202):
        return resp
    name = clean["path"].rsplit("/", 1)[-1]
    job = PrintJob(
        printer_id=printer_id,
        file_id=None,
        origin=PrintJobOrigin.EXTERNAL,
        created_by_user_id=ctx.user.id,
        state=PrintJobState.STARTING,
        plate=clean["plate"] or 1,
        subtask_name=name,
    )
    db.add(job)
    await db.commit()
    return JSONResponse(status_code=resp.status_code, content={"ok": True, "job_id": job.id})
