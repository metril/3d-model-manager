"""T5 spool housekeeping API shapes: suppliers, storage locations (+ HA
sensors), colour catalog, bulk ops, CSV import."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")


def _strip_required(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("must not be empty")
    return v


class SupplierIn(BaseModel):
    name: str
    website: str | None = None
    customer_number: str | None = None
    note: str | None = None

    _n = field_validator("name")(_strip_required)


class SupplierPatch(BaseModel):
    name: str | None = None
    website: str | None = None
    customer_number: str | None = None
    note: str | None = None

    @field_validator("name")
    @classmethod
    def _n(cls, v: str | None) -> str | None:
        return None if v is None else _strip_required(v)


class SupplierOut(BaseModel):
    id: int
    name: str
    website: str | None
    customer_number: str | None
    note: str | None
    created_at: datetime
    spool_count: int = 0


SensorKind = Literal["temperature", "humidity", "other"]


class SensorIn(BaseModel):
    name: str
    entity_id: str
    kind: SensorKind = "other"
    device_class: str | None = None
    unit: str | None = None
    alert_above: float | None = None
    alert_below: float | None = None
    notify_on_alert: bool = False
    show_on_card: bool = True
    sort_order: int = 0

    _n = field_validator("name", "entity_id")(_strip_required)


class SensorPatch(BaseModel):
    name: str | None = None
    entity_id: str | None = None
    kind: SensorKind | None = None
    device_class: str | None = None
    unit: str | None = None
    alert_above: float | None = None
    alert_below: float | None = None
    notify_on_alert: bool | None = None
    show_on_card: bool | None = None
    sort_order: int | None = None


class SensorOut(BaseModel):
    id: int
    location_id: int
    name: str
    entity_id: str
    kind: str
    device_class: str | None
    unit: str | None
    alert_above: float | None
    alert_below: float | None
    notify_on_alert: bool
    show_on_card: bool
    sort_order: int
    last_state: str | None
    last_changed_at: datetime | None
    alerting: bool


class ReadingOut(BaseModel):
    sensor_id: int
    name: str
    kind: str
    entity_id: str
    state: str | None
    unit: str | None
    last_changed_at: datetime | None
    alerting: bool


class HaEntityOut(BaseModel):
    entity_id: str
    friendly_name: str | None
    state: str | None
    unit: str | None
    device_class: str | None


class LocationIn(BaseModel):
    name: str
    identifier: str | None = None

    _n = field_validator("name")(_strip_required)


class LocationPatch(BaseModel):
    name: str | None = None
    identifier: str | None = None

    @field_validator("name")
    @classmethod
    def _n(cls, v: str | None) -> str | None:
        return None if v is None else _strip_required(v)


class LocationOut(BaseModel):
    id: int
    name: str
    identifier: str | None
    created_at: datetime
    spool_count: int = 0
    sensors: list[SensorOut] = []


def _hex(v: str | None) -> str | None:
    if v is not None and not _HEX.match(v):
        raise ValueError("hex must be #RRGGBB or #RRGGBBAA")
    return v.upper() if v else v


class CatalogIn(BaseModel):
    manufacturer: str
    color_name: str
    hex: str
    material: str | None = None
    extra_colors: list[str] | None = None
    effect_type: str | None = None

    _n = field_validator("manufacturer", "color_name")(_strip_required)
    _h = field_validator("hex")(_hex)


class CatalogPatch(BaseModel):
    manufacturer: str | None = None
    color_name: str | None = None
    hex: str | None = None
    material: str | None = None
    extra_colors: list[str] | None = None
    effect_type: str | None = None

    _h = field_validator("hex")(_hex)


class CatalogOut(BaseModel):
    id: int
    manufacturer: str
    color_name: str
    hex: str
    material: str | None
    is_default: bool
    extra_colors: list[str] | None
    effect_type: str | None


class CatalogNearestOut(CatalogOut):
    delta_e: float


class BulkUpdateFields(BaseModel):
    model_config = {"extra": "forbid"}

    material_type: str | None = None
    brand: str | None = None
    color_hex: str | None = None
    color_name: str | None = None
    label_weight_g: float | None = None
    empty_weight_g: float | None = None
    weight_used_g: float | None = None
    cost_per_kg: float | None = None
    low_stock_g: float | None = None
    location_id: int | None = None
    archived: bool | None = None
    last_dried_at: datetime | None = None


class BulkUpdateIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    update: BulkUpdateFields


class BulkIdsIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


class BulkItem(BaseModel):
    id: int
    ok: bool
    error: str | None = None


class BulkOut(BaseModel):
    results: list[BulkItem]
    not_found: list[int]


class ImportRow(BaseModel):
    line: int
    action: Literal["create", "update", "error", "skipped"]
    spool_id: int | None = None
    material: str | None = None
    brand: str | None = None
    color_name: str | None = None
    errors: list[str] = []
    warnings: list[str] = []


class ImportSummary(BaseModel):
    create: int = 0
    update: int = 0
    error: int = 0
    skipped: int = 0


class ImportPreviewOut(BaseModel):
    token: str | None
    rows: list[ImportRow]
    summary: ImportSummary
    warnings: list[str] = []


class ImportCommitIn(BaseModel):
    token: str


class ImportResultOut(BaseModel):
    created: int
    updated: int
    errors: int


class LabelsIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=300)
    template: Literal["tight", "roomy"] = "roomy"
    page: Literal["a4", "letter", "roll_62mm"] = "a4"
    format: Literal["pdf", "png"] = "pdf"
    fields: list[Literal["name", "material", "swatch", "weight", "qr", "number"]] = Field(
        default_factory=lambda: ["name", "material", "swatch", "weight", "qr", "number"]
    )
    offset: int = Field(default=0, ge=0, le=299)


class LabelsPreviewIn(BaseModel):
    ids: list[int]
    template: Literal["tight", "roomy"] = "roomy"
    page: Literal["a4", "letter", "roll_62mm"] = "a4"
    fields: list[Literal["name", "material", "swatch", "weight", "qr", "number"]] = Field(
        default_factory=lambda: ["name", "material", "swatch", "weight", "qr", "number"]
    )
    offset: int = Field(default=0, ge=0, le=299)


class LocationLabelsIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=300)
    template: Literal["tight", "roomy"] = "roomy"
    page: Literal["a4", "letter", "roll_62mm"] = "a4"
    format: Literal["pdf", "png"] = "pdf"
    offset: int = Field(default=0, ge=0, le=299)
