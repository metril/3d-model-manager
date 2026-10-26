"""Library trash API: list / restore / purge trashed models."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_storage_backend, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.schemas.trash import TrashCountOut, TrashIdsIn, TrashListOut
from app.services import trash
from app.storage.base import StorageBackend

router = APIRouter(prefix="/trash", tags=["trash"])
_write = [Depends(require_perm("library:write"))]


@router.get("", response_model=TrashListOut, dependencies=_write)
async def list_trash(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> TrashListOut:
    return await trash.list_trash(db, settings, ctx)


@router.post("/restore", response_model=TrashCountOut, dependencies=_write)
async def restore(
    payload: TrashIdsIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> TrashCountOut:
    return TrashCountOut(count=await trash.restore(db, ctx, payload.ids))


@router.post("/purge", response_model=TrashCountOut, dependencies=_write)
async def purge(
    payload: TrashIdsIn,
    db: AsyncSession = Depends(get_db),
    backend: StorageBackend = Depends(get_storage_backend),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> TrashCountOut:
    return TrashCountOut(count=await trash.purge(db, backend, settings, ctx, payload.ids))


@router.post("/purge-all", response_model=TrashCountOut, dependencies=_write)
async def purge_all(
    db: AsyncSession = Depends(get_db),
    backend: StorageBackend = Depends(get_storage_backend),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> TrashCountOut:
    """Admins empty the whole trash; other users only their own items."""
    return TrashCountOut(count=await trash.purge_all(db, backend, settings, ctx))
