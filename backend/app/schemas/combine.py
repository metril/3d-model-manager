"""``POST /models/{slug}/combine`` payload/response (put several meshes on one plate)."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.jobs import JobOut
from app.schemas.library import FileOut


class CombineItemIn(BaseModel):
    """grid/auto: ``copies`` of the file. manual: ONE instance at the pose
    (``x_mm``/``y_mm`` = footprint centre on the bed, ``copies`` ignored)."""

    file_id: int
    # Build item of a multi-object 3MF to use instead of the whole file.
    object_index: int | None = Field(default=None, ge=0, le=100_000)
    copies: int = Field(default=1, ge=1, le=100)
    x_mm: float | None = Field(default=None, ge=-5000, le=5000)
    y_mm: float | None = Field(default=None, ge=-5000, le=5000)
    rot_z_deg: float = Field(default=0.0, ge=-3600, le=3600)
    scale: float = Field(default=1.0, gt=0, le=20)
    copy_index: int = Field(default=0, ge=0, le=1000)
    # Unit quaternion [x, y, z, w] applied to the mesh before it is dropped on
    # the bed (then Z-rotated/scaled by the pose). Missing = as authored.
    orient: list[float] | None = Field(default=None, min_length=4, max_length=4)

    @field_validator("orient")
    @classmethod
    def _unit_quat(cls, v: list[float] | None) -> list[float] | None:
        if v is None:
            return None
        norm = math.sqrt(sum(c * c for c in v))
        if not all(math.isfinite(c) for c in v) or not 0.5 < norm < 2.0:
            raise ValueError("orient must be a unit quaternion [x, y, z, w]")
        return v


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
    # grid/auto only: orient every item for printing (best flat face down)
    # before laying it out, ignoring any ``orient`` on the items.
    auto_orient: bool = False

    @model_validator(mode="after")
    def _manual_needs_poses(self) -> CombineIn:
        if self.layout == "manual" and any(i.x_mm is None or i.y_mm is None for i in self.items):
            raise ValueError("manual layout needs x_mm and y_mm on every item")
        return self


class CombinePlacementOut(BaseModel):
    file_id: int
    object_index: int | None = None
    x_mm: float
    y_mm: float
    w_mm: float
    d_mm: float


class CombinePoseOut(BaseModel):
    file_id: int
    object_index: int | None = None
    x_mm: float
    y_mm: float
    rot_z_deg: float
    scale: float
    copy_index: int
    fits: bool
    orient: list[float] = [0.0, 0.0, 0.0, 1.0]
    # Oriented, unscaled source dims of this pose's mesh.
    w_mm: float = 0.0
    d_mm: float = 0.0
    h_mm: float = 0.0


class CombinePartOut(BaseModel):
    """Source mesh AABB (mm): ``min_*`` is its min corner, the pose pivot is
    (min_x + w/2, min_y + d/2, min_z)."""

    file_id: int
    object_index: int | None = None
    min_x: float
    min_y: float
    min_z: float
    w_mm: float
    d_mm: float
    h_mm: float
    orient: list[float] = [0.0, 0.0, 0.0, 1.0]


class OrientTarget(BaseModel):
    file_id: int
    object_index: int | None = None


class OrientIn(BaseModel):
    """``file_ids`` (whole files) and/or ``targets`` (a file's build item)."""

    file_ids: list[int] = Field(default_factory=list, max_length=100)
    targets: list[OrientTarget] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _something(self) -> OrientIn:
        if not self.file_ids and not self.targets:
            raise ValueError("give file_ids or targets")
        return self


class ObjectOut(BaseModel):
    """One build item of a multi-object 3MF on a single bed (bed-local mm;
    ``x_mm``/``y_mm`` = footprint centre)."""

    index: int
    plate: int | None
    name: str
    x_mm: float
    y_mm: float
    w_mm: float
    d_mm: float
    h_mm: float
    glb_url: str


class FileObjectsOut(BaseModel):
    """Empty ``objects`` = a single object / not a 3MF: use the whole file."""

    file_id: int
    objects: list[ObjectOut]


class OrientFileOut(BaseModel):
    file_id: int
    object_index: int | None = None
    orient: list[float]
    w_mm: float
    d_mm: float
    h_mm: float


class OrientOut(BaseModel):
    files: list[OrientFileOut]


class CombineOut(BaseModel):
    fits: bool
    bed: BedIn
    required_bed: BedIn
    placements: list[CombinePlacementOut]
    poses: list[CombinePoseOut] = []
    parts: list[CombinePartOut] = []
    file: FileOut | None = None
    job: JobOut | None = None
