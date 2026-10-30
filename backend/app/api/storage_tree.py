"""`GET /storage/tree` (R13b): folder-browse view of the storage layout."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_perm
from app.config import Settings, get_settings
from app.db import get_db
from app.schemas.jobs import JobOut
from app.schemas.storage_tree import FolderMoveIn, StorageTreeOut
from app.services import jobs as jobs_service
from app.services import storage_tree as storage_tree_service
from app.tasks.relocate import move_storage_folder

router = APIRouter(prefix="/storage", tags=["storage"])


@router.get("/tree", response_model=StorageTreeOut)
async def get_storage_tree(
    path: str = "",
    limit: int = Query(storage_tree_service.DEFAULT_FILE_LIMIT, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StorageTreeOut:
    return await storage_tree_service.get_storage_tree(
        db, settings, path, limit=limit, offset=offset
    )


@router.post(
    "/folders/move", response_model=JobOut, dependencies=[Depends(require_perm("library:write"))]
)
async def move_folder(payload: FolderMoveIn, db: AsyncSession = Depends(get_db)) -> JobOut:
    """Move a folder (every file under it) into another folder of the same
    revision. Runs as a job: each file is copied to its new key, hash-verified
    and committed before the old object is deleted (see ``move_storage_folder``)."""
    try:
        new_prefix = await storage_tree_service.plan_folder_move(db, payload.src, payload.dest)
    except storage_tree_service.FolderMoveError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    job = await jobs_service.create_job(
        db, id=uuid.uuid4(), type="move_storage_folder", subject_type="folder", subject_id=None
    )
    move_storage_folder.apply_async(
        args=[str(job.id), payload.src.strip("/"), new_prefix], task_id=str(job.id)
    )
    await db.refresh(job)
    return JobOut.from_model(job)
