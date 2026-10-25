from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TrashItemOut(BaseModel):
    id: int
    slug: str
    name: str
    created_by_user_id: int | None
    deleted_at: datetime
    deleted_by: str | None
    purge_at: datetime | None
    file_count: int
    size_bytes: int


class TrashListOut(BaseModel):
    items: list[TrashItemOut]
    retention_days: int


class TrashIdsIn(BaseModel):
    ids: list[int] = Field(min_length=1)


class TrashCountOut(BaseModel):
    count: int
