"""Minimal MQTT 3.1.1 broker over TLS, just enough of a Bambu printer for a
slicer's LAN-mode session: CONNECT (user ``bblp`` + access code), SUBSCRIBE,
PUBLISH on ``device/<serial>/request``, PINGREQ, DISCONNECT.

Ported from upstream's ``mqtt_server`` (asyncio, no broker dependency).
Replies are published on ``device/<serial>/report``: an idle ``push_status``
for ``pushall``/``start``, a ``get_version`` answer, and an ack for
``project_file``/``gcode_file`` (then the ``on_print`` callback fires).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import socket
import ssl
from collections.abc import Awaitable, Callable
from typing import Any

from app.virtual_printer.catalog import product_name

log = logging.getLogger("virtual_printer.mqtt")

PrintCallback = Callable[[str, dict[str, Any]], Awaitable[None] | None]
_PERIODIC_PUSH_S = 5.0
MAX_PACKET = 1 << 20


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def _publish_packet(topic: str, payload: bytes) -> bytes:
    t = topic.encode()
    body = len(t).to_bytes(2, "big") + t + payload
    return b"\x30" + _varint(len(body)) + body


class MqttBroker:
    def __init__(
        self,
        *,
        serial: str,
        model: str,
        access_code: str,
        cert_path: str,
        key_path: str,
        bind: str,
        port: int,
        on_print: PrintCallback | None = None,
    ):
        self.serial, self.model, self.access_code = serial, model, access_code
        self.cert_path, self.key_path = cert_path, key_path
        self.bind, self.port = bind, port
        self.on_print = on_print
        self.gcode_state = "IDLE"
        self.current_file = ""
        self.prepare_percent = "0"
        self._seq = 0
        self._server: asyncio.Server | None = None
        self._clients: dict[asyncio.StreamWriter, str] = {}
        self._push_task: asyncio.Task | None = None
        self._print_tasks: set[asyncio.Task] = set()

    # -- lifecycle ---------------------------------------------------
    async def start(self) -> None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.cert_path, self.key_path)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.set_ciphers("DEFAULT:AES256-GCM-SHA384:AES128-GCM-SHA256")
        self._server = await asyncio.start_server(self._client, self.bind, self.port, ssl=ctx)
        self.port = self._server.sockets[0].getsockname()[1]
        self._push_task = asyncio.create_task(self._periodic())

    async def stop(self) -> None:
        if self._push_task:
            self._push_task.cancel()
            await asyncio.gather(self._push_task, return_exceptions=True)
        for t in list(self._print_tasks):
            t.cancel()
        for w in list(self._clients):
            w.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def set_state(self, state: str, filename: str = "", percent: str = "0") -> None:
        self.gcode_state, self.current_file, self.prepare_percent = state, filename, percent

    async def push_all(self) -> None:
        for w, serial in list(self._clients.items()):
            await self._safe(self._send_status(w, serial))

    # -- reports -----------------------------------------------------
    def status_report(self) -> dict:
        self._seq += 1
        name = self.current_file
        return {
            "print": {
                "sequence_id": str(self._seq),
                "command": "push_status",
                "msg": 0,
                "gcode_state": self.gcode_state,
                "gcode_file": name,
                "gcode_file_prepare_percent": self.prepare_percent,
                "subtask_name": name.removesuffix(".3mf").removesuffix(".gcode"),
                "mc_print_stage": "",
                "mc_percent": 0,
                "mc_remaining_time": 0,
                "wifi_signal": "-44dBm",
                "print_error": 0,
                "print_type": "",
                "bed_temper": 25.0,
                "bed_target_temper": 0.0,
                "nozzle_temper": 25.0,
                "nozzle_target_temper": 0.0,
                "chamber_temper": 25.0,
                "cooling_fan_speed": "0",
                "big_fan1_speed": "0",
                "big_fan2_speed": "0",
                "heatbreak_fan_speed": "0",
                "spd_lvl": 1,
                "spd_mag": 100,
                "stg": [],
                "stg_cur": 0,
                "layer_num": 0,
                "total_layer_num": 0,
                "home_flag": 256,  # bit 8: SD card present
                "hw_switch_state": 0,
                "online": {"ahb": False, "rfid": False, "version": 7},
                "ams_status": 0,
                "sdcard": True,
                "storage": {"free": 1_000_000_000, "total": 32_000_000_000},
                "ipcam": {"ipcam_dev": "1", "ipcam_record": "enable", "resolution": "1080p"},
                "lights_report": [{"node": "chamber_light", "mode": "on"}],
                "nozzle_diameter": "0.4",
                "nozzle_type": "hardened_steel",
            }
        }

    def version_report(self, sequence_id: str, serial: str) -> dict:
        product = product_name(self.model)
        mods = [
            ("ota", "01.07.00.00", "OTA"),
            ("esp32", "01.07.22.25", "AP05"),
            ("rv1126", "00.00.27.38", "AP05"),
            ("th", "00.00.04.00", "TH07"),
            ("mc", "00.00.10.00", "MC07"),
        ]
        return {
            "info": {
                "command": "get_version",
                "sequence_id": sequence_id,
                "module": [
                    {
                        "name": n,
                        "product_name": product,
                        "sw_ver": sw,
                        "sw_new_ver": "",
                        "hw_ver": hw,
                        "sn": serial,
                        "flag": 0,
                    }
                    for n, sw, hw in mods
                ],
            }
        }

    async def _publish(self, w: asyncio.StreamWriter, serial: str, payload: dict) -> None:
        # Real printers send 4-space indented JSON; Studio's pre-flight dislikes compact.
        w.write(_publish_packet(f"device/{serial}/report", json.dumps(payload, indent=4).encode()))
        await asyncio.wait_for(w.drain(), 5)

    async def _send_status(self, w: asyncio.StreamWriter, serial: str) -> None:
        await self._publish(w, serial, self.status_report())

    @staticmethod
    async def _safe(coro: Awaitable[None]) -> None:
        with contextlib.suppress(OSError, TimeoutError, ConnectionError):
            await coro

    async def _periodic(self) -> None:
        while True:
            await asyncio.sleep(_PERIODIC_PUSH_S)
            await self.push_all()

    # -- session -----------------------------------------------------
    async def _read_packet(self, r: asyncio.StreamReader) -> tuple[int, int, bytes] | None:
        first = await r.read(1)
        if not first:
            return None
        n, shift = 0, 0
        for _ in range(4):
            b = (await r.readexactly(1))[0]
            n |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        else:
            return None
        if n > MAX_PACKET:
            return None
        return first[0] >> 4, first[0] & 0x0F, await r.readexactly(n) if n else b""

    def _check_connect(self, p: bytes) -> bool:
        try:
            i = 2 + int.from_bytes(p[0:2], "big")  # protocol name
            i += 1  # level
            flags = p[i]
            i += 1 + 2  # flags, keepalive
            i += 2 + int.from_bytes(p[i : i + 2], "big")  # client id
            if flags & 0x04:  # will
                i += 2 + int.from_bytes(p[i : i + 2], "big")
                i += 2 + int.from_bytes(p[i : i + 2], "big")
            if not (flags & 0x80 and flags & 0x40):
                return False
            ul = int.from_bytes(p[i : i + 2], "big")
            user = p[i + 2 : i + 2 + ul].decode()
            i += 2 + ul
            pl = int.from_bytes(p[i : i + 2], "big")
            pw = p[i + 2 : i + 2 + pl].decode()
        except (IndexError, UnicodeDecodeError):
            return False
        return user == "bblp" and hmac.compare_digest(pw, self.access_code)

    async def _client(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        sock = w.get_extra_info("socket")
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        authed = False
        try:
            while True:
                pkt = await asyncio.wait_for(self._read_packet(r), None if authed else 30)
                if pkt is None:
                    break
                ptype, flags, body = pkt
                if ptype == 1:  # CONNECT
                    if not self._check_connect(body):
                        w.write(b"\x20\x02\x00\x05")
                        await w.drain()
                        break
                    authed = True
                    w.write(b"\x20\x02\x00\x00")
                    await w.drain()
                    self._clients[w] = self.serial
                elif not authed:
                    break
                elif ptype == 8:  # SUBSCRIBE
                    await self._subscribe(w, body)
                elif ptype == 10:  # UNSUBSCRIBE
                    w.write(b"\xb0\x02" + body[:2])
                    await w.drain()
                elif ptype == 3:  # PUBLISH
                    await self._publish_in(w, flags, body)
                elif ptype == 12:
                    w.write(b"\xd0\x00")
                    await w.drain()
                elif ptype == 14:
                    break
        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError, ssl.SSLError, OSError):
            pass
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("mqtt session error")
        finally:
            self._clients.pop(w, None)
            with contextlib.suppress(OSError):
                w.close()

    @staticmethod
    def _serial_of(topic: str) -> str | None:
        parts = topic.split("/")
        return parts[1] if len(parts) >= 3 and parts[0] == "device" else None

    async def _subscribe(self, w: asyncio.StreamWriter, p: bytes) -> None:
        pid = p[:2]
        i, granted = 2, bytearray()
        while i + 2 <= len(p):
            tl = int.from_bytes(p[i : i + 2], "big")
            topic = p[i + 2 : i + 2 + tl].decode("utf-8", "ignore")
            qos = p[i + 2 + tl] if i + 2 + tl < len(p) else 0
            i += 3 + tl
            granted.append(min(qos, 1))
            s = self._serial_of(topic)
            if s and s != "+":
                self._clients[w] = s
        w.write(bytes([0x90, 2 + len(granted)]) + pid + bytes(granted))
        await w.drain()
        await self._send_status(w, self._clients.get(w, self.serial))

    async def _publish_in(self, w: asyncio.StreamWriter, flags: int, p: bytes) -> None:
        tl = int.from_bytes(p[:2], "big")
        topic = p[2 : 2 + tl].decode("utf-8", "ignore")
        i = 2 + tl
        qos = (flags & 0x06) >> 1
        if qos:
            pid = p[i : i + 2]
            i += 2
            if qos == 1:
                w.write(b"\x40\x02" + pid)
                await w.drain()
        if not (topic.startswith("device/") and topic.endswith("/request")):
            return
        serial = self._serial_of(topic) or self.serial
        self._clients[w] = serial
        try:
            data = json.loads(p[i:].decode("utf-8", "ignore").rstrip("\x00 \r\n\t"))
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        pushing = data.get("pushing")
        if isinstance(pushing, dict) and pushing.get("command") in ("pushall", "start"):
            await self._send_status(w, serial)
        info = data.get("info")
        if isinstance(info, dict) and info.get("command") == "get_version":
            await self._publish(
                w, serial, self.version_report(str(info.get("sequence_id", "0")), serial)
            )
        pr = data.get("print")
        if isinstance(pr, dict) and pr.get("command") in ("project_file", "gcode_file"):
            await self._on_project_file(w, serial, pr)

    async def _on_project_file(self, w: asyncio.StreamWriter, serial: str, pr: dict) -> None:
        name = str(pr.get("subtask_name") or "")
        shown = str(pr.get("file") or name)
        self.set_state("PREPARE", shown, "0")
        ack = {
            "print": {
                "command": pr.get("command"),
                "sequence_id": str(pr.get("sequence_id", "0")),
                "param": pr.get("param", "Metadata/plate_1.gcode"),
                "subtask_name": name,
                "gcode_state": "PREPARE",
                "gcode_file": shown,
                "gcode_file_prepare_percent": "0",
                "result": "SUCCESS",
                "msg": 0,
            }
        }
        await self._publish(w, serial, ack)
        if self.on_print:
            task = asyncio.create_task(self._run_on_print(name, pr))
            self._print_tasks.add(task)
            task.add_done_callback(self._print_tasks.discard)

    async def _run_on_print(self, name: str, pr: dict) -> None:
        try:
            assert self.on_print is not None
            res = self.on_print(name, pr)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            log.exception("print callback failed")
