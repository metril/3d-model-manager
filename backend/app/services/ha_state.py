"""Redis key helpers + readers for the P8 HA poll beat (Task 4). Every
writer lives in `app.tasks.home_assistant`; this module is the shared,
pure(ish) vocabulary both the poller and other readers (API, dispatcher)
use so the key shapes live in exactly one place.

``ha:alert:{pid}`` -- JSON ``{reason, entities, hold}``, `SET ... EX 60`
so a dead poller fails open (the key just expires). ``ha:energy:{pid}`` --
the published running energy total (a plain float string) for printer
``pid``; ``ha:energy:acc:{pid}`` is the internal Redis-native
`INCRBYFLOAT` target that key mirrors. ``ha:energy:raw:{pid}`` (private,
not part of the public vocabulary below) caches the last-seen raw HA
sensor reading so the poller can detect a counter reset next tick.
``ha:fired:{link_id}`` is reserved for edge-triggered actions (pause/hold
bookkeeping) in a later task. ``ha:status`` reports the poller's own
health: ``{ok, error?, entity_errors?}``.
"""

from __future__ import annotations

import json

from redis import Redis

STATUS_KEY = "ha:status"
ALERT_TTL_S = 60


def alert_key(pid: int) -> str:
    return f"ha:alert:{pid}"


def energy_key(pid: int) -> str:
    return f"ha:energy:{pid}"


def energy_acc_key(pid: int) -> str:
    return f"ha:energy:acc:{pid}"


def fired_key(link_id: int) -> str:
    return f"ha:fired:{link_id}"


def _raw_key(pid: int) -> str:
    """Private: last-seen raw HA sensor reading for `pid`, used to detect a
    counter_delta reset. Not part of the public key vocabulary -- nothing
    outside `app.tasks.home_assistant` should read or write it."""
    return f"ha:energy:raw:{pid}"


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
