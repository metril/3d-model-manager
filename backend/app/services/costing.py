"""Pure run-cost functions (no I/O)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.models.enums import ArchiveResult
from app.services.app_config import AppConfig

if TYPE_CHECKING:
    from app.models import Spool


@dataclass(frozen=True)
class CostRates:
    filament_per_kg: float
    machine_per_hour: float

    @classmethod
    def from_config(cls, cfg: AppConfig) -> CostRates:
        return cls(cfg.filament_cost_per_kg, cfg.machine_cost_per_hour)


@dataclass(frozen=True)
class RunCost:
    filament_cost: float | None
    machine_cost: float | None
    energy_cost: float | None
    total_cost: float | None


def charged_grams(
    est_g: float | None,
    result: ArchiveResult,
    *,
    layer: int | None,
    total_layers: int | None,
    progress_pct: float | None,
) -> float | None:
    """Grams to charge: full estimate if completed, else the printed fraction."""
    if est_g is None:
        return None
    if result == ArchiveResult.COMPLETED:
        return est_g
    if total_layers and layer is not None:
        ratio = layer / total_layers
    elif progress_pct is not None:
        ratio = progress_pct / 100
    else:
        ratio = 0.0
    return est_g * min(max(ratio, 0.0), 1.0)


def price_run(
    filament_g: float | None,
    duration_s: int | None,
    rates: CostRates,
    *,
    energy_cost: float | None = None,
    filament_cost: float | None = None,
) -> RunCost:
    """``filament_cost``, when given (P5 spool tray plan: per-spool pricing),
    replaces the flat ``filament_g/1000 x rate`` calculation entirely."""
    if filament_cost is not None:
        filament = filament_cost
    else:
        filament = None if filament_g is None else filament_g / 1000 * rates.filament_per_kg
    machine = None if duration_s is None else duration_s / 3600 * rates.machine_per_hour
    parts = [c for c in (filament, machine, energy_cost) if c is not None]
    return RunCost(filament, machine, energy_cost, sum(parts) if parts else None)


def job_energy_kwh(start: float | None, end: float | None) -> float | None:
    """The kWh consumed by a run, from the ``ha:energy:acc:{pid}`` running
    total snapshotted at job start/finish (P8 Home Assistant plan, Task 6)
    -- a plain delta, since the accumulator only ever increases through a
    counter reset (``app.services.home_assistant.counter_delta`` already
    folds that in at poll time). ``None`` when either snapshot is missing
    (HA not configured/linked for this printer) or the delta would be
    negative (a snapshot taken out of order, or a corrupt read) -- never a
    negative/garbage cost."""
    if start is None or end is None or end < start:
        return None
    return round(end - start, 4)


def filament_cost(usages: list[tuple[Spool | None, float]], global_per_kg: float) -> float | None:
    """Total filament cost across a job's charged ``(spool_or_None, grams)``
    usages (P5 spool tray plan): a spool's own ``cost_per_kg`` prices its
    grams, a spool-less tray falls back to ``global_per_kg``. ``None`` when
    there's nothing to charge (mirrors ``price_run``'s own ``None``-filament
    convention for an unknown/empty run)."""
    if not usages:
        return None
    total = 0.0
    for spool, grams in usages:
        rate = (
            spool.cost_per_kg
            if (spool is not None and spool.cost_per_kg is not None)
            else (global_per_kg)
        )
        total += grams / 1000 * rate
    return total
