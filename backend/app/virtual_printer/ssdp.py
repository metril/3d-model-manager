"""SSDP announcer: periodic NOTIFY broadcast plus M-SEARCH replies so slicers
list the virtual printer under "LAN" devices (Bambu uses UDP 2021)."""

from __future__ import annotations

import asyncio
import logging
import socket
import struct

log = logging.getLogger("virtual_printer.ssdp")

MULTICAST = "239.255.255.250"
BROADCAST = "255.255.255.255"
SEARCH_TARGET = "urn:bambulab-com:device:3dprinter:1"
NOTIFY_INTERVAL_S = 30.0


def local_ip(bind: str) -> str:
    if bind and bind != "0.0.0.0":  # noqa: S104
        return bind
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _headers(model: str, name: str, serial: str) -> str:
    return (
        f"DevModel.bambu.com: {model}\r\n"
        f"DevName.bambu.com: {name}\r\n"
        "DevSignal.bambu.com: -44\r\n"
        "DevConnect.bambu.com: lan\r\n"
        "DevBind.bambu.com: free\r\n"
        "Devseclink.bambu.com: secure\r\n"
        "DevInf.bambu.com: eth0\r\n"
        "DevVersion.bambu.com: 01.07.00.00\r\n"
        "DevCap.bambu.com: 1\r\n"
        "\r\n"
    )


class SsdpAnnouncer:
    def __init__(self, *, name: str, model: str, serial: str, bind: str, port: int):
        self.name, self.model, self.serial = name, model, serial
        self.bind, self.port = bind, port
        self._sock: socket.socket | None = None
        self._task: asyncio.Task | None = None

    def notify_message(self) -> bytes:
        return (
            "NOTIFY * HTTP/1.1\r\n"
            f"Host: {MULTICAST}:1990\r\n"
            "Server: UPnP/1.0\r\n"
            f"Location: {local_ip(self.bind)}\r\n"
            f"NT: {SEARCH_TARGET}\r\n"
            "NTS: ssdp:alive\r\n"
            f"USN: {self.serial}\r\n"
            "Cache-Control: max-age=1800\r\n" + _headers(self.model, self.name, self.serial)
        ).encode()

    def response_message(self) -> bytes:
        return (
            "HTTP/1.1 200 OK\r\n"
            "Server: UPnP/1.0\r\n"
            f"Location: {local_ip(self.bind)}\r\n"
            f"ST: {SEARCH_TARGET}\r\n"
            f"USN: {self.serial}\r\n"
            "Cache-Control: max-age=1800\r\n" + _headers(self.model, self.name, self.serial)
        ).encode()

    async def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind((self.bind, self.port))
        try:
            mreq = struct.pack("4s4s", socket.inet_aton(MULTICAST), socket.inet_aton("0.0.0.0"))  # noqa: S104
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            log.debug("multicast join failed; broadcast only")
        sock.setblocking(False)
        self._sock = sock
        self.port = sock.getsockname()[1]
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._sock:
            self._sock.close()
            self._sock = None

    def _notify(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.sendto(self.notify_message(), (BROADCAST, self.port))
        except OSError as exc:
            log.debug("SSDP NOTIFY failed: %s", exc)

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        assert self._sock is not None
        self._notify()
        next_notify = loop.time() + NOTIFY_INTERVAL_S
        while True:
            timeout = max(0.1, next_notify - loop.time())
            try:
                data, addr = await asyncio.wait_for(loop.sock_recvfrom(self._sock, 2048), timeout)
            except TimeoutError:
                self._notify()
                next_notify = loop.time() + NOTIFY_INTERVAL_S
                continue
            except OSError:
                return
            self.handle(data, addr)

    def handle(self, data: bytes, addr: tuple[str, int]) -> None:
        text = data.decode("utf-8", "ignore")
        if "M-SEARCH" not in text:
            return
        if SEARCH_TARGET not in text and "ssdp:all" not in text.lower():
            return
        if self._sock is not None:
            try:
                self._sock.sendto(self.response_message(), addr)
            except OSError as exc:
                log.debug("SSDP reply failed: %s", exc)
