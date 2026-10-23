"""Plug poll tick (beat, every 60 s) and helpers.

Per plug: refresh state/power; integrate power into the printer's energy
accumulator (``ha:energy:acc:{pid}``, the same one job start/finish snapshots
read, see ``costing.job_energy_kwh``) unless the printer already has an HA
``energy_kwh`` link; auto-off once the printer has been idle with a cold
nozzle for ``auto_off_after_min``."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.crypto import decrypt_secret
from app.models import HaLink, SmartPlug
from app.models.enums import HaRole
from app.services.ha_state import energy_acc_key, energy_key
from app.services.printer_state import read_state_sync
from app.services.smart_plugs.drivers import PlugError, PlugStatus, build_driver

log = logging.getLogger(__name__)

IDLE_STATES = {"IDLE", "FINISH", "FAILED"}
COLD_NOZZLE_C = 50.0
MAX_INTEGRATE_S = 300  # cap one tick's dt so a stalled poller can't invent energy


def idle_key(plug_id: int) -> str:
    return f"plug:idle_since:{plug_id}"


def polled_key(plug_id: int) -> str:
    return f"plug:polled:{plug_id}"


def load_config(settings: Settings, plug: SmartPlug) -> dict:
    return json.loads(decrypt_secret(settings, plug.config_enc))


def driver_for(session: Session, settings: Settings, plug: SmartPlug):
    return build_driver(plug.kind, load_config(settings, plug), session, settings)


def can_power_off(state: dict | None) -> bool:
    """Printer reported in, is not printing, and the nozzle is cold."""
    if not state or state.get("gcode_state") not in IDLE_STATES:
        return False
    nozzle = state.get("nozzle_temper")
    return nozzle is not None and float(nozzle) < COLD_NOZZLE_C


def _integrate(session: Session, r: Redis, plug: SmartPlug, power_w: float, now: float) -> None:
    last = r.getset(polled_key(plug.id), repr(now))
    r.expire(polled_key(plug.id), 3600)
    if last is None:
        return
    dt = min(max(now - float(last), 0.0), MAX_INTEGRATE_S)
    has_ha = session.scalar(
        select(HaLink.id).where(
            HaLink.printer_id == plug.printer_id, HaLink.role == HaRole.ENERGY_KWH
        )
    )
    if has_ha is not None or dt <= 0:
        return
    kwh = power_w * dt / 3_600_000
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
    for plug in session.scalars(select(SmartPlug)).all():
        try:
            drv = driver_for(session, settings, plug)
            st = drv.status()
            apply_status(plug, st, now)
            counts["polled"] += 1
            if st.power_w is not None:
                _integrate(session, r, plug, st.power_w, now.timestamp())
            if _maybe_auto_off(r, plug, drv, now):
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


def _maybe_auto_off(r: Redis, plug: SmartPlug, drv, now: datetime) -> bool:
    if not plug.auto_off_after_min or plug.last_state == "off":
        r.delete(idle_key(plug.id))
        return False
    state = read_state_sync(r, plug.printer_id)
    if not can_power_off(state):
        r.delete(idle_key(plug.id))
        return False
    r.set(idle_key(plug.id), repr(now.timestamp()), nx=True)
    since = float(r.get(idle_key(plug.id)) or now.timestamp())
    if now.timestamp() - since < plug.auto_off_after_min * 60:
        return False
    drv.off()
    plug.last_state = "off"
    r.delete(idle_key(plug.id))
    return True


def wake_for_queue(session: Session, settings: Settings, printer_ids: set[int] | None) -> int:
    """Power on ``auto_on_before_print`` plugs whose printer is off, for
    printers a waiting queue item may land on (``None`` = any printer)."""
    stmt = select(SmartPlug).where(
        SmartPlug.auto_on_before_print.is_(True), SmartPlug.last_state == "off"
    )
    if printer_ids is not None:
        stmt = stmt.where(SmartPlug.printer_id.in_(printer_ids))
    n = 0
    for plug in session.scalars(stmt).all():
        try:
            driver_for(session, settings, plug).on()
            plug.last_state = "on"
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
