"""Schemas for the dashboard stats API (R11-B item 13: ``GET /stats``)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.library import ModelSummary
from app.schemas.prints import PrintOut


class ModelsStats(BaseModel):
    total: int
    favorites: int
    archived: int
    drafts: int


class FilesStats(BaseModel):
    total: int
    bytes_total: int
    bytes_by_backend: dict[str, int]
    by_format: dict[str, int]


class PrintsStats(BaseModel):
    total: int
    succeeded: int
    failed: int
    filament_g_total: float
    duration_s_total: int
    cost_total: float


class RecentStats(BaseModel):
    models_added_7d: int
    prints_7d: int


class JobsStats(BaseModel):
    running: int
    queued: int
    failed_24h: int


class MaterialUsageOut(BaseModel):
    """One row of the dashboard's material-usage breakdown (R13c) --
    grouped by ``material_id`` where a print resolved to a catalog
    ``Material``, falling back to the free-text ``Print.filament`` name
    otherwise (``material_id: null`` in that case)."""

    material_id: int | None
    name: str
    grams: float
    prints: int


class FilamentPointOut(BaseModel):
    """One bucket of a ``FilamentSeriesOut`` (P5 Task 8: ``GET
    /stats/filament``)."""

    t: datetime
    grams: float
    cost: float


class FilamentSeriesOut(BaseModel):
    """Grams + cost over time for one material or one spool (P5 Task 8).
    ``key`` is the material id/name or the spool id, stringified -- stable
    across buckets, used by the frontend as the series identity/legend
    key."""

    key: str
    name: str
    color_hex: str | None
    total_g: float
    total_cost: float
    points: list[FilamentPointOut]


class FilamentStatsOut(BaseModel):
    by_material: list[FilamentSeriesOut]
    by_spool: list[FilamentSeriesOut]


class StatsOut(BaseModel):
    models: ModelsStats
    files: FilesStats
    tags: int
    collections: int
    prints: PrintsStats
    recent: RecentStats
    jobs: JobsStats
    # R13c: dashboard additions.
    recent_models: list[ModelSummary] = []
    recent_prints: list[PrintOut] = []
    material_usage: list[MaterialUsageOut] = []


# ---------------------------------------------------------------------------
# Q8b stats widgets (all windowed by ``days`` and visibility-filtered)
# ---------------------------------------------------------------------------


class SuccessRateOut(BaseModel):
    total: int
    completed: int
    failed: int
    canceled: int
    stopped: int
    success_rate: float | None  # completed / total, None when total == 0


class TimeAccuracyPointOut(BaseModel):
    archive_id: int
    printer_id: int
    estimated_s: int
    actual_s: int
    delta_pct: float  # (actual - estimated) / estimated * 100


class TimeAccuracyBucketOut(BaseModel):
    label: str
    count: int


class TimeAccuracyOut(BaseModel):
    count: int
    mean_delta_pct: float | None
    mean_abs_delta_pct: float | None
    distribution: list[TimeAccuracyBucketOut]
    points: list[TimeAccuracyPointOut]  # most recent 200


class FailureCountOut(BaseModel):
    key: str
    count: int


class FailureGroupOut(BaseModel):
    key: str
    name: str | None = None
    failed: int
    total: int
    failure_rate: float


class FailureAnalysisOut(BaseModel):
    total_failed: int
    by_error_code: list[FailureCountOut]
    by_hms: list[FailureCountOut]
    by_printer: list[FailureGroupOut]
    by_material: list[FailureGroupOut]


class HeatmapOut(BaseModel):
    # counts[weekday][hour]; weekday 0 = Monday, hours are UTC.
    counts: list[list[int]]
    max: int


class RecordEntryOut(BaseModel):
    archive_id: int
    name: str | None
    value: float
    printer_id: int


class BusiestDayOut(BaseModel):
    date: str
    prints: int


class RecordsOut(BaseModel):
    longest_print: RecordEntryOut | None  # value = seconds
    most_filament: RecordEntryOut | None  # value = grams
    busiest_day: BusiestDayOut | None


class PrinterStatsOut(BaseModel):
    printer_id: int
    name: str
    hours: float
    jobs: int
    completed: int
    success_rate: float | None
    filament_g: float


class FilamentTrendPointOut(BaseModel):
    week: datetime
    material: str
    grams: float


class StatsDashboardOut(BaseModel):
    days: int
    success_rate: SuccessRateOut
    time_accuracy: TimeAccuracyOut
    failures: FailureAnalysisOut
    heatmap: HeatmapOut
    records: RecordsOut
    printers: list[PrinterStatsOut]
    filament_trends: list[FilamentTrendPointOut]
