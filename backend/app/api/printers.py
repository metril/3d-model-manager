"""Printers CRUD + Developer-Mode test probe (SPEC "API surface"; M4).
Encrypt-on-write (Fernet, app.crypto) + mask-on-read (M3's redaction UX):
PrinterOut never carries the code/ciphertext, only ``access_code_set``. The
test probe is the ONLY place the API builds an adapter (decrypts, in a
worker thread) -- and it returns only ok/detail/gcode_state.

Import-safety: only ``app.printers.registry``/``app.printers.connection``/
``app.printers.discovery`` (all lib-free -- ``discovery`` is stdlib +
``cryptography``, Round 8 T1) are imported here -- ``bambulabs_api``/
``paho`` are only ever touched lazily, inside the real adapter's own build
function, when a probe actually runs with the flag on (see
``tests/test_flag_off_imports.py``).
"""

from __future__ import annotations

import json

import anyio
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete as sa_delete
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
from app.crypto import encrypt_secret
from app.db import get_db
from app.models import Blob, File, Printer, PrintJob
from app.models.enums import BlobFormat, PrintJobState
from app.printers import discovery
from app.printers.base import command_channel
from app.printers.connection import connection_from_printer
from app.printers.registry import build_adapter
from app.schemas.printers import (
    DetectSerialIn,
    DetectSerialOut,
    PrinterCreate,
    PrinterOut,
    PrinterStatusOut,
    PrinterUpdate,
    PrintJobOut,
    PrintRequest,
    ProbeOut,
    seed_build_volume_mm,
)
from app.services.printer_state import preflight_ok, read_state_async
from app.tasks.printing import send_to_printer

router = APIRouter(
    prefix="/printers", tags=["printers"], dependencies=[Depends(require_printer_enabled)]
)

# Redaction sentinel GET emits for a set secret (mirrors
# app.api.settings._REDACTED_SENTINEL) -- an incoming request carrying this
# literal value is read back verbatim from a form the client seeded off a
# masked PrinterOut, never a real access code.
_REDACTED_SENTINEL = "***"


async def _get_or_404(db: AsyncSession, printer_id: int) -> Printer:
    printer = await db.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found")
    return printer


def _require_visible(ctx: AuthContext, printer_id: int) -> None:
    """404 (not 403) for a printer the user has no ACL row for, so ids of
    hidden printers are not enumerable."""
    visible = visible_printer_ids(ctx)
    if visible is not None and printer_id not in visible:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found")


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=PrinterOut,
    dependencies=[Depends(require_perm("settings:write"))],
)
async def create_printer(
    payload: PrinterCreate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PrinterOut:
    code = (payload.access_code or "").strip()
    if code in ("", _REDACTED_SENTINEL):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "access_code is required")
    build_volume_mm = (
        payload.build_volume_mm.model_dump()
        if payload.build_volume_mm is not None
        else seed_build_volume_mm(payload.model)
    )
    printer = Printer(
        name=payload.name,
        kind=payload.kind,
        host=payload.host,
        serial=payload.serial,
        access_code_enc=encrypt_secret(settings, code),
        model=payload.model,
        enabled=payload.enabled,
        options=payload.options,
        build_volume_mm=build_volume_mm,
    )
    db.add(printer)
    await db.commit()
    await db.refresh(printer)
    return PrinterOut.from_model(printer)


@router.post(
    "/detect-serial",
    response_model=DetectSerialOut,
    dependencies=[Depends(require_perm("settings:write"))],
)
async def detect_serial(payload: DetectSerialIn) -> DetectSerialOut:
    """Reads the printer's serial straight off its TLS certificate (Round 8
    T1, ``app.printers.discovery.read_cert_cn``) -- no DB row and no access
    code involved (this endpoint doesn't even take one), so it can back the
    printer form's "Detect" button before a printer is even saved. Runs the
    blocking TLS handshake in a worker thread, same pattern as the test
    probe below.
    """
    try:
        cn = await anyio.to_thread.run_sync(discovery.read_cert_cn, payload.host, payload.port)
    except Exception:  # noqa: BLE001 -- any read/handshake/parse failure is "couldn't detect"
        return DetectSerialOut(
            serial=None,
            detail=(
                f"Couldn't read a serial from {payload.host}:{payload.port} — "
                "is LAN Mode + Developer Mode on?"
            ),
        )
    if not cn:
        return DetectSerialOut(
            serial=None, detail="Reached the printer but its certificate had no serial."
        )
    return DetectSerialOut(serial=cn, detail="Detected serial from the printer's certificate.")


@router.get("", response_model=list[PrinterOut])
async def list_printers(
    db: AsyncSession = Depends(get_db), ctx: AuthContext = Depends(require_session)
) -> list[PrinterOut]:
    stmt = select(Printer).order_by(Printer.id)
    visible = visible_printer_ids(ctx)
    if visible is not None:
        stmt = stmt.where(Printer.id.in_(visible))
    rows = (await db.execute(stmt)).scalars()
    return [PrinterOut.from_model(p) for p in rows]


@router.get("/{printer_id}", response_model=PrinterOut)
async def get_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> PrinterOut:
    _require_visible(ctx, printer_id)
    return PrinterOut.from_model(await _get_or_404(db, printer_id))


@router.patch(
    "/{printer_id}",
    response_model=PrinterOut,
    dependencies=[Depends(require_perm("settings:write"))],
)
async def update_printer(
    printer_id: int,
    payload: PrinterUpdate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PrinterOut:
    printer = await _get_or_404(db, printer_id)
    data = payload.model_dump(exclude_unset=True)
    if "access_code" in data:
        code = (data.pop("access_code") or "").strip()
        if code not in ("", _REDACTED_SENTINEL):
            printer.access_code_enc = encrypt_secret(settings, code)
        elif code == _REDACTED_SENTINEL and not printer.access_code_enc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                'Cannot set access code to the placeholder "***"; enter the real code.',
            )
        # blank, or sentinel-with-a-stored-code -> keep the existing ciphertext
    for key, value in data.items():
        setattr(printer, key, value)
    # R13c: a `model` change that leaves `build_volume_mm` NULL (and the
    # patch itself didn't set one) re-runs the create-time seed -- otherwise
    # an unset build volume would stay stuck at the OLD model's seed-or-null
    # forever, since PATCH never re-seeds on its own.
    if "model" in data and "build_volume_mm" not in data and printer.build_volume_mm is None:
        seeded = seed_build_volume_mm(printer.model)
        if seeded is not None:
            printer.build_volume_mm = seeded
    await db.commit()
    await db.refresh(printer)
    return PrinterOut.from_model(printer)


@router.delete(
    "/{printer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("settings:write"))],
)
async def delete_printer(printer_id: int, db: AsyncSession = Depends(get_db)) -> None:
    printer = await _get_or_404(db, printer_id)
    # print_jobs FK printers.id -- remove this printer's history first.
    await db.execute(sa_delete(PrintJob).where(PrintJob.printer_id == printer_id))
    await db.delete(printer)
    await db.commit()


@router.post(
    "/{printer_id}/test",
    response_model=ProbeOut,
    dependencies=[Depends(require_perm("settings:write"))],
)
async def test_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ProbeOut:
    printer = await _get_or_404(db, printer_id)
    conn = connection_from_printer(settings, printer)  # decrypts here only
    adapter = build_adapter(printer.kind, conn)
    result = await anyio.to_thread.run_sync(adapter.test_connection)
    return ProbeOut(ok=result.ok, detail=result.detail, gcode_state=result.gcode_state)


@router.post(
    "/{printer_id}/print",
    status_code=status.HTTP_201_CREATED,
    response_model=PrintJobOut,
    dependencies=[Depends(require_printer_access("print"))],
)
async def start_print(
    printer_id: int,
    payload: PrintRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> PrintJobOut:
    """Fast pre-checks only (SPEC "Printer integration"; Task 6): a cheap
    format check (no archive read) and a Redis preflight read. Both are
    re-checked AUTHORITATIVELY inside ``send_to_printer`` itself -- this
    endpoint only ever creates the ``print_jobs`` row and enqueues the task;
    every heavy step (fetching the file, opening the archive, the FTPS
    upload, the MQTT start) happens in the worker, never on the request
    path.
    """
    printer = await _get_or_404(db, printer_id)
    if not printer.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "printer is disabled")
    file = await db.get(File, payload.file_id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    blob = await db.get(Blob, file.blob_hash)
    if blob is None or blob.format != BlobFormat.GCODE_3MF:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "only sliced .gcode.3mf files can be sent to a printer",
        )
    client = aioredis.Redis.from_url(settings.redis_url)
    try:
        state = await read_state_async(client, printer_id)
    finally:
        await client.aclose()
    if not preflight_ok(state):
        gs = state.get("gcode_state") if state else "unknown"
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"printer not ready (state={gs}); must be IDLE/FINISH/FAILED"
        )
    job = PrintJob(
        printer_id=printer_id,
        file_id=file.id,
        state=PrintJobState.QUEUED,
        subtask_name=payload.subtask_name,
        plate=payload.plate,
        ams_mapping=list(payload.ams_mapping),
        created_by_user_id=ctx.user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    send_to_printer.apply_async(args=[job.id, payload.model_dump()])
    await db.refresh(job)  # eager mode already ran the task through its own sync session
    return PrintJobOut.from_model(job)


_STATE_FIELDS = (
    "gcode_state",
    "mc_percent",
    "layer_num",
    "total_layer_num",
    "mc_remaining_time",
    "print_error",
    "nozzle_temper",
    "bed_temper",
    "subtask_name",
    "wifi_signal",
)


@router.get("/{printer_id}/status", response_model=PrinterStatusOut)
async def printer_status(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> PrinterStatusOut:
    """Read-only poll of printerd's last-reported state (SPEC "Printer
    integration"; Task 7). Deliberately NOT gated on ``printer.enabled`` --
    last-known/"unknown" status for a disabled printer is harmless and still
    useful (unlike the command endpoints below, this never talks to
    printerd). An absent or corrupt Redis key degrades to ``online: False``,
    never a 500 (mirrors the send flow's preflight fail-closed behavior).
    """
    _require_visible(ctx, printer_id)
    await _get_or_404(db, printer_id)
    client = aioredis.Redis.from_url(settings.redis_url)
    try:
        state = await read_state_async(client, printer_id)
    finally:
        await client.aclose()
    if state is None:
        return PrinterStatusOut(online=False)
    # `trays` (M8 G3) is a list, not a scalar -- pass it explicitly and coerce a
    # missing/None value (older state written before this field existed) to [].
    return PrinterStatusOut(
        online=True,
        trays=state.get("trays") or [],
        **{k: state.get(k) for k in _STATE_FIELDS},
    )


async def _publish_command(settings: Settings, printer_id: int, command: str) -> None:
    client = aioredis.Redis.from_url(settings.redis_url)
    try:
        await client.publish(command_channel(printer_id), json.dumps({"command": command}))
    finally:
        await client.aclose()


async def _get_enabled_or_404(db: AsyncSession, printer_id: int) -> Printer:
    """Same access-control principle as ``start_print`` (Task 6): a disabled
    printer isn't supervised by printerd, so a command published for it
    would never be heard -- reject up front instead of silently publishing
    into the void.
    """
    printer = await _get_or_404(db, printer_id)
    if not printer.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "printer is disabled")
    return printer


@router.post(
    "/{printer_id}/pause",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_printer_access("control"))],
)
async def pause_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    await _get_enabled_or_404(db, printer_id)
    await _publish_command(settings, printer_id, "pause")
    return {"status": "sent"}


@router.post(
    "/{printer_id}/resume",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_printer_access("control"))],
)
async def resume_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    await _get_enabled_or_404(db, printer_id)
    await _publish_command(settings, printer_id, "resume")
    return {"status": "sent"}


@router.post(
    "/{printer_id}/stop",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_printer_access("control"))],
)
async def stop_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    await _get_enabled_or_404(db, printer_id)
    await _publish_command(settings, printer_id, "stop")
    return {"status": "sent"}
