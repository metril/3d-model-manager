"""In-memory ``PrinterAdapter`` for every non-hardware test (M4 testing
carve-out). Register it over ``bambu_lan`` in a fixture; drive reports via
``emit``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from app.models.enums import PrinterKind, PrintJobState
from app.printers.base import (
    CameraSource,
    PrinterAdapter,
    PrinterConnection,
    PrinterPublicState,
    PrintSpec,
    ProbeResult,
    RemoteFile,
    ReportHandler,
    public_state_from,
)


class FakeCamera:
    """In-memory ``CameraSource`` for camera-supervisor tests. ``hang=True``
    makes ``frames()`` block forever, IGNORING the worker's own ``stop``
    event -- only ``close()`` unblocks it, simulating a wedged socket."""

    def __init__(self, frames: list[bytes], hang: bool = False, delay: float = 0.0) -> None:
        self.frames_list = frames
        self.hang = hang
        self.delay = delay
        self._closed = threading.Event()

    def __enter__(self) -> FakeCamera:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def frames(self) -> Iterator[bytes]:
        if self.hang:
            while not self._closed.is_set():
                self._closed.wait(0.05)
            return
        for frame in self.frames_list:
            if self._closed.is_set():
                return
            if self.delay:
                time.sleep(self.delay)
            yield frame

    def close(self) -> None:
        self._closed.set()


_JOB_STATE = {
    "PREPARE": PrintJobState.STARTING,
    "RUNNING": PrintJobState.PRINTING,
    "PAUSE": PrintJobState.PAUSED,
    "FINISH": PrintJobState.FINISHED,
    "FAILED": PrintJobState.FAILED,
}


class FakePrinterAdapter(PrinterAdapter):
    """In-memory adapter for every non-hardware test (M4 testing carve-out).
    Register it over ``bambu_lan`` in a fixture; drive reports via ``emit``.
    """

    kind = PrinterKind.BAMBU_LAN
    probe_result = ProbeResult(ok=True, detail="fake ok", gcode_state="IDLE")

    def __init__(self, conn: PrinterConnection) -> None:
        super().__init__(conn)
        self.connected = False
        self.closed = False
        self.full_status_requests = 0
        self.paused = self.resumed = self.stopped = 0
        self.uploaded: list[PrintSpec] = []
        self._handler: ReportHandler | None = None
        self.files: dict[str, bytes] = {}
        self.file_mtimes: dict[str, datetime] = {}
        self.download_errors = 0
        self.downloads: list[str] = []
        self.camera: FakeCamera | None = None

    def list_dir(self, path: str) -> list[str]:
        parent = path.rstrip("/") or "/"
        return [k.rsplit("/", 1)[1] for k in self.files if (k.rsplit("/", 1)[0] or "/") == parent]

    def download_file(self, remote: str, dest: Path) -> None:
        self.downloads.append(remote)
        if self.download_errors > 0:
            self.download_errors -= 1
            raise OSError("fake download error")
        if remote not in self.files:
            raise FileNotFoundError(remote)
        Path(dest).write_bytes(self.files[remote])

    def list_files(self, path: str) -> list[RemoteFile]:
        parent = path.rstrip("/") or "/"
        out: list[RemoteFile] = []
        for key, data in self.files.items():
            if (key.rsplit("/", 1)[0] or "/") == parent:
                out.append(
                    RemoteFile(
                        name=key.rsplit("/", 1)[1],
                        size=len(data),
                        mtime=self.file_mtimes.get(key),
                    )
                )
        return out

    def test_connection(self, *, timeout: float = 10.0) -> ProbeResult:
        return self.probe_result

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.closed = True

    def set_report_handler(self, handler: ReportHandler) -> None:
        self._handler = handler

    def request_full_status(self) -> None:
        self.full_status_requests += 1

    def upload_and_start(self, spec: PrintSpec) -> None:
        self.uploaded.append(spec)

    def pause(self) -> None:
        self.paused += 1

    def resume(self) -> None:
        self.resumed += 1

    def stop(self) -> None:
        self.stopped += 1

    def merge_report(self, prev: dict | None, report: dict) -> dict:
        merged = dict(prev or {})
        merged.update(report.get("print", report))
        return merged

    def public_state(self, merged: dict) -> PrinterPublicState:
        return public_state_from(merged)

    def job_state(self, public: PrinterPublicState) -> PrintJobState | None:
        if public.print_error:
            return PrintJobState.FAILED
        return _JOB_STATE.get(public.gcode_state or "")

    def emit(self, report: dict) -> None:
        assert self._handler is not None, "set_report_handler was never called"
        self._handler(report)

    def open_camera(self, stop: threading.Event) -> CameraSource:
        if self.camera is None:
            raise NotImplementedError
        return self.camera
