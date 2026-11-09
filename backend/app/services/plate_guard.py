"""Plate/failed-print holds for DIRECT sends (the queue's dispatcher enforces
the same holds itself). Shared by ``/print/send``, ``/printers/{id}/print``,
SD-card print and the ``clear-plate`` route."""

from __future__ import annotations

from datetime import UTC, datetime

import anyio
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext
from app.models import AuditLog, Printer
from app.services import hooks
from app.services.printer_state import failed_hold_reason
from app.services.queue_settings import get_queue_config

PLATE_NOT_CLEARED = "plate_not_cleared"
PRINTER_FAILED = "printer_failed"

MESSAGES = {
    PLATE_NOT_CLEARED: "The plate hasn't been cleared since the last print.",
    PRINTER_FAILED: "The last print failed and hasn't been acknowledged.",
}


async def direct_hold(db: AsyncSession, printer: Printer, state: dict | None) -> str | None:
    """``printer_failed`` (always) or ``plate_not_cleared`` (only while
    ``queue.require_plate_clear`` is on), else None."""
    if failed_hold_reason(printer, (state or {}).get("gcode_state")) is not None:
        return PRINTER_FAILED
    if printer.awaiting_plate_clear and (await get_queue_config(db)).require_plate_clear:
        return PLATE_NOT_CLEARED
    return None


def acknowledge_plate(db: AsyncSession, ctx: AuthContext, printer: Printer) -> None:
    """Record the plate as cleared + audit (caller commits, then ``fire_cleared``)."""
    printer.awaiting_plate_clear = False
    printer.plate_cleared_at = datetime.now(UTC)
    db.add(
        AuditLog(
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            printer_id=printer.id,
            action="clear_plate",
            payload={},
        )
    )


async def fire_cleared(printer_id: int, ctx: AuthContext) -> None:
    await anyio.to_thread.run_sync(hooks.plate_cleared, printer_id, ctx.user.id)  # sync DB hook


async def enforce_direct_hold(
    db: AsyncSession, ctx: AuthContext, printer: Printer, state: dict | None, confirmed: bool
) -> bool:
    """409 ``{code, message}`` when a hold applies and the caller didn't confirm.
    A confirm needs printer "control" access (403 otherwise) and stages the
    acknowledgement on the session; returns True so the caller commits it with
    its own write and then calls ``fire_cleared``."""
    code = await direct_hold(db, printer, state)
    if code is None:
        return False
    if not confirmed:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": code, "message": MESSAGES[code]}
        )
    ctx.require_printer(printer.id, "control")
    acknowledge_plate(db, ctx, printer)
    return True
