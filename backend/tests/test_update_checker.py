"""Q7 update checker: GitHub release fetch, version compare, cache, route."""

from __future__ import annotations

import httpx
import respx

from app.config import get_settings
from app.services import updates
from app.tasks import base
from app.tasks.ops import updates_check


def test_is_newer():
    assert updates.is_newer("v1.2.0", "1.1.9")
    assert not updates.is_newer("v1.2.0", "1.2.0")
    assert not updates.is_newer("v1.0", "dev")
    assert not updates.is_newer(None, "1.0")


@respx.mock
async def test_check_caches_and_route_serves(authenticated_client, monkeypatch):
    monkeypatch.setenv("APP_VERSION", "1.0.0")
    get_settings.cache_clear()
    respx.get(updates.RELEASES_URL).respond(
        json={"tag_name": "v1.1.0", "html_url": "https://gh/rel", "body": "notes"}
    )
    out = updates_check.delay().get()
    assert out["update_available"] is True
    r = await authenticated_client.get("/api/system/update")
    body = r.json()
    assert body["latest"] == "v1.1.0" and body["current"] == "1.0.0"
    assert body["url"] == "https://gh/rel" and body["notes"] == "notes"
    assert body["update_available"] is True


@respx.mock
async def test_fetch_failure_is_cached_as_error(authenticated_client):
    respx.get(updates.RELEASES_URL).mock(side_effect=httpx.ConnectError("x"))
    with base.sync_session() as s:
        cache = updates.check(s, get_settings())
    assert cache["error"] and cache["update_available"] is False
    assert (await authenticated_client.get("/api/system/update")).json()["error"]


async def test_route_without_cache(authenticated_client):
    r = await authenticated_client.get("/api/system/update")
    assert r.status_code == 200 and r.json()["latest"] is None
