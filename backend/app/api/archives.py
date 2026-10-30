"""Print archive endpoints (P2): history of finished printer runs.

Archives on printers the caller cannot see are 404 (never 403), and stay
listable when the printer feature flag is off (history outlives the flag).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

import anyio
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import AuthContext, require_perm, require_session, visible_printer_ids
from app.config import Settings, get_settings
from app.db import get_db
from app.models import AuditLog, File, Model, Printer, PrintJob
from app.models.enums import ArchiveResult, BlobFormat, VerdictSource
from app.models.library import Blob, Print
from app.models.printing import PrintArchive
from app.schemas.archives import (
    ArchiveOut,
    ArchivePatchIn,
    ArchiveRunOut,
    LatestRunOut,
    PhotoOut,
    VerdictIn,
)
from app.schemas.library import TagIdsIn, TagRef
from app.schemas.print_flow import PlanIn, PlanOut
from app.schemas.prints import PrintOut
from app.services import archive_confirm
from app.services import tags as tags_svc
from app.services.archive import archive_path
from app.services.archive_blobs import blob_hashes, release_blobs, unlink_all
from app.services.print_plan import build_plan

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
        result=a.result_override or a.result,
        recorded_result=a.result,
        result_override=a.result_override,
        notes=a.notes,
        tags=list(a.tags or []),
        confirm_requested=a.confirm_requested,
        confirm_expires_at=a.confirm_expires_at,
        user_verdict=a.user_verdict,
        user_verdict_source=a.user_verdict_source,
        verdict_at=a.verdict_at,
        verdict_by_user_id=a.verdict_by_user_id,
        photos=[
            PhotoOut(
                filename=p["filename"],
                kind=p.get("kind", "photo"),
                uploaded_by=p.get("uploaded_by"),
                at=p.get("at"),
            )
            for p in (a.photos or [])
        ],
        duration_s=a.duration_s,
        est_filament=a.est_filament,
        plate=a.plate,
        fetch_state=a.fetch_state,
        fetch_error=a.fetch_error,
        has_thumbnail=a.thumbnail_blob is not None,
        has_file=a.archive_blob is not None,
        has_snapshot=a.snapshot_blob is not None,
        has_timelapse=a.timelapse_blob is not None,
        has_source=a.source_blob is not None,
        source_name=a.source_name,
        source_linked=a.source_linked,
        has_f3d=a.f3d_blob is not None,
        f3d_name=a.f3d_name,
        media_note=a.media_note,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=a.created_at,
        created_by_user_id=a.created_by_user_id,
        run_count=a.run_count,
        latest_run=LatestRunOut(
            id=run.id,
            outcome=a.result_override or a.result,
            finished_at=job.finished_at,
            duration_s=a.duration_s,
            total_cost=run.total_cost,
        )
        if run
        else None,
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


async def _with_tag_refs(db: AsyncSession, outs: list[ArchiveOut]) -> list[ArchiveOut]:
    refs = await tags_svc.refs_for(db, "archive", [o.id for o in outs])
    for o in outs:
        o.tag_refs = refs.get(o.id, [])
    return outs


@router.get("", response_model=list[ArchiveOut])
async def list_archives(
    printer: int | None = None,
    result: ArchiveResult | None = None,
    model: int | None = None,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    confirm_pending: bool = False,
    mine: bool = False,
    tags: Annotated[list[int] | None, Query()] = None,
    tag_mode: tags_svc.TagMode = "any",
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
        stmt = stmt.where(
            func.coalesce(PrintArchive.result_override, PrintArchive.result) == result
        )
    if model is not None:
        stmt = stmt.where(PrintArchive.model_id == model)
    if from_ is not None:
        stmt = stmt.where(
            PrintArchive.created_at >= datetime(from_.year, from_.month, from_.day, tzinfo=UTC)
        )
    if to is not None:
        end = datetime(to.year, to.month, to.day, tzinfo=UTC) + timedelta(days=1)
        stmt = stmt.where(PrintArchive.created_at < end)
    if confirm_pending:
        stmt = stmt.where(
            PrintArchive.confirm_requested.is_(True), PrintArchive.user_verdict.is_(None)
        )
    if mine:
        stmt = stmt.where(PrintJob.created_by_user_id == ctx.user.id)
    if tags:
        stmt = stmt.where(tags_svc.tag_filter("archive", PrintArchive.id, tags, tag_mode))
    stmt = stmt.order_by(PrintArchive.created_at.desc(), PrintArchive.id.desc())
    rows = (await db.execute(stmt.limit(limit).offset(offset))).all()
    return await _with_tag_refs(db, [archive_out(r) for r in rows])


def require_writer(ctx: AuthContext, job: PrintJob, printer: Printer) -> None:
    """Admin, the job's owner, or ``printers:print`` + the printer's print ACL."""
    if ctx.is_admin or (
        job.created_by_user_id is not None and job.created_by_user_id == ctx.user.id
    ):
        return
    ctx.require_printer(printer.id, "print")


@router.patch("/{archive_id}", response_model=ArchiveOut)
async def patch_archive(
    archive_id: int,
    body: ArchivePatchIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> ArchiveOut:
    arc, job, printer, _m, run = await _get_visible(db, ctx, archive_id)
    require_writer(ctx, job, printer)
    fields = body.model_fields_set
    if "notes" in fields:
        arc.notes = (body.notes or "").strip() or None
    if "tags" in fields:
        seen: list[str] = []
        for t in body.tags or []:
            t = t.strip()[:40]
            if t and t not in seen:
                seen.append(t)
        if len(seen) > 20:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "at most 20 tags")
        arc.tags = seen
    if "result_override" in fields:
        # Overriding to the recorded result is the same as clearing it.
        new = None if body.result_override == arc.result else body.result_override
        if new != arc.result_override:
            db.add(
                AuditLog(
                    user_id=ctx.user.id,
                    created_by_user_id=ctx.user.id,
                    printer_id=printer.id,
                    action="archive.result_override",
                    payload={
                        "archive_id": arc.id,
                        "from": arc.result_override,
                        "to": new,
                        "recorded": arc.result,
                    },
                )
            )
            arc.result_override = new
            archive_confirm.sync_run_result(arc, run)
    await db.commit()
    return (await _with_tag_refs(db, [archive_out(await _get_visible(db, ctx, archive_id))]))[0]


@router.put("/{archive_id}/tags", response_model=list[TagRef])
async def set_archive_tags(
    archive_id: int,
    body: TagIdsIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[TagRef]:
    """Replace the archive's relational tag set (same write rule as PATCH)."""
    _arc, job, printer, _m, _run = await _get_visible(db, ctx, archive_id)
    require_writer(ctx, job, printer)
    return await tags_svc.set_entity_tags(db, "archive", archive_id, body.tag_ids)


@router.delete(
    "/{archive_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("admin"))],
)
async def delete_archive(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    arc, _job, printer, _m, _r = await _get_visible(db, ctx, archive_id)
    hashes = blob_hashes(arc)
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            printer_id=printer.id,
            action="archive.delete",
            payload={"archive_id": arc.id, "print_job_id": arc.print_job_id},
        )
    )
    await db.delete(arc)
    await db.flush()
    paths = await db.run_sync(lambda s: _release(s, settings, hashes))
    await db.commit()
    await anyio.to_thread.run_sync(unlink_all, paths)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _release(sync_session: Any, settings: Settings, hashes: set[str]) -> list:
    return release_blobs(sync_session, settings, hashes)[1]


@router.post("/{archive_id}/verdict", response_model=ArchiveOut)
async def post_verdict(
    archive_id: int,
    body: VerdictIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> ArchiveOut:
    arc, job, printer, _m, run = await _get_visible(db, ctx, archive_id)
    require_writer(ctx, job, printer)
    archive_confirm.apply_verdict(arc, run, body.verdict, VerdictSource(body.source), ctx.user.id)
    await db.commit()
    return (await _with_tag_refs(db, [archive_out(await _get_visible(db, ctx, archive_id))]))[0]


@router.get("/{archive_id}/runs", response_model=list[ArchiveRunOut])
async def list_runs(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[ArchiveRunOut]:
    """Every run of the archive, newest first."""
    arc = (await _get_visible(db, ctx, archive_id))[0]
    rows = (
        await db.scalars(
            select(Print)
            .where(Print.archive_id == archive_id)
            .order_by(Print.printed_at.desc(), Print.id.desc())
        )
    ).all()
    return [
        ArchiveRunOut(
            id=r.id,
            archive_id=archive_id,
            print_job_id=r.print_job_id,
            printer_id=r.printer_id,
            printer_name=r.printer_name,
            started_at=r.started_at,
            finished_at=r.finished_at,
            printed_at=r.printed_at,
            outcome=r.result,
            duration_min=r.duration_min,
            filament_g=r.filament_g,
            filament_cost=r.filament_cost,
            machine_cost=r.machine_cost,
            wear_cost=r.wear_cost,
            energy_cost=r.energy_cost,
            total_cost=r.total_cost,
            is_latest=r.print_job_id == arc.print_job_id,
        )
        for r in rows
    ]


@router.get("/{archive_id}", response_model=ArchiveOut)
async def get_archive(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> ArchiveOut:
    return (await _with_tag_refs(db, [archive_out(await _get_visible(db, ctx, archive_id))]))[0]


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


@router.get("/{archive_id}/snapshot")
async def get_snapshot(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    row = await _get_visible(db, ctx, archive_id)
    return await _serve(row, row[0].snapshot_blob, "jpg", "image/jpeg", settings, None)


_TIMELAPSE_MEDIA_TYPES = {
    BlobFormat.MP4: "video/mp4",
    BlobFormat.AVI: "video/x-msvideo",
}


@router.get("/{archive_id}/timelapse")
async def get_timelapse(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    row = await _get_visible(db, ctx, archive_id)
    blob_hash = row[0].timelapse_blob
    if blob_hash is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not available")
    blob = await db.get(Blob, blob_hash)
    if blob is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not available")
    media_type = _TIMELAPSE_MEDIA_TYPES.get(blob.format, "application/octet-stream")
    return await _serve(row, blob_hash, blob.format.value, media_type, settings, None)


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


@router.post(
    "/{archive_id}/reprint",
    response_model=PlanOut,
    dependencies=[Depends(require_perm("printers:print"))],
)
async def reprint_archive(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> PlanOut:
    """Rebuild a print plan for a finished archive's run (P5 Task 7):
    re-uses the job's own sent file when it's still in the library, else
    falls back to any library ``File`` sharing the archived 3MF's blob hash
    (the job's file may since have been deleted/replaced) -- 409 when
    neither exists. Filament overrides come from the archive's own
    ``est_filament[].tray`` so the same AMS mapping is proposed again.
    """
    arc, job, printer, _model, _run = await _get_visible(db, ctx, archive_id)
    ctx.require_printer(printer.id, "print")

    file_id: int | None = job.file_id
    if file_id is None and arc.archive_blob is not None:
        file = (
            (await db.execute(select(File).where(File.blob_hash == arc.archive_blob)))
            .scalars()
            .first()
        )
        file_id = file.id if file is not None else None
    if file_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "archived file is not in the library")

    overrides = {
        row["filament_id"]: row["tray"]
        for row in (arc.est_filament or [])
        if row.get("filament_id") is not None and row.get("tray") is not None
    }
    body = PlanIn(file_id=file_id, plate=arc.plate, printer_id=job.printer_id, overrides=overrides)
    client = aioredis.Redis.from_url(settings.redis_url)
    try:
        return await build_plan(db, settings, client, ctx, body)
    finally:
        await client.aclose()
