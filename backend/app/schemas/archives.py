"""Print archive API shapes (P2)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.models.enums import ArchiveFetchState, ArchiveResult, PrintJobOrigin
from app.schemas.prints import PrintOut


class ArchiveOut(BaseModel):
    id: int
    print_job_id: int
    printer_id: int
    printer_name: str
    origin: PrintJobOrigin
    subtask_name: str | None
    model_id: int | None
    model_slug: str | None
    model_name: str | None
    revision_id: int | None
    result: ArchiveResult
    duration_s: int | None
    est_filament: list | None
    plate: int | None
    fetch_state: ArchiveFetchState
    fetch_error: str | None
    has_thumbnail: bool
    has_file: bool
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    created_by_user_id: int | None
    run: PrintOut | None = None
