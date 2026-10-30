"""The ``scan_library`` Celery task (SPEC "Rescan/reconcile"; Task 5 brief).

A Redis singleton lock (``SCAN_LOCK_KEY``) keeps two scans from ever running
concurrently -- a losing task marks its own ``ScanRun`` ``skipped`` rather
than blocking, since the API's own ``POST /api/scan`` 409s ahead of time on
any ``queued``/``running`` row; the lock is the last-line defense against a
race (two nearly-simultaneous POSTs) or a manually-dispatched task.

**Task 5 fix-wave Finding 2 (stale ``running`` reclaim):** a worker that's
hard-killed (SIGKILL/OOM) mid-scan never reaches ``scan_library``'s
``except``/``finally`` -- its ``ScanRun`` is stuck ``state="running"``
forever, and both the API's 409 guard and this module's own beat in-flight
check would otherwise block every future scan permanently. Both call sites
treat a ``running`` row as reclaimable when ``SCAN_LOCK_KEY`` is NOT
currently held in Redis: since the lock itself carries a
``_LOCK_TIMEOUT_S`` expiry, "nobody holds it" reliably means "no worker is
actively running a scan right now", whether that's because the lock was
cleanly released or because a dead worker's lock has since expired. This
piggybacks on a TTL the lock already has for its own correctness rather than
inventing a second, independently-tracked staleness window on the row
itself (which could drift out of sync with the lock's real timeout).
"""

from __future__ import annotations

import contextlib
import threading
from datetime import UTC, datetime

import redis.asyncio as aioredis
from redis import Redis
from sqlalchemy import select

from app.config import get_settings
from app.models import ScanRun
from app.services import scanner
from app.services.events import publish_scan_event_sync
from app.tasks import base
from app.tasks.celery_app import celery_app

SCAN_LOCK_KEY = "tdmm:scan:lock"
_LOCK_TIMEOUT_S = 3600
_LOCK_EXTEND_EVERY_S = 300.0


def _extend_lock_until(lock, stop: threading.Event, every_s: float = _LOCK_EXTEND_EVERY_S) -> None:
    """Heartbeat: keep the singleton lock alive for as long as the scan runs."""
    while not stop.wait(every_s):
        with contextlib.suppress(Exception):
            lock.reacquire()


# Short note stashed on a reclaimed run's `report` (Finding 2) -- there's no
# dedicated error column on `scan_runs`, and `report` is already the
# free-form JSONB bucket for this kind of scan-run bookkeeping.
STALE_SCAN_NOTE = "reclaimed: scan lock not held -- owning worker likely died mid-scan"


def is_scan_lock_held_sync(redis_url: str) -> bool:
    """Worker/beat-side (sync) check: does anything currently hold the scan
    singleton lock?
    """
    client = Redis.from_url(redis_url)
    try:
        return bool(client.exists(SCAN_LOCK_KEY))
    finally:
        client.close()


async def is_scan_lock_held(redis_url: str) -> bool:
    """API-side (async) twin of :func:`is_scan_lock_held_sync`."""
    client = aioredis.Redis.from_url(redis_url)
    try:
        return bool(await client.exists(SCAN_LOCK_KEY))
    finally:
        await client.aclose()


@celery_app.task(name="app.tasks.scan.scan_library")
def scan_library(scan_run_id: int) -> None:
    settings = get_settings()
    client = Redis.from_url(settings.redis_url)
    lock = client.lock(SCAN_LOCK_KEY, timeout=_LOCK_TIMEOUT_S, blocking=False)
    if not lock.acquire(blocking=False):
        with base.sync_session() as s:
            scanner.mark_scan_state(s, scan_run_id, "skipped")  # another run holds the lock
        client.close()
        return
    stop = threading.Event()
    beat = threading.Thread(target=_extend_lock_until, args=(lock, stop), daemon=True)
    beat.start()
    try:
        with base.sync_session() as s:
            scanner.mark_scan_state(s, scan_run_id, "running")
            publish_scan_event_sync(settings.redis_url, scan_run_id, "running")
            # Every configured storage backend, not just the default one
            # (Workstream C task C2) -- sets state=done, finished_at.
            scanner.run_scan_all_backends(s, settings, scan_run_id)
            publish_scan_event_sync(settings.redis_url, scan_run_id, "done")
    except Exception:
        with base.sync_session() as s:
            scanner.mark_scan_state(s, scan_run_id, "failed")
            publish_scan_event_sync(settings.redis_url, scan_run_id, "failed")
        raise
    finally:
        stop.set()
        beat.join(timeout=5)
        with contextlib.suppress(Exception):
            lock.release()
        with contextlib.suppress(Exception):
            client.close()


@celery_app.task(name="app.tasks.scan.schedule_scan_library")
def schedule_scan_library() -> None:
    """Beat-only entrypoint (opt-in, see ``app.tasks.celery_app``'s
    conditional ``beat_schedule``): ``scan_library`` itself always operates
    against a ``scan_run_id`` some caller already created (the API's
    ``POST /api/scan``, normally), so a scheduled tick needs its own small
    wrapper to create that row first. Skips the tick entirely (no new row,
    no dispatch) if a scan is still genuinely ``queued``/``running`` --
    mirrors ``POST /api/scan``'s 409 guard, just silent since there's no
    HTTP caller to report a conflict to. A ``running`` row whose lock isn't
    held is reclaimed (marked ``failed``) first, same as the API (Finding 2)
    -- otherwise a single dead worker would silently disable every future
    scheduled tick forever.
    """
    settings = get_settings()
    with base.sync_session() as s:
        in_flight = (
            s.execute(select(ScanRun).where(ScanRun.state.in_(("queued", "running"))))
            .scalars()
            .all()
        )

        blocked = False
        for run in in_flight:
            if run.state == "running" and not is_scan_lock_held_sync(settings.redis_url):
                run.state = "failed"
                run.finished_at = datetime.now(UTC)
                run.report = {"error": STALE_SCAN_NOTE}
            else:
                blocked = True
        if in_flight:
            s.commit()
        if blocked:
            return

        scan_run = ScanRun(state="queued")
        s.add(scan_run)
        s.commit()
        s.refresh(scan_run)
        scan_run_id = scan_run.id

    scan_library.apply_async(args=[scan_run_id])
