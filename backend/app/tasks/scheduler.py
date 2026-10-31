"""Database-driven scheduler dispatcher (Round 10 Task 2 -- "Settings" UI).

Before this module, every scheduled feature (``app.tasks.scan
.schedule_scan_library``, ``app.tasks.sync_collections.schedule_sync_all``,
``app.tasks.slicer_watch.scan_slicer_watch``) had its OWN conditional
``beat_schedule`` entry in ``app.tasks.celery_app``, built ONCE from
``Settings`` at process import time. Flipping an interval via the new
``PUT /settings/app`` (``app.services.app_config``) therefore had no effect
until every celery process -- api, worker, AND beat -- was restarted, since
beat's schedule was baked in at import.

This module replaces those three conditional entries with a SINGLE
unconditional beat entry, ``dispatch_scheduled``, ticking every
``_CADENCE_S`` seconds (see ``app.tasks.celery_app``). Each tick re-reads
the DB-backed ``AppConfig`` (``get_app_config_sync``) fresh and decides, per
feature, whether that feature's own interval has actually elapsed since it
last fired -- an edit via the Settings UI takes effect on the very next
tick, no restart required.

**Per-feature bookkeeping lives in Redis, not the DB**: one string key per
feature (``tdmm:sched:last:{scan,sync,watch}``, see ``_due``) holds a Unix
timestamp of its last dispatch.

**Arm-and-skip**: the first tick that ever sees a feature (its stamp key
missing -- a fresh deploy, a Redis flush, or the feature just having been
turned on) ARMS the stamp to ``now`` and returns ``False`` rather than
firing immediately. This deliberately avoids a thundering herd of
scan+sync+watch all firing on the very first tick after every deploy; the
cost is that a feature's first real run lands one full interval after it's
enabled, not instantly. An interval of ``0`` (feature off) never touches
the stamp at all, so turning a feature on later arms cleanly from that
point rather than firing immediately off a stamp written while it was off.

**Dispatch lock** (``DISPATCH_LOCK_KEY``): a non-blocking Redis singleton
lock, mirroring ``app.tasks.scan``/``app.tasks.slicer_watch``'s own locks --
guards against two overlapping ticks (a slow previous tick, or beat
misconfigured to run more than one process) both evaluating/firing at once.
A losing tick just returns; every stamp it would have checked is left
untouched, so the next tick re-evaluates from the same baseline.

``watch`` mirrors the deleted conditional entry's gating exactly:
``settings.watch_dir`` stays env-only (a filesystem path, not a
runtime-editable setting) and the watch interval is only even consulted --
so its stamp only arms -- once a watch dir is actually configured.
"""

from __future__ import annotations

import contextlib
import time

from redis import Redis

# Defined BEFORE the `app.tasks.celery_app` import below: `celery_app.py`
# itself imports this module (at its own bottom) to read `_CADENCE_S` when
# building `beat_schedule`. If something imports `app.tasks.scheduler`
# directly, as literally the first `app.tasks.*` module touched in a given
# process, that nested `celery_app.py` import runs while THIS module is
# still mid-import -- putting these constants first means they already
# exist on the (partially-initialized) module by the time `celery_app.py`
# reaches back for `scheduler._CADENCE_S`, instead of an
# `AttributeError: partially initialized module`.
_CADENCE_S = 15
DISPATCH_LOCK_KEY = "tdmm:sched:dispatch:lock"
_LOCK_TIMEOUT_S = 60

from app.config import get_settings  # noqa: E402
from app.services.app_config import get_app_config_sync  # noqa: E402
from app.services.server_config import get_server_config_sync  # noqa: E402
from app.tasks import base  # noqa: E402
from app.tasks.celery_app import celery_app  # noqa: E402


def _due(client: Redis, name: str, interval_s: float, now: float) -> bool:
    """Whether feature ``name``'s own interval has elapsed since its last
    dispatch (module docstring: arm-and-skip). ``interval_s <= 0`` (off)
    always returns ``False`` without touching the stamp at all.
    """
    if interval_s <= 0:
        return False
    key = f"tdmm:sched:last:{name}"
    stamp = client.get(key)
    if stamp is None:
        client.set(key, now)  # arm: first sighting never fires immediately
        return False
    if now - float(stamp) >= interval_s:
        client.set(key, now)
        return True
    return False


@celery_app.task(name="app.tasks.scheduler.dispatch_scheduled")
def dispatch_scheduled() -> None:
    """The single unconditional beat entry (see ``app.tasks.celery_app``).
    Re-reads the DB-backed ``AppConfig`` fresh on every tick and fires
    whichever of scan/sync/watch is actually due. The three target tasks are
    imported here, inside the function body, rather than at module import
    time -- each of them imports ``app.tasks.celery_app`` itself at ITS OWN
    module top (same pattern as this module), and this module is in turn
    imported FROM ``celery_app.py`` (to read ``_CADENCE_S`` at
    ``beat_schedule`` build time); importing them up front here would risk a
    circular partially-initialized-module import depending on which task
    module happens to be the first one any given process touches.
    """
    from app.tasks.scan import schedule_scan_library
    from app.tasks.slicer_watch import scan_slicer_watch
    from app.tasks.studio_exchange import scan_studio_exchange
    from app.tasks.sync_collections import schedule_sync_all

    settings = get_settings()
    client = Redis.from_url(settings.redis_url)
    try:
        lock = client.lock(DISPATCH_LOCK_KEY, timeout=_LOCK_TIMEOUT_S, blocking=False)
        if not lock.acquire(blocking=False):
            return  # another tick is still in flight; picked back up next tick
        try:
            with base.sync_session() as s:
                cfg = get_app_config_sync(s, settings)
                studio_on = get_server_config_sync(s, settings).studio_enabled
            now = time.time()

            if _due(client, "scan", cfg.scan_interval_s, now):
                schedule_scan_library.apply_async()
            if _due(client, "sync", cfg.collection_sync_interval_s, now):
                schedule_sync_all.apply_async()
            if settings.watch_dir is not None and _due(client, "watch", cfg.watch_interval_s, now):
                scan_slicer_watch.apply_async()
            if studio_on:
                # 5 s ingest cadence off the 15 s tick; the task's Redis lock
                # keeps overlapping runs harmless.
                for delay in range(0, _CADENCE_S, 5):
                    scan_studio_exchange.apply_async(countdown=delay)
        finally:
            with contextlib.suppress(Exception):
                lock.release()
    finally:
        # M1: this task ticks every `_CADENCE_S` (15s) forever -- unlike
        # `scan_library`/`scan_slicer_watch` (which share this same
        # no-close pattern but only run per-scan/per-poll, not on a fixed
        # short cadence), never closing this client accumulates a new
        # connection every tick in a long-lived beat process. Mirrors
        # `is_scan_lock_held_sync`'s close-in-finally (`app.tasks.scan`).
        with contextlib.suppress(Exception):
            client.close()
