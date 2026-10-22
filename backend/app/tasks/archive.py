"""Archive a finished print (P2). io queue; retries the printer fetch with
backoff. P4 camera plan Task 7 additionally attaches a camera snapshot
(inline) and schedules a delayed, retried fetch of the printer's timelapse
once the archive step (success OR its first retryable failure) has run."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import redis
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select

from app.config import Settings, get_settings
from app.models import PrintArchive, PrintJob
from app.models.enums import ArchiveResult, BlobFormat, PrintJobState
from app.models.library import Blob
from app.services.archive import ArchiveFetchError, archive_job, mark_archive_error
from app.services.archive_media import attach_snapshot, fetch_timelapse_for
from app.tasks import base
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_SWEEP_GRACE_S = 60
_TIMELAPSE_FETCH_DELAY_S = 90
_TIMELAPSE_RETRY_DELAY_S = 120
_SWEEP_RESULT = {
    PrintJobState.FINISHED: ArchiveResult.COMPLETED,
    PrintJobState.FAILED: ArchiveResult.FAILED,
    PrintJobState.CANCELED: ArchiveResult.CANCELED,
}


def _snapshot_and_schedule_timelapse(settings: Settings, print_job_id: int) -> None:
    """Best-effort: grab the run's camera snapshot now and schedule the
    delayed timelapse fetch. Neither half is allowed to fail the archive --
    a camera/broker hiccup is logged, never raised."""
    try:
        with base.sync_session() as session:
            job = session.get(PrintJob, print_job_id)
            arc = session.scalars(
                select(PrintArchive).where(PrintArchive.print_job_id == print_job_id)
            ).first()
            if job is not None and arc is not None:
                r = redis.Redis.from_url(settings.redis_url)
                try:
                    attach_snapshot(session, settings, r, job, arc)
                finally:
                    r.close()
                session.commit()
    except Exception as e:  # noqa: BLE001 -- never fail the archive over the camera
        logger.warning("archive snapshot failed for job %s: %s", print_job_id, type(e).__name__)
    try:
        fetch_timelapse.apply_async(args=[print_job_id], countdown=_TIMELAPSE_FETCH_DELAY_S)
    except Exception as e:  # noqa: BLE001 -- never fail the archive over enqueueing
        logger.warning(
            "timelapse fetch enqueue failed for job %s: %s", print_job_id, type(e).__name__
        )


@celery_app.task(
    name="app.tasks.archive.archive_print",
    bind=True,
    autoretry_for=(ArchiveFetchError,),
    max_retries=3,
    retry_backoff=30,
    retry_backoff_max=600,
    retry_jitter=True,
    soft_time_limit=300,
    time_limit=330,
)
def archive_print(self, print_job_id: int, result: str) -> None:
    settings = get_settings()
    try:
        with base.sync_session() as session:
            archive_job(session, settings, print_job_id, ArchiveResult(result))
    except SoftTimeLimitExceeded:
        with base.sync_session() as session:
            mark_archive_error(session, print_job_id, ArchiveResult(result), "archive timed out")
        return
    except ArchiveFetchError:
        # Only on the first attempt: the print already finished regardless
        # of whether the 3MF fetch succeeded, so the camera snapshot and
        # timelapse fetch shouldn't wait on (or repeat across) retries.
        if self.request.retries == 0:
            _snapshot_and_schedule_timelapse(settings, print_job_id)
        raise
    else:
        _snapshot_and_schedule_timelapse(settings, print_job_id)


@celery_app.task(
    name="app.tasks.archive.fetch_timelapse", bind=True, max_retries=3, soft_time_limit=300
)
def fetch_timelapse(self, print_job_id: int) -> str:
    settings = get_settings()
    with base.sync_session() as session:
        outcome = fetch_timelapse_for(session, settings, print_job_id)
        arc = session.scalars(
            select(PrintArchive).where(PrintArchive.print_job_id == print_job_id)
        ).first()
        if outcome == "retry":
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=_TIMELAPSE_RETRY_DELAY_S)
            if arc is not None:
                arc.media_note = "no timelapse found"
                session.commit()
            return "none"
        if outcome == "stored" and arc is not None and arc.timelapse_blob is not None:
            blob = session.get(Blob, arc.timelapse_blob)
            if blob is not None and blob.format == BlobFormat.AVI:
                from app.tasks.media import transcode_timelapse

                transcode_timelapse.delay(arc.id)
        return outcome


@celery_app.task(name="app.tasks.archive.archive_sweep")
def archive_sweep() -> int:
    """Enqueue archiving for terminal jobs whose enqueue was lost (broker
    down, daemon crash) -- no ``print_archives`` row after a grace period."""
    cutoff = datetime.now(UTC) - timedelta(seconds=_SWEEP_GRACE_S)
    with base.sync_session() as session:
        rows = session.execute(
            select(PrintJob.id, PrintJob.state)
            .outerjoin(PrintArchive, PrintArchive.print_job_id == PrintJob.id)
            .where(
                PrintJob.state.in_([s.value for s in _SWEEP_RESULT]),
                PrintJob.finished_at < cutoff,
                PrintArchive.id.is_(None),
            )
        ).all()
    for job_id, state in rows:
        archive_print.apply_async(args=[job_id, _SWEEP_RESULT[PrintJobState(state)].value])
    return len(rows)
