"""Redis key helpers + readers for the P8 HA poll beat (Task 4). Every
writer lives in `app.tasks.home_assistant`; this module is the shared,
pure(ish) vocabulary both the poller and other readers (API, dispatcher)
use so the key shapes live in exactly one place.

``ha:alert:{pid}`` -- JSON ``{reason, entities, hold}``, `SET ... EX 60`
so a dead poller fails open (the key just expires). ``ha:energy:{pid}`` --
the published running energy total (a plain float string) for printer
``pid``; ``ha:energy:acc:{pid}`` is the internal Redis-native
`INCRBYFLOAT` target that key mirrors. ``ha:energy:raw:{link_id}``
(private, not part of the public vocabulary below -- keyed by the LINK,
not the printer, so two energy links on one printer track independent
baselines) caches the last-seen raw HA sensor reading so the poller can
detect a counter reset next tick.
``ha:alert:fired:{link_id}:{pid}`` is the edge-triggered pause bookkeeping
key (``_handle_pause_edge``), keyed by BOTH the link and the printer so a
NULL-printer (global) pause link fires independently on every printer it
applies to, instead of one printer's edge suppressing another's.
``ha:status`` reports the poller's own health:
``{ok, error?, errors?, checked_at}``, `SET ... EX 120` so a dead poller's
status goes stale (fails open) rather than lying forever.
"""

from __future__ import annotations

import json

from redis import Redis

STATUS_KEY = "ha:status"
ALERT_TTL_S = 60
STATUS_TTL_S = 120


def alert_key(pid: int) -> str:
    return f"ha:alert:{pid}"


def energy_key(pid: int) -> str:
    return f"ha:energy:{pid}"


def energy_acc_key(pid: int) -> str:
    return f"ha:energy:acc:{pid}"


def fired_key(link_id: int, pid: int) -> str:
    return f"ha:alert:fired:{link_id}:{pid}"


def _raw_key(link_id: int) -> str:
    """Private: last-seen raw HA sensor reading for the energy link
    `link_id`, used to detect a counter_delta reset -- keyed by the LINK,
    not the printer, so two energy_kwh links on the same printer (two
    plugs feeding one machine) each track their own baseline instead of
    clobbering each other's. Not part of the public key vocabulary --
    nothing outside `app.services.home_assistant` should read or write
    it."""
    return f"ha:energy:raw:{link_id}"


def read_alert(r: Redis, pid: int) -> dict | None:
    raw = r.get(alert_key(pid))
    if raw is None:
        return None
    return json.loads(raw)


def hold_reason(r: Redis, pid: int) -> str | None:
    """The alert's `reason` iff it's currently holding the queue."""
    alert = read_alert(r, pid)
    if alert is None or not alert.get("hold"):
        return None
    return alert.get("reason")


def energy_acc(r: Redis, pid: int) -> float | None:
    """The running energy total for `pid`, or `None` if never polled."""
    raw = r.get(energy_acc_key(pid))
    if raw is None:
        return None
    return float(raw)
