"""Q7 beat/background tasks: plug poll (60 s), scheduled backup gate (10 min),
backup run, daily update check."""

from __future__ import annotations

# Defined before the celery_app import (see app.tasks.dispatch for why).
PLUG_INTERVAL_S = 60
BACKUP_GATE_INTERVAL_S = 600
DRYING_INTERVAL_S = 60
BEAT_TICK_INTERVAL_S = 30
BEAT_TICK_TASK = "app.tasks.ops.beat_tick"

import contextlib  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402

from celery.signals import before_task_publish  # noqa: E402
from redis import Redis  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services import backup as backup_svc  # noqa: E402
from app.services import (  # noqa: E402
    drying_tick,
    service_status,  # noqa: E402
    updates,
)
from app.services.smart_plugs import manager  # noqa: E402
from app.tasks import base  # noqa: E402
from app.tasks.celery_app import celery_app  # noqa: E402


@celery_app.task(name=BEAT_TICK_TASK, soft_time_limit=10, time_limit=15)
def beat_tick() -> None:
    """No-op: beat publishes it every ``BEAT_TICK_INTERVAL_S``; the heartbeat is
    written by the publish hook below, in the beat process itself."""


@before_task_publish.connect
def _beat_heartbeat(sender: str | None = None, **_: object) -> None:
    if sender != BEAT_TICK_TASK:
        return
    try:
        r = Redis.from_url(get_settings().redis_url)
        try:
            r.set(service_status.BEAT_KEY, json.dumps({"ts": time.time()}), ex=300)
        finally:
            r.close()
    except Exception:  # noqa: BLE001 -- a heartbeat must never break beat
        pass


@celery_app.task(name="app.tasks.plugs.tick", soft_time_limit=50, time_limit=55)
def plugs_tick() -> dict:
    settings = get_settings()
    r = Redis.from_url(settings.redis_url)
    try:
        with base.sync_session() as s:
            return manager.tick(s, r, settings)
    finally:
        r.close()


DRYING_LOCK_KEY = "tdmm:drying:tick:lock"
_DRYING_LOCK_TIMEOUT_S = 60


@celery_app.task(name="app.tasks.drying.tick", soft_time_limit=50, time_limit=55)
def drying_tick_task() -> dict:
    settings = get_settings()
    r = Redis.from_url(settings.redis_url)
    try:
        lock = r.lock(DRYING_LOCK_KEY, timeout=_DRYING_LOCK_TIMEOUT_S, blocking=False)
        if not lock.acquire(blocking=False):
            return {}  # an overlapping tick is still running
        try:
            with base.sync_session() as s:
                return drying_tick.tick(s, r, settings)
        finally:
            with contextlib.suppress(Exception):
                lock.release()
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
