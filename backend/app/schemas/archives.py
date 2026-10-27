"""Print archive API shapes (P2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.enums import (
    ArchiveFetchState,
    ArchiveResult,
    ArchiveVerdict,
    PrintJobOrigin,
    VerdictSource,
)
from app.schemas.library import TagRef
from app.schemas.prints import PrintOut


class PhotoOut(BaseModel):
    filename: str
    kind: Literal["photo", "timelapse"]
    uploaded_by: int | None = None
    at: str | None = None


class ArchiveOut(BaseModel):
    id: int
    print_job_id: int
    printer_id: int
    printer_name: str
    origin: PrintJobOrigin
    subtask_name: str | None
    model_id: int | None
    model_slug: str | None
    model_name: str | None
    revision_id: int | None
    result: ArchiveResult
    duration_s: int | None
    est_filament: list | None
    plate: int | None
    fetch_state: ArchiveFetchState
    fetch_error: str | None
    has_thumbnail: bool
    has_file: bool
    has_snapshot: bool
    has_timelapse: bool
    media_note: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    created_by_user_id: int | None
    run: PrintOut | None = None
    # T7
    recorded_result: ArchiveResult
    result_override: ArchiveResult | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    tag_refs: list[TagRef] = []
    confirm_requested: bool = False
    confirm_expires_at: datetime | None = None
    user_verdict: ArchiveVerdict | None = None
    user_verdict_source: VerdictSource | None = None
    verdict_at: datetime | None = None
    verdict_by_user_id: int | None = None
    photos: list[PhotoOut] = Field(default_factory=list)
    has_source: bool = False
    source_name: str | None = None
    source_linked: bool = False
    has_f3d: bool = False
    f3d_name: str | None = None


class ArchivePatchIn(BaseModel):
    notes: str | None = Field(None, max_length=10000)
    tags: list[str] | None = None
    result_override: ArchiveResult | None = None


class VerdictIn(BaseModel):
    verdict: ArchiveVerdict
    source: Literal["dialog", "card", "api"] = "dialog"


class SettingsDiffOut(BaseModel):
    key: str
    values: dict[str, Any]


class TimeOut(BaseModel):
    estimated_s: float | None = None
    actual_s: int | None = None


class CorrelationOut(BaseModel):
    key: str
    value: Any
    success_rate: float
    n: int


class CompareOut(BaseModel):
    archives: list[ArchiveOut]
    settings_diff: list[SettingsDiffOut]
    filament: dict[str, Any]
    time: dict[str, TimeOut]
    correlation: list[CorrelationOut]


class PurgeCountOut(BaseModel):
    count: int
    bytes: int


class PurgeRunOut(BaseModel):
    deleted: int
    bytes: int


class PurgeStatsOut(BaseModel):
    last_run_at: str | None
    last_deleted: int
    last_bytes: int
    next_run: str | None


class CalendarDayOut(BaseModel):
    date: str
    count: int
    ok: int
    failed: int
    hours: float


class TimelapseInfoOut(BaseModel):
    duration_s: float
    fps: float
    width: int
    height: int
    bytes: int


class TimelapseThumbsOut(BaseModel):
    frames: list[str]


class TimelapseJobOut(BaseModel):
    job_id: str


class TimelapseJobStateOut(BaseModel):
    state: Literal["pending", "running", "done", "error"]
    error: str | None = None
    filename: str | None = None
