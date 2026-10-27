"""Model CRUD + gallery listing (SPEC "API surface", Task 5 brief)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    get_storage_backend,
    require_perm,
    require_session,
)
from app.config import Settings, get_settings
from app.db import get_db
from app.models.enums import BlobFormat, BlobKind
from app.models.library import Revision
from app.schemas.jobs import JobOut
from app.schemas.library import (
    FileOut,
    GalleryPage,
    ModelBulkDeleteIn,
    ModelBulkDeleteOut,
    ModelBulkIn,
    ModelBulkOut,
    ModelCreate,
    ModelDetail,
    ModelPatch,
    ModelRedownloadIn,
    ModelRelocateIn,
)
from app.services import jobs as jobs_service
from app.services import layout, library, spool, zip_export
from app.services import storage_backends as storage_backends_service
from app.services import tags as tags_svc
from app.services import trash as trash_service
from app.services.http_names import content_disposition_attachment
from app.storage.base import StorageBackend
from app.tasks.importing import redownload_model as redownload_model_task
from app.tasks.ingest import store_to_backend
from app.tasks.relocate import relocate_model_storage

router = APIRouter(prefix="/models", tags=["models"])

_COVER_MAX_SIZE = 20 * 1024 * 1024  # 20 MB (R13a plan: "sane cap")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _read_magic(path: Path) -> bytes:
    with path.open("rb") as fh:
        return fh.read(len(_PNG_MAGIC))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ModelDetail,
    dependencies=[Depends(require_perm("library:write"))],
)
async def create_model(
    payload: ModelCreate,
    db: AsyncSession = Depends(get_db),
    backend: StorageBackend = Depends(get_storage_backend),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> ModelDetail:
    model = await library.create_model(
        db,
        backend,
        name=payload.name,
        description=payload.description,
        created_by_user_id=ctx.user.id,
    )
    return await library.build_model_detail(db, model, settings)


@router.get("", response_model=GalleryPage)
async def list_models(
    q: str | None = None,
    tag: str | None = None,
    tags: Annotated[list[int] | None, Query()] = None,
    tag_mode: Literal["any", "all"] = "any",
    file_tags: Annotated[list[int] | None, Query()] = None,
    file_tag_mode: Literal["any", "all"] = "any",
    format: str | None = None,
    has_sliced: bool | None = None,
    collection: int | None = None,
    favorite: bool | None = None,
    category: int | None = None,
    sort: str = "-updated_at",
    archived: bool = False,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> GalleryPage:
    items, next_cursor = await library.list_models(
        db,
        settings,
        q=q,
        tag=tag,
        format_=format,
        has_sliced=has_sliced,
        collection=collection,
        favorite=favorite,
        category=category,
        sort=sort,
        archived=archived,
        limit=limit,
        cursor=cursor,
        tag_ids=tags or (),
        tag_mode=tag_mode,
        file_tag_ids=file_tags or (),
        file_tag_mode=file_tag_mode,
    )
    return GalleryPage(items=items, next_cursor=next_cursor)


@router.post(
    "/bulk", response_model=ModelBulkOut, dependencies=[Depends(require_perm("library:write"))]
)
async def bulk_update_models(
    payload: ModelBulkIn, db: AsyncSession = Depends(get_db)
) -> ModelBulkOut:
    """Declared BEFORE ``/{slug}`` (Branch 4 Task 1) -- FastAPI matches
    routes in declaration order, so a literal ``/bulk`` segment must come
    before the ``{slug}`` path-param routes or it would be parsed as a slug.
    """
    updated = await library.bulk_update_models(
        db,
        ids=payload.ids,
        add_tags=payload.add_tags,
        remove_tags=payload.remove_tags,
        favorite=payload.favorite,
    )
    return ModelBulkOut(updated=updated)


@router.post(
    "/bulk-delete",
    response_model=ModelBulkDeleteOut,
    dependencies=[Depends(require_perm("library:write"))],
)
async def bulk_delete_models(
    payload: ModelBulkDeleteIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> ModelBulkDeleteOut:
    """Declared BEFORE ``/{slug}`` (same reasoning as ``/bulk`` above) --
    FastAPI matches routes in declaration order, so a literal ``/bulk-delete``
    segment must come before the ``{slug}`` path-param routes or it would be
    parsed as a slug. Moves the models to the trash (all-or-nothing: an
    unknown id 404s, a model the caller may not delete 403s).
    """
    deleted = await trash_service.move_to_trash(db, ctx, payload.ids)
    return ModelBulkDeleteOut(deleted=deleted)


@router.get("/{model_id}/files", response_model=list[FileOut])
async def list_model_files(
    model_id: int,
    tags: Annotated[list[int] | None, Query()] = None,
    tag_mode: Literal["any", "all"] = "any",
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[FileOut]:
    """Current-revision files (with ``tag_refs``), optionally narrowed to
    files tagged with any/all of ``tags`` (ids)."""
    model = await library.get_model_by_id(db, model_id)
    if model.current_revision_id is None:
        return []
    revision = await library.get_revision_or_404(db, model.current_revision_id)
    files = (await library.build_revision_detail(db, revision, settings)).files
    if tags:
        paths = set(
            (await db.execute(tags_svc.file_paths_with_tags(model_id, tags, tag_mode))).scalars()
        )
        files = [f for f in files if f.rel_path in paths]
    return files


@router.get("/{slug}", response_model=ModelDetail)
async def get_model(
    slug: str, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> ModelDetail:
    model = await library.get_model_by_slug(db, slug)
    return await library.build_model_detail(db, model, settings)


@router.post(
    "/{slug}/cover",
    response_model=ModelDetail,
    dependencies=[Depends(require_perm("library:write"))],
)
async def set_model_cover(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> ModelDetail:
    """Raw-body PNG upload (R13a): reuses the same tee-to-spool ingest path
    as ``PUT /api/uploads`` (``app.services.spool.stream_to_spool`` ->
    ``library.finalize_upload`` -> ``store_to_backend``/pipeline dispatch),
    landing the bytes at the FIXED ``_snapshots/cover.png`` rel_path on the
    model's current revision and pointing ``model.cover_blob_hash`` at the
    new blob in the same transaction as the ``File`` row. A repost reuses
    the same rel_path with ``replace=True`` (review fix) so the revision
    only ever carries ONE snapshot ``File`` row -- an epoch-suffixed
    rel_path would otherwise accumulate a permanent new row per click,
    leaking into file listings/zip export/storage totals and potentially
    winning the "first ok thumb by rel_path" gallery fallback.
    """
    model = await library.get_model_by_slug(db, slug)
    if model.current_revision_id is None:
        # Mirror `zip_export`'s "nothing to operate on yet" 409 -- a model
        # with no revision yet has nowhere to land the snapshot file, and
        # `db.get(Revision, None)` below would otherwise return `None` and
        # 500 deep inside `finalize_upload`.
        raise HTTPException(status.HTTP_409_CONFLICT, "model has no current revision")
    revision = await db.get(Revision, model.current_revision_id)

    token, path, blob_hash, size = await spool.stream_to_spool(
        request, settings, max_size=_COVER_MAX_SIZE
    )
    try:
        if size == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty upload body")

        magic = await anyio.to_thread.run_sync(_read_magic, path)
        if magic != _PNG_MAGIC:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "not a PNG file")

        rel_path = f"{layout.SNAPSHOT_PREFIX}cover.png"

        def _set_cover_hash() -> None:
            model.cover_blob_hash = blob_hash

        file = await library.finalize_upload(
            db,
            model=model,
            revision=revision,
            rel_path=rel_path,
            blob_hash=blob_hash,
            size=size,
            kind=BlobKind.IMAGE,
            format_=BlobFormat.PNG,
            replace=True,
            after_blob_flush=_set_cover_hash,
            created_by_user_id=ctx.user.id,
        )

        job = await jobs_service.create_job(
            db, id=token, type="store_to_backend", subject_type="file", subject_id=file.id
        )
    except BaseException:
        await anyio.to_thread.run_sync(lambda: path.unlink(missing_ok=True))
        raise

    store_to_backend.apply_async(args=[str(job.id), file.id, str(path)], task_id=str(job.id))

    return await library.build_model_detail(db, model, settings)


@router.patch(
    "/{slug}", response_model=ModelDetail, dependencies=[Depends(require_perm("library:write"))]
)
async def patch_model(
    slug: str,
    payload: ModelPatch,
    db: AsyncSession = Depends(get_db),
    backend: StorageBackend = Depends(get_storage_backend),
    settings: Settings = Depends(get_settings),
) -> ModelDetail:
    model = await library.get_model_by_slug(db, slug)
    model = await library.patch_model(db, backend, model, payload.model_dump(exclude_unset=True))
    return await library.build_model_detail(db, model, settings)


@router.delete(
    "/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("library:write"))],
)
async def delete_model(
    slug: str,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> None:
    """Moves the model to the trash (restorable; purged after the retention
    window or on demand via ``/trash``). Distinct from archive
    (``PATCH {"is_archived": true}``)."""
    model = await library.get_model_by_slug(db, slug)
    await trash_service.move_to_trash(db, ctx, [model.id])


@router.post(
    "/{slug}/redownload",
    response_model=JobOut,
    dependencies=[Depends(require_perm("library:write"))],
)
async def redownload_model(
    slug: str,
    payload: ModelRedownloadIn,
    db: AsyncSession = Depends(get_db),
) -> JobOut:
    """Re-fetches this model's files fresh from its original import source
    (feat/import-fidelity T3): dispatches
    ``app.tasks.importing.redownload_model``, which either lands the fresh
    download as a NEW revision (``mode="revision"``, old revision untouched)
    or overwrites the CURRENT revision's files in place
    (``mode="replace"``). 409 unless the model still has a resolvable import
    source -- ``library.check_redownload_source`` reuses the same registry +
    ``canonicalize`` seam ``app.services.imports.start_import`` uses to
    validate a fresh import's URL.

    Deliberately does NOT consult
    ``app.services.import_dedup.find_live_import`` -- that guard exists so a
    SECOND import can't create a second Model for a remote source already in
    the library. A re-download targets THIS existing model by id and never
    creates a new Model, so the guard doesn't apply: it's model-scoped by
    design, not source-scoped.
    """
    model = await library.get_model_by_slug(db, slug)
    library.check_redownload_source(model)

    job = await jobs_service.create_job(
        db,
        id=uuid.uuid4(),
        type="redownload_model",
        subject_type="model",
        subject_id=model.id,
    )
    redownload_model_task.apply_async(
        args=[str(job.id), model.id, payload.mode], task_id=str(job.id)
    )

    # Under the test suite's eager Celery mode, the line above already ran
    # the whole redownload inline through its own SYNC session -- refresh so
    # this (separate, async) session's identity map doesn't hand back the
    # stale "queued" snapshot from right after the insert (same reasoning as
    # `relocate_model` below).
    await db.refresh(job)
    return JobOut.from_model(job)


@router.get("/{slug}/zip")
async def download_model_zip(
    slug: str,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Stream a zip of ``model``'s current revision (plan item 12): every
    current-revision file plus a ``README.txt`` with provenance. 409 if the
    model has zero files -- there'd be nothing to zip."""
    model = await library.get_model_by_slug(db, slug)
    try:
        _chunk, body = await zip_export.first_chunk(zip_export.iter_model_zip(db, settings, model))
    except zip_export.EmptyModelError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    return StreamingResponse(
        body,
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition_attachment(f"{model.slug}.zip")},
    )


@router.post(
    "/{slug}/relocate", response_model=JobOut, dependencies=[Depends(require_perm("library:write"))]
)
async def relocate_model(
    slug: str,
    payload: ModelRelocateIn,
    db: AsyncSession = Depends(get_db),
) -> JobOut:
    """Dispatch ``app.tasks.relocate.relocate_model_storage`` (Workstream C
    task C3) to move or replicate every file of this model, across all its
    revisions, onto ``payload.target_backend_id``. ``mode`` is already
    constrained to ``{"move", "replicate"}`` at the schema boundary
    (``ModelRelocateIn``); a nonexistent target backend 404s here (via
    ``get_backend_row``) before a job row is ever created.
    """
    model = await library.get_model_by_slug(db, slug)
    await storage_backends_service.get_backend_row(db, payload.target_backend_id)

    job = await jobs_service.create_job(
        db,
        id=uuid.uuid4(),
        type="relocate_model_storage",
        subject_type="model",
        subject_id=model.id,
    )
    relocate_model_storage.apply_async(
        args=[str(job.id), model.id, payload.target_backend_id, payload.mode],
        task_id=str(job.id),
    )

    # Under the test suite's eager Celery mode, the line above already ran
    # the whole relocate inline through its own SYNC session -- refresh so
    # this (separate, async) session's identity map doesn't hand back the
    # stale "queued" snapshot from right after the insert (same reasoning as
    # app.api.settings.migrate_storage_settings).
    await db.refresh(job)
    return JobOut.from_model(job)
