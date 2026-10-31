"""Server-Sent Events endpoint (SPEC "API surface"; Global Constraints: SSE
at ``GET /api/events``, Redis pub/sub channel ``tdmm:events``).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import anyio
import redis.asyncio as aioredis
from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import ext_v2
from app.api.deps import SESSION_COOKIE_NAME, require_session, visible_printer_ids
from app.config import Settings, get_settings
from app.db import get_db
from app.models.printing import Printer
from app.services import api_tokens, printer_stream
from app.services.events import CHANNEL
from app.services.printer_stream import PrinterStream
from app.services.server_config import get_server_config

router = APIRouter(tags=["events"])


PING_MAX_S = 15.0


async def _event_stream(
    redis_url: str,
    heartbeat_interval: float,
    *,
    settings: Settings | None = None,
    printer_ids: list[int] | None = None,
    key_stream: bool = False,
) -> AsyncIterator[bytes]:
    """``printer_ids`` (already ACL-intersected) enables ``printer.*`` events;
    ``key_stream`` restricts the stream to ``print_job.updated`` + ``printer.*``
    for those printers."""
    client = aioredis.Redis.from_url(redis_url)
    pubsub = client.pubsub()
    await pubsub.subscribe(CHANNEL)
    ids = set(printer_ids or [])
    streamer = PrinterStream(settings, client, sorted(ids)) if ids and settings else None
    heartbeat = min(heartbeat_interval, PING_MAX_S)
    last_out = time.monotonic()
    next_tick = last_out  # poll immediately: initial printer.status
    try:
        while True:
            now = time.monotonic()
            due = min(next_tick if streamer else now + heartbeat, last_out + heartbeat)
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=max(0.0, due - now)
            )
            if message is not None:
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                if _forward(data, ids, key_stream):
                    last_out = time.monotonic()
                    yield f"data: {data}\n\n".encode()
            if streamer and time.monotonic() >= next_tick:
                next_tick = time.monotonic() + printer_stream.TICK_S
                for ev in await streamer.poll():
                    last_out = time.monotonic()
                    yield f"data: {json.dumps(ev, separators=(',', ':'))}\n\n".encode()
            if time.monotonic() - last_out >= heartbeat:
                last_out = time.monotonic()
                yield b": ping\n\n"
    finally:
        # Client disconnect closes this generator from inside an already-
        # cancelled scope (Starlette cancels the request task, which throws
        # into wherever the generator was suspended -- here, the
        # `get_message` await above). Without shielding, the FIRST await
        # below raises `CancelledError` immediately, skipping
        # `pubsub.aclose()`/`client.aclose()` entirely and leaking the Redis
        # connection. Shield just these cleanup awaits so they run to
        # completion regardless of the outer cancellation.
        with anyio.CancelScope(shield=True):
            await pubsub.unsubscribe(CHANNEL)
            await pubsub.aclose()
            await client.aclose()


def _forward(data: str, ids: set[int], key_stream: bool) -> bool:
    """Whether a pub/sub payload goes out on this stream. Session streams are
    unfiltered; key streams get only allowed printers' print/printer events."""
    if not key_stream:
        return True
    try:
        ev = json.loads(data)
    except ValueError:
        return False
    typ = ev.get("type", "")
    if typ == "print_job.updated":
        printer_stream.invalidate(ev.get("printer_id"))
        return ev.get("printer_id") in ids
    return typ.startswith("printer.") and ev.get("printer_id") in ids


async def _heartbeat_s(db: AsyncSession) -> float:
    return (await get_server_config(db)).sse_heartbeat_s


@dataclass(slots=True)
class StreamAuth:
    is_key: bool
    allowed: set[int] | None  # None = unrestricted (admin session)


async def stream_auth(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    tdmm_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamAuth:
    """Session cookie (web) OR a scoped ``read`` API key (HA)."""
    bearer = authorization is not None and authorization.lower().startswith("bearer ")
    if x_api_key or bearer:
        auth = await ext_v2.require_key(request, response, authorization, x_api_key, db, settings)
        if "read" not in (auth.key.scopes or []):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "missing scope: read")
        if not api_tokens.SCOPE_PERMS["read"] <= auth.ctx.perms:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "key owner lacks the permission")
        return StreamAuth(True, set(auth.ctx.printer_acl))
    ctx = await require_session(request, tdmm_session, db)
    return StreamAuth(False, visible_printer_ids(ctx))


def _parse_ids(raw: str | None) -> set[int] | None:
    if raw is None or not raw.strip():
        return None
    try:
        return {int(p) for p in raw.split(",") if p.strip()}
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "bad printer_ids") from None


@router.get("/events")
async def sse_events(
    printer_ids: str | None = Query(default=None),
    auth: StreamAuth = Depends(stream_auth),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    # Read once per stream open (the session is closed below, not held for the stream's lifetime).
    heartbeat = await _heartbeat_s(db)
    wanted = _parse_ids(printer_ids)
    if auth.is_key:
        ids = auth.allowed if wanted is None else auth.allowed & wanted
    elif wanted is None:
        ids = set()  # web unchanged: no printer.* unless asked
    elif auth.allowed is None:
        existing = (await db.execute(select(Printer.id).where(Printer.id.in_(wanted)))).scalars()
        ids = set(existing)
    else:
        ids = auth.allowed & wanted
    # FastAPI >= 0.118 runs yield-dependency teardown only after the response
    # finishes -- for SSE that is the stream's lifetime -- so release the
    # connection explicitly before streaming.
    await db.close()
    return StreamingResponse(
        _event_stream(
            settings.redis_url,
            heartbeat,
            settings=settings,
            printer_ids=sorted(ids),
            key_stream=auth.is_key,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
