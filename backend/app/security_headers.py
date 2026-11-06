"""Baseline response headers: ``nosniff`` and same-origin framing only.

Added only when a route has not set its own (the OrcaSlicer proxy and inline
file previews ship their own Content-Security-Policy)."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def frame_ancestors_policy() -> tuple[str, bool]:
    """``(frame-ancestors directive, send X-Frame-Options)``. XFO cannot express
    an origin list, so it is dropped once extra origins are configured."""
    from app.config import get_settings

    extra = " ".join(get_settings().frame_ancestors.replace(",", " ").split())
    return (f"frame-ancestors 'self' {extra}" if extra else "frame-ancestors 'self'"), not extra


def slicer_headers(host: str) -> list[tuple[str, str]]:
    """Headers for EVERY /slicer/ proxy response (any content-type: SVG/XHTML
    can run script too). No `sandbox` (the sidecar websocket needs same-origin +
    cookie); connect-src is pinned to the proxy path so sidecar script cannot
    reach /api."""
    fa, xfo = frame_ancestors_policy()
    connect = " ".join(f"{s}://{host}/slicer/" for s in ("http", "https", "ws", "wss"))
    csp = (
        f"{fa}; connect-src {connect}; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-src 'none'; worker-src 'self' blob:"
    )
    out = [("Content-Security-Policy", csp), ("X-Content-Type-Options", "nosniff")]
    if xfo:
        out.append(("X-Frame-Options", "SAMEORIGIN"))
    return out


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # /slicer/* is the OrcaSlicer proxy: it sets its own headers.
        if scope["type"] != "http" or scope["path"].startswith("/slicer/"):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                fa, xfo = frame_ancestors_policy()
                if "content-security-policy" not in headers:
                    headers["Content-Security-Policy"] = fa
                if xfo:
                    headers.setdefault("X-Frame-Options", "SAMEORIGIN")
                elif "x-frame-options" in headers:
                    del headers["x-frame-options"]
            await send(message)

        await self.app(scope, receive, send_wrapper)
