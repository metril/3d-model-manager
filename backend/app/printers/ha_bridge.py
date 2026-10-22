"""``ha_bridge``: printerd's own thread bridging printer state to Home
Assistant over MQTT (P8 plan Task 3). Owns its own thread -- never the
status-poll or command threads (same "never blocks the worker" rule as
``CameraSupervisor``). Reads HA connection config from ``app.services.
ha_config`` (re-read every ``config_every_s``), printer rows + derived
``last_result``/``queue_length`` from the DB (re-read every ``db_every_s``),
and live state from Redis (``app.services.printer_state``/``camera_state``)
on every ``tick()``.

Discovery (14 retained configs/printer, ``app.services.ha_discovery``) is
re-published in full on every broker connect; per-printer availability is
retained and published only on change; the state payload is rate-gated to
at most one publish per second per printer (``RateGate``). A deleted
printer, or a discovery-prefix/serial change, is diffed against the
``ha:published`` Redis hash and cleared with empty retained payloads to its
old topics.

Import-safety: ``paho.mqtt.client`` is lazy-imported inside ``paho_client()``
only (repo convention -- see ``app.printers.probe``), so this module itself
never pulls it in at import time.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from collections.abc import Callable

from sqlalchemy import select

from app.config import Settings
from app.models import PrintArchive, Printer, PrintJob, PrintQueueEntry
from app.models.enums import QueueState
from app.printers.base import command_channel
from app.services import camera_state
from app.services.ha_config import HaConfig, get_ha_config_sync, parse_mqtt_url
from app.services.ha_discovery import (
    BRIDGE_STATUS,
    BUTTONS,
    discovery_messages,
    node_id,
    removal_topics,
    state_payload,
    topics,
)
from app.services.printer_state import read_state_sync
from app.tasks import base

log = logging.getLogger("printerd.ha_bridge")

CAMERA_VIEWER_ID = "ha-bridge"
CAMERA_INTERVAL_S = 30.0
COMMAND_FILTER = "tdmm/+/command"
PUBLISHED_HASH_KEY = "ha:published"
BRIDGE_STATUS_REDIS_KEY = "ha:bridge:status"
BRIDGE_STATUS_TTL_S = 30
JOIN_TIMEOUT_S = 3.0
_QUEUE_ACTIVE_STATES = (QueueState.WAITING, QueueState.SLICING, QueueState.READY)


def paho_client():
    """Build a fresh ``paho.mqtt.client.Client`` (lazy import)."""
    import paho.mqtt.client as mqtt

    return mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)


class RateGate:
    """Pure (no I/O) per-printer rate limiter: ``due`` is true only when the
    payload changed since the last ``mark`` AND at least ``min_interval``
    seconds have passed since that mark (or there's never been one)."""

    def __init__(
        self, min_interval: float = 1.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.min_interval = min_interval
        self.clock = clock
        self._payload: dict[int, bytes] = {}
        self._at: dict[int, float] = {}

    def due(self, pid: int, payload: bytes) -> bool:
        if payload == self._payload.get(pid):
            return False
        last = self._at.get(pid)
        return last is None or (self.clock() - last) >= self.min_interval

    def mark(self, pid: int, payload: bytes) -> None:
        self._payload[pid] = payload
        self._at[pid] = self.clock()


def _load_printers(session) -> list[dict]:
    """All ``Printer`` rows plus each one's latest archived result (one
    ``DISTINCT ON`` query) and current queue length (``waiting``/``slicing``/
    ``ready`` per-file ``print_queue`` entries targeting it specifically, or
    targeting no specific printer at all)."""
    rows = session.execute(select(Printer)).scalars().all()

    last_results: dict[int, str] = {
        pid: result.value
        for pid, result in session.execute(
            select(PrintJob.printer_id, PrintArchive.result)
            .join(PrintJob, PrintJob.id == PrintArchive.print_job_id)
            .distinct(PrintJob.printer_id)
            .order_by(PrintJob.printer_id, PrintArchive.created_at.desc())
        ).all()
    }

    queue_targets = (
        session.execute(
            select(PrintQueueEntry.printer_id).where(
                PrintQueueEntry.state.in_(_QUEUE_ACTIVE_STATES),
                PrintQueueEntry.file_id.is_not(None),
            )
        )
        .scalars()
        .all()
    )
    unassigned = sum(1 for pid in queue_targets if pid is None)
    per_printer: dict[int, int] = {}
    for pid in queue_targets:
        if pid is not None:
            per_printer[pid] = per_printer.get(pid, 0) + 1

    return [
        {
            "id": p.id,
            "serial": p.serial,
            "name": p.name,
            "model": p.model,
            "enabled": p.enabled,
            "last_result": last_results.get(p.id),
            "queue_length": per_printer.get(p.id, 0) + unassigned,
        }
        for p in rows
    ]


class HaBridge:
    def __init__(
        self,
        r,
        settings: Settings,
        *,
        active_ids: Callable[[], set[int]],
        tick_s: float = 0.5,
        config_every_s: float = 10.0,
        db_every_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        client_factory: Callable[[], object] = paho_client,
    ) -> None:
        self.r = r
        self.settings = settings
        self.active_ids = active_ids
        self.tick_s = tick_s
        self.config_every_s = config_every_s
        self.db_every_s = db_every_s
        self.clock = clock
        self.client_factory = client_factory

        self._client = None
        self._config: HaConfig | None = None
        self._config_sig: tuple | None = None
        self._next_config_read = float("-inf")
        self._next_db_read = float("-inf")
        self._printers: list[dict] = []
        self._rate_gate = RateGate(clock=clock)
        self._availability: dict[int, bool] = {}
        self._camera_last_pub: dict[int, float] = {}
        self._bridge_connected = False
        self._bridge_error: str | None = None

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="ha-bridge")
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.tick_s):
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 -- must never die
                log.error("ha_bridge: tick failed: %s", type(exc).__name__)

    def stop(self, timeout: float = JOIN_TIMEOUT_S) -> None:
        self._stop.set()
        if self._client is not None:
            self._publish_offline_and_disconnect()
        if self._thread is not None:
            self._thread.join(timeout)
        self._release_camera_viewers()

    def _release_camera_viewers(self) -> None:
        """Drop every "ha-bridge" camera-viewer registration this instance
        still holds (one per printer it was actively capturing for) --
        otherwise it would sit in Redis until ``VIEWER_TTL_S`` expires on
        its own, keeping a printer's camera worker alive for no reason."""
        for pid in list(self._camera_last_pub):
            with contextlib.suppress(Exception):
                camera_state.remove_viewer(self.r, pid, CAMERA_VIEWER_ID)
        self._camera_last_pub = {}

    def _publish_offline_and_disconnect(self) -> None:
        client = self._client
        self._client = None
        try:
            client.publish(BRIDGE_STATUS, "offline", qos=1, retain=True)
        except Exception:
            log.debug("ha_bridge: offline publish failed during stop")
        with contextlib.suppress(Exception):
            client.loop_stop()
        with contextlib.suppress(Exception):
            client.disconnect()
        self._set_bridge_status(False, "stopped")

    def _set_bridge_status(self, connected: bool, error: str | None) -> None:
        """Write ``ha:bridge:status`` as JSON ``{connected, error, at}``
        with a 30s TTL -- a stale/crashed bridge's status goes stale (fails
        open to "unknown") instead of lying "connected" forever. Refreshed
        on every tick for as long as there's a live client -- connected OR
        disconnected-and-retrying -- so the web card shows "offline" during
        a broker drop instead of going stale to "unknown" after 30s."""
        self._bridge_connected = connected
        self._bridge_error = error
        payload = json.dumps({"connected": connected, "error": error, "at": time.time()})
        with contextlib.suppress(Exception):
            self.r.set(BRIDGE_STATUS_REDIS_KEY, payload, ex=BRIDGE_STATUS_TTL_S)

    # -- one loop body -------------------------------------------------

    def tick(self) -> None:
        now = self.clock()
        if now >= self._next_db_read:
            self._next_db_read = now + self.db_every_s
            with base.sync_session() as session:
                self._printers = _load_printers(session)
            if self._config is not None:
                self._diff_published()

        if self._client is not None:
            self._set_bridge_status(self._bridge_connected, self._bridge_error)

        if now >= self._next_config_read:
            self._next_config_read = now + self.config_every_s
            with base.sync_session() as session:
                new_config = get_ha_config_sync(session, self.settings)
            self._apply_config(new_config)

        if self._client is not None and self._config is not None:
            self._publish_live_state()

    # -- config / connect ------------------------------------------------

    def _apply_config(self, new_config: HaConfig) -> None:
        if not new_config.ha_mqtt_url:
            if self._client is not None:
                self._publish_offline_and_disconnect()
            self._config = new_config
            self._config_sig = None
            return

        new_sig = new_config.mqtt_signature()
        if self._config_sig is None or new_sig != self._config_sig:
            old_prefix = self._config.ha_discovery_prefix if self._config else None
            if old_prefix is not None and old_prefix != new_config.ha_discovery_prefix:
                self._clear_prefix(old_prefix)
            self._reconnect(new_config)
            self._config_sig = new_sig
        self._config = new_config

    def _clear_prefix(self, old_prefix: str) -> None:
        """A discovery-prefix change invalidates every retained config under
        the OLD prefix -- clear them (if we have a live client) before
        reconnecting."""
        if self._client is None:
            return
        for printer in self._printers:
            for topic in removal_topics(old_prefix, printer["serial"]):
                with contextlib.suppress(Exception):
                    self._client.publish(topic, b"", qos=1, retain=True)

    def _reconnect(self, config: HaConfig) -> None:
        if self._client is not None:
            self._publish_offline_and_disconnect()
        self._availability = {}
        self._release_camera_viewers()

        client = self.client_factory()
        client.will_set(BRIDGE_STATUS, "offline", qos=1, retain=True)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        if config.ha_mqtt_user:
            client.username_pw_set(config.ha_mqtt_user, config.ha_mqtt_password)
        host, port, tls = parse_mqtt_url(config.ha_mqtt_url)
        if tls:
            client.tls_set()
        client.connect_async(host, port, keepalive=60)
        client.loop_start()
        self._client = client

    # -- paho callbacks ---------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        if rc != 0:
            log.warning("ha_bridge: broker connect failed, rc=%r", rc)
            self._set_bridge_status(False, f"connect failed: rc={rc!r}")
            return
        try:
            client.publish(BRIDGE_STATUS, "online", qos=1, retain=True)
            active = self.active_ids()
            for printer in self._printers:
                for topic, payload in discovery_messages(
                    self._prefix(), printer["serial"], printer["name"], printer["model"]
                ):
                    client.publish(topic, json.dumps(payload), qos=1, retain=True)
                available = printer["id"] in active
                client.publish(
                    topics(printer["serial"])["availability"],
                    "online" if available else "offline",
                    qos=1,
                    retain=True,
                )
                self._availability[printer["id"]] = available
            client.subscribe(COMMAND_FILTER)
            self._rate_gate = RateGate(clock=self.clock)
            self._set_bridge_status(True, None)
        except Exception as exc:  # noqa: BLE001 -- callback runs on paho's own thread
            log.error("ha_bridge: on_connect failed: %s", type(exc).__name__)
            self._set_bridge_status(False, type(exc).__name__)

    def _on_disconnect(self, client, userdata, *args) -> None:
        """paho's VERSION2 callback is ``(client, userdata, flags, rc,
        properties)`` on MQTTv5 and ``(client, userdata, rc)`` on MQTTv3.1.1
        -- read the trailing int-like arg as the reason code regardless of
        which shape shows up, rather than pinning to one signature."""
        rc = next((a for a in reversed(args) if isinstance(a, int)), None)
        log.warning("ha_bridge: broker disconnected, rc=%r", rc)
        self._set_bridge_status(False, f"disconnected: rc={rc!r}")

    def _on_message(self, client, userdata, msg) -> None:
        try:
            parts = msg.topic.split("/")
            if len(parts) != 3:
                return
            node = parts[1]
            pid = next((p["id"] for p in self._printers if node_id(p["serial"]) == node), None)
            if pid is None:
                return
            payload = msg.payload.decode("utf-8", errors="replace")
            if payload in BUTTONS:
                self.r.publish(command_channel(pid), json.dumps({"command": payload}))
            else:
                log.debug("ha_bridge: dropped command %r for printer %s", payload, pid)
        except Exception as exc:  # noqa: BLE001 -- callback runs on paho's own thread
            log.error("ha_bridge: on_message failed: %s", type(exc).__name__)

    # -- per-tick publish ---------------------------------------------------

    def _prefix(self) -> str:
        return self._config.ha_discovery_prefix if self._config else "homeassistant"

    def _publish_live_state(self) -> None:
        client = self._client
        active = self.active_ids()
        for printer in self._printers:
            pid = printer["id"]
            t = topics(printer["serial"])

            available = pid in active
            if self._availability.get(pid) != available:
                self._availability[pid] = available
                with contextlib.suppress(Exception):
                    client.publish(
                        t["availability"], "online" if available else "offline", qos=1, retain=True
                    )

            public = read_state_sync(self.r, pid)
            payload_dict = state_payload(
                public,
                last_result=printer["last_result"],
                queue_length=printer["queue_length"],
            )
            payload = json.dumps(payload_dict).encode()
            if self._rate_gate.due(pid, payload):
                with contextlib.suppress(Exception):
                    client.publish(t["state"], payload, qos=0, retain=False)
                self._rate_gate.mark(pid, payload)

            self._tick_camera(pid, t["camera"], payload_dict["printing"])

    def _tick_camera(self, pid: int, camera_topic: str, printing: bool) -> None:
        if not printing:
            if pid in self._camera_last_pub:
                del self._camera_last_pub[pid]
                with contextlib.suppress(Exception):
                    camera_state.remove_viewer(self.r, pid, CAMERA_VIEWER_ID)
            return

        with contextlib.suppress(Exception):
            camera_state.add_viewer(self.r, pid, CAMERA_VIEWER_ID)

        last = self._camera_last_pub.get(pid)
        now = self.clock()
        if last is not None and now - last < CAMERA_INTERVAL_S:
            return
        frame = camera_state.get_frame(self.r, pid)
        if frame is not None:
            with contextlib.suppress(Exception):
                self._client.publish(camera_topic, frame, qos=0, retain=False)
        self._camera_last_pub[pid] = now

    # -- ha:published diff ---------------------------------------------

    def _publish_discovery_for(self, printer: dict) -> None:
        """Publish one printer's 14 discovery configs + its current
        availability right now, instead of waiting for the next broker
        reconnect -- used when `_diff_published` sees a printer that's new
        or whose serial/name/model changed while already connected."""
        if self._client is None:
            return
        for topic, payload in discovery_messages(
            self._prefix(), printer["serial"], printer["name"], printer["model"]
        ):
            with contextlib.suppress(Exception):
                self._client.publish(topic, json.dumps(payload), qos=1, retain=True)
        available = printer["id"] in self.active_ids()
        with contextlib.suppress(Exception):
            self._client.publish(
                topics(printer["serial"])["availability"],
                "online" if available else "offline",
                qos=1,
                retain=True,
            )
        self._availability[printer["id"]] = available

    def _diff_published(self) -> None:
        current = {p["id"]: (self._prefix(), p["serial"]) for p in self._printers}
        raw = self.r.hgetall(PUBLISHED_HASH_KEY) or {}
        stored: dict[int, tuple[str, str]] = {}
        for key, value in raw.items():
            key = key.decode() if isinstance(key, bytes) else key
            value = value.decode() if isinstance(value, bytes) else value
            try:
                decoded = json.loads(value)
                stored[int(key)] = (decoded["prefix"], decoded["serial"])
            except (ValueError, KeyError, TypeError):
                continue

        for pid, (old_prefix, old_serial) in stored.items():
            if current.get(pid) != (old_prefix, old_serial):
                # Only drop the hash entry once the removal publish actually
                # went out (requires a live, connected client) -- otherwise
                # (bridge disconnected / unconfigured) the OLD retained
                # configs would be stranded in HA forever, since nothing
                # would ever retry clearing them (M3).
                if self._client is None:
                    continue
                try:
                    for topic in removal_topics(old_prefix, old_serial):
                        self._client.publish(topic, b"", qos=1, retain=True)
                except Exception:
                    log.debug("ha_bridge: removal publish failed for printer %s", pid)
                    continue
                self.r.hdel(PUBLISHED_HASH_KEY, str(pid))
                self._availability.pop(pid, None)
                self._camera_last_pub.pop(pid, None)

        printers_by_id = {p["id"]: p for p in self._printers}
        for pid, sig in current.items():
            if stored.get(pid) != sig:
                # New printer, or an existing one's serial/name/model
                # changed -- publish its discovery right now instead of
                # waiting for the next broker reconnect (I4).
                printer = printers_by_id.get(pid)
                if printer is not None:
                    self._publish_discovery_for(printer)
                self.r.hset(
                    PUBLISHED_HASH_KEY, str(pid), json.dumps({"prefix": sig[0], "serial": sig[1]})
                )
