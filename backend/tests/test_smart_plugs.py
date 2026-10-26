"""Q7 smart plugs: per-driver httpx mocks, auto-off rule, energy feed, routes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import redis as redis_lib
import respx

from app.config import get_settings
from app.crypto import encrypt_secret
from app.models import Printer, SmartPlug
from app.models.enums import PrinterKind
from app.printers.base import state_key
from app.services.ha_state import energy_acc
from app.services.smart_plugs import manager
from app.services.smart_plugs.drivers import (
    HaDriver,
    PlugError,
    RestDriver,
    TasmotaDriver,
)
from app.tasks import base

pytestmark = pytest.mark.usefixtures("printer_enabled")

CREATE = {"name": "P", "host": "10.0.0.5", "serial": "0309ABC", "access_code": "12345678"}


def _printer(s) -> int:
    p = Printer(
        name="P",
        kind=PrinterKind.BAMBU_LAN,
        host="h",
        serial="S",
        access_code_enc=encrypt_secret(get_settings(), "1"),
    )
    s.add(p)
    s.commit()
    return p.id


def _plug(s, pid, kind="rest", cfg=None, **kw) -> SmartPlug:
    cfg = cfg or {
        "on_url": "http://plug/on",
        "off_url": "http://plug/off",
        "status_url": "http://plug/status",
        "state_path": "relay",
        "power_path": "meter.w",
    }
    kw.setdefault("auto_off", kw.get("auto_off_after_min") is not None)
    plug = SmartPlug(
        printer_id=pid, kind=kind, config_enc=encrypt_secret(get_settings(), json.dumps(cfg)), **kw
    )
    s.add(plug)
    s.commit()
    return plug


@respx.mock
def test_tasmota_driver():
    respx.get("http://tas/cm", params={"cmnd": "Power On"}).respond(json={"POWER": "ON"})
    respx.get("http://tas/cm", params={"cmnd": "Power Off"}).respond(json={"POWER": "OFF"})
    respx.get("http://tas/cm", params={"cmnd": "Power"}).respond(json={"POWER": "ON"})
    respx.get("http://tas/cm", params={"cmnd": "Status 8"}).respond(
        json={"StatusSNS": {"ENERGY": {"Power": 123}}}
    )
    d = TasmotaDriver({"host": "tas"})
    d.on()
    d.off()
    st = d.status()
    assert st.on is True and st.power_w == 123.0


@respx.mock
def test_tasmota_failure_is_plugerror():
    respx.get("http://tas/cm").respond(500)
    with pytest.raises(PlugError):
        TasmotaDriver({"host": "tas"}).on()


@respx.mock
def test_rest_driver():
    on = respx.post("http://plug/on").respond(200)
    respx.post("http://plug/off").respond(200)
    respx.get("http://plug/status").respond(json={"relay": True, "meter": {"w": 42.5}})
    d = RestDriver(
        {
            "on_url": "http://plug/on",
            "off_url": "http://plug/off",
            "status_url": "http://plug/status",
            "state_path": "relay",
            "power_path": "meter.w",
        }
    )
    d.on()
    assert on.called
    st = d.status()
    assert st.on is True and st.power_w == 42.5


@respx.mock
def test_ha_driver(monkeypatch):
    from app.models import Setting

    with base.sync_session() as s:
        s.add(
            Setting(
                key="home_assistant",
                value={
                    "ha_url": "http://ha:8123",
                    "ha_token_enc": encrypt_secret(get_settings(), "t"),
                },
            )
        )
        s.commit()
        drv = HaDriver(
            {"entity_id": "switch.printer", "power_entity_id": "sensor.w"}, s, get_settings()
        )
    svc = respx.post("http://ha:8123/api/services/switch/turn_off").respond(200, json=[])
    respx.get("http://ha:8123/api/states/switch.printer").respond(json={"state": "on"})
    respx.get("http://ha:8123/api/states/sensor.w").respond(json={"state": "55.5"})
    drv.off()
    assert svc.called
    st = drv.status()
    assert st.on is True and st.power_w == 55.5


def test_mqtt_driver_publishes(monkeypatch):
    from app.models import Setting
    from app.services.smart_plugs import drivers

    published = []

    class FakeInfo:
        def wait_for_publish(self, timeout=None):
            pass

        def is_published(self):
            return True

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def username_pw_set(self, *a):
            pass

        def connect(self, host, port, keepalive=10):
            published.append(("connect", host, port))

        def loop_start(self):
            pass

        def loop_stop(self):
            pass

        def disconnect(self):
            pass

        def publish(self, topic, payload, qos=0):
            published.append((topic, payload))
            return FakeInfo()

    import paho.mqtt.client as mqtt

    monkeypatch.setattr(mqtt, "Client", FakeClient)
    with base.sync_session() as s:
        s.add(Setting(key="home_assistant", value={"ha_mqtt_url": "mqtt://broker:1883"}))
        s.commit()
        d = drivers.MqttDriver(
            {"command_topic": "cmnd/p/POWER", "off_payload": "0"}, s, get_settings()
        )
    d.off()
    assert ("cmnd/p/POWER", "0") in published and ("connect", "broker", 1883) in published


COLD = {"gcode_state": "FINISH", "nozzle_temper": 30.0}
HOT = {"gcode_state": "FINISH", "nozzle_temper": 180.0}


def test_can_power_off_rule():
    assert manager.can_power_off(COLD)
    assert not manager.can_power_off(HOT)
    assert not manager.can_power_off({"gcode_state": "RUNNING", "nozzle_temper": 20.0})
    assert not manager.can_power_off({"gcode_state": "IDLE"})  # unknown nozzle: fail safe
    assert not manager.can_power_off(None)
    drying = {**COLD, "ams_units": [{"id": 0, "dry_status": 2}]}
    assert not manager.can_power_off(drying)  # never kill a drying cycle
    assert manager.can_power_off({**COLD, "ams_units": [{"id": 0, "dry_status": 0}]})


@respx.mock
def test_auto_off_after_idle_window(redis_url):
    off = respx.post("http://plug/off").respond(200)
    respx.get("http://plug/status").respond(json={"relay": True, "meter": {"w": 5}})
    r = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        pid = _printer(s)
        plug = _plug(s, pid, auto_off_after_min=10)
        plug_id = plug.id
    r.set(state_key(pid), json.dumps(COLD))
    t0 = datetime.now(UTC)
    settings = get_settings()

    with base.sync_session() as s:
        manager.tick(s, r, settings, now=t0)  # starts the idle window
        assert not off.called
        manager.tick(s, r, settings, now=t0 + timedelta(minutes=9))
        assert not off.called
        # nozzle heats back up -> window resets
        r.set(state_key(pid), json.dumps(HOT))
        manager.tick(s, r, settings, now=t0 + timedelta(minutes=11))
        assert not off.called and r.get(manager.idle_key(plug_id)) is None
        r.set(state_key(pid), json.dumps(COLD))
        manager.tick(s, r, settings, now=t0 + timedelta(minutes=12))
        counts = manager.tick(s, r, settings, now=t0 + timedelta(minutes=23))
        assert off.called and counts["turned_off"] == 1
        assert s.get(SmartPlug, plug_id).last_state == "off"
    r.close()


@respx.mock
def test_energy_integration_feeds_accumulator(redis_url):
    respx.get("http://plug/status").respond(json={"relay": True, "meter": {"w": 1000}})
    r = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        pid = _printer(s)
        _plug(s, pid)
    r.delete(f"ha:energy:acc:{pid}", manager.polled_key(1))
    t0 = datetime.now(UTC)
    with base.sync_session() as s:
        manager.tick(s, r, get_settings(), now=t0)
        manager.tick(s, r, get_settings(), now=t0 + timedelta(seconds=60))
    # 1000 W for 60 s = 1/60 kWh
    assert energy_acc(r, pid) == pytest.approx(1 / 60, rel=1e-3)
    r.close()


@respx.mock
def test_tick_records_error_without_leaking(redis_url):
    respx.get("http://plug/status").respond(500)
    r = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        pid = _printer(s)
        pl = _plug(s, pid)
        plug_id = pl.id
        counts = manager.tick(s, r, get_settings())
        assert counts["errors"] == 1
        assert "plug/status" not in (s.get(SmartPlug, plug_id).last_error or "")
    r.close()


@respx.mock
async def test_routes_roundtrip(authenticated_client, viewer_client):
    c = authenticated_client
    pid = (await c.post("/api/printers", json=CREATE)).json()["id"]
    assert (await c.get(f"/api/printers/{pid}/plugs")).json() == []
    body = {
        "name": "Heater",
        "printer_id": pid,
        "kind": "rest",
        "config": {"on_url": "http://plug/on", "off_url": "http://plug/off", "headers": {"k": "v"}},
        "auto_off_after_min": 10,
    }
    r = await c.post("/api/plugs", json=body)
    assert r.status_code == 201
    out = r.json()
    plug_id = out["id"]
    assert out["config"]["headers"] == "***"  # secrets masked on read
    assert out["auto_off"] is True and out["controls_printer_power"] is True
    # a second plug on the same printer (accessory)
    fan = {**body, "name": "Fan", "controls_printer_power": False, "auto_off_after_min": None}
    assert (await c.post("/api/plugs", json=fan)).status_code == 201
    assert len((await c.get(f"/api/printers/{pid}/plugs")).json()) == 2
    # sentinel keeps the stored secret
    body["config"]["headers"] = "***"
    assert (await c.put(f"/api/plugs/{plug_id}", json=body)).status_code == 200

    on = respx.post("http://plug/on").respond(200)
    respx.post("http://plug/off").respond(200)
    r = await c.post(f"/api/plugs/{plug_id}/on")
    assert r.status_code == 200 and r.json()["last_state"] == "on" and on.called
    r = await c.post(f"/api/plugs/{plug_id}/off")
    assert r.json()["last_state"] == "off"

    respx.post("http://plug/on").mock(side_effect=httpx.ConnectError("boom"))
    assert (await c.post(f"/api/plugs/{plug_id}/on")).status_code == 502

    assert (await viewer_client.post(f"/api/plugs/{plug_id}/on")).status_code == 403
    assert (await viewer_client.put(f"/api/plugs/{plug_id}", json=body)).status_code == 403
    assert (await c.delete(f"/api/plugs/{plug_id}")).status_code == 204


async def test_standalone_plug_and_validation(authenticated_client):
    c = authenticated_client
    cfg = {"on_url": "http://plug/on", "off_url": "http://plug/off"}
    r = await c.post("/api/plugs", json={"name": "Lamp", "kind": "rest", "config": cfg})
    assert r.status_code == 201 and r.json()["printer_id"] is None
    assert any(p["name"] == "Lamp" for p in (await c.get("/api/plugs")).json())
    bad = {"name": "x", "kind": "rest", "config": cfg}
    assert (await c.post("/api/plugs", json={**bad, "printer_id": 99999})).status_code == 404
    assert (await c.post("/api/plugs", json={**bad, "schedule_tz": "Mars/Base"})).status_code == 422
    assert (
        await c.post("/api/plugs", json={**bad, "schedule_on_time": "25:00"})
    ).status_code == 422
    assert (await c.post("/api/plugs", json={**bad, "schedule_enabled": True})).status_code == 422


async def test_put_rejects_bad_config(authenticated_client):
    c = authenticated_client
    r = await c.post("/api/plugs", json={"name": "a", "kind": "rest", "config": {}})
    assert r.status_code == 422
    r = await c.post("/api/plugs", json={"name": "a", "kind": "zigbee", "config": {}})
    assert r.status_code == 422


async def test_plugs_summary(authenticated_client, viewer_client):
    c = authenticated_client
    a = (await c.post("/api/printers", json=CREATE)).json()["id"]
    cfg = {"on_url": "http://plug/on", "off_url": "http://plug/off"}
    fan = {
        "name": "Fan",
        "printer_id": a,
        "controls_printer_power": False,
        "kind": "rest",
        "config": cfg,
    }
    main = {"name": "Main", "printer_id": a, "kind": "rest", "config": cfg}
    assert (await c.post("/api/plugs", json=fan)).status_code == 201
    assert (await c.post("/api/plugs", json=main)).status_code == 201
    out = await c.get("/api/printers/plugs/summary")
    assert out.status_code == 200
    assert list(out.json()["plugs"]) == [str(a)]
    assert out.json()["plugs"][str(a)]["name"] == "Main"  # the power plug wins


def _tick(r, now):
    with base.sync_session() as s:
        return manager.tick(s, r, get_settings(), now=now)


@respx.mock
def test_accessory_auto_off_and_power_mark_offline(redis_url):
    r = redis_lib.Redis.from_url(redis_url)
    respx.get("http://plug/status").respond(200, json={"relay": True, "meter": {"w": 5}})
    off = respx.post("http://plug/off").respond(200)
    with base.sync_session() as s:
        pid = _printer(s)
        fan = _plug(s, pid, auto_off_after_min=1, controls_printer_power=False)
        fan_id = fan.id
    hot_idle = {"gcode_state": "FINISH", "nozzle_temper": 200.0, "bed_temper": 60.0}
    r.set(state_key(pid), json.dumps(hot_idle))
    t0 = datetime.now(UTC)
    _tick(r, t0)
    _tick(r, t0 + timedelta(minutes=2))  # accessory: no cold-nozzle gate
    assert off.called
    assert r.get(state_key(pid)) is not None  # accessory never blanks printer state
    assert fan_id


@respx.mock
def test_temperature_mode_waits_for_cool(redis_url):
    r = redis_lib.Redis.from_url(redis_url)
    respx.get("http://plug/status").respond(200, json={"relay": True, "meter": {"w": 5}})
    off = respx.post("http://plug/off").respond(200)
    with base.sync_session() as s:
        pid = _printer(s)
        _plug(s, pid, auto_off=True, off_delay_mode="temperature", off_temp_threshold=40)
    r.set(
        state_key(pid),
        json.dumps({"gcode_state": "FINISH", "nozzle_temper": 35.0, "bed_temper": 55.0}),
    )
    t0 = datetime.now(UTC)
    _tick(r, t0)
    assert not off.called  # bed still warm
    r.set(
        state_key(pid),
        json.dumps({"gcode_state": "FINISH", "nozzle_temper": 35.0, "bed_temper": 30.0}),
    )
    _tick(r, t0 + timedelta(seconds=30))
    assert off.called
    assert r.get(state_key(pid)) is None  # power plug marks the printer offline


@respx.mock
def test_power_alert_cooldown(redis_url, monkeypatch):
    r = redis_lib.Redis.from_url(redis_url)
    respx.get("http://plug/status").respond(200, json={"relay": True, "meter": {"w": 900}})
    sent = []
    monkeypatch.setattr(manager.events, "emit", lambda *a, **k: sent.append(a))
    with base.sync_session() as s:
        pid = _printer(s)
        _plug(s, pid, power_alert_enabled=True, power_alert_high_w=500.0, name="P")
    t0 = datetime.now(UTC)
    _tick(r, t0)
    _tick(r, t0 + timedelta(minutes=5))  # cooldown
    assert len(sent) == 1 and sent[0][0] == "plug_power_alert"
    _tick(r, t0 + timedelta(minutes=40))
    assert len(sent) == 2


@respx.mock
def test_daily_schedule_fires_once(redis_url):
    r = redis_lib.Redis.from_url(redis_url)
    respx.get("http://plug/status").respond(200, json={"relay": False, "meter": {"w": 0}})
    on = respx.post("http://plug/on").respond(200)
    with base.sync_session() as s:
        pid = _printer(s)
        _plug(s, pid, schedule_enabled=True, schedule_on_time="07:30", schedule_off_time="22:00")
    t = datetime(2026, 1, 5, 7, 30, 20, tzinfo=UTC)
    _tick(r, t)
    assert on.call_count == 1
    _tick(r, t + timedelta(minutes=1))
    assert on.call_count == 1  # deduped for the day


@respx.mock
def test_energy_counter_standalone(redis_url):
    r = redis_lib.Redis.from_url(redis_url)
    respx.get("http://plug/status").respond(200, json={"relay": True, "meter": {"w": 1000}})
    with base.sync_session() as s:
        p = _plug(s, None)
        plug_id = p.id
    r.delete(manager.polled_key(plug_id))
    t0 = datetime.now(UTC)
    _tick(r, t0)
    _tick(r, t0 + timedelta(seconds=60))
    with base.sync_session() as s:
        assert s.get(SmartPlug, plug_id).energy_kwh == pytest.approx(1000 * 60 / 3_600_000)


async def test_orphaned_plug_control_needs_settings_write(
    authenticated_client, make_user, make_group, login_as, monkeypatch
):
    from app.api import smart_plugs as sp_api

    monkeypatch.setattr(sp_api, "_command", lambda *a, **k: None)
    c = authenticated_client
    pid = (await c.post("/api/printers", json=CREATE)).json()["id"]
    cfg = {"on_url": "http://plug/on", "off_url": "http://plug/off"}
    body = {"name": "Heater", "printer_id": pid, "kind": "rest", "config": cfg}
    orphan = (await c.post("/api/plugs", json=body)).json()["id"]
    free = (await c.post("/api/plugs", json={**body, "name": "Lamp", "printer_id": None})).json()[
        "id"
    ]
    member = await make_user("member")
    await make_group("ops", permissions=["printers:control"], members=[member])
    mc = await login_as(member)

    assert (await c.delete(f"/api/printers/{pid}")).status_code == 204
    got = {p["id"]: p for p in (await c.get("/api/plugs")).json()}
    assert got[orphan]["orphaned"] is True and got[free]["orphaned"] is False
    assert (await mc.post(f"/api/plugs/{orphan}/on")).status_code == 403
    assert (await mc.post(f"/api/plugs/{free}/on")).status_code == 200  # standalone: as before
    assert (await c.post(f"/api/plugs/{orphan}/on")).status_code == 200  # admin

    # manual unlink of an orphan leaves it orphaned; re-linking clears the flag
    r = await c.put(f"/api/plugs/{orphan}", json={**body, "printer_id": None})
    assert r.json()["orphaned"] is True
    pid2 = (await c.post("/api/printers", json={**CREATE, "serial": "0309ABE"})).json()["id"]
    r = await c.put(f"/api/plugs/{orphan}", json={**body, "printer_id": pid2})
    assert r.json()["orphaned"] is False
