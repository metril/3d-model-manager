"""Server settings: resolve/seed/validate/API (spec 2026-10-24)."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import httpx
import pytest
import redis.asyncio as aioredis
import respx
from pydantic import ValidationError
from sqlalchemy import delete, select

from app.config import get_settings
from app.db import get_sessionmaker
from app.logging_config import LevelPoller, apply_level
from app.models import AuditLog, Setting
from app.services import server_config as sc
from app.services.server_config import ServerConfig

ENV_NAMES = [*sc.ENV_SEED.values(), *sc.EXTRA_IGNORED_ENV]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


async def _drop_row(db) -> None:
    await db.execute(delete(Setting).where(Setting.key == "server"))
    await db.commit()


async def _row(db) -> dict:
    db.expire_all()
    return (await db.get(Setting, "server")).value


# --- model ----------------------------------------------------------------


def test_defaults_match_spec() -> None:
    cfg = ServerConfig()
    assert cfg.public_url is None
    assert cfg.max_upload_bytes == 4 * 1024**3 and cfg.max_download_bytes == 2 * 1024**3
    assert (cfg.sse_heartbeat_s, cfg.log_level, cfg.slicer_enabled) == (15, "INFO", True)
    assert (cfg.slicer_url, cfg.slice_timeout_s) == ("http://slicer:3001", 600)
    assert (cfg.vp_port_ssdp, cfg.vp_port_mqtt, cfg.vp_port_ftp) == (2021, 8883, 990)
    assert (cfg.vp_pasv_min, cfg.vp_pasv_max, cfg.vp_pasv_address) == (50000, 50009, None)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://m.example.com/", "https://m.example.com"),
        ("http://m.example.com/tdmm/", "http://m.example.com/tdmm"),
        ("", None),
        (None, None),
    ],
)
def test_public_url_normalised(raw, expected) -> None:
    assert ServerConfig(public_url=raw).public_url == expected


@pytest.mark.parametrize("raw", ["ftp://x", "m.example.com", "https://", "https://x/?q=1"])
def test_public_url_rejects(raw) -> None:
    with pytest.raises(ValidationError):
        ServerConfig(public_url=raw)


@pytest.mark.parametrize(
    "kw",
    [
        {"vp_port_mqtt": 990},  # == ftp
        {"vp_pasv_min": 50010},  # min > max
        {"vp_pasv_min": 900, "vp_pasv_max": 1000},  # includes ftp 990
        {"max_upload_bytes": 10},
        {"sse_heartbeat_s": 1},
        {"slice_timeout_s": 5},
        {"log_level": "TRACE"},
        {"vp_bind": "not-an-ip"},
        {"vp_port_ssdp": 70000},
    ],
)
def test_validation_rejects(kw) -> None:
    with pytest.raises(ValidationError):
        ServerConfig(**kw)


# --- resolve --------------------------------------------------------------


async def test_resolve_defaults_without_row(db_session) -> None:
    await _drop_row(db_session)
    assert await sc.get_server_config(db_session) == ServerConfig()


async def test_resolve_never_reads_env(db_session, monkeypatch) -> None:
    await _drop_row(db_session)
    monkeypatch.setenv("TDMM_SLICER_URL", "http://env:1")
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "5000000")
    cfg = await sc.get_server_config(db_session)
    assert cfg.slicer_url == "http://slicer:3001" and cfg.max_upload_bytes == 4 * 1024**3


async def test_resolve_degrades_invalid_stored_field(db_session) -> None:
    row = await db_session.get(Setting, "server")
    row.value = {"slicer_enabled": False, "max_upload_bytes": 3, "slice_timeout_s": 100}
    await db_session.commit()
    cfg = await sc.get_server_config(db_session)
    assert cfg.max_upload_bytes == 4 * 1024**3 and cfg.slice_timeout_s == 100


# --- seed -----------------------------------------------------------------


async def test_seed_from_env_records_meta(db_session, monkeypatch) -> None:
    await _drop_row(db_session)
    monkeypatch.setenv("TDMM_PUBLIC_URL", "https://m.example.com/")
    monkeypatch.setenv("TDMM_SLICER_URL", "http://s:9/")
    monkeypatch.setenv("TDMM_VP_PORT_MQTT", "8884")
    monkeypatch.setenv("SSE_HEARTBEAT_INTERVAL", "30")
    assert await sc.seed_server_config(db_session) is True
    value = await _row(db_session)
    assert value["public_url"] == "https://m.example.com"
    assert value["slicer_url"] == "http://s:9" and value["vp_port_mqtt"] == 8884
    assert value["sse_heartbeat_s"] == 30
    assert sorted(value["_seeded_from_env"]) == sorted(
        ["public_url", "slicer_url", "vp_port_mqtt", "sse_heartbeat_s"]
    )
    assert value["slicer_enabled"] is True


async def test_seed_does_not_overwrite(db_session, monkeypatch) -> None:
    monkeypatch.setenv("TDMM_SLICER_URL", "http://env:1")
    before = await _row(db_session)
    assert await sc.seed_server_config(db_session) is False
    assert await _row(db_session) == before


async def test_seed_empty_env_is_unset(db_session, monkeypatch) -> None:
    await _drop_row(db_session)
    monkeypatch.setenv("TDMM_SLICER_URL", "  ")
    await sc.seed_server_config(db_session)
    value = await _row(db_session)
    assert value["slicer_url"] == "http://slicer:3001" and value["_seeded_from_env"] == []


async def test_seed_from_legacy_app_row(db_session, monkeypatch) -> None:
    await _drop_row(db_session)
    db_session.add(
        Setting(key="app", value={"slicer_url": "http://legacy:1", "slice_timeout_s": 90})
    )
    await db_session.commit()
    await sc.seed_server_config(db_session)
    value = await _row(db_session)
    assert value["slicer_url"] == "http://legacy:1" and value["slice_timeout_s"] == 90
    assert value["_seeded_from_env"] == []


async def test_seed_env_beats_legacy(db_session, monkeypatch) -> None:
    await _drop_row(db_session)
    db_session.add(Setting(key="app", value={"slicer_url": "http://legacy:1"}))
    await db_session.commit()
    monkeypatch.setenv("TDMM_SLICER_URL", "http://env:2")
    await sc.seed_server_config(db_session)
    assert (await _row(db_session))["slicer_url"] == "http://env:2"


async def test_seed_invalid_env_warns_and_defaults(db_session, monkeypatch, caplog) -> None:
    await _drop_row(db_session)
    monkeypatch.setenv("TDMM_SLICE_TIMEOUT_S", "banana")
    monkeypatch.setenv("TDMM_PUBLIC_URL", "ftp://nope")
    monkeypatch.setenv("TDMM_SLICER_URL", "http://ok:1")
    with caplog.at_level(logging.WARNING):
        await sc.seed_server_config(db_session)
    value = await _row(db_session)
    assert value["slice_timeout_s"] == 600 and value["public_url"] is None
    assert value["slicer_url"] == "http://ok:1"
    assert value["_seeded_from_env"] == ["slicer_url"]
    assert "TDMM_SLICE_TIMEOUT_S" in caplog.text and "TDMM_PUBLIC_URL" in caplog.text


async def test_seed_pasv_range_via_env_both_set(db_session, monkeypatch) -> None:
    await _drop_row(db_session)
    monkeypatch.setenv("TDMM_VP_PASV_MIN", "60000")
    monkeypatch.setenv("TDMM_VP_PASV_MAX", "60010")
    await sc.seed_server_config(db_session)
    value = await _row(db_session)
    assert (value["vp_pasv_min"], value["vp_pasv_max"]) == (60000, 60010)


async def test_seed_inconsistent_vp_env_falls_back(db_session, monkeypatch, caplog) -> None:
    await _drop_row(db_session)
    monkeypatch.setenv("TDMM_VP_PORT_MQTT", "990")  # collides with default ftp
    with caplog.at_level(logging.WARNING):
        await sc.seed_server_config(db_session)
    value = await _row(db_session)
    assert value["vp_port_mqtt"] == 8883 and "vp_port_mqtt" not in value["_seeded_from_env"]
    assert "inconsistent" in caplog.text


async def test_seed_is_idempotent_across_processes(db_session, monkeypatch) -> None:
    await _drop_row(db_session)
    monkeypatch.setenv("TDMM_SLICER_URL", "http://env:1")

    async def one() -> bool:
        async with get_sessionmaker()() as s:
            return await sc.seed_server_config(s)

    results = await asyncio.gather(*(one() for _ in range(5)))
    assert 1 <= sum(results) <= 5
    rows = (await db_session.execute(select(Setting).where(Setting.key == "server"))).all()
    assert len(rows) == 1


def test_seed_sync_twin(monkeypatch) -> None:
    from sqlalchemy import delete as sdelete

    from app.tasks import base

    monkeypatch.setenv("TDMM_SLICER_URL", "http://env:3")
    with base.sync_session() as s:
        s.execute(sdelete(Setting).where(Setting.key == "server"))
        s.commit()
        assert sc.seed_server_config_sync(s) is True
        assert sc.seed_server_config_sync(s) is False
        assert sc.get_server_config_sync(s).slicer_url == "http://env:3"


# --- warnings -------------------------------------------------------------


def test_warn_ignored_env(monkeypatch, caplog) -> None:
    monkeypatch.setenv("TDMM_SLICER_URL", "http://x")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("TDMM_OBICO_URL", "http://o")
    with caplog.at_level(logging.WARNING):
        names = sc.warn_ignored_server_env(get_settings(), ServerConfig())
    assert set(names) == {"TDMM_SLICER_URL", "COOKIE_SECURE", "TDMM_OBICO_URL"}
    assert "TDMM_SLICER_URL is ignored after first boot" in caplog.text
    assert "Public URL" in caplog.text


def test_warn_falsy_cookie_secure_with_https_public_url(monkeypatch, caplog) -> None:
    monkeypatch.setenv("COOKIE_SECURE", "false")
    with caplog.at_level(logging.WARNING):
        sc.warn_ignored_server_env(get_settings(), ServerConfig(public_url="https://m.example.com"))
    assert "plain-http logins will fail" in caplog.text


def test_no_warning_when_unset(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        assert sc.warn_ignored_server_env(get_settings()) == []
    assert caplog.text == ""


# --- set / meta -----------------------------------------------------------


async def test_set_prunes_changed_fields_from_seeded(db_session, monkeypatch) -> None:
    await _drop_row(db_session)
    monkeypatch.setenv("TDMM_SLICER_URL", "http://env:1")
    monkeypatch.setenv("TDMM_PUBLIC_URL", "https://m.example.com")
    await sc.seed_server_config(db_session)
    cfg = await sc.get_server_config(db_session)
    await sc.set_server_config(db_session, cfg.model_copy(update={"slicer_url": "http://new:2"}))
    assert await sc.seeded_fields(db_session) == ["public_url"]
    assert (await sc.get_server_config(db_session)).slicer_url == "http://new:2"


# --- legacy slicer migration ------------------------------------------------


async def test_legacy_migration_one_shot(db_session) -> None:
    app_row = Setting(
        key="app",
        value={"printer_enabled": True, "slicer_url": "http://old:1", "slice_timeout_s": 77},
    )
    db_session.add(app_row)
    await db_session.commit()
    assert await sc.migrate_legacy_slicer(db_session) is True
    cfg = await sc.get_server_config(db_session)
    assert (cfg.slicer_url, cfg.slice_timeout_s) == ("http://old:1", 77)
    db_session.expire_all()
    assert (await db_session.get(Setting, "app")).value == {"printer_enabled": True}
    assert await sc.migrate_legacy_slicer(db_session) is False


async def test_legacy_migration_skips_env_seeded(db_session, monkeypatch) -> None:
    await _drop_row(db_session)
    monkeypatch.setenv("TDMM_SLICER_URL", "http://env:1")
    await sc.seed_server_config(db_session)
    db_session.add(Setting(key="app", value={"slicer_url": "http://old:1"}))
    await db_session.commit()
    await sc.migrate_legacy_slicer(db_session)
    assert (await sc.get_server_config(db_session)).slicer_url == "http://env:1"


# --- API ------------------------------------------------------------------


async def test_api_get_put_roundtrip_and_audit(authenticated_client, db_session) -> None:
    r = await authenticated_client.get("/api/settings/server")
    assert r.status_code == 200
    body = r.json()
    assert body["config"]["slicer_enabled"] is False  # test fixture seed
    assert body["derived"]["request_origin"] == "http://test"
    assert body["derived"]["cookie_secure"] is None
    new = {**body["config"], "public_url": "https://m.example.com/", "log_level": "WARNING"}
    put = await authenticated_client.put("/api/settings/server", json=new)
    assert put.status_code == 200, put.text
    out = put.json()
    assert out["config"]["public_url"] == "https://m.example.com"
    assert out["derived"]["cookie_secure"] is True
    audit = (await db_session.scalars(select(AuditLog))).all()
    assert [a.action for a in audit] == ["server_settings.update"]
    assert set(audit[0].payload["changed"]) == {"public_url", "log_level"}
    apply_level("INFO")


async def test_api_put_422(authenticated_client) -> None:
    cfg = (await authenticated_client.get("/api/settings/server")).json()["config"]
    for bad in ({"vp_port_mqtt": cfg["vp_port_ftp"]}, {"public_url": "nope"}, {"log_level": "X"}):
        r = await authenticated_client.put("/api/settings/server", json={**cfg, **bad})
        assert r.status_code == 422


async def test_api_reports_seeded_and_ignored(
    authenticated_client, db_session, monkeypatch
) -> None:
    await _drop_row(db_session)
    monkeypatch.setenv("TDMM_SLICER_URL", "http://env:1")
    await sc.seed_server_config(db_session)
    body = (await authenticated_client.get("/api/settings/server")).json()
    assert body["seeded_from_env"] == {"slicer_url": "TDMM_SLICER_URL"}
    assert body["ignored_env"] == ["TDMM_SLICER_URL"]


async def test_api_admin_only(viewer_client) -> None:
    assert (await viewer_client.get("/api/settings/server")).status_code == 403
    assert (await viewer_client.put("/api/settings/server", json={})).status_code == 403
    assert (
        await viewer_client.post("/api/settings/server/slicer/test", json={})
    ).status_code == 403
    assert (await viewer_client.get("/api/settings/server/status")).status_code == 403


async def test_slicer_test_endpoint(authenticated_client) -> None:
    with respx.mock:
        respx.get("http://s:1/health").mock(return_value=httpx.Response(200, json={}))
        r = await authenticated_client.post(
            "/api/settings/server/slicer/test", json={"url": "http://s:1/"}
        )
    assert r.json()["ok"] is True and isinstance(r.json()["latency_ms"], int)
    with respx.mock:
        respx.get("http://s:1/health").mock(return_value=httpx.Response(503))
        r = await authenticated_client.post(
            "/api/settings/server/slicer/test", json={"url": "http://s:1"}
        )
    assert r.json() == {"ok": False, "latency_ms": r.json()["latency_ms"], "detail": "HTTP 503"}
    with respx.mock:
        respx.get("http://s:1/health").mock(side_effect=httpx.ConnectError("x"))
        r = await authenticated_client.post(
            "/api/settings/server/slicer/test", json={"url": "http://s:1"}
        )
    assert r.json()["ok"] is False and r.json()["latency_ms"] is None


async def test_status_endpoint(authenticated_client, redis_url) -> None:
    from tests.server_helpers import set_server

    r = aioredis.Redis.from_url(redis_url)
    await r.delete("tdmm:server:probe", "tdmm:vp:status")
    try:
        # slicer disabled -> slicer_healthy null; obico probed at /hc/; vp offline
        with respx.mock:
            respx.get("http://obico-ml:3333/hc/").mock(return_value=httpx.Response(200))
            body = (await authenticated_client.get("/api/settings/server/status")).json()
        assert body["slicer_healthy"] is None and body["slicer"]["detail"] == "disabled"
        assert body["obico"] == {"ok": True, "detail": "ok"}
        assert body["virtual_printer"]["state"] == "offline"

        await r.delete("tdmm:server:probe")
        await set_server(slicer_enabled=True, slicer_url="http://s:1")
        await r.set(
            "tdmm:vp:status",
            '{"state":"running","vp_id":3,"bind":"0.0.0.0","ports":{"ssdp":2021,"mqtt":8883,'
            '"ftp":990},"pasv":[50000,50009],"errors":[],"ts":"2026-10-24T00:00:00Z"}',
            ex=30,
        )
        with respx.mock:
            respx.get("http://s:1/health").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "version": "v1",
                        "busy": True,
                        "running": 1,
                        "max_concurrency": 2,
                        "job_timeout_s": 600.0,
                        "mem_limit": None,
                        "slicer_version": "v2.4.2",
                    },
                )
            )
            respx.get("http://obico-ml:3333/hc/").mock(return_value=httpx.Response(500))
            body = (await authenticated_client.get("/api/settings/server/status")).json()
            # second call within 10 s is served from the cache (no new probes)
            respx.get("http://s:1/health").mock(side_effect=httpx.ConnectError("x"))
            again = (await authenticated_client.get("/api/settings/server/status")).json()
        assert body["slicer_healthy"] is True and body["obico"]["ok"] is False
        assert body["slicer"]["info"] == {
            "version": "v1",
            "slicer_version": "v2.4.2",
            "busy": True,
            "running": 1,
            "max_concurrency": 2,
            "job_timeout_s": 600.0,
        }
        assert (
            body["virtual_printer"]["state"] == "running" and body["virtual_printer"]["vp_id"] == 3
        )
        assert again["slicer_healthy"] is True
    finally:
        await r.delete("tdmm:server:probe", "tdmm:vp:status")
        await r.aclose()


# --- slicer disabled gating -------------------------------------------------


async def test_profiles_sidecar_import_409_when_disabled(authenticated_client) -> None:
    r = await authenticated_client.post("/api/profiles/import", json={"from_sidecar": True})
    assert r.status_code == 409 and r.json()["detail"] == "slicer_disabled"


# --- log level --------------------------------------------------------------


def test_apply_level_and_poller_ttl() -> None:
    from app.tasks import base

    root = logging.getLogger()
    original = root.level
    try:
        root.setLevel(logging.INFO)
        assert apply_level("DEBUG") is True and root.level == logging.DEBUG
        assert apply_level("DEBUG") is False  # unchanged -> early return
        assert apply_level("nonsense") is False

        with base.sync_session() as s:
            s.merge(Setting(key="server", value={"log_level": "ERROR", "slicer_enabled": False}))
            s.commit()
        poller = LevelPoller(ttl_s=3600)
        assert poller.maybe_apply_sync(base.sync_session) is True
        assert root.level == logging.ERROR
        root.setLevel(logging.INFO)
        assert poller.maybe_apply_sync(base.sync_session) is False  # inside TTL
        assert root.level == logging.INFO
        assert LevelPoller(ttl_s=0).maybe_apply_sync(base.sync_session) is True
    finally:
        root.setLevel(original)


async def test_poller_async() -> None:
    root = logging.getLogger()
    original = root.level
    try:
        async with get_sessionmaker()() as s:
            row = await s.get(Setting, "server")
            row.value = {**row.value, "log_level": "WARNING"}
            await s.commit()
        root.setLevel(logging.INFO)
        assert await LevelPoller(ttl_s=0).maybe_apply(get_sessionmaker()) is True
        assert root.level == logging.WARNING
    finally:
        root.setLevel(original)


# --- guard: moved Settings fields are seed-only ---------------------------------

_MOVED = re.compile(
    r"settings\.(public_url|cookie_secure|max_upload_bytes|max_download_bytes|"
    r"sse_heartbeat_interval\w*|slicer_url|slice_timeout_s|obico_url|obico_callback|vp_\w+)\b"
)
_ALLOWED = {"services/server_config.py", "services/obico.py"}


def test_no_moved_settings_reads_outside_seed_modules() -> None:
    root = Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel in _ALLOWED:
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if _MOVED.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


# --- studio -----------------------------------------------------------------


async def test_studio_defaults_and_env_seed(db_session, monkeypatch) -> None:
    cfg = ServerConfig()
    assert (cfg.studio_enabled, cfg.studio_url, cfg.studio_launcher_url) == (
        False,
        "http://studio:3000",
        "http://studio:3010",
    )
    assert cfg.studio_launcher_token is None
    await _drop_row(db_session)
    monkeypatch.setenv("TDMM_STUDIO_ENABLED", "true")
    monkeypatch.setenv("TDMM_STUDIO_URL", "http://s:1/")
    monkeypatch.setenv("TDMM_STUDIO_LAUNCHER_TOKEN", "sekret")
    await sc.seed_server_config(db_session)
    got = await sc.get_server_config(db_session)
    assert got.studio_enabled is True and got.studio_url == "http://s:1"
    assert got.studio_launcher_token.get_secret_value() == "sekret"
    value = await _row(db_session)
    assert value["studio_launcher_token"] != "sekret"  # encrypted at rest


async def test_studio_token_masked_and_kept_on_put(authenticated_client) -> None:
    cfg = (await authenticated_client.get("/api/settings/server")).json()["config"]
    put = await authenticated_client.put(
        "/api/settings/server",
        json={**cfg, "studio_enabled": True, "studio_launcher_token": "abc"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["config"]["studio_launcher_token"] == "***"
    cfg = put.json()["config"]
    again = await authenticated_client.put("/api/settings/server", json=cfg)  # "***" keeps
    assert again.json()["config"]["studio_launcher_token"] == "***"
    cleared = await authenticated_client.put(
        "/api/settings/server", json={**cfg, "studio_launcher_token": ""}
    )
    assert cleared.json()["config"]["studio_launcher_token"] is None
    bad = await authenticated_client.put("/api/settings/server", json={**cfg, "studio_url": "nope"})
    assert bad.status_code == 422
