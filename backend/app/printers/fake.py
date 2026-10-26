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
        # Q2: every control call is recorded as ``(name, args...)``. If
        # ``reply`` is set it is delivered to the reply handler during the
        # call (simulating the printer's MQTT answer); ``raise_on`` makes
        # the named call raise.
        self.calls: list[tuple] = []
        self.reply: dict | None = None
        self.raise_on: dict[str, Exception] = {}
        self.connected = False
        self.closed = False
        self.full_status_requests = 0
        self.paused = self.resumed = self.stopped = 0
        self.uploaded: list[PrintSpec] = []
        self.hms_actions: list[tuple] = []
        self.files_uploaded: list[tuple[str, int]] = []
        self._handler: ReportHandler | None = None
        self.files: dict[str, bytes] = {}
        self.file_mtimes: dict[str, datetime] = {}
        self.download_errors = 0
        self.downloads: list[str] = []
        self.camera: FakeCamera | None = None
        self.storage: int | None = None  # total bytes for storage_info()

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
        prefix = "/" if parent == "/" else parent + "/"
        dirs = {
            k[len(prefix) :].split("/", 1)[0]
            for k in self.files
            if k.startswith(prefix) and "/" in k[len(prefix) :]
        }
        out.extend(RemoteFile(name=d, size=None, mtime=None, is_dir=True) for d in sorted(dirs))
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

    def execute_hms_action(self, print_error: str, action: str, job_id: str | None = None) -> None:
        self.hms_actions.append((print_error, action, job_id))

    def upload_file(self, remote: str, local) -> None:
        self.files_uploaded.append((remote, local.stat().st_size))

    def upload_and_start(self, spec: PrintSpec) -> None:
        self.uploaded.append(spec)

    def _record(self, name: str, *args) -> None:
        self.calls.append((name, *args))
        if name in self.raise_on:
            raise self.raise_on[name]
        if self.reply is not None and self._reply_handler is not None:
            self._reply_handler(dict(self.reply))

    def pause(self) -> None:
        self.paused += 1
        self._record("pause")

    def resume(self) -> None:
        self.resumed += 1
        self._record("resume")

    def stop(self) -> None:
        self.stopped += 1
        self._record("stop")

    def set_temperature(self, target: str, value: int, extruder: int | None = None) -> None:
        if extruder is None:
            self._record("set_temperature", target, value)
        else:
            self._record("set_temperature", target, value, extruder)

    def select_extruder(self, extruder: int) -> None:
        self._record("select_extruder", extruder)

    def set_fan(self, fan: str, pct: int) -> None:
        self._record("set_fan", fan, pct)

    def set_light(self, on: bool) -> None:
        self._record("set_light", on)

    def set_speed(self, level: int) -> None:
        self._record("set_speed", level)

    def home(self) -> None:
        self._record("home")

    def jog(self, axis: str, mm: float) -> None:
        self._record("jog", axis, mm)

    def extrude(self, mm: float) -> None:
        self._record("extrude", mm)

    def send_gcode(self, gcode: str) -> None:
        self._record("send_gcode", gcode)

    def skip_objects(self, object_ids: list[int]) -> None:
        self._record("skip_objects", list(object_ids))

    def load_filament(self, tray: int) -> None:
        self._record("load_filament", tray)

    def unload_filament(self) -> None:
        self._record("unload_filament")

    def calibrate(self, bed_leveling: bool, vibration: bool, motor_noise: bool) -> None:
        self._record("calibrate", bed_leveling, vibration, motor_noise)

    def set_airduct(self, mode: str) -> None:
        self._record("set_airduct", mode)

    def clear_hms(self) -> None:
        self._record("clear_hms")

    def send_devmode_probe(self, slot: dict, seq: str) -> None:
        self.calls.append(("devmode_probe", dict(slot), seq))

    def set_xcam(self, module: str, enable: bool, sensitivity: str) -> None:
        self._record("set_xcam", module, enable, sensitivity)

    def set_print_option(self, option: str, value: bool) -> None:
        self._record("set_print_option", option, value)

    def ams_slot_configure(
        self,
        ams_id: int,
        tray_id: int,
        tray_info_idx: str,
        tray_type: str,
        sub_brand: str,
        color: str,
        nozzle_min: int,
        nozzle_max: int,
    ) -> None:
        self._record(
            "ams_slot_configure",
            ams_id,
            tray_id,
            tray_info_idx,
            tray_type,
            sub_brand,
            color,
            nozzle_min,
            nozzle_max,
        )

    def ams_slot_reset(self, ams_id: int, tray_id: int) -> None:
        self._record("ams_slot_reset", ams_id, tray_id)

    def drying_start(
        self, ams_id: int, temp: int, duration_hours: int, filament: str, rotate_tray: bool
    ) -> None:
        self._record("drying_start", ams_id, temp, duration_hours, filament, rotate_tray)

    def drying_stop(self, ams_id: int) -> None:
        self._record("drying_stop", ams_id)

    def start_sd_print(self, path: str, plate: int | None, use_ams: bool) -> None:
        self._record("start_sd_print", path, plate, use_ams)

    def kprofile_list(self, nozzle_diameter: str, extruder_id: int | None = None) -> None:
        self._record("kprofile_list", nozzle_diameter, extruder_id)

    def kprofile_set(self, entry: dict) -> None:
        self._record("kprofile_set", entry)

    def kprofile_delete(self, entry: dict) -> None:
        self._record("kprofile_delete", entry)

    def kprofile_select(
        self, ams_id: int, tray_id: int, cali_idx: int, filament_id: str, nozzle_diameter: str
    ) -> None:
        self._record("kprofile_select", ams_id, tray_id, cali_idx, filament_id, nozzle_diameter)

    def delete_file(self, path: str) -> None:
        self.calls.append(("delete_file", path))
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]

    def storage_info(self) -> dict | None:
        self.calls.append(("storage_info",))
        if self.storage is None:
            return None
        used = sum(len(v) for v in self.files.values())
        return {"total": self.storage, "used": used, "free": self.storage - used, "source": "fake"}

    def emit_reply(self, reply: dict) -> None:
        assert self._reply_handler is not None
        self._reply_handler(reply)

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
