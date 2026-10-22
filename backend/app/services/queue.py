"""Print queue: an ordered "models to print" worklist (Branch 4 Task 1),
extended by the P6 slicer/dispatch plan (Task 6) with per-file queue items
that carry a plate/printer/recipe and track their own slice-and-send
lifecycle through to the dispatcher (``app.services.dispatcher``).

Positions are a dense 1..n ranking over the WHOLE queue -- renumbered on
every insert/delete/reorder so the UI can always render (and PATCH back) a
contiguous list, rather than tracking gaps.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import AuthContext, visible_printer_ids
from app.config import Settings
from app.models.enums import BlobFormat, QueueState
from app.models.library import Blob, File, Model, PrintQueueEntry, Revision
from app.models.slicing import PrintRecipe
from app.schemas.queue import QueueEnqueueIn, QueueEntryOut
from app.services import library

# Formats the slicer sidecar can accept as input (mirrors
# `app.services.library._GLB_FORMATS` -- the mesh/cad formats the pipeline
# already knows how to hand a slicer-shaped tool). As-sliced (no-recipe)
# enqueues skip this entirely -- they must already be `GCODE_3MF`.
SLICEABLE_FORMATS = (
    BlobFormat.STL,
    BlobFormat.OBJ,
    BlobFormat.THREEMF,
    BlobFormat.STEP,
    BlobFormat.IGES,
)

# Manual retry (`POST /queue/{id}/retry`) is only ever valid from a
# terminal-ish failure state -- anything else (waiting/slicing/ready/
# dispatched/done) already has its own forward path.
RETRYABLE_STATES = (QueueState.FAILED, QueueState.HELD)


async def _ordered_entries(db: AsyncSession, limit: int | None = None) -> list[PrintQueueEntry]:
    stmt = select(PrintQueueEntry).order_by(PrintQueueEntry.position)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def _up_next_entries(
    db: AsyncSession, printer_ids: list[int], limit_per_printer: int
) -> list[PrintQueueEntry]:
    """P6 Task 8: up to ``limit_per_printer`` non-terminal entries PER
    TARGET, in one query regardless of how many printers are visible --
    a ``row_number()`` window partitioned by each entry's target printer
    (``printer_id``, falling back to ``assigned_printer_id`` once the
    dispatcher has picked one; NULL when neither is set, which the window
    function groups into a single shared "unassigned" partition). The
    caller splits the flat result back into per-printer/unassigned buckets
    by inspecting each ``QueueEntryOut``'s ``printer_id``/
    ``assigned_printer_id`` -- cheap in Python, and avoids N extra queries.
    """
    target = func.coalesce(PrintQueueEntry.printer_id, PrintQueueEntry.assigned_printer_id)
    rn = func.row_number().over(partition_by=target, order_by=PrintQueueEntry.position)
    inner = (
        select(PrintQueueEntry.id, rn.label("rn"))
        .where(PrintQueueEntry.state != QueueState.DONE)
        .where(or_(target.is_(None), target.in_(printer_ids)))
    ).subquery()
    ranked_ids = select(inner.c.id).where(inner.c.rn <= limit_per_printer)
    stmt = (
        select(PrintQueueEntry)
        .where(PrintQueueEntry.id.in_(ranked_ids))
        .order_by(PrintQueueEntry.position)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _to_out(
    db: AsyncSession, settings: Settings, entries: list[PrintQueueEntry]
) -> list[QueueEntryOut]:
    """Attach each entry's ``ModelSummary``, reusing
    ``library.build_model_summaries`` rather than duplicating the
    aggregate-then-assemble gallery logic, plus (Round 8 Task 3) each
    entry's ``printable_file`` -- resolved in ONE batch query via
    ``library.newest_printable_files`` across every distinct current
    revision in ``entries``, never one query per row. P6 Task 6 additionally
    batches each entry's requested ``file_id``'s display name and its
    ``recipe_id``'s name the same way.
    """
    if not entries:
        return []
    model_ids = [e.model_id for e in entries]
    stmt = (
        select(Model)
        .where(Model.id.in_(model_ids))
        .options(selectinload(Model.tags), selectinload(Model.category))
    )
    models_by_id = {m.id: m for m in (await db.execute(stmt)).scalars().unique().all()}
    summaries = await library.build_model_summaries(
        db, settings, [models_by_id[e.model_id] for e in entries]
    )
    summary_by_model_id = {s.id: s for s in summaries}

    revision_ids = [
        m.current_revision_id for m in models_by_id.values() if m.current_revision_id is not None
    ]
    printable_by_revision = await library.newest_printable_files(db, settings, revision_ids)

    file_ids = [e.file_id for e in entries if e.file_id is not None]
    files_by_id: dict[int, File] = {}
    if file_ids:
        files_by_id = {
            f.id: f
            for f in (await db.execute(select(File).where(File.id.in_(file_ids)))).scalars().all()
        }

    recipe_ids = [e.recipe_id for e in entries if e.recipe_id is not None]
    recipes_by_id: dict[int, PrintRecipe] = {}
    if recipe_ids:
        recipes_by_id = {
            r.id: r
            for r in (await db.execute(select(PrintRecipe).where(PrintRecipe.id.in_(recipe_ids))))
            .scalars()
            .all()
        }

    out = []
    for e in entries:
        file_row = files_by_id.get(e.file_id) if e.file_id is not None else None
        recipe_row = recipes_by_id.get(e.recipe_id) if e.recipe_id is not None else None
        out.append(
            QueueEntryOut(
                id=e.id,
                model_id=e.model_id,
                position=e.position,
                added_at=e.added_at,
                model=summary_by_model_id[e.model_id],
                printable_file=printable_by_revision.get(
                    models_by_id[e.model_id].current_revision_id
                ),
                printer_id=e.printer_id,
                recipe_id=e.recipe_id,
                recipe_name=recipe_row.name if recipe_row is not None else None,
                file_id=e.file_id,
                sliced_file_id=e.sliced_file_id,
                plate=e.plate,
                copies=e.copies,
                state=e.state,
                assigned_printer_id=e.assigned_printer_id,
                print_job_id=e.print_job_id,
                attempts=e.attempts,
                last_error=e.last_error,
                file_name=Path(file_row.rel_path).name if file_row is not None else None,
            )
        )
    return out


async def list_queue(
    db: AsyncSession,
    settings: Settings,
    ctx: AuthContext | None = None,
    *,
    include_done: bool = False,
    limit: int | None = None,
    printer_ids: list[int] | None = None,
    limit_per_printer: int | None = None,
) -> list[QueueEntryOut]:
    """Visibility: a non-admin only sees entries with no specific printer
    target (``printer_id is None`` -- still eligible for any compatible
    printer) or one in their own ACL (``visible_printer_ids``); an admin
    (or no ``ctx`` -- internal callers) sees everything. ``include_done``
    additionally hides (default) or shows terminal `done` entries.

    ``printer_ids``/``limit_per_printer`` (P6 Task 8) switch to the
    per-target "up next" shape used by the printers overview: up to
    ``limit_per_printer`` non-terminal entries per printer in
    ``printer_ids``, plus up to ``limit_per_printer`` unassigned ones, all
    via ``_up_next_entries``'s single windowed query. Mutually exclusive
    with ``limit``/``include_done``/``ctx`` filtering above.
    """
    if printer_ids is not None and limit_per_printer is not None:
        entries = await _up_next_entries(db, printer_ids, limit_per_printer)
        return await _to_out(db, settings, entries)

    entries = await _ordered_entries(db, limit)
    if ctx is not None:
        visible = visible_printer_ids(ctx)
        if visible is not None:
            entries = [
                e
                for e in entries
                if (e.printer_id is None or e.printer_id in visible)
                and (e.assigned_printer_id is None or e.assigned_printer_id in visible)
            ]
    if not include_done:
        entries = [e for e in entries if e.state != QueueState.DONE]
    return await _to_out(db, settings, entries)


async def _append_position(db: AsyncSession, *, front: bool) -> int:
    if front:
        others = await _ordered_entries(db)
        for other in others:
            other.position += 1
        await db.flush()
        return 1
    max_position = await db.scalar(select(func.max(PrintQueueEntry.position)))
    return (max_position or 0) + 1


async def enqueue_model(
    db: AsyncSession, settings: Settings, model_id: int, created_by_user_id: int | None = None
) -> tuple[QueueEntryOut, bool]:
    """Append ``model_id`` at the end of the queue. Idempotent: re-adding an
    already-queued BARE model returns its EXISTING entry (``created=False``)
    rather than erroring or duplicating it. 404s if the model doesn't exist.
    """
    await library.get_model_by_id(db, model_id)  # raises 404 if unknown

    existing = await db.scalar(
        select(PrintQueueEntry).where(
            PrintQueueEntry.model_id == model_id, PrintQueueEntry.file_id.is_(None)
        )
    )
    if existing is not None:
        out = await _to_out(db, settings, [existing])
        return out[0], False

    position = await _append_position(db, front=False)
    entry = PrintQueueEntry(
        model_id=model_id, position=position, created_by_user_id=created_by_user_id
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    out = await _to_out(db, settings, [entry])
    return out[0], True


async def enqueue_file(
    db: AsyncSession,
    settings: Settings,
    payload: QueueEnqueueIn,
    ctx: AuthContext,
) -> QueueEntryOut:
    """Append a per-file queue entry (P6 Task 6). Never idempotent -- a
    model can be queued more than once as long as each extra entry names a
    specific ``file_id`` (the DB's partial unique index only covers bare
    entries).
    """
    if payload.printer_id is not None:
        ctx.require_printer(payload.printer_id, "print")

    file = await db.get(File, payload.file_id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"file {payload.file_id} not found")
    revision = await db.get(Revision, file.revision_id)
    if revision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file's revision not found")
    blob = await db.get(Blob, file.blob_hash)

    sliced_file_id: int | None = None
    if payload.recipe_id is not None:
        recipe = await db.get(PrintRecipe, payload.recipe_id)
        if recipe is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"recipe {payload.recipe_id} not found")
        if blob is None or blob.format not in SLICEABLE_FORMATS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "file is not in a format the slicer can accept",
            )
        state = QueueState.WAITING
    else:
        if blob is None or blob.format != BlobFormat.GCODE_3MF:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "only sliced .gcode.3mf files can be queued without a recipe",
            )
        state = QueueState.READY
        sliced_file_id = file.id  # already sliced -- nothing for slice_file to do

    position = await _append_position(db, front=payload.front)
    entry = PrintQueueEntry(
        model_id=revision.model_id,
        file_id=file.id,
        sliced_file_id=sliced_file_id,
        printer_id=payload.printer_id,
        recipe_id=payload.recipe_id,
        plate=payload.plate,
        copies=payload.copies,
        state=state,
        position=position,
        ams_mapping=payload.ams_mapping,
        print_options=payload.options.model_dump() if payload.options is not None else None,
        created_by_user_id=ctx.user.id,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    out = await _to_out(db, settings, [entry])
    return out[0]


async def _get_entry_or_404(db: AsyncSession, entry_id: int) -> PrintQueueEntry:
    entry = await db.get(PrintQueueEntry, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"queue entry {entry_id} not found")
    return entry


async def retry_entry(db: AsyncSession, settings: Settings, entry_id: int) -> QueueEntryOut:
    """Manual retry: only valid from `failed`/`held` (409 otherwise). Resets
    the attempt counter and dispatch bookkeeping so the dispatcher's own
    eligibility filter (`waiting`/`ready`, `attempts < QUEUE_MAX_ATTEMPTS`)
    picks it back up on its next tick -- re-slicing first if it has a
    recipe and no sliced file yet, otherwise going straight back to
    `ready`.
    """
    entry = await _get_entry_or_404(db, entry_id)
    if entry.state not in RETRYABLE_STATES:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"cannot retry from state {entry.state.value}"
        )
    entry.state = (
        QueueState.WAITING
        if entry.recipe_id is not None and entry.sliced_file_id is None
        else QueueState.READY
    )
    entry.attempts = 0
    entry.last_error = None
    entry.assigned_printer_id = None
    entry.print_job_id = None
    await db.commit()
    await db.refresh(entry)
    out = await _to_out(db, settings, [entry])
    return out[0]


async def remove_entry(db: AsyncSession, entry_id: int) -> None:
    """Delete the entry, then compact the remaining positions back to a
    contiguous 1..n.
    """
    entry = await _get_entry_or_404(db, entry_id)
    await db.delete(entry)
    await db.flush()

    remaining = await _ordered_entries(db)
    for position, remaining_entry in enumerate(remaining, start=1):
        remaining_entry.position = position
    await db.commit()


async def move_entry(
    db: AsyncSession,
    settings: Settings,
    entry_id: int,
    position: int,
    ctx: AuthContext | None = None,
) -> list[QueueEntryOut]:
    """Move ``entry_id`` to 1-based ``position``, clamped to ``[1, n]``,
    shifting every other entry to make room. Returns the whole reordered
    queue (the UI re-renders the whole thing on any reorder) -- I8: through
    the same visibility filter (``ctx``) ``list_queue`` applies everywhere
    else, so a reorder never hands a non-admin back entries on printers
    outside their own ACL.
    """
    entry = await _get_entry_or_404(db, entry_id)
    entries = await _ordered_entries(db)

    others = [e for e in entries if e.id != entry_id]
    clamped = max(1, min(position, len(entries)))
    others.insert(clamped - 1, entry)

    for new_position, reordered_entry in enumerate(others, start=1):
        reordered_entry.position = new_position
    await db.commit()

    return await list_queue(db, settings, ctx)
