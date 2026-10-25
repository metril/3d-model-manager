"""V4 plate-empty detection: reference calibration, ``check-plate`` and a
small camera diagnose. Frames come from the printerd camera worker via the
Redis frame cache (same path as ``/camera/snapshot``)."""

from __future__ import annotations

import asyncio
import time
import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_printer_access
from app.api.printers import _get_enabled_or_404
from app.config import Settings, get_settings
from app.db import get_db
from app.models import AuditLog, PlateReference
from app.printers.camera_select import camera_kind
from app.services import camera_state as cs
from app.services import plate_detection as pd

router = APIRouter(prefix="/printers", tags=["plate-check"])
_control = Depends(require_printer_access("control"))
FRAME_WAIT_S = 10.0


class CheckPlateIn(BaseModel):
    plate_type: str | None = Field(default=None, max_length=64)
    threshold: float | None = Field(default=None, ge=pd.MIN_THRESHOLD, le=pd.MAX_THRESHOLD)


class CheckPlateOut(BaseModel):
    empty: bool
    score: float
    threshold: float
    needs_calibration: bool
    message: str
    references: int
    preview: str | None = None


class ReferenceIn(BaseModel):
    plate_type: str = Field(default="", max_length=64)
    label: str = Field(default="", max_length=100)


class ReferenceOut(BaseModel):
    id: int
    plate_type: str
    label: str
    created_at: str


class DiagnoseOut(BaseModel):
    camera_kind: str
    worker_status: str | None
    frame_bytes: int | None
    viewers: int
    ok: bool
    hint: str


async def grab_frame(
    settings: Settings, printer_id: int, timeout: float = FRAME_WAIT_S
) -> bytes | None:
    """Register a viewer so printerd's on-demand worker starts, then poll."""
    r = aioredis.Redis.from_url(settings.redis_url)
    vid = f"plate:{uuid.uuid4()}"
    try:
        await cs.aadd_viewer(r, printer_id, vid)
        deadline = time.monotonic() + timeout
        while True:
            frame = await cs.aget_frame(r, printer_id)
            if frame is not None:
                return frame
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.5)
    finally:
        await cs.aremove_viewer(r, printer_id, vid)
        await r.aclose()


async def _refs(db: AsyncSession, printer_id: int, plate_type: str) -> list[PlateReference]:
    rows = await db.scalars(
        select(PlateReference)
        .where(PlateReference.printer_id == printer_id, PlateReference.plate_type == plate_type)
        .order_by(PlateReference.id)
    )
    return list(rows)


def _out(r: PlateReference) -> ReferenceOut:
    return ReferenceOut(
        id=r.id, plate_type=r.plate_type, label=r.label, created_at=r.created_at.isoformat()
    )


@router.post("/{printer_id}/check-plate", response_model=CheckPlateOut)
async def check_plate(
    printer_id: int,
    body: CheckPlateIn | None = None,
    ctx: AuthContext = _control,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CheckPlateOut:
    printer = await _get_enabled_or_404(db, printer_id)
    cfg = pd.config_from_options(printer.options)
    body = body or CheckPlateIn()
    plate_type = body.plate_type if body.plate_type is not None else cfg.plate_type
    threshold = body.threshold if body.threshold is not None else cfg.threshold
    refs = await _refs(db, printer_id, plate_type)
    if not refs:
        return CheckPlateOut(
            empty=True,
            score=0.0,
            threshold=threshold,
            needs_calibration=True,
            message="No calibration - capture empty-plate references first",
            references=0,
        )
    frame = await grab_frame(settings, printer_id)
    if frame is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "camera is warming up")
    try:
        res = await asyncio.to_thread(
            pd.analyze, frame, [r.jpeg for r in refs], threshold, with_preview=True
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "could not analyze frame"
        ) from exc
    return CheckPlateOut(
        empty=res.empty,
        score=res.score,
        threshold=res.threshold,
        needs_calibration=res.needs_calibration,
        message=res.message,
        references=len(refs),
        preview=res.preview_data_url(),
    )


@router.get("/{printer_id}/plate-references", response_model=list[ReferenceOut])
async def list_references(
    printer_id: int, ctx: AuthContext = _control, db: AsyncSession = Depends(get_db)
) -> list[ReferenceOut]:
    rows = await db.scalars(
        select(PlateReference)
        .where(PlateReference.printer_id == printer_id)
        .order_by(PlateReference.plate_type, PlateReference.id)
    )
    return [_out(r) for r in rows]


@router.post(
    "/{printer_id}/plate-references",
    response_model=ReferenceOut,
    status_code=status.HTTP_201_CREATED,
)
async def capture_reference(
    printer_id: int,
    body: ReferenceIn,
    ctx: AuthContext = _control,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReferenceOut:
    """Capture the current camera frame as an empty-plate reference."""
    await _get_enabled_or_404(db, printer_id)
    frame = await grab_frame(settings, printer_id)
    if frame is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "camera is warming up")
    try:
        jpeg = await asyncio.to_thread(pd.normalize_jpeg, frame)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid camera frame") from exc
    existing = await _refs(db, printer_id, body.plate_type)
    for old in existing[: max(0, len(existing) - pd.MAX_REFERENCES + 1)]:
        await db.delete(old)
    row = PlateReference(
        printer_id=printer_id, plate_type=body.plate_type, label=body.label, jpeg=jpeg
    )
    db.add(row)
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            printer_id=printer_id,
            action="plate_reference_capture",
            payload={"plate_type": body.plate_type},
        )
    )
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.get("/{printer_id}/plate-references/{ref_id}/image")
async def reference_image(
    printer_id: int, ref_id: int, ctx: AuthContext = _control, db: AsyncSession = Depends(get_db)
) -> Response:
    row = await db.get(PlateReference, ref_id)
    if row is None or row.printer_id != printer_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reference not found")
    thumb = await asyncio.to_thread(pd.thumbnail, row.jpeg)
    return Response(content=thumb, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.delete("/{printer_id}/plate-references/{ref_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reference(
    printer_id: int, ref_id: int, ctx: AuthContext = _control, db: AsyncSession = Depends(get_db)
) -> None:
    res = await db.execute(
        delete(PlateReference).where(
            PlateReference.id == ref_id, PlateReference.printer_id == printer_id
        )
    )
    if res.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reference not found")
    await db.commit()


@router.post("/{printer_id}/camera/diagnose", response_model=DiagnoseOut)
async def diagnose_camera(
    printer_id: int,
    ctx: AuthContext = _control,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DiagnoseOut:
    """Lightweight self-diagnosis: camera source, worker status, whether a
    frame arrives within the wait window, and a plain-language hint."""
    printer = await _get_enabled_or_404(db, printer_id)
    kind = camera_kind(printer.model, printer.serial, printer.options)
    r = aioredis.Redis.from_url(settings.redis_url)
    try:
        worker = await cs.aget_status(r, printer_id)
        viewers = int(await r.zcard(cs.viewers_key(printer_id)))
    finally:
        await r.aclose()
    frame = None if kind == "none" else await grab_frame(settings, printer_id, 8.0)
    if kind == "none":
        hint = "This printer has no supported camera. Configure an external camera URL."
    elif worker == "unsupported":
        hint = "The camera worker reports this source as unsupported."
    elif frame is None:
        hint = (
            "No frame arrived. Check the printer is online, LAN-mode liveview is enabled "
            "(or the external camera URL is reachable) and printerd is running."
        )
    else:
        hint = "Camera is delivering frames."
    return DiagnoseOut(
        camera_kind=kind,
        worker_status=worker,
        frame_bytes=len(frame) if frame else None,
        viewers=viewers,
        ok=frame is not None,
        hint=hint,
    )
