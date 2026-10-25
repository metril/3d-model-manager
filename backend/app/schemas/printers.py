"""Schemas for the printers CRUD + probe/status/print API (SPEC "API
surface"; M4). ``PrinterOut`` NEVER carries ``access_code``/
``access_code_enc`` -- only ``access_code_set: bool`` (Global Constraints:
the decrypted access code must never appear in an API response). Encryption
happens in ``app.api.printers`` via ``app.crypto.encrypt_secret``; decryption
happens ONLY inside the test-probe path via
``app.printers.connection.connection_from_printer``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from app.models.enums import PrinterKind, PrintJobOrigin
from app.printers.camera_select import redact_options

if TYPE_CHECKING:
    from app.models import Printer, PrintJob

from app.schemas.archives import ArchiveOut
from app.schemas.queue import QueueEntryOut


def _clean_serial(value: str) -> str:
    """Shared body for both schemas' validators (Round 8 T1): strip, reject
    blank, and enforce a LENIENT alnum/6-24-char shape. Deliberately not the
    A1's specific 15-char format -- the ``/printers/{id}/test`` probe's TLS
    cert cross-match (``app.printers.probe``) is the authoritative check;
    this only keeps obviously-wrong input (blank, punctuation, a pasted URL)
    out of the DB.
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError("serial is required")
    if not stripped.isalnum() or not (6 <= len(stripped) <= 24):
        raise ValueError("serial must be 6-24 alphanumeric characters")
    return stripped


class BuildVolumeMm(BaseModel):
    """`{x, y, z}` mm build plate dimensions (R13c) -- used by
    `GcodePreview`'s build-plate render."""

    x: float
    y: float
    z: float


# R13c: seeded onto `Printer.build_volume_mm` on create when `model` matches
# a known key (case-insensitive substring match -- see
# `seed_build_volume_mm`); left `None` for anything else, editable
# afterward. Bambu's A1/A1 mini share a printer `kind` but differ in bed
# size, hence the separate "a1 mini" vs "a1" entries (mini matched first).
_KNOWN_BUILD_VOLUMES_MM: dict[str, BuildVolumeMm] = {
    "a1 mini": BuildVolumeMm(x=180, y=180, z=180),
    "a1": BuildVolumeMm(x=256, y=256, z=256),
    "p1s": BuildVolumeMm(x=256, y=256, z=256),
    "x1c": BuildVolumeMm(x=256, y=256, z=256),
    "mk4": BuildVolumeMm(x=250, y=210, z=220),
}


def seed_build_volume_mm(model: str | None) -> dict[str, float] | None:
    """Best-effort seed for a new printer's ``build_volume_mm`` from its
    free-text ``model`` field (Round 8 T1: ``Printer`` has no structured
    model enum). Matches the longest/most specific key first (``"a1 mini"``
    before ``"a1"``) so an A1 mini printer doesn't get the plain A1's
    larger bed. Returns ``None`` -- left for the user to fill in -- when
    nothing matches.
    """
    if not model:
        return None
    lowered = model.strip().lower()
    for key in sorted(_KNOWN_BUILD_VOLUMES_MM, key=len, reverse=True):
        if key in lowered:
            return _KNOWN_BUILD_VOLUMES_MM[key].model_dump()
    return None


class PrinterCreate(BaseModel):
    name: str
    kind: PrinterKind = PrinterKind.BAMBU_LAN
    host: str
    serial: str
    access_code: str
    model: str | None = None
    enabled: bool = True
    options: dict = Field(default_factory=dict)
    # R13c: explicit value wins over the `model`-based seed (see
    # `app.api.printers.create_printer`).
    build_volume_mm: BuildVolumeMm | None = None

    @field_validator("serial")
    @classmethod
    def _validate_serial(cls, value: str) -> str:
        return _clean_serial(value)


class PrinterUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    serial: str | None = None
    access_code: str | None = None
    model: str | None = None
    enabled: bool | None = None
    power_off_when_drained: bool | None = None
    options: dict | None = None
    build_volume_mm: BuildVolumeMm | None = None

    @field_validator("serial")
    @classmethod
    def _validate_serial(cls, value: str | None) -> str:
        """An *absent* ``serial`` is fine (``exclude_unset`` drops it before
        it reaches the service layer, same PATCH pattern as
        ``PrintPatchIn._reject_explicit_null``) -- but an explicit ``null``
        or blank string is a real user mistake (clearing a NOT-NULL column),
        so both raise here rather than sailing through as a 500.
        """
        if value is None:
            raise ValueError("serial cannot be null")
        return _clean_serial(value)


class CapabilitiesOut(BaseModel):
    model_key: str | None = None
    chamber_heater: bool = False
    chamber_temp: bool = False
    airduct: bool = False
    door: bool = False
    dual_nozzle: bool = False
    aux_fan: bool = False
    chamber_fan: bool = False
    camera: str = "none"
    lidar: bool = False
    max_bed: int | None = None
    max_nozzle: int | None = None
    ams_drying: bool = False
    drying_screen_only: bool = False
    ams_drying_while_printing: bool = False
    xcam_modules: list[str] = Field(default_factory=list)
    sd_browser: bool = False
    sd_print: bool = False
    verified: bool = False


class PrinterOut(BaseModel):
    id: int
    name: str
    kind: str
    host: str
    serial: str
    model: str | None
    enabled: bool
    power_off_when_drained: bool = False
    options: dict
    access_code_set: bool  # NEVER the code or ciphertext
    build_volume_mm: BuildVolumeMm | None = None
    capabilities: CapabilitiesOut = Field(default_factory=CapabilitiesOut)
    # Sum of archived run durations (hours); filled by the overview builder,
    # ``None`` where not computed.
    total_print_hours: float | None = None
    maintenance_mode: bool = False
    awaiting_plate_clear: bool = False

    @classmethod
    def from_model(cls, p: Printer) -> PrinterOut:
        from app.printers.capabilities import capabilities_for

        return cls(
            id=p.id,
            name=p.name,
            kind=p.kind,
            host=p.host,
            serial=p.serial,
            model=p.model,
            enabled=p.enabled,
            power_off_when_drained=bool(getattr(p, "power_off_when_drained", False)),
            maintenance_mode=bool(getattr(p, "maintenance_mode", False)),
            awaiting_plate_clear=bool(getattr(p, "awaiting_plate_clear", False)),
            options=redact_options(p.options),
            access_code_set=bool(p.access_code_enc),
            build_volume_mm=p.build_volume_mm,
            capabilities=CapabilitiesOut(**capabilities_for(p.model, p.serial).as_dict()),
        )


class ProbeOut(BaseModel):
    ok: bool
    detail: str
    gcode_state: str | None = None


class DetectSerialIn(BaseModel):
    """``POST /printers/detect-serial`` payload (Round 8 T1): reads the
    serial straight off the printer's TLS cert (``app.printers.discovery``)
    so the user doesn't have to hunt for it on the printer's screen. No
    access code -- this is a plain TLS handshake, not an MQTT login."""

    host: str
    port: int = 8883


class DetectSerialOut(BaseModel):
    serial: str | None = None
    detail: str


class SpoolBriefOut(BaseModel):
    """Minimal spool identity for an AMS tray chip (P5 Task 6 fills this
    in; declared now so ``AmsTrayOut`` has somewhere to put it). ``low_stock``
    (final-review fix wave item 8) is computed server-side -- a card/dialog
    rendering a tray chip no longer needs its own separate low-stock spool
    lookup to badge it."""

    id: int
    material_type: str
    brand: str | None = None
    color_hex: str | None = None
    color_name: str | None = None
    low_stock: bool = False

    @classmethod
    def from_model(cls, s) -> SpoolBriefOut:
        from app.services.spool_inventory import is_low_stock

        return cls(
            id=s.id,
            material_type=s.material_type,
            brand=s.brand,
            color_hex=s.color_hex,
            color_name=s.color_name,
            low_stock=is_low_stock(s),
        )


class AmsTrayOut(BaseModel):
    """One AMS filament slot, loaded or empty (M8 G3 color sync + P5 spool
    tray plan). ``color`` is ``#RRGGBB`` (alpha already stripped) or None
    for an empty/unknown slot. ``slot`` is the global tray id (unchanged
    meaning); ``ams_unit``/``tray`` are the per-unit id/tray-within-unit
    reported by the printer. ``tray_uuid`` is the RFID tag read off a spool
    (None if empty or untagged); ``remain_pct`` is the printer's own
    remaining-material estimate."""

    slot: int
    ams_unit: int | None = None
    tray: int | None = None
    color: str | None = None
    material: str | None = None
    tray_type: str | None = None
    tray_uuid: str | None = None
    remain_pct: int | None = None
    # T2: the printer's selected K-profile (-1 = default) and its K value.
    cali_idx: int | None = None
    k_value: float | None = None
    tray_info_idx: str | None = None
    spool: SpoolBriefOut | None = None


class HmsEntryOut(BaseModel):
    attr: int
    code: int


class AmsModuleFwOut(BaseModel):
    id: int
    sw_ver: str | None = None


class FirmwareOut(BaseModel):
    ota: str | None = None
    ams: list[AmsModuleFwOut] = Field(default_factory=list)


class AmsUnitOut(BaseModel):
    id: int
    humidity: float | None = None  # relative humidity %, never the 1-5 index
    temp: float | None = None
    serial: str | None = None
    fw: str | None = None
    label: str | None = None  # T3: user label (ams_labels), merged by the status route
    # T1 drying (info bits + adapter-cached cycle target; printer never echoes it).
    dry_time_min: int | None = None
    dry_status: int | None = None  # 0 off,1 checking,2 drying,3 cooling,4 stopping,5 error
    dry_sub_status: int | None = None
    dry_sf_reason: list[int] = Field(default_factory=list)
    humidity_index: int | None = None  # raw 1-5 drop index (inverted vs %), display only
    dry_target_temp: int | None = None
    dry_filament: str | None = None
    dry_started_at: float | None = None


class PrinterStatusOut(BaseModel):
    online: bool
    gcode_state: str | None = None
    mc_percent: int | None = None
    layer_num: int | None = None
    total_layer_num: int | None = None
    mc_remaining_time: int | None = None
    print_error: int | None = None
    nozzle_temper: float | None = None
    bed_temper: float | None = None
    subtask_name: str | None = None
    wifi_signal: str | None = None
    trays: list[AmsTrayOut] = Field(default_factory=list)
    # Q1 status schema v2 (all optional; fan values are percent).
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
    hms: list[HmsEntryOut] = Field(default_factory=list)
    sdcard: bool | None = None
    nozzle_type: str | None = None
    firmware: FirmwareOut | None = None
    ams_units: list[AmsUnitOut] = Field(default_factory=list)
    ext_tray: AmsTrayOut | None = None
    print_thumbnail_key: str | None = None
    ipcam: bool | None = None
    dev_mode: bool | None = None
    dev_mode_reason: str | None = None
    last_report_at: str | None = None
    tray_now: int | None = None
    door_open: bool | None = None
    print_options: dict = Field(default_factory=dict)
    # T3: from the DB (printers.awaiting_plate_clear), overlaid by the status route.
    awaiting_plate_clear: bool = False
    # T1: firmware-aware capabilities (set by the status route; None offline).
    capabilities: CapabilitiesOut | None = None


# Everything in PrinterStatusOut that is copied verbatim from the Redis state.
STATE_FIELDS: tuple[str, ...] = tuple(
    k
    for k in PrinterStatusOut.model_fields
    if k not in ("online", "trays", "awaiting_plate_clear", "capabilities")
)


def status_from_state(state: dict) -> PrinterStatusOut:
    """Online ``PrinterStatusOut`` from a printerd Redis state dict. Absent or
    null list fields (state written before they existed) become ``[]``."""
    data = {k: state.get(k) for k in STATE_FIELDS}
    for k in ("hms", "ams_units"):
        data[k] = data[k] or []
    data["print_options"] = data["print_options"] or {}
    return PrinterStatusOut(online=True, trays=state.get("trays") or [], **data)


class PrintRequest(BaseModel):
    file_id: int
    plate: int = 1
    subtask_name: str | None = None
    use_ams: bool = False
    ams_mapping: list[int] = Field(default_factory=lambda: [0])
    bed_levelling: bool = True
    flow_cali: bool = True
    timelapse: bool = False


class PrintJobOut(BaseModel):
    id: int
    printer_id: int
    file_id: int | None
    origin: PrintJobOrigin
    subtask_name: str | None
    state: str
    progress_pct: float | None
    remaining_min: int | None
    layer: int | None
    total_layers: int | None
    printer_error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_model(cls, j: PrintJob) -> PrintJobOut:
        return cls(
            id=j.id,
            printer_id=j.printer_id,
            file_id=j.file_id,
            origin=j.origin,
            subtask_name=j.subtask_name,
            state=j.state,
            progress_pct=j.progress_pct,
            remaining_min=j.remaining_min,
            layer=j.layer,
            total_layers=j.total_layers,
            printer_error=j.printer_error,
            created_at=j.created_at,
            started_at=j.started_at,
            finished_at=j.finished_at,
        )


class ActiveJobOut(PrintJobOut):
    """A ``PrintJobOut`` resolved onto its file's model (P3 Task 1), for the
    printers-overview dashboard. All three fields are ``None`` when the job
    has no ``file_id`` (an ``external`` job) or the file's revision/model
    was since deleted.
    """

    plate: int | None = None
    model_id: int | None = None
    model_slug: str | None = None
    model_name: str | None = None
    # Q12: the print's plate thumbnail (`/api/blobs/<hash>/plates/<n>/thumb`)
    # when the job's file is a stored 3MF; else None.
    thumbnail_url: str | None = None


class PrinterOverviewItem(BaseModel):
    printer: PrinterOut
    status: PrinterStatusOut
    active_job: ActiveJobOut | None = None
    last_runs: list[ArchiveOut] = Field(default_factory=list)
    can_print: bool
    can_control: bool
    # P6 Task 8: up to 3 non-terminal queue entries targeting THIS printer
    # (``printer_id == id`` or, once dispatched, ``assigned_printer_id ==
    # id``) -- the top-level ``PrintersOverviewOut.up_next`` now only holds
    # unassigned entries.
    up_next: list[QueueEntryOut] = Field(default_factory=list)
    # P8 Task 7: this printer's currently-firing HA alert, if any
    # (``app.services.ha_state.read_alert``) -- ``ha_held`` is true iff that
    # alert is ALSO currently holding the dispatcher off this printer
    # (``hold_queue=true`` on the firing link), distinct from merely being
    # reported.
    ha_alert: str | None = None
    ha_held: bool = False


class PrintersOverviewOut(BaseModel):
    printers: list[PrinterOverviewItem]
    up_next: list[QueueEntryOut]
    recent_runs: list[ArchiveOut]


class CameraUrlsOut(BaseModel):
    """``POST /printers/{id}/camera/token`` response (P4 Task 4): relative
    URLs (no origin) carrying a scoped camera token in the query string, so
    the browser hits the same origin it's already on for the stream/
    snapshot ``<img>``/``<video>`` tags."""

    stream_url: str
    snapshot_url: str
    expires_at: datetime


class CameraStatusOut(BaseModel):
    status: str
    source: str  # external | a1 | rtsp | none
    beta: bool
    rotation: int
    default_fps: int


class OverlayTokenIn(BaseModel):
    label: str | None = None


class OverlayTokenOut(BaseModel):
    id: int
    printer_id: int
    label: str | None
    created_at: datetime
    revoked_at: datetime | None


class OverlayTokenCreatedOut(OverlayTokenOut):
    token: str  # plaintext, shown once
    overlay_url: str


class OverlayOut(BaseModel):
    printer_name: str
    gcode_state: str | None
    progress: int | None
    layer: int | None
    total_layers: int | None
    remaining_min: int | None
    nozzle_temp: float | None
    bed_temp: float | None
    subtask_name: str | None
    rotation: int
    stream_url: str
    snapshot_url: str
