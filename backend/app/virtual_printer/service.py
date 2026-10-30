"""Runs one virtual printer: SSDP + MQTT + FTPS, correlating a slicer's FTP
upload with its ``project_file`` command (either arrival order)."""

from __future__ import annotations

import asyncio
import logging
import socket
from pathlib import Path
from typing import Any

from app.config import Settings
from app.virtual_printer import certs
from app.virtual_printer.ftps import FtpsServer
from app.virtual_printer.intake import IngestResult, VpConfig, VpNetwork, ingest, normalize_name
from app.virtual_printer.mqtt import MqttBroker
from app.virtual_printer.ssdp import SsdpAnnouncer

log = logging.getLogger("virtual_printer")

# A file uploaded with no project_file command (Studio's "upload only") is
# archived, never queued, once this many seconds pass.
ORPHAN_UPLOAD_S = 30.0
PENDING_COMMAND_S = 120.0


def preflight(net: VpNetwork) -> list[str]:
    """Human-readable problems for every required port that isn't free."""
    problems = []
    for label, kind, port in (
        ("SSDP", socket.SOCK_DGRAM, net.port_ssdp),
        ("MQTT", socket.SOCK_STREAM, net.port_mqtt),
        ("FTPS", socket.SOCK_STREAM, net.port_ftp),
    ):
        if port == 0:
            continue
        with socket.socket(socket.AF_INET, kind) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((net.bind, port))
            except OSError as exc:
                proto = "udp" if kind == socket.SOCK_DGRAM else "tcp"
                problems.append(f"{label} port {port}/{proto} is not available ({exc.strerror})")
    return problems


class VirtualPrinterService:
    def __init__(self, settings: Settings, cfg: VpConfig, net: VpNetwork):
        self.settings, self.cfg, self.net = settings, cfg, net
        base = settings.data_dir / "virtual-printer"
        self.upload_dir = base / "uploads"
        self.results: list[IngestResult] = []
        self._uploads: dict[str, Path] = {}
        self._commands: dict[str, dict[str, Any]] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._tasks: set[asyncio.Task] = set()
        cert, key = certs.ensure_cert(base / "certs", cfg.serial)
        self.mqtt = MqttBroker(
            serial=cfg.serial,
            model=cfg.model,
            access_code=cfg.access_code,
            cert_path=str(cert),
            key_path=str(key),
            bind=net.bind,
            port=net.port_mqtt,
            on_print=self._on_print,
        )
        self.ftps = FtpsServer(
            upload_dir=self.upload_dir,
            access_code=cfg.access_code,
            cert_path=str(cert),
            key_path=str(key),
            bind=net.bind,
            port=net.port_ftp,
            pasv_min=net.pasv_min,
            pasv_max=net.pasv_max,
            pasv_address=net.pasv_address,
            on_upload=self._on_upload,
        )
        self.ssdp = SsdpAnnouncer(
            name=cfg.name,
            model=cfg.model,
            serial=cfg.serial,
            bind=net.bind,
            port=net.port_ssdp,
        )

    async def start(self) -> None:
        await self.ftps.start()
        await self.mqtt.start()
        await self.ssdp.start()
        log.info(
            "virtual printer %r (%s) up: ssdp=%s mqtt=%s ftps=%s mode=%s",
            self.cfg.name,
            self.cfg.serial,
            self.ssdp.port,
            self.mqtt.port,
            self.ftps.port,
            self.cfg.mode.value,
        )

    async def stop(self) -> None:
        for t in self._timers.values():
            t.cancel()
        await self.ssdp.stop()
        await self.mqtt.stop()
        await self.ftps.stop()

    # -- correlation -------------------------------------------------
    def _command_key(self, subtask: str, options: dict[str, Any]) -> str:
        url = str(options.get("url") or "")
        return normalize_name(url.rsplit("/", 1)[-1] if url else subtask or "")

    async def _on_print(self, subtask: str, options: dict[str, Any]) -> None:
        key = self._command_key(subtask, options)
        self._cancel(key)
        path = self._uploads.pop(key, None)
        if path is not None and await asyncio.to_thread(path.exists):
            await self._process(key, path, options)
            return
        self._commands[key] = options
        self._arm(key, PENDING_COMMAND_S, self._expire_command)

    async def _on_upload(self, path: Path, _ip: str) -> None:
        key = normalize_name(path.name)
        self._cancel(key)
        options = self._commands.pop(key, None)
        if options is not None:
            await self._process(key, path, options)
            return
        self._uploads[key] = path
        self._arm(key, ORPHAN_UPLOAD_S, self._expire_upload)

    def _arm(self, key: str, delay: float, fn) -> None:
        loop = asyncio.get_running_loop()
        self._timers[key] = loop.call_later(delay, lambda: self._spawn(fn(key)))

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _cancel(self, key: str) -> None:
        t = self._timers.pop(key, None)
        if t:
            t.cancel()

    async def _expire_command(self, key: str) -> None:
        self._timers.pop(key, None)
        self._commands.pop(key, None)
        self.mqtt.set_state("IDLE")
        await self.mqtt.push_all()

    async def _expire_upload(self, key: str) -> None:
        self._timers.pop(key, None)
        path = self._uploads.pop(key, None)
        if path is not None and await asyncio.to_thread(path.exists):
            await self._process(key, path, None)

    async def _process(self, key: str, path: Path, options: dict[str, Any] | None) -> None:
        name = path.name
        try:
            res = await asyncio.to_thread(ingest, self.settings, self.cfg, path, options)
            self.results.append(res)
            log.info("virtual printer ingested %s -> %s", name, res)
        except Exception:
            log.exception("virtual printer ingest failed for %s", name)
            await asyncio.to_thread(path.unlink, missing_ok=True)
            self.mqtt.set_state("FAILED", name)
        else:
            self.mqtt.set_state("FINISH", name, "100")
        await self.mqtt.push_all()
