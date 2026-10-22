"""Archive a finished print (P2). io queue; retries the printer fetch with backoff."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select

from app.config import get_settings
from app.models import PrintArchive, PrintJob
from app.models.enums import ArchiveResult, PrintJobState
from app.services.archive import ArchiveFetchError, archive_job, mark_archive_error
from app.tasks import base
from app.tasks.celery_app import celery_app

_SWEEP_GRACE_S = 60
_SWEEP_RESULT = {
    PrintJobState.FINISHED: ArchiveResult.COMPLETED,
    PrintJobState.FAILED: ArchiveResult.FAILED,
    PrintJobState.CANCELED: ArchiveResult.CANCELED,
}


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
    try:
        with base.sync_session() as session:
            archive_job(session, get_settings(), print_job_id, ArchiveResult(result))
    except SoftTimeLimitExceeded:
        with base.sync_session() as session:
            mark_archive_error(session, print_job_id, ArchiveResult(result), "archive timed out")


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
