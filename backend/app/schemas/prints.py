"""Per-model print history (Branch 5 Task 1): a user-entered log of print
attempts, distinct from the print queue's worklist (``app.schemas.queue``)
and ``print_jobs``' live send-to-printer telemetry (M4).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from app.models.enums import PrintQuality, PrintResult, PrintSource

if TYPE_CHECKING:
    from app.models.library import Print


class PrintMaterialOut(BaseModel):
    """The nested `material` a `PrintOut` carries -- the full CRUD shape
    lives in `app.schemas.materials`."""

    id: int
    name: str
    kind: str | None = None
    color: str | None = None


class PrintCreateIn(BaseModel):
    """``POST /models/{model_id}/prints`` payload. An omitted ``printed_at``
    defaults to now (the DB column's own ``server_default=now()`` -- left
    unset here rather than resolved client-side so the server clock is
    authoritative).
    """

    printed_at: datetime | None = None
    printer_name: str | None = None
    filament: str | None = None
    filament_g: float | None = Field(default=None, ge=0)
    # R13c: optional structured material, alongside the `filament` free-text
    # snapshot above (the Log-print form's "Other..." free-text path leaves
    # this unset).
    material_id: int | None = None
    result: PrintResult = PrintResult.SUCCESS
    duration_min: int | None = Field(default=None, ge=0)
    notes: str | None = None


class PrintPatchIn(BaseModel):
    """All fields optional; only the ones present in the request body are
    applied (``model_dump(exclude_unset=True)`` in ``app.api.prints``,
    mirrors ``ModelPatch``'s patch semantics).
    """

    printed_at: datetime | None = None
    printer_name: str | None = None
    filament: str | None = None
    filament_g: float | None = Field(default=None, ge=0)
    material_id: int | None = None
    result: PrintResult | None = None
    duration_min: int | None = Field(default=None, ge=0)
    notes: str | None = None
    quality: PrintQuality | None = None
    failure_reason: str | None = Field(default=None, max_length=500)

    @field_validator("printed_at", "result")
    @classmethod
    def _reject_explicit_null(cls, value: object) -> object:
        """``printed_at``/``result`` are NOT-NULL columns -- an *absent* field
        is fine (``exclude_unset`` drops it before it reaches the service
        layer), but an explicit ``null`` would otherwise sail through this
        `T | None` typing and hit the DB as a NOT-NULL violation (500)
        instead of a clean 422.
        """
        if value is None:
            raise ValueError("field cannot be null")
        return value


class PrintOut(BaseModel):
    id: int
    model_id: int | None
    print_job_id: int | None = None
    source: PrintSource = PrintSource.MANUAL
    quality: PrintQuality | None = None
    failure_reason: str | None = None
    filament_cost: float | None = None
    machine_cost: float | None = None
    energy_kwh: float | None = None
    energy_cost: float | None = None
    wear_cost: float | None = None
    total_cost: float | None = None
    created_by_user_id: int | None = None
    printed_at: datetime
    printer_name: str | None
    filament: str | None
    filament_g: float | None
    material_id: int | None = None
    material: PrintMaterialOut | None = None
    result: PrintResult
    duration_min: int | None
    notes: str | None
    created_at: datetime
    # R13c: populated only by `app.services.stats`'s `recent_prints` --
    # `None` for every other caller (per-model listing already scopes to
    # one model, so it would be redundant there).
    model_slug: str | None = None
    model_name: str | None = None

    @classmethod
    def from_model(
        cls,
        row: Print,
        *,
        model_slug: str | None = None,
        model_name: str | None = None,
    ) -> PrintOut:
        material = (
            PrintMaterialOut(
                id=row.material.id,
                name=row.material.name,
                kind=row.material.kind,
                color=row.material.color,
            )
            if row.material is not None
            else None
        )
        return cls(
            id=row.id,
            model_id=row.model_id,
            print_job_id=row.print_job_id,
            source=row.source,
            quality=row.quality,
            failure_reason=row.failure_reason,
            filament_cost=row.filament_cost,
            machine_cost=row.machine_cost,
            energy_kwh=row.energy_kwh,
            energy_cost=row.energy_cost,
            wear_cost=row.wear_cost,
            total_cost=row.total_cost,
            created_by_user_id=row.created_by_user_id,
            printed_at=row.printed_at,
            printer_name=row.printer_name,
            filament=row.filament,
            filament_g=row.filament_g,
            material_id=row.material_id,
            material=material,
            result=row.result,
            duration_min=row.duration_min,
            notes=row.notes,
            created_at=row.created_at,
            model_slug=model_slug,
            model_name=model_name,
        )
