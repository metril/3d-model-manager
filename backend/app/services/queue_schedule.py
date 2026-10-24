"""T4 queue scheduling helpers used by ``app.services.dispatcher``: the
previous-success gate, the beat-driven preheat/soak stage machine, bed
keep-warm and auto power-off. Kept out of ``dispatcher`` so that module only
gains small call sites. Everything here is SYNC (worker side) and never
blocks: a stage that is not ready yet simply returns "not yet" and the next
beat tick re-evaluates."""

from __future__ import annotations

import json
import logging
import time

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.audit_log import AuditLog
from app.models.enums import ArchiveResult, PrintJobState, QueueState
from app.models.library import Model, PrintQueueEntry
from app.models.ops import SmartPlug
from app.models.printing import PrintArchive, Printer, PrintJob
from app.printers.base import command_channel
from app.printers.capabilities import capabilities_for
from app.services.notify import events
from app.services.printer_state import read_state_sync
from app.services.queue_preheat import QueuePreheatConfig

log = logging.getLogger(__name__)

REACHED_TOL_C = 2
PREVIOUS_FAILED = "previous_failed"
_STATE_TTL_S = 7200
AUTO_OFF_PREFIX = "queue:autooff:"
_FAILED_ARCHIVE = (ArchiveResult.FAILED, ArchiveResult.CANCELED, ArchiveResult.STOPPED)


def _now() -> float:
    return time.time()


def preheat_key(entry_id: int) -> str:
    return f"queue:preheat:{entry_id}"


def keepwarm_key(printer_id: int) -> str:
    return f"queue:keepwarm:{printer_id}"


def autooff_key(printer_id: int) -> str:
    return f"{AUTO_OFF_PREFIX}{printer_id}"


def send_command(client: Redis, printer_id: int, command: str, args: dict) -> None:
    """Fire-and-forget onto printerd's command channel (same wire format as
    the API's ``run_command``, minus the result wait)."""
    client.publish(command_channel(printer_id), json.dumps({"command": command, "args": args}))


def _load(client: Redis, key: str) -> dict | None:
    raw = client.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _save(client: Redis, key: str, rec: dict, ex: int = _STATE_TTL_S) -> None:
    client.set(key, json.dumps(rec), ex=ex)


# -- previous-success gate ---------------------------------------------------


def _verdict(session: Session, prev: PrintQueueEntry) -> str | None:
    """``"failed"``, ``"ok"`` or ``None`` (neutral: look further back)."""
    if prev.state == QueueState.SKIPPED:
        return "failed"
    job = session.get(PrintJob, prev.print_job_id) if prev.print_job_id is not None else None
    if prev.state == QueueState.FAILED:
        if job is not None and job.state == PrintJobState.CANCELED:
            return None  # a cancel is the operator's call, not a print fault
        return "failed"
    if job is not None:
        archive = session.scalar(select(PrintArchive).where(PrintArchive.print_job_id == job.id))
        if archive is not None and archive.result in _FAILED_ARCHIVE:
            return "failed"
    return "ok"


def previous_failed(session: Session, item: PrintQueueEntry, printer_id: int) -> bool:
    """True when ``item`` requires a successful predecessor and the latest
    non-neutral terminal entry on ``printer_id`` (batch-scoped when the item
    is in a batch) failed."""
    if not item.require_previous_success:
        return False
    stmt = select(PrintQueueEntry).where(
        PrintQueueEntry.id != item.id,
        PrintQueueEntry.assigned_printer_id == printer_id,
        PrintQueueEntry.state.in_((QueueState.DONE, QueueState.FAILED, QueueState.SKIPPED)),
    )
    if item.batch_id is not None:
        stmt = stmt.where(PrintQueueEntry.batch_id == item.batch_id)
    stmt = stmt.order_by(
        PrintQueueEntry.print_job_id.desc().nulls_last(), PrintQueueEntry.id.desc()
    ).limit(50)
    for prev in session.scalars(stmt):
        verdict = _verdict(session, prev)
        if verdict is not None:
            return verdict == "failed"
    return False


def skip_previous_failed(session: Session, item: PrintQueueEntry, printer_id: int) -> None:
    item.state = QueueState.SKIPPED
    item.hold_reason = PREVIOUS_FAILED
    item.assigned_printer_id = printer_id
    item.last_error = "Previous print failed or was aborted"
    session.commit()
    model = session.get(Model, item.model_id)
    printer = session.get(Printer, printer_id)
    events.emit(
        "queue_item_skipped",
        printer_id,
        {
            "name": model.name if model is not None else "print",
            "reason": item.last_error,
            "queue_id": item.id,
            "printer_name": printer.name if printer is not None else None,
        },
    )


# -- preheat / soak ----------------------------------------------------------


def preheat_plan(
    cfg: QueuePreheatConfig, item: PrintQueueEntry, printer: Printer, filament_types: list[str]
) -> tuple[str, int] | None:
    """``("chamber"|"bed", deg C)`` or ``None`` when no preheat applies."""
    if item.preheat_override == "off":
        return None
    if item.preheat_override != "on" and not cfg.enabled:
        return None
    types = [t.upper() for t in filament_types if t]
    if capabilities_for(printer.model, printer.serial).chamber_heater:
        explicit = item.preheat_chamber_target
        target = (
            explicit
            if explicit is not None
            else max((cfg.chamber_targets.get(t, 0) for t in types), default=0)
        )
        return ("chamber", target) if target > 0 else None
    target = max((cfg.bed_targets.get(t, 0) for t in types), default=0)
    return ("bed", target) if target > 0 else None


def preheat_gate(
    session: Session,
    client: Redis,
    cfg: QueuePreheatConfig,
    item: PrintQueueEntry,
    printer: Printer,
    filament_types: list[str],
) -> bool:
    """True = go ahead and dispatch now; False = still preheating/soaking
    (``item.stage`` says which)."""
    plan = preheat_plan(cfg, item, printer, filament_types)
    if plan is None:
        if item.stage is not None:
            item.stage = None
            session.commit()
        return True
    kind, value = plan
    key = preheat_key(item.id)
    now = _now()
    rec = _load(client, key)
    if rec is None or rec.get("printer_id") != printer.id:
        send_command(client, printer.id, "temperature", {"target": kind, "value": value})
        _save(client, key, {"printer_id": printer.id, "phase": "preheating", "started": now})
        item.stage = "preheating"
        session.commit()
        return False
    if rec["phase"] == "preheating":
        state = read_state_sync(client, printer.id) or {}
        cur = state.get("chamber_temper" if kind == "chamber" else "bed_temper")
        reached = cur is not None and float(cur) >= value - REACHED_TOL_C
        if not (reached or now - rec["started"] >= cfg.max_wait_s):
            return False
        if cfg.soak_s > 0:
            rec.update(phase="soaking", soak_started=now)
            _save(client, key, rec)
            item.stage = "soaking"
            session.commit()
            return False
    elif now - rec["soak_started"] < cfg.soak_s:
        return False
    client.delete(key)
    item.stage = None
    session.commit()
    return True


# -- keep-warm ---------------------------------------------------------------


def run_keep_warm(
    session: Session, client: Redis, cfg: QueuePreheatConfig, pending_ids: set[int]
) -> None:
    """After a print finishes with more work queued for the same printer, hold
    the bed at ``keep_warm_bed_c``; release it (``M140 S0``) when nothing is
    queued or ``keep_warm_max_min`` has passed."""
    if not cfg.keep_warm_enabled:
        return
    for printer in session.scalars(select(Printer).where(Printer.enabled.is_(True))).all():
        key = keepwarm_key(printer.id)
        rec = _load(client, key)
        gstate = (read_state_sync(client, printer.id) or {}).get("gcode_state")
        if gstate not in ("FINISH", "IDLE"):
            if rec is not None:
                client.delete(key)  # a new print took over the bed
            continue
        now = _now()
        if rec is None:
            if gstate == "FINISH" and printer.id in pending_ids:
                send_command(
                    client,
                    printer.id,
                    "temperature",
                    {"target": "bed", "value": cfg.keep_warm_bed_c},
                )
                _save(client, key, {"since": now, "off": False}, ex=86400)
            continue
        if rec.get("off"):
            continue
        if printer.id not in pending_ids or now - rec["since"] > cfg.keep_warm_max_min * 60:
            send_command(client, printer.id, "temperature", {"target": "bed", "value": 0})
            rec["off"] = True
            _save(client, key, rec, ex=86400)


# -- auto power-off ----------------------------------------------------------


def note_finished(client: Redis | None, item: PrintQueueEntry) -> None:
    """A print ended (done/failed): remember the power-off intent until the
    printer is idle and cold."""
    if client is not None and item.auto_off_after and item.assigned_printer_id is not None:
        client.set(autooff_key(item.assigned_printer_id), item.id, ex=_STATE_TTL_S)


def process_auto_off(session: Session, settings: Settings, client: Redis, wanted: set[int]) -> int:
    """Power off plugs for printers with a pending ``auto_off_after`` intent.
    Cancelled when more queued work targets the printer (that entry's own
    flag then governs). Returns the number of plugs turned off."""
    from app.services.smart_plugs import manager

    n = 0
    for raw in client.scan_iter(f"{AUTO_OFF_PREFIX}*"):
        key = raw.decode() if isinstance(raw, bytes) else raw
        printer_id = int(key.removeprefix(AUTO_OFF_PREFIX))
        queue_id = client.get(key)
        if printer_id in wanted:
            client.delete(key)
            continue
        plug = session.scalar(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
        if plug is None or plug.last_state == "off":
            client.delete(key)
            continue
        if not manager.can_power_off(read_state_sync(client, printer_id)):
            continue  # still hot / printing: re-check next tick
        try:
            manager.command(session, settings, plug, "off")
        except Exception:  # noqa: BLE001 -- retry next tick, never stall dispatch
            log.exception("queue auto-off failed for printer %s", printer_id)
            session.rollback()
            continue
        session.add(
            AuditLog(
                printer_id=printer_id,
                action="queue_auto_off",
                payload={"queue_id": int(queue_id) if queue_id else None},
            )
        )
        session.commit()
        client.delete(key)
        n += 1
    return n
