"""Print plan / send (P5 spool tray plan, Task 7): resolve a sliced file's
plate + filament needs against a target printer's live AMS trays and spool
inventory, matching Task 6's ``ams_match``/``spool_inventory`` services, and
the shared ``create_job`` the legacy ``/printers/{id}/print`` route and the
new ``/print/send`` route both call.
"""

from __future__ import annotations

import anyio
import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, visible_printer_ids
from app.config import Settings
from app.models import Blob, File, Model, Printer, PrintJob, Revision
from app.models.enums import BlobFormat, PrintJobState
from app.models.processing import BlobMeta
from app.schemas.library import PlateOut
from app.schemas.print_flow import PlanFilament, PlanIn, PlanOut, PlanPrinter
from app.schemas.printers import PrintRequest, SpoolBriefOut
from app.services import derivatives
from app.services import spool_inventory as sinv
from app.services.ams_match import FilamentNeed, Match, TrayInfo, match, to_ams_mapping
from app.services.app_config import get_app_config
from app.services.costing import CostRates, filament_cost, price_run
from app.services.printer_state import preflight_ok, read_state_async


def _spool_label(s) -> str:
    bits = [s.color_name or s.color_hex, s.brand, s.material_type]
    return " ".join(b for b in bits if b) or f"spool {s.id}"


def _can_print(ctx: AuthContext, printer_id: int) -> bool:
    if ctx.is_admin:
        return True
    acl = ctx.printer_acl.get(printer_id)
    return "printers:print" in ctx.perms and acl is not None and acl.can_print


async def _thumbnail_existence(settings: Settings, blob_hash: str, plates: list[dict]) -> dict:
    paths = {
        p["index"]: derivatives.plate_thumb_path(settings, blob_hash, p["index"]) for p in plates
    }

    def _check() -> dict[int, bool]:
        return {idx: path.exists() for idx, path in paths.items()}

    return await anyio.to_thread.run_sync(_check) if paths else {}


async def build_plan(
    db: AsyncSession, settings: Settings, redis: aioredis.Redis, ctx: AuthContext, body: PlanIn
) -> PlanOut:
    file = await db.get(File, body.file_id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    blob = await db.get(Blob, file.blob_hash)
    if blob is None or blob.format != BlobFormat.GCODE_3MF:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "only sliced .gcode.3mf files can be planned"
        )
    meta = await db.get(BlobMeta, file.blob_hash)
    raw_plates: list[dict] = ((meta.raw or {}).get("plates") or []) if meta is not None else []
    if not raw_plates:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "file has no sliced plates")

    plate_no = body.plate if body.plate is not None else raw_plates[0]["index"]
    plate = next((p for p in raw_plates if p["index"] == plate_no), None)
    if plate is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"plate {plate_no} not found")

    rev = await db.get(Revision, file.revision_id)
    model = await db.get(Model, rev.model_id) if rev is not None else None

    if body.printer_id is not None:
        ctx.require_printer(body.printer_id, "print")

    visible = visible_printer_ids(ctx)
    stmt = select(Printer).order_by(Printer.name)
    if visible is not None:
        stmt = stmt.where(Printer.id.in_(visible))
    all_printers = list((await db.execute(stmt)).scalars().all())
    printable = [p for p in all_printers if _can_print(ctx, p.id)]

    printer_model = (meta.printer_model if meta is not None else None) or None
    printers: list[PlanPrinter] = []
    states: dict[int, dict | None] = {}
    for p in printable:
        state = await read_state_async(redis, p.id)
        states[p.id] = state
        idle = preflight_ok(state)
        compatible = True
        reason = None
        if printer_model and p.model and printer_model.lower() not in p.model.lower():
            compatible = False
            reason = f"sliced for {printer_model}"
        printers.append(
            PlanPrinter(
                id=p.id, name=p.name, model=p.model, idle=idle, compatible=compatible, reason=reason
            )
        )

    printer_id = body.printer_id
    if printer_id is None:
        pick = next((pp for pp in printers if pp.idle and pp.compatible), None)
        printer_id = pick.id if pick is not None else None

    needs = [
        FilamentNeed(
            filament_id=f["filament_id"],
            type=f.get("type"),
            color=f.get("color"),
            grams=f.get("used_g"),
        )
        for f in plate["filaments"]
        if f.get("filament_id") is not None
    ]

    tray_infos: list[TrayInfo] = []
    smap: dict[tuple[int, int], object] = {}
    if printer_id is not None:
        state = states.get(printer_id)
        if state is None:
            state = await read_state_async(redis, printer_id)
        for t in (state or {}).get("trays") or []:
            tray_infos.append(
                TrayInfo(tray=t["slot"], type=t.get("tray_type"), color=t.get("color"))
            )
        smap = await sinv.slot_map(db, [printer_id])

    matches: list[Match] = match(needs, tray_infos, overrides=body.overrides)
    ams_mapping = to_ams_mapping(matches)
    use_ams = any(m.tray not in (None, sinv.EXTERNAL_SLOT) for m in matches)

    cfg = await get_app_config(db, settings)
    rates = CostRates.from_config(cfg)

    filaments: list[PlanFilament] = []
    usages: list[tuple[object | None, float]] = []
    warnings: list[str] = []
    unmatched = 0
    by_filament_id = {
        f["filament_id"]: f for f in plate["filaments"] if f.get("filament_id") is not None
    }
    for m in matches:
        src = by_filament_id.get(m.filament_id, {})
        spool = (
            smap.get((printer_id, m.tray))
            if printer_id is not None and m.tray is not None
            else None
        )
        grams = src.get("used_g")
        if m.status == "none":
            unmatched += 1
        if spool is not None and grams is not None:
            usages.append((spool, grams))
            if sinv.remaining_g(spool) < grams:
                warnings.append(
                    f"spool {_spool_label(spool)} low: {sinv.remaining_g(spool):.0f} g left"
                )
        filaments.append(
            PlanFilament(
                filament_id=m.filament_id,
                type=src.get("type"),
                color=src.get("color"),
                grams=grams,
                tray=m.tray,
                status=m.status,
                delta_e=m.delta_e,
                spool=SpoolBriefOut(
                    id=spool.id,
                    material_type=spool.material_type,
                    brand=spool.brand,
                    color_hex=spool.color_hex,
                    color_name=spool.color_name,
                )
                if spool is not None
                else None,
            )
        )
    if unmatched:
        warnings.append(f"{unmatched} filament(s) need a slot")

    if printer_id is not None:
        state = states.get(printer_id)
        if state is None:
            warnings.append("printer offline")
        elif not preflight_ok(state):
            warnings.append("printer busy")

    est_duration_s = plate.get("prediction_s")
    est_grams = plate.get("weight_g")
    machine_cost = price_run(None, est_duration_s, rates).machine_cost
    fc = filament_cost(usages, rates.filament_per_kg)
    if fc is None and est_grams is not None:
        fc = est_grams / 1000 * rates.filament_per_kg
    est_cost = None
    if fc is not None or machine_cost is not None:
        est_cost = (fc or 0.0) + (machine_cost or 0.0)

    existence = await _thumbnail_existence(settings, file.blob_hash, raw_plates)
    plates_out = [
        PlateOut.from_raw(p, thumbnail_available=existence.get(p["index"], False), rates=rates)
        for p in raw_plates
    ]

    return PlanOut(
        file_id=file.id,
        model_id=model.id if model is not None else None,
        plate=plate_no,
        plates=plates_out,
        printer_id=printer_id,
        printers=printers,
        filaments=filaments,
        ams_mapping=ams_mapping,
        use_ams=use_ams,
        est_duration_s=est_duration_s,
        est_grams=est_grams,
        est_cost=est_cost,
        warnings=warnings,
    )


async def create_job(
    db: AsyncSession, settings: Settings, ctx: AuthContext, printer_id: int, req: PrintRequest
) -> PrintJob:
    """Validate + enqueue a send-to-printer job (moved body of the legacy
    ``POST /printers/{id}/print`` handler -- see that route's docstring for
    why only fast pre-checks live here; the authoritative re-checks are in
    ``app.tasks.printing.send_to_printer``)."""
    from app.tasks.printing import send_to_printer

    printer = await db.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found")
    if not printer.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "printer is disabled")
    file = await db.get(File, req.file_id)
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
        subtask_name=req.subtask_name,
        plate=req.plate,
        ams_mapping=list(req.ams_mapping),
        created_by_user_id=ctx.user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    send_to_printer.apply_async(args=[job.id, req.model_dump()])
    await db.refresh(job)  # eager mode already ran the task through its own sync session
    return job
