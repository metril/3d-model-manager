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
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import anyio
import redis.asyncio as aioredis
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    SESSION_COOKIE_NAME,
    AuthContext,
    require_perm,
    require_printer_access,
    require_printer_enabled,
    require_session,
    visible_printer_ids,
)
from app.config import Settings, get_settings
from app.crypto import encrypt_secret
from app.db import get_db, get_sessionmaker
from app.models import Printer, PrintJob, User
from app.models.inventory import Spool
from app.printers import discovery
from app.printers.base import command_channel
from app.printers.connection import connection_from_printer
from app.printers.registry import build_adapter
from app.schemas.inventory import SlotAssignIn
from app.schemas.printers import (
    AmsTrayOut,
    CameraUrlsOut,
    DetectSerialIn,
    DetectSerialOut,
    PrinterCreate,
    PrinterOut,
    PrintersOverviewOut,
    PrinterStatusOut,
    PrinterUpdate,
    PrintJobOut,
    PrintRequest,
    ProbeOut,
    SpoolBriefOut,
    seed_build_volume_mm,
)
from app.services import camera_state as cs
from app.services import signed_urls, spool_inventory
from app.services.app_config import get_app_config
from app.services.permissions import load_grants
from app.services.print_plan import create_job
from app.services.printer_overview import build_overview
from app.services.printer_state import read_state_async
from app.tasks import base

# AMS slot coordinates: unit 0-3 (a real AMS unit) or the 255/254
# "external spool" sentinel pair (app.services.spool_inventory).
_VALID_UNITS = frozenset({0, 1, 2, 3, spool_inventory.EXTERNAL_UNIT})
_VALID_SLOTS = frozenset({0, 1, 2, 3})

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


@router.get("/overview", response_model=PrintersOverviewOut)
async def printers_overview(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> PrintersOverviewOut:
    """One-call dashboard payload (P3 Task 1): every visible printer's
    live status + active job + recent history, plus the queue's head and
    cross-printer recent runs. Declared before ``/{printer_id}`` so
    ``overview`` isn't swallowed as a path param.
    """
    client = aioredis.Redis.from_url(settings.redis_url)
    try:
        return await build_overview(db, client, settings, ctx)
    finally:
        await client.aclose()


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

    P5 Task 7: the body now lives in ``app.services.print_plan.create_job``,
    shared with the new ``POST /print/send`` route -- this handler is kept as
    a thin legacy alias so existing callers (and the frontend, until it
    migrates to the plan/send flow) are unaffected.
    """
    job = await create_job(db, settings, ctx, printer_id, payload)
    return PrintJobOut.from_model(job)


@router.put(
    "/{printer_id}/slots/{unit}/{slot}",
    response_model=list[AmsTrayOut],
    dependencies=[Depends(require_printer_access("control"))],
)
async def set_slot(
    printer_id: int,
    unit: int,
    slot: int,
    payload: SlotAssignIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[AmsTrayOut]:
    """Point an AMS slot at a registered spool, or clear it (P5 Task 6).
    ``unit``/``slot`` are the PER-UNIT coordinates the printer itself
    reports (not the global tray id) -- ``unit`` 255 + ``slot`` 254 is the
    "external spool" sentinel pair (``app.services.spool_inventory``).
    """
    valid = unit in _VALID_UNITS and (
        (unit == spool_inventory.EXTERNAL_UNIT and slot == spool_inventory.EXTERNAL_SLOT)
        or (unit != spool_inventory.EXTERNAL_UNIT and slot in _VALID_SLOTS)
    )
    if not valid:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid unit/slot")
    await _get_or_404(db, printer_id)
    if payload.spool_id is not None and await db.get(Spool, payload.spool_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "spool not found")

    # Delegates to the shared `spool_inventory.assign_slot` (sync -- the
    # same one printerd's RFID auto-assign uses) via its own short-lived
    # sync session, rather than re-implementing the move/clear logic here
    # against the async session. A concurrent duplicate assignment racing
    # past the read-then-write above surfaces as a DB-level IntegrityError
    # (the `spool_slots` unique constraints) -- caught as a 409, not a 500.
    def _assign() -> None:
        with base.sync_session() as s:
            try:
                spool_inventory.assign_slot(s, printer_id, unit, slot, payload.spool_id)
            except IntegrityError:
                s.rollback()
                raise

    try:
        await anyio.to_thread.run_sync(_assign)
    except IntegrityError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "slot already assigned") from e

    client = aioredis.Redis.from_url(settings.redis_url)
    try:
        state = await read_state_async(client, printer_id)
    finally:
        await client.aclose()
    trays = [AmsTrayOut(**t) for t in ((state or {}).get("trays") or [])]
    await _fill_tray_spools(db, printer_id, trays)
    return trays


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


async def _fill_tray_spools(db: AsyncSession, printer_id: int, trays: list[AmsTrayOut]) -> None:
    """Enrich each tray's ``spool`` from the printer's current slot
    assignments (P5 Task 6) -- the printer's own report only knows the
    RFID ``tray_uuid``, never which registered ``Spool`` that maps to."""
    if not trays:
        return
    smap = await spool_inventory.slot_map(db, [printer_id])
    for t in trays:
        spool = smap.get((printer_id, t.slot))
        if spool is not None:
            t.spool = SpoolBriefOut.from_model(spool)


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
    out = PrinterStatusOut(
        online=True,
        trays=state.get("trays") or [],
        **{k: state.get(k) for k in _STATE_FIELDS},
    )
    await _fill_tray_spools(db, printer_id, out.trays)
    return out


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


# -- Camera (P4 "Live camera and timelapse" plan, Task 4) ------------------
#
# ``camera_public_router`` is mounted directly on ``api_router`` (see
# ``app.api``'s module docstring), NOT under ``protected_router`` -- the
# stream/snapshot routes accept a session cookie OR a scoped camera token,
# so they can't sit under a blanket ``require_session`` dependency that
# would run before the handler ever sees the query string (same reasoning
# as ``files.public_router``'s signed download route). A request with
# neither still 401s (``_authorize_camera`` below), so the auth sweep is
# unaffected without needing an entry in its ``PUBLIC_ROUTES``.
camera_public_router = APIRouter(prefix="/printers", tags=["printers"])

# How often ``mjpeg_parts`` re-registers its viewer (refreshes the Redis
# ZSET TTL) while streaming, and how long it waits with no new frame before
# giving up and ending the stream -- module-level so tests can monkeypatch
# ``STREAM_IDLE_S`` down for a fast "ends when idle" test.
HEARTBEAT_S = cs.HEARTBEAT_S
STREAM_IDLE_S = 20.0


@router.post(
    "/{printer_id}/camera/token",
    response_model=CameraUrlsOut,
    dependencies=[Depends(require_printer_access("control"))],
)
async def mint_camera_token(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> CameraUrlsOut:
    """Mint a scoped, short-lived token authorizing THIS user to open the
    public stream/snapshot routes for ``printer_id`` with no session
    cookie -- for a plain ``<img>``/``<video>`` tag, which can't carry one
    cross-origin the way a fetch() with credentials can.
    """
    await _get_or_404(db, printer_id)
    scope = f"camera:{printer_id}:{ctx.user.id}"
    token = signed_urls.sign_scoped(settings, scope)
    expires_at = datetime.now(UTC) + timedelta(seconds=signed_urls.DEFAULT_TTL_S)
    return CameraUrlsOut(
        stream_url=f"/api/printers/{printer_id}/camera/stream?token={token}",
        snapshot_url=f"/api/printers/{printer_id}/camera/snapshot?token={token}",
        expires_at=expires_at,
    )


async def _authorize_camera(
    printer_id: int,
    token: str | None,
    tdmm_session: str | None,
    settings: Settings,
) -> AuthContext:
    """Session cookie OR a scoped camera token authorizes the public
    stream/snapshot routes. Uses its own short-lived async DB session --
    closed before this returns, well before a stream route's response ever
    starts yielding -- rather than FastAPI's per-request ``get_db``
    dependency, since that connection could otherwise be held open for as
    long as the MJPEG stream runs.
    """
    async with get_sessionmaker()() as db:
        if token is not None:
            scope = signed_urls.verify_scoped(settings, token, f"camera:{printer_id}:")
            if scope is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
            try:
                uid = int(scope.rsplit(":", 1)[-1])
            except ValueError as exc:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc
            user = await db.get(User, uid)
            if user is None or not user.is_active:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
            groups, perms, acl = await load_grants(db, user)
            ctx = AuthContext(user=user, session=None, perms=perms, printer_acl=acl, groups=groups)
        else:
            ctx = await require_session(tdmm_session=tdmm_session, db=db)

        config = await get_app_config(db, settings)
        if not config.printer_enabled:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "printer integration is disabled"
            )

    ctx.require_printer(printer_id, "control")
    return ctx


def _mjpeg_part(jpeg: bytes) -> bytes:
    return (
        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpeg)
        + jpeg
        + b"\r\n"
    )


async def mjpeg_parts(redis, printer_id: int, viewer_id: str) -> AsyncIterator[bytes]:
    """Yield ``multipart/x-mixed-replace`` parts for ``printer_id``'s live
    camera feed. Registers ``viewer_id`` (so printerd's camera worker starts
    or stays up), subscribes the frame pub/sub channel, yields whatever
    frame is already current (if any), then yields each newly-published
    frame as it arrives -- heartbeating the viewer registration every
    ``HEARTBEAT_S`` and ending the stream after ``STREAM_IDLE_S`` with no
    frame at all. Always unregisters the viewer and tears down the
    subscription on the way out, including when the caller ``aclose()``s
    this generator early (e.g. the client disconnected).
    """
    await cs.aadd_viewer(redis, printer_id, viewer_id)
    pubsub = redis.pubsub()
    await pubsub.subscribe(cs.frame_channel(printer_id))
    try:
        frame = await cs.aget_frame(redis, printer_id)
        now = time.monotonic()
        last_frame_at = now
        last_heartbeat_at = now
        if frame is not None:
            yield _mjpeg_part(frame)

        while True:
            message = await pubsub.get_message(timeout=1.0)
            now = time.monotonic()
            if message is not None and message["type"] == "message":
                last_frame_at = now
                yield _mjpeg_part(message["data"])

            if now - last_heartbeat_at >= HEARTBEAT_S:
                await cs.aadd_viewer(redis, printer_id, viewer_id)
                last_heartbeat_at = now

            if now - last_frame_at >= STREAM_IDLE_S:
                return
    finally:
        # Shielded: when the caller `aclose()`s this generator early (a
        # disconnecting client cancels the task driving it), the ambient
        # cancellation would otherwise also cancel this cleanup partway
        # through -- leaking the viewer registration and the pubsub
        # subscription instead of releasing them.
        with anyio.CancelScope(shield=True):
            await cs.aremove_viewer(redis, printer_id, viewer_id)
            await pubsub.unsubscribe(cs.frame_channel(printer_id))
            await pubsub.aclose()


@camera_public_router.get("/{printer_id}/camera/stream")
async def camera_stream(
    printer_id: int,
    token: str | None = Query(default=None),
    tdmm_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    await _authorize_camera(printer_id, token, tdmm_session, settings)
    redis = aioredis.Redis.from_url(settings.redis_url)
    try:
        if await cs.aget_status(redis, printer_id) == "unsupported":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "camera not supported")
    except Exception:
        await redis.aclose()
        raise
    viewer_id = f"stream:{uuid.uuid4()}"

    async def _gen() -> AsyncIterator[bytes]:
        try:
            async for part in mjpeg_parts(redis, printer_id, viewer_id):
                yield part
        finally:
            await redis.aclose()

    return StreamingResponse(
        _gen(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@camera_public_router.get("/{printer_id}/camera/snapshot")
async def camera_snapshot(
    printer_id: int,
    token: str | None = Query(default=None),
    tdmm_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    settings: Settings = Depends(get_settings),
) -> Response:
    await _authorize_camera(printer_id, token, tdmm_session, settings)
    redis = aioredis.Redis.from_url(settings.redis_url)
    try:
        current_status = await cs.aget_status(redis, printer_id)
        if current_status == "unsupported":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "camera not supported")

        # Register a viewer even on a cold camera (no removal -- the next
        # heartbeat/stream viewer or its own TTL cleans it up) so printerd's
        # on-demand worker starts warming up for the *next* request.
        await cs.aadd_viewer(redis, printer_id, f"snap:{uuid.uuid4()}")

        frame = await cs.aget_frame(redis, printer_id)
        if frame is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "camera is warming up",
                headers={"Retry-After": "2"},
            )
        return Response(
            content=frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )
    finally:
        await redis.aclose()
