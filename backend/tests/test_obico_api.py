import httpx
import pytest
import redis as redis_lib

from app.models import FailureDetection, Printer
from app.services import camera_state as cs
from app.services import obico
from app.services.obico_ml import MlError
from app.tasks.obico import record_event

pytestmark = pytest.mark.usefixtures("printer_enabled")
JPEG = b"\xff\xd8\xff\xe0" + bytes(10) + b"\xff\xd9"


async def test_global_settings_roundtrip(authenticated_client: httpx.AsyncClient) -> None:
    r = await authenticated_client.get("/api/settings/obico")
    assert r.status_code == 200
    assert r.json()["enabled"] is False and r.json()["interval_s"] == 10
    body = {**r.json(), "url": "http://obico-ml:3333/", "enabled": True, "pause": 0.6, "warn": 0.3}
    put = await authenticated_client.put("/api/settings/obico", json=body)
    assert put.status_code == 200 and put.json()["url"] == "http://obico-ml:3333"
    again = await authenticated_client.get("/api/settings/obico")
    assert again.json()["enabled"] is True and again.json()["pause"] == 0.6


async def test_global_settings_validation_and_perm(
    authenticated_client: httpx.AsyncClient, viewer_client: httpx.AsyncClient
) -> None:
    bad = await authenticated_client.put("/api/settings/obico", json={"warn": 0.9, "pause": 0.2})
    assert bad.status_code == 422
    assert (await viewer_client.get("/api/settings/obico")).status_code == 403
    assert (await viewer_client.put("/api/settings/obico", json={})).status_code == 403


async def test_settings_test_connection(
    authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = await authenticated_client.post("/api/settings/obico/test", json={})
    assert r.json() == {"ok": False, "detail": "not_configured"}
    r = await authenticated_client.post(
        "/api/settings/obico/test", json={"url": "http://127.0.0.1:1"}
    )
    assert r.json()["ok"] is False


async def test_printer_detection_toggle(
    authenticated_client: httpx.AsyncClient, printer_row: Printer
) -> None:
    url = f"/api/printers/{printer_row.id}/detection"
    r = await authenticated_client.get(url)
    assert r.json()["enabled"] is False and r.json()["status"]["active"] is False
    put = await authenticated_client.put(url, json={"enabled": True, "action": "pause"})
    assert put.status_code == 200
    assert put.json()["enabled"] is True and put.json()["effective_action"] == "pause"
    assert (await authenticated_client.put(url, json={"action": "bogus"})).status_code == 422
    assert (
        await authenticated_client.put("/api/printers/99999/detection", json={})
    ).status_code == 404


async def test_printer_detection_requires_acl(
    member_client: httpx.AsyncClient, printer_row: Printer
) -> None:
    url = f"/api/printers/{printer_row.id}/detection"
    assert (await member_client.get(url)).status_code == 403
    assert (await member_client.put(url, json={"enabled": True})).status_code == 403
    assert (await member_client.post(url + "/test")).status_code == 403


async def test_detections_list_and_frame(
    authenticated_client: httpx.AsyncClient, printer_row: Printer, redis_url: str, migrated_db
) -> None:
    r = redis_lib.Redis.from_url(redis_url)
    try:
        r.set("obico:evt:t:1", JPEG, ex=60)
        det_id = record_event(printer_row.id, None, 1.2, 0.8, "pause", "obico:evt:t:1")
    finally:
        r.close()
    assert det_id is not None
    out = await authenticated_client.get(f"/api/printers/{printer_row.id}/detections")
    ev = out.json()["events"]
    assert ev[0]["id"] == det_id and ev[0]["action"] == "pause" and ev[0]["has_frame"] is True
    frame = await authenticated_client.get(
        f"/api/printers/{printer_row.id}/detections/{det_id}/frame"
    )
    assert frame.content == JPEG and frame.headers["content-type"] == "image/jpeg"
    miss = await authenticated_client.get(f"/api/printers/{printer_row.id}/detections/9999/frame")
    assert miss.status_code == 404
    assert FailureDetection.__tablename__ == "failure_detections"


async def test_detection_status_from_redis(
    authenticated_client: httpx.AsyncClient, printer_row: Printer, redis_url: str
) -> None:
    r = redis_lib.Redis.from_url(redis_url)
    r.set(
        obico.status_key(printer_row.id),
        '{"state":"warn","score":0.5,"smoothed":0.45,"paused":false}',
    )
    try:
        out = await authenticated_client.get(f"/api/printers/{printer_row.id}/detections")
        assert out.json()["status"] == {
            "active": True,
            "state": "warn",
            "score": 0.5,
            "smoothed": 0.45,
            "detection_paused": False,
        }
        r.set(obico.status_key(printer_row.id), '{"paused":true}')
        out = await authenticated_client.get(f"/api/printers/{printer_row.id}/detections")
        assert out.json()["status"]["detection_paused"] is True
    finally:
        r.delete(obico.status_key(printer_row.id))
        r.close()


async def test_detection_test_endpoint(
    authenticated_client: httpx.AsyncClient,
    printer_row: Printer,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"/api/printers/{printer_row.id}/detection/test"
    assert (await authenticated_client.post(url)).status_code == 422  # no URL yet
    await authenticated_client.put(
        "/api/settings/obico", json={"url": "http://ml:3333", "enabled": True}
    )
    r = redis_lib.Redis.from_url(redis_url)
    cs.put_frame(r, printer_row.id, JPEG)
    monkeypatch.setattr("app.api.obico.score_frame", lambda *a, **k: 0.42)
    try:
        ok = await authenticated_client.post(url)
        assert ok.status_code == 200 and ok.json() == {"score": 0.42}

        def boom(*a, **k):
            raise MlError("ConnectError")

        monkeypatch.setattr("app.api.obico.score_frame", boom)
        assert (await authenticated_client.post(url)).status_code == 502
    finally:
        r.delete(cs.frame_key(printer_row.id))
        r.close()


async def test_public_frame_is_single_use(client: httpx.AsyncClient, redis_url: str) -> None:
    r = redis_lib.Redis.from_url(redis_url)
    r.set(obico.nonce_key("abc"), JPEG, ex=30)
    r.close()
    first = await client.get("/api/obico/frame/abc")
    assert first.status_code == 200 and first.content == JPEG
    assert (await client.get("/api/obico/frame/abc")).status_code == 404


async def test_detections_summary(
    authenticated_client: httpx.AsyncClient,
    member_client: httpx.AsyncClient,
    printer_row: Printer,
    redis_url: str,
) -> None:
    r = redis_lib.Redis.from_url(redis_url)
    r.set(obico.status_key(printer_row.id), '{"state":"warn","score":0.5,"smoothed":0.45}')
    try:
        out = await authenticated_client.get("/api/printers/detections/summary")
        assert out.status_code == 200
        body = out.json()["printers"]
        assert body[str(printer_row.id)]["state"] == "warn"
        # a member without an ACL row sees no printers
        assert (await member_client.get("/api/printers/detections/summary")).json() == {
            "printers": {}
        }
    finally:
        r.delete(obico.status_key(printer_row.id))
        r.close()
