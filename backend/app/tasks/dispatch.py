"""The P6 slicer/dispatch plan's beat task (Task 7): ticks
``app.services.dispatcher.dispatch_once`` every ``DISPATCH_INTERVAL_S``
seconds via Celery beat, guarded by a non-blocking Redis lock (mirrors
``app.tasks.scheduler``'s own lock -- a losing tick just returns, picked
back up next tick) and a no-op when the printer flag is off (DB-backed,
re-read fresh every tick, same posture as ``app.api.deps
.require_printer_enabled``).
"""

from __future__ import annotations

import contextlib

# Defined BEFORE the `app.tasks.celery_app` import below, same reason as
# `app.tasks.scheduler._CADENCE_S`: `celery_app.py` imports THIS module (at
# its own bottom) to read `DISPATCH_INTERVAL_S` when building
# `beat_schedule`. If this module is the first `app.tasks.*` module touched
# in a given process, that nested `celery_app.py` import runs while this
# module is still mid-import -- putting these constants first means they
# already exist on the (partially-initialized) module by the time
# `celery_app.py` reaches back for them, instead of an
# `AttributeError: partially initialized module`.
DISPATCH_LOCK_KEY = "tdmm:dispatch:lock"
_LOCK_TIMEOUT_S = 60
DISPATCH_INTERVAL_S = 10

from redis import Redis  # noqa: E402
from redis.exceptions import LockError  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.app_config import get_app_config_sync  # noqa: E402
from app.services.dispatcher import dispatch_once  # noqa: E402
from app.tasks import base  # noqa: E402
from app.tasks.celery_app import celery_app  # noqa: E402


@celery_app.task(name="app.tasks.dispatch.dispatch_queue")
def dispatch_queue() -> dict | None:
    settings = get_settings()
    client = Redis.from_url(settings.redis_url)
    try:
        with base.sync_session() as s:
            cfg = get_app_config_sync(s, settings)
        if not cfg.printer_enabled:
            return None

        lock = client.lock(DISPATCH_LOCK_KEY, timeout=_LOCK_TIMEOUT_S, blocking=False)
        if not lock.acquire(blocking=False):
            return None  # another tick is still in flight; picked back up next tick
        try:

            def _keepalive() -> bool:
                # Re-arm the TTL per item: a slow tick must not outlive the lock
                # and let a second worker dispatch the same entries.
                try:
                    lock.extend(_LOCK_TIMEOUT_S, replace_ttl=True)
                except LockError:
                    return False
                return True

            with base.sync_session() as s:
                return dispatch_once(s, client, settings, keepalive=_keepalive)
        finally:
            with contextlib.suppress(Exception):
                lock.release()
    finally:
        with contextlib.suppress(Exception):
            client.close()
