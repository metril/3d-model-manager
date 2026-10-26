"""Q7 request/response schemas: maintenance, smart plugs, backups, ops settings."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PlugKind = Literal["tasmota", "ha", "mqtt", "rest"]


class MaintenanceTaskIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    interval_hours: float | None = Field(default=None, gt=0)
    interval_days: int | None = Field(default=None, gt=0)
    notes: str | None = None


class MaintenanceTaskPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    interval_hours: float | None = Field(default=None, gt=0)
    interval_days: int | None = Field(default=None, gt=0)
    notes: str | None = None


class MaintenanceDoneIn(BaseModel):
    note: str | None = None


class MaintenanceTaskOut(BaseModel):
    id: int
    printer_id: int
    name: str
    interval_hours: float | None
    interval_days: int | None
    last_done_at: datetime | None
    notes: str | None
    status: Literal["ok", "soon", "overdue"]
    hours_since: float | None
    days_since: int | None
    hours_remaining: float | None
    days_remaining: int | None


class MaintenanceLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    done_at: datetime
    hours_at: float | None
    note: str | None
    created_by_user_id: int | None


class MaintenanceModeIn(BaseModel):
    enabled: bool


class MaintenanceModeOut(BaseModel):
    maintenance_mode: bool


_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class PlugIn(BaseModel):
    name: str = Field(default="", max_length=100)
    printer_id: int | None = None
    controls_printer_power: bool = True
    kind: PlugKind
    config: dict = Field(default_factory=dict)
    # ``None`` = infer from ``auto_off_after_min`` (pre-v4 clients).
    auto_off: bool | None = None
    auto_off_after_min: int | None = Field(default=None, ge=1, le=1440)
    off_delay_mode: Literal["time", "temperature"] = "time"
    off_temp_threshold: int = Field(default=40, ge=20, le=150)
    auto_on_before_print: bool = False
    auto_off_after_drying: bool = False
    drying_off_delay_min: int = Field(default=0, ge=0, le=1440)
    power_alert_enabled: bool = False
    power_alert_high_w: float | None = Field(default=None, ge=0)
    power_alert_low_w: float | None = Field(default=None, ge=0)
    schedule_enabled: bool = False
    schedule_on_time: str | None = Field(default=None, pattern=_HHMM)
    schedule_off_time: str | None = Field(default=None, pattern=_HHMM)
    schedule_tz: str = "UTC"
    show_in_switchbar: bool = False


class PlugOut(BaseModel):
    id: int
    name: str
    printer_id: int | None
    controls_printer_power: bool
    kind: str
    config: dict
    auto_off: bool
    auto_off_after_min: int | None
    off_delay_mode: str
    off_temp_threshold: int
    auto_on_before_print: bool
    auto_off_after_drying: bool
    drying_off_delay_min: int
    power_alert_enabled: bool
    power_alert_high_w: float | None
    power_alert_low_w: float | None
    schedule_enabled: bool
    schedule_on_time: str | None
    schedule_off_time: str | None
    schedule_tz: str
    show_in_switchbar: bool
    energy_kwh: float
    last_state: str | None
    last_power_w: float | None
    last_polled_at: datetime | None
    last_error: str | None


class BackupRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    status: str
    size_bytes: int | None
    location: str | None
    git_pushed: bool
    error: str | None
    created_by_user_id: int | None
    created_at: datetime
    finished_at: datetime | None


class RestoreSettingsOut(BaseModel):
    restored: int


class OpsSettingsIn(BaseModel):
    backup_enabled: bool | None = None
    backup_interval_hours: int | None = Field(default=None, ge=1, le=24 * 90)
    backup_retention: int | None = Field(default=None, ge=1, le=365)
    git_provider: Literal["github", "gitlab", "gitea", "forgejo"] | None = None
    git_base_url: str | None = None
    git_repo: str | None = None
    git_branch: str | None = None
    git_token: str | None = None  # write-only: omitted / "***" keeps, "" clears
    update_check_enabled: bool | None = None


class OpsSettingsOut(BaseModel):
    backup_enabled: bool
    backup_interval_hours: int
    backup_retention: int
    git_provider: str | None
    git_base_url: str | None
    git_repo: str | None
    git_branch: str
    git_token_set: bool
    update_check_enabled: bool


class UpdateOut(BaseModel):
    latest: str | None
    current: str
    url: str | None
    notes: str | None
    update_available: bool
    checked_at: datetime | None
    error: str | None
