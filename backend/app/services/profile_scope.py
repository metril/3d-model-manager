"""Printer scope for the Profiles & recipes lists: the printer's canonical
model key plus the nozzle that applies (live state -> last seen -> 0.4)."""

from __future__ import annotations

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Printer
from app.services.printer_state import read_state_async
from app.services.slicer_profiles import model_key

NozzleSource = Literal["override", "live", "last_seen", "assumed"]
DEFAULT_NOZZLE_MM = 0.4


def _positive(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def resolve_nozzle(
    live: float | None, last_seen: float | None, override: float | None = None
) -> tuple[float, NozzleSource]:
    """Override -> live state -> `Printer.nozzle_mm` -> 0.4 (assumed)."""
    if override is not None:
        return override, "override"
    if (v := _positive(live)) is not None:
        return v, "live"
    if (v := _positive(last_seen)) is not None:
        return v, "last_seen"
    return DEFAULT_NOZZLE_MM, "assumed"


async def scope_for_printer(
    session: AsyncSession, redis, printer_id: int, nozzle_override: float | None = None
) -> tuple[str | None, float, NozzleSource]:
    """`(printer_key, nozzle, nozzle_source)` for a registered printer.
    Raises `LookupError` when the printer doesn't exist."""
    printer = await session.get(Printer, printer_id)
    if printer is None:
        raise LookupError(printer_id)
    live = None
    if nozzle_override is None and redis is not None:
        state = await read_state_async(redis, printer_id)
        live = (state or {}).get("nozzle_mm")
    nozzle, source = resolve_nozzle(live, printer.nozzle_mm, nozzle_override)
    return model_key(printer.model), nozzle, source
