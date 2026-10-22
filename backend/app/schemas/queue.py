"""Print queue: an ordered "models to print" worklist (Branch 4 Task 1),
extended by the P6 slicer/dispatch plan (Task 6) with per-file queue items
that carry a plate/printer/recipe and track their own slice-and-send
lifecycle.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import QueueState, ReviewState
from app.schemas.library import FileOut, ModelSummary


class PlanOptions(BaseModel):
    """Pre-print toggles shared by the print-plan preview (``app.schemas
    .print_flow.PlanOut``, which re-exports this) and a per-file queue
    entry's stored ``print_options`` (P6 Task 6). Defined here rather than
    in ``print_flow`` to avoid a ``printers -> queue -> print_flow ->
    printers`` import cycle (``app.schemas.printers`` imports
    ``QueueEntryOut``, and ``print_flow`` imports from ``printers``).
    """

    bed_levelling: bool = True
    flow_cali: bool = True
    timelapse: bool = False


class QueueEnqueueIn(BaseModel):
    # Bare legacy entry (model only, no specific file chosen yet) when
    # ``file_id`` is None -- idempotent, unchanged from Branch 4 Task 1.
    model_id: int | None = None
    file_id: int | None = None
    plate: int | None = None
    printer_id: int | None = None
    recipe_id: int | None = None
    copies: int = Field(1, ge=1, le=50)
    ams_mapping: list[int] | None = None
    options: PlanOptions | None = None
    # ``PrintDialog``'s "Slice and send": inserts at position 1 instead of
    # appending at the end.
    front: bool = False
    # P7 review/billing plan: which cost center to reserve against when
    # billing is on. ``None`` lazily resolves to the enqueuer's own private
    # center (``app.services.billing.ensure_private_center``).
    cost_center_id: int | None = None


class ReviewIn(BaseModel):
    """``POST /queue/{entry_id}/review`` payload (P7 review/billing plan
    Task 6)."""

    action: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=500)


class ReviewCountOut(BaseModel):
    count: int


class QueueReorderIn(BaseModel):
    """``PATCH /queue/{entry_id}`` payload: move the entry to this 1-based
    position, clamped to ``[1, n]`` by the service layer.
    """

    position: int


class QueueEntryOut(BaseModel):
    id: int
    model_id: int
    position: int
    added_at: datetime
    model: ModelSummary
    # Round 8 Task 3: the newest (highest File.id) file on the model's
    # CURRENT revision whose blob is a sliced `.gcode.3mf`, if any -- lets a
    # queue row offer a "Print" action directly rather than sending the user
    # back to the model detail page. Deliberately NOT keyed off
    # `ModelSummary.has_sliced` (that means "metadata extracted", a
    # different condition -- see `library.newest_printable_files`). `None`
    # when the model has no current revision, or none of its current-
    # revision files are a sendable gcode_3mf yet.
    printable_file: FileOut | None = None
    # P6 Task 6: per-file dispatch columns -- see `PrintQueueEntry`'s
    # docstring (`app.models.library`) for the full state machine.
    printer_id: int | None = None
    recipe_id: int | None = None
    recipe_name: str | None = None
    file_id: int | None = None
    sliced_file_id: int | None = None
    plate: int | None = None
    copies: int = 1
    state: QueueState = QueueState.WAITING
    assigned_printer_id: int | None = None
    print_job_id: int | None = None
    attempts: int = 0
    last_error: str | None = None
    # Display name of the requested ``file_id`` (its `rel_path`'s basename),
    # `None` for a bare entry.
    file_name: str | None = None
    # P7 review/billing plan columns -- see `PrintQueueEntry`'s docstring
    # (`app.models.library`) for the full state machine.
    cost_center_id: int | None = None
    review_state: ReviewState = ReviewState.NONE
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    reserved_cost: float | None = None
    hold_reason: str | None = None
