"""T2 K-profiles (pressure advance): ``extrusion_cali_get`` parsing, the
per-printer table cache in Redis, slot-selection helpers and the drift fixer
that puts a spool's stored profile back when the printer lost the selection
(power cycle: ``cali_idx`` falls back to -1 while everything else is unchanged).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Printer, SpoolSlot
from app.models.kprofiles import SpoolKProfile
from app.printers.base import cmd_result_key, command_channel
from app.printers.capabilities import capabilities_for

log = logging.getLogger(__name__)

CACHE_TTL_S = 600  # K-profile table cache per printer + nozzle
REAPPLY_DEBOUNCE_S = 600  # per tray
IDLE_STATES = frozenset({"IDLE", "FINISH", "FAILED"})


def cache_key(printer_id: int, nozzle: str) -> str:
    return f"printer:{printer_id}:kprofiles:{nozzle}"


def debounce_key(printer_id: int, ams_unit: int, tray: int) -> str:
    return f"printer:{printer_id}:kprofile_reapply:{ams_unit}:{tray}"


def norm_nozzle(value: object) -> str | None:
    try:
        return f"{float(value):.1f}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _f(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def parse_kprofiles(filaments: object, envelope_nozzle: object) -> list[dict]:
    """``extrusion_cali_get`` ``filaments[]`` -> profile dicts. The printer
    reports ``nozzle_diameter`` on the response envelope only, so a per-item
    value falls back to it (bambuddy #1748), and finally to ``"0.4"``."""
    out: list[dict] = []
    if not isinstance(filaments, list):
        return out
    fallback = norm_nozzle(envelope_nozzle) or "0.4"
    for i, f in enumerate(filaments):
        if not isinstance(f, dict):
            continue
        try:
            out.append(
                {
                    "cali_idx": int(f.get("cali_idx", i)),
                    "setting_id": f.get("setting_id") or None,
                    "filament_id": str(f.get("filament_id") or ""),
                    "name": str(f.get("name") or ""),
                    "k_value": _f(f.get("k_value")),
                    "n_coef": _f(f.get("n_coef")),
                    "extruder_id": int(f.get("extruder_id", 0)),
                    "nozzle_id": str(f.get("nozzle_id") or ""),
                    "nozzle_diameter": norm_nozzle(f.get("nozzle_diameter")) or fallback,
                    "ams_id": int(f.get("ams_id", 0)),
                    "tray_id": int(f.get("tray_id", -1)),
                }
            )
        except (TypeError, ValueError):
            continue  # one malformed entry must not hide the rest
    return out


def store_cache(redis_client, printer_id: int, nozzle: str, profiles: list[dict]) -> dict:
    entry = {
        "profiles": profiles,
        "nozzle_diameter": nozzle,
        "fetched_at": datetime.now(UTC).isoformat(),
    }
    redis_client.set(cache_key(printer_id, nozzle), json.dumps(entry), ex=CACHE_TTL_S)
    return entry


def read_cache_sync(redis_client, printer_id: int, nozzle: str) -> dict | None:
    return _load(redis_client.get(cache_key(printer_id, nozzle)))


async def read_cache_async(redis_client, printer_id: int, nozzle: str) -> dict | None:
    return _load(await redis_client.get(cache_key(printer_id, nozzle)))


def _load(raw) -> dict | None:
    if not raw:
        return None
    try:
        doc = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("profiles"), list) else None


def invalidate_cache(redis_client, printer_id: int, nozzle: str) -> None:
    redis_client.delete(cache_key(printer_id, nozzle))


def find_in_cache(cache: dict | None, setting_id: str) -> dict | None:
    for p in (cache or {}).get("profiles", []):
        if p.get("setting_id") == setting_id:
            return p
    return None


def selection_lost(cali_idx: object) -> bool:
    """``cali_idx`` -1 or missing = the printer lost (or never had) the selection."""
    if cali_idx is None:
        return True
    try:
        return int(cali_idx) < 0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def has_lost_trays(trays: list[dict]) -> bool:
    return any(t.get("tray_type") and selection_lost(t.get("cali_idx")) for t in trays or [])


def match_profile(rows: list[SpoolKProfile], nozzle_mm: float | None, dual: bool):
    """The stored profile for the printer's current nozzle. Single-nozzle
    printers only use extruder 0; a dual-nozzle printer's tray->extruder map is
    not reported, so the lowest extruder wins."""
    cands = [
        r
        for r in rows
        if nozzle_mm is None or abs(float(r.nozzle_diameter) - float(nozzle_mm)) < 0.005
    ]
    if not dual:
        cands = [r for r in cands if r.extruder == 0]
    return min(cands, key=lambda r: r.extruder, default=None)


def resolve_cali_idx(redis_client, printer_id: int, row: SpoolKProfile) -> int | None:
    """The row's current ``cali_idx``: the cached table wins (indices shift when
    other profiles are deleted), else the stored value."""
    nozzle = norm_nozzle(row.nozzle_diameter)
    if row.setting_id and nozzle and redis_client is not None:
        hit = find_in_cache(read_cache_sync(redis_client, printer_id, nozzle), row.setting_id)
        if hit is not None:
            return int(hit["cali_idx"])
    return row.cali_idx


SelectFn = Callable[[int, int, int, str, str], None]  # ams_id, tray_id, cali_idx, filament, nozzle


def reapply_lost_kprofiles(
    session: Session,
    printer_id: int,
    state: dict,
    *,
    redis_client,
    send: SelectFn,
    debounce: bool = True,
) -> list[dict]:
    """Re-select the stored K-profile on every loaded tray whose selection was
    lost. Never touches a tray holding a real profile (``cali_idx`` >= 0), a
    tray without an assigned spool or without a stored profile. Debounced per
    tray (10 min); each re-selection is audited as ``kprofile.reapply``."""
    trays = [
        t
        for t in (state.get("trays") or [])
        if t.get("tray_type") and selection_lost(t.get("cali_idx")) and t.get("tray_info_idx")
    ]
    if not trays:
        return []
    printer = session.get(Printer, printer_id)
    if printer is None:
        return []
    dual = capabilities_for(printer.model, printer.serial).dual_nozzle
    nozzle_mm = state.get("nozzle_mm")
    slots = {
        (r.ams_unit, r.slot): r.spool_id
        for r in session.scalars(select(SpoolSlot).where(SpoolSlot.printer_id == printer_id))
    }
    done: list[dict] = []
    for tray in trays:
        spool_id = slots.get((tray.get("ams_unit"), tray.get("tray")))
        if spool_id is None:
            continue
        rows = list(
            session.scalars(
                select(SpoolKProfile).where(
                    SpoolKProfile.spool_id == spool_id, SpoolKProfile.printer_id == printer_id
                )
            )
        )
        row = match_profile(rows, nozzle_mm, dual)
        if row is None:
            continue
        cali = resolve_cali_idx(redis_client, printer_id, row)
        if cali is None or cali < 0:
            continue
        key = debounce_key(printer_id, tray["ams_unit"], tray["tray"])
        if (
            debounce
            and redis_client is not None
            and not redis_client.set(key, "1", nx=True, ex=REAPPLY_DEBOUNCE_S)
        ):
            continue
        nozzle = norm_nozzle(row.nozzle_diameter) or "0.4"
        try:
            send(tray["ams_unit"], tray["tray"], cali, tray["tray_info_idx"], nozzle)
        except Exception as e:  # noqa: BLE001 -- type only
            log.warning("kprofile reapply send failed: %s", type(e).__name__)
            continue
        info = {
            "ams_id": tray["ams_unit"],
            "tray_id": tray["tray"],
            "spool_id": spool_id,
            "cali_idx": cali,
            "filament_id": tray["tray_info_idx"],
            "nozzle_diameter": nozzle,
            "setting_id": row.setting_id,
        }
        session.add(AuditLog(printer_id=printer_id, action="kprofile.reapply", payload=info))
        done.append(info)
    if done:
        session.commit()
    return done


def request_reapply_sync(redis_client, printer_id: int, wait_s: float = 3.0) -> bool:
    """Ask printerd to run the drift fixer now (used right before a queued job
    is sent) and wait briefly for it. Best effort: never raises."""
    try:
        request_id = uuid.uuid4().hex
        receivers = redis_client.publish(
            command_channel(printer_id),
            json.dumps({"command": "kprofile_reapply", "args": {}, "request_id": request_id}),
        )
        if not receivers:  # printerd is not listening
            return False
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if redis_client.get(cmd_result_key(printer_id, request_id)):
                return True
            time.sleep(0.1)
    except Exception as e:  # noqa: BLE001
        log.warning("kprofile reapply request failed: %s", type(e).__name__)
    return False
