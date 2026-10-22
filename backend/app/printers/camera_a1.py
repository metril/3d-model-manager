"""Clean-room A1 camera protocol client (P4 T1). Reimplements the wire
format used by the stock Bambu Handy app / slicer to pull MJPEG-over-TLS
frames from an A1-class printer's local camera port (:6000): an 80-byte
auth packet (username + access code, each null-padded to 32 bytes) sent
once the TLS handshake completes, followed by a stream of
``<little-endian u32 length><12 reserved bytes><payload>`` frames whose
payload is a JPEG image.

Import-safety: stdlib ``socket``/``ssl``/``struct``/``threading`` only --
no ``bambulabs_api`` import here (see ``tests/test_flag_off_imports.py``'s
intent for the MQTT client; this module follows the same discipline).
"""

from __future__ import annotations

import contextlib
import socket
import ssl
import struct
import threading
from collections.abc import Iterator
from types import TracebackType

HEADER_LEN = 16
MAX_FRAME_BYTES = 4 * 1024 * 1024  # generous cap; real frames are <1 MB
JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"

CONNECT_TIMEOUT_S = 5.0
SOCKET_TIMEOUT_S = 5.0


class CameraError(OSError):
    """Base error for the A1 camera client."""


class CameraTimeout(CameraError):
    """No bytes received within ``read_timeout``."""


class CameraAuthError(CameraError):
    """Peer closed the connection (``recv`` returned ``b""``) before any
    frame was received -- almost always a wrong access code."""


class CameraProtocolError(CameraError):
    """A frame header declared an out-of-range length."""


def auth_packet(username: str, access_code: str) -> bytes:
    """Build the 80-byte auth packet: a fixed 16-byte header
    (``0x40, 0x3000, 0, 0`` as little-endian u32s) followed by the
    username and access code, each null-padded to 32 bytes."""
    u, c = username.encode("ascii"), access_code.encode("ascii")
    if len(u) > 32 or len(c) > 32:
        raise ValueError("username/access code longer than 32 bytes")
    return struct.pack("<IIII", 0x40, 0x3000, 0, 0) + u.ljust(32, b"\0") + c.ljust(32, b"\0")


class FrameParser:
    """Incremental parser: feed it arbitrarily-chunked bytes off the wire,
    get back the complete JPEG payloads that chunk completed. Non-JPEG
    payloads (bad magic/trailer) are dropped silently, not fatal -- only a
    declared frame length outside ``(0, MAX_FRAME_BYTES]`` is an error."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        out: list[bytes] = []
        while len(self._buf) >= HEADER_LEN:
            (size,) = struct.unpack_from("<I", self._buf, 0)
            if size == 0 or size > MAX_FRAME_BYTES:
                self._buf.clear()
                raise CameraProtocolError(f"bad frame length {size}")
            end = HEADER_LEN + size
            if len(self._buf) < end:
                break
            payload = bytes(self._buf[HEADER_LEN:end])
            del self._buf[:end]
            if payload[:2] == JPEG_SOI and payload[-2:] == JPEG_EOI:
                out.append(payload)
        return out


class CameraA1Client:
    """Context manager: ``__enter__`` opens a TLS connection to the
    printer's camera port and sends the auth packet; ``frames()`` then
    yields decoded JPEG payloads until ``stop`` is set, ``close()`` is
    called from another thread, or an error occurs.

    Never verifies the peer certificate -- the printer's camera port
    presents the same self-signed LAN-mode cert as its MQTT port.
    """

    def __init__(
        self,
        host: str,
        access_code: str,
        *,
        port: int = 6000,
        username: str = "bblp",
        connect_timeout: float = CONNECT_TIMEOUT_S,
        read_timeout: float = SOCKET_TIMEOUT_S,
        stop: threading.Event | None = None,
    ) -> None:
        self._host = host
        self._access_code = access_code
        self._port = port
        self._username = username
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._stop = stop if stop is not None else threading.Event()
        self._sock: ssl.SSLSocket | None = None
        self._lock = threading.Lock()
        self._closed = False

    def __enter__(self) -> CameraA1Client:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection((self._host, self._port), timeout=self._connect_timeout)
        try:
            tls = ctx.wrap_socket(raw, server_hostname=self._host)
        except Exception:
            raw.close()
            raise
        tls.sendall(auth_packet(self._username, self._access_code))
        with self._lock:
            if self._closed:
                tls.close()
            else:
                self._sock = tls
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def frames(self) -> Iterator[bytes]:
        """Yield decoded JPEG frames until ``stop`` is set or ``close()``
        is called. Raises ``CameraTimeout`` once ``read_timeout`` seconds
        pass with no bytes received, or ``CameraAuthError`` if the peer
        closes the connection before any frame arrives."""
        with self._lock:
            sock = self._sock
        if sock is None:
            raise CameraError("frames() called outside the context manager")

        poll_timeout = min(self._read_timeout, 0.5)
        sock.settimeout(poll_timeout)
        parser = FrameParser()
        got_frame = False
        elapsed_idle = 0.0

        while not self._stop.is_set():
            try:
                data = sock.recv(4096)
            except TimeoutError:
                elapsed_idle += poll_timeout
                if elapsed_idle >= self._read_timeout:
                    raise CameraTimeout(f"no data received within {self._read_timeout}s") from None
                continue
            except OSError:
                if self._closed:
                    return
                raise

            if data == b"":
                if not got_frame:
                    raise CameraAuthError("peer closed connection before any frame")
                return

            elapsed_idle = 0.0
            for payload in parser.feed(data):
                got_frame = True
                yield payload

    def close(self) -> None:
        """Idempotent, thread-safe, safe to call before ``__enter__``.
        Shuts the socket down (unblocking any in-progress ``recv``) and
        closes it; never raises."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sock = self._sock
            self._sock = None
        self._stop.set()
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                sock.close()
