"""Slicer pipelines: CRUD (``/slicer-pipelines``), run on a file
(``POST /slicer-pipelines/{id}/run``) and its slice-job history. Writes need
``inventory:write`` (like recipes); running needs ``printers:print`` (like
``POST /files/{id}/slice`` and queue creation)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_owner_or_admin, require_perm, require_session
from app.api.profiles import _ilike_escape, resolve_scope
from app.api.queue import _notify_enqueued
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Printer, PrintRecipe, SliceJob, SlicerPipeline, SlicerProfile
from app.schemas.pipelines import (
    PipelineIn,
    PipelineOut,
    PipelineRunIn,
    PipelineRunOut,
    PipelineUpdate,
)
from app.schemas.slicing import SliceJobOut
from app.services import pipelines, slice_jobs
from app.services.print_plan import recipe_compatibility_reason, recipe_scope_reason

router = APIRouter(prefix="/slicer-pipelines", tags=["slicer-pipelines"])


async def _get_or_404(db: AsyncSession, pipeline_id: int) -> SlicerPipeline:
    row = await db.get(SlicerPipeline, pipeline_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"pipeline {pipeline_id} not found")
    return row


async def _out(db: AsyncSession, row: SlicerPipeline) -> PipelineOut:
    recipe = await db.get(PrintRecipe, row.recipe_id)
    out = PipelineOut.model_validate(row, from_attributes=True)
    out.recipe_name = recipe.name if recipe is not None else None
    return out


async def _check_recipe(db: AsyncSession, recipe_id: int) -> None:
    if await db.get(PrintRecipe, recipe_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"recipe {recipe_id} not found")


@router.get("", response_model=list[PipelineOut])
async def list_pipelines(
    printer_id: int | None = None,
    nozzle_mm: float | None = None,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> list[PipelineOut]:
    """Pipelines; with `printer_id`, only those that queue to that printer or
    whose recipe fits it (model + nozzle scope)."""
    stmt = select(SlicerPipeline).order_by(SlicerPipeline.name)
    if q and q.strip():
        stmt = stmt.where(SlicerPipeline.name.ilike(_ilike_escape(q.strip()), escape="\\"))
    rows = (await db.execute(stmt)).scalars().all()
    if printer_id is not None:
        printer_key, nozzle, _source = await resolve_scope(db, settings, ctx, printer_id, nozzle_mm)
        printer = await db.get(Printer, printer_id)
        if printer is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "printer not found")
        profiles = {p.id: p for p in (await db.execute(select(SlicerProfile))).scalars().all()}
        recipes = {r.id: r for r in (await db.execute(select(PrintRecipe))).scalars().all()}
        names = {i: n for i, n in (await db.execute(select(Printer.id, Printer.name))).all()}

        def keep(row: SlicerPipeline) -> bool:
            for step in row.steps or []:
                if step.get("kind") == "queue" and step.get("printer_id") == printer_id:
                    return True
            recipe = recipes.get(row.recipe_id)
            if recipe is None:
                return False
            pp = profiles.get(recipe.printer_profile_id)
            return (
                recipe_compatibility_reason(
                    recipe,
                    pp.printer_model if pp else None,
                    printer,
                    names.get(recipe.target_printer_id),
                )
                is None
                and recipe_scope_reason(recipe, printer_key, nozzle, profiles) is None
            )

        rows = [r for r in rows if keep(r)]
    return [await _out(db, r) for r in rows]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=PipelineOut,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def create_pipeline(
    payload: PipelineIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> PipelineOut:
    await _check_recipe(db, payload.recipe_id)
    data = payload.model_dump(mode="json")
    row = SlicerPipeline(**data, created_by_user_id=ctx.user.id)
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"a pipeline named {payload.name!r} already exists"
        ) from None
    await db.refresh(row)
    return await _out(db, row)


@router.get("/{pipeline_id}", response_model=PipelineOut)
async def get_pipeline(pipeline_id: int, db: AsyncSession = Depends(get_db)) -> PipelineOut:
    return await _out(db, await _get_or_404(db, pipeline_id))


@router.patch(
    "/{pipeline_id}",
    response_model=PipelineOut,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def update_pipeline(
    pipeline_id: int,
    payload: PipelineUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> PipelineOut:
    row = await _get_or_404(db, pipeline_id)
    require_owner_or_admin(ctx, row.created_by_user_id)
    changes = payload.model_dump(exclude_unset=True, mode="json")
    if changes.get("recipe_id") is not None:
        await _check_recipe(db, changes["recipe_id"])
    for field, value in changes.items():
        if field in ("name", "recipe_id", "steps") and value is None:
            continue
        setattr(row, field, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"a pipeline named {changes.get('name')!r} already exists"
        ) from None
    await db.refresh(row)
    return await _out(db, row)


@router.delete(
    "/{pipeline_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("inventory:write"))],
)
async def delete_pipeline(
    pipeline_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> None:
    row = await _get_or_404(db, pipeline_id)
    require_owner_or_admin(ctx, row.created_by_user_id)
    await db.delete(row)
    await db.commit()


@router.post(
    "/{pipeline_id}/run",
    status_code=status.HTTP_201_CREATED,
    response_model=PipelineRunOut,
    dependencies=[Depends(require_perm("printers:print"))],
)
async def run_pipeline(
    pipeline_id: int,
    payload: PipelineRunIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> PipelineRunOut:
    result = await pipelines.run_pipeline(
        db, settings, ctx, pipeline_id, payload.file_id, payload.plate
    )
    if result.queue_entry is not None:
        _notify_enqueued(result.queue_entry, ctx)
    return result


@router.get(
    "/{pipeline_id}/runs",
    response_model=list[SliceJobOut],
    dependencies=[Depends(require_perm("printers:print"))],
)
async def list_runs(
    pipeline_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[SliceJobOut]:
    """Recent slice-only runs of this pipeline (newest first; your own, or all
    for an admin). Runs with a ``queue`` step live in the queue instead."""
    await _get_or_404(db, pipeline_id)
    stmt = (
        select(SliceJob)
        .where(SliceJob.pipeline_id == pipeline_id)
        .order_by(SliceJob.created_at.desc())
        .limit(25)
    )
    if not ctx.is_admin:
        stmt = stmt.where(SliceJob.created_by_user_id == ctx.user.id)
    rows = (await db.execute(stmt)).scalars().all()
    return [await slice_jobs.job_out(db, j) for j in rows]
