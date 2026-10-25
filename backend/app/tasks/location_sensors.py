"""Beat tick (every 120 s) refreshing storage-location Home Assistant sensors."""

from __future__ import annotations

import contextlib

POLL_LOCK_KEY = "location_sensors:poll:lock"
POLL_INTERVAL_S = 120.0
_LOCK_TIMEOUT_S = 100

from redis import Redis  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.ha_config import get_ha_config_sync  # noqa: E402
from app.services.location_sensors import poll_once  # noqa: E402
from app.tasks import base  # noqa: E402
from app.tasks.celery_app import celery_app  # noqa: E402


@celery_app.task(name="location_sensors.poll", soft_time_limit=90, time_limit=100)
def poll() -> dict | None:
    settings = get_settings()
    client = Redis.from_url(settings.redis_url)
    try:
        lock = client.lock(POLL_LOCK_KEY, timeout=_LOCK_TIMEOUT_S, blocking=False)
        if not lock.acquire(blocking=False):
            return None
        try:
            with base.sync_session() as s:
                return poll_once(s, get_ha_config_sync(s, settings))
        finally:
            with contextlib.suppress(Exception):
                lock.release()
    finally:
        with contextlib.suppress(Exception):
            client.close()
