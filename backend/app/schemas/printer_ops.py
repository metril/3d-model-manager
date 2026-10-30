"""Q5 schemas: HMS, firmware, diagnostics, discovery."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.printers.filament_ids import generic_filament_id


class HmsFaultOut(BaseModel):
    code: str
    full_code: str
    kind: Literal["hms", "print_error"]
    severity: str
    severity_level: int
    module: str
    description: str | None = None
    wiki_url: str
    actions: list[str] = Field(default_factory=list)


class HmsActionIn(BaseModel):
    print_error: str = Field(min_length=8, max_length=16, pattern=r"^[0-9A-Fa-f]+$")
    action: str = Field(min_length=1, max_length=64)
    job_id: str | None = Field(default=None, max_length=64)


class FirmwareInfoOut(BaseModel):
    model_supported: bool
    current: str | None = None
    latest: str | None = None
    release_notes: str | None = None
    release_time: str | None = None
    checked_at: str | None = None
    download_available: bool = False
    update_available: bool = False
    upload: dict | None = None  # last SD-stage status: {status, version, ...}


class DiagnosticIn(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    serial: str = Field(min_length=1, max_length=64)
    access_code: str = Field(min_length=1, max_length=64)
    model: str | None = None


class DiagnosticCheckOut(BaseModel):
    id: str
    status: Literal["ok", "fail", "skip"]
    hint: str
    detail: str | None = None


class DiagnosticOut(BaseModel):
    ok: bool
    checks: list[DiagnosticCheckOut]


class DiscoverIn(BaseModel):
    cidr: str | None = Field(default=None, max_length=64)


class DiscoveredOut(BaseModel):
    host: str
    serial: str
    model_hint: str | None = None
    source: Literal["ssdp", "sweep"]


# -- T3 printer ops ----------------------------------------------------------


class SlotConfigIn(BaseModel):
    """``PUT /printers/{id}/slots/{unit}/{slot}``. ``spool_id`` keeps its
    legacy meaning (null = clear the assignment); the device-side fields
    (all of ``tray_type``/``color``/``nozzle_min``/``nozzle_max`` required
    together) additionally write the filament profile into the slot."""

    spool_id: int | None = None
    tray_info_idx: str | None = Field(default=None, max_length=32)
    tray_type: str | None = Field(default=None, max_length=32)
    sub_brand: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, max_length=9)
    nozzle_min: int | None = Field(default=None, ge=0, le=350)
    nozzle_max: int | None = Field(default=None, ge=0, le=350)
    apply_device: bool = True

    def device_args(self) -> dict | None:
        """The ``ams_slot_configure`` args, or ``None`` when no device field was sent."""
        keys = ("tray_info_idx", "tray_type", "sub_brand", "color", "nozzle_min", "nozzle_max")
        if all(getattr(self, k) is None for k in keys):
            return None
        return {
            "tray_info_idx": self.tray_info_idx or generic_filament_id(self.tray_type),
            "tray_type": self.tray_type,
            "sub_brand": self.sub_brand or "",
            "color": self.color,
            "nozzle_min": self.nozzle_min,
            "nozzle_max": self.nozzle_max,
        }


class AmsLabelIn(BaseModel):
    label: str = Field(min_length=1, max_length=100)


class AmsLabelOut(BaseModel):
    ams_id: int
    ams_serial: str
    label: str


class SdEntryOut(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int | None = None
    mtime: str | None = None


class SdStorageOut(BaseModel):
    total: int | None = None
    used: int | None = None
    free: int | None = None
    source: str = "unknown"  # avbl | stat | sdcard | unknown
    present: bool | None = None  # SD inserted (state fallback)


class SdPrintIn(BaseModel):
    path: str
    plate: int | None = Field(default=None, ge=1, le=99)
    use_ams: bool = True


class AmsBackupSlotOut(BaseModel):
    ams_id: int
    tray_id: int
    tray_type: str
    color: str | None = None
    tray_info_idx: str | None = None
    sub_brand: str | None = None
    nozzle_min: int | None = None
    nozzle_max: int | None = None
    cali_idx: int | None = None
    k_value: float | None = None


class AmsBackupOut(BaseModel):
    taken_at: str
    slots: list[AmsBackupSlotOut]


class AmsRestoreOut(BaseModel):
    restored: int
    failed: int
    pending: int = 0
