"""RTSP camera source (Q4). X1/H2/P2-class printers serve an RTSPS stream at
``rtsps://bblp:{code}@host:322/streaming/live/1`` (self-signed cert). We let
ffmpeg do the RTSP/TLS/H.264 work and re-encode to MJPEG on stdout, which
``JpegSplitter`` cuts into frames. ffmpeg is a child process: if it exits the
generator ends/raises and the supervisor's worker restarts the source.

Beta: only exercised against a mediamtx test server in CI, not real X1 hardware.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from types import TracebackType
from urllib.parse import quote

from app.printers.camera_a1 import JPEG_EOI, JPEG_SOI, CameraError

log = logging.getLogger("printerd.camera")

# Bambu RTSP constants live here and only here.
BETA = True
BAMBU_RTSP_PORT = 322
BAMBU_RTSP_PATH = "/streaming/live/1"
BAMBU_RTSP_USER = "bblp"

MAX_BUFFER_BYTES = 8 * 1024 * 1024
RTSP_FPS = 15  # ffmpeg output cap; viewers throttle further via ?fps=


def bambu_rtsp_url(host: str, access_code: str, *, port: int = BAMBU_RTSP_PORT) -> str:
    return f"rtsps://{BAMBU_RTSP_USER}:{quote(access_code, safe='')}@{host}:{port}{BAMBU_RTSP_PATH}"


class JpegSplitter:
    """Incremental SOI/EOI scanner over a concatenated-JPEG byte stream. The
    buffer is bounded: a runaway stream with no EOI is dropped, not grown."""

    def __init__(self, max_buffer: int = MAX_BUFFER_BYTES) -> None:
        self._buf = bytearray()
        self._max = max_buffer

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        out: list[bytes] = []
        while True:
            soi = self._buf.find(JPEG_SOI)
            if soi < 0:
                # keep a trailing 0xFF: the SOI may straddle chunks
                del self._buf[: max(len(self._buf) - 1, 0)]
                break
            if soi:
                del self._buf[:soi]
            eoi = self._buf.find(JPEG_EOI, 2)
            if eoi < 0:
                if len(self._buf) > self._max:
                    self._buf.clear()
                break
            out.append(bytes(self._buf[: eoi + 2]))
            del self._buf[: eoi + 2]
        return out


def build_argv(
    url: str, *, fps: int = RTSP_FPS, tls_insecure: bool = False, ffmpeg: str = "ffmpeg"
) -> list[str]:
    """``tls_insecure`` is accepted for call-site clarity: ffmpeg's rtsps
    default is already no cert verification, and ``-tls_verify`` is not a
    valid rtsp demuxer option (ffmpeg trac #7609)."""
    argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if url.startswith("rtsp"):
        argv += ["-rtsp_transport", "tcp", "-timeout", "10000000"]
    else:
        argv += ["-rw_timeout", "10000000"]
    argv += ["-i", url, "-an", "-vf", f"fps={fps}", "-q:v", "5", "-f", "mjpeg", "-"]
    return argv


class FfmpegMjpegSource:
    """``CameraSource`` backed by an ffmpeg child emitting MJPEG on stdout."""

    min_interval = 1 / 15

    def __init__(self, argv: list[str], stop: threading.Event | None = None) -> None:
        self._argv = argv
        self._stop = stop if stop is not None else threading.Event()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._closed = False

    def __enter__(self) -> FfmpegMjpegSource:
        if shutil.which(self._argv[0]) is None:
            raise CameraError("ffmpeg not found")
        proc = subprocess.Popen(  # noqa: S603 -- argv list, no shell
            self._argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL
        )
        with self._lock:
            if self._closed:
                proc.kill()
            self._proc = proc
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def frames(self) -> Iterator[bytes]:
        with self._lock:
            proc = self._proc
        if proc is None or proc.stdout is None:
            raise CameraError("frames() called outside the context manager")
        fd = proc.stdout.fileno()
        splitter = JpegSplitter()
        got = False
        while not self._stop.is_set():
            try:
                data = os.read(fd, 65536)
            except OSError:
                data = b""
            if not data:
                break
            for frame in splitter.feed(data):
                got = True
                yield frame
        if self._closed or self._stop.is_set():
            return
        rc = proc.wait(timeout=2)
        if not got:
            raise CameraError(f"ffmpeg exited ({rc}) before any frame")

    def close(self) -> None:
        with self._lock:
            self._closed = True
            proc = self._proc
            self._proc = None
        self._stop.set()
        if proc is not None:
            with contextlib.suppress(OSError):
                proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(timeout=2)
            with contextlib.suppress(Exception):
                if proc.stdout:
                    proc.stdout.close()


def open_bambu_rtsp(host: str, access_code: str, stop: threading.Event) -> FfmpegMjpegSource:
    url = bambu_rtsp_url(host, access_code)
    return FfmpegMjpegSource(build_argv(url, tls_insecure=True), stop)
