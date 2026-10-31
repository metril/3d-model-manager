"""T4 print batches (``queue:review``)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.schemas.batches import BatchDispatchIn, BatchIn, BatchOut, BatchPatch
from app.services import batches as svc

router = APIRouter(
    prefix="/batches", tags=["batches"], dependencies=[Depends(require_perm("queue:review"))]
)


@router.get("", response_model=list[BatchOut])
async def list_batches(db: AsyncSession = Depends(get_db)) -> list[BatchOut]:
    return await svc.list_batches(db)


@router.post("", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
async def create_batch(
    payload: BatchIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> BatchOut:
    return await svc.create_batch(db, settings, payload, ctx)


@router.get("/{batch_id}", response_model=BatchOut)
async def get_batch(batch_id: int, db: AsyncSession = Depends(get_db)) -> BatchOut:
    return (await svc.to_out(db, [await svc.get_or_404(db, batch_id)]))[0]


@router.patch("/{batch_id}", response_model=BatchOut)
async def patch_batch(
    batch_id: int, payload: BatchPatch, db: AsyncSession = Depends(get_db)
) -> BatchOut:
    return await svc.patch_batch(db, batch_id, payload)


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_batch(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> None:
    await svc.delete_batch(db, batch_id, ctx)


@router.post("/{batch_id}/dispatch", response_model=BatchOut)
async def dispatch_batch(
    batch_id: int,
    payload: BatchDispatchIn | None = None,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> BatchOut:
    return await svc.dispatch_batch(
        db, settings, batch_id, ctx, force=payload.force if payload else False
    )


@router.post("/{batch_id}/ungroup", response_model=BatchOut)
async def ungroup_batch(batch_id: int, db: AsyncSession = Depends(get_db)) -> BatchOut:
    return await svc.ungroup_batch(db, batch_id)
