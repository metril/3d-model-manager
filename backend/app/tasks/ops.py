"""Q7 beat/background tasks: plug poll (60 s), scheduled backup gate (10 min),
backup run, daily update check."""

from __future__ import annotations

# Defined before the celery_app import (see app.tasks.dispatch for why).
PLUG_INTERVAL_S = 60
BACKUP_GATE_INTERVAL_S = 600

from redis import Redis  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services import backup as backup_svc  # noqa: E402
from app.services import updates  # noqa: E402
from app.services.smart_plugs import manager  # noqa: E402
from app.tasks import base  # noqa: E402
from app.tasks.celery_app import celery_app  # noqa: E402


@celery_app.task(name="app.tasks.plugs.tick", soft_time_limit=50, time_limit=55)
def plugs_tick() -> dict:
    settings = get_settings()
    r = Redis.from_url(settings.redis_url)
    try:
        with base.sync_session() as s:
            return manager.tick(s, r, settings)
    finally:
        r.close()


@celery_app.task(name="app.tasks.backup.run", soft_time_limit=1900, time_limit=2000)
def backup_run(run_id: int) -> dict:
    settings = get_settings()
    with base.sync_session() as s:
        run = backup_svc.run_backup(s, settings, run_id)
        return {"id": run.id, "status": run.status, "error": run.error}


@celery_app.task(name="app.tasks.backup.scheduled", soft_time_limit=60, time_limit=90)
def backup_scheduled() -> int | None:
    settings = get_settings()
    with base.sync_session() as s:
        if not backup_svc.due(s, settings):
            return None
        run = backup_svc.new_run(s, "scheduled", None)
        run_id = run.id
    backup_run.delay(run_id)
    return run_id


@celery_app.task(name="app.tasks.updates.check", soft_time_limit=30, time_limit=40)
def updates_check() -> dict:
    settings = get_settings()
    with base.sync_session() as s:
        return updates.check(s, settings)
