"""Camera viewer/frame Redis state (P4 "Live camera and timelapse" plan,
Task 2). Single source of these key names + timings -- printerd's
``CameraSupervisor``, the camera API routes, and the archive snapshot task
all read/write through here, never by hand-building a key string.

Sync functions (printerd, Celery) use a plain ``redis.Redis``; the
``a``-prefixed functions (FastAPI routes) use ``redis.asyncio.Redis``. Both
halves share the same key helpers below.
"""

from __future__ import annotations

import time

VIEWER_TTL_S = 10
HEARTBEAT_S = 5
FRAME_TTL_S = 5
JOB_FRAME_TTL_S = 3600
VIEWERS_KEY_TTL_S = 60


def viewers_key(printer_id: int) -> str:
    return f"printer:{printer_id}:camera:viewers"


def frame_key(printer_id: int) -> str:
    return f"printer:{printer_id}:camera:frame"


def frame_channel(printer_id: int) -> str:
    return f"printer:{printer_id}:camera:frames"


def status_key(printer_id: int) -> str:
    return f"printer:{printer_id}:camera:status"


def job_frame_key(printer_id: int, job_id: int) -> str:
    return f"printer:{printer_id}:camera:job:{job_id}"


# -- sync (printerd / Celery) -------------------------------------------


def add_viewer(r, printer_id: int, viewer_id: str, *, now: float | None = None) -> None:
    t = now if now is not None else time.time()
    key = viewers_key(printer_id)
    r.zadd(key, {viewer_id: t + VIEWER_TTL_S})
    r.expire(key, VIEWERS_KEY_TTL_S)


def remove_viewer(r, printer_id: int, viewer_id: str) -> None:
    r.zrem(viewers_key(printer_id), viewer_id)


def viewer_count(r, printer_id: int, *, now: float | None = None) -> int:
    t = now if now is not None else time.time()
    key = viewers_key(printer_id)
    r.zremrangebyscore(key, "-inf", t)
    return r.zcard(key)


def put_frame(r, printer_id: int, jpeg: bytes) -> None:
    key = frame_key(printer_id)
    pipe = r.pipeline()
    pipe.set(key, jpeg, ex=FRAME_TTL_S)
    pipe.publish(frame_channel(printer_id), jpeg)
    pipe.execute()


def get_frame(r, printer_id: int) -> bytes | None:
    return r.get(frame_key(printer_id))


def set_status(r, printer_id: int, status: str) -> None:
    r.set(status_key(printer_id), status, ex=15)


def get_status(r, printer_id: int) -> str | None:
    value = r.get(status_key(printer_id))
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else value


def keep_job_frame(r, printer_id: int, job_id: int) -> bool:
    dest = job_frame_key(printer_id, job_id)
    if not r.copy(frame_key(printer_id), dest, replace=True):
        return False
    return bool(r.expire(dest, JOB_FRAME_TTL_S))


def wait_for_frame(r, printer_id: int, *, timeout: float = 12.0, viewer_id: str) -> bytes | None:
    """Register ``viewer_id`` (so a worker starts), poll for a frame every
    0.5s, then remove the viewer registration on the way out -- including
    on timeout."""
    add_viewer(r, printer_id, viewer_id)
    deadline = time.monotonic() + timeout
    try:
        while True:
            frame = get_frame(r, printer_id)
            if frame is not None:
                return frame
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.5)
    finally:
        remove_viewer(r, printer_id, viewer_id)


# -- async (API) ----------------------------------------------------------


async def aadd_viewer(r, printer_id: int, viewer_id: str, *, now: float | None = None) -> None:
    t = now if now is not None else time.time()
    key = viewers_key(printer_id)
    await r.zadd(key, {viewer_id: t + VIEWER_TTL_S})
    await r.expire(key, VIEWERS_KEY_TTL_S)


async def aremove_viewer(r, printer_id: int, viewer_id: str) -> None:
    await r.zrem(viewers_key(printer_id), viewer_id)


async def aget_frame(r, printer_id: int) -> bytes | None:
    return await r.get(frame_key(printer_id))


async def aget_status(r, printer_id: int) -> str | None:
    value = await r.get(status_key(printer_id))
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else value
