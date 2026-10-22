"""In-memory ``PrinterAdapter`` for every non-hardware test (M4 testing
carve-out). Register it over ``bambu_lan`` in a fixture; drive reports via
``emit``.
"""

from __future__ import annotations

from pathlib import Path

from app.models.enums import PrinterKind, PrintJobState
from app.printers.base import (
    PrinterAdapter,
    PrinterConnection,
    PrinterPublicState,
    PrintSpec,
    ProbeResult,
    ReportHandler,
)

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
        self.download_errors = 0
        self.downloads: list[str] = []

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
        return PrinterPublicState(
            gcode_state=merged.get("gcode_state"),
            mc_percent=merged.get("mc_percent"),
            layer_num=merged.get("layer_num"),
            total_layer_num=merged.get("total_layer_num"),
            mc_remaining_time=merged.get("mc_remaining_time"),
            print_error=merged.get("print_error"),
            nozzle_temper=merged.get("nozzle_temper"),
            bed_temper=merged.get("bed_temper"),
            subtask_name=merged.get("subtask_name"),
            wifi_signal=merged.get("wifi_signal"),
        )

    def job_state(self, public: PrinterPublicState) -> PrintJobState | None:
        if public.print_error:
            return PrintJobState.FAILED
        return _JOB_STATE.get(public.gcode_state or "")

    def emit(self, report: dict) -> None:
        assert self._handler is not None, "set_report_handler was never called"
        self._handler(report)
