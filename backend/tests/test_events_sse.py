"""``GET /api/events`` SSE endpoint (Global Constraints: Redis pub/sub
channel ``tdmm:events``, JSON event shape; Task 6 interface decision).

Uses a real (loopback) uvicorn server rather than the usual
``ASGITransport``-backed ``client`` fixture: ``ASGITransport.handle_async_request``
awaits the whole ASGI app call to completion before handing back ANY
response bytes (see its source), which never happens for a deliberately
never-ending SSE stream -- a real socket is required to observe genuinely
incremental streaming.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import redis.asyncio as aioredis
import uvicorn

from app.config import get_settings
from app.main import create_app
from app.services.events import publish_job_event

ADMIN_USERNAME = "sse-admin"
ADMIN_PASSWORD = "sse-admin-password"


@pytest.fixture
async def live_client(
    migrated_db: str,
    redis_url: str,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """A real HTTP client against a real uvicorn server bound to loopback,
    already logged in as a freshly lifespan-bootstrapped admin user.
    """
    monkeypatch.setenv("ADMIN_USERNAME", ADMIN_USERNAME)
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    get_settings.cache_clear()

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="on", log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]

        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            login = await client.post(
                "/api/auth/login",
                json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
            )
            assert login.status_code == 204, login.text
            yield client
    finally:
        server.should_exit = True
        await task
        get_settings.cache_clear()


async def test_sse_forwards_a_published_job_event(
    live_client: httpx.AsyncClient,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Short heartbeat so the test can deterministically wait for "the
    # endpoint has subscribed" (its first heartbeat) before publishing,
    # instead of racing a fixed sleep against the subscribe call.
    from app.api import events

    async def _fast(_db):
        return 0.05

    monkeypatch.setattr(events, "_heartbeat_s", _fast)

    async def _consume() -> tuple[dict, uuid.UUID]:
        async with live_client.stream("GET", "/api/events") as response:
            assert response.status_code == 200
            lines = response.aiter_lines()

            async for line in lines:
                if line.startswith(":"):
                    break

            published_job_id = uuid.uuid4()
            await publish_job_event(
                redis_url,
                job_id=published_job_id,
                job_type="store_to_backend",
                state="done",
                subject_type="file",
                subject_id=42,
            )

            async for line in lines:
                if line.startswith("data:"):
                    return json.loads(line[len("data:") :].strip()), published_job_id
            raise AssertionError("stream ended before the published event arrived")

    try:
        event, published_job_id = await asyncio.wait_for(_consume(), timeout=10)
    finally:
        get_settings.cache_clear()

    assert event["type"] == "job.updated"
    assert event["job_id"] == str(published_job_id)
    assert event["job_type"] == "store_to_backend"
    assert event["state"] == "done"
    assert event["subject_type"] == "file"
    assert event["subject_id"] == 42


async def _poll_until(predicate, *, timeout: float = 5.0, interval: float = 0.01) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(interval)


async def test_sse_client_disconnect_closes_pubsub(
    live_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backlog fold (M1 final review): a client disconnecting mid-stream
    cancels ``_event_stream``'s generator from inside an already-cancelled
    scope -- without ``anyio.CancelScope(shield=True)`` around the cleanup
    awaits in ``app.api.events._event_stream``, ``pubsub.aclose()`` would
    never run and the Redis connection would leak. Pins that fix by spying
    on ``PubSub.aclose`` and proving a real client disconnect still triggers
    it.
    """
    from app.api import events

    async def _fast(_db):
        return 0.05

    monkeypatch.setattr(events, "_heartbeat_s", _fast)

    aclose_calls = 0
    original_aclose = aioredis.client.PubSub.aclose

    async def _counting_aclose(self, *args, **kwargs):
        nonlocal aclose_calls
        aclose_calls += 1
        return await original_aclose(self, *args, **kwargs)

    monkeypatch.setattr(aioredis.client.PubSub, "aclose", _counting_aclose)

    try:
        async with live_client.stream("GET", "/api/events") as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith(":"):
                    break  # first heartbeat -> the endpoint has subscribed
        # Exiting the `async with` block above closes the httpx stream
        # (and the underlying socket) before the server-side generator would
        # ever finish on its own -- a genuine client disconnect, mid-flight.
        await asyncio.wait_for(_poll_until(lambda: aclose_calls >= 1), timeout=5)
    finally:
        get_settings.cache_clear()

    assert aclose_calls >= 1


async def test_sse_sends_heartbeat_when_idle(
    live_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import events

    async def _fast(_db):
        return 0.05

    monkeypatch.setattr(events, "_heartbeat_s", _fast)

    async def _first_nonblank_line() -> str:
        async with live_client.stream("GET", "/api/events") as response:
            async for line in response.aiter_lines():
                if line:
                    return line
        raise AssertionError("stream ended without output")

    try:
        line = await asyncio.wait_for(_first_nonblank_line(), timeout=5)
    finally:
        get_settings.cache_clear()

    assert line.startswith(":")
