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
