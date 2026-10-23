"""Smart-plug drivers (Q7). Each is a small sync object built from the plug's
decrypted config dict: ``on()``, ``off()``, ``status() -> PlugStatus``.
Errors are folded into ``PlugError`` with a fixed message (no URLs/creds)."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.services.ha_client import HaClient, HaUnreachable
from app.services.ha_config import get_ha_config_sync, parse_mqtt_url

TIMEOUT_S = 5.0


class PlugError(Exception):
    pass


@dataclass(frozen=True)
class PlugStatus:
    on: bool | None
    power_w: float | None = None


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dig(data: Any, path: str | None) -> Any:
    if not path:
        return None
    for part in path.split("."):
        if isinstance(data, dict):
            data = data.get(part)
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            return None
    return data


def _as_bool(v: Any, on_values: tuple[str, ...] = ("on", "true", "1")) -> bool | None:
    if v is None:
        return None
    return str(v).strip().lower() in on_values


class TasmotaDriver:
    """Tasmota HTTP: ``/cm?cmnd=Power On`` etc.; energy via ``Status 8``."""

    def __init__(self, cfg: dict) -> None:
        host = (cfg.get("host") or "").strip()
        if not host:
            raise PlugError("tasmota host is required")
        self._base = host if host.startswith("http") else f"http://{host}"
        self._auth = {"user": cfg.get("user", ""), "password": cfg.get("password", "")}
        self._auth = {k: v for k, v in self._auth.items() if v}

    def _cmd(self, cmnd: str) -> dict:
        try:
            r = httpx.get(
                f"{self._base.rstrip('/')}/cm",
                params={"cmnd": cmnd, **self._auth},
                timeout=TIMEOUT_S,
            )
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError):
            raise PlugError("tasmota request failed") from None
        return data if isinstance(data, dict) else {}

    def on(self) -> None:
        self._cmd("Power On")

    def off(self) -> None:
        self._cmd("Power Off")

    def status(self) -> PlugStatus:
        power = self._cmd("Power")
        on = _as_bool(power.get("POWER") or power.get("POWER1"))
        watts = None
        try:
            sns = self._cmd("Status 8")
            watts = _num(_dig(sns, "StatusSNS.ENERGY.Power"))
        except PlugError:
            pass
        return PlugStatus(on, watts)


class HaDriver:
    """Home Assistant switch/light/input_boolean via the saved HA REST config.
    Optional ``power_entity_id`` is a W sensor."""

    def __init__(self, cfg: dict, session: Any, settings: Settings) -> None:
        self._entity = (cfg.get("entity_id") or "").strip()
        if "." not in self._entity:
            raise PlugError("HA entity_id is required")
        self._power = (cfg.get("power_entity_id") or "").strip() or None
        ha = get_ha_config_sync(session, settings)
        if not (ha.ha_url and ha.ha_token):
            raise PlugError("Home Assistant is not configured")
        self._ha = (ha.ha_url, ha.ha_token)

    def _client(self) -> HaClient:
        return HaClient(*self._ha)

    def _call(self, service: str) -> None:
        c = self._client()
        try:
            c.call_service(self._entity.split(".")[0], service, {"entity_id": self._entity})
        except HaUnreachable:
            raise PlugError("home assistant request failed") from None
        finally:
            c.close()

    def on(self) -> None:
        self._call("turn_on")

    def off(self) -> None:
        self._call("turn_off")

    def status(self) -> PlugStatus:
        c = self._client()
        try:
            st = c.state(self._entity)
            pw = c.state(self._power) if self._power else None
        except HaUnreachable:
            raise PlugError("home assistant request failed") from None
        finally:
            c.close()
        on = None if st is None else _as_bool(st.get("state"))
        return PlugStatus(on, _num(pw.get("state")) if pw else None)


class MqttDriver:
    """Publish-only MQTT using the saved HA MQTT broker settings. State is
    assumed from the last command (``status`` returns ``on=None``)."""

    def __init__(self, cfg: dict, session: Any, settings: Settings) -> None:
        self._topic = (cfg.get("command_topic") or "").strip()
        if not self._topic:
            raise PlugError("mqtt command_topic is required")
        self._on = str(cfg.get("on_payload", "ON"))
        self._off = str(cfg.get("off_payload", "OFF"))
        ha = get_ha_config_sync(session, settings)
        if not ha.ha_mqtt_url:
            raise PlugError("MQTT broker is not configured")
        try:
            self._host, self._port, self._tls = parse_mqtt_url(ha.ha_mqtt_url)
        except ValueError:
            raise PlugError("MQTT broker URL is invalid") from None
        self._creds = (ha.ha_mqtt_user, ha.ha_mqtt_password)

    def _publish(self, payload: str) -> None:
        import paho.mqtt.client as mqtt

        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        if self._creds[0]:
            client.username_pw_set(self._creds[0], self._creds[1])
        if self._tls:
            client.tls_set()
        try:
            client.connect(self._host, self._port, keepalive=10)
            client.loop_start()
            info = client.publish(self._topic, payload, qos=1)
            info.wait_for_publish(timeout=TIMEOUT_S)
            if not info.is_published():
                raise PlugError("mqtt publish timed out")
        except (OSError, ValueError, RuntimeError):
            raise PlugError("mqtt publish failed") from None
        finally:
            with contextlib.suppress(Exception):
                client.loop_stop()
                client.disconnect()

    def on(self) -> None:
        self._publish(self._on)

    def off(self) -> None:
        self._publish(self._off)

    def status(self) -> PlugStatus:
        return PlugStatus(None, None)


class RestDriver:
    """Generic REST plug: ``on_url`` / ``off_url`` (+ ``method``, ``headers``,
    ``body``); optional ``status_url`` returning JSON with dotted paths
    ``state_path`` (truthy per ``on_values``) and ``power_path`` (W)."""

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        if not (cfg.get("on_url") and cfg.get("off_url")):
            raise PlugError("on_url and off_url are required")

    def _req(self, method: str, url: str) -> httpx.Response:
        try:
            r = httpx.request(
                method,
                url,
                headers=self._cfg.get("headers") or None,
                content=self._cfg.get("body") or None,
                timeout=TIMEOUT_S,
            )
            r.raise_for_status()
            return r
        except httpx.HTTPError:
            raise PlugError("rest plug request failed") from None

    def on(self) -> None:
        self._req(self._cfg.get("method", "POST").upper(), self._cfg["on_url"])

    def off(self) -> None:
        self._req(self._cfg.get("method", "POST").upper(), self._cfg["off_url"])

    def status(self) -> PlugStatus:
        url = self._cfg.get("status_url")
        if not url:
            return PlugStatus(None, None)
        r = self._req("GET", url)
        try:
            data = r.json()
        except ValueError:
            raise PlugError("rest plug returned a malformed body") from None
        on_values = tuple(str(v).lower() for v in self._cfg.get("on_values", ["on", "true", "1"]))
        return PlugStatus(
            _as_bool(_dig(data, self._cfg.get("state_path")), on_values),
            _num(_dig(data, self._cfg.get("power_path"))),
        )


def build_driver(kind: str, cfg: dict, session: Any, settings: Settings):
    if kind == "tasmota":
        return TasmotaDriver(cfg)
    if kind == "ha":
        return HaDriver(cfg, session, settings)
    if kind == "mqtt":
        return MqttDriver(cfg, session, settings)
    if kind == "rest":
        return RestDriver(cfg)
    raise PlugError("unknown plug kind")
