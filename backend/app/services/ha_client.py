"""Thin REST client for Home Assistant's `/api/` surface (P8 Task 4). Bearer
token auth, 5s timeout. Every raised exception carries a fixed, static
message -- never `str(exc)`/the request/response object, which could carry
the `Authorization` header or the broker URL -- mirroring the P2
access-code logging rule (see `test_ha_token_never_logged`). Every non-2xx
response except 404 (treated as "entity doesn't exist", not a failure) --
and any malformed-JSON body -- is folded into the same `HaUnreachable`
"fail open" path, so callers never need to handle HA's full status-code
space. The response body is capped at `_MAX_BODY_BYTES` before anything
touches it, so a misbehaving/huge response can't blow up memory.
"""

from __future__ import annotations

import json

import httpx

_MAX_BODY_BYTES = 64 * 1024


class HaUnreachable(Exception):
    """Home Assistant could not be reached at all (connection/timeout),
    responded with any non-2xx status other than 404, or sent a body that
    didn't parse as JSON. Callers (`poll_once`) treat this as "fail open":
    clear alerts, leave the queue running."""


class HaClient:
    def __init__(self, base_url: str, token: str) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )

    def close(self) -> None:
        self._http.close()

    def ping(self) -> str:
        """GET /api/ -- HA's root endpoint. Returns the `message` field
        (`"API running."` on a healthy instance)."""
        data = self._json(self._get("/api/"))
        return str(data.get("message", "")) if isinstance(data, dict) else ""

    def state(self, entity_id: str) -> dict | None:
        """GET /api/states/<entity_id>. `None` for an unknown entity (404);
        any other non-2xx status, a transport failure, or a malformed body
        raises `HaUnreachable`."""
        response = self._get(f"/api/states/{entity_id}")
        if response.status_code == 404:
            return None
        return self._json(response)

    def call_service(self, domain: str, service: str, data: dict) -> None:
        """POST /api/services/<domain>/<service> (Q7 smart plugs)."""
        try:
            response = self._http.post(f"/api/services/{domain}/{service}", json=data)
        except httpx.TransportError as exc:
            raise HaUnreachable("could not reach home assistant") from exc
        if not (200 <= response.status_code < 300):
            raise HaUnreachable(f"home assistant returned {response.status_code}") from None

    def _get(self, path: str) -> httpx.Response:
        try:
            response = self._http.get(path)
        except httpx.TransportError as exc:
            raise HaUnreachable("could not reach home assistant") from exc
        if response.status_code == 404:
            return response
        if not (200 <= response.status_code < 300):
            raise HaUnreachable(f"home assistant returned {response.status_code}") from None
        return response

    @staticmethod
    def _json(response: httpx.Response) -> dict | None:
        body = response.content[:_MAX_BODY_BYTES]
        try:
            return json.loads(body)
        except ValueError as exc:
            raise HaUnreachable("home assistant returned a malformed body") from exc
