"""Print-jobs history (SPEC "API surface": print-jobs; M4 Task 7). Read-only
DB listing/lookup -- no adapter/lib involved, so this stays behind the same
``require_printer_enabled`` 503 gate as ``app.api.printers`` (the history is
only meaningful while the printer feature is on).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_printer_enabled, require_session, visible_printer_ids
from app.db import get_db
from app.models import PrintJob
from app.schemas.printers import PrintJobOut

router = APIRouter(
    prefix="/print-jobs", tags=["print-jobs"], dependencies=[Depends(require_printer_enabled)]
)


@router.get("", response_model=list[PrintJobOut])
async def list_print_jobs(
    printer_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[PrintJobOut]:
    stmt = select(PrintJob).order_by(PrintJob.id.desc()).limit(limit)
    visible = visible_printer_ids(ctx)
    if visible is not None:
        stmt = stmt.where(PrintJob.printer_id.in_(visible))
    if printer_id is not None:
        stmt = stmt.where(PrintJob.printer_id == printer_id)
    rows = (await db.execute(stmt)).scalars()
    return [PrintJobOut.from_model(j) for j in rows]


@router.get("/{job_id}", response_model=PrintJobOut)
async def get_print_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> PrintJobOut:
    job = await db.get(PrintJob, job_id)
    visible = visible_printer_ids(ctx)
    if job is None or (visible is not None and job.printer_id not in visible):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "print job not found")
    return PrintJobOut.from_model(job)
