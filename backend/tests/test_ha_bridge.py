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
from app.models import Printer, Setting, User
from app.models.auth import Group, GroupMember, PrinterAccess
from app.models.enums import PrinterKind, UserRole
from app.printers.base import command_channel
from app.printers.ha_bridge import (
    BRIDGE_STATUS_REDIS_KEY,
    PUBLISHED_HASH_KEY,
    HaBridge,
    RateGate,
)
from app.services import camera_state as cs
from app.services.ha_config import HaConfig
from app.services.ha_discovery import BRIDGE_STATUS, node_id, topics
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
# Final review I4/M3: `_diff_published` publishes/retires discovery live,
# without going through the DB/mosquitto fixtures (direct state pokes).
# ---------------------------------------------------------------------------


def _bare_bridge(r, client):
    bridge = HaBridge(r, get_settings(), active_ids=lambda: {1}, client_factory=lambda: client)
    bridge._client = client
    bridge._config = HaConfig(
        ha_mqtt_url="mqtt://broker:1883",
        ha_mqtt_user=None,
        ha_mqtt_password=None,
        ha_url=None,
        ha_token=None,
    )
    return bridge


def test_new_printer_after_connect_gets_discovery_immediately(r):
    """Final review I4: a printer added (or a serial/name/model change)
    while the bridge is already connected publishes its 14 discovery
    configs + availability right away, instead of waiting for the next
    broker reconnect."""
    client = FakeClient()
    bridge = _bare_bridge(r, client)
    bridge._printers = []
    bridge._diff_published()  # baseline: nothing published yet
    client.published.clear()

    bridge._printers = [{"id": 1, "serial": "NEW1", "name": "New", "model": "A1", "enabled": True}]
    bridge._diff_published()

    config_topics = [t for t, *_ in client.published if "/config" in t]
    assert len(config_topics) == 14
    availability = [p for p in client.published if p[0] == topics("NEW1")["availability"]]
    assert availability and availability[-1][1] == "online"


def test_removal_publish_failure_keeps_hash_entry(r):
    """Final review M3: if the removal publish raises (bridge not really
    connected despite ``_client`` being set), the ``ha:published`` hash
    entry for that printer must NOT be dropped -- otherwise the old
    retained discovery configs are stranded forever, since nothing would
    ever retry clearing them."""

    class _RaisingClient(FakeClient):
        def publish(self, topic, payload=None, qos=0, retain=False):
            raise RuntimeError("broker write failed")

    client = _RaisingClient()
    bridge = _bare_bridge(r, client)
    bridge._printers = [{"id": 1, "serial": "S1", "name": "N", "model": "A1", "enabled": True}]
    # Seed the hash directly (bypassing the publish path) as if printer 1
    # was previously published under a DIFFERENT serial.
    r.hset(PUBLISHED_HASH_KEY, "1", json.dumps({"prefix": "homeassistant", "serial": "OLD"}))

    bridge._diff_published()

    assert r.hexists(PUBLISHED_HASH_KEY, "1")  # kept, not dropped


def test_removal_publish_skipped_when_disconnected_keeps_hash_entry(r):
    bridge = HaBridge(r, get_settings(), active_ids=lambda: set())
    bridge._client = None
    bridge._config = HaConfig(
        ha_mqtt_url="mqtt://broker:1883",
        ha_mqtt_user=None,
        ha_mqtt_password=None,
        ha_url=None,
        ha_token=None,
    )
    bridge._printers = []
    r.hset(PUBLISHED_HASH_KEY, "9", json.dumps({"prefix": "homeassistant", "serial": "OLD"}))

    bridge._diff_published()

    assert r.hexists(PUBLISHED_HASH_KEY, "9")


# ---------------------------------------------------------------------------
# Final review I5: ha:bridge:status is JSON {connected, error, at}, TTL 30s.
# ---------------------------------------------------------------------------


def test_bridge_status_json_on_connect_and_disconnect(r):
    client = FakeClient()
    bridge = HaBridge(r, get_settings(), active_ids=lambda: set(), client_factory=lambda: client)
    bridge._apply_config(
        HaConfig(
            ha_mqtt_url="mqtt://broker:1883",
            ha_mqtt_user=None,
            ha_mqtt_password=None,
            ha_url=None,
            ha_token=None,
        )
    )
    raw = r.get(BRIDGE_STATUS_REDIS_KEY)
    status = json.loads(raw)
    assert status == {
        "connected": True,
        "error": None,
        "actor_ok": False,
        "actor_error": "no HA service account",
        "at": status["at"],
    }
    ttl = r.ttl(BRIDGE_STATUS_REDIS_KEY)
    assert 0 < ttl <= 30

    bridge._on_disconnect(client, None, 7)
    status = json.loads(r.get(BRIDGE_STATUS_REDIS_KEY))
    assert status["connected"] is False
    assert status["error"] is not None


def test_bridge_status_keeps_refreshing_while_disconnected(r):
    """Coordinator follow-up: after `_on_disconnect`, `tick()` must keep
    refreshing `ha:bridge:status` as `{connected: False, error, at}` with
    the same 30s TTL -- the client is still alive and retrying, so the
    status must say "offline", not go stale to "unknown" after 30s."""
    t = [0.0]
    client = FakeClient()
    bridge = HaBridge(
        r,
        get_settings(),
        active_ids=lambda: set(),
        clock=lambda: t[0],
        client_factory=lambda: client,
        db_every_s=1000.0,
        config_every_s=1000.0,
    )
    bridge._apply_config(
        HaConfig(
            ha_mqtt_url="mqtt://broker:1883",
            ha_mqtt_user=None,
            ha_mqtt_password=None,
            ha_url=None,
            ha_token=None,
        )
    )
    bridge._on_disconnect(client, None, 7)
    first = json.loads(r.get(BRIDGE_STATUS_REDIS_KEY))
    assert first["connected"] is False
    # Isolate this tick from the db/config resync branches (both due
    # immediately otherwise, since `_next_db_read`/`_next_config_read`
    # start at -inf) -- this test is only about the bottom status refresh.
    bridge._next_db_read = float("inf")
    bridge._next_config_read = float("inf")

    t[0] = 25.0  # before the 30s TTL would otherwise expire
    bridge.tick()
    second = json.loads(r.get(BRIDGE_STATUS_REDIS_KEY))
    assert second["connected"] is False
    assert second["error"] == first["error"]
    assert second["at"] >= first["at"]
    assert r.ttl(BRIDGE_STATUS_REDIS_KEY) > 25


def test_bridge_status_refreshed_every_tick(r):
    t = [0.0]
    client = FakeClient()
    bridge = HaBridge(
        r,
        get_settings(),
        active_ids=lambda: set(),
        clock=lambda: t[0],
        client_factory=lambda: client,
        db_every_s=1000.0,
        config_every_s=1000.0,
    )
    bridge._apply_config(
        HaConfig(
            ha_mqtt_url="mqtt://broker:1883",
            ha_mqtt_user=None,
            ha_mqtt_password=None,
            ha_url=None,
            ha_token=None,
        )
    )
    first_at = json.loads(r.get(BRIDGE_STATUS_REDIS_KEY))["at"]

    t[0] = 25.0  # before the 30s TTL would otherwise expire
    bridge.tick()
    second_at = json.loads(r.get(BRIDGE_STATUS_REDIS_KEY))["at"]
    assert second_at >= first_at
    assert r.ttl(BRIDGE_STATUS_REDIS_KEY) > 25


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


def _seed_user(
    role: UserRole, *, active: bool = True, grant: str | None = None, pid: int = 0
) -> int:
    """A user; ``grant`` = ``"acl"`` (ACL can_control, no global perm),
    ``"perm"`` (global printers:control, no ACL row) or ``"both"``."""
    with base.sync_session() as s:
        user = User(username=f"u-{role.value}-{grant}-{active}", role=role, is_active=active)
        s.add(user)
        s.flush()
        if grant:
            group = Group(
                name=f"g-{user.id}", permissions=["printers:control"] if grant != "acl" else []
            )
            s.add(group)
            s.flush()
            s.add(GroupMember(group_id=group.id, user_id=user.id))
            if grant != "perm":
                s.add(PrinterAccess(group_id=group.id, printer_id=pid, can_control=True))
        s.commit()
        return user.id


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
    actor: int | None = None,
) -> None:
    value = {"ha_mqtt_url": mqtt_url, "ha_discovery_prefix": prefix}
    if actor is not None:
        value["ha_actor_user_id"] = actor
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
    _seed_ha_config(mqtt_url, actor=_seed_user(UserRole.ADMIN))
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
        # Discovery is published after "online" and arrives asynchronously.
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            discovery_count = sum(
                1 for m in list(probe.messages) if m[0].startswith("homeassistant/")
            )
            if discovery_count >= 14:
                break
            time.sleep(0.1)
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


# ---------------------------------------------------------------------------
# HA service account gating (unit, fake paho client)
# ---------------------------------------------------------------------------


class _Msg:
    def __init__(self, topic: str, payload: str) -> None:
        self.topic = topic
        self.payload = payload.encode()


def _gated_bridge(r, actor: int | None, serial: str):
    pid = _seed_printer(serial=serial)
    client = FakeClient()
    bridge = HaBridge(r, get_settings(), active_ids=lambda: set(), client_factory=lambda: client)
    bridge._apply_config(
        HaConfig(
            ha_mqtt_url="mqtt://broker:1883",
            ha_mqtt_user=None,
            ha_mqtt_password=None,
            ha_url=None,
            ha_token=None,
            ha_actor_user_id=actor,
        )
    )
    bridge._printers = [{"id": pid, "serial": serial, "name": "p", "model": None}]
    return bridge, client, pid


def _send(bridge, client, serial: str, payload: str = "pause") -> None:
    bridge._on_message(client, None, _Msg(f"tdmm/{node_id(serial)}/command", payload))


def _published(r, pid: int, fn) -> list[dict]:
    pubsub = r.pubsub()
    pubsub.subscribe(command_channel(pid))
    pubsub.get_message(timeout=1)
    fn()
    out = []
    while (m := pubsub.get_message(timeout=0.3)) is not None:
        if m["type"] == "message":
            out.append(json.loads(m["data"]))
    pubsub.close()
    return out


def test_command_dropped_without_actor(r, migrated_db):
    bridge, client, pid = _gated_bridge(r, None, "G0")
    assert _published(r, pid, lambda: _send(bridge, client, "G0")) == []
    with base.sync_session() as s:
        bridge._refresh_actor(s)
    bridge._set_bridge_status(True, None)
    status = json.loads(r.get(BRIDGE_STATUS_REDIS_KEY))
    assert status["actor_ok"] is False
    assert status["actor_error"] == "no HA service account"


def test_command_dropped_for_inactive_actor(r, migrated_db):
    uid = _seed_user(UserRole.ADMIN, active=False)
    bridge, client, pid = _gated_bridge(r, uid, "G1")
    assert _published(r, pid, lambda: _send(bridge, client, "G1")) == []
    with base.sync_session() as s:
        bridge._refresh_actor(s)
    assert bridge._actor_ok is False
    assert bridge._actor_error == "HA service account is missing or inactive"


@pytest.mark.parametrize("grant", ["acl", "perm", None])
def test_command_dropped_when_actor_lacks_control(r, migrated_db, grant):
    pid_probe = _seed_printer(serial="Gprobe")  # first printer -> id reused below
    uid = _seed_user(UserRole.VIEWER, grant=grant, pid=pid_probe)
    bridge, client, _ = _gated_bridge(r, uid, "G2")
    bridge._printers[0]["id"] = pid_probe
    assert _published(r, pid_probe, lambda: _send(bridge, client, "G2")) == []


@pytest.mark.parametrize("kind", ["both", "admin"])
def test_command_published_for_allowed_actor(r, migrated_db, kind):
    pid_probe = _seed_printer(serial="Gok0")
    if kind == "admin":
        uid = _seed_user(UserRole.ADMIN)
    else:
        uid = _seed_user(UserRole.VIEWER, grant="both", pid=pid_probe)
    bridge, client, _ = _gated_bridge(r, uid, "Gok")
    bridge._printers[0]["id"] = pid_probe
    with base.sync_session() as s:
        bridge._refresh_actor(s)
    assert bridge._actor_ok is True and bridge._actor_error is None
    assert _published(r, pid_probe, lambda: _send(bridge, client, "Gok")) == [{"command": "pause"}]


def _controller_user(pids: list[int], name: str) -> int:
    """Non-admin viewer with global printers:control + ACL can_control on ``pids``."""
    with base.sync_session() as s:
        user = User(username=name, role=UserRole.VIEWER)
        group = Group(name=f"g-{name}", permissions=["printers:control"])
        s.add_all([user, group])
        s.flush()
        s.add(GroupMember(group_id=group.id, user_id=user.id))
        for pid in pids:
            s.add(PrinterAccess(group_id=group.id, printer_id=pid, can_control=True))
        s.commit()
        return user.id


def test_acl_actor_allowed_and_denied_per_printer(r, migrated_db):
    pid_a = _seed_printer(serial="GA")
    pid_b = _seed_printer(serial="GB")
    uid = _controller_user([pid_a], "acl-actor")
    bridge, client, _ = _gated_bridge(r, uid, "GX")
    bridge._printers = [
        {"id": pid_a, "serial": "GA", "name": "a", "model": None},
        {"id": pid_b, "serial": "GB", "name": "b", "model": None},
    ]
    with base.sync_session() as s:
        bridge._refresh_actor(s)
    assert bridge._actor_ok is True  # controls at least one known printer
    for button in ("pause", "resume", "stop"):
        assert _published(r, pid_a, lambda b=button: _send(bridge, client, "GA", b)) == [
            {"command": button}
        ]
        assert _published(r, pid_b, lambda b=button: _send(bridge, client, "GB", b)) == []


def test_actor_without_any_control_status(r, migrated_db):
    pid = _seed_printer(serial="GN")
    uid = _seed_user(UserRole.VIEWER, grant="perm", pid=pid)  # perm, no ACL row
    bridge, client, _ = _gated_bridge(r, uid, "GM")
    bridge._printers = [{"id": pid, "serial": "GN", "name": "n", "model": None}]
    with base.sync_session() as s:
        bridge._refresh_actor(s)
    assert bridge._actor_ok is False
    assert bridge._actor_error == "HA service account lacks printer control"
    bridge._set_bridge_status(True, None)
    status = json.loads(r.get(BRIDGE_STATUS_REDIS_KEY))
    assert status["actor_ok"] is False
    assert status["actor_error"] == "HA service account lacks printer control"
