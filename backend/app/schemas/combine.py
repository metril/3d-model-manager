"""``POST /models/{slug}/combine`` payload/response (put several meshes on one plate)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.jobs import JobOut
from app.schemas.library import FileOut


class CombineItemIn(BaseModel):
    file_id: int
    copies: int = Field(default=1, ge=1, le=100)


class BedIn(BaseModel):
    x_mm: float = Field(gt=0, le=2000)
    y_mm: float = Field(gt=0, le=2000)


class CombineIn(BaseModel):
    items: list[CombineItemIn] = Field(min_length=1, max_length=50)
    layout: Literal["grid", "auto"] = "auto"
    spacing_mm: float = Field(default=5.0, ge=0, le=100)
    # Bed: a printer slicer profile's printable area, or explicit dimensions.
    printer_profile_id: int | None = None
    bed: BedIn | None = None
    name: str | None = Field(default=None, max_length=200)
    # Plan only: nothing is written, the placements come back for a preview.
    dry_run: bool = False


class CombinePlacementOut(BaseModel):
    file_id: int
    x_mm: float
    y_mm: float
    w_mm: float
    d_mm: float


class CombineOut(BaseModel):
    fits: bool
    bed: BedIn
    required_bed: BedIn
    placements: list[CombinePlacementOut]
    file: FileOut | None = None
    job: JobOut | None = None
