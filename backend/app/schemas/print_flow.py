"""Print plan / send API shapes (P5 spool tray plan, Task 7)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.library import PlateOut
from app.schemas.printers import PrintRequest, SpoolBriefOut
from app.schemas.queue import PlanOptions  # re-exported -- see queue.py's docstring


class PlanIn(BaseModel):
    file_id: int
    plate: int | None = None
    printer_id: int | None = None
    overrides: dict[int, int] = Field(default_factory=dict)


class PlanFilament(BaseModel):
    filament_id: int
    type: str | None
    color: str | None
    grams: float | None
    tray: int | None
    status: str
    delta_e: float | None
    spool: SpoolBriefOut | None = None


class PlanPrinter(BaseModel):
    id: int
    name: str
    model: str | None
    idle: bool
    compatible: bool
    reason: str | None = None


class PlanOut(BaseModel):
    file_id: int
    model_id: int | None
    plate: int | None
    plates: list[PlateOut]
    printer_id: int | None
    printers: list[PlanPrinter]
    filaments: list[PlanFilament]
    ams_mapping: list[int]
    use_ams: bool
    est_duration_s: int | None
    est_grams: float | None
    est_cost: float | None
    warnings: list[str]
    options: PlanOptions = Field(default_factory=PlanOptions)


class SendIn(PrintRequest):
    printer_id: int
