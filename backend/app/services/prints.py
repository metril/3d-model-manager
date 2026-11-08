"""Per-model print history (Branch 5 Task 1): a user-entered log of print
attempts, distinct from the ``print_queue`` "to print" worklist
(``app.services.queue``) and ``print_jobs``' live send-to-printer telemetry
(M4). The API layer (``app.api.prints``) already resolves/404s the parent
model for the two model-scoped routes, so ``model_id`` here is trusted.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models.enums import ArchiveResult, PrintJobState, PrintResult, PrintSource
from app.models.library import Material, Model, Print
from app.models.printing import PrintArchive, PrintJob
from app.schemas.prints import PrintOut
from app.services.archive_blobs import blob_hashes, release_blobs
from app.services.costing import CostRates, price_run


async def _check_material_id(db: AsyncSession, material_id: int | None) -> None:
    if material_id is not None and await db.get(Material, material_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown material_id")


async def create_print(
    db: AsyncSession,
    model_id: int,
    *,
    printed_at: datetime | None,
    printer_name: str | None,
    filament: str | None,
    filament_g: float | None = None,
    material_id: int | None = None,
    result: PrintResult,
    duration_min: int | None,
    notes: str | None,
    created_by_user_id: int | None = None,
    rates: CostRates,
) -> PrintOut:
    """Inserts the print row AND bumps ``Model.print_count`` by one, in the
    SAME transaction (R13b Risk resolution 4: this -- plus ``delete_print``
    below -- is the ONLY writer of ``print_count``; ``recount_print_counts``
    is the self-healing backstop, not a second writer).
    """
    await _check_material_id(db, material_id)
    cost = price_run(filament_g, None if duration_min is None else duration_min * 60, rates)
    row = Print(
        model_id=model_id,
        filament_cost=cost.filament_cost,
        machine_cost=cost.machine_cost,
        total_cost=cost.total_cost,
        printer_name=printer_name,
        filament=filament,
        filament_g=filament_g,
        material_id=material_id,
        result=result,
        duration_min=duration_min,
        notes=notes,
        created_by_user_id=created_by_user_id,
    )
    if printed_at is not None:
        row.printed_at = printed_at
    db.add(row)
    await db.execute(
        update(Model).where(Model.id == model_id).values(print_count=Model.print_count + 1)
    )
    await db.commit()
    await db.refresh(row)
    await db.refresh(row, attribute_names=["material"])
    return PrintOut.from_model(row)


async def list_prints(
    db: AsyncSession, model_id: int, visible_printer_ids: set[int] | None = None
) -> list[PrintOut]:
    """Reverse-chronological by ``printed_at``, ``id`` desc as a tiebreak for
    rows sharing the same (possibly explicitly-set) ``printed_at``.

    ``visible_printer_ids`` (P3 Task 2) scopes out auto-archived runs on a
    printer the caller has no ACL row for -- never a manually-logged row
    (``source == manual``) and never a legacy auto row with no resolvable
    ``print_job_id``/printer (``visible is None`` here means "no printer
    behind this row to check", not "admin sees everything" -- that's
    ``visible_printer_ids=None`` at the call site, which skips the whole
    filter instead).
    """
    stmt = (
        select(Print)
        .outerjoin(PrintJob, PrintJob.id == Print.print_job_id)
        .where(Print.model_id == model_id)
        .options(selectinload(Print.material))
        .order_by(Print.printed_at.desc(), Print.id.desc())
    )
    if visible_printer_ids is not None:
        stmt = stmt.where(
            or_(
                Print.source == PrintSource.MANUAL,
                PrintJob.printer_id.is_(None),
                PrintJob.printer_id.in_(visible_printer_ids),
            )
        )
    rows = (await db.execute(stmt)).scalars().all()
    return [PrintOut.from_model(row) for row in rows]


async def _get_print_or_404(db: AsyncSession, print_id: int) -> Print:
    row = await db.get(Print, print_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"print {print_id} not found")
    return row


async def patch_print(
    db: AsyncSession, print_id: int, changes: dict[str, object], rates: CostRates
) -> PrintOut:
    """Apply ``changes`` (already ``exclude_unset``-filtered by the caller) --
    only the fields present in the request body change.
    """
    row = await _get_print_or_404(db, print_id)
    old_duration = row.duration_min
    if "material_id" in changes:
        await _check_material_id(db, changes["material_id"])
    for field in (
        "printed_at",
        "printer_name",
        "filament",
        "filament_g",
        "material_id",
        "result",
        "duration_min",
        "notes",
        "quality",
        "failure_reason",
    ):
        if field in changes:
            setattr(row, field, changes[field])
    if "filament_g" in changes or "duration_min" in changes:
        cost = price_run(
            row.filament_g, None if row.duration_min is None else row.duration_min * 60, rates
        )
        if "filament_g" in changes:
            row.filament_cost = cost.filament_cost
        if "duration_min" in changes:
            row.machine_cost = cost.machine_cost
            # Wear is a per-printer rate x time, and the printer's rate isn't
            # known here: scale the stored wear by the new/old duration.
            if row.wear_cost is not None:
                row.wear_cost = (
                    row.wear_cost * row.duration_min / old_duration
                    if old_duration and row.duration_min is not None
                    else None
                )
    if {"filament_g", "duration_min", "quality", "failure_reason"} & changes.keys():
        parts = [
            c
            for c in (row.filament_cost, row.machine_cost, row.energy_cost, row.wear_cost)
            if c is not None
        ]
        row.total_cost = sum(parts) if parts else None
    await db.commit()
    await db.refresh(row)
    await db.refresh(row, attribute_names=["material"])
    return PrintOut.from_model(row)


async def _dismiss_jobs(db: AsyncSession, job_ids: set[int]) -> None:
    """Tombstone the jobs of deleted runs. The rows stay (spool usage, queue
    entries and billing reference them) but ``archive_sweep`` skips them and
    the job lists hide them -- otherwise a deleted run is re-archived."""
    if job_ids:
        await db.execute(
            update(PrintJob)
            .where(PrintJob.id.in_(job_ids), PrintJob.archive_dismissed_at.is_(None))
            .values(archive_dismissed_at=func.now())
        )


async def _runs_of(db: AsyncSession, arc: PrintArchive) -> list[Print]:
    """Every run (``prints`` row) folded into ``arc``, oldest first. The
    newest run may be linked only by ``print_job_id``."""
    stmt = (
        select(Print)
        .where(or_(Print.archive_id == arc.id, Print.print_job_id == arc.print_job_id))
        .order_by(Print.printed_at, Print.id)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _decrement_counts(db: AsyncSession, rows: list[Print]) -> None:
    per_model: dict[int, int] = {}
    for r in rows:
        if r.model_id is not None:
            per_model[r.model_id] = per_model.get(r.model_id, 0) + 1
    for model_id, n in per_model.items():
        # `GREATEST(..., 0)` is a pure defensive floor for an already-drifted count.
        await db.execute(
            update(Model)
            .where(Model.id == model_id)
            .values(print_count=func.greatest(Model.print_count - n, 0))
        )


def _release(sync_session: SyncSession, settings: Settings, hashes: set[str]) -> list[Path]:
    return release_blobs(sync_session, settings, hashes)[1]


async def delete_archive_full(
    db: AsyncSession, arc: PrintArchive, settings: Settings
) -> list[Path]:
    """ "Delete run": drop the archive and every run folded into it (fixing
    ``print_count``) and release its blobs. The jobs are kept but tombstoned.
    The caller commits, then unlinks the returned paths."""
    hashes = blob_hashes(arc)
    runs = await _runs_of(db, arc)
    job_ids = {arc.print_job_id} | {r.print_job_id for r in runs if r.print_job_id is not None}
    await _dismiss_jobs(db, job_ids)
    await _decrement_counts(db, runs)
    for r in runs:
        await db.delete(r)
    await db.delete(arc)
    await db.flush()
    return await db.run_sync(lambda s: _release(s, settings, hashes))


_RUN_PHOTO = re.compile(r"^run(\d+)-(photo|timelapse)\.(.+)$")
_RESULT_OF_STATE = {
    PrintJobState.FINISHED: ArchiveResult.COMPLETED,
    PrintJobState.FAILED: ArchiveResult.FAILED,
    PrintJobState.CANCELED: ArchiveResult.CANCELED,
}
_VERDICT_COLS = (
    "user_verdict",
    "user_verdict_source",
    "verdict_at",
    "verdict_by_user_id",
    "result_override",
    "confirm_token",
    "confirm_token_used_at",
    "confirm_expires_at",
    "timelapse_frames",
    "timelapse_layers",
    "media_note",
)


def _drop_run_photos(
    arc: PrintArchive, n: int, *, restore: bool
) -> tuple[set[str], dict[str, str]]:
    """Take run ``n``'s ``runN-photo``/``runN-timelapse`` entries out of
    ``arc.photos`` and renumber later runs down by one. Returns (blobs freed,
    {kind: blob} taken out for ``restore``)."""
    kept: list[dict] = []
    freed: set[str] = set()
    restored: dict[str, str] = {}
    for ph in arc.photos or []:
        m = _RUN_PHOTO.match(ph.get("filename") or "")
        k = int(m.group(1)) if m else None
        if m is None or k < n:
            kept.append(ph)
        elif k == n:
            if restore and ph.get("blob"):
                restored[m.group(2)] = ph["blob"]
            elif ph.get("blob"):
                freed.add(ph["blob"])
        else:
            kept.append({**ph, "filename": f"run{k - 1}-{m.group(2)}.{m.group(3)}"})
    arc.photos = kept
    return freed, restored


async def _remove_run(
    db: AsyncSession, row: Print, arc: PrintArchive, runs: list[Print], settings: Settings
) -> list[Path]:
    """Delete one run of a multi-run archive (``runs`` has >1 entry)."""
    remaining = [r for r in runs if r.id != row.id]
    # Per-run photo names are only trustworthy while the ordinals line up.
    consistent = len(runs) == arc.run_count
    freed: set[str] = set()
    if row.print_job_id == arc.print_job_id:
        # The latest run: the previous one becomes the archive's current run.
        freed |= {h for h in (arc.snapshot_blob, arc.timelapse_blob) if h}
        arc.snapshot_blob = arc.timelapse_blob = None
        for col in _VERDICT_COLS:
            setattr(arc, col, None)
        arc.confirm_requested = False
        job = (
            await db.get(PrintJob, remaining[-1].print_job_id)
            if remaining[-1].print_job_id
            else None
        )
        if job is not None:
            arc.print_job_id = job.id
            arc.result = _RESULT_OF_STATE.get(job.state, arc.result)
            if job.started_at and job.finished_at:
                arc.duration_s = int((job.finished_at - job.started_at).total_seconds())
        if consistent:
            # The previous run's media was rolled into photos as run{N-1}-*.
            _, restored = _drop_run_photos(arc, len(remaining), restore=True)
            arc.snapshot_blob = restored.get("photo")
            arc.timelapse_blob = restored.get("timelapse")
    elif consistent:
        idx = next(i for i, r in enumerate(runs) if r.id == row.id)
        freed |= _drop_run_photos(arc, idx + 1, restore=False)[0]
    arc.run_count = max(arc.run_count - 1, 1)
    await db.delete(row)
    await db.flush()
    freed -= {h for h in (arc.snapshot_blob, arc.timelapse_blob) if h}
    return await db.run_sync(lambda s: _release(s, settings, freed))


async def delete_print(db: AsyncSession, print_id: int, settings: Settings) -> list[Path]:
    """Delete a run. Its job row is kept but tombstoned so ``archive_sweep``
    can't re-archive it. An archive it belonged to loses the run (repointing at
    the newest remaining one) or goes away with its last run. Commits; the
    caller unlinks the returned (released-blob) paths."""
    row = await _get_print_or_404(db, print_id)
    arc = None
    if row.archive_id is not None:
        match = PrintArchive.id == row.archive_id
    elif row.print_job_id is not None:
        match = PrintArchive.print_job_id == row.print_job_id
    else:
        match = None
    if match is not None:
        arc = (
            (await db.execute(select(PrintArchive).where(match).with_for_update()))
            .scalars()
            .first()
        )
    paths: list[Path] = []
    runs = await _runs_of(db, arc) if arc is not None else []
    if arc is not None and len(runs) > 1:
        await _dismiss_jobs(db, {row.print_job_id} if row.print_job_id else set())
        paths = await _remove_run(db, row, arc, runs, settings)
        await _decrement_counts(db, [row])
    elif arc is not None:
        paths = await delete_archive_full(db, arc, settings)
    else:
        await _dismiss_jobs(db, {row.print_job_id} if row.print_job_id else set())
        await _decrement_counts(db, [row])
        await db.delete(row)
    await db.commit()
    return paths


async def recount_print_counts(db: AsyncSession) -> None:
    """Self-heals any ``print_count`` drift (R13b Risk resolution 4) by
    recomputing every model's count from ``prints`` directly, in one
    statement -- called at the end of the scan job (``app.tasks.scan``) so
    drift (a pre-migration import, a bug, manual DB surgery) never lingers
    past the next scan. Models with zero prints are reset to 0 via the
    ``COALESCE`` fallback -- a plain correlated-subquery ``UPDATE`` would
    otherwise leave them untouched only when they already happen to be 0.
    """
    subquery = select(func.count(Print.id)).where(Print.model_id == Model.id).scalar_subquery()
    await db.execute(update(Model).values(print_count=func.coalesce(subquery, 0)))
    await db.commit()


def recount_print_counts_sync(session: SyncSession) -> None:
    """SYNC twin of :func:`recount_print_counts` for the Celery scan job
    (``app.tasks.scan``/``app.services.scanner``, which runs in the sync
    worker world -- see ``app.tasks.base``)."""
    subquery = select(func.count(Print.id)).where(Print.model_id == Model.id).scalar_subquery()
    session.execute(update(Model).values(print_count=func.coalesce(subquery, 0)))
    session.commit()
