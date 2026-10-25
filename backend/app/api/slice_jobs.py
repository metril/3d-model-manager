"""Slice-only API: ``POST /files/{id}/slice`` starts a ``SliceJob``,
``GET /slice-jobs/{id}`` follows it, ``DELETE /slice-jobs/{id}`` cancels it.
Same permission as queue creation (``printers:print``)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_owner_or_admin, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.schemas.slicing import SliceJobIn, SliceJobOut
from app.services import slice_jobs

router = APIRouter(tags=["slicing"], dependencies=[Depends(require_perm("printers:print"))])


@router.post("/files/{file_id}/slice", status_code=status.HTTP_201_CREATED)
async def start_slice(
    file_id: int,
    payload: SliceJobIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> SliceJobOut:
    return await slice_jobs.create_job(db, settings, file_id, payload, ctx.user.id)


@router.get("/slice-jobs/{job_id}")
async def get_slice_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> SliceJobOut:
    return await slice_jobs.get_job_out(db, job_id)


@router.delete("/slice-jobs/{job_id}")
async def cancel_slice_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> SliceJobOut:
    job = await slice_jobs.get_job(db, job_id)
    require_owner_or_admin(ctx, job.created_by_user_id)
    return await slice_jobs.cancel_job(db, settings, job_id)
