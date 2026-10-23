"""Spoolman connector (Q9). Config lives in ``settings["spoolman"]`` as
``{url, mode}`` with ``mode`` one of ``off|mirror|authoritative``.

* ``off`` (default): every public function is a no-op -- no HTTP, no extra
  column written, no beat work.
* ``mirror``: local is the source of truth; usage and AMS locations are
  pushed, remote weight is only recorded (``last_pulled_weight``).
* ``authoritative``: as mirror, plus the remote used-weight overwrites the
  local ``weight_used_g`` on pull/reconcile.

HTTP is async httpx; DB logic is sync (Celery / archive / thread-run routes)
and bridges with ``_run``. Every push/pull path swallows transport errors
into ``spoolman_sync_state.last_error`` -- Spoolman being down must never
break an archive or a slot assignment.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import Setting, Spool, SpoolmanPush, SpoolmanSyncState
from app.services.spool_inventory import global_tray

SETTINGS_KEY = "spoolman"
MODES = ("off", "mirror", "authoritative")
TIMEOUT_S = 10.0


class SpoolmanError(Exception):
    pass


@dataclass(frozen=True)
class SpoolmanConfig:
    url: str | None = None
    mode: str = "off"

    @property
    def active(self) -> bool:
        return self.mode in ("mirror", "authoritative") and bool(self.url)


def _cfg(value: dict | None) -> SpoolmanConfig:
    value = value or {}
    mode = value.get("mode") or "off"
    url = (value.get("url") or "").strip().rstrip("/") or None
    return SpoolmanConfig(url=url, mode=mode if mode in MODES else "off")


def get_config_sync(session: Session) -> SpoolmanConfig:
    row = session.get(Setting, SETTINGS_KEY)
    return _cfg(row.value if row else None)


async def get_config(db: AsyncSession) -> SpoolmanConfig:
    row = await db.get(Setting, SETTINGS_KEY)
    return _cfg(row.value if row else None)


async def set_config(db: AsyncSession, *, url: str | None, mode: str) -> SpoolmanConfig:
    value = {"url": (url or "").strip().rstrip("/") or None, "mode": mode}
    row = await db.get(Setting, SETTINGS_KEY)
    if row is None:
        db.add(Setting(key=SETTINGS_KEY, value=value))
    else:
        row.value = value  # fresh dict so JSONB change-tracking sees it
    await db.commit()
    return _cfg(value)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run ``coro`` from sync code; safe even if a loop is running here."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict[str, Any] = {}

    def _t() -> None:
        try:
            box["v"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001
            box["e"] = e

    th = threading.Thread(target=_t)
    th.start()
    th.join()
    if "e" in box:
        raise box["e"]
    return box["v"]


async def _request(base_url: str, method: str, path: str, *, json: dict | None = None) -> Any:
    try:
        async with httpx.AsyncClient(base_url=f"{base_url}/api/v1", timeout=TIMEOUT_S) as c:
            r = await c.request(method, path, json=json)
            r.raise_for_status()
            return r.json() if r.content else None
    except httpx.HTTPError as e:
        raise SpoolmanError(str(e) or e.__class__.__name__) from e


async def test_connection(url: str) -> dict:
    """Probe ``/info`` (falls back to ``/health``). Raises SpoolmanError."""
    base = url.strip().rstrip("/")
    try:
        info = await _request(base, "GET", "/info")
    except SpoolmanError:
        info = await _request(base, "GET", "/health")
    return info if isinstance(info, dict) else {}


async def list_remote_spools(url: str) -> list[dict]:
    return await _request(url, "GET", "/spool") or []


def remote_summary(r: dict) -> dict:
    fil = r.get("filament") or {}
    vendor = fil.get("vendor") or {}
    return {
        "id": r["id"],
        "name": fil.get("name"),
        "material": fil.get("material"),
        "vendor": vendor.get("name"),
        "color_hex": fil.get("color_hex"),
        "remaining_weight": r.get("remaining_weight"),
        "used_weight": r.get("used_weight"),
        "location": r.get("location"),
    }


# ---------------------------------------------------------------------------
# state bookkeeping
# ---------------------------------------------------------------------------


def _state(session: Session, spool_id: int) -> SpoolmanSyncState:
    st = session.get(SpoolmanSyncState, spool_id)
    if st is None:
        st = SpoolmanSyncState(spool_id=spool_id)
        session.add(st)
    return st


def _ok_push(session: Session, spool: Spool) -> None:
    st = _state(session, spool.id)
    st.last_pushed_at = datetime.now(UTC)
    st.last_error = None


def _fail(session: Session, spool: Spool, err: Exception) -> None:
    st = _state(session, spool.id)
    st.last_error = str(err)[:500]


def _apply_pull(session: Session, spool: Spool, remote: dict, cfg: SpoolmanConfig) -> None:
    st = _state(session, spool.id)
    remaining = remote.get("remaining_weight")
    st.last_pulled_at = datetime.now(UTC)
    st.last_pulled_weight = remaining
    st.last_error = None
    if cfg.mode != "authoritative":
        return
    used = remote.get("used_weight")
    if used is None and remaining is not None:
        used = spool.label_weight_g - remaining
    if used is not None:
        spool.weight_used_g = max(float(used), 0.0)


# ---------------------------------------------------------------------------
# public operations (all no-ops when mode=off)
# ---------------------------------------------------------------------------


def pull_spool(session: Session, spool: Spool) -> dict | None:
    cfg = get_config_sync(session)
    if not cfg.active or spool.spoolman_id is None:
        return None
    try:
        remote = _run(_request(cfg.url, "GET", f"/spool/{spool.spoolman_id}"))
    except SpoolmanError as e:
        _fail(session, spool, e)
        session.commit()
        return None
    _apply_pull(session, spool, remote, cfg)
    session.commit()
    return remote


def push_usage(session: Session, spool: Spool, grams: float) -> bool:
    cfg = get_config_sync(session)
    if not cfg.active or spool.spoolman_id is None or grams == 0:
        return False
    try:
        _run(
            _request(cfg.url, "PUT", f"/spool/{spool.spoolman_id}/use", json={"use_weight": grams})
        )
    except SpoolmanError as e:
        _fail(session, spool, e)
        session.commit()
        return False
    _ok_push(session, spool)
    session.commit()
    return True


def push_usages(session: Session, job_id: int, usages: list[tuple[Spool | None, float]]) -> None:
    """Hook for ``record_job_usage``'s result. Pushes only the delta against
    what was already pushed for (job, spool), so archive redelivery never
    double-debits; the pushed total is recorded only after a 2xx, so a failed
    push is retried next time. Best effort; never raises."""
    try:
        if not get_config_sync(session).active:
            return
        totals: dict[int, tuple[Spool, float]] = {}
        for spool, grams in usages:
            if spool is not None and spool.spoolman_id is not None:
                prev = totals.get(spool.id, (spool, 0.0))[1]
                totals[spool.id] = (spool, prev + grams)
        for spool, total in totals.values():
            row = session.get(SpoolmanPush, (job_id, spool.id))
            delta = total - (row.grams if row is not None else 0.0)
            if abs(delta) < 1e-9:
                continue
            if push_usage(session, spool, delta):
                if row is None:
                    session.add(SpoolmanPush(print_job_id=job_id, spool_id=spool.id, grams=total))
                else:
                    row.grams = total
                session.commit()
    except Exception:  # noqa: BLE001
        session.rollback()


def location_for(printer_id: int, unit: int, slot: int) -> str:
    return f"printer:{printer_id}:{global_tray(unit, slot)}"


def set_location(session: Session, spool: Spool, location: str) -> bool:
    cfg = get_config_sync(session)
    if not cfg.active or spool.spoolman_id is None:
        return False
    try:
        _run(_request(cfg.url, "PATCH", f"/spool/{spool.spoolman_id}", json={"location": location}))
    except SpoolmanError as e:
        _fail(session, spool, e)
        session.commit()
        return False
    _state(session, spool.id).last_error = None
    session.commit()
    return True


def on_slot_change(
    session: Session,
    printer_id: int,
    unit: int,
    slot: int,
    old_spool_id: int | None,
    new_spool_id: int | None,
) -> None:
    """Hook for slot assignment: new spool gets ``printer:<id>:<tray>``, the
    displaced spool's location is cleared. Best effort; never raises."""
    try:
        if not get_config_sync(session).active:
            return
        if old_spool_id is not None and old_spool_id != new_spool_id:
            old = session.get(Spool, old_spool_id)
            if old is not None:
                set_location(session, old, "")
        if new_spool_id is not None:
            new = session.get(Spool, new_spool_id)
            if new is not None:
                set_location(session, new, location_for(printer_id, unit, slot))
    except Exception:  # noqa: BLE001
        session.rollback()


def reconcile(session: Session) -> dict:
    """One list call, then per linked spool: record/overwrite weight."""
    cfg = get_config_sync(session)
    if not cfg.active:
        return {"skipped": True, "pulled": 0, "errors": 0}
    linked = session.scalars(select(Spool).where(Spool.spoolman_id.is_not(None))).all()
    if not linked:
        return {"skipped": False, "pulled": 0, "errors": 0}
    try:
        remotes = {r["id"]: r for r in _run(list_remote_spools(cfg.url))}
    except SpoolmanError as e:
        for s in linked:
            _fail(session, s, e)
        session.commit()
        return {"skipped": False, "pulled": 0, "errors": len(linked)}
    pulled = errors = 0
    for s in linked:
        remote = remotes.get(s.spoolman_id)
        if remote is None:
            _fail(session, s, SpoolmanError("spool missing in Spoolman"))
            errors += 1
            continue
        _apply_pull(session, s, remote, cfg)
        pulled += 1
    session.commit()
    return {"skipped": False, "pulled": pulled, "errors": errors}
