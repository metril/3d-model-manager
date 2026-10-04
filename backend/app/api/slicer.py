"""``POST /slicer/intake`` -- Bambu Studio post-processing script upload
endpoint (Round 8 Task 4: ``scripts/bambu_postprocess.py``). Bearer-token
gated (``require_api_token``), the SAME auth plane ``/ext`` uses -- mounted
directly on ``api_router``, outside ``protected_router`` (see
``app.api.__init__``'s module docstring), since a Bambu Studio
post-processing script can't present the httponly session cookie either.

Mirrors ``app.api.uploads``'s raw-body spool-tee pattern (see that module's
docstring) almost exactly -- the one difference is that the
``(model, revision, rel_path)`` target isn't known from query params here;
``app.services.slicer_intake.resolve_and_attach`` resolves/creates the
model AND finalizes the file in one step, from ``filename`` alone.
"""

from __future__ import annotations

import uuid

import anyio
from blake3 import blake3
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_storage_backend, require_api_token
from app.config import Settings, get_settings
from app.db import get_db
from app.schemas.slicer import IntakeOut
from app.services import slicer_intake, spool
from app.storage.base import StorageBackend

router = APIRouter(prefix="/slicer", tags=["slicer"], dependencies=[Depends(require_api_token)])


@router.post("/intake", status_code=status.HTTP_201_CREATED, response_model=IntakeOut)
async def intake(
    request: Request,
    filename: str,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    backend: StorageBackend = Depends(get_storage_backend),
) -> IntakeOut:
    if not filename or not filename.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "filename is required")

    await anyio.to_thread.run_sync(spool.ensure_spool_dir, settings)
    token = uuid.uuid4()
    path = spool.spool_path(settings, token)

    # Same orphan-avoidance posture as `app.api.uploads`: any failure
    # between here and the job dispatch inside `resolve_and_attach` must
    # not leave a spool file behind with nothing left to ever clean it up.
    try:
        hasher = blake3()
        size = 0
        fh = await anyio.to_thread.run_sync(path.open, "wb")
        try:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "upload too large")
                hasher.update(chunk)
                await anyio.to_thread.run_sync(fh.write, chunk)
            await anyio.to_thread.run_sync(fh.flush)
        finally:
            await anyio.to_thread.run_sync(fh.close)

        if size == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty upload body")

        result = await slicer_intake.resolve_and_attach(
            db,
            backend,
            settings,
            filename=filename,
            spool_token=token,
            spool_path=path,
            blob_hash=hasher.hexdigest(),
            size=size,
        )
    except slicer_intake.UnsupportedIntake as exc:
        await anyio.to_thread.run_sync(lambda: path.unlink(missing_ok=True))
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"unsupported file: {exc}"
        ) from None
    except BaseException:
        await anyio.to_thread.run_sync(lambda: path.unlink(missing_ok=True))
        raise

    return IntakeOut(
        model_id=result.model_id,
        model_name=result.model_name,
        file_id=result.file_id,
        blob_hash=result.blob_hash,
        size=result.size,
        job_id=result.job_id,
        action=result.action,
    )
