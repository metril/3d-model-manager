"""Q9 beat: ``spoolman.reconcile`` every 5 min (no-op when mode=off)."""

from __future__ import annotations

from app.services import spoolman
from app.tasks import base
from app.tasks.celery_app import celery_app

RECONCILE_INTERVAL_S = 300


@celery_app.task(name="app.tasks.spoolman.reconcile", soft_time_limit=120, time_limit=150)
def reconcile() -> dict:
    with base.sync_session() as s:
        return spoolman.reconcile(s)
