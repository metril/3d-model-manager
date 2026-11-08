"""Per-model print history (Branch 5 Task 1): a user-entered log of print
attempts, distinct from the ``print_queue`` "to print" worklist
(``app.services.queue``) and ``print_jobs``' live send-to-printer telemetry
(M4). The API layer (``app.api.prints``) already resolves/404s the parent
model for the two model-scoped routes, so ``model_id`` here is trusted.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import PrintJobState, PrintResult, PrintSource
from app.models.library import Material, Model, Print
from app.models.printing import PrintArchive, PrintJob
from app.schemas.prints import PrintOut
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


async def purge_finished_jobs(db: AsyncSession, job_ids: set[int]) -> None:
    """Drop the terminal ``print_jobs`` rows behind deleted runs.

    Without this ``archive_sweep`` sees a terminal job with no archive and
    re-archives it, resurrecting the run. A job an archive still points at is
    kept. Spool usage / queue / detections keep their rows (FK SET NULL);
    only the per-job Spoolman push marker cascades.
    """
    if not job_ids:
        return
    await db.execute(
        delete(PrintJob).where(
            PrintJob.id.in_(job_ids),
            PrintJob.state.in_([s.value for s in _TERMINAL]),
            ~select(PrintArchive.id).where(PrintArchive.print_job_id == PrintJob.id).exists(),
        )
    )


_TERMINAL = (PrintJobState.FINISHED, PrintJobState.FAILED, PrintJobState.CANCELED)


async def delete_runs_of_archive(db: AsyncSession, archive_id: int, latest_job_id: int) -> set[int]:
    """Delete every run (``prints`` row) folded into an archive and fix each
    model's ``print_count``. Returns the runs' job ids; the caller deletes the
    archive, flushes, then calls :func:`purge_finished_jobs`."""
    rows = (
        (
            await db.execute(
                select(Print).where(
                    or_(Print.archive_id == archive_id, Print.print_job_id == latest_job_id)
                )
            )
        )
        .scalars()
        .all()
    )
    job_ids = {latest_job_id} | {r.print_job_id for r in rows if r.print_job_id is not None}
    per_model: dict[int, int] = {}
    for r in rows:
        if r.model_id is not None:
            per_model[r.model_id] = per_model.get(r.model_id, 0) + 1
        await db.delete(r)
    for model_id, n in per_model.items():
        await db.execute(
            update(Model)
            .where(Model.id == model_id)
            .values(print_count=func.greatest(Model.print_count - n, 0))
        )
    return job_ids


async def delete_print(db: AsyncSession, print_id: int) -> None:
    row = await _get_print_or_404(db, print_id)
    model_id = row.model_id
    job_id = row.print_job_id
    await db.delete(row)
    if model_id is not None:
        # `GREATEST(..., 0)` is a pure defensive floor -- normal operation never
        # needs it, since every print row was counted exactly once by
        # `create_print` above; it only guards against a print_count that was
        # already out of sync (pre-migration data, a bug) going negative.
        await db.execute(
            update(Model)
            .where(Model.id == model_id)
            .values(print_count=func.greatest(Model.print_count - 1, 0))
        )
    if job_id is not None:
        await db.flush()
        await purge_finished_jobs(db, {job_id})
    await db.commit()


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
