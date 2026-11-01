"""``import_from_url`` Celery task (SPEC "Gallery importers"; controller
decision 3). Site-agnostic orchestration over the SiteImporter contract,
in the worker's SYNC world (app.tasks.base):

  (a) FETCHING  -> fetch_metadata + list_files; reject paid/Club/exclusive
                   (metadata.reject_reason) BEFORE any download.
  (b) DOWNLOADING-> stream EVERY file to spool (blake3 each). ALL must succeed,
                   then ``app.importers.archives.process_staged_zips`` sniffs/
                   extracts every staged ``.zip`` (T1: mislabeled-3MF rename
                   in place, or real extraction with the archive discarded)
                   before anything is stored. T2's site cover + gallery
                   images (``_download_gallery_images``) are the ONE
                   exception to "ALL must succeed" -- each is independently
                   best-effort, never fails the import.
  (c) create model+revision (provenance) + per-file finalize/store dispatch
      (T1's files, then T2's gallery images, then ``cover_blob_hash`` set
      from whichever staged image was the cover); set imports.model_id; DONE.

Any failure in (a), (b), OR (c) ⇒ FAILED, model_id NULL, ZERO orphan
Model/Revision/File rows (Global Constraints "IMPORTS ATOMIC"): a failure
partway through (c) -- after Model+Revision are already committed -- deletes
the just-created model (the DB's ON DELETE CASCADE takes its Revision/File
rows with it) rather than leaving it orphaned. A rejected/failed import is a
NORMAL terminal state recorded on the row -- the task swallows the exception
(after marking FAILED) rather than re-raising, so ``POST /imports`` returns
the created row and the client polls its state (in eager test mode the task
runs inline, so this also keeps POST from 500ing on an expected rejection).

**M6 Task 4 (B1) -- re-entry idempotency under `task_acks_late` redelivery:**
a worker hard-killed (SIGKILL/OOM) mid-import never runs the `except` below;
Celery redelivers the same message and a naive reprocess-from-scratch would
create a SECOND model, and -- if the crash landed after phase (c)'s model
commit but before `DONE` -- orphan the first one behind a row stuck
`downloading` forever. Two mechanisms close this (mirrors `app.tasks.scan`'s
singleton lock):

1. A per-import Redis lock (``import_lock_key``) makes a genuinely
   concurrent redelivery (the original attempt still in-flight) a clean
   no-op -- the losing side returns immediately without touching the row.
2. `imp.model_id` is linked immediately after the model is created in
   phase (c) (rather than after the whole per-file store loop), AND in the
   SAME commit as the Model+Revision insert (``create_imported_model_sync``
   is called with ``commit=False`` for exactly this) -- so there is no
   window where a Model is durably committed while the link is not. A crash
   from that point on leaves a *detectable* orphan: the model exists, is
   linked from the import row, but the row's state never reached `DONE`. The
   task's entry guard reconciles against this on every run -- a terminal
   (`DONE`/`FAILED`) row is a no-op (nothing to redo), while a non-terminal
   row with `model_id` already set is exactly that partial-commit crash:
   delete the orphan model (cascade takes its Revision/File rows) and
   reprocess fresh. A crash BEFORE that single commit leaves nothing at all
   (the flushed-but-uncommitted Model/Revision are discarded when the
   session closes without committing). This still upholds the "ZERO
   orphans" invariant above: the same-execution failure handler's orphan
   delete relies on ``imports.model_id``'s ``ON DELETE SET NULL`` to null
   the link as part of that same statement, whether the early link had
   already committed or not."""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import urlparse

import httpx
from redis import Redis
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.importers import download
from app.importers.archives import process_staged_zips
from app.importers.base import ImportFile, SiteImporter
from app.importers.registry import IMPORTER_REGISTRY
from app.models.collections import FollowedCollection
from app.models.enums import ImportSite, ImportState
from app.models.system import Import
from app.services import events, layout, library
from app.services import jobs as jobs_service
from app.services.storage_backends import (
    resolve_backend_for_file_sync,
    resolve_default_backend_sync,
)
from app.storage.errors import StorageKeyNotFound
from app.tasks import base
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# Per-import singleton lock (mirrors `app.tasks.scan.SCAN_LOCK_KEY`): guards
# against a genuinely concurrent redelivery racing the still-in-flight
# original attempt. 1800s comfortably exceeds a realistic import (metadata
# fetch + N file downloads + store) -- if a worker is legitimately still
# running past that, the lock expiring just re-opens the door to the same
# entry-guard reconciliation below, not a silent double-run.
IMPORT_LOCK_TIMEOUT_S = 1800


def import_lock_key(import_id: int) -> str:
    return f"tdmm:import:{import_id}:lock"


class ImportRejected(Exception):
    """Un-importable model (paid/Club/exclusive, or no files) -- recorded as
    a clean FAILED with a human message, never a crash."""


# T2 (site cover + gallery images): at most this many of `meta.image_urls`
# (already cover-first + deduped by the importer) are downloaded per import
# -- a gallery card, not a full mirror of the site's photo album.
MAX_IMPORT_IMAGES = 8

_IMAGE_EXT_FROM_URL_SUFFIX = {"png": "png", "jpg": "jpg", "jpeg": "jpg", "webp": "webp"}


def _image_ext_from_url(url: str) -> str | None:
    """A recognizable image extension straight off the URL's path suffix
    (``.png``/``.jpg``/``.jpeg``/``.webp``, case-insensitive), or ``None`` --
    the caller falls back to sniffing the response's Content-Type instead."""
    suffix = PurePosixPath(urlparse(url).path).suffix.lower().lstrip(".")
    return _IMAGE_EXT_FROM_URL_SUFFIX.get(suffix)


def _image_rel_path_from_response(base: str):
    """Builds the ``rel_path_from_response`` callback ``download.
    stream_remote_to_spool`` calls once a gallery image's response headers
    are in, for a URL whose path suffix alone didn't resolve to a known
    image extension. Raising (unrecognized Content-Type) aborts THIS image's
    download only -- the per-image try/except in the loop below turns that
    into a skip, never an import failure."""

    def _resolve(resp: httpx.Response) -> str:
        ext = download.image_ext_from_content_type(resp.headers.get("content-type"))
        if ext is None:
            raise ValueError(
                f"unrecognized image content-type {resp.headers.get('content-type')!r}"
            )
        return f"{base}.{ext}"

    return _resolve


def _download_gallery_images(
    settings, log_context: str, image_urls: list[str]
) -> tuple[download.StagedFile | None, list[download.StagedFile]]:
    """Best-effort download of up to ``MAX_IMPORT_IMAGES`` unique
    ``image_urls`` (cover-first, per the importer contract) straight to
    spool -- mirrors the per-file download loop above, but ANY single
    image's failure (HTTP error, timeout, unrecognized content type) just
    skips that one image rather than failing the whole import (unlike a
    model's actual files, which must ALL succeed). Returns ``(cover_staged,
    all_staged)``: ``cover_staged`` is set ONLY when ``image_urls[0]``
    itself was among the ones that succeeded -- a failed cover is never
    silently promoted to the next successful image, it just means no
    ``cover_blob_hash`` gets set at all (the existing revision/assembly-thumb
    cover chain still applies).

    ``log_context`` (feat/import-fidelity T3) is a bare human-readable label
    for the per-image warning log line ONLY -- e.g. ``f"import {id}"`` from
    ``import_from_url`` or ``f"redownload model {id}"`` from
    ``redownload_model``, the two callers of this shared step.
    """
    cover_staged: download.StagedFile | None = None
    staged_images: list[download.StagedFile] = []
    unique_urls = list(dict.fromkeys(u for u in image_urls if u))[:MAX_IMPORT_IMAGES]
    for idx, url in enumerate(unique_urls, start=1):
        base = "images/01-cover" if idx == 1 else f"images/{idx:02d}"
        try:
            ext = _image_ext_from_url(url)
            if ext is not None:
                staged_image = download.stream_remote_to_spool(
                    settings, url=url, rel_path=f"{base}.{ext}"
                )
            else:
                staged_image = download.stream_remote_to_spool(
                    settings,
                    url=url,
                    rel_path=f"{base}.img",  # placeholder -- overwritten below
                    rel_path_from_response=_image_rel_path_from_response(base),
                )
        except Exception as exc:  # noqa: BLE001 -- a bad image must never fail the import
            logger.warning(
                "%s: gallery image %d download failed: %s",
                log_context,
                idx,
                type(exc).__name__,
            )
            continue
        staged_images.append(staged_image)
        if idx == 1:
            cover_staged = staged_image
    return cover_staged, staged_images


def _is_auto_import_cover(s, model_id: int, blob_hash: str) -> bool:
    """True when ``blob_hash`` is (still) exactly an auto-set import cover --
    i.e. it matches some ``images/01-cover.*`` file's blob in ANY revision of
    this model, not just the current one (``mode="revision"`` leaves old
    revisions in place, so an old auto cover surviving under an old,
    non-current revision must still read as "auto", not "user-picked").
    Anything else -- including NULL, checked separately by callers -- is
    read as a user-picked cover and must never be silently replaced by a
    redownload's refresh.
    """
    from app.models.library import File, Revision

    return (
        s.execute(
            select(File.id)
            .join(Revision, Revision.id == File.revision_id)
            .where(
                Revision.model_id == model_id,
                File.blob_hash == blob_hash,
                File.rel_path.like("images/01-cover.%"),
            )
            .limit(1)
        ).first()
        is not None
    )


def _should_refresh_cover(s, model, cover_staged: download.StagedFile | None) -> bool:
    """Shared refresh rule for both redownload apply paths (T3 review
    finding): a redownload's freshly-fetched cover overwrites
    ``model.cover_blob_hash`` ONLY when there's a new cover to set AND the
    CURRENT value is either unset or still just the auto-set import cover --
    never a user-picked one. Must be called (and its result captured) BEFORE
    either apply path mutates/deletes any current-revision file: ``mode=
    "replace"`` deletes the old ``images/01-cover.*`` row before storing the
    new one, which would make ``_is_auto_import_cover`` unable to find it
    after the fact.
    """
    if cover_staged is None:
        return False
    if model.cover_blob_hash is None:
        return True
    return _is_auto_import_cover(s, model.id, model.cover_blob_hash)


def _stage_and_process_files(
    settings: Settings,
    importer: SiteImporter,
    external_id: str,
    files: list[ImportFile],
) -> list[download.StagedFile]:
    """Phase (b) core, shared verbatim by ``import_from_url`` and
    (feat/import-fidelity T3) ``redownload_model``: stream EVERY ``files``
    entry to spool -- ALL must succeed, per the module docstring's phase (b)
    contract -- then run T1's zip/3MF intelligence
    (``app.importers.archives.process_staged_zips``) over the result.

    Any single download failure unlinks whatever this call already staged
    before re-raising a SANITIZED error (never the resolved URL itself,
    which can carry a signed token/access code -- see the inline comment
    below) -- so neither caller ever leaks a spool file, nor a secret, on a
    partial download.
    """
    staged: list[download.StagedFile] = []
    try:
        for f in files:
            resolved = importer.resolve_download(external_id, f)
            try:
                staged.append(
                    download.stream_remote_to_spool(
                        settings,
                        url=resolved.url,
                        rel_path=resolved.filename,
                        headers=resolved.headers or None,
                    )
                )
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                # `exc`'s own str() echoes the URL it hit -- for a real
                # importer that's a signed/short-TTL download URL that can
                # carry a token or access code (e.g. Thingiverse's
                # Authorization-bearer-style download links). Re-raise a
                # sanitized domain error naming only the filename + status
                # BEFORE it can reach a job's/import's error text or a log
                # line; `from None` also drops the original exception from
                # this new one's traceback chain, so exc_info on it can't
                # echo the URL back either.
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                detail = f"HTTP {status_code}" if status_code is not None else type(exc).__name__
                raise RuntimeError(
                    f"download failed for {resolved.filename!r} ({detail})"
                ) from None
    except Exception:
        for sf in staged:
            sf.spool_path.unlink(missing_ok=True)
        raise
    return process_staged_zips(settings, staged)


def _merge_tags(meta_tags, extra_tags) -> list[str]:
    """Site tags first, then the extension-supplied ones; case-insensitive dedupe."""
    out: list[str] = []
    seen: set[str] = set()
    for name in [*(meta_tags or ()), *(extra_tags or ())]:
        key = name.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(name.strip())
    return out


def _set_state(session, imp: Import, state: ImportState, *, error: str | None = None) -> None:
    imp.state = state
    if error is not None:
        imp.error = error
    session.commit()
    # Best-effort publish (Task 3 review finding, mirrors app.services.jobs's
    # `_publish`): a Redis blip here must never revert the state change just
    # committed above -- on the DONE path that would orphan an already-created
    # model behind a misleading FAILED row, and on the FAILED path it would
    # re-raise out of the task entirely (a 500 on the eager POST path).
    try:
        events.publish_import_event_sync(get_settings().redis_url, imp.id, state.value)
    except Exception as exc:  # noqa: BLE001 -- publish failures are logged, never propagated
        logger.warning("import event publish failed: %s", type(exc).__name__)


@celery_app.task(name="app.tasks.importing.import_from_url")
def import_from_url(import_id: int) -> None:
    from app.models.library import Model, Revision

    settings = get_settings()
    client = Redis.from_url(settings.redis_url)
    lock = client.lock(import_lock_key(import_id), timeout=IMPORT_LOCK_TIMEOUT_S, blocking=False)
    if not lock.acquire(blocking=False):
        # Another attempt (a genuinely concurrent redelivery) is already
        # inside this import -- no-op rather than racing its fetch/
        # download/store phases (mirrors scan.py's singleton lock).
        logger.info("import %s already being processed (lock held); redelivery ignored", import_id)
        return

    staged: list[download.StagedFile] = []
    staged_images: list[download.StagedFile] = []
    created_model_id: int | None = None
    try:
        with base.sync_session() as s:
            imp = s.get(Import, import_id)
            if imp is None:
                raise LookupError(f"import {import_id} not found")
            if imp.state in (ImportState.DONE, ImportState.FAILED):
                # Redelivery of an already-terminal run -- nothing to redo.
                return
            if imp.model_id is not None:
                # A prior attempt committed a model (the early link in phase
                # (c) below) but died before reaching DONE -- delete that
                # orphan (DB CASCADE takes its Revision/File rows with it)
                # and reprocess fresh rather than duplicate it.
                s.execute(sa_delete(Model).where(Model.id == imp.model_id))
                imp.model_id = None
                s.commit()
            importer = IMPORTER_REGISTRY.get(imp.site)
            if importer is None:
                raise ImportRejected(f"no importer registered for {imp.site}")
            external_id = imp.external_id or ""
            _set_state(s, imp, ImportState.FETCHING)

        meta = importer.fetch_metadata(external_id)
        if meta.reject_reason:
            raise ImportRejected(meta.reject_reason)
        files = importer.list_files(external_id)
        if not files:
            raise ImportRejected("no downloadable files found for this model")

        with base.sync_session() as s:
            imp = s.get(Import, import_id)
            _set_state(s, imp, ImportState.DOWNLOADING)

        # T1 zip/3MF intelligence: MakerWorld's per-print-profile ".zip"
        # downloads are actually mislabeled 3MF containers (renamed in
        # place, no extraction); a genuine zip (e.g. Thingiverse's loose-
        # file `ZipFile.zip`) is extracted member-by-member instead, with
        # the original archive discarded -- either way `staged` below is
        # the FINAL list this import actually stores, which
        # `imp.meta["files"]` (end of this function) then reflects.
        # feat/import-fidelity T3: this download-then-zip-intelligence step
        # is shared verbatim with `redownload_model` below via
        # `_stage_and_process_files`.
        staged = _stage_and_process_files(settings, importer, external_id, files)

        # T2: the site's own cover + gallery photos, best-effort (a bad/
        # missing image never fails the import -- see
        # `_download_gallery_images`'s docstring).
        cover_staged, staged_images = _download_gallery_images(
            settings, f"import {import_id}", meta.image_urls
        )

        with base.sync_session() as s:
            # Workstream C task C2: the model directory + sidecar (this is
            # the only DIRECT storage write in this task -- every imported
            # file's own bytes are written by the shared `store_to_backend`
            # task below, which resolves + stamps the default backend for
            # each File itself) always lands on the DEFAULT backend.
            backend, _default_backend_id = resolve_default_backend_sync(s, settings)
            imp = s.get(Import, import_id)
            # Branch 3 Task 1: carry the followed collection this import came
            # from (AUTO-mode sync, or an approved review item) onto the new
            # model. The collection may have been unfollowed/deleted mid-
            # import -- treat that the same as "no collection" rather than
            # failing the import over it.
            source_collection_id = None
            source_collection_title = None
            if imp.collection_id is not None:
                collection = s.get(FollowedCollection, imp.collection_id)
                if collection is not None:
                    source_collection_id = collection.id
                    source_collection_title = collection.title
            model = library.create_imported_model_sync(
                s,
                backend,
                name=meta.title,
                description=meta.description,
                source_url=meta.source_url,
                source_site=meta.site.value,
                source_author=meta.author,
                source_license=meta.license,
                imported_at=datetime.now(UTC),
                tags=_merge_tags(meta.tags, imp.extra_tags),
                source_collection_id=source_collection_id,
                source_collection_title=source_collection_title,
                initial_revision_name="imported",
                commit=False,
            )
            created_model_id = model.id
            # LINK EARLY, SAME TRANSACTION: `commit=False` above left Model+
            # Revision flushed-but-uncommitted -- this single commit persists
            # them together with `imp.model_id`, so there is no window where
            # the model is durably committed while the link is not. A crash
            # from here on leaves a DETECTABLE orphan (entry guard above); a
            # crash before this commit leaves NOTHING (the flush is rolled
            # back when the session closes without committing).
            imp.model_id = model.id
            s.commit()
            rev = s.get(Revision, model.current_revision_id)
            for sf in staged:
                library.store_imported_file_sync(s, model=model, revision=rev, staged=sf)
            # T2: gallery images land the SAME way as any other imported
            # file (Blob + File in this revision, thumb derivative pipeline
            # dispatched as usual) -- `store_imported_file_sync` is what
            # gets the Blob row that `cover_blob_hash` below points at
            # actually committed, so the FK it sets is always valid.
            for sf in staged_images:
                library.store_imported_file_sync(s, model=model, revision=rev, staged=sf)
            if cover_staged is not None:
                model.cover_blob_hash = cover_staged.blob_hash
            imp = s.get(Import, import_id)
            imp.meta = {
                "cover_url": meta.cover_url,
                "license": meta.license,
                "files": [sf.rel_path for sf in staged],
                "images": len(staged_images),
            }
            _set_state(s, imp, ImportState.DONE)
    except Exception as exc:  # noqa: BLE001 -- failure is a recorded terminal state, not a crash
        for sf in [*staged, *staged_images]:
            sf.spool_path.unlink(missing_ok=True)
        message = str(exc) if isinstance(exc, ImportRejected) else f"import failed: {exc}"
        if not isinstance(exc, ImportRejected):
            # `message` is safe to log by this point -- the one exception
            # type whose str() could carry a URL (the download try/except
            # above) is intercepted before it ever gets here. Logged as
            # type + message (never `exc_info=True`) as defense in depth,
            # mirroring app.printerd/app.tasks.printing's type-only scrub.
            logger.warning("import %s failed: %s: %s", import_id, type(exc).__name__, message)
        with base.sync_session() as s:
            if created_model_id is not None:
                # A failure in phase (c) happened AFTER Model+Revision (and,
                # since the M6 Task 4 early link, possibly `imports.model_id`
                # itself) were already committed -- delete the orphan so this
                # stays atomic (Global Constraints "IMPORTS ATOMIC"). The
                # DB's ON DELETE CASCADE (revisions.model_id, files.
                # revision_id) takes the Revision/File rows with it, and
                # `imports.model_id`'s own ON DELETE SET NULL nulls the link
                # as part of the same statement if it had already committed.
                s.execute(sa_delete(Model).where(Model.id == created_model_id))
                s.commit()
            imp = s.get(Import, import_id)
            if imp is not None:
                # Idempotent even when the delete above already nulled this
                # via the FK's ON DELETE SET NULL (or when no model was ever
                # created this run) -- either way `imports.model_id` must be
                # NULL on a FAILED row.
                imp.model_id = None
                _set_state(s, imp, ImportState.FAILED, error=message)
    finally:
        with contextlib.suppress(Exception):
            lock.release()


# ---------------------------------------------------------------------------
# feat/import-fidelity T3: re-download an EXISTING model's files fresh from
# its original import source. Reuses `_stage_and_process_files` (T1's zip/
# 3MF intelligence) and `_download_gallery_images` (T2's cover/gallery
# refresh) verbatim -- the same phase-(b) core `import_from_url` uses above.
# ---------------------------------------------------------------------------

REDOWNLOAD_MODES = frozenset({"revision", "replace"})


def _store_redownload_as_new_revision(
    s,
    settings: Settings,
    model,
    staged: list[download.StagedFile],
    staged_images: list[download.StagedFile],
    cover_staged: download.StagedFile | None,
) -> None:
    """``mode="revision"``: lands the fresh download as a brand-new revision
    -- numbered max+1, named "re-downloaded", directory via
    ``layout.revision_dir_name`` -- mirroring ``app.services.library.
    create_revision``'s row/directory shape (:947-1061) minus its old-
    revision copy step (there is nothing to copy; every file here is
    freshly downloaded, not snapshotted).

    ``model.current_revision_id`` is reassigned as the LAST statement before
    the final commit: a failure any time before that (a store call raising
    partway through the loop, a storage error, ...) leaves the OLD revision
    still current -- the model looks exactly as it did before this task ever
    ran. The new, partially-populated revision is NOT rolled back on such a
    failure -- each ``store_imported_file_sync`` call commits its own file
    immediately (mirroring ``import_from_url``'s per-file store loop above),
    so a mid-loop failure simply leaves that partial revision behind as
    inert, non-current history rather than surfacing it anywhere -- the same
    "stray debris on failure" trade-off ``app.tasks.relocate``'s module
    docstring documents for its own partial-progress case.
    """
    from app.models.library import Revision

    # F2 review finding: captured BEFORE anything below is stored -- reads
    # only the OLD (still current-until-the-end-of-this-function) cover
    # value, per `_should_refresh_cover`'s docstring.
    refresh_cover = _should_refresh_cover(s, model, cover_staged)

    backend, _default_backend_id = resolve_default_backend_sync(s, settings)
    max_number = s.scalar(select(func.max(Revision.number)).where(Revision.model_id == model.id))
    next_number = (max_number or 0) + 1
    dir_name = layout.revision_dir_name(next_number, "re-downloaded")
    revision = Revision(
        model_id=model.id, number=next_number, name="re-downloaded", dir_name=dir_name
    )
    s.add(revision)
    s.flush()
    backend.mkdirs(layout.revision_dir_key(model.slug, dir_name))

    for sf in staged:
        library.store_imported_file_sync(s, model=model, revision=revision, staged=sf)
    # T2 refresh: the freshly-downloaded gallery images land in the new
    # revision the SAME way any other imported file does.
    for sf in staged_images:
        library.store_imported_file_sync(s, model=model, revision=revision, staged=sf)
    # F2: only overwrite a cover this redownload is entitled to touch -- NULL
    # or still the auto-set import cover. A user-picked cover (any other
    # blob) is left exactly as the user set it.
    if refresh_cover:
        model.cover_blob_hash = cover_staged.blob_hash

    model.current_revision_id = revision.id
    model.updated_at = func.now()
    s.commit()


def _store_redownload_in_place(
    s,
    settings: Settings,
    model,
    staged: list[download.StagedFile],
    staged_images: list[download.StagedFile],
    cover_staged: download.StagedFile | None,
) -> None:
    """``mode="replace"``: overwrites the CURRENT revision's files in place
    -- same revision id/dir_name, fresh bytes. The caller has ALREADY
    finished downloading everything to spool by the time this runs (spool-
    first ordering, per the T3 brief), so the only remaining risk window is
    between deleting the old files and storing the new ones -- kept as small
    as possible by running the delete pass and the store pass back to back
    in this SAME short session, with nothing else in between.

    Fails the whole job (raises, caught by the caller's outer try/except) if
    ANY current-revision file still has a ``store_to_backend`` job in
    flight -- checked for EVERY file BEFORE any is touched, mirroring
    ``app.services.library.delete_file``'s ``_file_store_pending`` guard so
    a redownload can't race an in-flight store into leaking an object.
    """
    from app.models.library import File, Revision

    library.lock_model_row_sync(s, model.id)
    revision = s.get(Revision, model.current_revision_id)
    current_files = list(s.execute(select(File).where(File.revision_id == revision.id)).scalars())
    for file in current_files:
        if library.file_store_pending_sync(s, file):
            raise RuntimeError(
                "a file in the current revision is still processing; retry once stored"
            )

    # F2 review finding: captured BEFORE the delete pass below removes the
    # old `images/01-cover.*` row -- `_is_auto_import_cover` couldn't find it
    # (to confirm the CURRENT cover is still "auto") after that row is gone.
    refresh_cover = _should_refresh_cover(s, model, cover_staged)

    for file in current_files:
        file_backend = resolve_backend_for_file_sync(s, settings, file)
        with contextlib.suppress(StorageKeyNotFound):
            file_backend.delete(file.storage_path)
        s.delete(file)
    s.commit()

    for sf in staged:
        library.store_imported_file_sync(s, model=model, revision=revision, staged=sf)
    # T2 refresh: replaces the old `images/` set the same way any other
    # current-revision file was just replaced above.
    for sf in staged_images:
        library.store_imported_file_sync(s, model=model, revision=revision, staged=sf)
    # F2: only overwrite a cover this redownload is entitled to touch -- NULL
    # or still the auto-set import cover. A user-picked cover (any other
    # blob) is left exactly as the user set it.
    if refresh_cover:
        model.cover_blob_hash = cover_staged.blob_hash
    model.updated_at = func.now()
    s.commit()


@celery_app.task(name="app.tasks.importing.redownload_model")
def redownload_model(job_id: str, model_id: int, mode: str) -> None:
    """T3: re-fetches ``model``'s files fresh from its original import
    source. ``mode="revision"`` lands the fresh download as a brand-new
    revision (the OLD revision is left completely untouched -- see
    ``_store_redownload_as_new_revision``); ``mode="replace"`` overwrites
    the CURRENT revision's files in place (see ``_store_redownload_in_place``).

    ``fetch_metadata`` is consulted ONLY for ``image_urls`` (T2's cover/
    gallery refresh) -- ``title``/``description``/``author``/``license``/
    ``tags`` are deliberately NEVER re-applied here: a re-download must
    never clobber edits the user has since made in TDMM itself.

    Deliberately does NOT consult
    ``app.services.import_dedup.find_live_import``: that guard exists so a
    SECOND import can't create a second Model for a remote source already in
    the library. A re-download targets THIS existing model by id and never
    creates a new Model, so the guard doesn't apply -- it's model-scoped by
    design, not source-scoped (see the endpoint's own docstring,
    ``app.api.models.redownload_model``).

    Job status transitions (``running`` -> ``done``/``failed`` with error
    text) follow the same ``app.services.jobs`` conventions every other
    tracked task uses, so the web's ``job.updated`` SSE stream stays honest.
    """
    from app.models.library import Model

    settings = get_settings()
    with base.sync_session() as s:
        jobs_service.mark_running(s, job_id)

    staged: list[download.StagedFile] = []
    staged_images: list[download.StagedFile] = []
    try:
        if mode not in REDOWNLOAD_MODES:
            raise ValueError(
                f"unknown redownload mode: {mode!r}; expected one of {sorted(REDOWNLOAD_MODES)}"
            )

        with base.sync_session() as s:
            model = s.get(Model, model_id)
            if model is None:
                raise LookupError(f"model {model_id} not found")
            if not model.source_site or not model.source_url:
                raise RuntimeError("model has no import source to re-download from")
            try:
                site = ImportSite(model.source_site)
            except ValueError:
                raise RuntimeError(f"unrecognized source site {model.source_site!r}") from None
            importer = IMPORTER_REGISTRY.get(site)
            if importer is None:
                raise RuntimeError(f"no importer registered for {model.source_site}")
            external_id = importer.canonicalize(model.source_url)
            if external_id is None:
                raise RuntimeError("model's source URL is no longer importable")

        # Phase (a), mirroring `import_from_url`'s: a model that's since
        # gone paid/Club/exclusive, or lost every downloadable file, fails
        # the redownload cleanly rather than storing a partial/placeholder
        # result.
        meta = importer.fetch_metadata(external_id)
        if meta.reject_reason:
            raise RuntimeError(meta.reject_reason)
        files = importer.list_files(external_id)
        if not files:
            raise RuntimeError("no downloadable files found for this model")

        # Spool-first (per the T3 brief): everything is downloaded to spool
        # BEFORE either apply function below touches a single existing row
        # or backend object.
        staged = _stage_and_process_files(settings, importer, external_id, files)
        cover_staged, staged_images = _download_gallery_images(
            settings, f"redownload model {model_id}", meta.image_urls
        )

        with base.sync_session() as s:
            model = s.get(Model, model_id)
            if model is None:
                raise LookupError(f"model {model_id} disappeared mid-redownload")
            if mode == "revision":
                _store_redownload_as_new_revision(
                    s, settings, model, staged, staged_images, cover_staged
                )
            else:
                _store_redownload_in_place(s, settings, model, staged, staged_images, cover_staged)
    except Exception as exc:  # noqa: BLE001 -- failure is a recorded terminal job state, not a crash
        for sf in [*staged, *staged_images]:
            sf.spool_path.unlink(missing_ok=True)
        message = f"redownload failed: {exc}"
        # Type + message only (never exc_info=True), same posture as
        # `import_from_url`'s failure log line above -- by this point the
        # one exception type whose str() could carry a URL (the download
        # try/except inside `_stage_and_process_files`) has already been
        # intercepted and replaced with a sanitized RuntimeError.
        logger.warning(
            "redownload %s (model %s) failed: %s: %s", job_id, model_id, type(exc).__name__, message
        )
        with base.sync_session() as s:
            jobs_service.mark_failed(s, job_id, message)
        return

    with base.sync_session() as s:
        jobs_service.mark_done(s, job_id)
