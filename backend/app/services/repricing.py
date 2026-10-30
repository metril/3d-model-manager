"""Reprice past runs' filament cost at the spools' CURRENT price per kg.

A run's ``filament_cost`` is frozen when it finishes; correcting a spool's
``cost_per_kg`` afterwards changed nothing. This rebuilds ``filament_cost``
(and ``total_cost``) from the run's ``spool_usage`` rows; machine, wear and
energy costs are left exactly as recorded."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Spool, SpoolUsage
from app.models.library import Print


@dataclass
class RepriceResult:
    candidates: int = 0
    repriced: int = 0
    delta: float = 0.0


async def reprice_runs(
    db: AsyncSession, global_per_kg: float, *, dry_run: bool = False
) -> RepriceResult:
    usage_rows = (
        await db.execute(
            select(SpoolUsage.print_job_id, SpoolUsage.grams, Spool.cost_per_kg)
            .join(Spool, Spool.id == SpoolUsage.spool_id)
            .where(SpoolUsage.print_job_id.is_not(None))
        )
    ).all()
    by_job: dict[int, list[tuple[float, float | None]]] = defaultdict(list)
    for job_id, grams, per_kg in usage_rows:
        by_job[job_id].append((grams, per_kg))
    out = RepriceResult()
    if not by_job:
        return out
    runs = (await db.execute(select(Print).where(Print.print_job_id.in_(by_job)))).scalars().all()
    out.candidates = len(runs)
    for run in runs:
        rows = by_job[run.print_job_id]
        new_fc = sum(g / 1000 * (p if p is not None else global_per_kg) for g, p in rows)
        used = sum(g for g, _ in rows)
        if run.filament_g is not None and run.filament_g > used:
            # Spool-less remainder (tray with no spool) stays at the global rate.
            new_fc += (run.filament_g - used) / 1000 * global_per_kg
        old_fc = run.filament_cost or 0.0
        if abs(new_fc - old_fc) < 1e-6:
            continue
        out.repriced += 1
        out.delta += new_fc - old_fc
        if dry_run:
            continue
        run.filament_cost = new_fc
        parts = [
            c
            for c in (run.filament_cost, run.machine_cost, run.energy_cost, run.wear_cost)
            if c is not None
        ]
        run.total_cost = sum(parts) if parts else None
    if not dry_run:
        await db.commit()
    out.delta = round(out.delta, 4)
    return out
