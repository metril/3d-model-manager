"""Resolve a Bambu Studio slicer upload (``POST /api/slicer/intake``, Round 8
Task 4) to a model + file, creating or attaching as needed.

Async/sync twins, house convention (mirrors ``app.services.library``'s
``create_model``/``create_imported_model_sync`` and
``finalize_upload``/``store_imported_file_sync`` pairs): ``resolve_and_attach``
runs in the API's async world, off the raw-body spool-tee ``app.api.slicer``
does exactly like ``app.api.uploads``; ``resolve_and_attach_sync`` is the T5
watcher's sync twin, given an already-staged ``StagedFile`` (mirrors
``app.tasks.importing``'s usage of ``store_imported_file_sync``). Both are
thin wrappers over the SAME pure naming logic
(``app.services.slicer_naming``) and reuse the existing finalize/store seams
rather than duplicating them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession

from app.config import Settings
from app.models.enums import BlobFormat, BlobKind
from app.models.library import File, Model, Revision
from app.services import library
from app.services.layout import infer_blob_kind_format
from app.services.slicer_naming import _safe_basename, model_name_from_filename
from app.storage.base import StorageBackend

if TYPE_CHECKING:
    # Avoids a runtime import cycle, same reasoning as
    # `app.services.library`'s own TYPE_CHECKING-only import of StagedFile.
    from app.importers.download import StagedFile

# A filename that strips down to nothing at all (entirely slicing metadata,
# however unlikely) falls back to this generic name rather than creating a
# model with an empty/blank one.
FALLBACK_MODEL_NAME = "Imported model"

Action = Literal["created", "attached", "replaced"]


class UnsupportedIntake(Exception):
    """The uploaded filename's extension doesn't map to a recognized blob
    kind/format (``infer_blob_kind_format`` -> ``(OTHER, OTHER)``) -- e.g. a
    stray ``notes.txt`` handed to the intake endpoint. ``str(exc)`` is the
    (sanitized, basename-only) filename, safe to surface in a 422 detail.
    """


@dataclass(frozen=True, slots=True)
class IntakeResult:
    model_id: int
    model_name: str
    file_id: int
    blob_hash: str
    size: int
    job_id: uuid.UUID
    action: Action


async def _find_model_by_name_ci(db: AsyncSession, name: str) -> Model | None:
    """Case-insensitive EXACT match on ``Model.name``; the OLDEST matching
    row (lowest id) wins when duplicates somehow exist -- a slicer re-export
    should keep landing on the same, first-ever model rather than bouncing
    between same-named ones.
    """
    stmt = (
        select(Model)
        .where(func.lower(Model.name) == name.lower(), Model.deleted_at.is_(None))
        .order_by(Model.id.asc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _find_model_by_name_ci_sync(session: SyncSession, name: str) -> Model | None:
    stmt = (
        select(Model)
        .where(func.lower(Model.name) == name.lower(), Model.deleted_at.is_(None))
        .order_by(Model.id.asc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _resolve_names(filename: str) -> tuple[str, BlobKind, BlobFormat]:
    """Shared prep for both twins: the sanitized ORIGINAL basename (kept as
    the file's ``rel_path`` -- this is what keeps ``_plate_1``/``_plate_2``
    multi-plate exports of the SAME model distinct on disk), its inferred
    ``(kind, format)``, and the stripped model name to resolve/create
    against. Raises ``UnsupportedIntake`` up front, before anything touches
    the DB or storage, for a filename ``infer_blob_kind_format`` can't
    classify at all.
    """
    rel_path = _safe_basename(filename)
    kind, format_ = infer_blob_kind_format(rel_path)
    if (kind is BlobKind.OTHER and format_ is BlobFormat.OTHER) or kind is BlobKind.DOC:
        raise UnsupportedIntake(rel_path)
    return rel_path, kind, format_


async def resolve_and_attach(
    db: AsyncSession,
    backend: StorageBackend,
    settings: Settings,
    *,
    filename: str,
    spool_token: uuid.UUID,
    spool_path: Path,
    blob_hash: str,
    size: int,
    created_by_user_id: int | None = None,
) -> IntakeResult:
    """Resolve ``filename`` to a model (matched by stripped name, or newly
    created) and finalize the ALREADY-SPOOLED upload onto its current
    revision, dispatching the same ``store_to_backend`` job
    ``PUT /api/uploads`` does. The bytes themselves are not read here --
    the caller (``app.api.slicer``) has already streamed+hashed them to
    ``spool_path``.
    """
    rel_path, kind, format_ = _resolve_names(filename)
    stripped_name = model_name_from_filename(filename) or FALLBACK_MODEL_NAME

    model = await _find_model_by_name_ci(db, stripped_name)
    created = model is None
    if model is None:
        # `commit=False` (M2 fix-review): defers the Model+Revision insert
        # into the SAME transaction `finalize_upload` below commits -- a
        # failure there rolls the just-created model back too, rather than
        # leaving an empty, file-less model durably orphaned.
        model = await library.create_model(
            db,
            backend,
            name=stripped_name,
            description=None,
            initial_revision_name="slicer",
            commit=False,
            created_by_user_id=created_by_user_id,
        )

    revision = await db.get(Revision, model.current_revision_id)
    assert revision is not None  # every model created above/matched here has a current revision

    existing_id = await db.scalar(
        select(File.id).where(File.revision_id == revision.id, File.rel_path == rel_path)
    )
    replace_existing = existing_id is not None
    action: Action = "created" if created else ("replaced" if replace_existing else "attached")

    file = await library.finalize_upload(
        db,
        model=model,
        revision=revision,
        rel_path=rel_path,
        blob_hash=blob_hash,
        size=size,
        kind=kind,
        format_=format_,
        replace=replace_existing,
    )

    from app.services import jobs as jobs_service
    from app.tasks.ingest import store_to_backend

    job = await jobs_service.create_job(
        db, id=spool_token, type="store_to_backend", subject_type="file", subject_id=file.id
    )
    store_to_backend.apply_async(args=[str(job.id), file.id, str(spool_path)], task_id=str(job.id))

    return IntakeResult(
        model_id=model.id,
        model_name=model.name,
        file_id=file.id,
        blob_hash=blob_hash,
        size=size,
        job_id=job.id,
        action=action,
    )


def resolve_and_attach_sync(
    session: SyncSession,
    backend: StorageBackend,
    settings: Settings,
    *,
    filename: str,
    staged: StagedFile,
) -> IntakeResult:
    """SYNC twin of ``resolve_and_attach`` for the T5 watcher, given an
    already-staged file (mirrors ``app.tasks.importing``'s use of
    ``create_imported_model_sync``/``store_imported_file_sync``).

    ``staged.rel_path`` is overridden with the sanitized ORIGINAL basename
    (matching the async path's ``rel_path`` choice) -- the watcher stages
    files under their on-disk name, which is exactly the export filename
    this resolves against.
    """
    rel_path, _kind, _format_ = _resolve_names(filename)
    staged = replace(staged, rel_path=rel_path)
    stripped_name = model_name_from_filename(filename) or FALLBACK_MODEL_NAME

    model = _find_model_by_name_ci_sync(session, stripped_name)
    created = model is None
    if model is None:
        # `commit=False` (M2 fix-review): defers the Model+Revision insert
        # into the SAME transaction `store_imported_file_sync` below
        # commits -- a failure there rolls the just-created model back too,
        # rather than leaving an empty, file-less model durably orphaned.
        model = library.create_imported_model_sync(
            session,
            backend,
            name=stripped_name,
            description=None,
            source_url=None,
            source_site=None,
            source_author=None,
            source_license=None,
            imported_at=None,
            tags=[],
            initial_revision_name="slicer",
            commit=False,
        )

    revision = session.get(Revision, model.current_revision_id)
    assert revision is not None  # every model created above/matched here has a current revision

    existing = session.execute(
        select(File).where(File.revision_id == revision.id, File.rel_path == rel_path)
    ).scalar_one_or_none()
    action: Action = "created" if created else ("replaced" if existing is not None else "attached")

    if existing is not None:
        # Pre-delete-respecting-store-guard, mirroring
        # `app.tasks.importing._store_redownload_in_place`'s pending-job
        # check: reject rather than race an in-flight `store_to_backend` job
        # for the file being replaced.
        if library.file_store_pending_sync(session, existing):
            raise RuntimeError(f"rel_path {rel_path!r} is still processing; retry once stored")
        # M3 fix-review: mirror the async twin's (`finalize_upload`) same-key
        # overwrite ordering -- delete only the ROW here (flushed, not
        # committed, so it folds into `store_imported_file_sync`'s commit
        # below), and NEVER pre-delete the old bytes. `staged.rel_path` is
        # this same `rel_path`, so the new File's `storage_path` is
        # identical to the old one; the store job dispatched below
        # naturally overwrites it in place. Pre-deleting the bytes (as
        # before) opened a data-loss window: if the store step below failed
        # after that delete committed, the old file was gone with no
        # replacement.
        session.delete(existing)
        session.flush()

    file = library.store_imported_file_sync(session, model=model, revision=revision, staged=staged)

    return IntakeResult(
        model_id=model.id,
        model_name=model.name,
        file_id=file.id,
        blob_hash=staged.blob_hash,
        size=staged.size,
        job_id=staged.token,
        action=action,
    )
