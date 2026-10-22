"""Spool inventory API shapes (P5 spool tray plan, Task 6): physical spools,
their current AMS slot and usage ledger.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, field_validator

if TYPE_CHECKING:
    from app.models import Spool

from app.services.spool_inventory import is_low_stock, remaining_g

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _validate_hex(value: str | None) -> str | None:
    if value is not None and not _HEX_RE.match(value):
        raise ValueError("color_hex must be #RRGGBB")
    return value


def _non_negative(value: float | None) -> float | None:
    if value is not None and value < 0:
        raise ValueError("must be >= 0")
    return value


class SpoolSlotOut(BaseModel):
    printer_id: int
    ams_unit: int
    slot: int


class SpoolCreate(BaseModel):
    material_id: int | None = None
    material_type: str
    brand: str | None = None
    color_hex: str | None = None
    color_name: str | None = None
    label_weight_g: float = 1000
    empty_weight_g: float | None = None
    weight_used_g: float = 0
    cost_per_kg: float | None = None
    location: str | None = None
    low_stock_g: float = 100
    tray_uuid: str | None = None

    _hex = field_validator("color_hex")(_validate_hex)
    _nn = field_validator(
        "label_weight_g", "empty_weight_g", "weight_used_g", "cost_per_kg", "low_stock_g"
    )(_non_negative)


class SpoolUpdate(BaseModel):
    material_id: int | None = None
    material_type: str | None = None
    brand: str | None = None
    color_hex: str | None = None
    color_name: str | None = None
    label_weight_g: float | None = None
    empty_weight_g: float | None = None
    weight_used_g: float | None = None
    cost_per_kg: float | None = None
    location: str | None = None
    low_stock_g: float | None = None
    tray_uuid: str | None = None
    archived: bool | None = None

    _hex = field_validator("color_hex")(_validate_hex)
    _nn = field_validator(
        "label_weight_g", "empty_weight_g", "weight_used_g", "cost_per_kg", "low_stock_g"
    )(_non_negative)


class SpoolOut(BaseModel):
    id: int
    material_id: int | None
    material_type: str
    brand: str | None
    color_hex: str | None
    color_name: str | None
    label_weight_g: float
    empty_weight_g: float | None
    weight_used_g: float
    cost_per_kg: float | None
    location: str | None
    low_stock_g: float
    tray_uuid: str | None
    archived_at: datetime | None
    last_used_at: datetime | None
    created_by_user_id: int | None
    created_at: datetime
    remaining_g: float
    low_stock: bool
    slot: SpoolSlotOut | None = None

    @classmethod
    def from_model(cls, s: Spool, slot: SpoolSlotOut | None = None) -> SpoolOut:
        return cls(
            id=s.id,
            material_id=s.material_id,
            material_type=s.material_type,
            brand=s.brand,
            color_hex=s.color_hex,
            color_name=s.color_name,
            label_weight_g=s.label_weight_g,
            empty_weight_g=s.empty_weight_g,
            weight_used_g=s.weight_used_g,
            cost_per_kg=s.cost_per_kg,
            location=s.location,
            low_stock_g=s.low_stock_g,
            tray_uuid=s.tray_uuid,
            archived_at=s.archived_at,
            last_used_at=s.last_used_at,
            created_by_user_id=s.created_by_user_id,
            created_at=s.created_at,
            remaining_g=remaining_g(s),
            low_stock=is_low_stock(s),
            slot=slot,
        )


class UsageRowOut(BaseModel):
    id: int
    print_job_id: int | None
    archive_id: int | None = None
    model_name: str | None = None
    grams: float
    source: str
    created_at: datetime


class SeriesPoint(BaseModel):
    t: datetime
    remaining_g: float


class SpoolDetailOut(SpoolOut):
    usage: list[UsageRowOut]
    series: list[SeriesPoint]


class ManualUsageIn(BaseModel):
    grams: float

    @field_validator("grams")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("grams must be > 0")
        return v


class SlotAssignIn(BaseModel):
    spool_id: int | None = None
