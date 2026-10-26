"""Raw MQTT debug feed + generic MQTT relay."""

import json

import httpx
import pytest
import redis as redis_lib

from app.config import get_settings
from app.printers.base import command_channel
from app.printers.mqtt_relay import MqttRelay, event_for, parse_command
from app.services import mqtt_debug as md
from app.services import service_status as ss
from app.services.mqtt_relay_config import MqttRelayConfig

# --- debug feed -----------------------------------------------------------


def test_build_entry_masks_and_handles_non_json():
    e = md.build_entry(
        "in", "device/x/report", json.dumps({"print": {"access_code": "1234", "a": 1}})
    )
    assert e["payload"]["print"] == {"access_code": "***", "a": 1}
    assert md.build_entry("in", "t", b"\xffnot json")["payload"].startswith("�")
    assert md.build_entry("in", "t", {"x": "y" * 70000})["payload"] == {"_truncated": True}


def test_emit_sync_keeps_last_n_and_publishes(redis_url):
    r = redis_lib.Redis.from_url(redis_url)
    r.delete(md.list_key(7))
    ps = r.pubsub()
    ps.subscribe(md.channel(7))
    ps.get_message(timeout=1)
    for i in range(md.KEEP + 5):
        md.emit_sync(r, 7, md.build_entry("in", "t", {"i": i}))
    assert r.llen(md.list_key(7)) == md.KEEP
    msg = None
    for _ in range(5):
        msg = ps.get_message(timeout=1)
        if msg and msg["type"] == "message":
            break
    assert json.loads(msg["data"])["payload"] == {"i": 0}
    r.delete(md.list_key(7))
    ps.close()
    r.close()


async def test_history_api(authenticated_client: httpx.AsyncClient, redis_url, db_session):
    from app.models import Printer
    from app.models.enums import PrinterKind

    p = Printer(name="P", kind=PrinterKind.BAMBU_LAN, host="h", serial="S1", access_code_enc="x")
    db_session.add(p)
    await db_session.commit()
    r = redis_lib.Redis.from_url(redis_url)
    r.delete(md.list_key(p.id))
    for i in range(3):
        md.emit_sync(r, p.id, md.build_entry("in", "t", {"i": i}))
    body = (await authenticated_client.get(f"/api/admin/printers/{p.id}/mqtt?limit=2")).json()
    r.delete(md.list_key(p.id))
    r.close()
    # lrange 0..1 = the two newest, returned oldest-first
    assert [b["payload"]["i"] for b in body] == [1, 2]
    assert (await authenticated_client.get("/api/admin/printers/99999/mqtt")).status_code == 404


async def test_history_admin_only(viewer_client: httpx.AsyncClient):
    assert (await viewer_client.get("/api/admin/printers/1/mqtt")).status_code == 403


# --- relay ---------------------------------------------------------------


def test_event_for_and_parse_command():
    assert event_for(None, "RUNNING") is None
    assert event_for("IDLE", "RUNNING") == "started"
    assert event_for("RUNNING", "PAUSE") == "paused"
    assert event_for("PAUSE", "RUNNING") == "resumed"
    assert event_for("RUNNING", "FINISH") == "finished"
    assert event_for("RUNNING", "FAILED") == "failed"
    assert event_for("RUNNING", "RUNNING") is None
    assert parse_command("PAUSE") == "pause"
    assert parse_command('{"command": "stop"}') == "stop"
    assert parse_command("home") is None and parse_command("{bad") is None


class FakeClient:
    def __init__(self):
        self.published = []
        self.subscribed = []

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, retain))

    def subscribe(self, t):
        self.subscribed.append(t)


class Msg:
    def __init__(self, topic, payload):
        self.topic, self.payload = topic, payload.encode()


@pytest.fixture
def r(redis_url):
    return redis_lib.Redis.from_url(get_settings().redis_url)


def _relay(r, **cfg):
    relay = MqttRelay(r, get_settings(), active_ids=lambda: {1})
    relay._client = FakeClient()
    relay._connected = True
    relay._config = MqttRelayConfig(enabled=True, host="b", topic_prefix="x/y", **cfg)
    relay._printers = [
        {
            "id": 1,
            "serial": "SER1",
            "name": "A1",
            "model": "A1",
            "last_result": None,
            "queue_length": 0,
        }
    ]
    return relay


def test_publish_live_state_progress_event(r):
    r.set("printer:1:state", json.dumps({"gcode_state": "IDLE", "mc_percent": 0}))
    relay = _relay(r)
    relay._publish_live()
    r.set("printer:1:state", json.dumps({"gcode_state": "RUNNING", "mc_percent": 5}))
    relay._rate._at.clear()
    relay._publish_live()
    topics = {t for t, _, _ in relay._client.published}
    assert {
        "x/y/SER1/availability",
        "x/y/SER1/state",
        "x/y/SER1/progress",
        "x/y/SER1/event",
    } <= topics
    ev = next(p for t, p, _ in relay._client.published if t.endswith("/event"))
    assert json.loads(ev)["event"] == "started"
    avail = next(x for x in relay._client.published if x[0].endswith("/availability"))
    assert avail[1] == "online" and avail[2] is True
    r.delete("printer:1:state")


def test_command_routing_and_denials(r, monkeypatch):
    relay = _relay(r, commands_enabled=True, actor_user_id=5)
    ps = r.pubsub()
    ps.subscribe(command_channel(1))
    ps.get_message(timeout=1)
    monkeypatch.setattr("app.printers.mqtt_relay.can_control_sync", lambda s, u, p: u == 5)
    monkeypatch.setattr("app.printers.mqtt_relay.base.sync_session", _NullSession)

    relay._on_message(None, None, Msg("x/y/SER1/command", "pause"))
    got = None
    for _ in range(5):
        got = ps.get_message(timeout=1)
        if got and got["type"] == "message":
            break
    assert json.loads(got["data"]) == {"command": "pause"}
    res = json.loads(relay._client.published[-1][1])
    assert res["ok"] is True

    relay._on_message(None, None, Msg("x/y/SER1/command", "home"))  # not in relay set
    assert json.loads(relay._client.published[-1][1])["ok"] is False
    relay._on_message(None, None, Msg("x/y/NOPE/command", "stop"))  # unknown serial: ignored
    n = len(relay._client.published)
    relay._config = MqttRelayConfig(
        enabled=True, host="b", topic_prefix="x/y", commands_enabled=True, actor_user_id=9
    )
    relay._on_message(None, None, Msg("x/y/SER1/command", "stop"))  # denied actor
    assert len(relay._client.published) == n + 1
    assert json.loads(relay._client.published[-1][1])["ok"] is False
    ps.close()


class _NullSession:
    def __enter__(self):
        return object()

    def __exit__(self, *a):
        return False


# --- settings API + status -------------------------------------------------


async def test_relay_settings_api(authenticated_client: httpx.AsyncClient, redis_url):
    c = authenticated_client
    assert (await c.put("/api/settings/mqtt-relay", json={"enabled": True})).status_code == 422
    r = await c.put(
        "/api/settings/mqtt-relay",
        json={
            "enabled": True,
            "host": "broker.local",
            "username": "u",
            "password": "pw",
            "topic_prefix": "farm/a1",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_password"] is True and "pw" not in json.dumps(body)
    assert body["topic_prefix"] == "farm/a1" and body["port"] == 1883
    # sentinel keeps password
    body = (await c.put("/api/settings/mqtt-relay", json={"password": "***", "port": 8883})).json()
    assert body["has_password"] is True and body["port"] == 8883
    assert (
        await c.put("/api/settings/mqtt-relay", json={"topic_prefix": "bad/#"})
    ).status_code == 422
    assert (
        await c.put("/api/settings/mqtt-relay", json={"actor_user_id": 99999})
    ).status_code == 422
    body = (await c.put("/api/settings/mqtt-relay", json={"password": ""})).json()
    assert body["has_password"] is False

    # /admin/status reflects it
    rr = redis_lib.Redis.from_url(redis_url)
    rr.delete(ss.CACHE_KEY, "relay:status")
    st = (await c.get("/api/admin/status?refresh=true")).json()["services"]["mqtt_relay"]
    assert st["state"] == "down"  # enabled but printerd published nothing
    rr.set("relay:status", json.dumps({"connected": True, "actor_ok": True}))
    st = (await c.get("/api/admin/status?refresh=true")).json()["services"]["mqtt_relay"]
    assert st["state"] == "ok" and "broker.local" in st["detail"]
    await c.put("/api/settings/mqtt-relay", json={"enabled": False})
    st = (await c.get("/api/admin/status?refresh=true")).json()["services"]["mqtt_relay"]
    assert st["state"] == "off"
    rr.delete(ss.CACHE_KEY, "relay:status")
    rr.close()


async def test_relay_test_endpoint_reports_failure(authenticated_client: httpx.AsyncClient):
    r = await authenticated_client.post(
        "/api/settings/mqtt-relay/test", json={"host": "127.0.0.1", "port": 1}
    )
    assert r.status_code == 200 and r.json()["ok"] is False


async def test_relay_admin_only(viewer_client: httpx.AsyncClient):
    assert (await viewer_client.get("/api/settings/mqtt-relay")).status_code == 403
