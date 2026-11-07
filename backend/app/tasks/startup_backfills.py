"""One-shot backfills kicked off when a worker starts.

Rows only go stale when a release changes analysis output, and a release
always restarts the workers. Each backfill self-chains until drained, so one
enqueue per start replaces the old 10-minute beat polling. A Redis SET NX lock
(per task) keeps multiple workers/containers from double-enqueuing.
"""

import logging

from redis import Redis

from app.config import get_settings
from app.services.redis_pool import get_sync_redis

logger = logging.getLogger(__name__)

LOCK_TTL_S = 300
LOCK_PREFIX = "tdmm:backfill-start:"
# (task name, queue) -- queue matches the `app.tasks.pipeline.*` -> cpu route.
BACKFILLS: tuple[tuple[str, str], ...] = (
    ("app.tasks.pipeline.backfill_plates", "cpu"),
    ("app.tasks.pipeline.backfill_glb_orientation", "cpu"),
    ("app.tasks.slicing.backfill_profile_sanitize", "cpu"),
)


def enqueue_startup_backfills(client: Redis | None = None, prefix: str = LOCK_PREFIX) -> list[str]:
    """Enqueue each backfill once per lock TTL; never raises. Returns enqueued names."""
    from app.tasks.celery_app import celery_app

    enqueued: list[str] = []
    try:
        client = client or get_sync_redis(get_settings().redis_url)
        for name, queue in BACKFILLS:
            if client.set(f"{prefix}{name}", "1", nx=True, ex=LOCK_TTL_S):
                celery_app.send_task(name, queue=queue)
                enqueued.append(name)
    except Exception:
        logger.warning("startup backfill enqueue skipped", exc_info=True)
    return enqueued
