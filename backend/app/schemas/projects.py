"""V4 projects API schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

ProjectStatus = Literal["planning", "active", "on_hold", "completed", "archived"]


def _clean_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        t = t.strip()
        if t and t not in out:
            out.append(t[:50])
    return out


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    status: ProjectStatus = "planning"
    due_date: date | None = None
    cover_model_id: int | None = None
    tags: list[str] = Field(default_factory=list, max_length=30)
    notes: str = Field(default="", max_length=20000)


class ProjectPatch(BaseModel):
    """Only keys present are applied (``model_fields_set``)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    status: ProjectStatus | None = None
    due_date: date | None = None
    cover_model_id: int | None = None
    tags: list[str] | None = Field(default=None, max_length=30)
    notes: str | None = Field(default=None, max_length=20000)


class ItemIn(BaseModel):
    kind: Literal["part", "purchased"]
    name: str | None = Field(default=None, max_length=200)
    model_id: int | None = None
    file_id: int | None = None
    plate: int | None = Field(default=None, ge=1)
    quantity: int = Field(default=1, ge=1, le=10000)
    acquired: int = Field(default=0, ge=0, le=10000)
    unit_cost: float | None = Field(default=None, ge=0)
    url: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=2000)


class ItemPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    file_id: int | None = None
    plate: int | None = Field(default=None, ge=1)
    quantity: int | None = Field(default=None, ge=1, le=10000)
    acquired: int | None = Field(default=None, ge=0, le=10000)
    unit_cost: float | None = Field(default=None, ge=0)
    url: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=2000)


class AddModelsIn(BaseModel):
    model_ids: list[int] = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=10000)


class ArchiveIdsIn(BaseModel):
    archive_ids: list[int] = Field(min_length=1, max_length=500)


class QueueRemainingIn(BaseModel):
    printer_id: int | None = None
    recipe_id: int | None = None


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class FromTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    due_date: date | None = None


class ItemOut(BaseModel):
    id: int
    kind: str
    position: int
    name: str | None = None
    model_id: int | None = None
    model_slug: str | None = None
    model_name: str | None = None
    file_id: int | None = None
    file_name: str | None = None
    plate: int | None = None
    quantity: int
    # Parts only.
    done: int = 0
    queued: int = 0
    outstanding: int = 0
    # Purchased only.
    acquired: int = 0
    unit_cost: float | None = None
    line_cost: float | None = None
    url: str | None = None
    notes: str | None = None


class CostRollup(BaseModel):
    filament_g: float = 0.0
    print_time_s: int = 0
    print_cost: float = 0.0
    purchased_cost: float = 0.0
    total_cost: float = 0.0
    # Average cost of finished prints x outstanding copies.
    estimated_remaining_cost: float = 0.0


class ProgressOut(BaseModel):
    parts_total: int = 0
    parts_done: int = 0
    parts_queued: int = 0
    purchased_total: int = 0
    purchased_acquired: int = 0
    percent: int = 0


class AttachmentOut(BaseModel):
    id: int
    filename: str
    content_type: str | None = None
    size: int
    created_at: datetime


class ProjectSummary(BaseModel):
    id: int
    name: str
    description: str
    status: ProjectStatus
    due_date: date | None = None
    overdue: bool = False
    cover_model_id: int | None = None
    cover_model_slug: str | None = None
    tags: list[str]
    is_template: bool
    completed_at: datetime | None = None
    created_by_user_id: int | None = None
    created_at: datetime
    updated_at: datetime
    progress: ProgressOut
    cost: CostRollup


class ProjectOut(ProjectSummary):
    notes: str
    items: list[ItemOut]
    attachments: list[AttachmentOut]


class ProjectArchiveOut(BaseModel):
    id: int
    model_id: int | None = None
    model_name: str | None = None
    plate: int | None = None
    result: str
    duration_s: int | None = None
    filament_g: float | None = None
    total_cost: float | None = None
    explicit: bool
    created_at: datetime


class TimelineEntry(BaseModel):
    at: datetime
    kind: str
    message: str
    user_id: int | None = None
    payload: dict | None = None


class QueueRemainingOut(BaseModel):
    enqueued: list[dict]
    skipped: list[dict]
