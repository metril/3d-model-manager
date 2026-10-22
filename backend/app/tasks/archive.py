"""Archive a finished print (P2). io queue; retries the printer fetch with backoff."""

from __future__ import annotations

from app.config import get_settings
from app.models.enums import ArchiveResult
from app.services.archive import ArchiveFetchError, archive_job
from app.tasks import base
from app.tasks.celery_app import celery_app


@celery_app.task(
    name="app.tasks.archive.archive_print",
    bind=True,
    autoretry_for=(ArchiveFetchError,),
    max_retries=3,
    retry_backoff=30,
    retry_backoff_max=600,
    retry_jitter=True,
)
def archive_print(self, print_job_id: int, result: str) -> None:
    with base.sync_session() as session:
        archive_job(session, get_settings(), print_job_id, ArchiveResult(result))
