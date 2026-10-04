"""Library domain: models, revisions, files, tags, notes (SPEC "Data model",
"Storage layer", Task 5 brief).

Endpoints (``app/api/{models,revisions,tags,notes,files}.py``) stay thin --
this module owns DB queries, the storage-first-then-commit ordering, and
building the response schemas from loaded ORM rows.

Per Task 5's interface decision, storage side effects always run *before*
the DB commit for the same logical operation (create model, create
revision, delete file): if the storage op raises, the request fails and
nothing about it is committed (the ``get_db`` dependency's
``async with session:`` rolls back on the way out). Cross-request crash
consistency for storage ops that partially succeeded before a crash (e.g. a
directory or a few copied files with no matching committed rows) is left to
the scanner (SPEC M3 "Rescan/reconcile") -- not handled here.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal

import anyio
from fastapi import HTTPException, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select, tuple_
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.importers.base import SiteImporter
from app.importers.registry import get_importer
from app.models.enums import BlobFormat, BlobKind, DerivativeKind, DerivativeStatus, ImportSite
from app.models.library import (
    Blob,
    Category,
    File,
    Model,
    Note,
    Print,
    Project,
    Revision,
    Tag,
    model_tags,
)
from app.models.processing import AssemblyThumb, BlobMeta, Derivative
from app.models.storage import FileLocation, StorageBackendRow
from app.models.system import Job
from app.schemas.library import (
    BlobMetaOut,
    DiffEntry,
    DiffEntrySide,
    DiffResponse,
    FileEnrichment,
    FileOut,
    ModelBackendOut,
    ModelCategoryOut,
    ModelDetail,
    ModelProjectOut,
    ModelSummary,
    NoteOut,
    PlateOut,
    RevisionDetail,
    RevisionSummary,
    TagOut,
)
from app.services import derivatives, layout
from app.services import jobs as jobs_service
from app.services.cursor import decode_cursor, encode_cursor
from app.services.storage_backends import (
    backend_for_id,
    resolve_backend_for_file,
    resolve_default_backend,
)
from app.storage.base import StorageBackend
from app.storage.errors import StorageKeyNotFound

if TYPE_CHECKING:
    # Avoids a runtime import cycle (app.importers.download doesn't import
    # this module, but keeping the dependency one-directional at runtime is
    # simplest): only needed for the `store_imported_file_sync` annotation.
    from app.importers.download import StagedFile


def _parse_cursor_datetime(raw: str) -> datetime:
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid cursor") from exc


def _parse_cursor_int(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid cursor") from exc


def _parse_cursor_str(raw: str) -> str:
    return raw


# Table-driven keyset-cursor support (Task 5 D1; R13b Risk resolution 7):
# each sortable field maps to `(column, parse, extract)` -- `parse` turns
# the cursor's decoded string back into a value comparable against `column`
# (an int/datetime/str cursor all work through the SAME `list_models` code
# path below), and `extract` renders a page's last row's sort value back
# into a string for `encode_cursor`. Adding a new sortable field is exactly
# one entry here, never a new `if field_name == ...` branch.
_SORT_FIELDS: dict[str, tuple[object, object, object]] = {
    "updated_at": (Model.updated_at, _parse_cursor_datetime, lambda m: m.updated_at.isoformat()),
    "created_at": (Model.created_at, _parse_cursor_datetime, lambda m: m.created_at.isoformat()),
    "name": (Model.name, _parse_cursor_str, lambda m: m.name),
    "print_count": (Model.print_count, _parse_cursor_int, lambda m: str(m.print_count)),
}

# Formats whose blobs get a `glb` derivative at all (Global Constraints
# "Pipeline shape" table) -- mirrors `app.tasks.pipeline`'s private
# `_MESH_FORMATS + _CAD_FORMATS`, duplicated here rather than imported to
# avoid a services -> tasks layering inversion (`app.tasks.pipeline` already
# imports `app.services.jobs`/`app.services.derivatives`).
_GLB_FORMATS = (
    BlobFormat.STL,
    BlobFormat.OBJ,
    BlobFormat.THREEMF,
    BlobFormat.STEP,
    BlobFormat.IGES,
)


# -- file enrichment (Task 7) ---------------------------------------------


def _derivative_ok(blob: Blob, kind: DerivativeKind) -> bool:
    return any(d.kind == kind and d.status == DerivativeStatus.OK for d in blob.derivatives)


def _glb_status(blob: Blob) -> Literal["ok", "pending", "failed", "unsupported"] | None:
    """``None`` when ``blob.format`` never produces a GLB at all; a missing
    row on a GLB-format blob is ``"pending"`` (Task 7 interface decision).
    """
    if blob.format not in _GLB_FORMATS:
        return None
    deriv = next((d for d in blob.derivatives if d.kind == DerivativeKind.GLB), None)
    if deriv is None:
        return "pending"
    return deriv.status.value


async def _build_file_enrichments(
    settings: Settings, blobs: Iterable[Blob]
) -> dict[str, FileEnrichment]:
    """``{blob_hash: FileEnrichment}`` for every DISTINCT blob among
    ``blobs`` (already ``selectinload``ed with ``.meta``/``.derivatives`` by
    the caller -- no extra DB queries here). The only filesystem access
    anywhere in this function is plate-thumbnail existence (plate PNGs are
    rowless, Global Constraints "Derivative store"): every plate path across
    every blob is batched into ONE ``anyio.to_thread.run_sync`` call rather
    than one per plate/file (Task 7 interface decision).
    """
    unique_blobs = {blob.hash: blob for blob in blobs}

    plate_paths: dict[tuple[str, int], object] = {}
    for blob_hash, blob in unique_blobs.items():
        raw_plates = (blob.meta.raw or {}).get("plates") if blob.meta is not None else None
        for plate in raw_plates or []:
            plate_paths[(blob_hash, plate["index"])] = derivatives.plate_thumb_path(
                settings, blob_hash, plate["index"]
            )

    def _check_existence() -> dict[tuple[str, int], bool]:
        return {key: path.exists() for key, path in plate_paths.items()}

    existence = await anyio.to_thread.run_sync(_check_existence) if plate_paths else {}

    enrichments: dict[str, FileEnrichment] = {}
    for blob_hash, blob in unique_blobs.items():
        meta_out = None
        if blob.meta is not None:
            raw_plates = (blob.meta.raw or {}).get("plates")
            plates_out = (
                [
                    PlateOut.from_raw(
                        plate, thumbnail_available=existence.get((blob_hash, plate["index"]), False)
                    )
                    for plate in raw_plates
                ]
                if raw_plates
                else None
            )
            meta_out = BlobMetaOut.from_model(blob.meta, plates_out)
        enrichments[blob_hash] = FileEnrichment(
            meta=meta_out,
            thumb_ready=_derivative_ok(blob, DerivativeKind.THUMB_256),
            glb_status=_glb_status(blob),
            glb_preview_ready=_derivative_ok(blob, DerivativeKind.GLB_PREVIEW),
        )
    return enrichments


# -- models -------------------------------------------------------------


async def _unique_slug(db: AsyncSession, name: str) -> str:
    """Base slug, uniquified with ``-2``, ``-3``, ... on collision."""
    base = layout.slug_for(name)
    slug = base
    suffix = 2
    while await db.scalar(select(Model.id).where(Model.slug == slug)) is not None:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


async def create_model(
    db: AsyncSession,
    backend: StorageBackend,
    *,
    name: str,
    description: str | None,
    project_id: int | None = None,
    print_status: str | None = None,
    quantity_target: int = 1,
    quantity_printed: int = 0,
    print_tips: str | None = None,
    cover_blob_hash: str | None = None,
    metadata_json: dict[str, str] | None = None,
    source_url: str | None = None,
    source_site: str | None = None,
    source_author: str | None = None,
    source_license: str | None = None,
    imported_at: datetime | None = None,
    initial_revision_name: str = "initial",
    commit: bool = True,
) -> Model:
    """``commit=False`` (Round 8 fix-review M2, mirroring
    ``create_imported_model_sync``'s existing ``commit`` param) lets a
    caller that immediately attaches a file to the just-created model (e.g.
    ``app.services.slicer_intake.resolve_and_attach``) fold the Model+
    Revision insert into the SAME transaction as ``finalize_upload``'s file
    insert/commit -- so a failure in that later step (a concurrent-blob
    409, an unexpected storage error) rolls back the model too, instead of
    leaving an orphan, file-less Model durably committed on its own.
    """
    slug = await _unique_slug(db, name)
    # `tags=[]` marks the relationship collection as already-loaded on this
    # (about to become persistent) instance -- without it, a bare
    # `model.tags` access later in the same request (build_model_detail)
    # would find the collection unloaded and try to lazy-load it, which
    # raises ``MissingGreenlet`` outside of an explicit ``await
    # session.execute(...)``-style call.
    model = Model(
        slug=slug,
        name=name,
        description=description,
        tags=[],
        category=None,
        project=None,
        project_id=project_id,
        print_status=print_status,
        quantity_target=quantity_target,
        quantity_printed=quantity_printed,
        print_tips=print_tips,
        cover_blob_hash=cover_blob_hash,
        metadata_json=metadata_json,
        source_url=source_url,
        source_site=source_site,
        source_author=source_author,
        source_license=source_license,
        imported_at=imported_at,
    )
    db.add(model)
    await db.flush()  # assigns model.id, needed for the sidecar body

    dir_name = layout.revision_dir_name(1, initial_revision_name)
    revision = Revision(model_id=model.id, number=1, name=initial_revision_name, dir_name=dir_name)
    db.add(revision)
    await db.flush()

    def _write_storage() -> None:
        backend.mkdirs(layout.revision_dir_key(slug, revision.dir_name))
        layout.write_sidecar(backend, model.id, slug, model.name)

    await anyio.to_thread.run_sync(_write_storage)

    model.current_revision_id = revision.id
    if commit:
        await db.commit()
    return model


def _unique_slug_sync(session: SyncSession, name: str) -> str:
    base = layout.slug_for(name)
    slug = base
    suffix = 2
    while session.scalar(select(Model.id).where(Model.slug == slug)) is not None:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def create_imported_model_sync(
    session: SyncSession,
    backend: StorageBackend,
    *,
    name: str,
    description: str | None,
    source_url: str | None,
    source_site: str | None,
    source_author: str | None,
    source_license: str | None,
    imported_at: datetime | None,
    tags: list[str],
    source_collection_id: int | None = None,
    source_collection_title: str | None = None,
    initial_revision_name: str = "imported",
    commit: bool = True,
) -> Model:
    """SYNC twin of ``create_model`` for the import worker (app.tasks.base
    sync world). Inserts the Model + first revision WITH provenance, writes
    the storage sidecar, get-or-creates tag rows, and (by default) commits
    atomically -- called only AFTER every file is staged to spool, so a
    Model row never exists for a failed import (Global Constraints "IMPORTS
    ATOMIC").

    ``commit=False`` (M6 Task 4 fix-review) lets ``app.tasks.importing``
    defer the commit so it can set ``imports.model_id`` in the SAME
    transaction as the Model+Revision insert -- the flushes below already
    populate ``model.id``/``model.current_revision_id`` on the still-
    in-session instance, so the caller has everything it needs before
    committing. Without this, the early link was a SEPARATE second commit,
    leaving a window where Model+Revision were durably committed while
    ``imports.model_id`` (and the DOWNLOADING->done transition) was not --
    undetectable to the redelivery guard, which only reconciles on
    ``imports.model_id IS NOT NULL``.

    ``source_collection_id``/``source_collection_title`` (Branch 3 Task 1) are
    the followed collection this import came from, if any -- ``None`` for a
    manually-pasted URL."""
    slug = _unique_slug_sync(session, name)
    model = Model(
        slug=slug,
        name=name,
        description=description,
        tags=[],
        category=None,
        project=None,
        source_url=source_url,
        source_site=source_site,
        source_author=source_author,
        source_license=source_license,
        imported_at=imported_at,
        source_collection_id=source_collection_id,
        source_collection_title=source_collection_title,
    )
    session.add(model)
    session.flush()
    dir_name = layout.revision_dir_name(1, initial_revision_name)
    revision = Revision(model_id=model.id, number=1, name=initial_revision_name, dir_name=dir_name)
    session.add(revision)
    session.flush()
    backend.mkdirs(layout.revision_dir_key(slug, dir_name))
    layout.write_sidecar(backend, model.id, slug, model.name)
    for tag_name in tags:
        tag = session.scalar(select(Tag).where(Tag.name == tag_name))
        if tag is None:
            tag = Tag(name=tag_name)
            session.add(tag)
            session.flush()
        model.tags.append(tag)
    model.current_revision_id = revision.id
    if commit:
        session.commit()
    return model


def store_imported_file_sync(
    session: SyncSession, *, model: Model, revision: Revision, staged: StagedFile
) -> File:
    """The §3a ingest seam for one staged import file (SYNC twin of the
    ``finalize_upload`` + ``create_job`` + ``store_to_backend.apply_async``
    sequence ``PUT /uploads`` runs). Upserts the Blob by hash, inserts the
    File (verified_at NULL), then dispatches the SAME store_to_backend job a
    browser upload does -- so glb/thumbs run afterward via the normal
    pipeline. ``staged.token`` is the spool token AND the job id (so a retry
    re-finds the spool), mirroring ``app.api.uploads``."""
    from app.services import jobs as jobs_service
    from app.tasks.ingest import store_to_backend

    blob = session.get(Blob, staged.blob_hash)
    if blob is None:
        blob = Blob(
            hash=staged.blob_hash, size=staged.size, kind=staged.kind, format=staged.format_
        )
        try:
            # Savepoint: a duplicate-blob failure rolls back only the insert,
            # never the caller's pending (possibly nested) transaction state.
            with session.begin_nested():
                session.add(blob)
                session.flush()
        except IntegrityError:
            blob = session.get(Blob, staged.blob_hash)  # concurrent insert of same content
    elif blob.format == BlobFormat.OTHER and staged.format_ != BlobFormat.OTHER:
        # Same bytes landed before under a name `infer_blob_kind_format`
        # couldn't classify (e.g. a MakerWorld print-profile `.zip` that's
        # actually a 3MF container) and got stuck as `other`/`other` --
        # PIPELINE_STEPS[OTHER] is empty, so that blob has never produced a
        # glb/thumb for ANY file sharing its hash. A later import/re-download
        # of the IDENTICAL content under a real extension now knows better;
        # upgrade the stored row in place so `start_pipeline_sync` below
        # (keyed off THIS row, not `staged`) finds a real step chain.
        # Never downgrades (guarded by the `== OTHER` check above) and never
        # clobbers one already-specific format with a different specific one
        # -- which of two disagreeing specific formats is "right" is
        # genuinely ambiguous, so that case is deliberately left untouched.
        blob.kind = staged.kind
        blob.format = staged.format_
    file = File(
        revision_id=revision.id,
        blob_hash=staged.blob_hash,
        rel_path=staged.rel_path,
        storage_path=layout.file_key(model.slug, revision.dir_name, staged.rel_path),
        verified_at=None,
    )
    session.add(file)
    session.commit()
    session.refresh(file)
    job = jobs_service.create_job_sync(
        session, id=staged.token, type="store_to_backend", subject_type="file", subject_id=file.id
    )
    store_to_backend.apply_async(
        args=[str(job.id), file.id, str(staged.spool_path)], task_id=str(job.id)
    )
    return file


async def get_model_by_id(db: AsyncSession, model_id: int) -> Model:
    model = await db.get(Model, model_id)
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"model {model_id} not found")
    return model


async def get_model_by_slug(db: AsyncSession, slug: str) -> Model:
    stmt = (
        select(Model)
        .where(Model.slug == slug)
        .options(
            selectinload(Model.tags),
            selectinload(Model.category),
            selectinload(Model.project),
        )
    )
    model = (await db.execute(stmt)).scalar_one_or_none()
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"model {slug!r} not found")
    return model


async def patch_model(
    db: AsyncSession, backend: StorageBackend, model: Model, changes: dict[str, object]
) -> Model:
    """Apply ``changes`` (already ``exclude_unset``-filtered by the caller).

    ``name`` never touches ``slug``/on-disk directories in M1 (Task 5 brief),
    but DOES rewrite the ``.3dmm.json`` sidecar's ``name`` field (M3 carried
    backlog item: the sidecar used to go stale after a rename). ``review_state``
    is accepted here too so the UI can clear an adopted flag (M3 scanner note).
    Pre-validates ``cover_blob_hash`` if provided: must exist in blobs table.
    """
    # Pre-validate cover_blob_hash before applying changes
    if "cover_blob_hash" in changes:
        new_hash = changes["cover_blob_hash"]
        if new_hash is not None:  # None clears the cover; only validate non-None values
            blob = await db.get(Blob, new_hash)
            if blob is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, detail="unknown cover_blob_hash"
                )

    if "category_id" in changes:
        new_category_id = changes["category_id"]
        if new_category_id is not None:  # None clears the category; only validate non-None
            category = await db.get(Category, new_category_id)
            if category is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, detail="unknown category_id"
                )

    if "project_id" in changes:
        new_project_id = changes["project_id"]
        if new_project_id is not None:  # None clears the project; only validate non-None
            project = await db.get(Project, new_project_id)
            if project is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, detail="unknown project_id"
                )

    if "print_status" in changes:
        new_status = changes["print_status"]
        if new_status is not None and new_status not in (
            "idle",
            "to_print",
            "printing",
            "printed",
            "finishing",
            "failed",
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid print_status {new_status!r}"
            )

    if "quantity_target" in changes:
        target = changes["quantity_target"]
        if target is not None and (not isinstance(target, int) or target < 1):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail="quantity_target must be >= 1"
            )

    if "quantity_printed" in changes:
        printed = changes["quantity_printed"]
        if printed is not None and (not isinstance(printed, int) or printed < 0):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail="quantity_printed must be >= 0"
            )

    new_name = changes.get("name")
    name_changing = "name" in changes and new_name != model.name
    if name_changing:
        # Storage side effect before the commit (module docstring's ordering
        # rule): if the sidecar write fails, the request fails and nothing
        # about it is committed.
        await anyio.to_thread.run_sync(
            layout.write_sidecar, backend, model.id, model.slug, new_name
        )

    for field in (
        "name",
        "description",
        "cover_blob_hash",
        "review_state",
        "favorite",
        "is_archived",
        "category_id",
        "project_id",
        "print_status",
        "quantity_target",
        "quantity_printed",
        "print_tips",
    ):
        if field in changes:
            setattr(model, field, changes[field])
    # R13c: API field `metadata` maps to the `metadata_json` column
    # (`metadata` is reserved on `Base`).
    if "metadata" in changes:
        model.metadata_json = changes["metadata"]
    await db.commit()
    refresh_fields = []
    if "category_id" in changes:
        refresh_fields.append("category")
    if "project_id" in changes:
        refresh_fields.append("project")
    if refresh_fields:
        await db.refresh(model, refresh_fields)
    return model


async def hard_delete_model(
    db: AsyncSession, backend: StorageBackend, settings: Settings, model: Model
) -> None:
    """``DELETE /models/{slug}`` (feat/import-fidelity T3): a REAL delete,
    replacing the old soft-delete-only behavior (soft-delete is now `PATCH
    {"is_archived": true}`, see ``patch_model`` above).

    Every revision's files are physically destroyed FIRST -- the object on
    its own primary backend AND every replica recorded in ``file_locations``
    (Workstream C multi-backend storage: ``store_to_backend``/
    ``relocate_model_storage`` both keep a ``file_locations`` row for the
    PRIMARY location too, not only replicas -- see
    ``app.tasks.ingest.store_to_backend`` -- so walking that table alone
    already reaches every physical copy) -- plus the model's ``.3dmm.json``
    sidecar. THEN the ``Model`` row itself is deleted: DB ``ON DELETE
    CASCADE`` takes revisions/files/prints/print_queue/notes/tags with it
    (``Model.favorite`` is a plain column, not a joined table, so it just
    disappears with the row), and ``imports.model_id``'s ``ON DELETE SET
    NULL`` frees ``(site, external_id)`` for a future re-import of the same
    remote model (see ``app.services.import_dedup``'s module docstring).

    409s -- nothing is deleted -- if ANY file across ANY revision still has
    a ``store_to_backend`` job in flight (``_file_store_pending``'s idiom,
    reused verbatim from ``delete_file``): a delete racing an in-flight
    store could leak the object that job is about to write, with no row
    left afterward to ever notice it.

    Revision/model directories are deliberately left behind once empty --
    ``StorageBackend`` (``app.storage.base``) exposes no ``rmdir``/
    directory-delete operation, and adding one is out of this task's scope.

    Orphaned ``Blob`` rows are left alone on purpose (physical storage is
    layout-addressed per file, not garbage-collected by blob reference count
    -- the storage explorer already tolerates a blob hash with no
    referencing file) -- no GC here, per the T3 brief.
    """
    files = list(
        (
            await db.execute(
                select(File)
                .join(Revision, File.revision_id == Revision.id)
                .where(Revision.model_id == model.id)
            )
        ).scalars()
    )
    for file in files:
        if await _file_store_pending(db, file):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "a file is still processing; retry once stored",
            )

    def _delete_sidecar() -> None:
        with contextlib.suppress(StorageKeyNotFound):
            backend.delete(layout.sidecar_key(model.slug))

    if files:
        file_ids = [f.id for f in files]
        locations = list(
            (
                await db.execute(select(FileLocation).where(FileLocation.file_id.in_(file_ids)))
            ).scalars()
        )
        storage_path_by_file_id = {f.id: f.storage_path for f in files}

        # Resolve every backend instance up front (async, off the DB) so the
        # actual deletes below can run as one synchronous batch, off the
        # event loop thread (`anyio.to_thread.run_sync`, per
        # `app.storage.base`'s threading rule).
        primary_backend_by_file_id: dict[int, StorageBackend] = {}
        for file in files:
            primary_backend_by_file_id[file.id] = await resolve_backend_for_file(db, settings, file)
        location_backend_by_id: dict[int, StorageBackend] = {}
        for loc in locations:
            if loc.backend_id not in location_backend_by_id:
                location_backend_by_id[loc.backend_id] = await backend_for_id(
                    db, settings, loc.backend_id
                )

        def _delete_all() -> None:
            for file in files:
                with contextlib.suppress(StorageKeyNotFound):
                    primary_backend_by_file_id[file.id].delete(file.storage_path)
            for loc in locations:
                storage_path = storage_path_by_file_id[loc.file_id]
                with contextlib.suppress(StorageKeyNotFound):
                    location_backend_by_id[loc.backend_id].delete(storage_path)
            _delete_sidecar()

        await anyio.to_thread.run_sync(_delete_all)
    else:
        await anyio.to_thread.run_sync(_delete_sidecar)

    await db.execute(sa_delete(Model).where(Model.id == model.id))
    await db.commit()


async def merge_models(
    db: AsyncSession,
    backend: StorageBackend,
    settings: Settings,
    target: Model,
    sources: list[Model],
) -> Model:
    """Merge one or more source models into target.
    Transfers/copies all files to target's current revision,
    merges tags, adopts cover if target has none, and cleanly
    deletes source models.
    """
    target_rev_id = target.current_revision_id
    if target_rev_id is None:
        rev_stmt = (
            select(Revision).where(Revision.model_id == target.id).order_by(Revision.number.desc())
        )
        rev = (await db.execute(rev_stmt)).scalars().first()
        if rev is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Target model has no revision")
        target_rev_id = rev.id
        target.current_revision_id = target_rev_id

    target_rev = await db.get(Revision, target_rev_id)
    if target_rev is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Target revision not found")

    target_files = list(
        (await db.execute(select(File).where(File.revision_id == target_rev.id))).scalars()
    )
    existing_paths = {f.rel_path: f for f in target_files}

    copied_keys: list[str] = []
    for source in sources:
        if source.id == target.id:
            continue

        source_files = list(
            (
                await db.execute(
                    select(File)
                    .join(Revision, File.revision_id == Revision.id)
                    .where(Revision.model_id == source.id)
                )
            ).scalars()
        )

        for sf in source_files:
            dest_rel_path = sf.rel_path
            if dest_rel_path in existing_paths:
                existing = existing_paths[dest_rel_path]
                if existing.blob_hash == sf.blob_hash:
                    continue
                # Disambiguate filename
                if dest_rel_path.lower().endswith(".gcode.3mf"):
                    stem = dest_rel_path[:-10]
                    dest_rel_path = f"{stem}_{source.slug[:6]}.gcode.3mf"
                else:
                    p = Path(dest_rel_path)
                    dest_rel_path = f"{p.stem}_{source.slug[:6]}{p.suffix}"

            dest_storage_key = layout.file_key(target.slug, target_rev.dir_name, dest_rel_path)
            try:
                await anyio.to_thread.run_sync(
                    lambda src=sf.storage_path, dst=dest_storage_key: backend.copy(src, dst)
                )
                copied_keys.append(dest_storage_key)
            except Exception as exc:
                for key in copied_keys:
                    with contextlib.suppress(Exception):
                        await anyio.to_thread.run_sync(lambda k=key: backend.delete(k))
                # Never fall back to the source's storage_path: the source is
                # hard-deleted below and would take the target's bytes with it.
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    f"Failed to copy '{sf.rel_path}' to the target model",
                ) from exc

            new_file = File(
                revision_id=target_rev.id,
                blob_hash=sf.blob_hash,
                rel_path=dest_rel_path,
                storage_path=dest_storage_key,
                backend_id=sf.backend_id,
                verified_at=func.now(),
            )
            db.add(new_file)
            existing_paths[dest_rel_path] = new_file

        # Merge tags
        target_tags = {t.name for t in target.tags}
        for t in source.tags:
            if t.name not in target_tags:
                target.tags.append(t)
                target_tags.add(t.name)

        # Adopt cover if target has none
        if not target.cover_blob_hash and source.cover_blob_hash:
            target.cover_blob_hash = source.cover_blob_hash

        # Delete source model cleanly
        await hard_delete_model(db, backend, settings, source)

    await anyio.to_thread.run_sync(
        lambda: layout.write_sidecar(backend, target.id, target.slug, target.name)
    )
    await db.commit()
    await db.refresh(target)
    return target


async def bulk_hard_delete_models(
    db: AsyncSession,
    backend: StorageBackend,
    settings: Settings,
    *,
    ids: list[int],
) -> int:
    """``POST /models/bulk-delete`` (Round 11 Task 1): hard-delete every
    model in ``ids`` in one call, reusing ``hard_delete_model`` per model.

    Every id is validated to exist BEFORE any deletion runs, so an unknown id
    404s with NOTHING deleted -- same validate-before-mutate posture as
    ``bulk_update_models`` above. Pending ``store_to_backend`` jobs are
    likewise pre-checked across the WHOLE batch, via one query joining
    ``File`` -> ``Revision`` for every id in the batch, BEFORE any model is
    touched: if ANY model in the selection has a file still processing, the
    whole batch 409s with nothing deleted, rather than leaving it
    half-deleted at whichever model happens to hit ``hard_delete_model``'s
    own (per-model) guard first. ``hard_delete_model`` re-checks per model
    too -- that's fine, redundant but harmless; this batch pre-check is what
    makes the common failure atomic across the whole selection.

    ``hard_delete_model`` commits per model, which expires every ORM object
    still tracked by the session -- including the OTHER not-yet-processed
    models in this batch. That's harmless (SQLAlchemy just re-SELECTs them
    lazily on next attribute access), but a model already deleted in an
    earlier loop iteration must never be touched again afterward -- its row
    is gone, so a refresh attempt would raise ``ObjectDeletedError``. The
    loop below satisfies that by construction: each id is looked up and
    handed to ``hard_delete_model`` exactly once, moving strictly forward.
    """
    unique_ids = list(dict.fromkeys(ids))  # de-dupe, preserve order
    if not unique_ids:
        return 0

    models_by_id = {
        m.id: m for m in (await db.execute(select(Model).where(Model.id.in_(unique_ids)))).scalars()
    }
    missing = [i for i in unique_ids if i not in models_by_id]
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"model(s) not found: {sorted(missing)}")

    files_with_model_id = (
        await db.execute(
            select(File, Revision.model_id)
            .join(Revision, File.revision_id == Revision.id)
            .where(Revision.model_id.in_(unique_ids))
        )
    ).all()
    blocked: set[int] = set()
    for file, model_id in files_with_model_id:
        if await _file_store_pending(db, file):
            blocked.add(model_id)
    if blocked:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"model(s) have files still processing: {sorted(blocked)}",
        )

    for model_id in unique_ids:
        await hard_delete_model(db, backend, settings, models_by_id[model_id])

    return len(unique_ids)


def check_redownload_source(model: Model) -> SiteImporter:
    """409 unless ``model`` still has a resolvable import source: both
    ``source_site``/``source_url`` set, AND that site's registered importer
    still canonicalizes ``source_url`` to an id (reuses the SAME registry +
    ``canonicalize`` seam ``app.services.imports.start_import`` uses to
    validate a fresh import's URL). Called by ``POST
    /models/{slug}/redownload`` BEFORE a job row is ever created. Returns
    the resolved importer as a convenience for a caller that also needs it,
    though ``app.tasks.importing.redownload_model`` re-resolves its own copy
    independently (a different process, possibly much later).
    """
    if not model.source_site or not model.source_url:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "model has no import source to re-download from"
        )
    try:
        site = ImportSite(model.source_site)
    except ValueError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"unrecognized source site {model.source_site!r}"
        ) from None
    importer = get_importer(site)
    if importer is None or importer.canonicalize(model.source_url) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "model's source URL is no longer importable")
    return importer


async def _get_or_create_tag_id(db: AsyncSession, name: str) -> int:
    """Get-or-create by name, returning just the id. Twin of the get-or-create
    half of ``add_tag_to_model``, but never commits -- ``bulk_update_models``
    (below) needs the id only, and commits once for the whole batch rather
    than once per tag.
    """
    tag_id = await db.scalar(select(Tag.id).where(Tag.name == name))
    if tag_id is not None:
        return tag_id
    tag = Tag(name=name)
    db.add(tag)
    await db.flush()
    return tag.id


async def bulk_update_models(
    db: AsyncSession,
    *,
    ids: list[int],
    add_tags: list[str] | None,
    remove_tags: list[str] | None,
    favorite: bool | None,
    project_id: int | None = None,
    print_status: str | None = None,
) -> int:
    """``POST /models/bulk`` (Branch 4 Task 1): apply the same tag/favorite/project
    changes to every id in one go. Every id is validated to exist BEFORE any
    mutation runs, so an unknown id 404s with NOTHING applied.
    """
    unique_ids = list(dict.fromkeys(ids))  # de-dupe, preserve order
    models_by_id = {
        m.id: m for m in (await db.execute(select(Model).where(Model.id.in_(unique_ids)))).scalars()
    }
    missing = [i for i in unique_ids if i not in models_by_id]
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"model(s) not found: {missing}")

    touched_ids: set[int] = set()

    if add_tags:
        tag_ids = [await _get_or_create_tag_id(db, name) for name in add_tags]
        existing_pairs = set(
            (
                await db.execute(
                    select(model_tags.c.model_id, model_tags.c.tag_id).where(
                        model_tags.c.model_id.in_(unique_ids),
                        model_tags.c.tag_id.in_(tag_ids),
                    )
                )
            ).all()
        )
        new_links = [
            {"model_id": model_id, "tag_id": tag_id}
            for model_id in unique_ids
            for tag_id in tag_ids
            if (model_id, tag_id) not in existing_pairs
        ]
        if new_links:
            await db.execute(model_tags.insert(), new_links)
            touched_ids.update(link["model_id"] for link in new_links)

    if remove_tags:
        remove_tag_ids = list(
            (await db.execute(select(Tag.id).where(Tag.name.in_(remove_tags)))).scalars()
        )
        if remove_tag_ids:
            result = await db.execute(
                model_tags.delete()
                .where(
                    model_tags.c.model_id.in_(unique_ids),
                    model_tags.c.tag_id.in_(remove_tag_ids),
                )
                .returning(model_tags.c.model_id)
            )
            touched_ids.update(result.scalars().all())

    if favorite is not None:
        for model_id in unique_ids:
            models_by_id[model_id].favorite = favorite

    if project_id is not None:
        if project_id <= 0:
            for model_id in unique_ids:
                models_by_id[model_id].project_id = None
                touched_ids.add(model_id)
        else:
            project = await db.get(Project, project_id)
            if project is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"project {project_id} not found")
            for model_id in unique_ids:
                models_by_id[model_id].project_id = project_id
                touched_ids.add(model_id)

    if print_status is not None:
        if print_status not in ("idle", "to_print", "printing", "printed", "finishing", "failed"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"invalid print_status {print_status!r}",
            )
        for model_id in unique_ids:
            models_by_id[model_id].print_status = print_status
            touched_ids.add(model_id)

    # Backlog fold: see `finalize_upload`'s matching comment -- neither a
    # model_tags-only insert/delete nor a same-value `favorite` assignment
    # (SQLAlchemy skips the UPDATE entirely when nothing actually changed)
    # otherwise issues an UPDATE against `models`. Only models whose tag
    # membership actually changed get bumped here.
    for model_id in touched_ids:
        models_by_id[model_id].updated_at = func.now()

    await db.commit()
    return len(unique_ids)


def _escape_like(value: str) -> str:
    """Escape ``\\``, ``%``, ``_`` so a user's ``q`` is matched literally by
    ``ILIKE`` rather than as a wildcard pattern (Task 7 backlog fold) -- e.g.
    a search for ``"100%"`` must not incidentally match every row containing
    plain ``"100"``. Paired with ``escape="\\\\"`` on the ``ilike()`` calls
    below.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Phase 6: `best_slicer_file` priority -- lower wins; ties broken by
# `rel_path` (see `_gallery_aggregates`).
_SLICER_FORMAT_PRIORITY = {
    BlobFormat.THREEMF: 0,
    BlobFormat.STEP: 1,
    BlobFormat.OBJ: 2,
    BlobFormat.STL: 3,
    BlobFormat.IGES: 4,
    BlobFormat.GCODE_3MF: 5,
    BlobFormat.GCODE: 6,
}


@dataclass(slots=True)
class _GalleryAggregate:
    """Per-(current-revision) batch of gallery fields, computed for a whole
    page at once (Task 7 brief: "FIXED number of queries per page, never
    per-model/per-file").
    """

    file_count: int
    formats: list[BlobFormat]
    has_sliced: bool
    print_time_s: int | None
    assembly_ok: bool
    first_ok_thumb_blob_hash: str | None
    # Phase 6: card-level dims + file picks.
    dims_mm: list[float] | None
    best_slicer_file: FileOut | None
    printable_file: FileOut | None


async def _gallery_aggregates(
    db: AsyncSession, settings: Settings, page_models: list[Model]
) -> tuple[dict[int, _GalleryAggregate], set[str]]:
    """``({revision_id: _GalleryAggregate}, {ok-thumb cover_blob_hash})`` for
    ``page_models``'s current revisions -- four queries total for the whole
    page (file/format/print-time/thumb-ok join, assembly-thumb-ok set,
    cover-blob-ok set, and ONE batched meta-enrichment query keyed by the
    DISTINCT ``blob_hash`` set of the ``best_slicer_file``/``printable_file``
    picks -- Phase 6 review fix: the Send-to-printer dialog needs
    ``printable_file.meta.plates`` to pick a plate, not just the bare
    ``FileOut``), never one per model or per file.
    """
    revision_ids = [m.current_revision_id for m in page_models if m.current_revision_id is not None]
    if not revision_ids:
        return {}, set()

    rows = (
        await db.execute(
            select(
                File.revision_id,
                File.id,
                File.rel_path,
                File.storage_path,
                File.blob_hash,
                File.mtime,
                File.verified_at,
                Blob.format,
                Blob.kind,
                Blob.size,
                BlobMeta.print_time_s,
                BlobMeta.dims_mm,
                BlobMeta.volume_cm3,
                Derivative.id,
            )
            .join(Blob, Blob.hash == File.blob_hash)
            .outerjoin(BlobMeta, BlobMeta.blob_hash == File.blob_hash)
            .outerjoin(
                Derivative,
                (Derivative.blob_hash == File.blob_hash)
                & (Derivative.kind == DerivativeKind.THUMB_256)
                & (Derivative.status == DerivativeStatus.OK),
            )
            .where(File.revision_id.in_(revision_ids))
        )
    ).all()

    buckets: dict[int, dict] = {}
    for (
        revision_id,
        file_id,
        rel_path,
        storage_path,
        blob_hash,
        mtime,
        verified_at,
        fmt,
        kind,
        size,
        print_time_s,
        dims_mm,
        volume_cm3,
        thumb_ok_id,
    ) in rows:
        bucket = buckets.setdefault(
            revision_id,
            {
                "file_ids": set(),
                "formats": set(),
                "print_times": [],
                "thumb_files": [],
                "slicer_candidates": [],
                "printable_candidates": [],
                "mesh_candidates": [],
            },
        )
        bucket["file_ids"].add(file_id)
        bucket["formats"].add(fmt)
        if print_time_s is not None:
            bucket["print_times"].append(print_time_s)
        # Internal snapshot files (the cover-image snapshot -- R13a review
        # fix) must never win the "first ok thumb by rel_path" gallery
        # fallback below -- `_snapshots/` sorts first, which would make a
        # user-set cover eclipse every uploaded model file's own thumb.
        if not layout.is_snapshot_path(rel_path):
            bucket["thumb_files"].append((rel_path, blob_hash, thumb_ok_id is not None))

        file_row = {
            "id": file_id,
            "revision_id": revision_id,
            "rel_path": rel_path,
            "storage_path": storage_path,
            "blob_hash": blob_hash,
            "mtime": mtime,
            "verified_at": verified_at,
            "format": fmt,
            "kind": kind,
            "size": size,
            "dims_mm": dims_mm,
        }
        if verified_at is not None and fmt in _SLICER_FORMAT_PRIORITY:
            bucket["slicer_candidates"].append(file_row)
        if fmt == BlobFormat.GCODE_3MF:
            bucket["printable_candidates"].append(file_row)
        if kind == BlobKind.MESH and dims_mm is not None:
            bucket["mesh_candidates"].append((volume_cm3 or 0.0, file_row))

    assembly_ok_revision_ids = set(
        (
            await db.execute(
                select(AssemblyThumb.revision_id).where(
                    AssemblyThumb.revision_id.in_(revision_ids),
                    AssemblyThumb.status == DerivativeStatus.OK,
                )
            )
        ).scalars()
    )

    cover_hashes = {m.cover_blob_hash for m in page_models if m.cover_blob_hash is not None}
    cover_ok_hashes: set[str] = set()
    if cover_hashes:
        cover_ok_hashes = set(
            (
                await db.execute(
                    select(Derivative.blob_hash).where(
                        Derivative.blob_hash.in_(cover_hashes),
                        Derivative.kind == DerivativeKind.THUMB_256,
                        Derivative.status == DerivativeStatus.OK,
                    )
                )
            ).scalars()
        )

    # Resolve each bucket's file picks first so the DISTINCT blob_hash set
    # across the whole page can be enriched with ONE extra query, rather
    # than reaching for `.meta` per-bucket.
    best_slicer_by_revision: dict[int, dict | None] = {}
    printable_by_revision: dict[int, dict | None] = {}
    pick_blob_hashes: set[str] = set()
    for revision_id, bucket in buckets.items():
        best_slicer_row = None
        if bucket["slicer_candidates"]:
            best_slicer_row = min(
                bucket["slicer_candidates"],
                key=lambda r: (_SLICER_FORMAT_PRIORITY[r["format"]], r["rel_path"]),
            )
            pick_blob_hashes.add(best_slicer_row["blob_hash"])
        best_slicer_by_revision[revision_id] = best_slicer_row

        printable_row = None
        if bucket["printable_candidates"]:
            printable_row = max(bucket["printable_candidates"], key=lambda r: r["id"])
            pick_blob_hashes.add(printable_row["blob_hash"])
        printable_by_revision[revision_id] = printable_row

    pick_enrichments: dict[str, FileEnrichment] = {}
    if pick_blob_hashes:
        pick_blobs = (
            (
                await db.execute(
                    select(Blob)
                    .where(Blob.hash.in_(pick_blob_hashes))
                    .options(selectinload(Blob.meta), selectinload(Blob.derivatives))
                )
            )
            .scalars()
            .all()
        )
        pick_enrichments = await _build_file_enrichments(settings, pick_blobs)

    aggregates: dict[int, _GalleryAggregate] = {}
    for revision_id, bucket in buckets.items():
        first_ok_thumb = next(
            (
                blob_hash
                for _, blob_hash, ok in sorted(bucket["thumb_files"], key=lambda t: t[0])
                if ok
            ),
            None,
        )

        best_slicer_row = best_slicer_by_revision[revision_id]
        best_slicer_file = (
            _gallery_file_out(best_slicer_row, pick_enrichments.get(best_slicer_row["blob_hash"]))
            if best_slicer_row
            else None
        )

        printable_row = printable_by_revision[revision_id]
        printable_file = (
            _gallery_file_out(printable_row, pick_enrichments.get(printable_row["blob_hash"]))
            if printable_row
            else None
        )

        if best_slicer_row is not None and best_slicer_row["dims_mm"] is not None:
            dims_mm = best_slicer_row["dims_mm"]
        elif bucket["mesh_candidates"]:
            dims_mm = max(bucket["mesh_candidates"], key=lambda t: t[0])[1]["dims_mm"]
        else:
            dims_mm = None

        aggregates[revision_id] = _GalleryAggregate(
            file_count=len(bucket["file_ids"]),
            formats=sorted(bucket["formats"]),
            has_sliced=bool(bucket["print_times"]),
            print_time_s=min(bucket["print_times"]) if bucket["print_times"] else None,
            assembly_ok=revision_id in assembly_ok_revision_ids,
            first_ok_thumb_blob_hash=first_ok_thumb,
            dims_mm=dims_mm,
            best_slicer_file=best_slicer_file,
            printable_file=printable_file,
        )
    return aggregates, cover_ok_hashes


def _gallery_file_out(row: dict, enrichment: FileEnrichment | None = None) -> FileOut:
    """Build a ``FileOut`` for ``ModelSummary.best_slicer_file``/
    ``printable_file`` straight from the ``_gallery_aggregates`` batch query
    row, plus ``enrichment`` (Phase 6 review fix) resolved by ONE extra
    batched query keyed by the DISTINCT blob_hash set of just these picks --
    keeping the gallery page's query count fixed rather than growing with
    the page size.
    """
    return FileOut(
        id=row["id"],
        revision_id=row["revision_id"],
        rel_path=row["rel_path"],
        storage_path=row["storage_path"],
        blob_hash=row["blob_hash"],
        size=row["size"],
        format=row["format"],
        kind=row["kind"],
        mtime=row["mtime"],
        verified_at=row["verified_at"],
        meta=enrichment.meta if enrichment else None,
        thumb_ready=enrichment.thumb_ready if enrichment else False,
        glb_status=enrichment.glb_status if enrichment else None,
        glb_preview_ready=enrichment.glb_preview_ready if enrichment else False,
    )


def _gallery_cover_url(
    model: Model, aggregate: _GalleryAggregate | None, cover_ok_hashes: set[str]
) -> str | None:
    """Cover priority chain (Task 7 interface decision): the model's own
    ``cover_blob_hash`` (if its thumb is ready) beats the revision's
    assembly thumbnail (if ready) beats the first (by ``rel_path``) file
    with a ready thumb; ``None`` if nothing is ready yet.
    """
    if model.cover_blob_hash is not None and model.cover_blob_hash in cover_ok_hashes:
        return f"/api/blobs/{model.cover_blob_hash}/thumb?size=256"
    if aggregate is not None and aggregate.assembly_ok:
        return f"/api/revisions/{model.current_revision_id}/assembly-thumb"
    if aggregate is not None and aggregate.first_ok_thumb_blob_hash is not None:
        return f"/api/blobs/{aggregate.first_ok_thumb_blob_hash}/thumb?size=256"
    return None


def _gallery_render_url(model: Model, aggregate: _GalleryAggregate | None) -> str | None:
    """The revision's own assembly-thumbnail render URL alone (T2), when its
    derivative is OK -- deliberately NOT the ``_gallery_cover_url`` priority
    chain above, which may prefer ``cover_blob_hash`` (a site cover image, or
    a user-picked file) over the assembly render even when both are ready.
    ``render_url`` always names the render specifically, independent of
    whatever ``cover`` is currently showing; ``None`` until it's ready.
    Reuses the SAME aggregate `_gallery_aggregates` already computed for
    ``cover`` -- no extra query.
    """
    if aggregate is not None and aggregate.assembly_ok:
        return f"/api/revisions/{model.current_revision_id}/assembly-thumb"
    return None


def _get_loaded(obj: object, attr: str) -> object | None:
    try:
        insp = sa_inspect(obj)
        if attr in insp.unloaded:
            return None
        return getattr(obj, attr, None)
    except Exception:
        return None


async def build_model_summaries(
    db: AsyncSession, settings: Settings, models: list[Model]
) -> list[ModelSummary]:
    """Build ``ModelSummary`` rows for an arbitrary list of already-loaded
    ``models`` (Branch 4 Task 1) -- not just one gallery page. Shared by
    ``list_models`` and the print queue's ``GET /queue``
    (``app.services.queue``), which reuses this instead of duplicating the
    aggregate-then-assemble logic. Callers must have ``selectinload``ed
    ``Model.tags`` already.
    """
    aggregates, cover_ok_hashes = await _gallery_aggregates(db, settings, models)

    items = []
    for m in models:
        agg = aggregates.get(m.current_revision_id)
        items.append(
            ModelSummary(
                id=m.id,
                slug=m.slug,
                name=m.name,
                description=m.description,
                tags=[t.name for t in m.tags],
                updated_at=m.updated_at,
                created_at=m.created_at,
                file_count=agg.file_count if agg else 0,
                formats=agg.formats if agg else [],
                cover=_gallery_cover_url(m, agg, cover_ok_hashes),
                render_url=_gallery_render_url(m, agg),
                print_time_s=agg.print_time_s if agg else None,
                has_sliced=agg.has_sliced if agg else False,
                source_site=m.source_site,
                review_state=m.review_state,
                source_collection_id=m.source_collection_id,
                source_collection_title=m.source_collection_title,
                favorite=m.favorite,
                dims_mm=agg.dims_mm if agg else None,
                best_slicer_file=agg.best_slicer_file if agg else None,
                printable_file=agg.printable_file if agg else None,
                category_id=m.category_id,
                category=(
                    ModelCategoryOut(id=cat.id, name=cat.name, color=cat.color)
                    if (cat := _get_loaded(m, "category")) is not None
                    else None
                ),
                project_id=m.project_id,
                project=(
                    ModelProjectOut(
                        id=proj.id,
                        name=proj.name,
                        slug=proj.slug,
                        color=proj.color,
                    )
                    if (proj := _get_loaded(m, "project")) is not None
                    else None
                ),
                print_status=m.print_status,
                quantity_target=m.quantity_target,
                quantity_printed=m.quantity_printed,
                metadata=m.metadata_json,
            )
        )
    return items


async def list_models(
    db: AsyncSession,
    settings: Settings,
    *,
    q: str | None,
    tag: str | None,
    format_: str | None,
    has_sliced: bool | None,
    collection: int | None,
    favorite: bool | None,
    category: int | None,
    project: int | None = None,
    print_status: str | None = None,
    sort: str,
    archived: bool,
    limit: int,
    cursor: str | None,
) -> tuple[list[ModelSummary], str | None]:
    """Gallery query: search/filter/sort + cursor pagination (Task 5
    interface decision; Task 7 adds the ``has_sliced`` filter; Branch 3 Task 1
    adds the ``collection`` filter -- a plain equality on the denormalized
    ``Model.source_collection_id``, no join needed; Branch 4 Task 1 adds the
    ``favorite`` filter -- ``favorite=true`` narrows to starred models,
    ``false``/omitted apply no filter at all (never hides favorites); R13b
    adds the ``category`` filter -- a plain equality on ``Model.category_id``,
    same shape as ``collection``; project and print_status filters support
    project/folder organization and manufacturing workflow tracking).
    """
    is_desc = sort.startswith("-")
    field_name = sort[1:] if is_desc else sort
    sort_field = _SORT_FIELDS.get(field_name)
    if sort_field is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid sort field: {field_name!r}")
    sort_column, parse_cursor_value, extract_sort_value = sort_field

    stmt = select(Model).options(
        selectinload(Model.tags),
        selectinload(Model.category),
        selectinload(Model.project),
    )
    if not archived:
        stmt = stmt.where(Model.is_archived.is_(False))
    if q:
        like = f"%{_escape_like(q)}%"
        stmt = stmt.where(
            or_(
                Model.name.ilike(like, escape="\\"),
                Model.description.ilike(like, escape="\\"),
            )
        )
    if tag:
        stmt = stmt.where(
            Model.id.in_(
                select(model_tags.c.model_id)
                .join(Tag, Tag.id == model_tags.c.tag_id)
                .where(Tag.name == tag)
            )
        )
    if format_:
        stmt = stmt.where(
            Model.current_revision_id.in_(
                select(File.revision_id)
                .join(Blob, Blob.hash == File.blob_hash)
                .where(Blob.format == format_)
            )
        )
    if has_sliced is not None:
        sliced_revision_ids = (
            select(File.revision_id)
            .join(BlobMeta, BlobMeta.blob_hash == File.blob_hash)
            .where(BlobMeta.print_time_s.is_not(None))
        )
        stmt = stmt.where(
            Model.current_revision_id.in_(sliced_revision_ids)
            if has_sliced
            else Model.current_revision_id.not_in(sliced_revision_ids)
        )
    if collection is not None:
        stmt = stmt.where(Model.source_collection_id == collection)
    if favorite:
        stmt = stmt.where(Model.favorite.is_(True))
    if category is not None:
        stmt = stmt.where(Model.category_id == category)
    if project is not None:
        if project <= 0:
            stmt = stmt.where(Model.project_id.is_(None))
        else:
            stmt = stmt.where(Model.project_id == project)
    if print_status:
        stmt = stmt.where(Model.print_status == print_status)

    order_col = sort_column.desc() if is_desc else sort_column.asc()
    order_id = Model.id.desc() if is_desc else Model.id.asc()
    stmt = stmt.order_by(order_col, order_id)

    if cursor:
        cursor_raw, cursor_id = decode_cursor(cursor)
        cursor_value = parse_cursor_value(cursor_raw)
        keyset = tuple_(sort_column, Model.id)
        cursor_tuple = tuple_(cursor_value, cursor_id)
        stmt = stmt.where(keyset < cursor_tuple if is_desc else keyset > cursor_tuple)

    stmt = stmt.limit(limit + 1)
    page_models = list((await db.execute(stmt)).scalars().unique().all())

    has_more = len(page_models) > limit
    page_models = page_models[:limit]

    items = await build_model_summaries(db, settings, page_models)

    next_cursor = None
    if has_more and page_models:
        last = page_models[-1]
        next_cursor = encode_cursor(extract_sort_value(last), last.id)

    return items, next_cursor


# -- notes (shared helper for model/revision details) --------------------


async def _list_notes(
    db: AsyncSession, *, model_id: int | None, revision_id: int | None
) -> list[NoteOut]:
    stmt = select(Note).order_by(Note.created_at)
    if revision_id is not None:
        stmt = stmt.where(Note.revision_id == revision_id)
    else:
        stmt = stmt.where(Note.model_id == model_id, Note.revision_id.is_(None))
    notes = (await db.execute(stmt)).scalars().all()
    return [NoteOut.from_model(n) for n in notes]


# -- revisions --------------------------------------------------------


async def get_revision_or_404(db: AsyncSession, revision_id: int) -> Revision:
    stmt = (
        select(Revision)
        .where(Revision.id == revision_id)
        .options(
            # Two separate paths (not one chained loader) since `Blob.meta`/
            # `Blob.derivatives` are independent relationships off the same
            # `Blob` -- `build_revision_detail`'s `FileOut` enrichment needs
            # both, batched here rather than lazy-loaded per file.
            selectinload(Revision.files).selectinload(File.blob).selectinload(Blob.meta),
            selectinload(Revision.files).selectinload(File.blob).selectinload(Blob.derivatives),
        )
    )
    revision = (await db.execute(stmt)).scalar_one_or_none()
    if revision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"revision {revision_id} not found")
    return revision


async def build_revision_detail(
    db: AsyncSession, revision: Revision, settings: Settings
) -> RevisionDetail:
    notes = await _list_notes(db, model_id=None, revision_id=revision.id)
    sorted_files = sorted(
        (f for f in revision.files if not layout.is_snapshot_path(f.rel_path)),
        key=lambda f: f.rel_path,
    )
    enrichments = await _build_file_enrichments(settings, (f.blob for f in sorted_files))
    files = [FileOut.from_model(f, enrichments.get(f.blob_hash)) for f in sorted_files]
    return RevisionDetail(
        id=revision.id,
        model_id=revision.model_id,
        number=revision.number,
        name=revision.name,
        note=revision.note,
        dir_name=revision.dir_name,
        created_at=revision.created_at,
        files=files,
        notes=notes,
    )


async def newest_printable_files(
    db: AsyncSession, settings: Settings, revision_ids: Sequence[int]
) -> dict[int, FileOut]:
    """``{revision_id: FileOut}`` for the NEWEST (highest ``File.id``) file on
    each of ``revision_ids`` whose blob is a sliced ``.gcode.3mf``
    (``BlobFormat.GCODE_3MF``) -- the print queue's "ready to print" signal
    (``app.services.queue``, Round 8 Task 3). Deliberately independent of
    ``ModelSummary.has_sliced``/``_gallery_aggregates`` (those mean "slicer
    metadata was extracted", a broader condition that a plain ``3mf`` can
    also satisfy -- this is specifically "there's a sendable gcode_3mf").

    One query for every revision in ``revision_ids``, never one per model/
    file; enrichment reuses ``_build_file_enrichments`` -- the SAME loader
    ``build_revision_detail`` uses for the model-detail path -- rather than a
    parallel serializer. A revision with no such file is simply absent from
    the returned dict.
    """
    if not revision_ids:
        return {}
    stmt = (
        select(File)
        .join(Blob, Blob.hash == File.blob_hash)
        .where(
            File.revision_id.in_(revision_ids),
            Blob.format.in_((BlobFormat.GCODE_3MF, BlobFormat.GCODE)),
        )
        .options(
            selectinload(File.blob).selectinload(Blob.meta),
            selectinload(File.blob).selectinload(Blob.derivatives),
        )
        .order_by(File.id.desc())
    )
    files = (await db.execute(stmt)).scalars().all()

    newest_by_revision: dict[int, File] = {}
    for file in files:
        # `ORDER BY File.id DESC` above means the first file seen per
        # revision_id is already the newest -- `setdefault` keeps it and
        # ignores any older one seen later for the same revision.
        newest_by_revision.setdefault(file.revision_id, file)
    if not newest_by_revision:
        return {}

    enrichments = await _build_file_enrichments(
        settings, (file.blob for file in newest_by_revision.values())
    )
    return {
        revision_id: FileOut.from_model(file, enrichments.get(file.blob_hash))
        for revision_id, file in newest_by_revision.items()
    }


async def _model_backends_summary(
    db: AsyncSession, settings: Settings, files: Iterable[File]
) -> list[ModelBackendOut]:
    """Distinct storage backends holding ``files``' bytes (Workstream C task
    C4 UI exposure): each file's PRIMARY backend (``file.backend_id``), NOT
    every backend a file has been replicated onto (``file_locations`` -- a
    ``mode="replicate"`` relocate never changes ``backend_id``, by design;
    see ``app.tasks.relocate``). A NULL ``backend_id`` (pre-migration-seed
    safety net, mirrors ``resolve_backend_for_file``) resolves to the
    current default. One query for every distinct id involved, never one
    per file.
    """
    backend_ids = {f.backend_id for f in files if f.backend_id is not None}
    if any(f.backend_id is None for f in files):
        _, default_id = await resolve_default_backend(db, settings)
        backend_ids.add(default_id)
    if not backend_ids:
        return []
    rows = (
        await db.execute(
            select(StorageBackendRow.id, StorageBackendRow.name)
            .where(StorageBackendRow.id.in_(backend_ids))
            .order_by(StorageBackendRow.id)
        )
    ).all()
    return [ModelBackendOut(id=row.id, name=row.name) for row in rows]


async def build_model_detail(db: AsyncSession, model: Model, settings: Settings) -> ModelDetail:
    notes = await _list_notes(db, model_id=model.id, revision_id=None)
    current_revision = None
    backends: list[ModelBackendOut] = []
    if model.current_revision_id is not None:
        revision = await get_revision_or_404(db, model.current_revision_id)
        current_revision = await build_revision_detail(db, revision, settings)
        backends = await _model_backends_summary(db, settings, revision.files)

    # Branch 5 Task 1: print history aggregates -- one query for both
    # (count + max(printed_at)) rather than two round trips. `func.count`
    # over zero rows is 0, not None; `func.max` over zero rows is None
    # (zero-state: `print_count=0, last_printed_at=None`).
    print_count, last_printed_at = (
        await db.execute(
            select(func.count(Print.id), func.max(Print.printed_at)).where(
                Print.model_id == model.id
            )
        )
    ).one()

    return ModelDetail(
        id=model.id,
        slug=model.slug,
        name=model.name,
        description=model.description,
        source_url=model.source_url,
        source_site=model.source_site,
        source_author=model.source_author,
        source_license=model.source_license,
        source_collection_id=model.source_collection_id,
        source_collection_title=model.source_collection_title,
        imported_at=model.imported_at,
        cover_blob_hash=model.cover_blob_hash,
        is_archived=model.is_archived,
        created_at=model.created_at,
        updated_at=model.updated_at,
        tags=[t.name for t in model.tags],
        current_revision=current_revision,
        notes=notes,
        review_state=model.review_state,
        backends=backends,
        favorite=model.favorite,
        print_count=print_count,
        last_printed_at=last_printed_at,
        category_id=model.category_id,
        category=(
            ModelCategoryOut(
                id=model.category.id, name=model.category.name, color=model.category.color
            )
            if model.category is not None
            else None
        ),
        project_id=model.project_id,
        project=(
            ModelProjectOut(
                id=model.project.id,
                name=model.project.name,
                slug=model.project.slug,
                color=model.project.color,
            )
            if model.project is not None
            else None
        ),
        print_status=model.print_status,
        quantity_target=model.quantity_target,
        quantity_printed=model.quantity_printed,
        metadata=model.metadata_json,
        print_tips=model.print_tips,
    )


async def list_revisions(db: AsyncSession, model: Model) -> list[RevisionSummary]:
    stmt = (
        select(Revision, func.count(File.id))
        .outerjoin(File, File.revision_id == Revision.id)
        .where(Revision.model_id == model.id)
        .group_by(Revision.id)
        .order_by(Revision.number)
    )
    rows = (await db.execute(stmt)).all()
    return [
        RevisionSummary(
            id=r.id,
            model_id=r.model_id,
            number=r.number,
            name=r.name,
            note=r.note,
            dir_name=r.dir_name,
            created_at=r.created_at,
            file_count=count,
        )
        for r, count in rows
    ]


async def create_revision(
    db: AsyncSession,
    backend: StorageBackend,
    model: Model,
    settings: Settings,
    *,
    name: str | None,
    note: str | None,
) -> Revision:
    """Full-snapshot-copy the current revision's files into a new revision
    (SPEC "Storage layer" -> "New revision"): mkdirs -> ``backend.copy()``
    each file, reusing blob hashes (no re-hash) -> insert ``files`` rows ->
    bump ``current_revision_id``.

    Workstream C task C2: ``copy``/``move`` are same-backend-only operations,
    so each old file is copied on ITS OWN primary backend (resolved up
    front, below) rather than the caller's ``backend`` (the API's
    dependency-injected DEFAULT backend -- correct only when every old file
    also happens to live there, e.g. the pre-Workstream-C single-backend
    case). ``backend`` is still used as the mkdirs target for a fresh, empty
    revision (no old files to inherit a backend from).
    """
    max_number = await db.scalar(
        select(func.max(Revision.number)).where(Revision.model_id == model.id)
    )
    next_number = (max_number or 0) + 1
    dir_name = layout.revision_dir_name(next_number, name)

    old_files: list[File] = []
    if model.current_revision_id is not None:
        old_files = list(
            (
                await db.execute(select(File).where(File.revision_id == model.current_revision_id))
            ).scalars()
        )

    # Pre-check BEFORE creating the new revision row or touching disk:
    # `backend.copy()` on a file whose store job hasn't settled raises
    # `StorageKeyNotFound` (the source object may not exist yet), which would
    # otherwise surface as a raw 500 after the new revision directory and
    # however many files had already been copied were left behind as storage
    # debris. Fail the whole snapshot up front instead.
    for old_file in old_files:
        if await _file_store_pending(db, old_file):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "files in the current revision are still processing; retry once stored",
            )

    # Resolve every old file's own primary backend BEFORE creating the new
    # revision row or touching disk (async, off the DB -- `backend_for_id`/
    # `resolve_default_backend` need the session, which the sync thread
    # below can't touch). Grouped by backend id so `_snapshot_copy` mkdirs
    # each backend actually involved exactly once.
    old_file_backend_id: dict[int, int] = {}
    backend_by_id: dict[int, StorageBackend] = {}
    for old_file in old_files:
        if old_file.backend_id is not None:
            bid = old_file.backend_id
            if bid not in backend_by_id:
                backend_by_id[bid] = await backend_for_id(db, settings, bid)
        else:
            # NULL backend_id pre-seed safety net (mirrors
            # `resolve_backend_for_file`) -- default.
            default_backend, bid = await resolve_default_backend(db, settings)
            backend_by_id.setdefault(bid, default_backend)
        old_file_backend_id[old_file.id] = bid

    new_revision = Revision(
        model_id=model.id, number=next_number, name=name, note=note, dir_name=dir_name
    )
    db.add(new_revision)
    await db.flush()

    def _snapshot_copy() -> None:
        if not old_files:
            # Nothing to inherit a backend from -- an empty new revision
            # still needs its directory to exist on the default write
            # backend.
            backend.mkdirs(layout.revision_dir_key(model.slug, dir_name))
            return
        for be in backend_by_id.values():
            be.mkdirs(layout.revision_dir_key(model.slug, dir_name))
        for old_file in old_files:
            be = backend_by_id[old_file_backend_id[old_file.id]]
            new_key = layout.file_key(model.slug, dir_name, old_file.rel_path)
            be.copy(old_file.storage_path, new_key)

    await anyio.to_thread.run_sync(_snapshot_copy)

    new_files: list[File] = []
    for old_file in old_files:
        new_file = File(
            revision_id=new_revision.id,
            blob_hash=old_file.blob_hash,
            rel_path=old_file.rel_path,
            storage_path=layout.file_key(model.slug, dir_name, old_file.rel_path),
            # Unverified until a scan (SPEC "Rescan/reconcile") touches
            # it -- the copy itself is trusted, but nothing has stat'd
            # the resulting file yet.
            verified_at=None,
            backend_id=old_file_backend_id[old_file.id],
        )
        db.add(new_file)
        new_files.append(new_file)

    if new_files:
        await db.flush()  # assigns new_file.id, needed for file_locations below
        for new_file in new_files:
            db.add(
                FileLocation(file_id=new_file.id, backend_id=new_file.backend_id, verified_at=None)
            )

    model.current_revision_id = new_revision.id
    await db.commit()

    # Local import: app.tasks.pipeline imports app.services.jobs (for the
    # mark_*/create_job_sync helpers), so importing it back at module level
    # here would risk a circular import -- same reasoning as
    # `app.services.jobs`'s own local imports of `app.tasks.pipeline`/
    # `app.tasks.ingest`. Every blob in the snapshot is already converted (it
    # was copied from the previous, presumably-processed revision), or the
    # readiness check just declines -- either way this can't fail the
    # request (Task 6 interface decision: best-effort).
    from app.tasks.pipeline import maybe_enqueue_assembly_async

    await maybe_enqueue_assembly_async(db, revision_id=new_revision.id)

    return await get_revision_or_404(db, new_revision.id)


def _diff_side(file: File | None) -> DiffEntrySide | None:
    if file is None:
        return None
    return DiffEntrySide(blob_hash=file.blob_hash, size=file.blob.size)


async def diff_revisions(db: AsyncSession, revision_a_id: int, revision_b_id: int) -> DiffResponse:
    """Full outer join on ``rel_path`` between two revisions of the SAME
    model, comparing ``blob_hash`` (SPEC "Data model": "Revision diff").
    """
    revision_a = await get_revision_or_404(db, revision_a_id)
    revision_b = await get_revision_or_404(db, revision_b_id)
    if revision_a.model_id != revision_b.model_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "revisions belong to different models")

    files_a = {f.rel_path: f for f in revision_a.files}
    files_b = {f.rel_path: f for f in revision_b.files}

    added: list[DiffEntry] = []
    removed: list[DiffEntry] = []
    changed: list[DiffEntry] = []
    unchanged: list[DiffEntry] = []

    for rel_path in sorted(set(files_a) | set(files_b)):
        file_a = files_a.get(rel_path)
        file_b = files_b.get(rel_path)
        entry = DiffEntry(rel_path=rel_path, a=_diff_side(file_a), b=_diff_side(file_b))
        if file_a is None:
            added.append(entry)
        elif file_b is None:
            removed.append(entry)
        elif file_a.blob_hash == file_b.blob_hash:
            unchanged.append(entry)
        else:
            changed.append(entry)

    return DiffResponse(added=added, removed=removed, changed=changed, unchanged=unchanged)


# -- files --------------------------------------------------------------


def _validate_rel_path(rel_path: str) -> None:
    """Reject ``rel_path`` values that would produce an unsafe storage key.

    ``rel_path`` is user input that gets embedded into the file's storage
    key (``<slug>/<dir_name>/<rel_path>``). The storage backend would also
    reject these keys (``LocalStorageBackend._resolve``), but only later,
    inside the Celery task -- by which point a poisoned ``files`` row is
    already committed. Mirror the backend's key rules up front instead:
    no empty/dot paths, no backslashes, no absolute paths, no ``..``.
    """
    pure = PurePosixPath(rel_path)
    if (
        not rel_path
        or "\\" in rel_path
        or pure.is_absolute()
        or pure.parts == ()
        or ".." in pure.parts
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unsafe rel_path: {rel_path!r}")


async def _file_store_pending(db: AsyncSession, file: File) -> bool:
    """Whether ``file``'s ``store_to_backend`` job hasn't settled yet -- i.e.
    its bytes on the storage backend could still change out from under a
    caller. Shared by every operation that touches a file's on-backend bytes
    off the back of a DB read (upload-replace, delete, revision-snapshot
    copy) so they all tell the same 409 story instead of three slightly
    different ones.

    ``verified_at IS NULL`` alone isn't a safe signal: it also stays NULL
    forever after a job permanently fails (e.g. hash mismatch) or is
    superseded, and neither of those has a live job left to race. Only NULL
    ``verified_at`` *combined with* the file's most recent job still being
    queued/running means "something may still write to this key".
    """
    if file.verified_at is not None:
        return False
    latest_state = await db.scalar(
        select(Job.state)
        .where(Job.subject_type == "file", Job.subject_id == file.id)
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    return latest_state in (jobs_service.STATE_QUEUED, jobs_service.STATE_RUNNING)


def file_store_pending_sync(session: SyncSession, file: File) -> bool:
    """SYNC twin of ``_file_store_pending`` for worker-side callers
    (feat/import-fidelity T3's ``app.tasks.importing.redownload_model``
    ``mode="replace"`` pending-job guard) that can't touch the API's async
    engine (see ``app.tasks.base``). Same reasoning as the async version."""
    if file.verified_at is not None:
        return False
    latest_state = session.scalar(
        select(Job.state)
        .where(Job.subject_type == "file", Job.subject_id == file.id)
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    return latest_state in (jobs_service.STATE_QUEUED, jobs_service.STATE_RUNNING)


async def validate_upload_target(
    db: AsyncSession, *, model_id: int, revision_id: int, rel_path: str, replace: bool
) -> tuple[Model, Revision]:
    """Pre-flight checks for ``PUT /api/uploads``, run BEFORE the request
    body is read (Task 6 interface decision: fail fast rather than making
    the client upload bytes for a request that's going to 404/409 anyway).
    """
    _validate_rel_path(rel_path)
    model = await get_model_by_id(db, model_id)
    revision = await db.get(Revision, revision_id)
    if revision is None or revision.model_id != model.id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"revision {revision_id} not found on model {model_id}"
        )
    if model.current_revision_id != revision.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "uploads are only allowed on the model's current revision",
        )
    existing = await db.scalar(
        select(File.id).where(File.revision_id == revision.id, File.rel_path == rel_path)
    )
    if existing is not None and not replace:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"rel_path {rel_path!r} already exists on this revision; pass replace=true",
        )
    return model, revision


async def finalize_upload(
    db: AsyncSession,
    *,
    model: Model,
    revision: Revision,
    rel_path: str,
    blob_hash: str,
    size: int,
    kind: BlobKind,
    format_: BlobFormat,
    replace: bool,
    after_blob_flush: Callable[[], None] | None = None,
) -> File:
    """Upsert the ``Blob`` by hash (dedupe) and create/replace the ``File``
    row once the upload's bytes are fully spooled and hashed (Task 6
    interface decision). The bytes themselves aren't on backend storage yet
    -- ``verified_at`` stays NULL until ``store_to_backend`` (enqueued by the
    caller right after this) succeeds. ``replace=True`` deletes the old
    ``File`` row; ``storage_path`` is rel_path-derived so the new file's
    backend write naturally overwrites the same object regardless of
    content.

    ``after_blob_flush`` (R13a's ``POST /models/{slug}/cover``) runs once
    the ``Blob`` row is guaranteed to exist in this transaction -- a caller
    that wants to point another row's FK at this same blob (e.g.
    ``model.cover_blob_hash``) sets it here rather than before this call,
    so the eventual commit below never races the blob insert (a
    same-transaction ``UPDATE ... SET cover_blob_hash`` issued before the
    referenced ``Blob`` row is flushed 500s on the FK constraint).
    """
    blob = await db.get(Blob, blob_hash)
    if blob is None:
        blob = Blob(hash=blob_hash, size=size, kind=kind, format=format_)
        db.add(blob)
        try:
            await db.flush()
        except IntegrityError:
            # Lost a race with a concurrent upload of the SAME content: the
            # other request's insert of this blob's PK committed in between
            # our `db.get` miss and this flush (Task 6 review finding). The
            # simpler of the two fixes considered (retry the lookup vs. map
            # to a clear 409) -- retrying would need its own error handling
            # for yet another concurrent delete/insert, for a benign,
            # rare-in-practice race. 409 is honest: the client's upload
            # didn't land as a new blob, but the caller can safely just
            # retry the whole upload.
            await db.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"blob {blob_hash!r} is being uploaded concurrently; retry",
            ) from None

    if after_blob_flush is not None:
        after_blob_flush()

    if replace:
        existing = (
            await db.execute(
                select(File).where(File.revision_id == revision.id, File.rel_path == rel_path)
            )
        ).scalar_one_or_none()
        if existing is not None:
            if await _file_store_pending(db, existing):
                # The file being replaced hasn't finished its own store job
                # yet: deleting its row now and dispatching a new job for the
                # same rel_path lets the two jobs' `backend.write`s race each
                # other on disk, with whichever `os.replace` lands last
                # winning regardless of which job the DB says is "verified".
                # Reject outright instead -- the client can retry once the
                # in-flight job settles.
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"rel_path {rel_path!r} is still processing; retry once stored",
                )
            await db.delete(existing)
            await db.flush()

    file = File(
        revision_id=revision.id,
        blob_hash=blob_hash,
        rel_path=rel_path,
        storage_path=layout.file_key(model.slug, revision.dir_name, rel_path),
        verified_at=None,
    )
    db.add(file)
    # Backlog fold: touch the model's `updated_at` so it sorts correctly in
    # the gallery's default `-updated_at` order. The column's own
    # `onupdate=func.now()` only fires when an UPDATE is actually issued for
    # THIS model row -- an upload never otherwise changes any `models`
    # column, so without this explicit touch the row would never get one.
    model.updated_at = func.now()
    try:
        await db.commit()
    except IntegrityError:
        # Lost a race with a concurrent upload to the same (revision_id,
        # rel_path): `validate_upload_target`'s pre-flight check ran before
        # the request body was read, so two concurrent uploads can both pass
        # it and then both reach here (Task 6 review finding). The
        # UniqueConstraint on `files` is the actual source of truth; the
        # loser gets the same 409 detail the pre-flight check would have
        # raised had it lost the race instead of winning it, rather than an
        # unhandled IntegrityError surfacing as a 500.
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"rel_path {rel_path!r} already exists on this revision; pass replace=true",
        ) from None
    await db.refresh(file)
    return file


async def _hard_delete_file_row(
    db: AsyncSession, settings: Settings, *, file: File, model: Model
) -> None:
    """Post-guard body shared by ``delete_file`` and
    ``try_delete_duplicate_copy`` (Round 11 Task 2): resolve ``file``'s own
    primary backend, delete its bytes (tolerating an already-missing
    object), delete the ``File`` row, bump the owning model's
    ``updated_at``, commit, then re-check the revision for an assembly
    render. Callers own the raising/skip-reason guard checks -- this is
    exactly the part that must not diverge between them.

    Workstream C task C2: deletes off ``file``'s OWN primary backend
    (``resolve_backend_for_file``), not the caller's default -- a file
    relocated (or adopted) onto a non-default backend must be deleted from
    where its bytes actually are.
    """
    backend = await resolve_backend_for_file(db, settings, file)
    # Already absent from the backend (e.g. the write never landed, or a
    # previous delete attempt crashed after removing the object but before
    # this commit) is tolerated, not an error: treating it as one would
    # permanently block deleting a `files` row whose object doesn't exist.
    # The DB row is what "should this file exist" actually means here, so a
    # missing backend object is just as good as a successful delete.
    with contextlib.suppress(StorageKeyNotFound):
        await anyio.to_thread.run_sync(backend.delete, file.storage_path)
    revision_id = file.revision_id
    await db.delete(file)
    # Backlog fold: see `finalize_upload`'s matching comment -- deleting a
    # file never otherwise issues an UPDATE against `models`.
    model.updated_at = func.now()
    await db.commit()

    # Local import: breaks the same import cycle as `create_revision`'s call
    # above (see that comment). The revision's composition just changed --
    # re-check whether it's now ready for an assembly render.
    from app.tasks.pipeline import maybe_enqueue_assembly_async

    await maybe_enqueue_assembly_async(db, revision_id=revision_id)


async def delete_file(db: AsyncSession, settings: Settings, file_id: int) -> None:
    """File ops apply only to the model's CURRENT revision (Task 5 brief);
    409 otherwise.
    """
    file = await db.get(File, file_id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"file {file_id} not found")
    revision = await db.get(Revision, file.revision_id)
    assert revision is not None  # FK guarantees this
    model = await db.get(Model, revision.model_id)
    assert model is not None  # FK guarantees this
    if model.current_revision_id != file.revision_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "file belongs to a revision that is not the model's current revision",
        )
    if await _file_store_pending(db, file):
        # Deleting a row whose store job is still in flight would race
        # `store_to_backend`'s own writes -- reject rather than remove a row
        # the ingest task might still be about to touch.
        raise HTTPException(status.HTTP_409_CONFLICT, "file is still processing; retry once stored")

    await _hard_delete_file_row(db, settings, file=file, model=model)


async def try_delete_duplicate_copy(
    db: AsyncSession, settings: Settings, file_id: int
) -> str | None:
    """``resolve_duplicates`` (Round 11 Task 2): same three guard checks as
    ``delete_file``, but return a skip reason instead of raising -- one
    unresolvable copy in a batch (already gone, on a superseded revision, or
    mid-upload) must not abort the copies that ARE safe to delete.

    Returns ``None`` on success (the row is gone); otherwise one of
    ``"not_found"`` / ``"not_current_revision"`` / ``"store_pending"``.
    """
    file = await db.get(File, file_id)
    if file is None:
        return "not_found"
    revision = await db.get(Revision, file.revision_id)
    assert revision is not None  # FK guarantees this
    model = await db.get(Model, revision.model_id)
    assert model is not None  # FK guarantees this
    if model.current_revision_id != file.revision_id:
        return "not_current_revision"
    if await _file_store_pending(db, file):
        return "store_pending"

    await _hard_delete_file_row(db, settings, file=file, model=model)
    return None


# -- tags -------------------------------------------------------------


async def list_tags(db: AsyncSession) -> list[TagOut]:
    tags = (await db.execute(select(Tag).order_by(Tag.name))).scalars().all()
    return [TagOut(id=t.id, name=t.name, color=t.color) for t in tags]


async def add_tag_to_model(
    db: AsyncSession, model_id: int, name: str, color: str | None = None
) -> TagOut:
    """Get-or-create the tag, then associate it with the model (idempotent)."""
    model = await get_model_by_id(db, model_id)

    tag = (await db.execute(select(Tag).where(Tag.name == name))).scalar_one_or_none()
    if tag is None:
        tag = Tag(name=name, color=color)
        db.add(tag)
        await db.flush()

    already_linked = await db.scalar(
        select(model_tags.c.model_id).where(
            model_tags.c.model_id == model_id, model_tags.c.tag_id == tag.id
        )
    )
    if already_linked is None:
        await db.execute(model_tags.insert().values(model_id=model_id, tag_id=tag.id))
        # Backlog fold: see `finalize_upload`'s matching comment -- tagging
        # never otherwise issues an UPDATE against `models`. Only on an
        # actual new link, not the idempotent no-op re-tag.
        model.updated_at = func.now()
    await db.commit()
    return TagOut(id=tag.id, name=tag.name, color=tag.color)


async def set_tag_color(db: AsyncSession, tag_id: int, color: str | None) -> TagOut:
    tag = await db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"tag {tag_id} not found")
    tag.color = color
    await db.commit()
    return TagOut(id=tag.id, name=tag.name, color=tag.color)


async def remove_tag_from_model(db: AsyncSession, model_id: int, name: str) -> None:
    model = await get_model_by_id(db, model_id)

    tag = (await db.execute(select(Tag).where(Tag.name == name))).scalar_one_or_none()
    if tag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"tag {name!r} not found")

    result = await db.execute(
        model_tags.delete().where(model_tags.c.model_id == model_id, model_tags.c.tag_id == tag.id)
    )
    if result.rowcount == 0:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"tag {name!r} is not attached to model {model_id}"
        )
    model.updated_at = func.now()
    await db.commit()


# -- notes (CRUD) -------------------------------------------------------


async def create_note(
    db: AsyncSession, *, model_id: int, revision_id: int | None, body: str
) -> NoteOut:
    model = await get_model_by_id(db, model_id)
    if revision_id is not None:
        revision = await db.get(Revision, revision_id)
        if revision is None or revision.model_id != model.id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"revision {revision_id} not found on model {model_id}",
            )
    note = Note(model_id=model_id, revision_id=revision_id, body=body)
    db.add(note)
    await db.commit()
    return NoteOut.from_model(note)


async def _get_note_or_404(db: AsyncSession, note_id: int) -> Note:
    note = await db.get(Note, note_id)
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"note {note_id} not found")
    return note


async def patch_note(db: AsyncSession, note_id: int, body: str) -> NoteOut:
    note = await _get_note_or_404(db, note_id)
    note.body = body
    await db.commit()
    return NoteOut.from_model(note)


async def delete_note(db: AsyncSession, note_id: int) -> None:
    note = await _get_note_or_404(db, note_id)
    await db.delete(note)
    await db.commit()
