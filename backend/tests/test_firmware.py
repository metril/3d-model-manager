"""Q5 firmware: feed parsing (httpx mock), cache refresh, API, SD-stage task."""

from __future__ import annotations

import json

import httpx
import pytest
import redis as redis_lib

from app.models import Setting
from app.printers.base import state_key
from app.services import firmware as fw
from app.tasks import base
from app.tasks.firmware import check_firmware

WIKI = (
    '<h2 id="h-01050000-20260301">x</h2><h2 id="h-01040000-20250101">y</h2>'
    '<h2 id="h-01050000-20260301">dup</h2>'
)
IMG = b"FIRMWARE-IMAGE"


def _handler(request: httpx.Request) -> httpx.Response:
    u = str(request.url)
    if "a1-mini-firmware-release-history" in u:
        return httpx.Response(200, text=WIKI)
    if u.endswith("/en/support/firmware-download/all"):
        return httpx.Response(200, text='{"buildId":"B123"}')
    if "/_next/data/B123/en/support/firmware-download/a1-mini.json" in u:
        versions = [
            {
                "version": "01.05.00.00",
                "url": "https://download.bambulab.com/fw/a1mini_01.05.00.00.bin",
                "release_notes_en": "Fixes things",
                "release_time": "2026-03-01",
            }
        ]
        return httpx.Response(
            200, json={"pageProps": {"printerMap": {"a1-mini": {"versions": versions}}}}
        )
    if u == "https://download.bambulab.com/fw/a1mini_01.05.00.00.bin":
        return httpx.Response(200, content=IMG)
    return httpx.Response(404)


def _client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_handler))


def test_model_mapping_and_compare():
    assert fw.api_key_for("A1 Mini") == "a1-mini" and fw.api_key_for("nope") is None
    assert fw.update_available("01.04.00.00", "01.05.00.00")
    assert not fw.update_available("01.05.00.00", "01.05.00.00")
    assert not fw.update_available(None, "01.05.00.00")


def test_wiki_versions_newest_first_deduped():
    assert fw.wiki_versions(_client(), "a1-mini") == [
        ("01.05.00.00", "20260301"),
        ("01.04.00.00", "20250101"),
    ]


def test_fetch_latest_enriches_from_download_page():
    info = fw.fetch_latest(_client(), "a1-mini")
    assert info["version"] == "01.05.00.00" and info["release_notes"] == "Fixes things"
    assert info["download_url"].endswith("a1mini_01.05.00.00.bin")


def test_fetch_latest_wiki_only_when_download_blocked():
    def h(request):
        if "bambulab.com" in str(request.url) and "wiki" not in str(request.url):
            return httpx.Response(403)
        return _handler(request)

    info = fw.fetch_latest(httpx.Client(transport=httpx.MockTransport(h)), "a1-mini")
    assert info["version"] == "01.05.00.00" and info["download_url"] == ""


def test_fetch_latest_unknown_model():
    assert fw.fetch_latest(_client(), "zzz") is None


CREATE = {
    "name": "A1 mini", "host": "192.168.1.50", "serial": "0309ABC",
    "access_code": "12345678", "model": "A1 Mini",
}  # fmt: skip


async def _setup(authenticated_client, redis_url, monkeypatch, ota="01.04.00.00"):
    monkeypatch.setattr(fw, "make_client", _client)
    pid = (await authenticated_client.post("/api/printers", json=CREATE)).json()["id"]
    c = redis_lib.Redis.from_url(redis_url)
    c.set(state_key(pid), json.dumps({"firmware": {"ota": ota}}))
    c.close()
    return pid


async def test_beat_task_caches_and_api_reports(
    authenticated_client, printer_enabled, redis_url, monkeypatch
):
    pid = await _setup(authenticated_client, redis_url, monkeypatch)
    assert check_firmware.apply().get()["models"] == ["a1-mini"]
    body = (await authenticated_client.get(f"/api/printers/{pid}/firmware")).json()
    assert body["current"] == "01.04.00.00" and body["latest"] == "01.05.00.00"
    assert body["update_available"] and body["download_available"]
    assert body["release_notes"] == "Fixes things"


async def test_upload_stages_image_and_never_flashes(
    authenticated_client, printer_enabled, redis_url, monkeypatch, fake_adapter
):
    pid = await _setup(authenticated_client, redis_url, monkeypatch)
    check_firmware.apply().get()
    r = await authenticated_client.post(f"/api/printers/{pid}/firmware/upload")
    assert r.status_code == 202
    assert fake_adapter.files_uploaded == [("/a1mini_01.05.00.00.bin", len(IMG))]
    body = (await authenticated_client.get(f"/api/printers/{pid}/firmware")).json()
    assert body["upload"]["status"] == "staged"


async def test_upload_409_when_up_to_date(
    authenticated_client, printer_enabled, redis_url, monkeypatch, fake_adapter
):
    pid = await _setup(authenticated_client, redis_url, monkeypatch, ota="01.05.00.00")
    check_firmware.apply().get()
    r = await authenticated_client.post(f"/api/printers/{pid}/firmware/upload")
    assert r.status_code == 409 and fake_adapter.files_uploaded == []


async def test_firmware_unknown_model(authenticated_client, printer_enabled):
    pid = (
        await authenticated_client.post("/api/printers", json={**CREATE, "model": "Zed"})
    ).json()["id"]
    body = (await authenticated_client.get(f"/api/printers/{pid}/firmware")).json()
    assert body["model_supported"] is False and body["update_available"] is False


def test_cache_row_shape(migrated_db):
    with base.sync_session() as s, _client() as c:
        fw.refresh_cache(s, ["A1 Mini", None, "A1 Mini"], c)
        assert set(s.get(Setting, fw.CACHE_KEY).value) == {"a1-mini"}


def test_download_rejects_bad_host_and_scheme(tmp_path):
    for url in ("https://evil.example/fw.bin", "http://download.bambulab.com/fw.bin",
                "https://bambulab.com.evil.example/fw.bin"):  # fmt: skip
        with pytest.raises(ValueError):
            fw.download_image(_client(), url, tmp_path)


def test_download_size_cap_cleans_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "MAX_IMAGE_BYTES", 5)
    with pytest.raises(ValueError):
        fw.download_image(
            _client(), "https://download.bambulab.com/fw/a1mini_01.05.00.00.bin", tmp_path
        )
    assert list(tmp_path.iterdir()) == []

    # running-count path (no Content-Length)
    def h(request):
        return httpx.Response(200, stream=httpx.ByteStream(IMG), headers={})

    with pytest.raises(ValueError):
        fw.download_image(
            httpx.Client(transport=httpx.MockTransport(h)),
            "https://download.bambulab.com/fw/x.bin",
            tmp_path,
        )
    assert list(tmp_path.iterdir()) == []
