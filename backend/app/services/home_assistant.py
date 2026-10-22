"""Pure condition evaluation + counter-reset math, and the orchestration
body of the P8 HA poll beat (`app.tasks.home_assistant.poll_ha` ticks this
every 15s). See the module docstring on `app.services.ha_state` for the
Redis key vocabulary.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

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
    STATUS_TTL_S,
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

# Atomic read-prev/compute-delta/write-raw/accumulate, so two overlapping
# `poll_once` calls (the Celery lock normally rules this out, but nothing
# else enforces it) can never both read the same `prev` and each apply
# their own delta on top of it -- double-counting the same tick's energy.
# Mirrors `counter_delta`'s own reset math (kept in sync by hand -- see
# `test_energy_update_lua_matches_counter_delta`).
_ENERGY_UPDATE_LUA = """
local raw_key = KEYS[1]
local acc_key = KEYS[2]
local cur = tonumber(ARGV[1])
local reset_factor = tonumber(ARGV[2])
local prev_raw = redis.call('GET', raw_key)
local delta = 0
if prev_raw then
    local prev = tonumber(prev_raw)
    if cur >= prev then
        delta = cur - prev
    elseif cur < reset_factor * prev then
        delta = cur
    else
        delta = 0
    end
end
redis.call('SET', raw_key, tostring(cur))
if delta > 0 then
    redis.call('INCRBYFLOAT', acc_key, delta)
elseif redis.call('EXISTS', acc_key) == 0 then
    redis.call('SET', acc_key, '0')
end
return redis.call('GET', acc_key)
"""


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
    if op in ("gt", "lt", "gte", "lte"):
        try:
            cur = float(state)
            target = float(value)
        except (TypeError, ValueError):
            return False
        if op == "gt":
            return cur > target
        if op == "lt":
            return cur < target
        if op == "gte":
            return cur >= target
        return cur <= target
    return False


_RESET_FACTOR = 0.9


def counter_delta(prev: float | None, cur: float) -> float:
    """HA total_increasing semantics: a drop means the meter reset (plug
    re-paired / rolled over), so the whole new reading is consumption since
    the reset -- but only when the drop is a REAL reset (``cur`` under 90%
    of ``prev``). A smaller dip is just sensor jitter around a flat reading
    and adds 0, rather than being mistaken for -- and then double counting
    on top of -- a reset. First sample establishes the baseline only."""
    if prev is None:
        return 0.0
    if cur >= prev:
        return cur - prev
    if cur < _RESET_FACTOR * prev:
        return cur
    return 0.0


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
            hold = hold or link.hold_queue
    return entities, hold


def _handle_pause_edge(
    r: Redis, links: list[HaLink], states: dict[str, dict | None], pid: int
) -> None:
    """Edge-triggered pause (Task 5): for each ``pause_print`` link relevant
    to ``pid``, a firing->fired transition (``ha:alert:fired:{link_id}:
    {pid}``, ``SET ... NX``) publishes one ``pause`` command iff the printer
    is currently printing (``PRINTING_STATES``) -- fires exactly once per
    alert episode, not once per tick. The fired key is keyed by BOTH the
    link and the printer, so a NULL-printer (global) pause link fires once
    per printer independently -- one printer's edge must never suppress
    another's. A link that stops firing clears its fired key so the next
    episode can fire again."""
    for link in links:
        if link.printer_id is not None and link.printer_id != pid:
            continue
        if not link.pause_print:
            continue
        state_obj = states.get(link.entity_id)
        state_val = state_obj.get("state") if state_obj else None
        if evaluate(link.alert_condition, state_val):
            if r.set(fired_key(link.id, pid), 1, nx=True):
                printer_state = read_state_sync(r, pid)
                if (printer_state or {}).get("gcode_state") in PRINTING_STATES:
                    r.publish(command_channel(pid), json.dumps({"command": "pause"}))
                    logger.info("HA alert paused printer %s (%s)", pid, link.entity_id)
        else:
            r.delete(fired_key(link.id, pid))


def poll_once(
    s: Session,
    r: Redis,
    cfg: HaConfig,
    client: HaClient | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    """One poll tick. No-op (returns `{"ok": True, "skipped": True}`) unless
    `cfg.ha_url`/`cfg.ha_token` are both set and at least one `ha_links` row
    exists. `now` overrides the `checked_at` timestamp written to
    `ha:status` (defaults to the current UTC time) -- for tests. See
    `app.services.ha_state`'s module docstring for the key shapes written
    below."""
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
            checked_at = now if now is not None else datetime.now(UTC)
            status = {"ok": False, "error": "unreachable", "checked_at": checked_at.isoformat()}
            r.set(STATUS_KEY, json.dumps(status), ex=STATUS_TTL_S)
            # Only the per-printer alert keys (`ha:alert:{pid}`, digits
            # right after the prefix) -- NOT `ha:alert:fired:{link_id}:
            # {pid}` (edge bookkeeping), which a plain "ha:alert:*" glob
            # also matches. Clearing a fired key here would let a
            # still-firing pause condition fire a SECOND pause the moment
            # HA comes back, since the edge-triggered NX guard would have
            # been wiped out from under it.
            for key in r.scan_iter("ha:alert:[0-9]*"):
                r.delete(key)
            return status

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

            total = r.eval(
                _ENERGY_UPDATE_LUA,
                2,
                _raw_key(link.id),
                energy_acc_key(pid),
                repr(cur),
                repr(_RESET_FACTOR),
            )
            r.set(energy_key(pid), total)

        checked_at = now if now is not None else datetime.now(UTC)
        status: dict = {"ok": True, "checked_at": checked_at.isoformat()}
        if entity_errors:
            status["errors"] = entity_errors
        r.set(STATUS_KEY, json.dumps(status), ex=STATUS_TTL_S)
        return status
    finally:
        if owns_client:
            client.close()
