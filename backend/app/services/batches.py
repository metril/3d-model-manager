"""T4 print batches: create, dispatch (fan out ``quantity`` queue entries
through ``queue.enqueue_file`` so Fanout/review/cost reservation still
apply), ungroup, cancel. Status ``done`` is derived from the entries."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext
from app.config import Settings
from app.models.audit_log import AuditLog
from app.models.enums import QueueState
from app.models.library import File, Model, PrintBatch, PrintQueueEntry, Revision
from app.schemas.batches import BatchIn, BatchOut, BatchPatch, BatchProgress
from app.schemas.queue import QueueEnqueueIn
from app.services import library
from app.services import queue as queue_service

_SCHEDULING = (
    "scheduled_at",
    "manual_start",
    "require_previous_success",
    "auto_off_after",
    "preheat_override",
    "preheat_chamber_target",
)
_TERMINAL = (QueueState.DONE, QueueState.FAILED, QueueState.SKIPPED)
# Entries a cancel may still remove (never ran / not in flight).
_REMOVABLE = (QueueState.WAITING, QueueState.READY, QueueState.HELD, QueueState.SKIPPED)


async def get_or_404(db: AsyncSession, batch_id: int) -> PrintBatch:
    batch = await db.get(PrintBatch, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"batch {batch_id} not found")
    return batch


async def _progress(db: AsyncSession, ids: list[int]) -> dict[int, BatchProgress]:
    out = {i: BatchProgress() for i in ids}
    if not ids:
        return out
    rows = await db.execute(
        select(PrintQueueEntry.batch_id, PrintQueueEntry.state, func.count())
        .where(PrintQueueEntry.batch_id.in_(ids))
        .group_by(PrintQueueEntry.batch_id, PrintQueueEntry.state)
    )
    for bid, state, n in rows:
        p = out[bid]
        p.total += n
        if state == QueueState.DONE:
            p.done += n
        elif state == QueueState.FAILED:
            p.failed += n
        elif state == QueueState.SKIPPED:
            p.skipped += n
        else:
            p.waiting += n
    return out


async def to_out(db: AsyncSession, batches: list[PrintBatch]) -> list[BatchOut]:
    progress = await _progress(db, [b.id for b in batches])
    from pathlib import Path

    model_ids = {b.model_id for b in batches if b.model_id is not None}
    file_ids = {b.file_id for b in batches if b.file_id is not None}
    names = (
        dict((await db.execute(select(Model.id, Model.name).where(Model.id.in_(model_ids)))).all())
        if model_ids
        else {}
    )
    files = (
        dict((await db.execute(select(File.id, File.rel_path).where(File.id.in_(file_ids)))).all())
        if file_ids
        else {}
    )
    result = []
    for b in batches:
        o = b.options or {}
        p = progress[b.id]
        st = b.status
        if st == "active" and p.waiting == 0:
            st = "done"  # all terminal, or ungrouped (nothing left to track)
        result.append(
            BatchOut(
                id=b.id,
                name=b.name,
                model_id=b.model_id,
                file_id=b.file_id,
                recipe_id=b.recipe_id,
                printer_id=b.printer_id,
                quantity=b.quantity,
                status=st,
                due_date=b.due_date,
                notes=b.notes,
                plate_targets=b.plate_targets or {},
                cost_center_id=b.cost_center_id,
                created_by_user_id=b.created_by_user_id,
                created_at=b.created_at,
                model_name=names.get(b.model_id),
                file_name=Path(files[b.file_id]).name if b.file_id in files else None,
                ams_mapping=o.get("ams_mapping"),
                options=o.get("print_options"),
                copies_per_entry=o.get("copies_per_entry", 1),
                progress=p,
            )
        )
    return result


async def list_batches(db: AsyncSession) -> list[BatchOut]:
    rows = (await db.execute(select(PrintBatch).order_by(PrintBatch.id.desc()))).scalars().all()
    return await to_out(db, list(rows))


def _check_plate_sum(quantity: int, plate_targets: dict[str, int]) -> None:
    if sum(plate_targets.values()) > quantity:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "plate_targets exceed the batch quantity"
        )


async def create_batch(
    db: AsyncSession, settings: Settings, payload: BatchIn, ctx: AuthContext
) -> BatchOut:
    if payload.file_id is None and payload.model_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "model_id or file_id required")
    model_id = payload.model_id
    if payload.file_id is not None:
        file = await db.get(File, payload.file_id)
        if file is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"file {payload.file_id} not found")
        rev = await db.get(Revision, file.revision_id)
        model_id = rev.model_id if rev is not None else model_id
    elif await db.get(Model, payload.model_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"model {payload.model_id} not found")
    _check_plate_sum(payload.quantity, payload.plate_targets)
    batch = PrintBatch(
        name=payload.name,
        model_id=model_id,
        file_id=payload.file_id,
        recipe_id=payload.recipe_id,
        printer_id=payload.printer_id,
        quantity=payload.quantity,
        status="draft",
        due_date=payload.due_date,
        notes=payload.notes,
        plate_targets=payload.plate_targets,
        cost_center_id=payload.cost_center_id,
        options={
            **{
                k: (v.isoformat() if k == "scheduled_at" and v is not None else v)
                for k in _SCHEDULING
                for v in [getattr(payload, k)]
            },
            "ams_mapping": payload.ams_mapping,
            "print_options": payload.options.model_dump() if payload.options else None,
            "copies_per_entry": payload.copies_per_entry,
        },
        created_by_user_id=ctx.user.id,
    )
    db.add(batch)
    await db.commit()
    await db.refresh(batch)
    return (await to_out(db, [batch]))[0]


async def patch_batch(db: AsyncSession, batch_id: int, payload: BatchPatch) -> BatchOut:
    batch = await get_or_404(db, batch_id)
    sent = payload.model_fields_set
    structural = {"quantity", "plate_targets", "recipe_id", "printer_id"} & sent
    if structural and batch.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "batch already dispatched")
    for name in sent:
        value = getattr(payload, name)
        if name in ("name", "quantity", "notes", "plate_targets") and value is None:
            continue
        setattr(batch, name, value)
    _check_plate_sum(batch.quantity, batch.plate_targets or {})
    await db.commit()
    await db.refresh(batch)
    return (await to_out(db, [batch]))[0]


def _plate_sequence(batch: PrintBatch) -> list[int | None]:
    seq: list[int | None] = []
    for plate, qty in sorted((batch.plate_targets or {}).items(), key=lambda kv: int(kv[0])):
        seq.extend([int(plate)] * qty)
    seq.extend([None] * (batch.quantity - len(seq)))
    return seq[: batch.quantity]


async def _resolve_file_id(db: AsyncSession, settings: Settings, batch: PrintBatch) -> int:
    if batch.file_id is not None:
        return batch.file_id
    model = await db.get(Model, batch.model_id) if batch.model_id is not None else None
    if model is None or model.current_revision_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "batch has no printable file")
    found = await library.newest_printable_files(db, settings, [model.current_revision_id])
    printable = found.get(model.current_revision_id)
    if printable is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "batch has no printable file")
    return printable.id


async def dispatch_batch(
    db: AsyncSession, settings: Settings, batch_id: int, ctx: AuthContext
) -> BatchOut:
    batch = await get_or_404(db, batch_id)
    if batch.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "batch already dispatched")
    file_id = await _resolve_file_id(db, settings, batch)
    opts = batch.options or {}
    created: list[int] = []
    try:
        for plate in _plate_sequence(batch):
            entry = await queue_service.enqueue_file(
                db,
                settings,
                QueueEnqueueIn(
                    file_id=file_id,
                    plate=plate,
                    printer_id=batch.printer_id,
                    recipe_id=batch.recipe_id,
                    copies=opts.get("copies_per_entry", 1),
                    ams_mapping=opts.get("ams_mapping"),
                    options=opts.get("print_options"),
                    cost_center_id=batch.cost_center_id,
                    batch_id=batch.id,
                    **{k: opts[k] for k in _SCHEDULING if opts.get(k) is not None},
                ),
                ctx,
            )
            created.append(entry.id)
    except HTTPException:
        await db.rollback()
        for entry_id in created:
            await queue_service.remove_entry(db, entry_id)
        raise
    batch = await get_or_404(db, batch_id)
    batch.status = "active"
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            action="batch_dispatch",
            payload={"batch_id": batch.id, "entries": created},
        )
    )
    await db.commit()
    await db.refresh(batch)
    return (await to_out(db, [batch]))[0]


async def ungroup_batch(db: AsyncSession, batch_id: int) -> BatchOut:
    batch = await get_or_404(db, batch_id)
    entries = (
        (await db.execute(select(PrintQueueEntry).where(PrintQueueEntry.batch_id == batch_id)))
        .scalars()
        .all()
    )
    for e in entries:
        e.batch_id = None
    await db.commit()
    await db.refresh(batch)
    return (await to_out(db, [batch]))[0]


async def delete_batch(db: AsyncSession, batch_id: int, ctx: AuthContext) -> None:
    """First delete cancels (removes non-dispatched entries, keeps the row as
    ``cancelled``); deleting an already-cancelled batch removes the row."""
    batch = await get_or_404(db, batch_id)
    removed: list[int] = []
    if batch.status == "cancelled":
        await db.delete(batch)
    else:
        entries = (
            (
                await db.execute(
                    select(PrintQueueEntry).where(
                        PrintQueueEntry.batch_id == batch_id, PrintQueueEntry.state.in_(_REMOVABLE)
                    )
                )
            )
            .scalars()
            .all()
        )
        for e in entries:
            await queue_service.remove_entry(db, e.id)
            removed.append(e.id)
        batch = await get_or_404(db, batch_id)
        batch.status = "cancelled"
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            action="batch_delete",
            payload={"batch_id": batch_id, "removed_entries": removed},
        )
    )
    await db.commit()
