"""Job listing + retry (SPEC "API surface", Task 6 interface decisions)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_perm
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Job
from app.schemas.jobs import JobOut
from app.services import jobs as jobs_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
async def list_jobs(
    state: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[JobOut]:
    rows = await jobs_service.list_jobs(db, state=state, limit=limit)
    return [JobOut.from_model(job) for job in rows]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> JobOut:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return JobOut.from_model(job)


@router.post(
    "/{job_id}/retry", response_model=JobOut, dependencies=[Depends(require_perm("library:write"))]
)
async def retry_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JobOut:
    job = await jobs_service.retry_job(db, settings, job_id)
    return JobOut.from_model(job)
