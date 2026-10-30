"""`GET /storage/tree` (R13b): folder-browse view of the storage layout."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_owner_or_admin, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.models import File, Job, Model
from app.schemas.jobs import JobOut
from app.schemas.storage_tree import FolderMoveIn, StorageTreeOut
from app.services import jobs as jobs_service
from app.services import storage_tree as storage_tree_service
from app.services.library import _escape_like
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
async def move_folder(
    payload: FolderMoveIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> JobOut:
    """Move a folder (every file under it) into another folder of the same
    revision. Runs as a job: each file is copied to its new key, hash-verified
    and committed before the old object is deleted (see ``move_storage_folder``)."""
    try:
        new_prefix = await storage_tree_service.plan_folder_move(db, payload.src, payload.dest)
    except storage_tree_service.FolderMoveError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    src = payload.src.strip("/")
    model = (
        await db.execute(select(Model).where(Model.slug == src.split("/", 1)[0]))
    ).scalar_one_or_none()
    if model is None:
        raise HTTPException(404, "folder not found")
    require_owner_or_admin(ctx, model.created_by_user_id)
    under = File.storage_path.like(f"{_escape_like(src)}/%", escape="\\")
    ids = select(File.id).where(under)
    unsettled = await db.scalar(
        select(func.count()).select_from(File).where(under, File.verified_at.is_(None))
    )
    pending = await db.scalar(
        select(func.count())
        .select_from(Job)
        .where(
            Job.type == "store_to_backend",
            Job.subject_id.in_(ids),
            Job.state.in_([jobs_service.STATE_QUEUED, jobs_service.STATE_RUNNING]),
        )
    )
    if unsettled or pending:
        raise HTTPException(409, "files in this folder are still being stored; try again shortly")
    job = await jobs_service.create_job(
        db, id=uuid.uuid4(), type="move_storage_folder", subject_type="folder", subject_id=None
    )
    try:
        move_storage_folder.apply_async(args=[str(job.id), src, new_prefix], task_id=str(job.id))
    except Exception as exc:
        job.state = jobs_service.STATE_FAILED
        job.error = "failed to enqueue move_storage_folder"
        await db.commit()
        raise HTTPException(503, "could not queue the move; retry") from exc
    await db.refresh(job)
    return JobOut.from_model(job)
