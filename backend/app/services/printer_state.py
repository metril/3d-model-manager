"""Preflight state helper (SPEC "Printer integration"; RESEARCH §4). The
send flow (API pre-check + the ``send_to_printer`` task's authoritative
re-check) both read printerd's merged state from Redis (``app.printers.base
.state_key``) and must agree a print may only start while ``gcode_state`` is
one of ``ALLOWED_PREFLIGHT_STATES`` -- an absent/unparseable state is always
treated as NOT ready (printerd not running, or the printer has never
reported in), never as an implicit pass.
"""

from __future__ import annotations

import json

from app.printers.base import state_key

ALLOWED_PREFLIGHT_STATES = {"IDLE", "FINISH", "FAILED"}  # RESEARCH §4

# printer_error stamped on a STARTING job the printer never picked up (reconcile
# timeout). Also the marker archive_sweep uses to skip it: nothing ran.
START_TIMEOUT_ERROR = "printer did not start the job"

# P6 Task 7: the dispatcher's own, narrower "idle" set -- a manual send may
# retry a FAILED printer (ALLOWED_PREFLIGHT_STATES above), but the
# dispatcher must never pick one on its own; a previous failure needs a
# person's eyes, not another silent auto-retry onto the same machine.
DISPATCH_IDLE_STATES = {"IDLE", "FINISH"}


def failure_acknowledged(printer) -> bool:
    """A person confirmed the plate (``POST /printers/{id}/clear-plate``) AFTER
    printerd last saw the printer enter FAILED. Independent of the
    ``require_plate_clear`` gate: a failed print always needs a person's eyes."""
    failed, cleared = printer.failed_at, printer.plate_cleared_at
    return (
        failed is not None
        and cleared is not None
        and cleared >= failed
        and not printer.awaiting_plate_clear
    )


def failed_hold_reason(printer, gcode_state: str | None) -> str | None:
    """``failed_unacknowledged`` while the printer reports FAILED -- or sits
    idle after a print it rejected with an error (``failed_at`` stamped by
    printerd) -- and nobody has confirmed the plate since. An idle printer
    with no ``failed_at`` (a stale ``print_error``) never holds."""
    if failure_acknowledged(printer):
        return None
    if gcode_state == "FAILED" or (
        gcode_state in DISPATCH_IDLE_STATES and printer.failed_at is not None
    ):
        return "failed_unacknowledged"
    return None


def preflight_ok(state: dict | None) -> bool:
    return bool(state) and state.get("gcode_state") in ALLOWED_PREFLIGHT_STATES


def dispatch_idle(state: dict | None) -> bool:
    return bool(state) and state.get("gcode_state") in DISPATCH_IDLE_STATES


def read_state_sync(redis_client, printer_id: int) -> dict | None:
    raw = redis_client.get(state_key(printer_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None  # corrupt/partial value -> treat as absent (fail closed)


async def read_states_async(redis_client, printer_ids: list[int]) -> dict[int, dict | None]:
    """One ``MGET`` for many printers (same parse rules as ``read_state_async``)."""
    if not printer_ids:
        return {}
    raws = await redis_client.mget([state_key(pid) for pid in printer_ids])
    out: dict[int, dict | None] = {}
    for pid, raw in zip(printer_ids, raws, strict=True):
        try:
            out[pid] = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            out[pid] = None
    return out


async def read_state_async(redis_client, printer_id: int) -> dict | None:
    raw = await redis_client.get(state_key(printer_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None  # corrupt/partial value -> treat as absent (fail closed)
