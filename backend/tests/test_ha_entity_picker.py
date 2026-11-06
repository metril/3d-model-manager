"""HA entity/service pickers (`/api/ha/*`) and the per-printer power source
priority: energy link > plug power > `power_w` link, never double counted."""

from __future__ import annotations

import json

import httpx
import pytest
import redis as redis_lib
import respx

from app.config import get_settings
from app.crypto import encrypt_secret
from app.models import SmartPlug
from app.models.enums import HaRole, PrinterKind
from app.models.home_assistant import HaLink
from app.models.printing import Printer
from app.services.ha_config import HaConfig
from app.services.ha_state import energy_acc, power_key
from app.services.home_assistant import poll_once
from app.tasks import base

pytestmark = pytest.mark.usefixtures("printer_enabled")

BASE_URL = "http://homeassistant.local:8123"

STATES = [
    {
        "entity_id": "sensor.plug_power",
        "state": "120",
        "attributes": {
            "friendly_name": "Plug power",
            "device_class": "power",
            "unit_of_measurement": "W",
        },
    },
    {
        "entity_id": "sensor.plug_energy",
        "state": "4.2",
        "attributes": {"friendly_name": "Plug energy", "device_class": "energy"},
    },
    {
        "entity_id": "binary_sensor.door",
        "state": "off",
        "attributes": {"friendly_name": "Front door", "device_class": "door"},
    },
    {"entity_id": "switch.printer", "state": "on", "attributes": {"friendly_name": "Printer"}},
    {"entity_id": "light.lamp", "state": "off", "attributes": {}},
]


@pytest.fixture(autouse=True)
def _clean_ha_redis_keys(redis_url: str):
    yield
    r = redis_lib.Redis.from_url(redis_url)
    try:
        for key in r.scan_iter("ha:*"):
            r.delete(key)
    finally:
        r.close()


async def _configure(client: httpx.AsyncClient) -> None:
    r = await client.put(
        "/api/settings/home-assistant", json={"ha_url": BASE_URL, "ha_token": "longlivedtoken"}
    )
    assert r.status_code == 200


async def test_entities_503_when_unconfigured(authenticated_client: httpx.AsyncClient):
    assert (await authenticated_client.get("/api/ha/entities")).status_code == 503
    assert (await authenticated_client.get("/api/ha/services")).status_code == 503


@respx.mock
async def test_entities_filters(authenticated_client: httpx.AsyncClient):
    await _configure(authenticated_client)
    route = respx.get(f"{BASE_URL}/api/states").respond(json=STATES)
    c = authenticated_client

    allr = (await c.get("/api/ha/entities")).json()
    assert len(allr) == len(STATES)
    ids = [e["entity_id"] for e in (await c.get("/api/ha/entities?domain=switch,light")).json()]
    assert ids == ["light.lamp", "switch.printer"]
    ids = [e["entity_id"] for e in (await c.get("/api/ha/entities?device_class=power")).json()]
    assert ids == ["sensor.plug_power"]
    ids = [
        e["entity_id"]
        for e in (await c.get("/api/ha/entities?domain=binary_sensor&device_class=door")).json()
    ]
    assert ids == ["binary_sensor.door"]
    ids = [e["entity_id"] for e in (await c.get("/api/ha/entities?q=FRONT")).json()]
    assert ids == ["binary_sensor.door"]
    hit = (await c.get("/api/ha/entities?device_class=power")).json()[0]
    assert hit["friendly_name"] == "Plug power" and hit["unit"] == "W" and hit["state"] == "120"
    # one HA fetch for the whole burst (10 s server-side cache)
    assert route.call_count == 1

    # the old location-sensor route stays, hard-filtered to sensors
    old = (await c.get("/api/storage-locations/sensors/entities")).json()
    assert {e["entity_id"] for e in old} == {
        "sensor.plug_power",
        "sensor.plug_energy",
        "binary_sensor.door",
    }


@respx.mock
async def test_services_listing(authenticated_client: httpx.AsyncClient):
    await _configure(authenticated_client)
    respx.get(f"{BASE_URL}/api/services").respond(
        json=[
            {"domain": "notify", "services": {"mobile_app_phone": {}, "notify": {}}},
            {"domain": "light", "services": {"turn_on": {}}},
        ]
    )
    r = await authenticated_client.get("/api/ha/services?domain=notify")
    assert r.json() == ["notify.mobile_app_phone", "notify.notify"]


# --- power source priority -------------------------------------------------


def _cfg() -> HaConfig:
    return HaConfig(
        ha_mqtt_url=None,
        ha_mqtt_user=None,
        ha_mqtt_password=None,
        ha_url=BASE_URL,
        ha_token="tok",
    )


def _printer(s) -> int:
    p = Printer(
        name="P1",
        kind=PrinterKind.BAMBU_LAN,
        host="h",
        serial="S",
        access_code_enc=encrypt_secret(get_settings(), "1"),
    )
    s.add(p)
    s.commit()
    return p.id


def _plug(s, pid, *, watts: float | None, error: str | None = None) -> SmartPlug:
    plug = SmartPlug(
        name="Main plug",
        printer_id=pid,
        kind="rest",
        config_enc=encrypt_secret(get_settings(), json.dumps({"on_url": "http://p/on"})),
        last_power_w=watts,
        last_error=error,
    )
    s.add(plug)
    s.commit()
    return plug


def _states(power: str = "1000", energy: str | None = None) -> None:
    body = [{"entity_id": "sensor.w", "state": power, "attributes": {}}]
    if energy is not None:
        body.append({"entity_id": "sensor.kwh", "state": energy, "attributes": {}})
    respx.get(f"{BASE_URL}/api/states").respond(json=body)


def _poll(s, r, ts: float) -> None:
    from datetime import UTC, datetime

    poll_once(s, r, _cfg(), now=datetime.fromtimestamp(ts, UTC))


@respx.mock
def test_power_link_integrates_when_alone(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    _states("1000")
    with base.sync_session() as s:
        pid = _printer(s)
        s.add(HaLink(printer_id=pid, entity_id="sensor.w", role=HaRole.POWER_W))
        s.commit()
        _poll(s, r, 10_000.0)
        _poll(s, r, 10_015.0)
    assert energy_acc(r, pid) == pytest.approx(1000 * 15 / 3_600_000)
    assert float(r.get(power_key(pid))) == 1000.0


@respx.mock
def test_plug_power_shadows_power_link(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    _states("1000")
    with base.sync_session() as s:
        pid = _printer(s)
        _plug(s, pid, watts=900.0)
        s.add(HaLink(printer_id=pid, entity_id="sensor.w", role=HaRole.POWER_W))
        s.commit()
        _poll(s, r, 10_000.0)
        _poll(s, r, 10_015.0)
    assert energy_acc(r, pid) is None  # the plug integrates; the link adds nothing
    assert float(r.get(power_key(pid))) == 1000.0  # live watts still published


@respx.mock
def test_broken_plug_hands_over_to_power_link(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    _states("600")
    with base.sync_session() as s:
        pid = _printer(s)
        _plug(s, pid, watts=900.0, error="boom")
        s.add(HaLink(printer_id=pid, entity_id="sensor.w", role=HaRole.POWER_W))
        s.commit()
        _poll(s, r, 10_000.0)
        _poll(s, r, 10_015.0)
    assert energy_acc(r, pid) == pytest.approx(600 * 15 / 3_600_000)


@respx.mock
def test_energy_link_shadows_power_link(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    _states("1000", energy="10.0")
    with base.sync_session() as s:
        pid = _printer(s)
        s.add(HaLink(printer_id=pid, entity_id="sensor.w", role=HaRole.POWER_W))
        s.add(HaLink(printer_id=pid, entity_id="sensor.kwh", role=HaRole.ENERGY_KWH))
        s.commit()
        _poll(s, r, 10_000.0)
        _states("1000", energy="10.5")
        _poll(s, r, 10_060.0)
    assert energy_acc(r, pid) == pytest.approx(0.5)  # meter only, no watt integration


async def test_links_api_marks_shadowed_power_link(
    authenticated_client: httpx.AsyncClient, db_session
):
    c = authenticated_client
    pid = (
        await c.post(
            "/api/printers",
            json={"name": "P", "host": "10.0.0.5", "serial": "0309ABC", "access_code": "12345678"},
        )
    ).json()["id"]
    link = await c.post(
        "/api/settings/home-assistant/links",
        json={"printer_id": pid, "entity_id": "sensor.w", "role": "power_w"},
    )
    assert link.json()["active"] is True and link.json()["shadowed_by"] is None

    db_session.add(
        SmartPlug(
            name="Main plug",
            printer_id=pid,
            kind="rest",
            config_enc=encrypt_secret(get_settings(), "{}"),
            last_power_w=50.0,
        )
    )
    await db_session.commit()
    row = (await c.get("/api/settings/home-assistant/links")).json()[0]
    assert row["active"] is False
    assert row["shadowed_by"] == {"kind": "plug", "name": "Main plug"}

    await c.post(
        "/api/settings/home-assistant/links",
        json={"printer_id": pid, "entity_id": "sensor.kwh", "role": "energy_kwh"},
    )
    row = (await c.get("/api/settings/home-assistant/links")).json()[0]
    assert row["shadowed_by"] == {"kind": "energy_link", "name": "sensor.kwh"}


async def test_plug_summary_exposes_link_power(authenticated_client: httpx.AsyncClient, redis_url):
    c = authenticated_client
    pid = (
        await c.post(
            "/api/printers",
            json={"name": "P", "host": "10.0.0.5", "serial": "0309ABC", "access_code": "12345678"},
        )
    ).json()["id"]
    r = redis_lib.Redis.from_url(redis_url)
    r.set(power_key(pid), "321.5", ex=60)
    out = (await c.get("/api/printers/plugs/summary")).json()
    assert out["link_power_w"] == {str(pid): 321.5}


# --- review fixes ----------------------------------------------------------


@respx.mock
async def test_entities_cache_expires(authenticated_client: httpx.AsyncClient, monkeypatch):
    from app.services import ha_entities

    await _configure(authenticated_client)
    route = respx.get(f"{BASE_URL}/api/states").respond(json=STATES)
    await authenticated_client.get("/api/ha/entities")
    await authenticated_client.get("/api/ha/entities")
    assert route.call_count == 1
    ha_entities.clear_cache()
    await authenticated_client.get("/api/ha/entities")
    assert route.call_count == 2


@respx.mock
async def test_services_body_over_64kb(authenticated_client: httpx.AsyncClient):
    await _configure(authenticated_client)
    big = [
        {
            "domain": "notify",
            "services": {f"mobile_app_{i}": {"desc": "x" * 40} for i in range(2500)},
        }
    ]
    assert len(json.dumps(big)) > 64 * 1024
    respx.get(f"{BASE_URL}/api/services").respond(json=big)
    r = await authenticated_client.get("/api/ha/services?domain=notify")
    assert r.status_code == 200 and len(r.json()) == 2500


@pytest.mark.parametrize(
    ("state", "unit", "want"),
    [
        ("1000", "W", 1000.0),
        ("1000", None, 1000.0),
        ("1.5", "kW", 1500.0),
        ("2500", "mW", 2.5),
        ("5", "BTU/h", None),
        ("nan", "W", None),
        ("inf", "W", None),
        ("-3", "W", None),
        ("unavailable", "W", None),
    ],
)
def test_state_watts(state, unit, want):
    from app.services.power_source import state_watts

    attrs = {"unit_of_measurement": unit} if unit else {}
    got = state_watts({"state": state, "attributes": attrs})
    assert got == (pytest.approx(want) if want is not None else None)


@respx.mock
def test_power_link_nan_does_not_break_poll(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        pid = _printer(s)
        s.add(HaLink(printer_id=pid, entity_id="sensor.w", role=HaRole.POWER_W))
        s.commit()
        _states("1000")
        _poll(s, r, 10_000.0)
        _states("nan")
        assert _poll_ok(s, r, 10_015.0)
        assert not r.keys("ha:power:last:*")
        _states("1000")
        _poll(s, r, 10_030.0)  # restart: no integration yet
    assert energy_acc(r, pid) is None


def _poll_ok(s, r, ts: float) -> bool:
    from datetime import UTC, datetime

    return poll_once(s, r, _cfg(), now=datetime.fromtimestamp(ts, UTC))["ok"]


@respx.mock
def test_power_link_scales_kw_and_skips_unknown_unit(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        pid = _printer(s)
        s.add(HaLink(printer_id=pid, entity_id="sensor.w", role=HaRole.POWER_W))
        s.commit()
        respx.get(f"{BASE_URL}/api/states").respond(
            json=[
                {
                    "entity_id": "sensor.w",
                    "state": "1.2",
                    "attributes": {"unit_of_measurement": "kW"},
                }
            ]
        )
        _poll(s, r, 10_000.0)
        _poll(s, r, 10_015.0)
        assert float(r.get(power_key(pid))) == pytest.approx(1200.0)
        acc = energy_acc(r, pid)
        assert acc == pytest.approx(1200 * 15 / 3_600_000)
        respx.get(f"{BASE_URL}/api/states").respond(
            json=[
                {
                    "entity_id": "sensor.w",
                    "state": "5",
                    "attributes": {"unit_of_measurement": "BTU/h"},
                }
            ]
        )
        _poll(s, r, 10_030.0)
    assert energy_acc(r, pid) == pytest.approx(acc)


@respx.mock
def test_integration_dt_capped_and_outage_clears_last(redis_url: str):
    r = redis_lib.Redis.from_url(redis_url)
    _states("1000")
    with base.sync_session() as s:
        pid = _printer(s)
        s.add(HaLink(printer_id=pid, entity_id="sensor.w", role=HaRole.POWER_W))
        s.commit()
        _poll(s, r, 10_000.0)
        _poll(s, r, 10_200.0)  # stalled poller: dt capped at 30 s
        assert energy_acc(r, pid) == pytest.approx(1000 * 30 / 3_600_000)
        respx.get(f"{BASE_URL}/api/states").respond(503)
        _poll(s, r, 10_215.0)
        assert not r.keys("ha:power:last:*")
        _states("1000")
        _poll(s, r, 10_230.0)  # first sample after the outage: nothing added
    assert energy_acc(r, pid) == pytest.approx(1000 * 30 / 3_600_000)


def test_plug_error_resets_integration(redis_url: str, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.services.smart_plugs import drivers, manager

    r = redis_lib.Redis.from_url(redis_url)
    mode = {"v": "ok"}

    class Drv:
        def status(self):
            if mode["v"] == "err":
                raise drivers.PlugError("down")
            return drivers.PlugStatus(on=True, power_w=600.0 if mode["v"] == "ok" else None)

    monkeypatch.setattr(manager, "driver_for", lambda *a, **k: Drv())
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    with base.sync_session() as s:
        pid = _printer(s)
        plug = _plug(s, pid, watts=None)
        pk = manager.polled_key(plug.id)
        tick = lambda n: manager.tick(s, r, get_settings(), now=t0 + timedelta(seconds=n))  # noqa: E731
        tick(0)
        assert r.get(pk) is not None
        mode["v"] = "err"
        tick(60)
        assert r.get(pk) is None
        mode["v"] = "ok"
        tick(600)  # first good sample after the error: no integration
        assert energy_acc(r, pid) is None
        tick(615)
        assert energy_acc(r, pid) == pytest.approx(600 * 15 / 3_600_000)
        mode["v"] = "nowatts"
        tick(630)
        assert r.get(pk) is None
