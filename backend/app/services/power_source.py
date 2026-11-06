"""Which source feeds a printer's energy accumulator (`ha:energy:acc:{pid}`).

Exactly one source accumulates, highest priority first, so energy is never
counted twice:

1. an ``energy_kwh`` HA link (a real meter),
2. a smart plug linked to the printer that reports watts (integrated),
3. a ``power_w`` HA link (integrated the same way).

Pure helpers over already-loaded rows so the HA poller (sync), the plug
manager (sync) and the links API (async) share one rule.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import TypedDict

from app.models.enums import HaRole
from app.models.home_assistant import HaLink
from app.models.ops import SmartPlug


class PowerShadow(TypedDict):
    """Which source shadows a ``power_w`` link: ``kind`` is ``energy_link``
    (``name`` = its entity id) or ``plug`` (``name`` = plug name, may be None)."""

    kind: str
    name: str | None


# Multiplier to watts per HA ``unit_of_measurement``; a missing unit is taken as W.
_WATT_FACTORS = {None: 1.0, "": 1.0, "W": 1.0, "kW": 1000.0, "mW": 0.001}


def state_watts(state_obj: dict | None) -> float | None:
    """Watts from an HA state object, or ``None`` when the reading is missing,
    non-numeric, non-finite, negative or in an unknown unit."""
    if not state_obj:
        return None
    unit = (state_obj.get("attributes") or {}).get("unit_of_measurement")
    factor = _WATT_FACTORS.get(unit)
    if factor is None:
        return None
    try:
        watts = float(state_obj.get("state")) * factor
    except (TypeError, ValueError):
        return None
    return watts if math.isfinite(watts) and watts >= 0 else None


def plug_provides_power(plug: SmartPlug, printer_id: int) -> bool:
    """A plug counts as the printer's power source while it is linked to it
    and its last poll produced watts without an error (so a broken plug
    hands over to a ``power_w`` link instead of leaving a gap)."""
    return (
        plug.printer_id == printer_id and plug.last_power_w is not None and plug.last_error is None
    )


def power_link_shadow(
    link: HaLink, links: Iterable[HaLink], plugs: Iterable[SmartPlug]
) -> PowerShadow | None:
    """Why a ``power_w`` link feeds nothing, or ``None`` when it is the
    active source. Names the shadowing source."""
    if link.role != HaRole.POWER_W or link.printer_id is None:
        return None
    for other in links:
        if other.role == HaRole.ENERGY_KWH and other.printer_id == link.printer_id:
            return {"kind": "energy_link", "name": other.entity_id}
    for plug in plugs:
        if plug_provides_power(plug, link.printer_id):
            return {"kind": "plug", "name": plug.name or None}
    return None
