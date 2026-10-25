"""Standalone slice jobs (``POST /files/{id}/slice``) and the shared cancel
plumbing used by both them and the queue's ``cancel-slice``.

A ``SliceJob`` is a slice with no queue entry/printer: ``app.tasks.slicing.
slice_job`` runs the same ``slice_source`` core as the queue's ``slice_file``
and stores the ``.gcode.3mf`` on the source file's revision.
"""

from __future__ import annotations

import logging
import uuid

import anyio
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Blob, File, PrintRecipe, Revision, SliceJob
from app.schemas.slicing import SliceJobIn, SliceJobOut
from app.services.queue import SLICEABLE_FORMATS
from app.services.server_config import get_server_config
from app.services.slicer_client import SlicerError, client_from_config

logger = logging.getLogger(__name__)

ACTIVE_STATES = ("queued", "running")


async def _out(db: AsyncSession, job: SliceJob) -> SliceJobOut:
    file = await db.get(File, job.file_id)
    revision = await db.get(Revision, file.revision_id) if file is not None else None
    out = SliceJobOut.model_validate(job, from_attributes=True)
    out.model_id = revision.model_id if revision is not None else None
    return out


async def create_job(
    db: AsyncSession, settings: Settings, file_id: int, payload: SliceJobIn, user_id: int
) -> SliceJobOut:
    if not (await get_server_config(db, settings)).slicer_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "slicer_disabled")
    file = await db.get(File, file_id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"file {file_id} not found")
    if await db.get(PrintRecipe, payload.recipe_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"recipe {payload.recipe_id} not found")
    blob = await db.get(Blob, file.blob_hash)
    if blob is None or blob.format not in SLICEABLE_FORMATS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "file is not in a format the slicer can accept"
        )
    job_id = uuid.uuid4()
    job = SliceJob(
        id=job_id,
        file_id=file_id,
        recipe_id=payload.recipe_id,
        plate=payload.plate,
        state="queued",
        celery_id=str(job_id),
        created_by_user_id=user_id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    from app.tasks.slicing import slice_job

    # Explicit task id == job id (like `app.services.jobs.create_job`) so
    # cancel can revoke it without a post-dispatch write racing the worker.
    slice_job.apply_async(args=[str(job_id)], task_id=str(job_id))
    return await _out(db, job)


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> SliceJob:
    job = await db.get(SliceJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"slice job {job_id} not found")
    return job


async def get_job_out(db: AsyncSession, job_id: uuid.UUID) -> SliceJobOut:
    job = await get_job(db, job_id)
    return await _out(db, job)


async def stop_slice(
    db: AsyncSession, settings: Settings, sidecar_job_id: str, task_id: str | None
) -> None:
    """Best-effort: kill the sidecar's CLI for `sidecar_job_id` and revoke the
    Celery task. The caller has already moved the DB row out of its active
    state, so a worker that still finishes discards its result."""
    killed = False
    client = client_from_config(await get_server_config(db, settings))
    if client is not None:
        try:
            killed = await anyio.to_thread.run_sync(client.cancel, sidecar_job_id)
        except SlicerError as exc:
            logger.warning("sidecar cancel of %s failed: %s", sidecar_job_id, exc)
    if task_id:
        from app.tasks.celery_app import celery_app

        try:
            # No sidecar job to kill => the task is queued or stuck: hard-stop.
            await anyio.to_thread.run_sync(
                lambda: celery_app.control.revoke(task_id, terminate=not killed, signal="SIGTERM")
            )
        except Exception as exc:  # broker blip: the DB state already wins
            logger.warning("revoke of %s failed: %s", task_id, exc)


async def cancel_job(db: AsyncSession, settings: Settings, job_id: uuid.UUID) -> SliceJobOut:
    from app.tasks.slicing import slice_job_sidecar_id

    job = await get_job(db, job_id)
    if job.state in ACTIVE_STATES:
        job.state = "cancelled"
        job.error = "cancelled"
        await db.commit()
        await stop_slice(db, settings, slice_job_sidecar_id(job.id), job.celery_id)
        await db.refresh(job)
    return await _out(db, job)
