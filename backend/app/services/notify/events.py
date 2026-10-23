"""Producer side of the notification pipeline (Q6): ``emit`` pushes onto the
Redis list ``notify:queue``; the Celery ``notify.deliver_pending`` beat task
consumes it. Emitting never raises and never blocks past a short socket
timeout, so printerd's MQTT callbacks and API handlers are safe callers."""

from __future__ import annotations

import json
import logging
import threading

import redis

from app.config import get_settings

log = logging.getLogger(__name__)

QUEUE_KEY = "notify:queue"
MAX_QUEUE = 1000


def emit(event: str, printer_id: int | None = None, payload: dict | None = None) -> bool:
    try:
        r = redis.Redis.from_url(
            get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2
        )
        try:
            item = json.dumps(
                {"event": event, "printer_id": printer_id, "payload": payload or {}}, default=str
            )
            r.rpush(QUEUE_KEY, item)
            r.ltrim(QUEUE_KEY, -MAX_QUEUE, -1)
        finally:
            r.close()
        return True
    except Exception as exc:
        log.warning("notify: emit(%s) failed: %s", event, type(exc).__name__)
        return False


def emit_bg(
    event: str, printer_id: int | None = None, payload: dict | None = None
) -> threading.Thread:
    """``emit`` on a throwaway daemon thread (sensor_tick pattern)."""
    t = threading.Thread(
        target=emit, args=(event, printer_id, payload), daemon=True, name=f"notify-{event}"
    )
    t.start()
    return t
