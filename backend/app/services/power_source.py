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

from collections.abc import Iterable

from app.models.enums import HaRole
from app.models.home_assistant import HaLink
from app.models.ops import SmartPlug


def plug_provides_power(plug: SmartPlug, printer_id: int) -> bool:
    """A plug counts as the printer's power source while it is linked to it
    and its last poll produced watts without an error (so a broken plug
    hands over to a ``power_w`` link instead of leaving a gap)."""
    return (
        plug.printer_id == printer_id and plug.last_power_w is not None and plug.last_error is None
    )


def power_link_shadow(
    link: HaLink, links: Iterable[HaLink], plugs: Iterable[SmartPlug]
) -> str | None:
    """Why a ``power_w`` link feeds nothing, or ``None`` when it is the
    active source. The text names the shadowing source."""
    if link.role != HaRole.POWER_W or link.printer_id is None:
        return None
    for other in links:
        if other.role == HaRole.ENERGY_KWH and other.printer_id == link.printer_id:
            return f"energy link {other.entity_id}"
    for plug in plugs:
        if plug_provides_power(plug, link.printer_id):
            return f"plug {plug.name}" if plug.name else "a smart plug"
    return None
