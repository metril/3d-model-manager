"""The P6 slicer/dispatch plan's dispatcher (Task 7): every ``_dispatch_tick``
worth of work -- eligibility, compatibility, fanout, copies-split, retry and
reconcile -- in one place, driven by the beat task ``app.tasks.dispatch
.dispatch_queue`` every ``DISPATCH_INTERVAL_S`` seconds.

Entirely worker-side (SYNC world, ``app.tasks.base``), same reasoning as
every other task module.

**State machine** (``PrintQueueEntry.state``, ``app.models.library``):
``waiting`` (has a recipe, not sliced yet) -> ``slicing`` (``slice_file``
running) -> ``ready`` (has a ``sliced_file_id``, as-sliced items start here
directly) -> ``dispatched`` (a ``PrintJob`` sent) -> ``done``/``failed``/
``held``. ``eligible_queue_items`` is the ONE filter P7 (adds
``review_state``) and P8 (adds ``printer_hold_reason`` -- already a slot
here, always ``None`` in P6) extend; nothing else in this module should
duplicate its WHERE clause.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis import Redis
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.enums import Fanout, PrintJobState, QueueState, ReviewState
from app.models.library import File, Model, PrintQueueEntry
from app.models.printing import PlateReference, Printer, PrintJob
from app.models.processing import BlobMeta
from app.models.slicing import PrintRecipe
from app.schemas.printers import PrintRequest
from app.services import billing, drying_tick, ha_state, queue_schedule
from app.services import camera_state as cs
from app.services import plate_detection as pd
from app.services import spool_inventory as sinv
from app.services.ams_match import FilamentNeed, TrayInfo
from app.services.notify import events
from app.services.permissions import can_print_sync
from app.services.print_plan import NotSendableError, create_job_sync, model_matches, plan_mapping
from app.services.printer_state import DISPATCH_IDLE_STATES, read_state_sync
from app.services.queue_preheat import get_preheat_config_sync
from app.services.queue_settings import get_queue_config_sync

# 3 attempts total (slicing + dispatch share the one `attempts` counter) --
# a sidecar-down slicer failure doesn't consume one (`app.tasks.slicing`'s
# own `SlicerUnavailable` branch), but every dispatch-side failure does.
QUEUE_MAX_ATTEMPTS = 3
# |sliced-for nozzle - printer's reported nozzle| beyond this (mm) is a
# compatibility rejection; skipped entirely when either side is unknown.
NOZZLE_TOL_MM = 0.05
# At most this many `waiting` items get handed to `slice_file.delay` per
# tick -- slicing is CPU-heavy (its own `cpu` queue), so a long backlog
# fills the worker pool gradually rather than all at once.
SLICES_PER_TICK = 3
# A `slicing` entry whose `slicing_started_at` is older than this (seconds)
# never got a result back (worker crash/kill mid-slice) -- `_reconcile`
# bounces it back to `waiting` rather than leaving it stuck forever.
SLICING_STALE_S = 900


@dataclass(frozen=True)
class Candidate:
    id: int
    idle: bool


@dataclass(frozen=True)
class Needs:
    """What a queued item's sliced plate (or, pre-slice, its recipe's
    profiles -- not used in P6, reserved for a later plan) requires of the
    printer it lands on."""

    printer_model: str | None
    nozzle_mm: float | None
    filaments: list[FilamentNeed]


def select_printers(
    fanout: Fanout, n: int | None, candidates: Sequence[Candidate], copies: int
) -> list[int]:
    """``candidates`` = compatible + permitted printers in stable
    (name, id) order; one copy per returned id."""
    pool = list(candidates)
    if fanout == Fanout.FIRST_N:
        pool = pool[: max(1, n or 1)]
    idle = [c.id for c in pool if c.idle]
    if copies < 1 or not idle:
        return []
    if fanout == Fanout.SINGLE:
        return idle[:1]
    return idle[:copies]


def compatibility_reason(printer: Printer, state: dict | None, need: Needs) -> str | None:
    """``None`` means compatible. Checks, in order: sliced-for printer
    model (substring, shared with ``build_plan``), nozzle size (skipped
    when either side is unknown), then whether every sliced filament has
    SOME compatible tray (``plan_mapping``'s per-filament status, rejecting
    "type"/"none" -- a mere color mismatch, "color", is fine)."""
    if not model_matches(need.printer_model, printer.model):
        return f"sliced for {need.printer_model}"

    printer_nozzle = (state or {}).get("nozzle_mm")
    if (
        need.nozzle_mm is not None
        and printer_nozzle is not None
        and abs(need.nozzle_mm - printer_nozzle) > NOZZLE_TOL_MM
    ):
        return f"nozzle mismatch: needs {need.nozzle_mm}mm, printer has {printer_nozzle}mm"

    if need.filaments:
        trays = [
            TrayInfo(tray=t["slot"], type=t.get("tray_type"), color=t.get("color"))
            for t in (state or {}).get("trays") or []
        ]
        matches, _, _ = plan_mapping(need.filaments, trays, None)
        for m in matches:
            if m.status in ("type", "none"):
                return f"no compatible filament slot for filament {m.filament_id}"
    return None


def printer_hold_reason(client: Redis, printer_id: int) -> str | None:
    """A printer-level hold (an HA alert link with ``hold_queue=true``
    currently firing) that keeps the dispatcher off it regardless of
    idle/compatible. Delegates to ``app.services.ha_state.hold_reason`` --
    the alert key's own 60s TTL (``ha_state.ALERT_TTL_S``) means a dead
    poller fails open within one cycle rather than holding forever."""
    return ha_state.hold_reason(client, printer_id)


def plate_hold_reason(printer: Printer, gate_on: bool) -> str | None:
    """T3: the printer finished/failed a print and nobody confirmed the plate
    is clear yet. Only a hold while ``queue.require_plate_clear`` is on."""
    if gate_on and printer.awaiting_plate_clear:
        return "awaiting_plate_clear"
    return None


PLATE_FRAME_WAIT_S = 8.0


def plate_gate(
    session: Session,
    client: Redis,
    item: PrintQueueEntry,
    printer: Printer,
    cache: dict[int, pd.PlateResult | None],
) -> bool:
    """V4: ``True`` = the entry may dispatch. With ``options.plate_check``
    enabled and references captured, a camera frame is compared to them; a
    non-empty plate parks the entry in ``waiting`` with ``last_error='plate
    not empty'`` (no attempt consumed; re-checked every tick) and emits
    ``plate_not_empty`` once per hold. Fails OPEN on a missing camera
    frame/analysis error -- a flaky camera must not stall the queue."""
    cfg = pd.config_from_options(printer.options)
    if not cfg.enabled:
        return True
    if printer.id not in cache:
        cache[printer.id] = None
        refs = list(
            session.scalars(
                select(PlateReference.jpeg).where(
                    PlateReference.printer_id == printer.id,
                    PlateReference.plate_type == cfg.plate_type,
                )
            )
        )
        if refs:
            try:
                frame = cs.wait_for_frame(
                    client,
                    printer.id,
                    timeout=PLATE_FRAME_WAIT_S,
                    viewer_id=f"plate:{printer.id}",
                )
                if frame is not None:
                    cache[printer.id] = pd.analyze(frame, refs, cfg.threshold)
            except Exception:  # noqa: BLE001 - fail open
                cache[printer.id] = None
    res = cache[printer.id]
    if res is None or res.needs_calibration or res.empty:
        if item.last_error == pd.PLATE_NOT_EMPTY:
            item.last_error = None
            session.commit()
        return True
    first = item.last_error != pd.PLATE_NOT_EMPTY
    item.state = QueueState.WAITING
    item.last_error = pd.PLATE_NOT_EMPTY
    session.commit()
    if first:
        model = session.get(Model, item.model_id) if item.model_id is not None else None
        events.emit(
            "plate_not_empty",
            printer.id,
            {
                "name": model.name if model is not None else "print",
                "printer_name": printer.name,
                "queue_id": item.id,
                "score": res.score,
            },
        )
    return False


def eligible_queue_items(
    session: Session, *, include_deferred: bool = False
) -> list[PrintQueueEntry]:
    """THE single filter P7/P8 extend (module docstring). P6: a per-file
    entry (``file_id`` set -- bare legacy entries never dispatch),
    ``waiting``/``ready``, under the shared attempt cap, oldest position
    first. P7 adds ``review_state IN (none, approved) AND hold_reason IS
    NULL`` here ONLY; P8 consults ``printer_hold_reason`` per-printer
    below, not here (a hold can clear without touching the item itself).
    T4: excludes ``scheduled_at`` in the future and unreleased ``manual_start``
    entries unless ``include_deferred`` (used to ask "is more work queued?").
    """
    stmt = (
        select(PrintQueueEntry)
        .where(
            PrintQueueEntry.file_id.is_not(None),
            PrintQueueEntry.state.in_((QueueState.WAITING, QueueState.READY)),
            PrintQueueEntry.attempts < QUEUE_MAX_ATTEMPTS,
            PrintQueueEntry.review_state.in_((ReviewState.NONE, ReviewState.APPROVED)),
            PrintQueueEntry.hold_reason.is_(None),
        )
        .order_by(PrintQueueEntry.position)
    )
    if not include_deferred:
        stmt = stmt.where(
            or_(PrintQueueEntry.scheduled_at.is_(None), PrintQueueEntry.scheduled_at <= func.now()),
            or_(
                PrintQueueEntry.manual_start.is_(False),
                PrintQueueEntry.released_at.is_not(None),
            ),
        )
    return list(session.scalars(stmt).all())


def _bump_attempt(item: PrintQueueEntry, error: str) -> None:
    item.attempts += 1
    item.last_error = error
    item.assigned_printer_id = None
    item.print_job_id = None
    item.state = QueueState.FAILED if item.attempts >= QUEUE_MAX_ATTEMPTS else QueueState.READY


def _reconcile(session: Session, counts: dict, client: Redis | None = None) -> None:
    """Step 1 (I5): a `dispatched` item's job now tells the whole story --
    FINISHED means the print actually completed (-> `done`, the item's
    terminal state, hidden from the default queue listing); CANCELED, or
    FAILED after it ever started (`started_at IS NOT NULL`, i.e. the
    printer picked it up and it failed mid-print) both mean the one attempt
    that happened is final -- no retry, straight to `failed`; FAILED before
    it ever started (`started_at IS NULL`) never got picked up by a running
    print at all -- apply the normal retry rule instead of giving up on the
    first try.

    Step 2: a `slicing` item whose `slicing_started_at` is older than
    `SLICING_STALE_S` never got a result back (worker crash/kill mid-slice)
    -- bounced back to `waiting` so the next tick re-slices it. This does
    NOT consume an attempt, same as `SlicerUnavailable` -- the slice itself
    never ran to completion either way.
    """
    items = session.scalars(
        select(PrintQueueEntry).where(
            PrintQueueEntry.state == QueueState.DISPATCHED,
            PrintQueueEntry.print_job_id.is_not(None),
        )
    ).all()
    for item in items:
        job = session.get(PrintJob, item.print_job_id)
        if job is None:
            continue
        if job.state == PrintJobState.FINISHED:
            item.state = QueueState.DONE
            counts["done"] += 1
            queue_schedule.note_finished(client, item)
        elif job.state == PrintJobState.CANCELED or (
            job.state == PrintJobState.FAILED and job.started_at is not None
        ):
            item.state = QueueState.FAILED
            item.last_error = job.printer_error or (
                "canceled" if job.state == PrintJobState.CANCELED else "print failed"
            )
            queue_schedule.note_finished(client, item)
        elif job.state == PrintJobState.FAILED and job.started_at is None:
            _bump_attempt(item, job.printer_error or "send failed")
            counts["retried"] += 1

    stale_cutoff = datetime.now(UTC) - timedelta(seconds=SLICING_STALE_S)
    stale_items = session.scalars(
        select(PrintQueueEntry).where(
            PrintQueueEntry.state == QueueState.SLICING,
            PrintQueueEntry.slicing_started_at.is_not(None),
            PrintQueueEntry.slicing_started_at < stale_cutoff,
        )
    ).all()
    for item in stale_items:
        item.state = QueueState.WAITING
        item.last_error = "slicing timed out"
        item.slicing_started_at = None

    session.commit()


def _busy_printer_ids(session: Session, client: Redis) -> set[int]:
    """Step 2: a printer with a live app-origin job in flight, a non-idle
    (or absent) Redis state, a P8 hold, or simply disabled -- never a
    dispatch target this tick."""
    busy: set[int] = set(
        session.scalars(
            select(PrintJob.printer_id).where(
                PrintJob.state.in_(
                    (PrintJobState.QUEUED, PrintJobState.UPLOADING, PrintJobState.STARTING)
                )
            )
        ).all()
    )
    gate_plate = get_queue_config_sync(session).require_plate_clear
    for p in session.scalars(select(Printer)).all():
        if (
            not p.enabled
            or p.maintenance_mode
            or printer_hold_reason(client, p.id) is not None
            or drying_tick.hold_reason(client, p.id) is not None
            or plate_hold_reason(p, gate_plate) is not None
        ):
            busy.add(p.id)
            continue
        state = read_state_sync(client, p.id)
        if _still_booting(client, p.id, state):
            busy.add(p.id)
            continue
        if (state or {}).get("gcode_state") not in DISPATCH_IDLE_STATES:
            busy.add(p.id)
    return busy


def _still_booting(client: Redis, printer_id: int, state: dict | None) -> bool:
    """Q7: after a plug wake-up the Redis state is stale until the printer
    reports again. Busy until ``last_report_at`` is newer than the wake time
    (capped at ``BOOT_WAIT_S``), then the marker is cleared."""
    from app.services.smart_plugs.manager import BOOT_WAIT_S, woken_key

    raw = client.get(woken_key(printer_id))
    if raw is None:
        return False
    woken = float(raw)
    reported = None
    with contextlib.suppress(ValueError):
        reported = datetime.fromisoformat(str((state or {}).get("last_report_at"))).timestamp()
    if (reported is not None and reported > woken) or datetime.now(
        UTC
    ).timestamp() - woken > BOOT_WAIT_S:
        client.delete(woken_key(printer_id))
        return False
    return True


def _needs_for_item(session: Session, item: PrintQueueEntry) -> Needs | None:
    """``None`` means "not ready to evaluate yet" (no sliced file, or its
    metadata hasn't been extracted) -- the caller leaves the item `ready`
    and tries again next tick, same as a slow pipeline extraction step."""
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
    filaments = (plate or {}).get("filaments") or []
    needs = [
        FilamentNeed(
            filament_id=f["filament_id"] if f.get("filament_id") is not None else i + 1,
            type=f.get("type"),
            color=f.get("color"),
            grams=f.get("used_g"),
        )
        for i, f in enumerate(filaments)
    ]
    return Needs(printer_model=meta.printer_model, nozzle_mm=meta.nozzle, filaments=needs)


def _candidate_printers(
    session: Session,
    client: Redis,
    item: PrintQueueEntry,
    recipe: PrintRecipe | None,
    need: Needs,
    busy: set[int],
    used_this_tick: set[int],
) -> tuple[list[Candidate], bool, str | None]:
    """Returns ``(candidates, had_any_permitted, reason)``. An empty
    candidate list with ``had_any_permitted=False`` means "hold" (no
    printer this item's creator may send to at all -- revoked access, or a
    null/inactive creator); with ``had_any_permitted=True`` it means either
    nothing matched the item/recipe's own printer target at all (M1: stays
    `ready` with ``reason="no matching printer"`` rather than held -- not an
    access problem), or every permitted printer failed
    ``compatibility_reason`` (``reason`` is the last one seen) -- same
    `ready`-for-retry outcome.
    """
    stmt = (
        select(Printer)
        .where(Printer.maintenance_mode.is_(False))
        .order_by(Printer.name, Printer.id)
    )
    if item.printer_id is not None:
        stmt = stmt.where(Printer.id == item.printer_id)
    elif recipe is not None and recipe.target_printer_id is not None:
        stmt = stmt.where(Printer.id == recipe.target_printer_id)
    printers = list(session.scalars(stmt).all())
    if (
        item.printer_id is None
        and recipe is not None
        and recipe.target_printer_id is None
        and recipe.target_printer_model is not None
    ):
        printers = [p for p in printers if model_matches(recipe.target_printer_model, p.model)]

    if not printers:
        # M1: nothing matches this item's own printer target at all -- not
        # a permission problem, so never `held`.
        return [], True, "no matching printer"

    permitted = [p for p in printers if can_print_sync(session, item.created_by_user_id, p.id)]
    if not permitted:
        return [], False, None

    reason: str | None = None
    compatible: list[Printer] = []
    for p in permitted:
        state = read_state_sync(client, p.id)
        r = compatibility_reason(p, state, need)
        if r is None:
            compatible.append(p)
        else:
            reason = r
    if not compatible:
        return [], True, reason

    candidates = [
        Candidate(id=p.id, idle=(p.id not in busy and p.id not in used_this_tick))
        for p in compatible
    ]
    return candidates, True, None


def _clone_entry(
    session: Session,
    item: PrintQueueEntry,
    *,
    copies: int,
    state: QueueState,
    position: int | None = None,
) -> PrintQueueEntry:
    """``position=None`` (the extra-printer clones a spread/first_n fanout
    sends to) appends at the end of the queue -- order among those doesn't
    matter, they're dispatched this very tick. ``position`` given (I6: the
    one leftover-copies clone) instead takes exactly ``item``'s own slot,
    densely shifting every entry from there on back by one -- so it keeps
    running ahead of whatever was queued after ``item``, rather than
    getting appended behind it."""
    if position is None:
        max_position = session.scalar(select(func.max(PrintQueueEntry.position))) or 0
        position = max_position + 1
    else:
        session.execute(
            update(PrintQueueEntry)
            .where(PrintQueueEntry.position >= position, PrintQueueEntry.id != item.id)
            .values(position=PrintQueueEntry.position + 1)
        )
    clone = PrintQueueEntry(
        model_id=item.model_id,
        file_id=item.file_id,
        sliced_file_id=item.sliced_file_id,
        printer_id=item.printer_id,
        recipe_id=item.recipe_id,
        plate=item.plate,
        copies=copies,
        state=state,
        position=position,
        ams_mapping=item.ams_mapping,
        print_options=item.print_options,
        created_by_user_id=item.created_by_user_id,
        cost_center_id=item.cost_center_id,
        review_state=item.review_state,
    )
    session.add(clone)
    session.flush()
    return clone


def _resolve_mapping(
    client: Redis, item: PrintQueueEntry, printer_id: int, need: Needs
) -> tuple[list[int], bool]:
    """Stored ``ams_mapping`` if we're sending to the item's ORIGINALLY
    requested printer (the one it was set up against), else a fresh
    ``plan_mapping`` against the printer the dispatcher actually picked."""
    if item.printer_id == printer_id and item.ams_mapping:
        mapping = list(item.ams_mapping)
    else:
        state = read_state_sync(client, printer_id)
        trays = [
            TrayInfo(tray=t["slot"], type=t.get("tray_type"), color=t.get("color"))
            for t in (state or {}).get("trays") or []
        ]
        _, mapping, _ = plan_mapping(need.filaments, trays, None)
    use_ams = any(v not in (sinv.UNMAPPED, sinv.EXTERNAL_SLOT) for v in mapping)
    return mapping, use_ams


def _send_one(
    session: Session,
    settings: Settings,
    client: Redis,
    target: PrintQueueEntry,
    printer_id: int,
    mapping: list[int],
    use_ams: bool,
) -> None:
    target.copies = 1
    opts = target.print_options or {}
    req = PrintRequest(
        file_id=target.sliced_file_id,
        plate=target.plate or 1,
        use_ams=use_ams,
        ams_mapping=mapping,
        bed_levelling=opts.get("bed_levelling", True),
        flow_cali=opts.get("flow_cali", True),
        timelapse=opts.get("timelapse", False),
    )
    try:
        job = create_job_sync(session, settings, client, printer_id, req, target.created_by_user_id)
        target.state = QueueState.DISPATCHED
        target.assigned_printer_id = printer_id
        target.print_job_id = job.id
    except (ValueError, NotSendableError) as exc:
        _bump_attempt(target, str(exc))
    session.commit()


def _dispatch(
    session: Session,
    settings: Settings,
    client: Redis,
    item: PrintQueueEntry,
    selected: list[int],
    fanout: Fanout,
    need: Needs,
) -> None:
    """Copies-split (module docstring): the "sent row" (``item`` itself,
    mutated in place) carries exactly 1 copy on ``selected[0]``; any FURTHER
    selected printers (spread/first_n landing on more than one idle
    printer this tick) each get their own extra dispatched clone; whatever
    copies are left over (not enough idle candidates this tick) become ONE
    more `ready` clone -- pinned back to the SAME printer when ``fanout``
    is SINGLE (it always serializes copies onto one printer), left
    unpinned otherwise so the next tick can spread further.

    M2: nothing is split off unless the PRIMARY send actually succeeded --
    a failed primary send means nothing was sent to any printer yet, so
    ``item`` keeps every one of its original copies for its own retry
    instead of a leftover clone (which would have inherited an arbitrary,
    none-sent copy count) appearing alongside it.
    """
    original_copies = item.copies
    # P7: the whole reservation posted at enqueue/slice time covers
    # `original_copies` copies -- each clone's own share (`unit *
    # clone.copies`) is handed off via `billing.transfer`, a brand-new
    # reservation against the SAME center rather than carving the amount
    # out of `item`'s own (unchanged) reservation -- conservative
    # double-counting until the original settles (module docstring of
    # `app.services.billing.transfer`).
    unit = item.reserved_cost / original_copies if item.reserved_cost is not None else None
    primary = selected[0]
    mapping, use_ams = _resolve_mapping(client, item, primary, need)
    _send_one(session, settings, client, item, primary, mapping, use_ams)

    if item.state != QueueState.DISPATCHED:
        item.copies = original_copies
        session.commit()
        return

    for printer_id in selected[1:]:
        clone = _clone_entry(session, item, copies=1, state=QueueState.READY)
        if unit is not None:
            billing.transfer(session, item, clone, unit * clone.copies)
        m, ua = _resolve_mapping(client, item, printer_id, need)
        _send_one(session, settings, client, clone, printer_id, m, ua)

    remaining = original_copies - len(selected)
    if remaining > 0:
        # I6: takes `item`'s own slot (densely renumbered), not appended
        # behind whatever else is already queued.
        rest = _clone_entry(
            session, item, copies=remaining, state=QueueState.READY, position=item.position
        )
        if unit is not None:
            billing.transfer(session, item, rest, unit * rest.copies)
        if fanout == Fanout.SINGLE:
            rest.printer_id = primary
            session.commit()


def queue_target_ids(
    session: Session, client: Redis, items: Sequence[PrintQueueEntry]
) -> tuple[set[int], set[int]]:
    """``(ready_ids, any_ids)``: printers a READY item could be placed on
    (ignoring busy/idle, via ``_candidate_printers``) and, additionally, the
    printers any WAITING item (not yet sliced -- compatibility unknown) may
    land on. Drives plug wake-up and the auto-off guard."""
    ready: set[int] = set()
    waiting: set[int] = set()
    for item in items:
        if item.state == QueueState.READY:
            need = _needs_for_item(session, item)
            if need is None:
                continue
            recipe = (
                session.get(PrintRecipe, item.recipe_id) if item.recipe_id is not None else None
            )
            cands, _, _ = _candidate_printers(session, client, item, recipe, need, set(), set())
            ready.update(c.id for c in cands)
        elif item.state == QueueState.WAITING:
            stmt = select(Printer.id).where(Printer.maintenance_mode.is_(False))
            if item.printer_id is not None:
                stmt = stmt.where(Printer.id == item.printer_id)
            waiting.update(session.scalars(stmt).all())
    return ready, ready | waiting


def _wake_plugs(
    session: Session, settings: Settings, client: Redis, items: Sequence[PrintQueueEntry]
) -> None:
    """Q7: power on ``auto_on_before_print`` plugs, only for printers a READY
    item may actually be placed on (a powered-off printer never reports idle
    otherwise)."""
    if not any(i.state == QueueState.READY for i in items):
        return
    from app.services.smart_plugs.manager import wake_for_queue

    try:
        ready, _ = queue_target_ids(session, client, items)
        if ready:
            wake_for_queue(session, settings, client, ready)
    except Exception:  # noqa: BLE001 -- a plug failure must never stall dispatch
        session.rollback()


def _housekeeping(session: Session, settings: Settings, client: Redis, cfg) -> None:
    """T4: bed keep-warm + auto power-off. Both ask "is more work queued for
    this printer?" over the deferred-inclusive queue; a failure here must
    never stall dispatch."""
    has_auto_off = next(client.scan_iter(f"{queue_schedule.AUTO_OFF_PREFIX}*", count=10), None)
    has_warm = next(client.scan_iter(f"{queue_schedule.KEEPWARM_PREFIX}*", count=10), None)
    if not (cfg.keep_warm_enabled or has_auto_off or has_warm):
        return
    try:
        horizon = datetime.now(UTC) + timedelta(minutes=cfg.keep_warm_max_min)
        queued = eligible_queue_items(session, include_deferred=True)
        _, wanted = queue_target_ids(session, client, queued)
        soon = [i for i in queued if i.scheduled_at is None or i.scheduled_at <= horizon]
        _, pending = queue_target_ids(session, client, soon)
        queue_schedule.run_keep_warm(session, client, cfg, pending)
        queue_schedule.process_auto_off(session, settings, client, wanted)
    except Exception:  # noqa: BLE001
        session.rollback()


def dispatch_once(session: Session, client: Redis, settings: Settings) -> dict:
    counts = {"sliced": 0, "sent": 0, "held": 0, "retried": 0, "done": 0, "skipped": 0}
    _reconcile(session, counts, client)
    items = eligible_queue_items(session)
    _wake_plugs(session, settings, client, items)
    preheat_cfg = get_preheat_config_sync(session)
    queue_schedule.release_abandoned_preheats(session, client, preheat_cfg, {i.id for i in items})
    _housekeeping(session, settings, client, preheat_cfg)
    busy = _busy_printer_ids(session, client)
    used_this_tick: set[int] = set()

    plate_cache: dict[int, pd.PlateResult | None] = {}
    for item in items:
        plate_held = (
            item.state == QueueState.WAITING
            and item.last_error == pd.PLATE_NOT_EMPTY
            and item.sliced_file_id is not None
        )
        if item.state == QueueState.WAITING and not plate_held:
            if (
                item.recipe_id is not None
                and item.sliced_file_id is None
                and counts["sliced"] < SLICES_PER_TICK
            ):
                from app.tasks.slicing import slice_file

                item.state = QueueState.SLICING
                item.slicing_started_at = datetime.now(UTC)
                session.commit()
                slice_file.delay(item.id)
                counts["sliced"] += 1
            continue

        if plate_held:
            item.state = QueueState.READY  # plate_gate re-parks it if still occupied
        elif item.state != QueueState.READY:
            continue

        need = _needs_for_item(session, item)
        if need is None:
            continue

        recipe = session.get(PrintRecipe, item.recipe_id) if item.recipe_id is not None else None
        candidates, had_permitted, reason = _candidate_printers(
            session, client, item, recipe, need, busy, used_this_tick
        )
        if not had_permitted:
            item.state = QueueState.HELD
            item.last_error = "no printer access for this item's creator"
            session.commit()
            counts["held"] += 1
            continue
        if not candidates:
            item.last_error = reason
            session.commit()
            continue

        fanout = recipe.fanout if recipe is not None else Fanout.SINGLE
        n = recipe.n if recipe is not None else None
        selected = select_printers(fanout, n, candidates, item.copies)
        if not selected:
            continue

        if queue_schedule.previous_failed(session, item, selected[0]):
            queue_schedule.skip_previous_failed(session, item, selected[0])
            counts["skipped"] += 1
            continue
        printer = session.get(Printer, selected[0])
        if printer is not None and not queue_schedule.preheat_gate(
            session, client, preheat_cfg, item, printer, [f.type for f in need.filaments]
        ):
            used_this_tick.add(selected[0])  # one preheating entry per printer
            continue

        if printer is not None and not plate_gate(session, client, item, printer, plate_cache):
            used_this_tick.add(selected[0])
            continue

        _dispatch(session, settings, client, item, selected, fanout, need)
        used_this_tick.update(selected)
        counts["sent"] += 1

    return counts
