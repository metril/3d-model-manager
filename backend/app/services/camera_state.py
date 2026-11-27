"""Camera viewer/frame Redis state (P4 "Live camera and timelapse" plan,
Task 2). Single source of these key names + timings -- printerd's
``CameraSupervisor``, the camera API routes, and the archive snapshot task
all read/write through here, never by hand-building a key string.

Sync functions (printerd, Celery) use a plain ``redis.Redis``; the
``a``-prefixed functions (FastAPI routes) use ``redis.asyncio.Redis``. Both
halves share the same key helpers below.
"""

from __future__ import annotations

import struct
import time

VIEWER_TTL_S = 10
HEARTBEAT_S = 5
FRAME_TTL_S = 5
JOB_FRAME_TTL_S = 3600
VIEWERS_KEY_TTL_S = 60
# Newest frame older than this while the worker is still registered = stalled.
STALL_AFTER_S = 10.0
# Outlives the camera status key (15 s) so a stalled worker is still detectable.
FRAME_TS_TTL_S = 60
_TS_HDR = struct.Struct(">Q")


def viewers_key(printer_id: int) -> str:
    return f"printer:{printer_id}:camera:viewers"


def frame_key(printer_id: int) -> str:
    return f"printer:{printer_id}:camera:frame"


def frame_channel(printer_id: int) -> str:
    return f"printer:{printer_id}:camera:frames"


def status_key(printer_id: int) -> str:
    return f"printer:{printer_id}:camera:status"


def frame_ts_key(printer_id: int) -> str:
    return f"printer:{printer_id}:camera:frame_ts"


def pack_frame(jpeg: bytes, ts_ms: int) -> bytes:
    """Pub/sub payload: 8-byte big-endian capture time (epoch ms) + JPEG."""
    return _TS_HDR.pack(ts_ms) + jpeg


def unpack_frame(data: bytes) -> tuple[bytes, int | None]:
    """Inverse of ``pack_frame``. A bare JPEG (old publisher, starts with
    FFD8; a packed header starts with 0x00) yields ``(data, None)``."""
    if data[:2] == b"\xff\xd8" or len(data) <= _TS_HDR.size:
        return data, None
    return data[_TS_HDR.size :], _TS_HDR.unpack_from(data)[0]


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


def put_frame(r, printer_id: int, jpeg: bytes, *, ts_ms: int | None = None) -> None:
    """Store + publish a frame with its wall-clock capture time (epoch ms),
    all in one MULTI so the frame and its timestamp never disagree."""
    ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
    pipe = r.pipeline()
    pipe.set(frame_key(printer_id), jpeg, ex=FRAME_TTL_S)
    pipe.set(frame_ts_key(printer_id), ts, ex=FRAME_TS_TTL_S)
    pipe.publish(frame_channel(printer_id), pack_frame(jpeg, ts))
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


async def aget_frame_with_ts(r, printer_id: int) -> tuple[bytes | None, int | None]:
    """Current frame + its capture time (epoch ms), read atomically."""
    frame, ts = await r.mget([frame_key(printer_id), frame_ts_key(printer_id)])
    return frame, (int(ts) if frame is not None and ts is not None else None)


async def acamera_state(r, printer_id: int, *, now: float | None = None) -> str:
    """``off | warming | live | stalled`` from the camera status key, newest
    frame time and live viewers. Stable between transitions (no timestamps)."""
    t = now if now is not None else time.time()
    pipe = r.pipeline(transaction=False)
    pipe.get(status_key(printer_id))
    pipe.get(frame_ts_key(printer_id))
    pipe.zcount(viewers_key(printer_id), t, "+inf")
    status, ts, viewers = await pipe.execute()
    status = status.decode() if isinstance(status, bytes) else status
    if status == "unsupported":
        return "off"
    if status is None:
        # No worker reporting: a waiting viewer means one is about to start.
        return "warming" if viewers else "off"
    if status != "live":
        return "warming"
    if ts is not None and t - int(ts) / 1000.0 <= STALL_AFTER_S:
        return "live"
    return "stalled"
