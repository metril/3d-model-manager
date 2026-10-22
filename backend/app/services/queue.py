"""Print queue: an ordered "models to print" worklist (Branch 4 Task 1),
extended by the P6 slicer/dispatch plan (Task 6) with per-file queue items
that carry a plate/printer/recipe and track their own slice-and-send
lifecycle through to the dispatcher (``app.services.dispatcher``).

Positions are a dense 1..n ranking over the WHOLE queue -- renumbered on
every insert/delete/reorder so the UI can always render (and PATCH back) a
contiguous list, rather than tracking gaps.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from app.api.deps import AuthContext, visible_printer_ids
from app.config import Settings
from app.models import CostCenter, GroupMember
from app.models.enums import BlobFormat, QueueState, ReviewState
from app.models.library import Blob, File, Model, PrintQueueEntry, Revision
from app.models.slicing import PrintRecipe
from app.schemas.queue import QueueEnqueueIn, QueueEntryOut, ReviewIn
from app.services import billing, library, review
from app.services.app_config import get_app_config
from app.services.print_plan import estimate_item_sync

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
                cost_center_id=e.cost_center_id,
                review_state=e.review_state,
                reviewed_by=e.reviewed_by,
                reviewed_at=e.reviewed_at,
                review_note=e.review_note,
                reserved_cost=float(e.reserved_cost) if e.reserved_cost is not None else None,
                hold_reason=e.hold_reason,
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
    await db.flush()

    cfg = await get_app_config(db, settings)

    def _review_and_billing(sync_session) -> None:
        current = sync_session.get(PrintQueueEntry, entry.id)
        targets = review.item_targets(sync_session, current)
        current.review_state = (
            ReviewState.PENDING if review.needs_review(ctx, targets) else ReviewState.NONE
        )
        if not cfg.billing_enabled:
            return
        if payload.cost_center_id is not None:
            cc = sync_session.get(CostCenter, payload.cost_center_id)
            if cc is None:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, f"cost center {payload.cost_center_id} not found"
                )
            is_member = cc.owner_user_id == ctx.user.id or (
                cc.group_id is not None
                and sync_session.scalar(
                    select(GroupMember).where(
                        GroupMember.group_id == cc.group_id, GroupMember.user_id == ctx.user.id
                    )
                )
                is not None
            )
            if not is_member:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, "cost center is not usable by this user"
                )
            if not cc.active:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, "cost center is inactive"
                )
        else:
            cc = billing.ensure_private_center(sync_session, ctx.user.id)
        current.cost_center_id = cc.id
        if current.sliced_file_id is not None:
            est = estimate_item_sync(sync_session, settings, current)
            billing.reserve(
                sync_session,
                cfg,
                current,
                Decimal(str(est)) if est is not None else None,
                user_id=ctx.user.id,
            )

    await db.run_sync(_review_and_billing)

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
    if entry.hold_reason == billing.HOLD_OVER_BUDGET:
        # P7: an over-budget hold sits independent of `state`/`attempts` --
        # retrying it just re-attempts the reservation against (possibly)
        # newly-freed budget, leaving everything else untouched.
        cfg = await get_app_config(db, settings)

        def _retry_reserve(sync_session) -> None:
            current = sync_session.get(PrintQueueEntry, entry.id)
            billing.reserve(
                sync_session,
                cfg,
                current,
                current.reserved_cost,
                user_id=current.created_by_user_id,
            )

        await db.run_sync(_retry_reserve)
        await db.commit()
        await db.refresh(entry)
        out = await _to_out(db, settings, [entry])
        return out[0]
    if entry.state not in RETRYABLE_STATES:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"cannot retry from state {entry.state.value}"
        )

    # C1: a prior attempt that already posted a `charge` (settled via the
    # normal archive path, e.g. a partial failure) can never be billed
    # again on THIS id -- `billing.append`'s own (queue_id, kind)
    # idempotency index would silently drop the retried print's charge.
    # Clone into a fresh queue row (new id -> fresh reservation keys) and
    # retire the old one instead of resetting it in place.
    already_charged = await db.run_sync(lambda s: billing.charged(s, entry.id))
    if already_charged:
        new_state = (
            QueueState.WAITING
            if entry.recipe_id is not None and entry.sliced_file_id is None
            else QueueState.READY
        )
        position = await _append_position(db, front=False)
        clone = PrintQueueEntry(
            model_id=entry.model_id,
            file_id=entry.file_id,
            sliced_file_id=entry.sliced_file_id,
            printer_id=entry.printer_id,
            recipe_id=entry.recipe_id,
            plate=entry.plate,
            copies=entry.copies,
            state=new_state,
            position=position,
            ams_mapping=entry.ams_mapping,
            print_options=entry.print_options,
            created_by_user_id=entry.created_by_user_id,
            cost_center_id=entry.cost_center_id,
        )
        db.add(clone)
        entry.state = QueueState.DONE
        await db.flush()

        cfg = await get_app_config(db, settings)

        def _reserve_clone(sync_session) -> None:
            current = sync_session.get(PrintQueueEntry, clone.id)
            if current.cost_center_id is None or current.sliced_file_id is None:
                return
            est = estimate_item_sync(sync_session, settings, current)
            billing.reserve(
                sync_session,
                cfg,
                current,
                Decimal(str(est)) if est is not None else None,
                user_id=current.created_by_user_id,
            )

        await db.run_sync(_reserve_clone)
        await db.commit()
        await db.refresh(clone)
        out = await _to_out(db, settings, [clone])
        return out[0]

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

    C2: a DISPATCHED or SLICING entry is already live against a printer or
    the slicer sidecar -- deleting it here would silently abandon that
    in-flight work without ever settling its reservation (the dispatcher/
    archive path is the only thing that calls `billing.release`/`charge`
    for it). 409 instead; the reservation stays open and settles normally
    once the print finishes (or fails) through the usual archive path.
    """
    entry = await _get_entry_or_404(db, entry_id)
    if entry.state in (QueueState.DISPATCHED, QueueState.SLICING):
        raise HTTPException(status.HTTP_409_CONFLICT, "cancel the print first")

    def _release(sync_session) -> None:
        current = sync_session.get(PrintQueueEntry, entry.id)
        billing.release(sync_session, current)

    await db.run_sync(_release)

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


# ---------------------------------------------------------------------------
# P7 review/billing plan Task 6: review inbox + approve/reject.
# ---------------------------------------------------------------------------


def _reviewable_ids_sync(s: Session, ctx: AuthContext) -> list[int]:
    """Every ``pending`` entry ``ctx`` is allowed to act on
    (``app.services.review.can_review``), oldest position first."""
    items = (
        s.execute(
            select(PrintQueueEntry)
            .where(PrintQueueEntry.review_state == ReviewState.PENDING)
            .order_by(PrintQueueEntry.position)
        )
        .scalars()
        .all()
    )
    return [item.id for item in items if review.can_review(ctx, s, item)]


async def review_inbox(
    db: AsyncSession, settings: Settings, ctx: AuthContext
) -> list[QueueEntryOut]:
    ids = await db.run_sync(_reviewable_ids_sync, ctx)
    if not ids:
        return []
    entries = list(
        (
            await db.execute(
                select(PrintQueueEntry)
                .where(PrintQueueEntry.id.in_(ids))
                .order_by(PrintQueueEntry.position)
            )
        )
        .scalars()
        .all()
    )
    return await _to_out(db, settings, entries)


async def review_inbox_count(db: AsyncSession, ctx: AuthContext) -> int:
    ids = await db.run_sync(_reviewable_ids_sync, ctx)
    return len(ids)


async def review_entry(
    db: AsyncSession,
    settings: Settings,
    entry_id: int,
    payload: ReviewIn,
    ctx: AuthContext,
) -> QueueEntryOut:
    """Approve/reject a pending review. 404 unknown; 403 unless
    ``can_review``; 409 unless ``review_state == pending``. A reject also
    releases the item's open reservation (``billing.release``) -- one
    commit covers the state transition and the release together.
    """
    entry = await _get_entry_or_404(db, entry_id)

    def _apply(s: Session) -> None:
        current = s.get(PrintQueueEntry, entry.id)
        if not review.can_review(ctx, s, current):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not a reviewer for this item")
        if current.review_state != ReviewState.PENDING:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"cannot review from state {current.review_state.value}",
            )
        current.review_state = (
            ReviewState.APPROVED if payload.action == "approve" else ReviewState.REJECTED
        )
        current.reviewed_by = ctx.user.id
        current.reviewed_at = datetime.now(UTC)
        current.review_note = payload.note
        if payload.action == "reject":
            billing.release(s, current, user_id=ctx.user.id)

    await db.run_sync(_apply)
    await db.commit()
    await db.refresh(entry)
    out = await _to_out(db, settings, [entry])
    return out[0]
