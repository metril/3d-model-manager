"""Schemas for slicer pipelines (``app.api.pipelines``): a recipe plus an
ordered list of steps run in one click on a file."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.library import NonEmptyStr
from app.schemas.queue import QueueEntryOut
from app.schemas.slicing import SliceJobOut


class OrientStep(BaseModel):
    """Pre-step: turn on the slicer's auto-orient for this run."""

    kind: Literal["orient"]


class ArrangeStep(BaseModel):
    """Pre-step: turn on the slicer's auto-arrange for this run."""

    kind: Literal["arrange"]


class RenameStep(BaseModel):
    """Post-step: rename the sliced file. ``{name}`` (source file stem),
    ``{recipe}``, ``{pipeline}`` and ``{plate}`` are substituted."""

    kind: Literal["rename"]
    template: str = Field(min_length=1, max_length=200)

    @field_validator("template")
    @classmethod
    def _no_path(cls, v: str) -> str:
        if "/" in v or "\\" in v:
            raise ValueError("template must not contain path separators")
        return v


class QueueStep(BaseModel):
    """Final step: queue the result for printing instead of stopping at the
    sliced file. The queue slices with the recipe itself, so this replaces the
    standalone slice (and is exclusive with ``rename``)."""

    kind: Literal["queue"]
    printer_id: int | None = None
    copies: int = Field(1, ge=1, le=50)
    front: bool = False
    manual_start: bool = False


PipelineStep = Annotated[
    OrientStep | ArrangeStep | RenameStep | QueueStep, Field(discriminator="kind")
]


def check_steps(steps: list) -> list:
    kinds = [s.kind for s in steps]
    if len(set(kinds)) != len(kinds):
        raise ValueError("each step kind may appear at most once")
    if "rename" in kinds and "queue" in kinds:
        raise ValueError("rename and queue steps are exclusive (the queue names its own slice)")
    return steps


class PipelineIn(BaseModel):
    name: NonEmptyStr
    description: str | None = None
    recipe_id: int
    plate: int | None = Field(None, ge=1)
    steps: list[PipelineStep] = []

    @model_validator(mode="after")
    def _validate(self) -> PipelineIn:
        check_steps(self.steps)
        return self


class PipelineUpdate(BaseModel):
    name: NonEmptyStr | None = None
    description: str | None = None
    recipe_id: int | None = None
    plate: int | None = Field(None, ge=1)
    steps: list[PipelineStep] | None = None

    @model_validator(mode="after")
    def _validate(self) -> PipelineUpdate:
        if self.steps is not None:
            check_steps(self.steps)
        return self


class PipelineOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    recipe_id: int
    recipe_name: str | None = None
    plate: int | None = None
    steps: list[PipelineStep]
    created_by_user_id: int | None = None
    created_at: datetime


class PipelineRunIn(BaseModel):
    file_id: int
    plate: int | None = Field(None, ge=1)


class PipelineRunOut(BaseModel):
    """``kind="slice"``: a slice job to follow (``slice_job``); ``"queue"``:
    the queue entry now slicing/printing (``queue_entry``)."""

    kind: Literal["slice", "queue"]
    slice_job: SliceJobOut | None = None
    queue_entry: QueueEntryOut | None = None
