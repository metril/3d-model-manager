"""T4 print batches: a named run of N queue entries."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class _SchedulingIn(BaseModel):
    """Scheduling fields applied to every entry the batch creates."""

    scheduled_at: datetime | None = None
    manual_start: bool = False
    require_previous_success: bool = False
    auto_off_after: bool = False
    preheat_override: Literal["on", "off"] | None = None
    preheat_chamber_target: int | None = Field(default=None, ge=0, le=65)


def _check_plates(v: dict[str, int] | None) -> dict[str, int] | None:
    if v is None:
        return v
    for k, q in v.items():
        if not k.isdigit() or int(k) < 1:
            raise ValueError("plate_targets keys must be plate numbers >= 1")
        if q < 1:
            raise ValueError("plate_targets quantities must be >= 1")
    return v


class BatchIn(_SchedulingIn):
    name: str = Field(min_length=1, max_length=200)
    model_id: int | None = None
    file_id: int | None = None
    recipe_id: int | None = None
    printer_id: int | None = None
    quantity: int = Field(ge=1, le=500)
    due_date: date | None = None
    notes: str = Field(default="", max_length=2000)
    plate_targets: dict[str, int] = Field(default_factory=dict)
    cost_center_id: int | None = None

    _plates = field_validator("plate_targets")(_check_plates)


class BatchPatch(BaseModel):
    """Only keys present are applied. Structural fields (``quantity``,
    ``plate_targets``, ``recipe_id``, ``printer_id``) require ``draft``."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    quantity: int | None = Field(default=None, ge=1, le=500)
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)
    plate_targets: dict[str, int] | None = None
    recipe_id: int | None = None
    printer_id: int | None = None

    _plates = field_validator("plate_targets")(_check_plates)


class BatchProgress(BaseModel):
    total: int = 0
    done: int = 0
    failed: int = 0
    skipped: int = 0
    waiting: int = 0


class BatchOut(BaseModel):
    id: int
    name: str
    model_id: int | None = None
    file_id: int | None = None
    recipe_id: int | None = None
    printer_id: int | None = None
    quantity: int
    status: Literal["draft", "active", "done", "cancelled"]
    due_date: date | None = None
    notes: str = ""
    plate_targets: dict[str, int] = Field(default_factory=dict)
    cost_center_id: int | None = None
    created_by_user_id: int | None = None
    created_at: datetime
    progress: BatchProgress = Field(default_factory=BatchProgress)
