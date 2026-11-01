"""Reverse proxy for the OrcaSlicer (Selkies) sidecar under ``/slicer/``.

Registered OUTSIDE ``/api`` and before the SPA catch-all. HTTP is streamed
through ``httpx``; WebSockets (``/slicer/api/websockets`` etc.) are bridged
with the ``websockets`` library. Both require an ``admin`` session: the OrcaSlicer
desktop is a root shell on the compose network, so it must not be reachable by
members. WebSocket auth is checked at connect only (not re-validated while the
socket stays open). The session cookie and ``Authorization`` header are never
forwarded, so the sidecar cannot see our credentials. 404 while
``orcaslicer_enabled`` is off.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from urllib.parse import quote, unquote, urlsplit

import httpx
import websockets
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask
from starlette.websockets import WebSocketDisconnect, WebSocketState
from websockets.exceptions import ConnectionClosed, WebSocketException

from app.api.deps import SESSION_COOKIE_NAME, AuthContext, require_perm, require_session
from app.db import get_db, get_sessionmaker
from app.services.server_config import ServerConfig, get_server_config

log = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

WS_UNAUTHENTICATED = 4401
WS_FORBIDDEN_ORIGIN = 4403
WS_DISABLED = 4404

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_REQ_DROP = _HOP_BY_HOP | {"cookie", "authorization", "host", "content-length"}
_NO_CODE = frozenset({1005, 1006, 1015})

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """Process-wide upstream client (created lazily, closed in the lifespan)."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(None, pool=10),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
    return _client


async def close_client() -> None:
    global _client
    client, _client = _client, None
    if client is not None:
        await client.aclose()


def _upstream_url(cfg: ServerConfig, path: str, query: str) -> str:
    decoded = unquote(unquote(path)).replace("\\", "/")
    if any(seg == ".." for seg in decoded.split("/")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid path")
    url = f"{cfg.orcaslicer_url.rstrip('/')}/slicer/{quote(path, safe='/:@!$&()*+,;=-._~%')}"
    return f"{url}?{query}" if query else url


async def _body(request: Request) -> AsyncIterator[bytes]:
    async for chunk in request.stream():
        yield chunk


@router.api_route(
    "/slicer/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"],
    dependencies=[Depends(require_perm("admin"))],
)
async def proxy_http(
    path: str, request: Request, db: AsyncSession = Depends(get_db)
) -> StreamingResponse:
    cfg = await get_server_config(db)
    if not cfg.orcaslicer_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "orcaslicer disabled")
    headers = [(k, v) for k, v in request.headers.items() if k.lower() not in _REQ_DROP]
    has_body = "content-length" in request.headers or "transfer-encoding" in request.headers
    client = get_client()
    upstream_req = client.build_request(
        request.method,
        _upstream_url(cfg, path, request.url.query),
        headers=headers,
        content=_body(request) if has_body else None,
    )
    try:
        upstream = await client.send(upstream_req, stream=True)
    except httpx.HTTPError as exc:
        log.warning("orcaslicer upstream error: %s", type(exc).__name__)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "orcaslicer unreachable") from None
    raw = [(k, v) for k, v in upstream.headers.multi_items() if k.lower() not in _HOP_BY_HOP]
    if upstream.headers.get("content-type", "").lower().startswith("text/html"):
        raw = [
            (k, v)
            for k, v in raw
            if k.lower() not in ("content-security-policy", "x-frame-options")
        ]
        raw += [
            # Always 'self': there is no server-config field for frame-ancestors.
            ("Content-Security-Policy", "frame-ancestors 'self'"),
            ("X-Frame-Options", "SAMEORIGIN"),
        ]
    response = StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        background=BackgroundTask(upstream.aclose),
    )
    response.raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in raw]
    return response


async def _ws_auth(websocket: WebSocket) -> AuthContext | None:
    """Cookie-based equivalent of ``require_perm("admin")``."""
    cookie = websocket.cookies.get(SESSION_COOKIE_NAME)
    if cookie is None:
        return None
    async with get_sessionmaker()() as db:
        try:
            ctx = await require_session(None, cookie, db)  # type: ignore[arg-type]
        except HTTPException:
            return None
    return ctx if ctx.can("admin") else None


def _same_origin(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc == websocket.headers.get("host", "")


def _ws_code(code: int | None) -> int:
    return 1000 if code is None or code in _NO_CODE else code


@router.websocket("/slicer/{path:path}")
async def proxy_ws(websocket: WebSocket, path: str) -> None:
    if not _same_origin(websocket):
        await websocket.close(code=WS_FORBIDDEN_ORIGIN)
        return
    if await _ws_auth(websocket) is None:
        await websocket.close(code=WS_UNAUTHENTICATED)
        return
    async with get_sessionmaker()() as db:
        cfg = await get_server_config(db)
    if not cfg.orcaslicer_enabled:
        await websocket.close(code=WS_DISABLED)
        return

    base = urlsplit(cfg.orcaslicer_url)
    scheme = "wss" if base.scheme == "https" else "ws"
    try:
        uri = _upstream_url(cfg, path, websocket.url.query).replace(base.scheme, scheme, 1)
    except HTTPException:
        await websocket.close(code=1008)
        return
    offered = list(websocket.scope.get("subprotocols") or [])
    try:
        upstream = await websockets.connect(
            uri,
            subprotocols=offered or None,
            origin=f"{base.scheme}://{base.netloc}",
            max_size=None,
            ping_interval=20,
            ping_timeout=30,
            open_timeout=10,
        )
    except (OSError, WebSocketException, TimeoutError) as exc:
        log.warning("orcaslicer ws upstream error: %s", type(exc).__name__)
        await websocket.close(code=1011)
        return

    await websocket.accept(subprotocol=upstream.subprotocol)

    async def client_to_upstream() -> int | None:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                return msg.get("code")
            if msg.get("text") is not None:
                await upstream.send(msg["text"])
            elif msg.get("bytes") is not None:
                await upstream.send(msg["bytes"])

    async def upstream_to_client() -> int | None:
        with contextlib.suppress(ConnectionClosed):
            async for message in upstream:
                if isinstance(message, str):
                    await websocket.send_text(message)
                else:
                    await websocket.send_bytes(bytes(message))
        return upstream.close_code

    tasks = [
        asyncio.create_task(client_to_upstream()),
        asyncio.create_task(upstream_to_client()),
    ]
    client_code: int | None = None
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if tasks[0] in done and not tasks[0].cancelled() and tasks[0].exception() is None:
            client_code = tasks[0].result()
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await upstream.close(code=_ws_code(client_code))
        if websocket.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await websocket.close(code=_ws_code(upstream.close_code))
