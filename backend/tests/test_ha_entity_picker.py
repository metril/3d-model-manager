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
    assert route.call_count == 6  # exactly one /api/states call per request

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
        _poll(s, r, 10_060.0)
    assert energy_acc(r, pid) == pytest.approx(1000 * 60 / 3_600_000)
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
        _poll(s, r, 10_060.0)
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
        _poll(s, r, 10_060.0)
    assert energy_acc(r, pid) == pytest.approx(600 * 60 / 3_600_000)


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
    assert row["active"] is False and "Main plug" in row["shadowed_by"]

    await c.post(
        "/api/settings/home-assistant/links",
        json={"printer_id": pid, "entity_id": "sensor.kwh", "role": "energy_kwh"},
    )
    row = (await c.get("/api/settings/home-assistant/links")).json()[0]
    assert "sensor.kwh" in row["shadowed_by"]


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
