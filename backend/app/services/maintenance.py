"""Maintenance tasks (Q7): per-model default task sets, due-status maths.

Hours come from archived print time (``printer_overview.total_print_hours``'
logic); a task is due once either interval has elapsed since its last
completion (or since creation / zero hours when never done)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MaintenanceTask, Printer, PrintJob
from app.models.printing import PrintArchive
from app.printers.capabilities import capabilities_for

Status = Literal["ok", "soon", "overdue"]
SOON_FRACTION = 0.9

# (name, interval_hours, interval_days, notes)
_COMMON = [
    ("Clean build plate", 50.0, None, "Wash with dish soap / IPA."),
    ("Lubricate linear rails / rods", 200.0, None, "A thin film of grease or oil."),
    ("Inspect nozzle & hotend", 300.0, None, "Clean or replace on wear or clogs."),
    ("Check belts tension", 500.0, None, "Tension should be firm, not tight."),
    ("Clean fans & vents", None, 90, "Dust causes overheating."),
]
_RODS = ("Clean carbon rods", 200.0, None, "Wipe, then lubricate (X1 / P1 series).")
_LIDAR = ("Clean lidar & bed sensors", 300.0, None, "Lint-free cloth.")
_AMS = ("Clean AMS feeders", 200.0, None, "Remove filament dust.")
_CHAMBER = ("Check chamber filter", 500.0, None, "Replace the carbon filter if saturated.")


def default_tasks(model: str | None, serial: str | None = None) -> list[tuple]:
    caps = capabilities_for(model, serial)
    key = caps.model_key or ""
    tasks = list(_COMMON)
    if key.startswith(("x1", "p1")):
        tasks.append(_RODS)
    if caps.lidar:
        tasks.append(_LIDAR)
    if caps.chamber_fan:
        tasks.append(_CHAMBER)
    if caps.ams_drying or key.startswith(("x1", "p1", "p2", "h2")):
        tasks.append(_AMS)
    return tasks


async def seed_defaults(db: AsyncSession, printer: Printer) -> int:
    """Insert the model's default tasks (once; no-op if any exist)."""
    exists = await db.scalar(
        select(func.count())
        .select_from(MaintenanceTask)
        .where(MaintenanceTask.printer_id == printer.id)
    )
    if exists:
        return 0
    rows = default_tasks(printer.model, printer.serial)
    baseline = await printer_hours(db, printer.id)
    for name, interval_h, days, notes in rows:
        db.add(
            MaintenanceTask(
                printer_id=printer.id,
                name=name,
                interval_hours=interval_h,
                interval_days=days,
                last_done_hours=baseline,
                notes=notes,
            )
        )
    await db.commit()
    return len(rows)


async def printer_hours(db: AsyncSession, printer_id: int) -> float:
    secs = await db.scalar(
        select(func.coalesce(func.sum(PrintArchive.duration_s), 0))
        .join(PrintJob, PrintJob.id == PrintArchive.print_job_id)
        .where(PrintJob.printer_id == printer_id)
    )
    return round((secs or 0) / 3600, 1)


@dataclass(frozen=True)
class Due:
    status: Status
    hours_since: float | None
    days_since: int | None
    hours_remaining: float | None
    days_remaining: int | None


def due_status(task: MaintenanceTask, hours_now: float, now: datetime | None = None) -> Due:
    now = now or datetime.now(UTC)
    ratios: list[float] = []
    hours_since = days_since = hours_rem = days_rem = None
    if task.interval_hours:
        hours_since = round(max(hours_now - (task.last_done_hours or 0.0), 0.0), 1)
        hours_rem = round(task.interval_hours - hours_since, 1)
        ratios.append(hours_since / task.interval_hours)
    if task.interval_days:
        since = task.last_done_at or task.created_at
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        days_since = max((now - since).days, 0)
        days_rem = task.interval_days - days_since
        ratios.append(days_since / task.interval_days)
    worst = max(ratios, default=0.0)
    status: Status = "overdue" if worst >= 1 else "soon" if worst >= SOON_FRACTION else "ok"
    return Due(status, hours_since, days_since, hours_rem, days_rem)
