"""Thin REST client for Home Assistant's `/api/` surface (P8 Task 4). Bearer
token auth, 5s timeout. Every raised exception carries a fixed, static
message -- never `str(exc)`/the request/response object, which could carry
the `Authorization` header -- mirroring the P2 access-code logging rule
(see `test_ha_token_never_logged`).
"""

from __future__ import annotations

import httpx


class HaUnreachable(Exception):
    """Home Assistant could not be reached at all (connection/timeout), or
    responded with an auth failure or server error. Callers (`poll_once`)
    treat this as "fail open": clear alerts, leave the queue running."""


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
        response = self._get("/api/")
        try:
            data = response.json()
        except ValueError:
            return ""
        return str(data.get("message", "")) if isinstance(data, dict) else ""

    def state(self, entity_id: str) -> dict | None:
        """GET /api/states/<entity_id>. `None` for an unknown entity (404);
        a transport failure, a 401, or any 5xx raises `HaUnreachable`."""
        response = self._get(f"/api/states/{entity_id}")
        if response.status_code == 404:
            return None
        return response.json()

    def _get(self, path: str) -> httpx.Response:
        try:
            response = self._http.get(path)
        except httpx.TransportError as exc:
            raise HaUnreachable("could not reach home assistant") from exc
        if response.status_code == 404:
            return response
        if response.status_code == 401 or response.status_code >= 500:
            raise HaUnreachable(f"home assistant returned {response.status_code}") from None
        response.raise_for_status()
        return response
