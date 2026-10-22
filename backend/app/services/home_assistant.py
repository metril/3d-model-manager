"""Pure condition evaluation + counter-reset math, and the orchestration
body of the P8 HA poll beat (`app.tasks.home_assistant.poll_ha` ticks this
every 15s). See the module docstring on `app.services.ha_state` for the
Redis key vocabulary.
"""

from __future__ import annotations

import json
import logging

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import HaRole
from app.models.home_assistant import HaLink
from app.models.printing import Printer
from app.printers.base import command_channel
from app.services.ha_client import HaClient, HaUnreachable
from app.services.ha_config import HaConfig
from app.services.ha_discovery import PRINTING_STATES
from app.services.ha_state import (
    ALERT_TTL_S,
    STATUS_KEY,
    _raw_key,
    alert_key,
    energy_acc_key,
    energy_key,
    fired_key,
)
from app.services.printer_state import read_state_sync

logger = logging.getLogger(__name__)

_ALERT_ROLES = (HaRole.DOOR, HaRole.SMOKE, HaRole.TEMP, HaRole.HUMIDITY)
_UNAVAILABLE_STATES = {"unknown", "unavailable", None}


def evaluate(cond: dict | None, state: str | None) -> bool:
    """Evaluate one `alert_condition` (`{"op": ..., "value": ...}`) against
    a raw HA state string. `None` condition, or an unknown/unavailable/
    missing state, never fires. Numeric ops (`gt`/`lt`) that can't parse
    the state as a float never fire either -- they don't raise."""
    if cond is None or state in _UNAVAILABLE_STATES:
        return False
    op = cond.get("op")
    value = cond.get("value")
    if op == "eq":
        return state == str(value)
    if op == "ne":
        return state != str(value)
    if op == "in":
        return state in (value or ())
    if op in ("gt", "lt"):
        try:
            cur = float(state)
            target = float(value)
        except (TypeError, ValueError):
            return False
        return cur > target if op == "gt" else cur < target
    return False


def counter_delta(prev: float | None, cur: float) -> float:
    """HA total_increasing semantics: a drop means the meter reset (plug
    re-paired / rolled over), so the whole new reading is consumption since
    the reset. First sample establishes the baseline only."""
    if prev is None:
        return 0.0
    if cur >= prev:
        return cur - prev
    return cur


def _firing_entities(
    links: list[HaLink], states: dict[str, dict | None], pid: int
) -> tuple[list[str], bool]:
    entities: list[str] = []
    hold = False
    for link in links:
        if link.printer_id is not None and link.printer_id != pid:
            continue
        state_obj = states.get(link.entity_id)
        state_val = state_obj.get("state") if state_obj else None
        if evaluate(link.alert_condition, state_val):
            entities.append(link.entity_id)
            hold = hold or link.pause_print or link.hold_queue
    return entities, hold


def _handle_pause_edge(
    r: Redis, links: list[HaLink], states: dict[str, dict | None], pid: int
) -> None:
    """Edge-triggered pause (Task 5): for each ``pause_print`` link relevant
    to ``pid``, a firing->fired transition (``ha:alert:fired:{link_id}``,
    ``SET ... NX``) publishes one ``pause`` command iff the printer is
    currently printing (``PRINTING_STATES``) -- fires exactly once per
    alert episode, not once per tick. A link that stops firing clears its
    fired key so the next episode can fire again."""
    for link in links:
        if link.printer_id is not None and link.printer_id != pid:
            continue
        if not link.pause_print:
            continue
        state_obj = states.get(link.entity_id)
        state_val = state_obj.get("state") if state_obj else None
        if evaluate(link.alert_condition, state_val):
            if r.set(fired_key(link.id), 1, nx=True):
                printer_state = read_state_sync(r, pid)
                if (printer_state or {}).get("gcode_state") in PRINTING_STATES:
                    r.publish(command_channel(pid), json.dumps({"command": "pause"}))
                    logger.info("HA alert paused printer %s (%s)", pid, link.entity_id)
        else:
            r.delete(fired_key(link.id))


def poll_once(
    s: Session,
    r: Redis,
    cfg: HaConfig,
    client: HaClient | None = None,
    *,
    now=None,
) -> dict:
    """One poll tick. No-op (returns `{"ok": True, "skipped": True}`) unless
    `cfg.ha_url`/`cfg.ha_token` are both set and at least one `ha_links` row
    exists. See `app.services.ha_state`'s module docstring for the key
    shapes written below."""
    if not cfg.ha_url or not cfg.ha_token:
        return {"ok": True, "skipped": True}

    links = list(s.scalars(select(HaLink)))
    if not links:
        return {"ok": True, "skipped": True}

    owns_client = client is None
    client = client or HaClient(cfg.ha_url, cfg.ha_token)
    try:
        entity_ids = sorted({link.entity_id for link in links})
        states: dict[str, dict | None] = {}
        entity_errors: list[str] = []
        try:
            for entity_id in entity_ids:
                value = client.state(entity_id)
                if value is None:
                    entity_errors.append(entity_id)
                states[entity_id] = value
        except HaUnreachable:
            r.set(STATUS_KEY, json.dumps({"ok": False, "error": "unreachable"}))
            for key in r.scan_iter("ha:alert:*"):
                r.delete(key)
            return {"ok": False, "error": "unreachable"}

        alert_links = [link for link in links if link.role in _ALERT_ROLES]
        energy_links = [
            link for link in links if link.role == HaRole.ENERGY_KWH and link.printer_id is not None
        ]

        # A NULL-printer alert link applies to every printer, so the set of
        # printers to evaluate isn't just the ones alert links mention --
        # it's every printer that exists, whenever any alert link is global.
        printer_ids = sorted(
            {link.printer_id for link in alert_links if link.printer_id is not None}
        )
        if any(link.printer_id is None for link in alert_links):
            printer_ids = sorted(s.scalars(select(Printer.id)))

        for pid in printer_ids:
            entities, hold = _firing_entities(alert_links, states, pid)
            if entities:
                payload = {
                    "reason": "Waiting on " + ", ".join(entities),
                    "entities": entities,
                    "hold": hold,
                }
                r.set(alert_key(pid), json.dumps(payload), ex=ALERT_TTL_S)
            else:
                r.delete(alert_key(pid))
            _handle_pause_edge(r, alert_links, states, pid)

        for link in energy_links:
            pid = link.printer_id
            state_obj = states.get(link.entity_id)
            state_val = state_obj.get("state") if state_obj else None
            try:
                cur = float(state_val)
            except (TypeError, ValueError):
                continue  # non-numeric: keeps prev, no update this tick

            prev_raw = r.get(_raw_key(pid))
            prev = float(prev_raw) if prev_raw is not None else None
            delta = counter_delta(prev, cur)
            r.set(_raw_key(pid), repr(cur))
            if delta:
                r.incrbyfloat(energy_acc_key(pid), delta)
            elif r.get(energy_acc_key(pid)) is None:
                r.set(energy_acc_key(pid), 0.0)
            total = r.get(energy_acc_key(pid))
            r.set(energy_key(pid), total)

        status: dict = {"ok": True}
        if entity_errors:
            status["entity_errors"] = entity_errors
        r.set(STATUS_KEY, json.dumps(status))
        return status
    finally:
        if owns_client:
            client.close()
