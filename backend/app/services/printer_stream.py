"""Live ``printer.status`` / ``printer.delta`` SSE events.

A per-connection poller reads printerd's Redis state for the allowed printers
(one ``MGET``), builds the shared ``PrinterStatusOut`` (DB overlays cached per
process, invalidated on ``print_job.updated``), and emits a full status on
connect / every ``KEEPALIVE_S``, otherwise an RFC 7386 merge-patch against the
last status *sent* -- only when something other than ``VOLATILE`` changed.
"""

from __future__ import annotations

import hashlib
import json
import time

from app.config import Settings
from app.db import get_sessionmaker
from app.models.printing import Printer
from app.printers.base import state_key
from app.schemas.printers import PrinterStatusOut
from app.services.printer_status import build_status

TICK_S = 1.0  # poll cadence == max delta rate per printer
KEEPALIVE_S = 300.0  # full status resend
CACHE_TTL_S = 5.0  # DB-overlay freshness bound
VOLATILE = frozenset({"last_report_at"})

# (printer_id) -> (state fingerprint, built-at monotonic, status)
_cache: dict[int, tuple[str, float, PrinterStatusOut]] = {}


def invalidate(printer_id: int | None = None) -> None:
    if printer_id is None:
        _cache.clear()
    else:
        _cache.pop(printer_id, None)


def _strip(v):
    if isinstance(v, dict):
        return {k: _strip(x) for k, x in v.items() if x is not None}
    return v


def comparable(doc: dict) -> dict:
    return _strip({k: v for k, v in doc.items() if k not in VOLATILE})


def merge_patch_diff(a, b):
    """RFC 7386 patch turning ``a`` into ``b`` (dicts recurse, anything else
    -- lists included -- is replaced whole, removed keys are ``None``)."""
    patch: dict = {}
    for k in a.keys() - b.keys():
        patch[k] = None
    for k, bv in b.items():
        if k not in a:
            patch[k] = bv
        elif a[k] != bv:
            av = a[k]
            patch[k] = (
                merge_patch_diff(av, bv) if isinstance(av, dict) and isinstance(bv, dict) else bv
            )
    return patch


def apply_merge_patch(target, patch):
    if not isinstance(patch, dict):
        return patch
    out = dict(target) if isinstance(target, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = apply_merge_patch(out.get(k), v)
    return out


def _fingerprint(state: dict | None) -> str:
    if state is None:
        return "-"
    s = {k: v for k, v in state.items() if k not in VOLATILE}
    return hashlib.sha1(json.dumps(s, sort_keys=True, default=str).encode()).hexdigest()


async def _status_for(settings: Settings, pid: int, state: dict | None) -> PrinterStatusOut | None:
    fp = _fingerprint(state)
    now = time.monotonic()
    hit = _cache.get(pid)
    if hit is not None and hit[0] == fp and now - hit[1] < CACHE_TTL_S:
        out = hit[2].model_copy()
    else:
        async with get_sessionmaker()() as db:
            printer = await db.get(Printer, pid)
            if printer is None:
                _cache.pop(pid, None)
                return None
            out = await build_status(db, settings, printer, state)
        _cache[pid] = (fp, now, out)
        out = out.model_copy()
    out.last_report_at = (state or {}).get("last_report_at", out.last_report_at)
    return out


class PrinterStream:
    """State for one SSE connection. ``poll()`` returns the events due now."""

    def __init__(self, settings: Settings, redis_client, printer_ids: list[int]) -> None:
        self.settings = settings
        self.redis = redis_client
        self.ids = sorted(printer_ids)
        self._sent: dict[int, dict] = {}
        self._full_at: dict[int, float] = {}

    async def poll(self) -> list[dict]:
        if not self.ids:
            return []
        raws = await self.redis.mget([state_key(p) for p in self.ids])
        now = time.monotonic()
        events: list[dict] = []
        for pid, raw in zip(self.ids, raws, strict=True):
            try:
                state = json.loads(raw) if raw else None
            except (TypeError, ValueError):
                state = None
            out = await _status_for(self.settings, pid, state)
            if out is None:
                continue
            doc = out.model_dump(mode="json")
            cmp = comparable(doc)
            last = self._sent.get(pid)
            if last is None or now - self._full_at[pid] >= KEEPALIVE_S:
                events.append({"type": "printer.status", "printer_id": pid, "status": doc})
                self._full_at[pid] = now
            elif cmp != last:
                events.append(
                    {
                        "type": "printer.delta",
                        "printer_id": pid,
                        "patch": merge_patch_diff(last, cmp),
                    }
                )
            else:
                continue
            self._sent[pid] = cmp
        return events
