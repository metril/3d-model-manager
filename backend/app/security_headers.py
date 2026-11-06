"""Baseline response headers: ``nosniff`` and same-origin framing only.

Added only when a route has not set its own (the OrcaSlicer proxy and inline
file previews ship their own Content-Security-Policy)."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


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
                if "content-security-policy" not in headers:
                    headers["Content-Security-Policy"] = "frame-ancestors 'self'"
                headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            await send(message)

        await self.app(scope, receive, send_wrapper)
