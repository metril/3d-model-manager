"""Print plan / send (P5 spool tray plan, Task 7): resolve a sliced file's
plate + filament needs against a target printer's live AMS trays and spool
inventory, matching Task 6's ``ams_match``/``spool_inventory`` services, and
the shared ``create_job`` the legacy ``/printers/{id}/print`` route and the
new ``/print/send`` route both call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import redis.asyncio as aioredis
from fastapi import HTTPException, status
from redis import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import Settings

if TYPE_CHECKING:
    # Deferred to a function-local import at its one runtime call site
    # (`build_plan`) -- `app.api.deps` pulls in the whole `app.api` package
    # (which imports `print_plan` right back, for `build_plan`/`create_job`),
    # and the P6 dispatcher (`app.services.dispatcher` -> this module) is
    # reached from the Celery worker's OWN entrypoint (`app.tasks.celery_app`)
    # before `app.api` is ever loaded -- a top-level import here would be a
    # real circular import in that process, not just an API-layer one.
    from app.api.deps import AuthContext
from app.models import Blob, File, Model, Printer, PrintJob, PrintQueueEntry, Revision
from app.models.enums import BlobFormat, PrintJobState
from app.models.processing import BlobMeta
from app.schemas.library import PlateOut
from app.schemas.print_flow import PlanFilament, PlanIn, PlanOut, PlanPrinter
from app.schemas.printers import PrintRequest, SpoolBriefOut
from app.services import derivatives, library
from app.services import spool_inventory as sinv
from app.services.ams_match import FilamentNeed, Match, TrayInfo, match, to_ams_mapping
from app.services.app_config import get_app_config, get_app_config_sync
from app.services.costing import CostRates, filament_cost, price_run
from app.services.printer_state import preflight_ok, read_state_async, read_state_sync


def _spool_label(s) -> str:
    bits = [s.color_name or s.color_hex, s.brand, s.material_type]
    return " ".join(b for b in bits if b) or f"spool {s.id}"


def model_matches(need: str | None, printer_model: str | None) -> bool:
    """Whether a sliced file's required ``printer_model`` (or a recipe's
    ``target_printer_model``) is compatible with an actual printer's
    reported ``model`` -- a case-insensitive substring match (e.g. "Bambu
    Lab A1 mini" sliced-for vs a printer's "A1 mini"). Either side unset is
    treated as compatible (nothing to check against). Shared by
    ``build_plan``'s ``PlanPrinter.compatible`` and the P6 dispatcher's
    ``compatibility_reason`` (``app.services.dispatcher``) so the two never
    drift.
    """
    if not need or not printer_model:
        return True
    a, b = need.lower(), printer_model.lower()
    return a in b or b in a


def recipe_compatibility_reason(
    recipe, profile_printer_model: str | None, printer, pinned_name: str | None = None
) -> str | None:
    """Why ``recipe`` can't run on ``printer`` (``None`` when it can).

    Used by the recipe picker (``GET /recipes?printer_id=``): a pin to a
    different printer, a ``target_printer_model`` mismatch, or the recipe's
    printer *profile* being for another model -- the latter two via
    ``model_matches``. The dispatcher applies only the pin and target-model
    checks (``app.services.dispatcher``), so the picker is the stricter of
    the two by design.
    """
    if recipe.target_printer_id is not None and recipe.target_printer_id != printer.id:
        return f"pinned to {pinned_name or 'another printer'}"
    if not model_matches(recipe.target_printer_model, printer.model):
        return f"for {recipe.target_printer_model}"
    if not model_matches(profile_printer_model, printer.model):
        return f"profile is for {profile_printer_model}"
    return None


def recipe_scope_reason(
    recipe,
    printer_key: str | None,
    nozzle: float | None,
    profiles: dict,
    layer: float | None = None,
) -> str | None:
    """Why ``recipe`` is out of the Profiles-page scope (printer model key +
    nozzle [+ layer height]); ``None`` when its printer, process and filament
    profiles all fit. Used only by the list/picker endpoints -- the dispatcher
    keeps ``recipe_compatibility_reason``. ``profiles`` maps id -> SlicerProfile."""
    from app.services.slicer_profiles import profile_in_scope, scope_mismatch_reason

    checks = [
        ("printer", profiles.get(recipe.printer_profile_id)),
        ("process", profiles.get(recipe.process_profile_id)),
        *(("filament", profiles.get(fid)) for fid in recipe.filament_profile_ids or [] if fid),
    ]
    for label, prof in checks:
        if prof is None:
            continue
        why = scope_mismatch_reason(prof, printer_key, nozzle)
        if why:
            return f"{label} profile {why}"
    process = profiles.get(recipe.process_profile_id)
    if (
        layer is not None
        and process is not None
        and not profile_in_scope(process, None, None, layer)
    ):
        return "layer height differs"
    return None


class NotSendableError(ValueError):
    """A file/blob isn't in a state ``create_job``/``create_job_sync`` can
    send to a printer (wrong format, missing blob)."""


def _check_sendable(blob: Blob | None) -> None:
    if blob is None or blob.format != BlobFormat.GCODE_3MF:
        raise NotSendableError("only sliced .gcode.3mf files can be sent to a printer")


def plan_mapping(
    needs: list[FilamentNeed], trays: list[TrayInfo], overrides: dict[int, int] | None
) -> tuple[list[Match], list[int], bool]:
    """Pure AMS-mapping core shared by ``build_plan`` (preview) and the P6
    dispatcher (``app.services.dispatcher``, both the compatibility check
    and the actual mapping sent with a dispatched job): match each sliced
    filament need to a live tray, and derive the flat ``ams_mapping`` array
    plus whether the AMS is used at all (vs. the single external spool).
    """
    matches = match(needs, trays, overrides=overrides)
    ams_mapping = to_ams_mapping(matches)
    use_ams = any(m.tray not in (None, sinv.EXTERNAL_SLOT) for m in matches)
    return matches, ams_mapping, use_ams


def estimate_from_plate(
    plate: dict, usages: list[tuple[object | None, float]], rates: CostRates
) -> float | None:
    """A plate's estimated total cost: per-spool filament pricing
    (``usages``) falling back to the plate's own ``weight_g`` at the
    global rate, plus machine time off ``prediction_s``. Extracted from
    ``build_plan`` (no behaviour change) so ``estimate_item_sync`` below
    can share it.
    """
    est_duration_s = plate.get("prediction_s")
    est_grams = plate.get("weight_g")
    machine_cost = price_run(None, est_duration_s, rates).machine_cost
    fc = filament_cost(usages, rates.filament_per_kg)
    if fc is None and est_grams is not None:
        fc = est_grams / 1000 * rates.filament_per_kg
    if fc is None and machine_cost is None:
        return None
    return (fc or 0.0) + (machine_cost or 0.0)


def estimate_item_sync(session: Session, settings: Settings, item: PrintQueueEntry) -> float | None:
    """Worker/enqueue-side (SYNC world) cost estimate for a queue entry
    that already has a sliced file: its ``BlobMeta`` plate's filaments,
    priced per-spool via ``item.ams_mapping``'s trays on ``item.printer_id``
    (``spool_inventory.spool_for_tray``) -- the global rate when the
    printer/tray is unknown (``estimate_from_plate``'s own fallback).
    ``None`` when there's no sliced file or metadata to estimate from yet.
    """
    if item.sliced_file_id is None:
        return None
    file = session.get(File, item.sliced_file_id)
    if file is None:
        return None
    meta = session.get(BlobMeta, file.blob_hash)
    if meta is None:
        return None
    plates = (meta.raw or {}).get("plates") or []
    plate_no = item.plate if item.plate is not None else (plates[0]["index"] if plates else None)
    plate = next((p for p in plates if p["index"] == plate_no), None)
    if plate is None:
        return None

    cfg = get_app_config_sync(session, settings)
    rates = CostRates.from_config(cfg)
    mapping = item.ams_mapping or []
    usages: list[tuple[object | None, float]] = []
    for i, f in enumerate(plate.get("filaments") or []):
        grams = f.get("used_g")
        if grams is None:
            continue
        fid = f["filament_id"] if f.get("filament_id") is not None else i + 1
        tray: int | None = None
        if 0 <= fid - 1 < len(mapping):
            tray = mapping[fid - 1]
        elif i < len(mapping):
            tray = mapping[i]
        spool = (
            sinv.spool_for_tray(session, item.printer_id, tray)
            if item.printer_id is not None
            else None
        )
        usages.append((spool, grams))
    per_copy = estimate_from_plate(plate, usages, rates)
    # I1: the reservation must cover every copy up front (the dispatcher's
    # copies-split fanout only ever TRANSFERS a slice of this total onto
    # each clone -- see `billing.transfer` -- it never tops up a short
    # reservation), so the per-clone unit (`reserved_cost / copies`) stays
    # consistent with what was actually priced per copy.
    if per_copy is None:
        return None
    return per_copy * item.copies


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
    from app.api.deps import visible_printer_ids

    file = await library.get_live_file(db, body.file_id)
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
        compatible = model_matches(printer_model, p.model)
        reason = f"sliced for {printer_model}" if not compatible else None
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
            # Pre-P5 meta parsed before `filament_id` existed carries none --
            # fall back to the filament's 1-based position on the plate (the
            # same convention `ams_mapping`/the archive's tray mapping use).
            filament_id=f["filament_id"] if f.get("filament_id") is not None else i + 1,
            type=f.get("type"),
            color=f.get("color"),
            grams=f.get("used_g"),
        )
        for i, f in enumerate(plate["filaments"])
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

    matches, ams_mapping, use_ams = plan_mapping(needs, tray_infos, body.overrides)

    cfg = await get_app_config(db, settings)
    rates = CostRates.from_config(cfg)

    filaments: list[PlanFilament] = []
    usages: list[tuple[object | None, float]] = []
    warnings: list[str] = []
    needs_slot = 0
    type_mismatch = 0
    by_filament_id = {
        (f["filament_id"] if f.get("filament_id") is not None else i + 1): f
        for i, f in enumerate(plate["filaments"])
    }
    for m in matches:
        src = by_filament_id.get(m.filament_id, {})
        spool = (
            smap.get((printer_id, m.tray))
            if printer_id is not None and m.tray is not None
            else None
        )
        grams = src.get("used_g")
        # Both an unmatched filament ("none" -- no tray at all) and a type
        # mismatch ("type" -- forced onto the wrong material, e.g. via an
        # override) are send-blocking (`hasRed` in the dialog); both must
        # surface as a warning, not just "none".
        if m.status == "none":
            needs_slot += 1
        elif m.status == "type":
            type_mismatch += 1
        if grams is not None:
            # Price EVERY charged tray, not just ones with a registered
            # spool -- an unassigned tray with grams still gets priced at
            # the global rate (`filament_cost`'s `(None, grams)` fallback).
            usages.append((spool, grams))
            if spool is not None and sinv.remaining_g(spool) < grams:
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
                spool=SpoolBriefOut.from_model(spool) if spool is not None else None,
            )
        )
    if needs_slot:
        warnings.append(f"{needs_slot} filament(s) need a slot")
    if type_mismatch:
        warnings.append(f"{type_mismatch} filament(s) assigned to the wrong material type")

    if printer_id is not None:
        state = states.get(printer_id)
        if state is None:
            warnings.append("printer offline")
        elif not preflight_ok(state):
            warnings.append("printer busy")

    est_duration_s = plate.get("prediction_s")
    est_grams = plate.get("weight_g")
    est_cost = estimate_from_plate(plate, usages, rates)

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
    ``app.tasks.printing.send_to_printer``).

    P7 review/billing plan: 409 ``queue_required`` when sending straight to
    a printer would skip the review gate -- either this printer
    ``requires_review`` for ``ctx`` (and they can't review themselves), or
    billing is on at all (every reservation/charge needs a queue row to
    hang off of). The dispatcher's OWN sync send path
    (``create_job_sync``) is deliberately NOT gated -- by the time the
    dispatcher calls it, the item already cleared ``eligible_queue_items``'
    review/hold filter.
    """
    from app.services.review import needs_review
    from app.tasks.printing import send_to_printer

    cfg = await get_app_config(db, settings)
    if needs_review(ctx, {printer_id}) or cfg.billing_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "queue_required")

    printer = await db.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found")
    if not printer.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "printer is disabled")
    file = await library.get_live_file(db, req.file_id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    blob = await db.get(Blob, file.blob_hash)
    try:
        _check_sendable(blob)
    except NotSendableError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
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


def create_job_sync(
    session: Session,
    settings: Settings,
    client: Redis,
    printer_id: int,
    req: PrintRequest,
    created_by_user_id: int | None,
) -> PrintJob:
    """Worker-side (SYNC world, ``app.tasks.base``) twin of ``create_job``,
    for the P6 dispatcher (``app.services.dispatcher``) -- same validation
    (printer exists/enabled, file is a sendable ``.gcode.3mf``, printer
    preflight-ready), raising a plain exception instead of an
    ``HTTPException`` (there's no request to 4xx -- the dispatcher's own
    retry rule handles a raise here). ``created_by_user_id`` is the queue
    entry's own creator, not an ``AuthContext`` (the dispatcher has no live
    session). The ``send_to_printer`` task is invoked with its own
    exception caught, not re-raised: in eager-mode tests that task runs its
    full authoritative re-check synchronously and writes the job's OWN
    FAILED state itself on failure -- that's the single source of truth,
    picked up by ``dispatcher._reconcile``'s retry step on the NEXT tick.
    If ``apply_async`` itself blows up (I4: before the task ever got a
    chance to write anything, e.g. the broker is unreachable), the job is
    marked FAILED here instead -- ``started_at`` stays ``None`` so that very
    same reconcile step still picks it up, rather than leaving a QUEUED job
    that will never move again.
    """
    from app.tasks.printing import send_to_printer

    printer = session.get(Printer, printer_id)
    if printer is None:
        raise ValueError("printer not found")
    if not printer.enabled:
        raise ValueError("printer is disabled")
    file = library.get_live_file_sync(session, req.file_id)
    if file is None:
        raise ValueError("file not found")
    blob = session.get(Blob, file.blob_hash)
    _check_sendable(blob)
    state = read_state_sync(client, printer_id)
    if not preflight_ok(state):
        gs = state.get("gcode_state") if state else "unknown"
        raise ValueError(f"printer not ready (state={gs}); must be IDLE/FINISH/FAILED")
    job = PrintJob(
        printer_id=printer_id,
        file_id=file.id,
        state=PrintJobState.QUEUED,
        subtask_name=req.subtask_name,
        plate=req.plate,
        ams_mapping=list(req.ams_mapping),
        created_by_user_id=created_by_user_id,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    try:
        send_to_printer.apply_async(args=[job.id, req.model_dump()])
    except Exception as exc:  # noqa: BLE001 - see docstring: I4
        job.state = PrintJobState.FAILED
        job.printer_error = str(exc)
        job.started_at = None
        session.commit()
    session.refresh(job)
    return job
