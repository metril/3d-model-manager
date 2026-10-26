"""``mqtt_relay``: printerd thread publishing printer status to an external
MQTT broker (generic, not Home-Assistant specific) and accepting a tiny
command set (pause/resume/stop).

Topics (``<p>`` = configured prefix, ``<serial>`` = printer serial):
  ``<p>/status``                bridge online/offline (retained, LWT)
  ``<p>/<serial>/availability`` online/offline (retained)
  ``<p>/<serial>/state``        full state JSON (rate-gated, not retained)
  ``<p>/<serial>/progress``     {state, progress, remaining, layer, job}
  ``<p>/<serial>/event``        print lifecycle events (started/paused/...)
  ``<p>/<serial>/command``      IN: ``pause|resume|stop`` or ``{"command": ..}``
  ``<p>/<serial>/command_result`` OUT: accepted / rejected + reason

Commands run as the configured service user (needs per-printer control) and
are validated via ``app.printers.commands`` then handed to printerd over the
same Redis command channel the API uses. Mirrors ``ha_bridge`` structure.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from collections.abc import Callable

from app.config import Settings
from app.models import AuditLog
from app.printers import commands as cmds
from app.printers.base import command_channel
from app.printers.ha_bridge import JOIN_TIMEOUT_S, RateGate, _load_printers, paho_client
from app.services.ha_config import actor_status_sync
from app.services.ha_discovery import state_payload
from app.services.mqtt_relay_config import (
    COMMANDS,
    STATUS_KEY,
    STATUS_TTL_S,
    MqttRelayConfig,
    get_relay_config_sync,
)
from app.services.permissions import can_control_sync
from app.services.printer_state import read_state_sync
from app.tasks import base

log = logging.getLogger("printerd.mqtt_relay")

CMD_BURST = 3
CMD_REFILL_S = 2.0

_EVENTS = {
    ("*", "PAUSE"): "paused",
    ("PAUSE", "RUNNING"): "resumed",
    ("*", "FINISH"): "finished",
    ("*", "FAILED"): "failed",
    ("*", "RUNNING"): "started",
}


def event_for(prev: str | None, cur: str | None) -> str | None:
    """Lifecycle event for a gcode_state transition (``None`` = nothing)."""
    if prev is None or cur is None or prev == cur:
        return None
    return _EVENTS.get((prev, cur)) or _EVENTS.get(("*", cur))


def parse_command(payload: str) -> str | None:
    text = payload.strip()
    if text.startswith("{"):
        try:
            text = str(json.loads(text).get("command", ""))
        except (ValueError, AttributeError):
            return None
    text = text.strip().lower()
    return text if text in COMMANDS else None


class MqttRelay:
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
        self._config = MqttRelayConfig()
        self._sig: tuple | None = None
        self._next_config = float("-inf")
        self._next_db = float("-inf")
        self._printers: list[dict] = []
        self._rate = RateGate(clock=clock)
        self._avail: dict[int, bool] = {}
        self._tokens: dict[int, tuple[float, float]] = {}
        self._last_state: dict[int, str | None] = {}
        self._last_progress: dict[int, bytes] = {}
        self._connected = False
        self._error: str | None = None
        self._actor_ok = False
        self._actor_error: str | None = None
        self._published = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="mqtt-relay")
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.tick_s):
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 -- must never die
                log.error("mqtt_relay: tick failed: %s", type(exc).__name__)

    def stop(self, timeout: float = JOIN_TIMEOUT_S) -> None:
        self._stop.set()
        if self._client is not None:
            self._disconnect()
        if self._thread is not None:
            self._thread.join(timeout)

    def _disconnect(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        with contextlib.suppress(Exception):
            client.publish(self._t("status"), "offline", qos=1, retain=True)
        with contextlib.suppress(Exception):
            client.loop_stop()
        with contextlib.suppress(Exception):
            client.disconnect()
        self._connected = False

    # -- helpers ------------------------------------------------------------

    def _t(self, *parts: str) -> str:
        return "/".join((self._config.topic_prefix, *parts))

    def _write_status(self) -> None:
        cfg = self._config
        body = {
            "enabled": cfg.active,
            "connected": self._connected,
            "error": self._error,
            "actor_ok": self._actor_ok,
            "actor_error": self._actor_error,
            "published": self._published,
            "at": time.time(),
        }
        with contextlib.suppress(Exception):
            self.r.set(STATUS_KEY, json.dumps(body), ex=STATUS_TTL_S)

    # -- one loop body -------------------------------------------------------

    def tick(self) -> None:
        now = self.clock()
        if now >= self._next_db:
            self._next_db = now + self.db_every_s
            with base.sync_session() as session:
                self._printers = _load_printers(session)
                self._refresh_actor(session)
        if now >= self._next_config:
            self._next_config = now + self.config_every_s
            with base.sync_session() as session:
                self._apply_config(get_relay_config_sync(session, self.settings))
                self._refresh_actor(session)
        if self._client is not None:
            self._publish_live()
        self._write_status()

    def _refresh_actor(self, session) -> None:
        if not self._config.commands_enabled:
            self._actor_ok, self._actor_error = True, None
            return
        self._actor_ok, self._actor_error = actor_status_sync(
            session, self._config.actor_user_id, [p["id"] for p in self._printers]
        )

    def _apply_config(self, new: MqttRelayConfig) -> None:
        if not new.active:
            if self._client is not None:
                self._disconnect()
            self._config, self._sig, self._error = new, None, None
            return
        sig = new.signature()
        if sig != self._sig:
            if self._client is not None:
                self._disconnect()
            self._config = new
            self._connect()
            self._sig = sig
        self._config = new

    def _connect(self) -> None:
        cfg = self._config
        self._avail = {}
        self._rate = RateGate(clock=self.clock)
        client = self.client_factory()
        client.will_set(self._t("status"), "offline", qos=1, retain=True)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        if cfg.username:
            client.username_pw_set(cfg.username, cfg.password)
        if cfg.tls:
            client.tls_set()
            if not cfg.tls_verify:
                client.tls_insecure_set(True)
        client.connect_async(cfg.host, cfg.port, keepalive=60)
        client.loop_start()
        self._client = client

    # -- paho callbacks (paho thread) ------------------------------------------

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        if rc != 0:
            self._connected, self._error = False, f"connect failed: rc={rc!r}"
            return
        try:
            client.publish(self._t("status"), "online", qos=1, retain=True)
            if self._config.commands_enabled:
                client.subscribe(self._t("+", "command"))
            self._connected, self._error = True, None
        except Exception as exc:  # noqa: BLE001
            self._connected, self._error = False, type(exc).__name__

    def _on_disconnect(self, client, userdata, *args) -> None:
        rc = next((a for a in reversed(args) if isinstance(a, int)), None)
        self._connected, self._error = False, f"disconnected: rc={rc!r}"
        self._avail = {}

    def _bucket(self, pid: int) -> bool:
        """Per-printer token bucket (broker clients are untrusted): bursts of
        ``CMD_BURST`` then one command per ``CMD_REFILL_S``."""
        now = self.clock()
        tokens, last = self._tokens.get(pid, (float(CMD_BURST), now))
        tokens = min(float(CMD_BURST), tokens + (now - last) / CMD_REFILL_S)
        if tokens < 1.0:
            self._tokens[pid] = (tokens, now)
            return False
        self._tokens[pid] = (tokens - 1.0, now)
        return True

    def _reply(self, serial: str, ok: bool, detail: str, command: str | None) -> None:
        with contextlib.suppress(Exception):
            self._client.publish(
                self._t(serial, "command_result"),
                json.dumps({"ok": ok, "command": command, "detail": detail, "at": time.time()}),
                qos=0,
            )

    def _on_message(self, client, userdata, msg) -> None:
        try:
            parts = msg.topic.split("/")
            prefix_n = len(self._config.topic_prefix.split("/"))
            if len(parts) != prefix_n + 2 or parts[-1] != "command":
                return
            serial = parts[-2]
            printer = next((p for p in self._printers if p["serial"] == serial), None)
            if printer is None or not self._config.commands_enabled:
                return
            command = parse_command(msg.payload.decode("utf-8", errors="replace"))
            if command is None:
                self._reply(serial, False, "unsupported command", None)
                return
            try:
                cmds.validate(command, {})
            except cmds.CommandError as exc:
                self._reply(serial, False, exc.message, command)
                return
            if not self._bucket(printer["id"]):
                self._reply(serial, False, "rate limited", command)
                return
            actor_id = self._config.actor_user_id
            with base.sync_session() as session:
                allowed = can_control_sync(session, actor_id, printer["id"])
                session.add(
                    AuditLog(
                        user_id=actor_id,
                        created_by_user_id=actor_id,
                        printer_id=printer["id"],
                        action="mqtt_relay_command" if allowed else "mqtt_relay_command_denied",
                        payload={"command": command, "via": "mqtt_relay"},
                    )
                )
                session.commit()
            if not allowed:
                log.warning("mqtt_relay: service user may not control printer %s", printer["id"])
                self._reply(serial, False, "service user may not control this printer", command)
                return
            self.r.publish(command_channel(printer["id"]), json.dumps({"command": command}))
            self._reply(serial, True, "accepted", command)
        except Exception as exc:  # noqa: BLE001 -- paho thread
            log.error("mqtt_relay: on_message failed: %s", type(exc).__name__)

    # -- publish ---------------------------------------------------------------

    def _pub(self, topic: str, payload, *, retain: bool = False, qos: int = 0) -> None:
        with contextlib.suppress(Exception):
            self._client.publish(topic, payload, qos=qos, retain=retain)
            self._published += 1

    def _publish_live(self) -> None:
        if not self._connected:
            return
        active = self.active_ids()
        for printer in self._printers:
            pid, serial = printer["id"], printer["serial"]
            if not serial:
                continue
            available = pid in active
            if self._avail.get(pid) != available:
                self._avail[pid] = available
                self._pub(
                    self._t(serial, "availability"),
                    "online" if available else "offline",
                    retain=True,
                    qos=1,
                )
            public = read_state_sync(self.r, pid)
            body = state_payload(
                public, last_result=printer["last_result"], queue_length=printer["queue_length"]
            )
            body["name"] = printer["name"]
            data = json.dumps(body).encode()
            if self._rate.due(pid, data):
                self._pub(self._t(serial, "state"), data)
                self._rate.mark(pid, data)
            progress = json.dumps(
                {k: body[k] for k in ("state", "progress", "remaining", "layer", "current_job")}
            ).encode()
            if progress != self._last_progress.get(pid):
                self._last_progress[pid] = progress
                self._pub(self._t(serial, "progress"), progress)
            cur = body["state"]
            ev = event_for(self._last_state.get(pid), cur)
            self._last_state[pid] = cur
            if ev:
                self._pub(
                    self._t(serial, "event"),
                    json.dumps(
                        {
                            "event": ev,
                            "state": cur,
                            "job": body["current_job"],
                            "printer": printer["name"],
                            "at": time.time(),
                        }
                    ),
                )
