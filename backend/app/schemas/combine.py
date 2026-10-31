"""``POST /models/{slug}/combine`` payload/response (put several meshes on one plate)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.jobs import JobOut
from app.schemas.library import FileOut


class CombineItemIn(BaseModel):
    """grid/auto: ``copies`` of the file. manual: ONE instance at the pose
    (``x_mm``/``y_mm`` = footprint centre on the bed, ``copies`` ignored)."""

    file_id: int
    copies: int = Field(default=1, ge=1, le=100)
    x_mm: float | None = Field(default=None, ge=-5000, le=5000)
    y_mm: float | None = Field(default=None, ge=-5000, le=5000)
    rot_z_deg: float = Field(default=0.0, ge=-3600, le=3600)
    scale: float = Field(default=1.0, gt=0, le=20)
    copy_index: int = Field(default=0, ge=0, le=1000)


class BedIn(BaseModel):
    x_mm: float = Field(gt=0, le=2000)
    y_mm: float = Field(gt=0, le=2000)


class CombineIn(BaseModel):
    items: list[CombineItemIn] = Field(min_length=1, max_length=100)
    layout: Literal["grid", "auto", "manual"] = "auto"
    spacing_mm: float = Field(default=5.0, ge=0, le=100)
    # Bed: a printer slicer profile's printable area, or explicit dimensions.
    printer_profile_id: int | None = None
    bed: BedIn | None = None
    name: str | None = Field(default=None, max_length=200)
    # Plan only: nothing is written, the placements come back for a preview.
    dry_run: bool = False

    @model_validator(mode="after")
    def _manual_needs_poses(self) -> CombineIn:
        if self.layout == "manual" and any(i.x_mm is None or i.y_mm is None for i in self.items):
            raise ValueError("manual layout needs x_mm and y_mm on every item")
        return self


class CombinePlacementOut(BaseModel):
    file_id: int
    x_mm: float
    y_mm: float
    w_mm: float
    d_mm: float


class CombinePoseOut(BaseModel):
    file_id: int
    x_mm: float
    y_mm: float
    rot_z_deg: float
    scale: float
    copy_index: int
    fits: bool


class CombinePartOut(BaseModel):
    """Source mesh AABB (mm): ``min_*`` is its min corner, the pose pivot is
    (min_x + w/2, min_y + d/2, min_z)."""

    file_id: int
    min_x: float
    min_y: float
    min_z: float
    w_mm: float
    d_mm: float
    h_mm: float


class CombineOut(BaseModel):
    fits: bool
    bed: BedIn
    required_bed: BedIn
    placements: list[CombinePlacementOut]
    poses: list[CombinePoseOut] = []
    parts: list[CombinePartOut] = []
    file: FileOut | None = None
    job: JobOut | None = None
