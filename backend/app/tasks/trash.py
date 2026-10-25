"""Daily beat task: purge trashed models past the retention window."""

from __future__ import annotations

from app.config import get_settings
from app.services import trash
from app.tasks import base
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.trash.purge_expired")
def purge_expired() -> int:
    with base.sync_session() as s:
        return trash.auto_purge_sync(s, get_settings())
