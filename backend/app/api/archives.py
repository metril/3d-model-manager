"""Print archive endpoints (P2): history of finished printer runs.

Archives on printers the caller cannot see are 404 (never 403), and stay
listable when the printer feature flag is off (history outlives the flag).
"""

from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import AuthContext, require_perm, require_session, visible_printer_ids
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Model, Printer, PrintJob
from app.models.enums import ArchiveResult
from app.models.library import Print
from app.models.printing import PrintArchive
from app.schemas.archives import ArchiveOut
from app.schemas.prints import PrintOut
from app.services.archive import archive_path

router = APIRouter(prefix="/archives", tags=["archives"])


def archive_select() -> Any:
    return (
        select(PrintArchive, PrintJob, Printer, Model, Print)
        .join(PrintJob, PrintJob.id == PrintArchive.print_job_id)
        .join(Printer, Printer.id == PrintJob.printer_id)
        .outerjoin(Model, Model.id == PrintArchive.model_id)
        .outerjoin(Print, Print.print_job_id == PrintArchive.print_job_id)
        .options(selectinload(Print.material))
    )


def archive_out(row: Any) -> ArchiveOut:
    a, job, printer, model, run = row
    return ArchiveOut(
        id=a.id,
        print_job_id=a.print_job_id,
        printer_id=printer.id,
        printer_name=printer.name,
        origin=job.origin,
        subtask_name=job.subtask_name,
        model_id=a.model_id,
        model_slug=model.slug if model else None,
        model_name=model.name if model else None,
        revision_id=a.revision_id,
        result=a.result,
        duration_s=a.duration_s,
        est_filament=a.est_filament,
        plate=a.plate,
        fetch_state=a.fetch_state,
        fetch_error=a.fetch_error,
        has_thumbnail=a.thumbnail_blob is not None,
        has_file=a.archive_blob is not None,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=a.created_at,
        created_by_user_id=a.created_by_user_id,
        run=PrintOut.from_model(
            run, model_slug=model.slug if model else None, model_name=model.name if model else None
        )
        if run
        else None,
    )


async def _get_visible(db: AsyncSession, ctx: AuthContext, archive_id: int) -> Any:
    stmt = archive_select().where(PrintArchive.id == archive_id)
    visible = visible_printer_ids(ctx)
    if visible is not None:
        stmt = stmt.where(PrintJob.printer_id.in_(visible))
    row = (await db.execute(stmt)).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "archive not found")
    return row


@router.get("", response_model=list[ArchiveOut])
async def list_archives(
    printer: int | None = None,
    result: ArchiveResult | None = None,
    model: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[ArchiveOut]:
    stmt = archive_select()
    visible = visible_printer_ids(ctx)
    if visible is not None:
        stmt = stmt.where(PrintJob.printer_id.in_(visible))
    if printer is not None:
        stmt = stmt.where(PrintJob.printer_id == printer)
    if result is not None:
        stmt = stmt.where(PrintArchive.result == result)
    if model is not None:
        stmt = stmt.where(PrintArchive.model_id == model)
    stmt = stmt.order_by(PrintArchive.created_at.desc(), PrintArchive.id.desc())
    rows = (await db.execute(stmt.limit(limit).offset(offset))).all()
    return [archive_out(r) for r in rows]


@router.get("/{archive_id}", response_model=ArchiveOut)
async def get_archive(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> ArchiveOut:
    return archive_out(await _get_visible(db, ctx, archive_id))


async def _serve(
    row: Any, blob: str | None, ext: str, media_type: str, settings: Settings, filename: str | None
) -> FileResponse:
    if blob is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not available")
    path = archive_path(settings, blob, ext)
    if not await anyio.to_thread.run_sync(path.exists):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not available")
    return FileResponse(path, media_type=media_type, filename=filename)


@router.get("/{archive_id}/thumbnail")
async def get_thumbnail(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    row = await _get_visible(db, ctx, archive_id)
    return await _serve(row, row[0].thumbnail_blob, "png", "image/png", settings, None)


@router.get("/{archive_id}/file")
async def get_file(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    row = await _get_visible(db, ctx, archive_id)
    name = (row[1].subtask_name or f"archive-{archive_id}").replace("/", "_")
    return await _serve(
        row,
        row[0].archive_blob,
        "gcode.3mf",
        "application/octet-stream",
        settings,
        f"{name}.gcode.3mf",
    )


@router.post("/{archive_id}/reprint", dependencies=[Depends(require_perm("printers:print"))])
async def reprint_archive(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> None:
    row = await _get_visible(db, ctx, archive_id)
    ctx.require_printer(row[2].id, "print")
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "reprint arrives with the P5 print flow")
