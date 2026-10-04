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

import contextlib
import json

import anyio
import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_printer_enabled
from app.config import Settings, get_settings
from app.crypto import encrypt_secret
from app.db import get_db
from app.models import Blob, File, Printer, PrintJob
from app.models.enums import BlobFormat, PrinterKind, PrintJobState
from app.printers import discovery
from app.printers.base import command_channel
from app.printers.connection import connection_from_printer
from app.printers.registry import build_adapter
from app.schemas.printers import (
    DetectSerialIn,
    DetectSerialOut,
    PrinterCameraOut,
    PrinterCreate,
    PrinterLightIn,
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


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PrinterOut)
async def create_printer(
    payload: PrinterCreate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PrinterOut:
    code = (payload.access_code or "").strip()
    if payload.kind == PrinterKind.BAMBU_LAN and code in ("", _REDACTED_SENTINEL):
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


@router.post("/detect-serial", response_model=DetectSerialOut)
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
async def list_printers(db: AsyncSession = Depends(get_db)) -> list[PrinterOut]:
    rows = (await db.execute(select(Printer).order_by(Printer.id))).scalars()
    return [PrinterOut.from_model(p) for p in rows]


@router.get("/{printer_id}", response_model=PrinterOut)
async def get_printer(printer_id: int, db: AsyncSession = Depends(get_db)) -> PrinterOut:
    return PrinterOut.from_model(await _get_or_404(db, printer_id))


@router.patch("/{printer_id}", response_model=PrinterOut)
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


@router.delete("/{printer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_printer(printer_id: int, db: AsyncSession = Depends(get_db)) -> None:
    printer = await _get_or_404(db, printer_id)
    # print_jobs FK printers.id -- remove this printer's history first.
    await db.execute(sa_delete(PrintJob).where(PrintJob.printer_id == printer_id))
    await db.delete(printer)
    await db.commit()


@router.post("/{printer_id}/test", response_model=ProbeOut)
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


@router.post("/{printer_id}/print", status_code=status.HTTP_201_CREATED, response_model=PrintJobOut)
async def start_print(
    printer_id: int,
    payload: PrintRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
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
    if blob is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    if printer.kind == PrinterKind.BAMBU_LAN and blob.format != BlobFormat.GCODE_3MF:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "only sliced .gcode.3mf files can be sent to a printer",
        )
    if blob.format not in (BlobFormat.GCODE_3MF, BlobFormat.GCODE):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "only sliced .gcode or .gcode.3mf files can be sent to a printer",
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
    "light_on",
)


@router.get("/{printer_id}/status", response_model=PrinterStatusOut)
async def printer_status(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PrinterStatusOut:
    """Read-only poll of printerd's last-reported state (SPEC "Printer
    integration"; Task 7). Deliberately NOT gated on ``printer.enabled`` --
    last-known/"unknown" status for a disabled printer is harmless and still
    useful (unlike the command endpoints below, this never talks to
    printerd). An absent or corrupt Redis key degrades to ``online: False``,
    never a 500 (mirrors the send flow's preflight fail-closed behavior).
    """
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


@router.post("/{printer_id}/pause", status_code=status.HTTP_202_ACCEPTED)
async def pause_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    await _get_enabled_or_404(db, printer_id)
    await _publish_command(settings, printer_id, "pause")
    return {"status": "sent"}


@router.post("/{printer_id}/resume", status_code=status.HTTP_202_ACCEPTED)
async def resume_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    await _get_enabled_or_404(db, printer_id)
    await _publish_command(settings, printer_id, "resume")
    return {"status": "sent"}


@router.post("/{printer_id}/stop", status_code=status.HTTP_202_ACCEPTED)
async def stop_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    await _get_enabled_or_404(db, printer_id)
    await _publish_command(settings, printer_id, "stop")
    return {"status": "sent"}


@router.post("/{printer_id}/light", status_code=status.HTTP_202_ACCEPTED)
async def toggle_printer_light(
    printer_id: int,
    payload: PrinterLightIn | None = None,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    await _get_enabled_or_404(db, printer_id)
    cmd = "toggle_light"
    if payload and payload.on is not None:
        cmd = "light_on" if payload.on else "light_off"
    await _publish_command(settings, printer_id, cmd)
    return {"status": "sent"}


_SNAPSHOT_MAX_BYTES = 5 * 1024 * 1024
_SNAPSHOT_TYPES = {"image/jpeg", "image/png"}


async def _camera_urls(printer: Printer, settings: Settings) -> dict:
    """Ask the printer adapter for its camera URLs, always releasing it."""
    adapter = build_adapter(printer.kind, connection_from_printer(settings, printer))
    try:
        return await anyio.to_thread.run_sync(adapter.get_camera_urls)
    finally:
        with contextlib.suppress(Exception):
            await anyio.to_thread.run_sync(adapter.close)


@router.get("/{printer_id}/camera", response_model=PrinterCameraOut)
async def get_printer_camera(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PrinterCameraOut:
    printer = await _get_or_404(db, printer_id)
    cam = await _camera_urls(printer, settings)
    stream_url = cam.get("stream_url")
    if not stream_url:
        return PrinterCameraOut(available=False)

    return PrinterCameraOut(
        available=True,
        name=cam.get("name") or "Camera",
        stream_url=f"/api/printers/{printer_id}/camera/stream",
        snapshot_url=f"/api/printers/{printer_id}/camera/snapshot",
        aspect_ratio=cam.get("aspect_ratio") or "4:3",
        direct_stream_url=stream_url,
    )


@router.get("/{printer_id}/camera/snapshot")
async def get_printer_camera_snapshot(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    printer = await _get_or_404(db, printer_id)
    cam = await _camera_urls(printer, settings)
    snapshot_url = cam.get("snapshot_url")
    if not snapshot_url:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera snapshot not available")

    unavailable = HTTPException(status.HTTP_502_BAD_GATEWAY, "Camera snapshot unavailable")
    try:
        async with (
            httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client,
            client.stream("GET", snapshot_url) as resp,
        ):
            media_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            # Some mjpg-streamer builds omit Content-Type on snapshots.
            media_type = media_type or "image/jpeg"
            if not resp.is_success or media_type not in _SNAPSHOT_TYPES:
                raise unavailable
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > _SNAPSHOT_MAX_BYTES:
                    raise unavailable
    except HTTPException:
        raise
    except Exception as exc:
        raise unavailable from exc
    return Response(
        content=bytes(buf),
        media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/{printer_id}/camera/stream")
async def get_printer_camera_stream(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    printer = await _get_enabled_or_404(db, printer_id)
    cam = await _camera_urls(printer, settings)
    stream_url = cam.get("stream_url")
    if not stream_url:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera stream not available")

    async def stream_generator():
        timeout = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            try:
                async with client.stream("GET", stream_url) as resp:
                    if not resp.is_success:
                        return
                    async for chunk in resp.aiter_bytes():
                        yield chunk
            except Exception:
                return

    return StreamingResponse(
        stream_generator(),
        media_type="multipart/x-mixed-replace;boundary=boundarydonotcross",
    )
