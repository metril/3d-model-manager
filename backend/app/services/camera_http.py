"""Camera HTTP core shared by the session routes (``app.api.printers``) and
the API-key routes (``app.api.ext_v2``): the MJPEG generator and the
snapshot/stream responses. Authorization stays with the callers."""

from __future__ import annotations

import io
import time
import uuid
from collections.abc import AsyncIterator

import anyio
import redis.asyncio as aioredis
from fastapi import HTTPException, status
from fastapi.responses import Response, StreamingResponse

from app.config import Settings
from app.printers.camera_select import DEFAULT_FPS, camera_kind, rotation_of
from app.services import camera_state as cs

# How often ``mjpeg_parts`` re-registers its viewer (refreshes the Redis
# ZSET TTL) while streaming, and how long it waits with no new frame before
# giving up and ending the stream -- module-level so tests can monkeypatch
# ``STREAM_IDLE_S`` down for a fast "ends when idle" test.
HEARTBEAT_S = cs.HEARTBEAT_S
MAX_FPS = 15


def rotate_jpeg(jpeg: bytes, degrees: int) -> bytes:
    """Rotate clockwise by ``degrees`` (90/180/270) with Pillow."""
    from PIL import Image

    img = Image.open(io.BytesIO(jpeg))
    method = {
        90: Image.Transpose.ROTATE_270,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_90,
    }[degrees]
    out = io.BytesIO()
    img.transpose(method).save(out, format="JPEG", quality=85)
    return out.getvalue()


STREAM_IDLE_S = 20.0


def _mjpeg_part(jpeg: bytes) -> bytes:
    return (
        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpeg)
        + jpeg
        + b"\r\n"
    )


async def mjpeg_parts(
    redis, printer_id: int, viewer_id: str, min_interval: float = 0.0
) -> AsyncIterator[bytes]:
    """Yield ``multipart/x-mixed-replace`` parts for ``printer_id``'s live
    camera feed. Registers ``viewer_id`` (so printerd's camera worker starts
    or stays up), subscribes the frame pub/sub channel, yields whatever
    frame is already current (if any), then yields each newly-published
    frame as it arrives -- heartbeating the viewer registration every
    ``HEARTBEAT_S`` and ending the stream after ``STREAM_IDLE_S`` with no
    frame at all. Always unregisters the viewer and tears down the
    subscription on the way out, including when the caller ``aclose()``s
    this generator early (e.g. the client disconnected).
    """
    await cs.aadd_viewer(redis, printer_id, viewer_id)
    pubsub = redis.pubsub()
    await pubsub.subscribe(cs.frame_channel(printer_id))
    try:
        frame = await cs.aget_frame(redis, printer_id)
        now = time.monotonic()
        last_frame_at = now
        last_heartbeat_at = now
        last_yield_at = now
        if frame is not None:
            yield _mjpeg_part(frame)

        while True:
            message = await pubsub.get_message(timeout=1.0)
            now = time.monotonic()
            if message is not None and message["type"] == "message":
                last_frame_at = now
                if now - last_yield_at >= min_interval:
                    last_yield_at = now
                    yield _mjpeg_part(message["data"])

            if now - last_heartbeat_at >= HEARTBEAT_S:
                await cs.aadd_viewer(redis, printer_id, viewer_id)
                last_heartbeat_at = now

            if now - last_frame_at >= STREAM_IDLE_S:
                return
    finally:
        # Shielded: when the caller `aclose()`s this generator early (a
        # disconnecting client cancels the task driving it), the ambient
        # cancellation would otherwise also cancel this cleanup partway
        # through -- leaking the viewer registration and the pubsub
        # subscription instead of releasing them.
        with anyio.CancelScope(shield=True):
            await cs.aremove_viewer(redis, printer_id, viewer_id)
            await pubsub.unsubscribe(cs.frame_channel(printer_id))
            await pubsub.aclose()


async def stream_response(
    settings: Settings, printer_id: int, options: dict, fps: int | None = None
) -> StreamingResponse:
    """MJPEG ``multipart/x-mixed-replace`` response reusing printerd's camera
    worker; 404 when the camera is unsupported."""
    if fps is None and camera_kind(None, None, options) == "external":
        fps = DEFAULT_FPS
    redis = aioredis.Redis.from_url(settings.redis_url)
    try:
        if await cs.aget_status(redis, printer_id) == "unsupported":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "camera not supported")
    except Exception:
        await redis.aclose()
        raise
    viewer_id = f"stream:{uuid.uuid4()}"

    async def _gen() -> AsyncIterator[bytes]:
        try:
            async for part in mjpeg_parts(redis, printer_id, viewer_id, 1.0 / fps if fps else 0.0):
                yield part
        finally:
            await redis.aclose()

    return StreamingResponse(
        _gen(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def snapshot_response(settings: Settings, printer_id: int, options: dict) -> Response:
    """Current JPEG frame (rotated per ``options``); 503 + ``Retry-After``
    while the camera warms up, 404 when unsupported."""
    redis = aioredis.Redis.from_url(settings.redis_url)
    try:
        if await cs.aget_status(redis, printer_id) == "unsupported":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "camera not supported")

        # Register a viewer even on a cold camera (no removal -- the next
        # heartbeat/stream viewer or its own TTL cleans it up) so printerd's
        # on-demand worker starts warming up for the *next* request.
        await cs.aadd_viewer(redis, printer_id, f"snap:{uuid.uuid4()}")

        frame = await cs.aget_frame(redis, printer_id)
        if frame is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "camera is warming up",
                headers={"Retry-After": "2"},
            )
        rotation = rotation_of(options)
        if rotation:
            frame = await anyio.to_thread.run_sync(rotate_jpeg, frame, rotation)
        return Response(
            content=frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )
    finally:
        await redis.aclose()
