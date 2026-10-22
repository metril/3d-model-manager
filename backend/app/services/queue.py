"""Print queue: an ordered "models to print" worklist (Branch 4 Task 1).

Positions are a dense 1..n ranking over the WHOLE queue -- renumbered on
every insert/delete/reorder so the UI can always render (and PATCH back) a
contiguous list, rather than tracking gaps.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models.library import Model, PrintQueueEntry
from app.schemas.queue import QueueEntryOut
from app.services import library


async def _ordered_entries(db: AsyncSession) -> list[PrintQueueEntry]:
    stmt = select(PrintQueueEntry).order_by(PrintQueueEntry.position)
    return list((await db.execute(stmt)).scalars().all())


async def _to_out(
    db: AsyncSession, settings: Settings, entries: list[PrintQueueEntry]
) -> list[QueueEntryOut]:
    """Attach each entry's ``ModelSummary``, reusing
    ``library.build_model_summaries`` rather than duplicating the
    aggregate-then-assemble gallery logic, plus (Round 8 Task 3) each
    entry's ``printable_file`` -- resolved in ONE batch query via
    ``library.newest_printable_files`` across every distinct current
    revision in ``entries``, never one query per row.
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

    return [
        QueueEntryOut(
            id=e.id,
            model_id=e.model_id,
            position=e.position,
            added_at=e.added_at,
            model=summary_by_model_id[e.model_id],
            printable_file=printable_by_revision.get(models_by_id[e.model_id].current_revision_id),
        )
        for e in entries
    ]


async def list_queue(db: AsyncSession, settings: Settings) -> list[QueueEntryOut]:
    return await _to_out(db, settings, await _ordered_entries(db))


async def enqueue_model(
    db: AsyncSession, settings: Settings, model_id: int, created_by_user_id: int | None = None
) -> tuple[QueueEntryOut, bool]:
    """Append ``model_id`` at the end of the queue. Idempotent: re-adding an
    already-queued model returns its EXISTING entry (``created=False``)
    rather than erroring or duplicating it. 404s if the model doesn't exist.
    """
    await library.get_model_by_id(db, model_id)  # raises 404 if unknown

    existing = await db.scalar(select(PrintQueueEntry).where(PrintQueueEntry.model_id == model_id))
    if existing is not None:
        out = await _to_out(db, settings, [existing])
        return out[0], False

    max_position = await db.scalar(select(func.max(PrintQueueEntry.position)))
    entry = PrintQueueEntry(
        model_id=model_id, position=(max_position or 0) + 1, created_by_user_id=created_by_user_id
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    out = await _to_out(db, settings, [entry])
    return out[0], True


async def _get_entry_or_404(db: AsyncSession, entry_id: int) -> PrintQueueEntry:
    entry = await db.get(PrintQueueEntry, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"queue entry {entry_id} not found")
    return entry


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
    db: AsyncSession, settings: Settings, entry_id: int, position: int
) -> list[QueueEntryOut]:
    """Move ``entry_id`` to 1-based ``position``, clamped to ``[1, n]``,
    shifting every other entry to make room. Returns the whole reordered
    queue (the UI re-renders the whole list on any reorder).
    """
    entry = await _get_entry_or_404(db, entry_id)
    entries = await _ordered_entries(db)

    others = [e for e in entries if e.id != entry_id]
    clamped = max(1, min(position, len(entries)))
    others.insert(clamped - 1, entry)

    for new_position, reordered_entry in enumerate(others, start=1):
        reordered_entry.position = new_position
    await db.commit()

    return await list_queue(db, settings)
