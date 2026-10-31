"""Assembles ``PrinterStatusOut`` from printerd's Redis state plus the DB
overlays (capabilities, AMS labels, spool assignments, the in-flight print
job and its plate thumbnail). Shared by the session and ext/v2 ``/status``
routes (and the SSE stream), so every consumer sees identical fields."""

from __future__ import annotations

import hashlib

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.library import File, Model, Revision
from app.models.printing import Printer, PrintJob
from app.printers.capabilities import capabilities_for
from app.schemas.printers import (
    AmsTrayOut,
    CapabilitiesOut,
    PrinterStatusOut,
    SpoolBriefOut,
    StatusPrintJobOut,
    status_from_state,
)
from app.services import derivatives, spool_inventory

ACTIVE_STATES = ("queued", "uploading", "starting", "printing", "paused")
_EXTERNAL_TRAY = 254


async def active_job_rows(
    db: AsyncSession, printer_ids: list[int]
) -> dict[int, tuple[PrintJob, Model | None, File | None]]:
    """Latest still-in-flight ``print_jobs`` row per printer, resolved onto
    its file and model via ``DISTINCT ON (printer_id)`` -- one query."""
    if not printer_ids:
        return {}
    stmt = (
        select(PrintJob, Model, File)
        .outerjoin(File, File.id == PrintJob.file_id)
        .outerjoin(Revision, Revision.id == File.revision_id)
        .outerjoin(Model, Model.id == Revision.model_id)
        .where(PrintJob.printer_id.in_(printer_ids))
        .where(PrintJob.state.in_(ACTIVE_STATES))
        .distinct(PrintJob.printer_id)
        .order_by(PrintJob.printer_id, PrintJob.id.desc())
    )
    return {
        job.printer_id: (job, model, file) for job, model, file in (await db.execute(stmt)).all()
    }


async def fill_tray_spools(db: AsyncSession, printer_id: int, trays: list[AmsTrayOut]) -> None:
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


def resolve_active_tray(out: PrinterStatusOut) -> AmsTrayOut | None:
    """``tray_now``: 0-253 a loaded slot (global ``ams_unit*4+slot`` id),
    254 the external spool, 255/None nothing."""
    now = out.tray_now
    if now is None or now == 255:
        return None
    if now == _EXTERNAL_TRAY:
        return out.ext_tray
    return next((t for t in out.trays if t.slot == now), None)


async def _job_overlay(
    settings: Settings, job: PrintJob, model: Model | None, file: File | None
) -> tuple[StatusPrintJobOut, str | None]:
    thumb_key: str | None = None
    if file is not None and file.rel_path.lower().endswith(".3mf"):
        plate = job.plate if job.plate and job.plate > 0 else 1
        path = derivatives.plate_thumb_path(settings, file.blob_hash, plate)
        if await anyio.to_thread.run_sync(path.exists):
            thumb_key = hashlib.sha1(f"{file.blob_hash}:{plate}".encode()).hexdigest()[:16]
    return (
        StatusPrintJobOut(
            print_job_id=job.id,
            file_id=job.file_id,
            model_id=model.id if model else None,
            model_name=model.name if model else None,
            revision_id=file.revision_id if file else None,
            plate=job.plate,
            started_at=job.started_at,
        ),
        thumb_key,
    )


_TEMP_FIELDS = (
    "nozzle_temper",
    "bed_temper",
    "nozzle_target",
    "bed_target",
    "chamber_temper",
    "chamber_target",
)


def _r1(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


def normalize_status(out: PrinterStatusOut) -> PrinterStatusOut:
    """Stable ordering + rounding so consumers (and the SSE delta diff) never
    see churn from reordered lists or sub-0.1 sensor jitter. In place."""
    for f in _TEMP_FIELDS:
        setattr(out, f, _r1(getattr(out, f)))
    if out.mc_remaining_time is not None:
        out.mc_remaining_time = round(out.mc_remaining_time)
    out.trays.sort(key=lambda t: (-1 if t.ams_unit is None else t.ams_unit, t.slot))
    out.hms.sort(key=lambda h: (h.attr, h.code))
    out.ams_units.sort(key=lambda u: u.id)
    out.extruders.sort(key=lambda e: e.id)
    for u in out.ams_units:
        u.temp = _r1(u.temp)
        u.humidity = _r1(u.humidity)
    for e in out.extruders:
        e.temp = _r1(e.temp)
        e.target = _r1(e.target)
    return out


async def build_status(
    db: AsyncSession, settings: Settings, printer: Printer, state: dict | None
) -> PrinterStatusOut:
    """``state`` is printerd's last-reported dict (``None`` / corrupt ->
    offline). Callable with a preloaded state so a streaming consumer can
    batch its Redis reads."""
    if state is None:
        out = PrinterStatusOut(online=False, awaiting_plate_clear=printer.awaiting_plate_clear)
    else:
        out = status_from_state(state)
        out.awaiting_plate_clear = printer.awaiting_plate_clear
        out.capabilities = CapabilitiesOut(
            **capabilities_for(
                printer.model, printer.serial, ((state.get("firmware") or {}).get("ota"))
            ).as_dict()
        )
        if out.ams_units:
            from app.api.printer_ops import label_map

            labels = await label_map(db, printer, state)
            for u in out.ams_units:
                u.label = labels.get(u.id)
        await fill_tray_spools(db, printer.id, out.trays)
        out.active_tray = resolve_active_tray(out)
    row = (await active_job_rows(db, [printer.id])).get(printer.id)
    if row is not None:
        out.print_job, out.print_thumbnail_key = await _job_overlay(settings, *row)
    return normalize_status(out)
