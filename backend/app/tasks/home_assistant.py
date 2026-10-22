"""The P8 Home Assistant plan's REST poll beat (Task 4): ticks
`app.services.home_assistant.poll_once` every 15s via Celery beat, guarded
by a non-blocking Redis lock (mirrors `app.tasks.dispatch`/`app.tasks
.scheduler`'s own locks -- a losing tick just returns, picked back up next
tick) so ticks never overlap.
"""

from __future__ import annotations

import contextlib

POLL_LOCK_KEY = "ha:poll:lock"
_LOCK_TIMEOUT_S = 14
POLL_INTERVAL_S = 15.0

from redis import Redis  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.ha_config import get_ha_config_sync  # noqa: E402
from app.services.home_assistant import poll_once  # noqa: E402
from app.tasks import base  # noqa: E402
from app.tasks.celery_app import celery_app  # noqa: E402


@celery_app.task(name="app.tasks.home_assistant.poll_ha")
def poll_ha() -> dict | None:
    settings = get_settings()
    client = Redis.from_url(settings.redis_url)
    try:
        lock = client.lock(POLL_LOCK_KEY, timeout=_LOCK_TIMEOUT_S, blocking=False)
        if not lock.acquire(blocking=False):
            return None  # another tick is still in flight; picked back up next tick
        try:
            with base.sync_session() as s:
                cfg = get_ha_config_sync(s, settings)
                return poll_once(s, client, cfg)
        finally:
            with contextlib.suppress(Exception):
                lock.release()
    finally:
        with contextlib.suppress(Exception):
            client.close()
