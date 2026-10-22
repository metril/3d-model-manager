"""Per-user printers-overview dashboard (P3 Task 1): one call assembling
every visible printer's live status, active job and recent history, plus
the global queue's head and cross-printer recent runs -- built for a single
dashboard render rather than N+1 per-printer round trips. Query count stays
constant in the number of VISIBLE printers (one query each for the printer
list, active jobs, last-5-per-printer runs and the top-10 recent runs; the
queue's own overhead is independent of printer count too).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.archives import archive_out, archive_select
from app.api.deps import AuthContext, visible_printer_ids
from app.config import Settings
from app.models.library import File, Model, Revision
from app.models.printing import PrintArchive, Printer, PrintJob
from app.printers.base import state_key
from app.schemas.archives import ArchiveOut
from app.schemas.printers import (
    ActiveJobOut,
    PrinterOut,
    PrinterOverviewItem,
    PrintersOverviewOut,
    PrinterStatusOut,
    PrintJobOut,
)
from app.services import queue as queue_service

_ACTIVE_STATES = ("queued", "uploading", "starting", "printing", "paused")

# Mirrors app.api.printers._STATE_FIELDS -- kept as a separate copy rather
# than an import, so this module (used by the API layer) never has to
# import back into app.api.printers.
_STATE_FIELDS = (
    "gcode_state",
    "mc_percent",
    "layer_num",
    "total_layer_num",
    "mc_remaining_time",
    "print_error",
    "nozzle_temper",
    "bed_temper",
    "subtask_name",
    "wifi_signal",
)


def _can(ctx: AuthContext, printer_id: int, kind: str) -> bool:
    if ctx.is_admin:
        return True
    acl = ctx.printer_acl.get(printer_id)
    flag = acl is not None and (acl.can_print if kind == "print" else acl.can_control)
    return f"printers:{kind}" in ctx.perms and flag


async def _statuses(redis: Redis, printer_ids: list[int]) -> dict[int, PrinterStatusOut]:
    if not printer_ids:
        return {}
    raws = await redis.mget([state_key(pid) for pid in printer_ids])
    out: dict[int, PrinterStatusOut] = {}
    for pid, raw in zip(printer_ids, raws, strict=True):
        state = None
        if raw:
            try:
                state = json.loads(raw)
            except (TypeError, ValueError):
                state = None  # corrupt value -> treat as absent (fail closed)
        if not state:
            out[pid] = PrinterStatusOut(online=False)
        else:
            out[pid] = PrinterStatusOut(
                online=True,
                trays=state.get("trays") or [],
                **{k: state.get(k) for k in _STATE_FIELDS},
            )
    return out


async def _active_jobs(db: AsyncSession, printer_ids: list[int]) -> dict[int, ActiveJobOut]:
    """Latest ``print_jobs`` row per printer whose state is still "in
    flight", resolved onto its file's model via ``DISTINCT ON
    (printer_id)`` -- one query regardless of printer count."""
    if not printer_ids:
        return {}
    stmt = (
        select(PrintJob, Model)
        .outerjoin(File, File.id == PrintJob.file_id)
        .outerjoin(Revision, Revision.id == File.revision_id)
        .outerjoin(Model, Model.id == Revision.model_id)
        .where(PrintJob.printer_id.in_(printer_ids))
        .where(PrintJob.state.in_(_ACTIVE_STATES))
        .distinct(PrintJob.printer_id)
        .order_by(PrintJob.printer_id, PrintJob.id.desc())
    )
    rows = (await db.execute(stmt)).all()
    out: dict[int, ActiveJobOut] = {}
    for job, model in rows:
        base = PrintJobOut.from_model(job).model_dump()
        out[job.printer_id] = ActiveJobOut(
            **base,
            plate=job.plate,
            model_id=model.id if model else None,
            model_slug=model.slug if model else None,
            model_name=model.name if model else None,
        )
    return out


def _sort_key(row: object) -> tuple[datetime, int]:
    archive, job = row[0], row[1]
    return (job.finished_at or datetime.min.replace(tzinfo=UTC), archive.id)


async def _last_runs(db: AsyncSession, printer_ids: list[int]) -> dict[int, list[ArchiveOut]]:
    """<=5 newest-finished archives per printer, via a row_number() window
    over a single query -- never one query per printer."""
    if not printer_ids:
        return {}
    rn = func.row_number().over(
        partition_by=PrintJob.printer_id,
        order_by=(PrintJob.finished_at.desc().nulls_last(), PrintArchive.id.desc()),
    )
    inner = (
        select(PrintArchive.id.label("archive_id"), rn.label("rn"))
        .join(PrintJob, PrintJob.id == PrintArchive.print_job_id)
        .where(PrintJob.printer_id.in_(printer_ids))
    ).subquery()
    ranked_ids = select(inner.c.archive_id).where(inner.c.rn <= 5)

    stmt = archive_select().where(PrintArchive.id.in_(ranked_ids))
    rows = (await db.execute(stmt)).all()

    grouped: dict[int, list] = {}
    for row in rows:
        grouped.setdefault(row[1].printer_id, []).append(row)
    out: dict[int, list[ArchiveOut]] = {}
    for pid, rs in grouped.items():
        rs.sort(key=_sort_key, reverse=True)
        out[pid] = [archive_out(r) for r in rs[:5]]
    return out


async def _recent_runs(db: AsyncSession, printer_ids: list[int]) -> list[ArchiveOut]:
    """<=10 newest-finished archives across every visible printer."""
    if not printer_ids:
        return []
    stmt = (
        archive_select()
        .where(PrintJob.printer_id.in_(printer_ids))
        .order_by(PrintJob.finished_at.desc().nulls_last(), PrintArchive.id.desc())
        .limit(10)
    )
    rows = (await db.execute(stmt)).all()
    return [archive_out(r) for r in rows]


async def build_overview(
    db: AsyncSession, redis: Redis, settings: Settings, ctx: AuthContext
) -> PrintersOverviewOut:
    visible = visible_printer_ids(ctx)
    stmt = select(Printer).order_by(Printer.name)
    if visible is not None:
        stmt = stmt.where(Printer.id.in_(visible))
    printers = list((await db.execute(stmt)).scalars().all())
    printer_ids = [p.id for p in printers]

    statuses = await _statuses(redis, printer_ids)
    active_jobs = await _active_jobs(db, printer_ids)
    last_runs = await _last_runs(db, printer_ids)

    items = [
        PrinterOverviewItem(
            printer=PrinterOut.from_model(p),
            status=statuses.get(p.id, PrinterStatusOut(online=False)),
            active_job=active_jobs.get(p.id),
            last_runs=last_runs.get(p.id, []),
            can_print=_can(ctx, p.id, "print"),
            can_control=_can(ctx, p.id, "control"),
        )
        for p in printers
    ]

    up_next = await queue_service.list_queue(db, settings, limit=3)
    recent_runs = await _recent_runs(db, printer_ids)

    return PrintersOverviewOut(printers=items, up_next=up_next, recent_runs=recent_runs)
