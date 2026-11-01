"""``relocate_model_storage`` Celery task (Workstream C multi-backend storage
design spec's "Relocate" section -- labeled task C2 in the shared spec, but
implemented here as task C3): moves or replicates every file of a model,
across ALL its revisions, from its current PRIMARY backend onto a TARGET
backend.

Reuses ``app.tasks.migrate``'s streaming copy-while-hashing-then-verify
pattern (a ``_chunks`` closure feeding ``dest.write`` while hashing, then
asserting ``result.hash`` against the expected hash) -- but per FILE rather
than per whole-tree walk, since a model's files can already span more than
one source backend (that's the whole point of Workstream C): each file
resolves its OWN current primary backend independently before copying.

**Content-dedup note:** physical storage here is layout-addressed, not
content-addressed -- the same blob referenced by two ``files`` rows at two
different ``storage_path`` keys exists as two independent physical objects
(see ``app.services.storage_backends`` module docstring). Relocating a file
therefore moves only THAT file's path; a sibling file elsewhere in the model
(or a different model entirely) that happens to share the same blob hash is
completely unaffected, even if it's never itself relocated.

**Verify-before-delete (move mode):** the target copy is streamed, hashed,
and verified -- and its ``file_locations`` row committed -- before anything
on the source is touched. Only once that's durably committed does this task
flip ``files.backend_id`` to the target and delete the source object/row.
This ordering (copy+verify+commit, THEN cut over, THEN delete source) means
a crash at any point before the delete leaves the source fully intact, so no
in-flight relocation can ever lose data -- the worst a crash can do is leave
extra bytes on the source backend that a later re-run of THIS SAME relocate
(current_backend_id already flipped to target) won't come back to clean up.
That stray-bytes case is a deliberately accepted, purely cosmetic trade-off:
it duplicates storage, but it never loses or orphans a DB reference.

Each file's copy + bookkeeping commits independently (its own
``base.sync_session()``), mirroring the scanner's per-chunk checkpoint
commits: a job that crashes partway through a large model leaves the
files already relocated exactly as they are (retryable no-ops, see below)
and picks back up on the remaining ones on retry.

Runs entirely in the worker's SYNC world -- see ``app.tasks.base``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import File, FileLocation, Model, Revision
from app.models.tag_links import model_file_tags
from app.services import jobs
from app.services.library import _escape_like
from app.services.storage_backends import (
    backend_for_id_sync,
    resolve_default_backend_sync,
)
from app.services.storage_tree import rewrite_folder_path
from app.storage.base import StorageBackend
from app.tasks import base
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

MODES = frozenset({"move", "replicate"})


@celery_app.task(name="app.tasks.relocate.relocate_model_storage")
def relocate_model_storage(job_id: str, model_id: int, target_backend_id: int, mode: str) -> None:
    settings = get_settings()
    with base.sync_session() as s:
        jobs.mark_running(s, job_id)

    try:
        # The API validates `mode` before ever dispatching (a pydantic
        # `Literal["move", "replicate"]`), so this is defense-in-depth for a
        # task invoked directly -- raised INSIDE the try so it still runs
        # through the normal mark_failed path below rather than leaving the
        # job stuck `running` forever.
        if mode not in MODES:
            raise ValueError(f"unknown relocate mode: {mode!r}; expected one of {sorted(MODES)}")

        with base.sync_session() as s:
            file_ids = list(
                s.execute(
                    select(File.id)
                    .join(Revision, File.revision_id == Revision.id)
                    .where(Revision.model_id == model_id)
                    .order_by(File.id)
                ).scalars()
            )
            # Resolved once -- the target is fixed for the whole job, and a
            # `StorageBackend` instance carries no session state, so it's
            # safe to reuse across every per-file session opened below (same
            # posture as `app.tasks.migrate`'s `dest`).
            target_backend = backend_for_id_sync(s, settings, target_backend_id)

        for file_id in file_ids:
            with base.sync_session() as s:
                _relocate_one_file(s, settings, file_id, target_backend_id, target_backend, mode)
    except Exception as exc:
        with base.sync_session() as s:
            jobs.mark_failed(s, job_id, str(exc))
        raise

    # Mark done in its OWN try (same posture as app.tasks.migrate): every
    # file that verified is already durably committed by this point, so a
    # post-success hiccup here must not misreport a genuinely-successful
    # relocate as failed.
    try:
        with base.sync_session() as s:
            jobs.mark_done(s, job_id)
    except Exception:
        logger.warning("relocate %s: succeeded but marking done failed", job_id, exc_info=True)


@celery_app.task(name="app.tasks.relocate.relocate_all_models")
def relocate_all_models(job_id: str, target_backend_id: int, mode: str = "move") -> None:
    """Bulk "move the whole library onto backend X" (M8 F): the correct
    multi-backend replacement for the legacy ``migrate_storage`` -- relocates
    EVERY file across every model to the target using the exact same
    per-file, verify-before-delete, retryable path as
    ``relocate_model_storage`` (so ``files.backend_id`` + ``file_locations``
    stay accurate, unlike migrate which only flipped the default row's
    config). Files already primary on the target are skipped. The caller
    (``POST /settings/storage/backends/{id}/migrate``) sets the target as the
    write-default before dispatching, so new uploads also land there."""
    settings = get_settings()
    with base.sync_session() as s:
        jobs.mark_running(s, job_id)

    try:
        if mode not in MODES:
            raise ValueError(f"unknown relocate mode: {mode!r}; expected one of {sorted(MODES)}")

        with base.sync_session() as s:
            file_ids = list(s.execute(select(File.id).order_by(File.id)).scalars())
            target_backend = backend_for_id_sync(s, settings, target_backend_id)

        for file_id in file_ids:
            with base.sync_session() as s:
                _relocate_one_file(s, settings, file_id, target_backend_id, target_backend, mode)
    except Exception as exc:
        with base.sync_session() as s:
            jobs.mark_failed(s, job_id, str(exc))
        raise

    try:
        with base.sync_session() as s:
            jobs.mark_done(s, job_id)
    except Exception:
        logger.warning("relocate_all %s: succeeded but marking done failed", job_id, exc_info=True)


def _relocate_one_file(
    s: Session,
    settings: Settings,
    file_id: int,
    target_backend_id: int,
    target_backend: StorageBackend,
    mode: str,
) -> None:
    file = s.get(File, file_id)
    if file is None:
        return  # deleted since the job was enqueued -- nothing left to relocate

    if file.backend_id is not None:
        current_backend_id = file.backend_id
        source_backend = backend_for_id_sync(s, settings, current_backend_id)
    else:
        # NULL backend_id pre-seed safety net (mirrors
        # `resolve_backend_for_file_sync`) -- default.
        source_backend, current_backend_id = resolve_default_backend_sync(s, settings)

    if current_backend_id == target_backend_id:
        return  # already primary on the target: nothing to move/replicate

    storage_path = file.storage_path
    expected_hash = file.blob_hash

    location = s.get(FileLocation, (file.id, target_backend_id))
    if location is None or location.verified_at is None:
        # Same streaming-copy shape as `app.tasks.migrate`'s `_chunks`
        # closure: `source_backend.read` is consumed lazily, one generator,
        # fed straight into `dest.write`, which hashes (blake3) as it writes
        # (`StorageBackend.write`'s own contract) and hands back that hash in
        # `result.hash` -- no second read of either side needed. Unlike
        # `migrate.py` (which only has two backends to compare against each
        # other), a relocated file's KNOWN-GOOD hash is already sitting right
        # there in `files.blob_hash`, so this checks against that instead of
        # a second independently-computed source-side hash -- a strictly
        # stronger check, since it also catches a source object that was
        # already corrupt before this copy ever started.
        def _chunks(source_backend=source_backend, storage_path=storage_path):
            yield from source_backend.read(storage_path)

        result = target_backend.write(storage_path, _chunks())
        if result.hash != expected_hash:
            # Aborts THIS file only -- the source is untouched (see
            # module docstring) -- but propagates to fail the whole job
            # (dead-letter/retry per `app.services.jobs`), same as
            # `app.tasks.migrate`'s hash-mismatch handling. Files already
            # relocated earlier in this loop stay relocated; a retry skips
            # them (their `file_locations` row already verified) and
            # resumes from here.
            raise RuntimeError(f"hash mismatch relocating file {file.id} ({storage_path!r})")
        now = datetime.now(UTC)
        if location is None:
            s.add(FileLocation(file_id=file.id, backend_id=target_backend_id, verified_at=now))
        else:
            location.verified_at = now
        s.commit()

    if mode == "move":
        # ONLY after the target copy verified and committed above: cut the
        # primary pointer over and drop the source's bookkeeping row in ONE
        # atomic commit, THEN physically delete the source object. A crash
        # between the commit and the physical delete just leaves a harmless
        # extra copy sitting on the source backend with no DB row pointing
        # at it -- never a lost file, never a dangling reference.
        file.backend_id = target_backend_id
        source_location = s.get(FileLocation, (file.id, current_backend_id))
        if source_location is not None:
            s.delete(source_location)
        s.commit()
        source_backend.delete(storage_path)


@celery_app.task(name="app.tasks.relocate.move_storage_folder")
def move_storage_folder(job_id: str, src: str, new_prefix: str) -> None:
    """Re-root every file under folder ``src`` to ``new_prefix`` (same
    revision; see ``storage_tree.plan_folder_move``). Same verify-before-delete
    discipline as ``_relocate_one_file``: per file the bytes are copied to the
    new key on EVERY backend holding a copy and hash-checked, the DB row is
    repointed in one commit, and only then are the old objects deleted -- a
    crash before the commit leaves the old key authoritative, after it leaves
    at worst a stray old object."""
    settings = get_settings()
    with base.sync_session() as s:
        jobs.mark_running(s, job_id)
    try:
        with base.sync_session() as s:
            file_ids = list(
                s.execute(
                    select(File.id)
                    .where(File.storage_path.like(f"{_escape_like(src)}/%", escape="\\"))
                    .order_by(File.id)
                ).scalars()
            )
        for file_id in file_ids:
            with base.sync_session() as s:
                _move_one_file(s, settings, file_id, src, new_prefix)
    except Exception as exc:
        with base.sync_session() as s:
            jobs.mark_failed(s, job_id, str(exc))
        raise
    try:
        with base.sync_session() as s:
            jobs.mark_done(s, job_id)
    except Exception:
        logger.warning("move_folder %s: succeeded but marking done failed", job_id, exc_info=True)


def _move_one_file(s: Session, settings: Settings, file_id: int, src: str, new_prefix: str) -> None:
    file = s.get(File, file_id)
    if file is None or not file.storage_path.startswith(src + "/"):
        return  # deleted, or already moved by a previous (retried) run
    _move_file_to(s, settings, file, rewrite_folder_path(file.storage_path, src, new_prefix))


def _move_file_to(s: Session, settings: Settings, file: File, new: str) -> None:
    """Copy ``file``'s bytes to key ``new`` on EVERY backend holding a copy
    (hash-verified), repoint the row, re-key its per-file tags and commit in
    one go, then delete the old objects. No-op when already at ``new``."""
    old = file.storage_path
    if old == new:
        return
    expected = file.blob_hash

    holders: list[tuple[StorageBackend, str]] = []
    if file.backend_id is not None:
        primary_id = file.backend_id
        holders.append((backend_for_id_sync(s, settings, primary_id), old))
    else:
        primary_backend, primary_id = resolve_default_backend_sync(s, settings)
        holders.append((primary_backend, old))
    for loc in s.execute(
        select(FileLocation).where(
            FileLocation.file_id == file.id, FileLocation.backend_id != primary_id
        )
    ).scalars():
        holders.append((backend_for_id_sync(s, settings, loc.backend_id), old))

    for backend, _ in holders:

        def _chunks(backend=backend, old=old):
            yield from backend.read(old)

        result = backend.write(new, _chunks())
        if result.hash != expected:
            backend.delete(new)
            raise RuntimeError(f"hash mismatch moving file {file.id} ({old!r})")

    old_rel = file.rel_path
    file.storage_path = new
    file.rel_path = new.split("/", 2)[2]
    _rekey_file_tags(s, file, old_rel)
    s.commit()
    for backend, _ in holders:
        backend.delete(old)


def _rekey_file_tags(s: Session, file: File, old_rel: str) -> None:
    """Carry ``model_file_tags`` rows from ``old_rel`` to the file's new
    ``rel_path`` (tags are keyed by (model, rel_path), not file id). The old
    rows go only when no other revision of the model still has that path."""
    if old_rel == file.rel_path:
        return
    model_id = s.scalar(select(Revision.model_id).where(Revision.id == file.revision_id))
    t = model_file_tags
    tag_ids = list(
        s.execute(
            select(t.c.tag_id).where(t.c.model_id == model_id, t.c.rel_path == old_rel)
        ).scalars()
    )
    if not tag_ids:
        return
    s.execute(
        pg_insert(t)
        .values([{"model_id": model_id, "rel_path": file.rel_path, "tag_id": i} for i in tag_ids])
        .on_conflict_do_nothing()
    )
    s.flush()
    still_used = s.scalar(
        select(File.id)
        .join(Revision, Revision.id == File.revision_id)
        .where(Revision.model_id == model_id, File.rel_path == old_rel)
        .limit(1)
    )
    if still_used is None:
        s.execute(delete(t).where(t.c.model_id == model_id, t.c.rel_path == old_rel))


@celery_app.task(name="app.tasks.relocate.move_model_files")
def move_model_files(job_id: str, model_id: int, moves: list[list]) -> None:
    """Rename/move individual files of a model's current revision
    (``moves`` = ``[[file_id, new_rel_path], ...]``). Per file: the same
    copy-verify-commit-delete as folder moves. Retries skip files already at
    their target (``_move_file_to`` is a no-op there)."""
    settings = get_settings()
    with base.sync_session() as s:
        jobs.mark_running(s, job_id)
    blob_hashes: set[str] = set()
    try:
        for file_id, new_rel in moves:
            with base.sync_session() as s:
                file = s.get(File, file_id)
                if file is None:
                    continue  # deleted since the request
                parts = file.storage_path.split("/", 2)
                _move_file_to(s, settings, file, f"{parts[0]}/{parts[1]}/{new_rel}")
                blob_hashes.add(file.blob_hash)
        with base.sync_session() as s:
            s.execute(update(Model).where(Model.id == model_id).values(updated_at=func.now()))
            s.commit()
            from app.tasks.pipeline import maybe_enqueue_assembly_sync

            for h in blob_hashes:
                maybe_enqueue_assembly_sync(s, blob_hash=h)
    except Exception as exc:
        with base.sync_session() as s:
            jobs.mark_failed(s, job_id, str(exc))
        raise
    try:
        with base.sync_session() as s:
            jobs.mark_done(s, job_id)
    except Exception:
        logger.warning("move_files %s: succeeded but marking done failed", job_id, exc_info=True)
