"""Archive a finished print (P2). io queue; retries the printer fetch with
backoff. P4 camera plan Task 7 additionally attaches a camera snapshot
(inline) and schedules a delayed, retried fetch of the printer's timelapse
once the archive step's first terminal outcome (success, a retryable
failure, or a timeout) has run -- exactly once per job, regardless of how
many of those paths fire across retries (``_claim_media_schedule``)."""

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
# Covers the full span a retried archive_print could still be going
# (retry_backoff_max=600 * max_retries=3, plus slack) -- long enough that
# the SETNX lock below can't expire and let a later retry double-schedule.
_MEDIA_SCHEDULE_LOCK_TTL_S = 3600
_SWEEP_RESULT = {
    PrintJobState.FINISHED: ArchiveResult.COMPLETED,
    PrintJobState.FAILED: ArchiveResult.FAILED,
    PrintJobState.CANCELED: ArchiveResult.CANCELED,
}


def media_schedule_lock_key(print_job_id: int) -> str:
    """Redis key for ``_claim_media_schedule``'s SETNX lock -- exported so
    tests can clear a stale lock left by a previous test that reused the
    same (truncate-and-restart-identity) job id against the same,
    session-shared Redis."""
    return f"archive:media-scheduled:{print_job_id}"


def _claim_media_schedule(settings: Settings, print_job_id: int) -> bool:
    """True the first time this is called for ``print_job_id`` (within
    ``_MEDIA_SCHEDULE_LOCK_TTL_S``), False on every call after. A Redis
    SETNX lock, not ``self.request.retries`` -- a retried task's manual
    redelivery (real broker retry, or a test driving ``.apply()`` directly)
    doesn't reliably carry a monotonically increasing retry count, so
    retries==0 can't be trusted to fire exactly once across attempts."""
    r = redis.Redis.from_url(settings.redis_url)
    try:
        return bool(
            r.set(
                media_schedule_lock_key(print_job_id),
                "1",
                nx=True,
                ex=_MEDIA_SCHEDULE_LOCK_TTL_S,
            )
        )
    finally:
        r.close()


def _snapshot_and_schedule_timelapse(settings: Settings, print_job_id: int) -> None:
    """Best-effort: grab the run's camera snapshot now and schedule the
    delayed timelapse fetch. Neither half is allowed to fail the archive --
    a camera/broker hiccup is logged, never raised. Guarded by
    ``_claim_media_schedule`` so the archive step's various terminal paths
    (success, a retried failure's first attempt, a soft-timeout) can't each
    schedule their own, duplicate follow-up for the same job."""
    try:
        if not _claim_media_schedule(settings, print_job_id):
            return
    except Exception as e:  # noqa: BLE001 -- never fail the archive over Redis
        logger.warning("media-schedule lock failed for job %s: %s", print_job_id, type(e).__name__)
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
        # The snapshot/timelapse follow-ups are independent of the 3MF
        # fetch that timed out -- still schedule them (``_claim_media_
        # schedule`` keeps this from double-firing against the other
        # terminal paths below).
        _snapshot_and_schedule_timelapse(settings, print_job_id)
        return
    except ArchiveFetchError:
        # The print already finished regardless of whether the 3MF fetch
        # succeeded, so the camera snapshot and timelapse fetch shouldn't
        # wait on retries. ``_claim_media_schedule`` ensures that if this
        # fires on attempt 1's failure, a later successful retry's `else`
        # branch below won't schedule a second time for the same job.
        _snapshot_and_schedule_timelapse(settings, print_job_id)
        raise
    else:
        _snapshot_and_schedule_timelapse(settings, print_job_id)


@celery_app.task(
    name="app.tasks.archive.fetch_timelapse",
    bind=True,
    max_retries=3,
    soft_time_limit=240,
    time_limit=300,
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
