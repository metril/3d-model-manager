"""``app.printers.ha_bridge`` (P8 plan Task 3): unit tests against a fake
clock/fake paho client, plus integration tests against a real mosquitto
broker (``tests/ha_fixtures.py``)."""

from __future__ import annotations

import json
import logging
import time
from urllib.parse import urlsplit

import pytest
import redis as redis_lib

from app.config import get_settings
from app.models import Printer, Setting
from app.models.enums import PrinterKind
from app.printers.base import command_channel
from app.printers.ha_bridge import HaBridge, RateGate
from app.services import camera_state as cs
from app.services.ha_config import HaConfig
from app.services.ha_discovery import BRIDGE_STATUS, node_id
from app.tasks import base

JPEG = b"\xff\xd8\xff\xe0" + bytes(10) + b"\xff\xd9"


class FakeClient:
    """Enough of ``paho.mqtt.client.Client``'s surface for a unit test:
    ``loop_start`` synchronously fires ``on_connect`` with rc=0, as if the
    connect succeeded immediately."""

    def __init__(self) -> None:
        self.published: list[tuple[str, object, int, bool]] = []
        self.subscribed: list[str] = []
        self.will = None
        self.on_connect = None
        self.on_message = None
        self.loop_started = False
        self.disconnected = False
        self.connect_args = None
        self.creds = None
        self.fire_on_connect = True

    def will_set(self, topic, payload=None, qos=0, retain=False):
        self.will = (topic, payload, qos, retain)

    def reconnect_delay_set(self, min_delay=1, max_delay=30):
        pass

    def username_pw_set(self, username, password=None):
        self.creds = (username, password)

    def tls_set(self, *a, **k):
        pass

    def connect_async(self, host, port, keepalive=60):
        self.connect_args = (host, port, keepalive)

    def loop_start(self):
        self.loop_started = True
        if self.fire_on_connect and self.on_connect:
            self.on_connect(self, None, None, 0)

    def loop_stop(self):
        self.loop_started = False

    def disconnect(self):
        self.disconnected = True

    def subscribe(self, topic_filter):
        self.subscribed.append(topic_filter)

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))


@pytest.fixture
def r(redis_url):
    return redis_lib.Redis.from_url(get_settings().redis_url)


# ---------------------------------------------------------------------------
# RateGate (pure)
# ---------------------------------------------------------------------------


def test_rate_gate():
    t = [0.0]
    gate = RateGate(min_interval=1.0, clock=lambda: t[0])
    publishes = []
    for i, dt in enumerate([0.0, 0.25, 0.5, 0.75, 1.0]):
        t[0] = dt
        payload = f"v{i}".encode()
        if gate.due(1, payload):
            gate.mark(1, payload)
            publishes.append(payload)
    assert len(publishes) == 2
    assert publishes[-1] == b"v4"  # the last of the 5 changes is eventually published


def test_rate_gate_unchanged_payload_never_due():
    t = [0.0]
    gate = RateGate(min_interval=1.0, clock=lambda: t[0])
    assert gate.due(1, b"same") is True
    gate.mark(1, b"same")
    t[0] = 5.0
    assert gate.due(1, b"same") is False  # unchanged, however long it's been


# ---------------------------------------------------------------------------
# Unit tests (fake clock + fake client, real redis for camera_state ops)
# ---------------------------------------------------------------------------


def test_camera_every_30s_while_printing(r):
    t = [0.0]
    client = FakeClient()
    bridge = HaBridge(
        r,
        get_settings(),
        active_ids=lambda: set(),
        clock=lambda: t[0],
        client_factory=lambda: client,
    )
    bridge._client = client
    pid = 9001
    cs.put_frame(r, pid, JPEG)

    bridge._tick_camera(pid, "tdmm/node/camera", True)
    assert cs.viewer_count(r, pid) == 1
    assert len(client.published) == 1

    t[0] = 10.0
    bridge._tick_camera(pid, "tdmm/node/camera", True)
    assert len(client.published) == 1  # not yet 30s since the first publish

    t[0] = 31.0
    bridge._tick_camera(pid, "tdmm/node/camera", True)
    assert len(client.published) == 2

    bridge._tick_camera(pid, "tdmm/node/camera", False)
    assert cs.viewer_count(r, pid) == 0
    assert pid not in bridge._camera_last_pub


def test_no_config_idle(r):
    created = []

    def factory():
        created.append(1)
        return FakeClient()

    bridge = HaBridge(r, get_settings(), active_ids=lambda: set(), client_factory=factory)
    bridge._apply_config(
        HaConfig(
            ha_mqtt_url=None,
            ha_mqtt_user=None,
            ha_mqtt_password=None,
            ha_url=None,
            ha_token=None,
        )
    )
    assert bridge._client is None
    assert not created


def test_stop_before_connect_returns(r):
    bridge = HaBridge(r, get_settings(), active_ids=lambda: set())
    t0 = time.monotonic()
    bridge.stop()
    assert time.monotonic() - t0 < 3.0


# ---------------------------------------------------------------------------
# Integration tests (real mosquitto)
# ---------------------------------------------------------------------------


def _seed_printer(*, serial: str, name: str = "p") -> int:
    with base.sync_session() as s:
        printer = Printer(
            name=name,
            kind=PrinterKind.BAMBU_LAN,
            host="h",
            serial=serial,
            access_code_enc="x",
            enabled=True,
        )
        s.add(printer)
        s.commit()
        s.refresh(printer)
        return printer.id


def _delete_printer(pid: int) -> None:
    with base.sync_session() as s:
        printer = s.get(Printer, pid)
        s.delete(printer)
        s.commit()


def _seed_ha_config(
    mqtt_url: str,
    *,
    prefix: str = "homeassistant",
    user: str | None = None,
    password: str | None = None,
) -> None:
    value = {"ha_mqtt_url": mqtt_url, "ha_discovery_prefix": prefix}
    if user is not None:
        value["ha_mqtt_user"] = user
    if password is not None:
        # Legacy-plaintext fallback (app.services.ha_config) -- no Fernet key needed here.
        value["ha_mqtt_password_enc"] = password
    with base.sync_session() as s:
        row = s.get(Setting, "home_assistant")
        if row is None:
            s.add(Setting(key="home_assistant", value=value))
        else:
            row.value = value
        s.commit()


def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _next_message(pubsub, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
        if msg and msg["type"] == "message":
            return msg
    return None


def _make_bridge(r, active_ids):
    # ``redis_url`` is session-scoped (unlike the per-test-truncated Postgres
    # container) -- Postgres identity columns restart at 1 every test, so a
    # stale ``ha:published`` entry from an EARLIER test's printer sharing the
    # same id could otherwise be mistaken for a serial change on this one.
    r.delete("ha:published")
    return HaBridge(
        r, get_settings(), active_ids=active_ids, tick_s=0.1, config_every_s=0.2, db_every_s=0.2
    )


def test_discovery_retained_on_connect(mqtt_url, mqtt_probe, r, migrated_db, printer_enabled):
    pid = _seed_printer(serial="S1")
    _seed_ha_config(mqtt_url)
    bridge = _make_bridge(r, lambda: {pid})
    bridge.start()
    try:
        probe = mqtt_probe("homeassistant/#")
        assert probe.wait_for_count(14, timeout=15.0)
    finally:
        bridge.stop()


def test_button_routes_to_redis(mqtt_url, r, migrated_db, printer_enabled):
    import paho.mqtt.client as mqtt

    pid = _seed_printer(serial="S2")
    _seed_ha_config(mqtt_url)
    bridge = _make_bridge(r, lambda: {pid})
    bridge.start()
    pubsub = r.pubsub()
    pubsub.subscribe(command_channel(pid))
    pub_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    try:
        time.sleep(1.0)  # let the bridge connect + subscribe first
        parsed = urlsplit(mqtt_url)
        pub_client.connect(parsed.hostname, parsed.port, keepalive=60)
        pub_client.loop_start()
        time.sleep(0.5)
        node = node_id("S2")
        pub_client.publish(f"tdmm/{node}/command", "pause")
        msg = _next_message(pubsub)
        assert msg is not None
        assert json.loads(msg["data"]) == {"command": "pause"}

        pub_client.publish(f"tdmm/{node}/command", "rm -rf")
        assert _next_message(pubsub, timeout=2.0) is None
    finally:
        pub_client.loop_stop()
        pub_client.disconnect()
        bridge.stop()
        pubsub.close()


def test_delete_printer_removes_discovery(mqtt_url, mqtt_probe, r, migrated_db, printer_enabled):
    pid = _seed_printer(serial="S3")
    _seed_ha_config(mqtt_url)
    bridge = _make_bridge(r, lambda: {pid})
    bridge.start()
    try:
        node = node_id("S3")
        probe = mqtt_probe(f"homeassistant/+/{node}/+/config")
        assert probe.wait_for_count(14, timeout=15.0)
        assert _wait_until(lambda: r.hexists("ha:published", str(pid)), timeout=5.0)

        _delete_printer(pid)
        removed = probe.wait_for(lambda m: m[1] == b"", timeout=20.0)
        assert removed is not None
        assert not r.hexists("ha:published", str(pid))
    finally:
        bridge.stop()


def test_lwt_offline_on_drop(mqtt_url, mqtt_probe, r, migrated_db, printer_enabled):
    _seed_ha_config(mqtt_url)
    bridge = _make_bridge(r, lambda: set())
    bridge.start()
    try:
        probe = mqtt_probe("tdmm/bridge/status")
        assert probe.wait_for(lambda m: m[1] == b"online", timeout=15.0)
        probe.messages.clear()

        client = bridge._client
        sock = client.socket()
        client.loop_stop()
        if sock is not None:
            sock.close()

        assert probe.wait_for(lambda m: m[1] == b"offline", timeout=10.0) is not None
    finally:
        bridge._client = None  # already dead -- stop() must not try to use it
        bridge.stop()


def test_reconnect_after_broker_restart(
    mosquitto, mqtt_url, mqtt_probe, r, migrated_db, printer_enabled
):
    pid = _seed_printer(serial="S4")
    _seed_ha_config(mqtt_url)
    bridge = _make_bridge(r, lambda: {pid})
    bridge.start()
    try:
        probe = mqtt_probe("#")
        assert probe.wait_for(lambda m: m[0] == BRIDGE_STATUS and m[1] == b"online", timeout=15.0)
        probe.messages.clear()

        mosquitto.get_wrapped_container().restart()

        assert (
            probe.wait_for(lambda m: m[0] == BRIDGE_STATUS and m[1] == b"online", timeout=40.0)
            is not None
        )
        discovery_count = sum(1 for m in probe.messages if m[0].startswith("homeassistant/"))
        assert discovery_count >= 14
    finally:
        bridge.stop()


def test_bridge_secret_never_logged(mqtt_url, r, migrated_db, printer_enabled, caplog):
    _seed_ha_config(mqtt_url, user="baduser", password="SUPERSECRETPW")
    bridge = _make_bridge(r, lambda: set())
    with caplog.at_level(logging.DEBUG):
        bridge.start()
        time.sleep(1.5)
        bridge.stop()
    assert "SUPERSECRETPW" not in caplog.text
