"""GET /admin/status: unified service health."""

import json
import time

import httpx
import pytest
import redis as redis_lib

from app.services import service_status as ss


async def test_admin_only(viewer_client: httpx.AsyncClient) -> None:
    assert (await viewer_client.get("/api/admin/status")).status_code == 403


async def test_status_shape_and_heartbeats(
    authenticated_client: httpx.AsyncClient,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ss, "_inspect_workers", lambda: {"w1": {"io"}, "w2": {"cpu", "io"}})
    r = redis_lib.Redis.from_url(redis_url)
    r.delete(ss.CACHE_KEY)
    r.set(ss.BEAT_KEY, json.dumps({"ts": time.time() - 5}))
    r.set(ss.PRINTERD_KEY, json.dumps({"ts": time.time(), "workers": 2}))
    try:
        body = (await authenticated_client.get("/api/admin/status?refresh=true")).json()
        svc = body["services"]
        assert list(svc) == list(ss.ORDER)
        assert svc["db"]["state"] == "ok" and svc["db"]["version"]
        assert svc["redis"]["ok"] is True
        assert svc["worker_io"]["ok"] and svc["worker_cpu"]["ok"]
        assert svc["beat"]["ok"] is True
        assert svc["printerd"]["ok"] is True and "2 printer" in svc["printerd"]["detail"]
        assert svc["slicer"]["state"] in ("off", "ok", "down")
        assert svc["virtual_printer"]["state"] == "down"  # no status published in tests
        assert svc["smtp"]["state"] == "off" and svc["spoolman"]["state"] == "off"
        assert all(s["checked_at"] > 0 for s in svc.values())
        assert body["overall"] == "down"

        # cached within the TTL: a heartbeat going stale is not seen until expiry
        r.set(ss.BEAT_KEY, json.dumps({"ts": time.time() - 999}))
        cached = (await authenticated_client.get("/api/admin/status")).json()
        assert cached["services"]["beat"]["ok"] is True
        fresh = (await authenticated_client.get("/api/admin/status?refresh=true")).json()
        assert fresh["services"]["beat"]["state"] == "down"
    finally:
        r.delete(ss.CACHE_KEY, ss.BEAT_KEY, ss.PRINTERD_KEY)
        r.close()


async def test_missing_workers_and_probe_timeout(
    authenticated_client: httpx.AsyncClient,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> dict:
        raise RuntimeError("broker gone")

    monkeypatch.setattr(ss, "_inspect_workers", boom)
    r = redis_lib.Redis.from_url(redis_url)
    try:
        svc = (await authenticated_client.get("/api/admin/status?refresh=true")).json()["services"]
        assert svc["worker_io"] == {**svc["worker_io"], "ok": False, "state": "down"}
        assert svc["worker_io"]["detail"] == "RuntimeError"
        assert svc["worker_cpu"]["state"] == "down"
    finally:
        r.delete(ss.CACHE_KEY)
        r.close()
