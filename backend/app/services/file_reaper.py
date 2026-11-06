"""Reaper for ingest-orphaned ``File`` rows:

* ``verified_at IS NULL`` whose latest ``store_to_backend`` job failed/died
  more than an hour ago will never be settled by anything;
* ``*.replaced-<hex>`` tombstones left by an upload-replace that crashed
  between enqueue and deleting the old row, once the replacing file's job has
  finished and is an hour old.

Each is deleted like ``library._hard_delete_file_row`` (object only when no
other file row shares the storage key, model ``updated_at`` bump, assembly
re-check) plus its leftover spool file. Files with a queued/running job are
never touched.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as SyncSession

from app.config import Settings
from app.models import Job
from app.models.library import File, Model, Revision
from app.models.storage import FileLocation
from app.services import jobs as jobs_service
from app.services import pending_deletes, spool
from app.services.storage_backends import backend_for_id_sync, resolve_backend_for_file_sync

log = logging.getLogger(__name__)

REAP_AFTER = timedelta(minutes=60)
_TOMBSTONE = ".replaced-"


def _latest_jobs_subq(cutoff: datetime):
    """Latest ``store_to_backend`` job per file (DISTINCT ON), older than cutoff."""
    return (
        select(Job.subject_id, Job.state, Job.id, Job.created_at)
        .where(Job.subject_type == "file", Job.type == "store_to_backend")
        .distinct(Job.subject_id)
        .order_by(Job.subject_id, Job.created_at.desc(), Job.id.desc())
        .subquery()
    )


def _delete(session: SyncSession, settings: Settings, file: File, job_id: object | None) -> None:
    path, fid, revision_id, blob_hash = (
        file.storage_path,
        file.id,
        file.revision_id,
        file.blob_hash,
    )
    shared = session.scalar(
        select(func.count()).select_from(File).where(File.storage_path == path, File.id != fid)
    )
    targets = []
    if not shared:
        targets = [(resolve_backend_for_file_sync(session, settings, file), file.backend_id, path)]
        for loc in session.execute(
            select(FileLocation).where(FileLocation.file_id == fid)
        ).scalars():
            targets.append(
                (backend_for_id_sync(session, settings, loc.backend_id), loc.backend_id, path)
            )
    model = session.scalar(
        select(Model)
        .join(Revision, Revision.model_id == Model.id)
        .where(Revision.id == revision_id)
    )
    session.delete(file)
    if model is not None:
        model.updated_at = func.now()
    # Commit the row delete first; storage cleanup is best-effort afterwards.
    session.commit()
    pending_deletes.record_sync(session, pending_deletes.delete_collecting(targets))
    if job_id is not None:
        spool.spool_path(settings, job_id).unlink(missing_ok=True)
    from app.tasks.pipeline import maybe_enqueue_assembly_sync

    maybe_enqueue_assembly_sync(session, blob_hash=blob_hash)


def reap_unverified_files_sync(
    session: SyncSession, settings: Settings, now: datetime | None = None
) -> int:
    pending_deletes.retry_sync(session, settings)
    cutoff = (now or datetime.now(UTC)) - REAP_AFTER
    latest = _latest_jobs_subq(cutoff)
    reaped = 0

    # 1. Unverified files whose store job is dead.
    targets: list[tuple[File, object | None]] = [
        (f, jid)
        for f, jid in session.execute(
            select(File, latest.c.id)
            .join(latest, latest.c.subject_id == File.id)
            .where(
                File.verified_at.is_(None),
                latest.c.created_at < cutoff,
                latest.c.state.in_((jobs_service.STATE_FAILED, jobs_service.STATE_DEAD)),
            )
        ).all()
    ]

    # 2. Tombstones whose replacing file's job finished long ago.
    for t in session.execute(select(File).where(File.rel_path.contains(_TOMBSTONE))).scalars():
        base_path = t.rel_path.rsplit(_TOMBSTONE, 1)[0]
        new_id = session.scalar(
            select(File.id).where(File.revision_id == t.revision_id, File.rel_path == base_path)
        )
        if new_id is None:
            continue
        state = session.scalar(
            select(latest.c.state).where(
                latest.c.subject_id == new_id, latest.c.created_at < cutoff
            )
        )
        if state == jobs_service.STATE_DONE:
            targets.append((t, None))

    for file, jid in targets:
        fid = file.id
        try:
            _delete(session, settings, file, jid)
            reaped += 1
        except Exception:
            session.rollback()
            log.exception("file reap failed for file %s", fid)
    return reaped
