"""Studio sidecar proxy: auth/gating, HTTP pass-through, WebSocket bridge."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator

import httpx
import pytest
import respx
import uvicorn
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve
from websockets.exceptions import InvalidStatus

from app.api import studio_proxy
from app.api.deps import SESSION_COOKIE_NAME
from app.main import create_app
from app.services import server_config as sc

UPSTREAM = "http://studio.test:3000"


@pytest.fixture(autouse=True)
async def _fresh_client() -> AsyncGenerator[None, None]:
    await studio_proxy.close_client()
    yield
    await studio_proxy.close_client()


async def _enable(db_session, url: str = UPSTREAM, **kw) -> None:
    cfg = await sc.get_server_config(db_session)
    await sc.set_server_config(
        db_session, cfg.model_copy(update={"studio_enabled": True, "studio_url": url, **kw})
    )


async def test_requires_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/slicer/")).status_code == 401


async def test_requires_admin(
    viewer_client: httpx.AsyncClient, member_client: httpx.AsyncClient, db_session
) -> None:
    await _enable(db_session)
    assert (await viewer_client.get("/slicer/")).status_code == 403
    # member has library:write but is not admin -> still 403
    assert (await member_client.get("/slicer/")).status_code == 403


@respx.mock
async def test_admin_ok_and_dotdot_rejected(
    authenticated_client: httpx.AsyncClient, db_session
) -> None:
    await _enable(db_session)
    respx.get(f"{UPSTREAM}/slicer/ok").respond(200, text="hi")
    assert (await authenticated_client.get("/slicer/ok")).status_code == 200
    for bad in ("/slicer/a/%2e%2e/x", "/slicer/a/%252e%252e/x"):
        r = await authenticated_client.get(bad)
        assert r.status_code == 400, bad


async def test_disabled_is_404(authenticated_client: httpx.AsyncClient) -> None:
    r = await authenticated_client.get("/slicer/api/x")
    assert r.status_code == 404 and r.json()["detail"] == "studio disabled"


@respx.mock
async def test_http_passthrough(authenticated_client: httpx.AsyncClient, db_session) -> None:
    await _enable(db_session)
    html = respx.get(f"{UPSTREAM}/slicer/").respond(
        200,
        html="<html>studio</html>",
        headers={"Cache-Control": "max-age=60", "Connection": "close", "X-Up": "1"},
    )
    js = respx.get(f"{UPSTREAM}/slicer/app.js").respond(
        200, content=b"x" * 100_000, headers={"Content-Type": "text/javascript"}
    )
    post = respx.post(f"{UPSTREAM}/slicer/api/thing").respond(201, json={"ok": True})

    r = await authenticated_client.get("/slicer/")
    assert r.status_code == 200 and r.text == "<html>studio</html>"
    assert r.headers["content-security-policy"] == "frame-ancestors 'self'"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["cache-control"] == "max-age=60" and r.headers["x-up"] == "1"
    sent = html.calls.last.request
    assert "cookie" not in sent.headers and "authorization" not in sent.headers

    r = await authenticated_client.get("/slicer/app.js", headers={"Authorization": "Bearer x"})
    assert r.content == b"x" * 100_000
    assert "content-security-policy" not in r.headers
    assert "authorization" not in js.calls.last.request.headers

    r = await authenticated_client.post("/slicer/api/thing?a=1", json={"k": "v"})
    assert r.status_code == 201 and r.json() == {"ok": True}
    req = post.calls.last.request
    assert req.url.query == b"a=1" and json.loads(req.content) == {"k": "v"}


@respx.mock
async def test_upstream_down_is_502(authenticated_client: httpx.AsyncClient, db_session) -> None:
    await _enable(db_session)
    respx.get(f"{UPSTREAM}/slicer/").mock(side_effect=httpx.ConnectError("nope"))
    assert (await authenticated_client.get("/slicer/")).status_code == 502


def test_ws_unauthenticated_closes_4401() -> None:
    # No cookie -> rejected before any DB access, so a sync TestClient is safe.
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        TestClient(create_app()).websocket_connect(
            "/slicer/api/websockets", subprotocols=["selkies"]
        ),
    ):
        pass
    assert exc.value.code == 4401


@contextlib.asynccontextmanager
async def _serve_app() -> AsyncGenerator[int, None]:
    """The real app under uvicorn on an ephemeral port, in the test's own loop
    (the cached async engine is loop-bound, so TestClient's thread loop can't
    be used for authenticated WebSockets)."""
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield server.servers[0].sockets[0].getsockname()[1]
    finally:
        server.should_exit = True
        await task


@contextlib.asynccontextmanager
async def _echo_server(seen: dict) -> AsyncGenerator[int, None]:
    async def handler(conn) -> None:
        seen["path"] = conn.request.path
        seen["origin"] = conn.request.headers.get("Origin")
        seen["cookie"] = conn.request.headers.get("Cookie")
        seen["subprotocol"] = conn.subprotocol
        async for msg in conn:
            await conn.send(msg)

    async with ws_serve(handler, "127.0.0.1", 0, subprotocols=["selkies"]) as srv:
        yield srv.sockets[0].getsockname()[1]


async def test_ws_bridge(authenticated_client: httpx.AsyncClient, db_session) -> None:
    seen: dict = {}
    cookie = authenticated_client.cookies[SESSION_COOKIE_NAME]
    async with _echo_server(seen) as up_port:
        await _enable(db_session, f"http://127.0.0.1:{up_port}")
        async with (
            _serve_app() as port,
            ws_connect(
                f"ws://127.0.0.1:{port}/slicer/api/websockets?token=1",
                subprotocols=["selkies"],
                additional_headers={"Cookie": f"{SESSION_COOKIE_NAME}={cookie}"},
            ) as ws,
        ):
            assert ws.subprotocol == "selkies"
            await ws.send("hello")
            assert await ws.recv() == "hello"
            await ws.send(b"\x00\x01\x02")
            assert await ws.recv() == b"\x00\x01\x02"
            await ws.close(code=1000)
    assert seen["path"] == "/slicer/api/websockets?token=1"
    assert seen["origin"] == f"http://127.0.0.1:{up_port}"
    assert seen["cookie"] is None and seen["subprotocol"] == "selkies"


async def test_ws_rejects_without_perm_and_when_disabled(
    authenticated_client: httpx.AsyncClient, member_client: httpx.AsyncClient, db_session
) -> None:
    admin = authenticated_client.cookies[SESSION_COOKIE_NAME]
    viewer = member_client.cookies[SESSION_COOKIE_NAME]

    async def attempt(port: int, cookie: str) -> int:
        with pytest.raises(InvalidStatus) as exc:
            async with ws_connect(
                f"ws://127.0.0.1:{port}/slicer/api/websockets",
                additional_headers={"Cookie": f"{SESSION_COOKIE_NAME}={cookie}"},
            ):
                pass
        return exc.value.response.status_code

    async with _serve_app() as port:
        assert await attempt(port, admin) == 403  # studio disabled
        await _enable(db_session, "http://127.0.0.1:1")
        assert await attempt(port, viewer) == 403  # member: library:write but not admin


def test_upstream_url_rejects_dotdot() -> None:
    from fastapi import HTTPException

    from app.services.server_config import ServerConfig

    cfg = ServerConfig()
    for bad in ("../x", "a/../x", "a/%2e%2e/x", "a/%252e%252e/x", "a\\..\\x"):
        with pytest.raises(HTTPException) as exc:
            studio_proxy._upstream_url(cfg, bad, "")
        assert exc.value.status_code == 400
    assert studio_proxy._upstream_url(cfg, "a..b/c", "").endswith("/slicer/a..b/c")
