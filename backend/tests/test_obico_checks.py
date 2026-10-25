"""Obico settings card backend: seed flags, ML version, 3-stage connection test."""

import re
from pathlib import Path

import httpx
import pytest
import redis as redis_lib

from app.services import obico

pytestmark = pytest.mark.usefixtures("printer_enabled")
ROOT = Path(__file__).resolve().parents[2]


async def test_settings_reports_seed_and_version(
    authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("OBICO_ML_VERSION", "abc123")
    get_settings.cache_clear()
    try:
        body = (await authenticated_client.get("/api/settings/obico")).json()
        assert body["ml_version"] == "abc123"
        assert body["ml_version_source"] == "configured"
        assert isinstance(body["seeded_from_env"], dict)
    finally:
        get_settings.cache_clear()


def test_seed_records_env_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDMM_OBICO_URL", "http://ml:3333/")
    monkeypatch.delenv("TDMM_OBICO_CALLBACK", raising=False)
    seeded = obico._seeded(None)
    assert seeded is not None
    assert seeded["url"] == "http://ml:3333"
    assert seeded[obico.SEEDED_META] == {"url": "TDMM_OBICO_URL"}


async def test_connection_checks_report_each_stage(
    authenticated_client: httpx.AsyncClient,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.api.obico as mod

    r = redis_lib.Redis.from_url(redis_url)
    fetch = {"on": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/hc/":
            return httpx.Response(200, text="ok")
        nonce = request.url.params["img"].rsplit("/", 1)[1]
        if fetch["on"]:
            assert r.getdel(obico.nonce_key(nonce))  # the sidecar's callback fetch
        return httpx.Response(200, json={"detections": [["failure", 0.4, [1, 2, 3, 4]]]})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        mod.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    try:
        ok = (
            await authenticated_client.post(
                "/api/settings/obico/test",
                json={"url": "http://ml:3333", "callback_url": "http://api:8080"},
            )
        ).json()
        assert ok["ok"] is True
        assert [(c["name"], c["ok"]) for c in ok["checks"]] == [
            ("sidecar", True),
            ("callback", True),
            ("inference", True),
        ]
        assert "1 detection" in ok["checks"][2]["detail"]
        fetch["on"] = False  # sidecar answered but never fetched the frame
        bad = (
            await authenticated_client.post(
                "/api/settings/obico/test", json={"url": "http://ml:3333"}
            )
        ).json()
        assert bad["ok"] is False
        assert [(c["name"], c["ok"]) for c in bad["checks"]][:2] == [
            ("sidecar", True),
            ("callback", False),
        ]
    finally:
        r.close()


async def test_connection_checks_skip_when_sidecar_down(
    authenticated_client: httpx.AsyncClient,
) -> None:
    out = (
        await authenticated_client.post(
            "/api/settings/obico/test", json={"url": "http://127.0.0.1:1"}
        )
    ).json()
    assert [c["ok"] for c in out["checks"]] == [False, False, False]
    assert out["checks"][1]["detail"].startswith("skipped")


def test_compose_ml_pins_agree() -> None:
    pin = re.search(
        r"obico-server\.git#([0-9a-f]{40}):ml_api", (ROOT / "compose.obico.yaml").read_text()
    )
    api = re.search(r"OBICO_ML_VERSION: ([0-9a-f]{40})", (ROOT / "compose.yaml").read_text())
    assert pin and api and pin.group(1) == api.group(1)
