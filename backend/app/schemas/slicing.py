"""Schemas for slicer profiles, print recipes, and sidecar health (P6
"slicer, profiles, recipes and dispatch" plan, Task 4). CRUD lives behind
``app.api.profiles``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.enums import Fanout, ProfileKind, ProfileSource
from app.schemas.library import NonEmptyStr


class ProfileIn(BaseModel):
    name: NonEmptyStr
    kind: ProfileKind
    printer_model: str | None = None
    nozzle_mm: float | None = None
    data: dict


class ProfileUpdate(BaseModel):
    """All fields optional; only the ones present in the request body are
    applied (``model_dump(exclude_unset=True)``, mirrors
    ``MaterialUpdate``'s patch semantics)."""

    name: NonEmptyStr | None = None
    printer_model: str | None = None
    nozzle_mm: float | None = None
    data: dict | None = None


class ProfileOut(BaseModel):
    id: int
    name: str
    kind: ProfileKind
    printer_model: str | None = None
    nozzle_mm: float | None = None
    source: ProfileSource
    created_by_user_id: int | None = None
    created_at: datetime
    updated_at: datetime
    # Omitted from list responses unless ``?full=1``; always present on
    # ``GET /profiles/{id}``.
    data: dict | None = None


class ProfileImportOut(BaseModel):
    created: int
    updated: int
    skipped: int = 0


class RecipeIn(BaseModel):
    name: NonEmptyStr
    printer_profile_id: int
    process_profile_id: int
    filament_profile_ids: list[int | None] = []
    bed_type: NonEmptyStr
    options: dict = {}
    target_printer_id: int | None = None
    target_printer_model: str | None = None
    fanout: Fanout = Fanout.SINGLE
    n: int | None = None


class RecipeUpdate(BaseModel):
    name: NonEmptyStr | None = None
    printer_profile_id: int | None = None
    process_profile_id: int | None = None
    filament_profile_ids: list[int | None] | None = None
    bed_type: NonEmptyStr | None = None
    options: dict | None = None
    target_printer_id: int | None = None
    target_printer_model: str | None = None
    fanout: Fanout | None = None
    n: int | None = None


class RecipeOut(BaseModel):
    id: int
    name: str
    printer_profile_id: int
    process_profile_id: int
    filament_profile_ids: list[int | None]
    bed_type: str
    options: dict
    target_printer_id: int | None = None
    target_printer_model: str | None = None
    fanout: Fanout
    n: int | None = None
    created_by_user_id: int | None = None
    created_at: datetime
    # Set only when listed with ``?printer_id=``; ``None`` otherwise.
    compatible: bool | None = None
    incompatible_reason: str | None = None


class SlicerHealthOut(BaseModel):
    busy: bool | None = None
    running: int | None = None
    max_concurrency: int | None = None
    job_timeout_s: float | None = None
    mem_limit: int | None = None
    bambu_version: str | None = None
    configured: bool
    ok: bool
    version: str | None = None
    error: str | None = None


class SliceJobIn(BaseModel):
    recipe_id: int
    plate: int | None = None


class SliceJobOut(BaseModel):
    id: uuid.UUID
    file_id: int
    model_id: int | None = None
    model_slug: str | None = None
    file_name: str | None = None
    recipe_id: int | None = None
    plate: int | None = None
    state: Literal["queued", "running", "done", "failed", "cancelled"]
    error: str | None = None
    log_tail: str | None = None
    sliced_file_id: int | None = None
    # {print_time_s, filament_g, filament_m, filament_types, layer_height,
    # nozzle, printer_model, plate_count, plates: [{index, prediction_s,
    # weight_g}]} -- set once `done`.
    meta: dict | None = None
    created_at: datetime
    updated_at: datetime
