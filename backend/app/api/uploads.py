"""Raw-body streamed upload endpoint (SPEC "Upload flow", Task 6 interface
decisions): tees the body to blake3 + a spool file while it streams, then
upserts blob/file rows and enqueues ``store_to_backend`` to copy the spool
bytes onto the storage backend. The uploaded file is NOT yet on backend
storage when this returns -- ``verified_at`` stays NULL until the job
completes (see ``app.tasks.ingest.store_to_backend``).
"""

from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_owner_or_admin, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.models import File
from app.schemas.uploads import DuplicateUploadOut, ExistingUploadModel, UploadResult
from app.services import jobs as jobs_service
from app.services import library, spool
from app.services.import_dedup import find_model_by_blob_hash
from app.services.layout import infer_blob_kind_format
from app.services.server_config import get_server_config
from app.tasks.ingest import store_to_backend

router = APIRouter(tags=["uploads"])


class _DuplicateBlob(Exception):
    """Internal signal: the streamed blob's hash already exists elsewhere in
    the library. Raised (rather than returned directly) so it flows through
    the same spool-cleanup ``finally``/``except`` path as every other
    failure between the stream and the job dispatch."""

    def __init__(self, body: DuplicateUploadOut) -> None:
        self.body = body


@router.put(
    "/uploads",
    status_code=status.HTTP_201_CREATED,
    response_model=UploadResult,
    dependencies=[Depends(require_perm("library:write"))],
)
async def upload_file(
    request: Request,
    model_id: int,
    revision_id: int,
    rel_path: str,
    replace: bool = False,
    allow_duplicate: bool = False,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> UploadResult | JSONResponse:
    model, revision = await library.validate_upload_target(
        db, model_id=model_id, revision_id=revision_id, rel_path=rel_path, replace=replace
    )
    require_owner_or_admin(ctx, model.created_by_user_id)

    # Any failure between here and the job dispatch below must not orphan
    # the spool file (once dispatched, the spool's lifecycle belongs to
    # store_to_backend: deleted on success, kept on failure for retry).
    token, path, blob_hash, size = await spool.stream_to_spool(
        request, settings, max_size=(await get_server_config(db)).max_upload_bytes
    )
    replaced_ids: list[int] = []
    try:
        if size == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty upload body")

        if not allow_duplicate:
            existing = await find_model_by_blob_hash(db, blob_hash, exclude_model_id=model_id)
            if existing is not None:
                raise _DuplicateBlob(
                    DuplicateUploadOut(
                        existing=ExistingUploadModel(
                            slug=existing.slug,
                            name=existing.name,
                            url=f"/models/{existing.slug}",
                        ),
                        suggested_name=f"{model.name} (2)",
                    )
                )

        kind, format_ = infer_blob_kind_format(rel_path)

        file = await library.finalize_upload(
            db,
            model=model,
            revision=revision,
            rel_path=rel_path,
            blob_hash=blob_hash,
            size=size,
            kind=kind,
            format_=format_,
            replace=replace,
            created_by_user_id=ctx.user.id,
            job_id=token,
            replaced_ids=replaced_ids,
        )
        job = await jobs_service.get_job_or_404(db, token)
    except _DuplicateBlob as dup:
        await anyio.to_thread.run_sync(lambda: path.unlink(missing_ok=True))
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=dup.body.model_dump())
    except BaseException:
        await anyio.to_thread.run_sync(lambda: path.unlink(missing_ok=True))
        raise

    try:
        store_to_backend.apply_async(args=[str(job.id), file.id, str(path)], task_id=str(job.id))
    except Exception:
        # Broker down: nothing will ever settle this job. Fail it and drop the
        # never-stored file row so the client can simply retry the upload.
        job.state = jobs_service.STATE_FAILED
        job.error = "failed to enqueue store_to_backend"
        orphan = await db.get(File, file.id)
        if orphan is not None:
            await db.delete(orphan)
        await db.flush()
        for old_id in replaced_ids:  # the replaced file survives a failed enqueue
            old = await db.get(File, old_id)
            if old is not None:
                old.rel_path = rel_path
        await db.commit()
        await anyio.to_thread.run_sync(lambda: path.unlink(missing_ok=True))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "could not queue the upload; retry"
        ) from None

    for old_id in replaced_ids:
        old = await db.get(File, old_id)
        if old is not None:
            await db.delete(old)
    if replaced_ids:
        await db.commit()

    return UploadResult(file_id=file.id, blob_hash=blob_hash, size=size, job_id=job.id)
