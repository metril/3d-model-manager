"""HMS notification dedup: one notification per fault episode.

A fault that flickers (clears and re-raises within seconds) must not notify on
every re-raise. Per (printer, code) we keep the last-notified and last-cleared
times in Redis and re-notify only when the fault stayed clear for
``hms_renotify_clear_s`` or the episode outlived ``hms_renotify_after_s``.
Thresholds live in the ``notify_settings`` settings row.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import Setting

SETTINGS_KEY = "notify_settings"
DEFAULT_CLEAR_S = 300
DEFAULT_AFTER_S = 3600


@dataclass(frozen=True)
class NotifySettings:
    hms_renotify_clear_s: int = DEFAULT_CLEAR_S
    hms_renotify_after_s: int = DEFAULT_AFTER_S


def _to_settings(value: dict | None) -> NotifySettings:
    v = value or {}
    return NotifySettings(
        hms_renotify_clear_s=int(v.get("hms_renotify_clear_s", DEFAULT_CLEAR_S)),
        hms_renotify_after_s=int(v.get("hms_renotify_after_s", DEFAULT_AFTER_S)),
    )


def get_settings_sync(session: Session) -> NotifySettings:
    row = session.get(Setting, SETTINGS_KEY)
    return _to_settings(row.value if row else None)


async def get_settings_async(db: AsyncSession) -> NotifySettings:
    row = await db.get(Setting, SETTINGS_KEY)
    return _to_settings(row.value if row else None)


async def set_settings(db: AsyncSession, patch: dict) -> NotifySettings:
    row = await db.get(Setting, SETTINGS_KEY)
    value = dict(row.value) if row is not None else {}
    value.update(patch)
    if row is None:
        db.add(Setting(key=SETTINGS_KEY, value=value))
    else:
        row.value = value
    await db.commit()
    return _to_settings(value)


def _key(printer_id: int, code: str) -> str:
    return f"notify:hms:{printer_id}:{code}"


def should_notify(
    r,
    printer_id: int,
    code: str,
    cfg: NotifySettings,
    *,
    now: float | None = None,
) -> bool:
    """A code just (re)appeared. True = send a notification (and record it)."""
    now = time.time() if now is None else now
    key = _key(printer_id, code)
    rec = r.hgetall(key)
    notified = float(rec[b"notified"]) if b"notified" in rec else None
    cleared = float(rec[b"cleared"]) if b"cleared" in rec else None
    if notified is not None:
        renotify = (cleared is not None and now - cleared >= cfg.hms_renotify_clear_s) or (
            now - notified >= cfg.hms_renotify_after_s
        )
        if not renotify:
            r.hdel(key, "cleared")  # same episode, active again
            return False
    r.hset(key, mapping={"notified": now})
    r.hdel(key, "cleared")
    r.expire(key, int(max(cfg.hms_renotify_after_s, cfg.hms_renotify_clear_s) * 2 + 60))
    return True


def mark_cleared(r, printer_id: int, code: str, *, now: float | None = None) -> None:
    key = _key(printer_id, code)
    if r.exists(key):
        r.hset(key, mapping={"cleared": time.time() if now is None else now})
