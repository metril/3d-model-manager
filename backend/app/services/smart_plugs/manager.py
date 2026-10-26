"""Plug poll tick (beat, every 60 s) and helpers.

Per plug: refresh state/power; integrate power into the printer's energy
accumulator (``ha:energy:acc:{pid}``, the same one job start/finish snapshots
read, see ``costing.job_energy_kwh``) unless the printer already has an HA
``energy_kwh`` link; auto-off once the printer has been idle with a cold
nozzle for ``auto_off_after_min``."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.crypto import decrypt_secret
from app.models import HaLink, SmartPlug
from app.models.enums import HaRole
from app.printers.base import state_key
from app.services.drying import ACTIVE_DRY_STATUSES
from app.services.ha_state import energy_acc_key, energy_key
from app.services.notify import events
from app.services.printer_state import read_state_sync
from app.services.smart_plugs.drivers import PlugError, PlugStatus, build_driver

log = logging.getLogger(__name__)

IDLE_STATES = {"IDLE", "FINISH", "FAILED"}
COLD_NOZZLE_C = 50.0
ALERT_COOLDOWN_MIN = 30
MAX_INTEGRATE_S = 300  # cap one tick's dt so a stalled poller can't invent energy


def idle_key(plug_id: int) -> str:
    return f"plug:idle_since:{plug_id}"


BOOT_WAIT_S = 300


def woken_key(printer_id: int) -> str:
    return f"plug:woken_at:{printer_id}"


def polled_key(plug_id: int) -> str:
    return f"plug:polled:{plug_id}"


def load_config(settings: Settings, plug: SmartPlug) -> dict:
    return json.loads(decrypt_secret(settings, plug.config_enc))


def driver_for(session: Session, settings: Settings, plug: SmartPlug):
    return build_driver(plug.kind, load_config(settings, plug), session, settings)


def _idle_no_drying(state: dict | None) -> bool:
    if not state or state.get("gcode_state") not in IDLE_STATES:
        return False
    # a drying cycle can run for hours
    return not any(u.get("dry_status") in ACTIVE_DRY_STATUSES for u in state.get("ams_units") or [])


def can_power_off(state: dict | None, threshold: float = COLD_NOZZLE_C) -> bool:
    """Printer reported in, is not printing, no AMS is drying, and the
    nozzle (and bed, when reported) is below ``threshold``."""
    if not state or not _idle_no_drying(state):
        return False
    nozzle = state.get("nozzle_temper")
    if nozzle is None or float(nozzle) >= threshold:
        return False
    bed = state.get("bed_temper")
    return bed is None or float(bed) < threshold


def mark_offline(r: Redis, plug: SmartPlug) -> None:
    """A plug that cuts the printer's own power takes the printer offline:
    drop its cached state so nothing treats it as idle/ready."""
    if plug.printer_id is not None and plug.controls_printer_power:
        r.delete(state_key(plug.printer_id))


def power_plugs(session: Session, printer_id: int) -> list[SmartPlug]:
    return list(
        session.scalars(
            select(SmartPlug).where(
                SmartPlug.printer_id == printer_id, SmartPlug.controls_printer_power.is_(True)
            )
        ).all()
    )


def _integrate(session: Session, r: Redis, plug: SmartPlug, power_w: float, now: float) -> None:
    last = r.getset(polled_key(plug.id), repr(now))
    r.expire(polled_key(plug.id), 3600)
    if last is None:
        return
    dt = min(max(now - float(last), 0.0), MAX_INTEGRATE_S)
    if dt <= 0:
        return
    kwh = power_w * dt / 3_600_000
    plug.energy_kwh = (plug.energy_kwh or 0.0) + kwh  # the plug's own lifetime counter
    if plug.printer_id is None:
        return
    has_ha = session.scalar(
        select(HaLink.id).where(
            HaLink.printer_id == plug.printer_id, HaLink.role == HaRole.ENERGY_KWH
        )
    )
    if has_ha is not None:
        return
    total = r.incrbyfloat(energy_acc_key(plug.printer_id), kwh)
    r.set(energy_key(plug.printer_id), total)


def apply_status(plug: SmartPlug, st: PlugStatus, now: datetime) -> None:
    if st.on is not None:
        plug.last_state = "on" if st.on else "off"
    plug.last_power_w = st.power_w
    plug.last_polled_at = now
    plug.last_error = None


def tick(
    session: Session, r: Redis, settings: Settings, *, now: datetime | None = None
) -> dict[str, int]:
    now = now or datetime.now(UTC)
    counts = {"polled": 0, "errors": 0, "turned_off": 0}
    wanted = _queue_wanted(session, r)
    for plug in session.scalars(select(SmartPlug)).all():
        try:
            drv = driver_for(session, settings, plug)
            st = drv.status()
            apply_status(plug, st, now)
            counts["polled"] += 1
            if st.power_w is not None:
                _integrate(session, r, plug, st.power_w, now.timestamp())
            _check_alert(plug, st, now)
            if _run_schedule(r, plug, drv, now, wanted) or _run_dry_off(r, plug, drv, now):
                continue
            if _maybe_auto_off(r, plug, drv, now, wanted):
                counts["turned_off"] += 1
        except PlugError as exc:
            plug.last_error = str(exc)[:200]
            counts["errors"] += 1
        except Exception:  # noqa: BLE001 -- one bad plug must not stop the tick
            log.exception("plug tick failed for plug %s", plug.id)
            plug.last_error = "unexpected error"
            counts["errors"] += 1
    session.commit()
    return counts


def _queue_wanted(session: Session, r: Redis) -> set[int] | None:
    """Printers any READY/WAITING queue item could land on; ``None`` if the
    queue can't be evaluated (then auto-off proceeds as before)."""
    from app.services import dispatcher

    try:
        return dispatcher.queue_target_ids(session, r, dispatcher.eligible_queue_items(session))[1]
    except Exception:  # noqa: BLE001
        log.exception("queue evaluation failed")
        session.rollback()
        return None


def _check_alert(plug: SmartPlug, st: PlugStatus, now: datetime) -> None:
    if not plug.power_alert_enabled or st.power_w is None:
        return
    w = st.power_w
    high, low = plug.power_alert_high_w, plug.power_alert_low_w
    if high is not None and w > high:
        direction, threshold = "above", high
    elif low is not None and plug.last_state == "on" and w < low:
        direction, threshold = "below", low
    else:
        return
    last = plug.power_alert_last_at
    if last is not None and now - last < timedelta(minutes=ALERT_COOLDOWN_MIN):
        return
    plug.power_alert_last_at = now
    events.emit(
        "plug_power_alert",
        plug.printer_id,
        {
            "plug_name": plug.name or f"Plug {plug.id}",
            "power_w": round(w, 1),
            "direction": direction,
            "threshold": threshold,
        },
    )


SCHED_OFF_DEFER_MIN = 360  # a printer-power "off" waits this long for the printer to go idle


def _schedule_off_blocked(r: Redis, plug: SmartPlug, wanted: set[int] | None) -> bool:
    """A scheduled "off" on a plug that cuts a printer's power must not
    kill a print, a hot nozzle, a drying cycle or queued work."""
    if not plug.controls_printer_power or plug.printer_id is None:
        return False
    if wanted is not None and plug.printer_id in wanted:
        return True
    return not can_power_off(read_state_sync(r, plug.printer_id))


def _run_schedule(
    r: Redis, plug: SmartPlug, drv, now: datetime, wanted: set[int] | None = None
) -> bool:
    """Daily on/off at ``HH:MM`` in the plug's timezone. Fires inside a 2-minute
    window (a skipped beat still lands); a Redis key dedupes per day. A
    printer-power "off" is deferred (window widened) while the printer is
    busy / hot / wanted by the queue."""
    if not plug.schedule_enabled:
        return False
    try:
        local = now.astimezone(ZoneInfo(plug.schedule_tz or "UTC"))
    except Exception:  # noqa: BLE001 -- bad tz name: treat as UTC
        local = now.astimezone(UTC)
    minute = local.hour * 60 + local.minute
    for action, hhmm in (("on", plug.schedule_on_time), ("off", plug.schedule_off_time)):
        if not hhmm:
            continue
        h, m = hhmm.split(":")
        window = SCHED_OFF_DEFER_MIN if action == "off" and plug.controls_printer_power else 1
        if not 0 <= minute - (int(h) * 60 + int(m)) <= window:
            continue
        if action == "off" and _schedule_off_blocked(r, plug, wanted):
            continue
        if not r.set(f"plug:sched:{plug.id}:{action}:{local.date()}", "1", nx=True, ex=7200):
            continue
        getattr(drv, action)()
        plug.last_state = action
        if action == "off":
            mark_offline(r, plug)
        return True
    return False


def dry_off_key(plug_id: int) -> str:
    return f"plug:dryoff:{plug_id}"


def request_dry_off(
    session: Session, settings: Settings, r: Redis, plug: SmartPlug, *, now: datetime | None = None
) -> bool:
    """AMS drying finished: power off now, or after ``drying_off_delay_min``.
    Returns True when switched off immediately."""
    if plug.drying_off_delay_min > 0:
        due = (now or datetime.now(UTC)) + timedelta(minutes=plug.drying_off_delay_min)
        r.set(dry_off_key(plug.id), repr(due.timestamp()), ex=plug.drying_off_delay_min * 60 + 3600)
        return False
    command(session, settings, plug, "off")
    mark_offline(r, plug)
    return True


def _run_dry_off(r: Redis, plug: SmartPlug, drv, now: datetime) -> bool:
    raw = r.get(dry_off_key(plug.id))
    if raw is None or now.timestamp() < float(raw):
        return False
    r.delete(dry_off_key(plug.id))
    state = read_state_sync(r, plug.printer_id) if plug.printer_id is not None else None
    if plug.last_state == "off" or (state is not None and not _idle_no_drying(state)):
        return False  # a new print/cycle took over: leave it on
    drv.off()
    plug.last_state = "off"
    mark_offline(r, plug)
    return True


def _maybe_auto_off(
    r: Redis, plug: SmartPlug, drv, now: datetime, wanted: set[int] | None = None
) -> bool:
    if plug.printer_id is None:
        return False
    if wanted is not None and plug.printer_id in wanted:
        r.delete(idle_key(plug.id))  # the queue needs this printer: keep it on
        return False
    if not plug.auto_off or plug.last_state == "off":
        r.delete(idle_key(plug.id))
        return False
    state = read_state_sync(r, plug.printer_id)
    temp_mode = plug.off_delay_mode == "temperature"
    if temp_mode:
        ok = can_power_off(state, float(plug.off_temp_threshold))
    elif plug.controls_printer_power:
        ok = can_power_off(state)
    else:
        ok = _idle_no_drying(state)  # accessories (fan, light): no cold-nozzle gate
    if not ok:
        r.delete(idle_key(plug.id))
        return False
    if not temp_mode:
        delay_min = plug.auto_off_after_min if plug.auto_off_after_min is not None else 5
        r.set(idle_key(plug.id), repr(now.timestamp()), nx=True)
        since = float(r.get(idle_key(plug.id)) or now.timestamp())
        if now.timestamp() - since < delay_min * 60:
            return False
    drv.off()
    plug.last_state = "off"
    mark_offline(r, plug)
    r.delete(idle_key(plug.id))
    return True


def wake_for_queue(session: Session, settings: Settings, r: Redis, printer_ids: set[int]) -> int:
    """Power on ``auto_on_before_print`` plugs (currently off) for exactly
    ``printer_ids``; records ``plug:woken_at:{pid}`` so the dispatcher waits
    for a fresh report before trusting the printer's (stale) state."""
    stmt = select(SmartPlug).where(
        SmartPlug.auto_on_before_print.is_(True),
        SmartPlug.controls_printer_power.is_(True),
        SmartPlug.last_state == "off",
        SmartPlug.printer_id.in_(printer_ids),
    )
    n = 0
    for plug in session.scalars(stmt).all():
        try:
            driver_for(session, settings, plug).on()
            plug.last_state = "on"
            r.set(woken_key(plug.printer_id), repr(datetime.now(UTC).timestamp()), ex=3600)
            n += 1
        except PlugError as exc:
            plug.last_error = str(exc)[:200]
    session.commit()
    return n


def command(session: Session, settings: Settings, plug: SmartPlug, action: str) -> SmartPlug:
    """Run ``on``/``off`` now and record the resulting state."""
    drv = driver_for(session, settings, plug)
    getattr(drv, action)()
    plug.last_state = action
    plug.last_error = None
    plug.last_polled_at = datetime.now(UTC)
    session.commit()
    return plug
