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

import contextlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as SyncSession

from app.config import Settings
from app.models import Job
from app.models.library import File, Model, Revision
from app.models.storage import FileLocation
from app.services import jobs as jobs_service
from app.services import spool
from app.services.storage_backends import backend_for_id_sync, resolve_backend_for_file_sync
from app.storage.errors import StorageKeyNotFound

log = logging.getLogger(__name__)

REAP_AFTER = timedelta(minutes=60)
_TOMBSTONE = ".replaced-"


def _latest_jobs(session: SyncSession, cutoff: datetime) -> dict[int, tuple[str, object]]:
    """file_id -> (state, job_id) of the latest store job, when older than cutoff."""
    latest = (
        select(Job.subject_id, func.max(Job.created_at).label("at"))
        .where(Job.subject_type == "file", Job.type == "store_to_backend")
        .group_by(Job.subject_id)
        .subquery()
    )
    rows = session.execute(
        select(Job.subject_id, Job.state, Job.id)
        .join(latest, (Job.subject_id == latest.c.subject_id) & (Job.created_at == latest.c.at))
        .where(Job.subject_type == "file", Job.type == "store_to_backend", latest.c.at < cutoff)
    ).all()
    return {fid: (state, jid) for fid, state, jid in rows}


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
    if not shared:
        backends = [resolve_backend_for_file_sync(session, settings, file)]
        for loc in session.execute(
            select(FileLocation).where(FileLocation.file_id == fid)
        ).scalars():
            backends.append(backend_for_id_sync(session, settings, loc.backend_id))
        for b in backends:
            with contextlib.suppress(StorageKeyNotFound):
                b.delete(path)
    model = session.scalar(
        select(Model)
        .join(Revision, Revision.model_id == Model.id)
        .where(Revision.id == revision_id)
    )
    session.delete(file)
    if model is not None:
        model.updated_at = func.now()
    session.commit()
    if job_id is not None:
        spool.spool_path(settings, job_id).unlink(missing_ok=True)
    from app.tasks.pipeline import maybe_enqueue_assembly_sync

    maybe_enqueue_assembly_sync(session, blob_hash=blob_hash)


def reap_unverified_files_sync(
    session: SyncSession, settings: Settings, now: datetime | None = None
) -> int:
    cutoff = (now or datetime.now(UTC)) - REAP_AFTER
    jobs = _latest_jobs(session, cutoff)
    reaped = 0

    # 1. Unverified files whose store job is dead.
    orphans = session.execute(
        select(File).where(File.verified_at.is_(None), File.id.in_(list(jobs)))
    ).scalars()
    targets: list[tuple[File, object | None]] = []
    for f in orphans:
        state, jid = jobs[f.id]
        if state in (jobs_service.STATE_FAILED, jobs_service.STATE_DEAD):
            targets.append((f, jid))

    # 2. Tombstones whose replacing file's job finished long ago.
    for t in session.execute(select(File).where(File.rel_path.contains(_TOMBSTONE))).scalars():
        base_path = t.rel_path.rsplit(_TOMBSTONE, 1)[0]
        new_id = session.scalar(
            select(File.id).where(File.revision_id == t.revision_id, File.rel_path == base_path)
        )
        if new_id in jobs and jobs[new_id][0] == jobs_service.STATE_DONE:
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
