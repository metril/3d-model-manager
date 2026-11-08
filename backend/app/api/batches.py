"""T4 print batches. Anyone who can print may use their OWN batches;
``queue:review`` (or admin) sees and manages all of them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.schemas.batches import BatchDispatchIn, BatchIn, BatchOut, BatchPatch
from app.services import batches as svc

router = APIRouter(
    prefix="/batches", tags=["batches"], dependencies=[Depends(require_perm("printers:print"))]
)


def _sees_all(ctx: AuthContext) -> bool:
    return ctx.is_admin or ctx.can("queue:review")


async def _own_or_404(db: AsyncSession, ctx: AuthContext, batch_id: int):
    """The batch, or 404 when it is someone else's and ``ctx`` is no reviewer."""
    batch = await svc.get_or_404(db, batch_id)
    if not _sees_all(ctx) and batch.created_by_user_id != ctx.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"batch {batch_id} not found")
    return batch


@router.get("", response_model=list[BatchOut])
async def list_batches(
    db: AsyncSession = Depends(get_db), ctx: AuthContext = Depends(require_session)
) -> list[BatchOut]:
    out = await svc.list_batches(db)
    return out if _sees_all(ctx) else [b for b in out if b.created_by_user_id == ctx.user.id]


@router.post("", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
async def create_batch(
    payload: BatchIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> BatchOut:
    return await svc.create_batch(db, settings, payload, ctx)


@router.get("/{batch_id}", response_model=BatchOut)
async def get_batch(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> BatchOut:
    return (await svc.to_out(db, [await _own_or_404(db, ctx, batch_id)]))[0]


@router.patch("/{batch_id}", response_model=BatchOut)
async def patch_batch(
    batch_id: int,
    payload: BatchPatch,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> BatchOut:
    await _own_or_404(db, ctx, batch_id)
    return await svc.patch_batch(db, batch_id, payload)


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_batch(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> None:
    await _own_or_404(db, ctx, batch_id)
    await svc.delete_batch(db, batch_id, ctx)


@router.post("/{batch_id}/dispatch", response_model=BatchOut)
async def dispatch_batch(
    batch_id: int,
    payload: BatchDispatchIn | None = None,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> BatchOut:
    await _own_or_404(db, ctx, batch_id)
    return await svc.dispatch_batch(
        db, settings, batch_id, ctx, force=payload.force if payload else False
    )


@router.post("/{batch_id}/ungroup", response_model=BatchOut)
async def ungroup_batch(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> BatchOut:
    await _own_or_404(db, ctx, batch_id)
    return await svc.ungroup_batch(db, batch_id)
