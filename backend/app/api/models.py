"""Model CRUD + gallery listing (SPEC "API surface", Task 5 brief)."""

from __future__ import annotations

import contextlib
import hashlib
import uuid
from pathlib import Path

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_storage_backend
from app.config import Settings, get_settings
from app.db import get_db
from app.models.enums import BlobFormat, BlobKind, DerivativeKind, DerivativeStatus
from app.models.library import Blob, File, Model, Project, Revision
from app.models.processing import Derivative
from app.schemas.jobs import JobOut
from app.schemas.library import (
    GalleryPage,
    ModelBulkDeleteIn,
    ModelBulkDeleteOut,
    ModelBulkIn,
    ModelBulkOut,
    ModelCreate,
    ModelDetail,
    ModelMergeIn,
    ModelPatch,
    ModelRedownloadIn,
    ModelRelocateIn,
    ModelSummary,
)
from app.services import derivatives, layout, library, spool, zip_export
from app.services import jobs as jobs_service
from app.services import projects as projects_service
from app.services import storage_backends as storage_backends_service
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


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ModelDetail)
async def create_model(
    payload: ModelCreate,
    db: AsyncSession = Depends(get_db),
    backend: StorageBackend = Depends(get_storage_backend),
    settings: Settings = Depends(get_settings),
) -> ModelDetail:
    model = await library.create_model(
        db,
        backend,
        name=payload.name,
        description=payload.description,
        project_id=payload.project_id,
    )
    return await library.build_model_detail(db, model, settings)


@router.get("", response_model=GalleryPage)
async def list_models(
    q: str | None = None,
    tag: str | None = None,
    format: str | None = None,
    has_sliced: bool | None = None,
    collection: int | None = None,
    favorite: bool | None = None,
    category: int | None = None,
    project: int | None = None,
    print_status: str | None = None,
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
        project=project,
        print_status=print_status,
        sort=sort,
        archived=archived,
        limit=limit,
        cursor=cursor,
    )
    return GalleryPage(items=items, next_cursor=next_cursor)


@router.post("/bulk", response_model=ModelBulkOut)
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
        project_id=payload.project_id,
        print_status=payload.print_status,
    )
    return ModelBulkOut(updated=updated)


@router.post("/bulk-delete", response_model=ModelBulkDeleteOut)
async def bulk_delete_models(
    payload: ModelBulkDeleteIn,
    db: AsyncSession = Depends(get_db),
    backend: StorageBackend = Depends(get_storage_backend),
    settings: Settings = Depends(get_settings),
) -> ModelBulkDeleteOut:
    """Declared BEFORE ``/{slug}`` (same reasoning as ``/bulk`` above) --
    FastAPI matches routes in declaration order, so a literal ``/bulk-delete``
    segment must come before the ``{slug}`` path-param routes or it would be
    parsed as a slug.
    """
    deleted = await library.bulk_hard_delete_models(db, backend, settings, ids=payload.ids)
    return ModelBulkDeleteOut(deleted=deleted)


@router.get("/{slug}", response_model=ModelDetail)
async def get_model(
    slug: str, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> ModelDetail:
    model = await library.get_model_by_slug(db, slug)
    return await library.build_model_detail(db, model, settings)


@router.post("/{slug}/cover", response_model=ModelDetail)
async def set_model_cover(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
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
        )

        job = await jobs_service.create_job(
            db, id=token, type="store_to_backend", subject_type="file", subject_id=file.id
        )
    except BaseException:
        await anyio.to_thread.run_sync(lambda: path.unlink(missing_ok=True))
        raise

    store_to_backend.apply_async(args=[str(job.id), file.id, str(path)], task_id=str(job.id))

    return await library.build_model_detail(db, model, settings)


@router.patch("/{slug}", response_model=ModelDetail)
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


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    slug: str,
    db: AsyncSession = Depends(get_db),
    backend: StorageBackend = Depends(get_storage_backend),
    settings: Settings = Depends(get_settings),
) -> None:
    """A REAL delete (feat/import-fidelity T3) -- physically destroys every
    revision's files (its primary backend AND every replica) plus the
    model's ``.3dmm.json`` sidecar, then the ``Model`` row itself (DB
    cascades take the rest). Soft-delete ("archive") moved to ``PATCH
    {"is_archived": true}`` -- see ``patch_model``/``ModelPatch`` -- since
    this endpoint no longer offers a reversible option.
    """
    model = await library.get_model_by_slug(db, slug)
    await library.hard_delete_model(db, backend, settings, model)


@router.post("/{slug}/redownload", response_model=JobOut)
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


@router.post("/{slug}/relocate", response_model=JobOut)
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


@router.post("/{slug}/explode-plates", response_model=list[ModelSummary])
async def explode_plates(
    slug: str,
    db: AsyncSession = Depends(get_db),
    backend: StorageBackend = Depends(get_storage_backend),
    settings: Settings = Depends(get_settings),
) -> list[ModelSummary]:
    """Explode a multi-plate model into distinct tracked child models in the project (Option B).
    Creates an entry per plate with its dedicated plate thumbnail, print status, and metrics.
    """
    model = await library.get_model_by_slug(db, slug)
    if model.current_revision_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Le modèle n'a pas de fichier.")

    # Find the file and blob with plates
    stmt = (
        select(File, Blob)
        .join(Blob, Blob.hash == File.blob_hash)
        .options(selectinload(Blob.meta))
        .where(File.revision_id == model.current_revision_id)
        .order_by(File.rel_path)
    )
    files = (await db.execute(stmt)).all()

    target_file: File | None = None
    target_blob: Blob | None = None
    plates: list[dict] | None = None

    for f, b in files:
        if b.meta and b.meta.raw:
            raw_plates = b.meta.raw.get("plates")
            if raw_plates and len(raw_plates) > 1:
                target_file = f
                target_blob = b
                plates = raw_plates
                break

    if not target_file or not target_blob or not plates:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Ce modèle ne contient pas plusieurs plateaux."
        )

    # Ensure project exists
    attached_new_project = model.project_id is None
    if model.project_id is None:
        proj = await projects_service.create_project(
            db,
            name=model.name,
            description=f"Projet créé lors de l'éclatement des plateaux de {model.name}",
        )
        model.project_id = proj.id
        await db.commit()
        project_id = proj.id
    else:
        proj = await projects_service.create_project(
            db,
            name=model.name,
            description=f"Plateaux extraits de {model.name}",
            parent_id=model.project_id,
        )
        project_id = proj.id

    created_models: list[Model] = []
    copied_keys: list[str] = []
    for plate in plates:
        idx = plate.get("index", 1)
        p_name = plate.get("name") or f"Plateau {idx}"
        child_name = f"{model.name} - {p_name}"

        # Generate print tips / filament info
        pred = plate.get("prediction_s")
        weight = plate.get("weight_g")
        filaments = plate.get("filaments", [])
        fil_strs = [
            f"{f.get('type') or 'Filament'} ({f.get('color') or ''})".replace(" ()", "")
            for f in filaments
            if isinstance(f, dict)
        ]
        tips_parts = []
        if pred:
            h = pred // 3600
            m = (pred % 3600) // 60
            tips_parts.append(f"Temps: {h}h {m}m" if h > 0 else f"Temps: {m}m")
        if weight:
            tips_parts.append(f"Poids: {round(float(weight), 1)}g")
        if fil_strs:
            tips_parts.append(f"Filaments: {', '.join(fil_strs)}")
        print_tips = " • ".join(tips_parts) if tips_parts else None

        # Check plate thumbnail
        cover_hash = None
        plate_thumb_path = derivatives.plate_thumb_path(settings, target_blob.hash, idx)
        if plate_thumb_path.is_file():
            png_bytes = await anyio.to_thread.run_sync(plate_thumb_path.read_bytes)
            cover_hash = hashlib.sha256(png_bytes).hexdigest()

            # Ensure Blob exists
            thumb_blob = await db.get(Blob, cover_hash)
            if thumb_blob is None:
                thumb_blob = Blob(
                    hash=cover_hash,
                    size=len(png_bytes),
                    kind=BlobKind.IMAGE,
                    format=BlobFormat.PNG,
                )
                db.add(thumb_blob)
                await db.flush()

            # Ensure Derivative THUMB_256 exists
            stmt = select(Derivative).where(
                Derivative.blob_hash == cover_hash,
                Derivative.kind == DerivativeKind.THUMB_256,
            )
            deriv = (await db.execute(stmt)).scalar_one_or_none()
            deriv_path = derivatives.derivative_path(settings, cover_hash, DerivativeKind.THUMB_256)
            deriv_path.parent.mkdir(parents=True, exist_ok=True)
            if not deriv_path.is_file():
                await anyio.to_thread.run_sync(deriv_path.write_bytes, png_bytes)

            if deriv is None:
                deriv = Derivative(
                    blob_hash=cover_hash,
                    kind=DerivativeKind.THUMB_256,
                    status=DerivativeStatus.OK,
                )
                db.add(deriv)

        child = await library.create_model(
            db,
            backend,
            name=child_name,
            description=f"Plateau {idx} extrait de {model.name}",
            project_id=project_id,
            print_status="to_print",
            quantity_target=1,
            quantity_printed=0,
            print_tips=print_tips,
            cover_blob_hash=cover_hash,
            metadata_json={"plate_index": str(idx), "parent_model_slug": model.slug},
            commit=False,
        )

        # Attach file reference to child revision
        if child.current_revision_id is not None:
            dest_storage_path = layout.file_key(
                child.slug,
                layout.revision_dir_name(1, "initial"),
                target_file.rel_path,
            )
            copied_keys.append(dest_storage_path)
            try:
                await anyio.to_thread.run_sync(
                    lambda src=target_file.storage_path, dst=dest_storage_path: backend.copy(
                        src, dst
                    )
                )
            except Exception as exc:
                # Best-effort cleanup: copied bytes (incl. the failed key) and
                # the project this call auto-created if it is still empty.
                await db.rollback()
                for key in copied_keys:
                    with contextlib.suppress(Exception):
                        await anyio.to_thread.run_sync(lambda k=key: backend.delete(k))
                try:
                    if attached_new_project:
                        await db.execute(
                            update(Model)
                            .where(Model.id == model.id, Model.project_id == proj.id)
                            .values(project_id=None)
                        )
                    has_models = await db.scalar(
                        select(func.count()).select_from(Model).where(Model.project_id == proj.id)
                    )
                    has_children = await db.scalar(
                        select(func.count())
                        .select_from(Project)
                        .where(Project.parent_id == proj.id)
                    )
                    if not has_models and not has_children:
                        await projects_service.delete_project(db, proj.id)
                except Exception:
                    await db.rollback()
                # Never share storage_path between File rows: deleting one
                # model would remove the other's bytes.
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    f"Failed to copy plate file '{target_file.rel_path}'",
                ) from exc

            file_row = File(
                revision_id=child.current_revision_id,
                blob_hash=target_blob.hash,
                rel_path=target_file.rel_path,
                storage_path=dest_storage_path,
                backend_id=target_file.backend_id,
                verified_at=func.now(),
            )
            db.add(file_row)

        created_models.append(child)

    await db.commit()

    # Re-fetch models with relationships loaded for build_model_summaries
    child_ids = [c.id for c in created_models]
    stmt = (
        select(Model)
        .where(Model.id.in_(child_ids))
        .options(
            selectinload(Model.tags),
            selectinload(Model.category),
            selectinload(Model.project),
        )
        .order_by(Model.name)
    )
    loaded_children = (await db.execute(stmt)).scalars().all()
    return await library.build_model_summaries(db, settings, loaded_children)


@router.post("/{slug}/merge", response_model=ModelDetail)
async def merge_models_endpoint(
    slug: str,
    payload: ModelMergeIn,
    db: AsyncSession = Depends(get_db),
    backend: StorageBackend = Depends(get_storage_backend),
    settings: Settings = Depends(get_settings),
) -> ModelDetail:
    target = await library.get_model_by_slug(db, slug)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Model '{slug}' not found")

    slugs = list(payload.source_slugs)
    if payload.source_slug and payload.source_slug not in slugs:
        slugs.append(payload.source_slug)

    if not slugs:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No source models provided to merge")

    sources: list[Model] = []
    for s_slug in slugs:
        if s_slug == slug:
            continue
        src = await library.get_model_by_slug(db, s_slug)
        if src is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Source model '{s_slug}' not found")
        sources.append(src)

    if not sources:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot merge model into itself")

    merged = await library.merge_models(db, backend, settings, target, sources)
    return await library.build_model_detail(db, merged, settings)
