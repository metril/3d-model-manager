"""Slicer pipelines: a recipe plus ordered steps run in one click on a file.

Pre-steps (``orient`` / ``arrange``) become slice-option overrides layered on
the recipe's own options. The run then either

* slices standalone (a ``SliceJob`` carrying ``pipeline_id``): the ``rename``
  post-step is applied by ``apply_post_steps`` when the slice finishes, and
  the existing slice-job tracking/toasts follow it; or
* with a ``queue`` step, enqueues the file with the recipe (the queue's own
  slice-and-send flow does the rest, honouring the overrides via
  ``print_queue.slice_options``).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.api.deps import AuthContext
from app.config import Settings
from app.models import File, Printer, PrintRecipe, SlicerPipeline, SlicerProfile
from app.schemas.pipelines import PipelineRunOut, QueueStep
from app.schemas.queue import QueueEnqueueIn
from app.schemas.slicing import SliceJobIn
from app.services import queue as queue_service
from app.services import slice_jobs
from app.services.print_plan import recipe_compatibility_reason

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r'[\x00-\x1f/\\:*?"<>|]')
_SOURCE_SUFFIXES = (".gcode.3mf", ".3mf", ".stl", ".step", ".stp", ".obj")


def option_overrides(steps: list[dict]) -> dict:
    """Pre-steps -> slice options that override the recipe's own."""
    kinds = {s.get("kind") for s in steps}
    out: dict = {}
    if "orient" in kinds:
        out["auto_orient"] = True
    if "arrange" in kinds:
        out["arrange"] = True
    return out


def _step(steps: list[dict], kind: str) -> dict | None:
    return next((s for s in steps if s.get("kind") == kind), None)


def _stem(name: str) -> str:
    for suffix in _SOURCE_SUFFIXES:
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return name


def render_name(template: str, *, name: str, recipe: str, pipeline: str, plate: int) -> str:
    """The renamed file name for a ``rename`` step (always ends
    ``.gcode.3mf``, never contains a path separator)."""
    text = template
    for key, value in (
        ("name", name),
        ("recipe", recipe),
        ("pipeline", pipeline),
        ("plate", plate),
    ):
        text = text.replace("{" + key + "}", str(value))
    text = _stem(_UNSAFE.sub("_", text).strip(" ."))
    return f"{text or name}.gcode.3mf"


def apply_post_steps(session: Session, job, outcome) -> None:
    """Runs a finished pipeline slice job's post-steps (``job`` is the locked
    ``SliceJob`` row, committed by the caller). Best-effort: a step that can't
    apply (e.g. the target name is taken) is skipped and logged, never fails
    the slice."""
    pipeline = session.get(SlicerPipeline, job.pipeline_id)
    if pipeline is None:
        return
    rename = _step(pipeline.steps or [], "rename")
    # Only a file this run created is renamed -- a byte-identical re-slice
    # reuses an existing File that other jobs/entries may reference.
    if rename is None or not outcome.created:
        return
    sliced = session.get(File, outcome.file_id)
    source = session.get(File, job.file_id)
    recipe = session.get(PrintRecipe, job.recipe_id) if job.recipe_id else None
    if sliced is None or source is None:
        return
    new_name = render_name(
        rename["template"],
        name=_stem(Path(source.rel_path).name),
        recipe=recipe.name if recipe is not None else "",
        pipeline=pipeline.name,
        plate=job.plate or 1,
    )
    parent = Path(sliced.rel_path).parent
    new_rel = new_name if str(parent) == "." else f"{parent}/{new_name}"
    taken = session.scalar(
        select(File.id).where(File.revision_id == sliced.revision_id, File.rel_path == new_rel)
    )
    if taken is not None and taken != sliced.id:
        logger.info("pipeline %s: rename target %r is taken", pipeline.id, new_rel)
        return
    sliced.rel_path = new_rel


async def run_pipeline(
    db: AsyncSession,
    settings: Settings,
    ctx: AuthContext,
    pipeline_id: int,
    file_id: int,
    plate: int | None,
    force: bool = False,
) -> PipelineRunOut:
    pipeline = await db.get(SlicerPipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"pipeline {pipeline_id} not found")
    steps = pipeline.steps or []
    overrides = option_overrides(steps)
    plate = plate if plate is not None else pipeline.plate

    queue_step = _step(steps, "queue")
    if queue_step is None:
        job = await slice_jobs.create_job(
            db,
            settings,
            file_id,
            SliceJobIn(recipe_id=pipeline.recipe_id, plate=plate, force=force),
            ctx.user.id,
            pipeline_id=pipeline.id,
            options=overrides,
        )
        return PipelineRunOut(kind="slice", slice_job=job)

    step = QueueStep.model_validate(queue_step)
    if step.printer_id is not None:
        printer = await db.get(Printer, step.printer_id)
        if printer is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"printer {step.printer_id} not found")
        recipe = await db.get(PrintRecipe, pipeline.recipe_id)
        profile = await db.get(SlicerProfile, recipe.printer_profile_id) if recipe else None
        reason = (
            recipe_compatibility_reason(recipe, profile.printer_model if profile else None, printer)
            if recipe is not None
            else None
        )
        if reason is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"recipe isn't compatible with {printer.name}: {reason}"
            )
    entry = await queue_service.enqueue_file(
        db,
        settings,
        QueueEnqueueIn(
            file_id=file_id,
            plate=plate,
            printer_id=step.printer_id,
            recipe_id=pipeline.recipe_id,
            copies=step.copies,
            front=step.front,
            manual_start=step.manual_start,
        ),
        ctx,
        slice_options=overrides or None,
    )
    return PipelineRunOut(kind="queue", queue_entry=entry)
