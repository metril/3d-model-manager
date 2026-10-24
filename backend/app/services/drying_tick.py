"""T1 ``drying.tick`` (beat, 60 s): scheduled runs, ambient / queue
auto-drying, queue hold, drying edges (notify + plug auto-off).

Sync (celery worker). Printer telemetry comes from the Redis state printerd
publishes; commands go out on the printer's command channel exactly like the
API's ``run_command`` (fire-and-forget: success is judged from telemetry)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuditLog, Printer, ScheduledDrying, SmartPlug
from app.printers.base import command_channel
from app.printers.capabilities import capabilities_for
from app.printers.commands import PRINTING_STATES
from app.services import drying
from app.services.drying_settings import DryingConfig, get_drying_config_sync
from app.services.notify import events
from app.services.printer_state import read_state_sync

log = logging.getLogger(__name__)

DID_NOT_START_S = 90
QUEUE_WAIT_MAX_S = 30 * 60
HOLD_TTL_S = 130
COOLDOWN_S = 6 * 3600  # per unit, after an automated cycle was created
RETENTION_DAYS = 7
OPEN = ("pending", "waiting", "running")
IDLE_STATES = {"IDLE", "FINISH", "FAILED"}


def hold_key(printer_id: int) -> str:
    return f"drying:hold:{printer_id}"


def hold_reason(client: Redis, printer_id: int) -> str | None:
    """``"drying"`` while ``queue_drying_block`` keeps the dispatcher off."""
    return "drying" if client.exists(hold_key(printer_id)) else None


def _active_key(pid: int, ams: int) -> str:
    return f"drying:active:{pid}:{ams}"


def _seen_key(sid: int) -> str:
    return f"drying:seen:{sid}"


def _humid_key(pid: int, ams: int) -> str:
    return f"drying:humid_since:{pid}:{ams}"


def _cooldown_key(pid: int, ams: int) -> str:
    return f"drying:cooldown:{pid}:{ams}"


def _units(state: dict | None) -> list[dict]:
    return [u for u in (state or {}).get("ams_units") or [] if isinstance(u.get("id"), int)]


def _is_active(unit: dict) -> bool:
    return unit.get("dry_status") in drying.ACTIVE_DRY_STATUSES


def _fw(state: dict | None) -> str | None:
    return ((state or {}).get("firmware") or {}).get("ota")


def _send_start(r: Redis, row: ScheduledDrying, filament: str) -> None:
    r.publish(
        command_channel(row.printer_id),
        json.dumps(
            {
                "command": "drying_start",
                "args": {
                    "ams_id": row.ams_id,
                    "temp": row.temp,
                    "duration_hours": row.duration_hours,
                    "filament": filament,
                    "rotate_tray": row.rotate_tray,
                },
                "request_id": uuid.uuid4().hex,
            }
        ),
    )


def _fail(row: ScheduledDrying, code: str, now: datetime) -> None:
    row.status, row.error_code, row.finished_at, row.waiting_reason = "failed", code, now, None
    events.emit(
        "drying_failed",
        row.printer_id,
        {"ams_id": row.ams_id, "error": code, "filament": row.filament, "schedule_id": row.id},
    )


# -- steps ---------------------------------------------------------------------
def _edges(
    session: Session,
    r: Redis,
    settings: Settings,
    states: dict[int, dict | None],
    counts: dict,
    queue_wanted: set[int] | None = None,
) -> None:
    """Observe unit active <-> idle transitions: notify, and plug auto-off
    once per finished cycle."""
    from app.services.smart_plugs import manager
    from app.services.smart_plugs.drivers import PlugError

    for pid, state in states.items():
        if state is None:
            continue
        finished = False
        for u in _units(state):
            key = _active_key(pid, u["id"])
            if _is_active(u):
                if r.set(key, "1", nx=True, ex=3 * 86400):
                    events.emit(
                        "drying_started",
                        pid,
                        {
                            "ams_id": u["id"],
                            "filament": u.get("dry_filament") or "",
                            "temp": u.get("dry_target_temp"),
                        },
                    )
            elif u.get("dry_status") == 0 and r.delete(key):
                finished = True
                events.emit("drying_finished", pid, {"ams_id": u["id"]})
        if not finished:
            continue
        busy = (
            pid in (queue_wanted or ())
            or state.get("gcode_state") in PRINTING_STATES
            or any(_is_active(u) for u in _units(state))
        )
        plug = session.scalar(
            select(SmartPlug).where(
                SmartPlug.printer_id == pid, SmartPlug.auto_off_after_drying.is_(True)
            )
        )
        if plug is None or busy:
            continue
        try:
            manager.command(session, settings, plug, "off")
            counts["plug_off"] += 1
        except PlugError as exc:
            plug.last_error = str(exc)[:200]
        except Exception:  # noqa: BLE001 -- one plug must not stop the tick
            log.exception("drying: plug off failed for printer %s", pid)
            session.rollback()


def _update_running(
    session: Session, r: Redis, states: dict[int, dict | None], now: datetime, counts: dict
) -> None:
    rows = session.scalars(select(ScheduledDrying).where(ScheduledDrying.status == "running"))
    for row in rows.all():
        unit = drying.find_ams_unit(states.get(row.printer_id), row.ams_id)
        if unit is None:
            continue  # offline mid-run: resolve when it reports again
        if _is_active(unit):
            r.set(_seen_key(row.id), "1", ex=3 * 86400)
        elif unit.get("dry_status") == 0:
            if r.get(_seen_key(row.id)):
                row.status, row.finished_at = "done", now
                r.delete(_seen_key(row.id))
                counts["done"] += 1
            elif row.started_at and (now - row.started_at).total_seconds() >= DID_NOT_START_S:
                _fail(row, "did_not_start", now)
                counts["failed"] += 1


def _open_units(session: Session) -> set[tuple[int, int]]:
    rows = session.execute(
        select(ScheduledDrying.printer_id, ScheduledDrying.ams_id).where(
            ScheduledDrying.status.in_(OPEN)
        )
    )
    return {(p, a) for p, a in rows.all()}


def _auto_candidates(
    session: Session,
    r: Redis,
    cfg: DryingConfig,
    printers: dict[int, Printer],
    states: dict[int, dict | None],
    now: datetime,
    counts: dict,
) -> None:
    """Ambient (sustained humidity, idle) and queue (humid unit on a printer
    the queue is about to use) auto-drying: both create a schedule that the
    due step starts in the same tick."""
    if not (cfg.ambient_drying_enabled or cfg.queue_drying_enabled):
        return
    wanted: set[int] = set()
    if cfg.queue_drying_enabled:
        from app.services import dispatcher

        try:
            items = dispatcher.eligible_queue_items(session)
            wanted = dispatcher.queue_target_ids(session, r, items)[0]  # READY items only
        except Exception:  # noqa: BLE001
            log.exception("drying: queue evaluation failed")
            session.rollback()
    open_units = _open_units(session)
    ts = now.timestamp()
    for pid, printer in printers.items():
        state = states.get(pid)
        if state is None:
            continue
        caps = capabilities_for(printer.model, printer.serial, _fw(state))
        if not caps.ams_drying:
            continue
        gcode = state.get("gcode_state")
        idle = gcode in IDLE_STATES
        may_print_dry = cfg.print_drying_enabled and caps.ams_drying_while_printing
        if not (idle or (may_print_dry and gcode in PRINTING_STATES)):
            continue
        for u in _units(state):
            ams = u["id"]
            hum = u.get("humidity")
            trays = drying.unit_trays(state, ams)
            if hum is None or not trays or _is_active(u) or (pid, ams) in open_units:
                r.delete(_humid_key(pid, ams))
                continue
            thr = drying.resolve_humidity_threshold(
                trays, cfg.per_type_thresholds, cfg.ams_humidity_fair
            )
            if hum < thr:
                r.delete(_humid_key(pid, ams))
                continue
            source = None
            r.set(_humid_key(pid, ams), repr(ts), nx=True, ex=86400)
            since = float(r.get(_humid_key(pid, ams)) or ts)
            if cfg.ambient_drying_enabled and ts - since >= cfg.ambient_sustained_min * 60:
                source = "ambient"
            elif cfg.queue_drying_enabled and pid in wanted and idle:
                source = "queue"
            if source is None or r.exists(_cooldown_key(pid, ams)):
                continue
            params = drying.conservative_params(trays, ams, cfg.presets_dict())
            if params is None:
                continue
            temp, hours, fil = params
            session.add(
                ScheduledDrying(
                    printer_id=pid,
                    ams_id=ams,
                    temp=temp,
                    duration_hours=hours,
                    filament=fil,
                    source=source,
                    status="pending",
                )
            )
            open_units.add((pid, ams))
            r.set(_cooldown_key(pid, ams), "1", ex=COOLDOWN_S)
            r.delete(_humid_key(pid, ams))
            counts["created"] += 1
    session.flush()


def _start_due(
    session: Session,
    r: Redis,
    printers: dict[int, Printer],
    states: dict[int, dict | None],
    now: datetime,
    counts: dict,
) -> None:
    rows = session.scalars(
        select(ScheduledDrying)
        .where(ScheduledDrying.status.in_(("pending", "waiting")))
        .order_by(ScheduledDrying.id)
    )
    running = {
        (p, a)
        for p, a in session.execute(
            select(ScheduledDrying.printer_id, ScheduledDrying.ams_id).where(
                ScheduledDrying.status == "running"
            )
        ).all()
    }
    for row in rows.all():
        if row.start_after is not None and row.start_after > now:
            continue
        if (row.printer_id, row.ams_id) in running:
            row.status, row.waiting_reason = "waiting", drying.WAITING_REASON_BLOCKED
            continue
        printer, state = printers.get(row.printer_id), states.get(row.printer_id)
        if printer is None or state is None:
            row.status, row.waiting_reason = "waiting", drying.WAITING_REASON_OFFLINE
            continue
        caps = capabilities_for(printer.model, printer.serial)
        try:
            out = drying.validate_preflight(
                model_key=caps.model_key,
                firmware=_fw(state),
                state=state,
                ams_id=row.ams_id,
                filament=row.filament,
            )
        except drying.DryingRefused as e:
            if e.code in ("unsupported", "screen_only"):
                _fail(row, e.code, now)
                counts["failed"] += 1
            elif e.code == "blocked":
                row.status = "waiting"
                row.waiting_reason = drying.waiting_reason_for_codes(e.extra["codes"])
            elif e.code == "printing":
                row.status, row.waiting_reason = "waiting", drying.WAITING_REASON_BUSY
            else:  # unknown unit: the AMS may not have reported yet
                row.status, row.waiting_reason = "waiting", drying.WAITING_REASON_OFFLINE
            continue
        row.filament = out["filament"]
        row.status, row.waiting_reason, row.started_at = "running", None, now
        session.add(
            AuditLog(
                user_id=row.created_by_user_id,
                created_by_user_id=row.created_by_user_id,
                printer_id=row.printer_id,
                action="drying_start",
                payload={
                    "schedule_id": row.id,
                    "source": row.source,
                    "ams_id": row.ams_id,
                    "temp": row.temp,
                    "duration_hours": row.duration_hours,
                    "filament": row.filament,
                },
            )
        )
        running.add((row.printer_id, row.ams_id))
        counts["started"] += 1
        session.commit()  # durable `running` first: a crash can't re-send the start
        _send_start(r, row, row.filament)


def _expire_stuck(session: Session, now: datetime, counts: dict) -> None:
    """A queue-sourced run stuck ``waiting`` (blocked AMS, offline...) fails
    after ``QUEUE_WAIT_MAX_S`` so it can't keep the queue waiting forever."""
    cutoff = now - timedelta(seconds=QUEUE_WAIT_MAX_S)
    rows = session.scalars(
        select(ScheduledDrying).where(
            ScheduledDrying.status == "waiting",
            ScheduledDrying.source == "queue",
            ScheduledDrying.created_at < cutoff,
        )
    )
    for row in rows.all():
        _fail(row, row.waiting_reason or "blocked", now)
        counts["failed"] += 1


def _holds(
    session: Session,
    r: Redis,
    cfg: DryingConfig,
    states: dict[int, dict | None],
) -> None:
    """Dispatcher hold: with ``queue_drying_block``, a printer whose AMS is
    drying (or has a running queue-sourced run) is not a dispatch target; the
    key's short TTL fails open if the tick dies."""
    queue_open = {
        p
        for (p,) in session.execute(
            select(ScheduledDrying.printer_id).where(
                ScheduledDrying.status == "running", ScheduledDrying.source == "queue"
            )
        ).all()
    }
    for pid, state in states.items():
        held = cfg.queue_drying_block and (
            pid in queue_open or any(_is_active(u) for u in _units(state))
        )
        if held:
            r.set(hold_key(pid), "1", ex=HOLD_TTL_S)
        else:
            r.delete(hold_key(pid))


def tick(
    session: Session, r: Redis, settings: Settings, *, now: datetime | None = None
) -> dict[str, int]:
    now = now or datetime.now(UTC)
    cfg = get_drying_config_sync(session)
    printers = {
        p.id: p for p in session.scalars(select(Printer).where(Printer.enabled.is_(True))).all()
    }
    states = {pid: read_state_sync(r, pid) for pid in printers}
    counts = dict.fromkeys(("started", "done", "failed", "created", "plug_off"), 0)
    _update_running(session, r, states, now, counts)
    session.commit()
    from app.services.smart_plugs import manager

    _edges(session, r, settings, states, counts, manager._queue_wanted(session, r))
    _auto_candidates(session, r, cfg, printers, states, now, counts)
    _start_due(session, r, printers, states, now, counts)
    _expire_stuck(session, now, counts)
    session.execute(
        ScheduledDrying.__table__.delete().where(
            ScheduledDrying.status.in_(("done", "failed", "cancelled")),
            ScheduledDrying.finished_at < now - timedelta(days=RETENTION_DAYS),
        )
    )
    session.commit()
    _holds(session, r, cfg, states)
    return counts
