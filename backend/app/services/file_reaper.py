"""Reaper for ingest-orphaned ``File`` rows: ``verified_at IS NULL`` whose
latest ``store_to_backend`` job failed/died more than an hour ago will never
be settled by anything, so drop the row (and its object, when no other file
row shares the storage key). Files with a queued/running job are never touched.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as SyncSession

from app.config import Settings
from app.models import Job
from app.models.library import File
from app.models.storage import FileLocation
from app.services import jobs as jobs_service
from app.services.storage_backends import backend_for_id_sync, resolve_backend_for_file_sync
from app.storage.errors import StorageKeyNotFound

log = logging.getLogger(__name__)

REAP_AFTER = timedelta(minutes=60)


def reap_unverified_files_sync(
    session: SyncSession, settings: Settings, now: datetime | None = None
) -> int:
    cutoff = (now or datetime.now(UTC)) - REAP_AFTER
    latest = (
        select(Job.subject_id, func.max(Job.created_at).label("at"))
        .where(Job.subject_type == "file", Job.type == "store_to_backend")
        .group_by(Job.subject_id)
        .subquery()
    )
    rows = session.execute(
        select(File, Job.state)
        .join(latest, latest.c.subject_id == File.id)
        .join(
            Job,
            (Job.subject_type == "file")
            & (Job.type == "store_to_backend")
            & (Job.subject_id == File.id)
            & (Job.created_at == latest.c.at),
        )
        .where(File.verified_at.is_(None), latest.c.at < cutoff)
    ).all()
    reaped = 0
    for file, state in rows:
        if state not in (jobs_service.STATE_FAILED, jobs_service.STATE_DEAD):
            continue
        try:
            path, fid = file.storage_path, file.id
            shared = session.scalar(
                select(func.count())
                .select_from(File)
                .where(File.storage_path == path, File.id != fid)
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
            session.delete(file)
            session.commit()
            reaped += 1
        except Exception:
            session.rollback()
            log.exception("unverified-file reap failed for file %s", file.id)
    return reaped
