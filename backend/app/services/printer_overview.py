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
from app.models.library import PrintQueueEntry
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
    SpoolBriefOut,
    status_from_state,
)
from app.services import ha_state
from app.services import queue as queue_service
from app.services import spool_inventory as sinv
from app.services.printer_status import active_job_rows
from app.services.queue_filaments import details_for_entries


async def total_print_hours(db: AsyncSession, printer_ids: list[int]) -> dict[int, float]:
    """Hours of archived print time per printer (one grouped query)."""
    if not printer_ids:
        return {}
    stmt = (
        select(PrintJob.printer_id, func.coalesce(func.sum(PrintArchive.duration_s), 0))
        .join(PrintJob, PrintJob.id == PrintArchive.print_job_id)
        .where(PrintJob.printer_id.in_(printer_ids))
        .group_by(PrintJob.printer_id)
    )
    return {pid: round(secs / 3600, 1) for pid, secs in (await db.execute(stmt)).all()}


def _can(ctx: AuthContext, printer_id: int, kind: str) -> bool:
    if ctx.is_admin:
        return True
    acl = ctx.printer_acl.get(printer_id)
    flag = acl is not None and (acl.can_print if kind == "print" else acl.can_control)
    return f"printers:{kind}" in ctx.perms and flag


async def _statuses(
    db: AsyncSession, redis: Redis, printer_ids: list[int]
) -> dict[int, PrinterStatusOut]:
    if not printer_ids:
        return {}
    raws = await redis.mget([state_key(pid) for pid in printer_ids])
    smap = await sinv.slot_map(db, printer_ids)
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
            status_out = status_from_state(state)
            for t in status_out.trays:
                spool = smap.get((pid, t.slot))
                if spool is not None:
                    t.spool = SpoolBriefOut.from_model(spool)
            out[pid] = status_out
    return out


async def _ha_alerts(redis: Redis, printer_ids: list[int]) -> dict[int, tuple[str, bool]]:
    """``{pid: (reason, held)}`` for every printer with a currently-firing
    HA alert (``ha:alert:{pid}``, P8), via one batched ``MGET`` -- never one
    read per printer. A missing/corrupt key is simply absent (fail open,
    matching ``ha_state.read_alert``'s own TTL-expiry story)."""
    if not printer_ids:
        return {}
    raws = await redis.mget([ha_state.alert_key(pid) for pid in printer_ids])
    out: dict[int, tuple[str, bool]] = {}
    for pid, raw in zip(printer_ids, raws, strict=True):
        if not raw:
            continue
        try:
            alert = json.loads(raw)
        except (TypeError, ValueError):
            continue
        reason = alert.get("reason")
        if reason:
            out[pid] = (reason, bool(alert.get("hold")))
    return out


def _plate_thumb_url(blob_hash: str | None, rel_path: str | None, plate: int | None) -> str | None:
    """Plate thumbnail of a stored 3MF (rowless derivative; 404 -> card falls back)."""
    if not blob_hash or not rel_path or not rel_path.lower().endswith(".3mf"):
        return None
    return f"/api/blobs/{blob_hash}/plates/{plate if plate and plate > 0 else 1}/thumb"


async def _active_jobs(
    db: AsyncSession, settings: Settings, printer_ids: list[int]
) -> dict[int, ActiveJobOut]:
    """Latest ``print_jobs`` row per printer whose state is still "in
    flight", resolved onto its file's model via ``DISTINCT ON
    (printer_id)`` -- one query regardless of printer count."""
    if not printer_ids:
        return {}
    found = await active_job_rows(db, printer_ids)
    rows = [
        (job, model, f.blob_hash if f else None, f.rel_path if f else None)
        for job, model, f in found.values()
    ]
    entries = list(
        (
            await db.execute(
                select(PrintQueueEntry).where(
                    PrintQueueEntry.print_job_id.in_([r[0].id for r in rows])
                )
            )
        )
        .scalars()
        .all()
    )
    details = await details_for_entries(db, settings, entries)
    # The entry's estimate covers every copy; a job is one copy.
    cost_by_job = {
        e.print_job_id: None
        if details[e.id].estimated_cost is None
        else details[e.id].estimated_cost / max(e.copies, 1)
        for e in entries
        if e.id in details
    }
    out: dict[int, ActiveJobOut] = {}
    for job, model, blob_hash, rel_path in rows:
        base = PrintJobOut.from_model(job).model_dump()
        out[job.printer_id] = ActiveJobOut(
            **base,
            plate=job.plate,
            model_id=model.id if model else None,
            model_slug=model.slug if model else None,
            model_name=model.name if model else None,
            thumbnail_url=_plate_thumb_url(blob_hash, rel_path, job.plate),
            estimated_cost=cost_by_job.get(job.id),
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

    statuses = await _statuses(db, redis, printer_ids)
    active_jobs = await _active_jobs(db, settings, printer_ids)
    last_runs = await _last_runs(db, printer_ids)
    ha_alerts = await _ha_alerts(redis, printer_ids)
    stalled = (
        {
            pid
            for pid, v in zip(
                printer_ids,
                await redis.mget([f"plate:stalled:{pid}" for pid in printer_ids]),
                strict=True,
            )
            if v
        }
        if printer_ids
        else set()
    )
    hours = await total_print_hours(db, printer_ids)

    # P6 Task 8: one batched windowed query (per-target ``row_number()``,
    # not one query per printer) returns up to 3 non-terminal entries per
    # printer plus up to 3 unassigned ones; split back out in Python so
    # each printer's card shows only ITS up-next items and the top-level
    # list keeps only the unassigned ones.
    up_next_entries = await queue_service.list_queue(
        db, settings, printer_ids=printer_ids, limit_per_printer=3, redis=redis
    )
    up_next_by_printer: dict[int, list] = {}
    unassigned: list = []
    for entry in up_next_entries:
        target = entry.printer_id if entry.printer_id is not None else entry.assigned_printer_id
        if target is None:
            unassigned.append(entry)
        else:
            up_next_by_printer.setdefault(target, []).append(entry)

    def _printer_out(p: Printer) -> PrinterOut:
        out = PrinterOut.from_model(p)
        out.total_print_hours = hours.get(p.id, 0.0)
        return out

    items = [
        PrinterOverviewItem(
            printer=_printer_out(p),
            status=statuses.get(p.id, PrinterStatusOut(online=False)),
            active_job=active_jobs.get(p.id),
            last_runs=last_runs.get(p.id, []),
            can_print=_can(ctx, p.id, "print"),
            can_control=_can(ctx, p.id, "control"),
            up_next=up_next_by_printer.get(p.id, [])[:3],
            ha_alert=ha_alerts.get(p.id, (None, False))[0],
            ha_held=ha_alerts.get(p.id, (None, False))[1],
            plate_check_stalled=p.id in stalled,
        )
        for p in printers
    ]

    recent_runs = await _recent_runs(db, printer_ids)

    return PrintersOverviewOut(printers=items, up_next=unassigned[:3], recent_runs=recent_runs)
