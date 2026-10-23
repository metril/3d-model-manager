"""User-supplied external camera (Q4): ``options.camera = {kind, url_enc}``
with kind mjpeg | rtsp | snapshot. Takes precedence over the built-in camera."""

from __future__ import annotations

import base64
import contextlib
import threading
import urllib.parse
import urllib.request
from collections.abc import Iterator
from types import TracebackType

from app.printers.camera_a1 import CameraError
from app.printers.camera_rtsp import FfmpegMjpegSource, JpegSplitter, build_argv

KINDS = ("mjpeg", "rtsp", "snapshot")
ALLOWED_SCHEMES = {
    "mjpeg": ("http", "https"),
    "snapshot": ("http", "https"),
    "rtsp": ("rtsp", "rtsps"),
}
HTTP_TIMEOUT_S = 10.0
SNAPSHOT_INTERVAL_S = 1.0


def validate_url(kind: str, url: str) -> None:
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if kind not in KINDS or scheme not in ALLOWED_SCHEMES[kind]:
        raise ValueError(f"{kind} camera needs a {'/'.join(ALLOWED_SCHEMES.get(kind, ()))} URL")


def split_credentials(url: str) -> tuple[str, dict[str, str]]:
    """urllib ignores ``user:pass@`` in the URL: strip it and send Basic auth."""
    parts = urllib.parse.urlsplit(url)
    if parts.username is None:
        return url, {}
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parts.port:
        host += f":{parts.port}"
    cred = f"{urllib.parse.unquote(parts.username)}:{urllib.parse.unquote(parts.password or '')}"
    token = base64.b64encode(cred.encode()).decode()
    return urllib.parse.urlunsplit(parts._replace(netloc=host)), {"Authorization": f"Basic {token}"}


class ExternalCameraSource:
    min_interval = 1 / 15

    def __init__(self, kind: str, url: str, stop: threading.Event | None = None) -> None:
        validate_url(kind, url)
        self._kind = kind
        self._url = url
        self._stop = stop if stop is not None else threading.Event()
        self._ffmpeg: FfmpegMjpegSource | None = None
        self._resp = None
        self._closed = False

    def __enter__(self) -> ExternalCameraSource:
        if self._kind == "rtsp":
            self._ffmpeg = FfmpegMjpegSource(
                build_argv(self._url, tls_insecure=self._url.startswith("rtsps")), self._stop
            )
            self._ffmpeg.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _open(self):
        url, headers = split_credentials(self._url)
        req = urllib.request.Request(url, headers=headers)  # noqa: S310 -- scheme validated
        return urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S)  # noqa: S310

    def frames(self) -> Iterator[bytes]:
        if self._kind == "rtsp":
            assert self._ffmpeg is not None
            yield from self._ffmpeg.frames()
        elif self._kind == "mjpeg":
            self._resp = self._open()
            splitter = JpegSplitter()
            while not self._stop.is_set():
                chunk = self._resp.read(16384)
                if not chunk:
                    raise CameraError("mjpeg stream ended")
                yield from splitter.feed(chunk)
        else:
            while not self._stop.is_set():
                with self._open() as resp:
                    body = resp.read(8 * 1024 * 1024)
                frames = JpegSplitter().feed(body)
                if not frames:
                    raise CameraError("snapshot URL did not return a JPEG")
                yield frames[0]
                self._stop.wait(SNAPSHOT_INTERVAL_S)

    def close(self) -> None:
        self._closed = True
        self._stop.set()
        if self._ffmpeg is not None:
            self._ffmpeg.close()
        if self._resp is not None:
            with contextlib.suppress(Exception):
                self._resp.close()
