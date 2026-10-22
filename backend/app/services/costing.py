"""Pure run-cost functions (no I/O)."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import ArchiveResult
from app.services.app_config import AppConfig


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
) -> RunCost:
    filament = None if filament_g is None else filament_g / 1000 * rates.filament_per_kg
    machine = None if duration_s is None else duration_s / 3600 * rates.machine_per_hour
    parts = [c for c in (filament, machine, energy_cost) if c is not None]
    return RunCost(filament, machine, energy_cost, sum(parts) if parts else None)
