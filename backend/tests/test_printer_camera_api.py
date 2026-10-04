from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import httpx

CREATE_MOONRAKER = {
    "name": "Qidi Q2",
    "kind": "moonraker",
    "host": "imprimante3d-1.lan",
    "serial": "Q20001",
}


async def _make_moonraker_printer(client) -> int:
    return (await client.post("/api/printers", json=CREATE_MOONRAKER)).json()["id"]


async def test_camera_returns_stream_info(authenticated_client, printer_enabled):
    pid = await _make_moonraker_printer(authenticated_client)
    with patch(
        "app.printers.moonraker.MoonrakerAdapter.get_camera_urls",
        return_value={
            "name": "Qidi Cam",
            "stream_url": "http://imprimante3d-1.lan/webcam/?action=stream",
            "snapshot_url": "http://imprimante3d-1.lan/webcam/?action=snapshot",
            "aspect_ratio": "4:3",
        },
    ):
        r = await authenticated_client.get(f"/api/printers/{pid}/camera")
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is True
        assert data["name"] == "Qidi Cam"
        assert data["stream_url"] == f"/api/printers/{pid}/camera/stream"
        assert data["snapshot_url"] == f"/api/printers/{pid}/camera/snapshot"
        assert data["direct_stream_url"] == "http://imprimante3d-1.lan/webcam/?action=stream"


async def test_camera_unavailable_when_no_stream(authenticated_client, printer_enabled):
    pid = await _make_moonraker_printer(authenticated_client)
    with patch(
        "app.printers.moonraker.MoonrakerAdapter.get_camera_urls",
        return_value={},
    ):
        r = await authenticated_client.get(f"/api/printers/{pid}/camera")
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is False


_REAL_ASYNC_CLIENT = httpx.AsyncClient
_CAM = {
    "name": "Cam",
    "stream_url": "http://imprimante3d-1.lan/webcam/?action=stream",
    "snapshot_url": "http://imprimante3d-1.lan/webcam/?action=snapshot",
}


def _fake_httpx(handler):
    transport = httpx.MockTransport(handler)
    return SimpleNamespace(
        AsyncClient=lambda **kw: _REAL_ASYNC_CLIENT(transport=transport, **kw),
        Timeout=httpx.Timeout,
    )


async def test_snapshot_proxy_ok_sets_nosniff(authenticated_client, printer_enabled):
    pid = await _make_moonraker_printer(authenticated_client)
    fake = _fake_httpx(
        lambda req: httpx.Response(200, content=b"jpg", headers={"content-type": "image/jpeg"})
    )
    with (
        patch("app.printers.moonraker.MoonrakerAdapter.get_camera_urls", return_value=_CAM),
        patch("app.api.printers.httpx", fake),
    ):
        r = await authenticated_client.get(f"/api/printers/{pid}/camera/snapshot")
    assert r.status_code == 200 and r.content == b"jpg"
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-content-type-options"] == "nosniff"


async def test_snapshot_proxy_missing_content_type_defaults_to_jpeg(
    authenticated_client, printer_enabled
):
    pid = await _make_moonraker_printer(authenticated_client)
    fake = _fake_httpx(lambda req: httpx.Response(200, content=b"jpg"))
    with (
        patch("app.printers.moonraker.MoonrakerAdapter.get_camera_urls", return_value=_CAM),
        patch("app.api.printers.httpx", fake),
    ):
        r = await authenticated_client.get(f"/api/printers/{pid}/camera/snapshot")
    assert r.status_code == 200 and r.content == b"jpg"
    assert r.headers["content-type"] == "image/jpeg"


async def test_snapshot_proxy_oversize_is_generic_502(authenticated_client, printer_enabled):
    pid = await _make_moonraker_printer(authenticated_client)
    big = b"x" * (5 * 1024 * 1024 + 1)
    fake = _fake_httpx(
        lambda req: httpx.Response(200, content=big, headers={"content-type": "image/jpeg"})
    )
    with (
        patch("app.printers.moonraker.MoonrakerAdapter.get_camera_urls", return_value=_CAM),
        patch("app.api.printers.httpx", fake),
    ):
        r = await authenticated_client.get(f"/api/printers/{pid}/camera/snapshot")
    assert r.status_code == 502
    assert r.json()["detail"] == "Camera snapshot unavailable"


async def test_snapshot_proxy_rejects_non_image_type(authenticated_client, printer_enabled):
    pid = await _make_moonraker_printer(authenticated_client)
    fake = _fake_httpx(
        lambda req: httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
    )
    with (
        patch("app.printers.moonraker.MoonrakerAdapter.get_camera_urls", return_value=_CAM),
        patch("app.api.printers.httpx", fake),
    ):
        r = await authenticated_client.get(f"/api/printers/{pid}/camera/snapshot")
    assert r.status_code == 502


async def test_stream_rejects_disabled_printer(authenticated_client, printer_enabled):
    pid = await _make_moonraker_printer(authenticated_client)
    r = await authenticated_client.patch(f"/api/printers/{pid}", json={"enabled": False})
    assert r.status_code == 200, r.text
    with patch("app.printers.moonraker.MoonrakerAdapter.get_camera_urls", return_value=_CAM):
        r = await authenticated_client.get(f"/api/printers/{pid}/camera/stream")
    assert r.status_code == 409
