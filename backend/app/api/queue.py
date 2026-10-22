"""Print queue: an ordered "models to print" worklist (Branch 4 Task 1),
extended by the P6 slicer/dispatch plan (Task 6) with per-file queue items.

``GET /queue``/``POST /queue``/``DELETE /queue/{id}``/``PATCH /queue/{id}``/
``POST /queue/{id}/retry``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_owner_or_admin, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.models.library import PrintQueueEntry
from app.schemas.queue import (
    QueueEnqueueIn,
    QueueEntryOut,
    QueueReorderIn,
    ReviewCountOut,
    ReviewIn,
)
from app.services import queue as queue_service

router = APIRouter(prefix="/queue", tags=["queue"])


@router.get("", response_model=list[QueueEntryOut])
async def list_queue(
    include_done: bool = False,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> list[QueueEntryOut]:
    return await queue_service.list_queue(db, settings, ctx, include_done=include_done)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=QueueEntryOut,
    dependencies=[Depends(require_perm("printers:print"))],
)
async def enqueue(
    payload: QueueEnqueueIn,
    response: Response,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> QueueEntryOut:
    """Append to the queue. A bare entry (no ``file_id``) is idempotent:
    re-adding an already-queued model answers 200 with the existing entry
    instead of erroring or duplicating it. A per-file entry (``file_id``
    set) is never idempotent -- it always creates a new row.
    """
    if payload.file_id is not None:
        return await queue_service.enqueue_file(db, settings, payload, ctx)

    if payload.model_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "model_id or file_id required")

    entry, created = await queue_service.enqueue_model(
        db, settings, payload.model_id, created_by_user_id=ctx.user.id
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return entry


@router.get(
    "/review-inbox",
    response_model=list[QueueEntryOut],
    dependencies=[Depends(require_perm("queue:review"))],
)
async def review_inbox(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> list[QueueEntryOut]:
    """Pending items ``ctx`` may act on as a reviewer (P7 Task 6).
    Declared before ``/{entry_id}`` so it's never shadowed by that path
    param route.
    """
    return await queue_service.review_inbox(db, settings, ctx)


@router.get(
    "/review-inbox/count",
    response_model=ReviewCountOut,
    dependencies=[Depends(require_perm("queue:review"))],
)
async def review_inbox_count(
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> ReviewCountOut:
    return ReviewCountOut(count=await queue_service.review_inbox_count(db, ctx))


@router.post(
    "/{entry_id}/retry",
    response_model=QueueEntryOut,
    dependencies=[Depends(require_perm("printers:print"))],
)
async def retry_entry(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> QueueEntryOut:
    entry = await db.get(PrintQueueEntry, entry_id)
    if entry is not None:  # unknown id falls through to the service's 404
        require_owner_or_admin(ctx, entry.created_by_user_id)
    return await queue_service.retry_entry(db, settings, entry_id)


@router.post(
    "/{entry_id}/review",
    response_model=QueueEntryOut,
    dependencies=[Depends(require_perm("queue:review"))],
)
async def review_entry(
    entry_id: int,
    payload: ReviewIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> QueueEntryOut:
    return await queue_service.review_entry(db, settings, entry_id, payload, ctx)


@router.delete(
    "/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("printers:print"))],
)
async def remove_entry(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> None:
    entry = await db.get(PrintQueueEntry, entry_id)
    if entry is not None:  # unknown id falls through to the service's 404
        require_owner_or_admin(ctx, entry.created_by_user_id)
    await queue_service.remove_entry(db, entry_id)


@router.patch(
    "/{entry_id}",
    response_model=list[QueueEntryOut],
    dependencies=[Depends(require_perm("printers:print"))],
)
async def move_entry(
    entry_id: int,
    payload: QueueReorderIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> list[QueueEntryOut]:
    """Reorder the queue, returning the whole updated (ordered) list -- the
    UI re-renders the whole thing on any reorder.
    """
    entry = await db.get(PrintQueueEntry, entry_id)
    if entry is not None:  # unknown id falls through to the service's 404
        require_owner_or_admin(ctx, entry.created_by_user_id)
    return await queue_service.move_entry(db, settings, entry_id, payload.position, ctx)
