"""The ``PrinterAdapter`` contract (SPEC "Printer integration"): every
capability printerd and the API need, kind-agnostic. ``printerd`` and the API
only ever speak this ABC -- never a concrete adapter (M4 plan Task 2).

Import-safety: this module must be importable with the printer feature OFF,
so it pulls in stdlib + ``app.models.enums`` only -- no ``bambulabs_api``, no
``paho``. The real ``bambu_lan`` adapter (Task 3) lazy-imports those inside
its own build function.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Protocol

from app.models.enums import PrinterKind, PrintJobState


class CameraSource(Protocol):
    """A connected camera stream (P4 camera plan). Mirrors
    ``CameraA1Client``'s shape -- a context manager whose ``frames()``
    yields JPEG payloads until ``stop`` is set or ``close()`` is called."""

    def __enter__(self) -> CameraSource: ...
    def __exit__(self, *exc: object) -> None: ...
    def frames(self) -> Iterator[bytes]: ...
    def close(self) -> None: ...


ReportHandler = Callable[[dict], None]


@dataclass(frozen=True)
class PrinterConnection:
    """Everything an adapter needs to reach ONE printer. ``access_code`` is
    the DECRYPTED code -- this object is only ever built inside the adapter/
    printerd/probe processes (never in a CRUD read path)."""

    host: str
    serial: str
    access_code: str = field(repr=False)
    model: str | None = None
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PrintSpec:
    """One plate of one ``.gcode.3mf`` to upload + start."""

    source_path: Path  # local temp .gcode.3mf to FTPS-upload
    remote_name: str  # filename under /cache/ on the SD card
    plate: int  # N -> param "Metadata/plate_N.gcode"
    subtask_name: str
    use_ams: bool = False
    ams_mapping: tuple[int, ...] = (0,)
    bed_levelling: bool = True
    flow_cali: bool = True
    timelapse: bool = False


@dataclass(frozen=True)
class RemoteFile:
    """One entry from an FTPS directory listing, with size/mtime (P4 camera
    plan Task 7: timelapse fetch needs to pick the newest candidate).
    ``mtime`` is UTC; ``None`` when a listing line couldn't be parsed."""

    name: str
    size: int | None
    mtime: datetime | None
    is_dir: bool = False


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    detail: str
    gcode_state: str | None = None
    # Q5: where the staged probe stopped -- connect | auth | status | ok.
    stage: str | None = None


@dataclass
class PrinterPublicState:
    """The ONLY printer telemetry that leaves printerd (Redis + status
    endpoint). Never carries the access code."""

    gcode_state: str | None = None
    mc_percent: int | None = None
    layer_num: int | None = None
    total_layer_num: int | None = None
    mc_remaining_time: int | None = None
    print_error: int | None = None
    nozzle_temper: float | None = None
    bed_temper: float | None = None
    # P6 Task 7: the nozzle diameter currently registered to the printer
    # (MQTT `nozzle_diameter`), mm -- the dispatcher's `compatibility_reason`
    # cross-checks this against a sliced plate's required nozzle size.
    # `None` when the adapter/printer hasn't reported one (never a bare
    # `0.0` -- see `app.printers.bambu._nozzle_mm`).
    nozzle_mm: float | None = None
    subtask_name: str | None = None
    wifi_signal: str | None = None
    # AMS filament slots, loaded or empty (M8 G3 + P5 spool tray plan):
    # [{"slot": int, "ams_unit": int, "tray": int, "color": "#RRGGBB"|None,
    # "material": str|None, "tray_type": str|None, "tray_uuid": str|None,
    # "remain_pct": int|None}]. Plain dicts (not a nested dataclass) so
    # `dataclasses.asdict` -> `json.dumps` round-trips them into Redis unchanged.
    trays: list[dict] = field(default_factory=list)
    # Q1 status schema v2 -- all optional; fan values are already percent.
    nozzle_target: float | None = None
    bed_target: float | None = None
    chamber_temper: float | None = None
    chamber_target: float | None = None
    fan_part_pct: int | None = None
    fan_aux_pct: int | None = None
    fan_chamber_pct: int | None = None
    light_on: bool | None = None
    speed_level: int | None = None
    stage: str | None = None
    hms: list[dict] = field(default_factory=list)  # [{"attr": int, "code": int}]
    sdcard: bool | None = None
    nozzle_type: str | None = None
    firmware: dict | None = None  # {"ota": str|None, "ams": [{"id": int, "sw_ver": str}]}
    ams_units: list[dict] = field(default_factory=list)
    ext_tray: dict | None = None
    print_thumbnail_key: str | None = None
    ipcam: bool | None = None
    dev_mode: bool | None = None  # None = unknown (probe lands in Q2)
    dev_mode_reason: str | None = None
    last_report_at: str | None = None  # ISO-8601 UTC
    # Q12: loaded tray (AMS report `tray_now`; 254 = external spool, 255 = none)
    # and the enclosure door (only on models whose capabilities have a door).
    tray_now: int | None = None
    door_open: bool | None = None
    # T3: xcam/print-option state, `{"xcam": {module: {"enable": bool,
    # "sensitivity": str|None}}, "print_halt": bool|None,
    # "auto_recovery": bool|None, "auto_switch_filament": bool|None}`.
    print_options: dict = field(default_factory=dict)


def public_state_from(merged: dict) -> PrinterPublicState:
    """Build a ``PrinterPublicState`` from a snapshot dict by field name
    (missing keys fall back to the dataclass defaults)."""
    names = {f.name for f in fields(PrinterPublicState)}
    return PrinterPublicState(**{k: v for k, v in merged.items() if k in names and v is not None})


def state_key(printer_id: int) -> str:
    return f"printer:{printer_id}:state"


def command_channel(printer_id: int) -> str:
    return f"printer:{printer_id}:commands"


def cmd_result_key(printer_id: int, request_id: str) -> str:
    """Redis key (30 s TTL) printerd writes a command's result JSON to."""
    return f"printer:{printer_id}:cmd_result:{request_id}"


CMD_RESULT_TTL_S = 30

ReplyHandler = Callable[[dict], None]


class PrinterAdapter(ABC):
    kind: ClassVar[PrinterKind]
    # True when the adapter forwards printer command replies (``result`` /
    # ``reason``) to the reply handler, so printerd can wait for one.
    emits_replies: ClassVar[bool] = False

    def __init__(self, conn: PrinterConnection) -> None:
        self.conn = conn
        self._reply_handler: ReplyHandler | None = None
        self._raw_tap: Callable[[str, bytes], None] | None = None

    def set_reply_handler(self, handler: ReplyHandler) -> None:
        self._reply_handler = handler

    def set_raw_tap(self, tap: Callable[[str, bytes], None]) -> None:
        """Receive every raw MQTT message ``(topic, payload)`` (debug feed)."""
        self._raw_tap = tap

    @abstractmethod
    def test_connection(self, *, timeout: float = 10.0) -> ProbeResult: ...
    @abstractmethod
    def connect(self) -> None: ...
    @abstractmethod
    def close(self) -> None: ...
    @abstractmethod
    def set_report_handler(self, handler: ReportHandler) -> None: ...
    @abstractmethod
    def request_full_status(self) -> None: ...  # MQTT pushall
    @abstractmethod
    def upload_and_start(self, spec: PrintSpec) -> None: ...
    @abstractmethod
    def pause(self) -> None: ...
    @abstractmethod
    def resume(self) -> None: ...
    @abstractmethod
    def stop(self) -> None: ...
    @abstractmethod
    def merge_report(self, prev: dict | None, report: dict) -> dict: ...
    @abstractmethod
    def public_state(self, merged: dict) -> PrinterPublicState: ...
    @abstractmethod
    def job_state(self, public: PrinterPublicState) -> PrintJobState | None: ...

    # Optional capabilities (P2 print archive): adapters without file access
    # simply inherit these.
    def list_dir(self, path: str) -> list[str]:
        """Bare file names under ``path`` on the printer."""
        raise NotImplementedError

    def download_file(self, remote: str, dest: Path) -> None:
        """Fetch ``remote`` to ``dest``; raises ``OSError`` on failure."""
        raise NotImplementedError

    def list_files(self, path: str) -> list[RemoteFile]:
        """Directory listing with size/mtime (P4 camera plan Task 7: picking
        the newest timelapse candidate needs more than ``list_dir``'s bare
        names). Adapters without this detail inherit the default and raise
        ``NotImplementedError``."""
        raise NotImplementedError

    def open_camera(self, stop: threading.Event) -> CameraSource:
        """Open a live camera stream (P4 camera plan). ``stop`` is the
        worker's own lifecycle event -- most clients check it inside
        ``frames()``. Adapters without a camera (or an unsupported model)
        raise ``NotImplementedError``."""
        raise NotImplementedError

    # Q2 controls: adapters without a given control inherit these and the
    # worker reports ``unsupported``.
    def set_temperature(self, target: str, value: int) -> None:
        """``target`` is nozzle | bed | chamber; ``value`` in deg C (0 = off)."""
        raise NotImplementedError

    def set_fan(self, fan: str, pct: int) -> None:
        """``fan`` is part | aux | chamber; ``pct`` 0-100."""
        raise NotImplementedError

    def set_light(self, on: bool) -> None:
        raise NotImplementedError

    def set_speed(self, level: int) -> None:
        """1 silent, 2 standard, 3 sport, 4 ludicrous."""
        raise NotImplementedError

    def home(self) -> None:
        raise NotImplementedError

    def jog(self, axis: str, mm: float) -> None:
        """Relative move of ``axis`` (X|Y|Z) by ``mm``."""
        raise NotImplementedError

    def extrude(self, mm: float) -> None:
        raise NotImplementedError

    def send_gcode(self, gcode: str) -> None:
        raise NotImplementedError

    def skip_objects(self, object_ids: list[int]) -> None:
        raise NotImplementedError

    def load_filament(self, tray: int) -> None:
        raise NotImplementedError

    def unload_filament(self) -> None:
        raise NotImplementedError

    def calibrate(self, bed_leveling: bool, vibration: bool, motor_noise: bool) -> None:
        raise NotImplementedError

    def set_airduct(self, mode: str) -> None:
        """``mode`` is cooling | heating."""
        raise NotImplementedError

    def clear_hms(self) -> None:
        raise NotImplementedError

    def send_devmode_probe(self, slot: dict, seq: str) -> None:
        """Publish the Developer-Mode echo probe (decision 2)."""
        raise NotImplementedError

    def execute_hms_action(self, print_error: str, action: str, job_id: str | None = None) -> None:
        """Run a remedy action (resume/stop/ignore/...) for an active fault (Q5)."""
        raise NotImplementedError

    def upload_file(self, remote: str, local: Path) -> None:
        """FTPS-upload ``local`` to ``remote`` on the printer's SD card (Q5)."""
        raise NotImplementedError

    # T3 printer ops.
    def set_xcam(self, module: str, enable: bool, sensitivity: str) -> None:
        """AI-detection module toggle; ``sensitivity`` low|medium|high|never_halt."""
        raise NotImplementedError

    def set_print_option(self, option: str, value: bool) -> None:
        """``option`` is auto_recovery | auto_switch_filament."""
        raise NotImplementedError

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
        """Write filament settings into an AMS/external slot; ``color`` RRGGBBAA."""
        raise NotImplementedError

    def ams_slot_reset(self, ams_id: int, tray_id: int) -> None:
        raise NotImplementedError

    def drying_start(
        self, ams_id: int, temp: int, duration_hours: int, filament: str, rotate_tray: bool
    ) -> None:
        """Start an AMS drying cycle (``duration_hours`` is the wire unit)."""
        raise NotImplementedError

    def drying_stop(self, ams_id: int) -> None:
        raise NotImplementedError

    def start_sd_print(self, path: str, plate: int | None, use_ams: bool) -> None:
        """Start a print of a file already on the printer's SD card."""
        raise NotImplementedError

    def delete_file(self, path: str) -> None:
        """Delete a file on the printer's SD card; raises ``OSError``."""
        raise NotImplementedError

    # -- T2 K-profiles (pressure advance) -------------------------------
    def kprofile_list(self, nozzle_diameter: str, extruder_id: int | None = None) -> None:
        """Request the printer's K-profile table (the reply arrives through the
        reply handler as an ``extrusion_cali_get`` dict)."""
        raise NotImplementedError

    def kprofile_set(self, entry: dict) -> None:
        raise NotImplementedError

    def kprofile_delete(self, entry: dict) -> None:
        raise NotImplementedError

    def kprofile_select(
        self, ams_id: int, tray_id: int, cali_idx: int, filament_id: str, nozzle_diameter: str
    ) -> None:
        raise NotImplementedError

    def storage_info(self) -> dict | None:
        """``{"total": int|None, "free": int|None, "used": int|None, "source": str}``
        or ``None`` when the printer can't tell."""
        raise NotImplementedError
