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


class PrinterOut(BaseModel):
    id: int
    name: str
    kind: str
    host: str
    serial: str
    model: str | None
    enabled: bool
    options: dict
    access_code_set: bool  # NEVER the code or ciphertext
    build_volume_mm: BuildVolumeMm | None = None

    @classmethod
    def from_model(cls, p: Printer) -> PrinterOut:
        return cls(
            id=p.id,
            name=p.name,
            kind=p.kind,
            host=p.host,
            serial=p.serial,
            model=p.model,
            enabled=p.enabled,
            options=p.options or {},
            access_code_set=bool(p.access_code_enc),
            build_volume_mm=p.build_volume_mm,
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
    in; declared now so ``AmsTrayOut`` has somewhere to put it)."""

    id: int
    material_type: str
    brand: str | None = None
    color_hex: str | None = None
    color_name: str | None = None


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
    spool: SpoolBriefOut | None = None


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


class PrinterOverviewItem(BaseModel):
    printer: PrinterOut
    status: PrinterStatusOut
    active_job: ActiveJobOut | None = None
    last_runs: list[ArchiveOut] = Field(default_factory=list)
    can_print: bool
    can_control: bool


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
