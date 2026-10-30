"""AMS slot backup/restore (D2): one ``settings`` row per printer under
``ams_backup:<printer_id>`` holding every configured slot's filament profile
(type, colour, filament id, nozzle range) and its K-profile reference."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Setting
from app.printers.filament_ids import generic_filament_id

_DEFAULT_RANGE = (190, 230)


def key(printer_id: int) -> str:
    return f"ams_backup:{printer_id}"


def snapshot_slots(trays: list[dict]) -> list[dict]:
    """The restorable slots of a state's ``trays``: configured ones only."""
    out: list[dict] = []
    for t in trays:
        if not t.get("tray_type") or t.get("ams_unit") is None or t.get("tray") is None:
            continue
        out.append(
            {
                "ams_id": t["ams_unit"],
                "tray_id": t["tray"],
                "tray_type": t["tray_type"],
                "color": (t.get("color") or "").lstrip("#") or None,
                "tray_info_idx": t.get("tray_info_idx"),
                "sub_brand": t.get("sub_brand"),
                "nozzle_min": t.get("nozzle_min"),
                "nozzle_max": t.get("nozzle_max"),
                "cali_idx": t.get("cali_idx"),
                "k_value": t.get("k_value"),
            }
        )
    return out


def configure_args(slot: dict) -> dict:
    """``ams_slot_configure`` args for a stored slot (default range when unknown)."""
    lo, hi = slot.get("nozzle_min"), slot.get("nozzle_max")
    if lo is None or hi is None or lo > hi:
        lo, hi = _DEFAULT_RANGE
    return {
        "ams_id": slot["ams_id"],
        "tray_id": slot["tray_id"],
        "tray_info_idx": slot.get("tray_info_idx") or generic_filament_id(slot["tray_type"]),
        "tray_type": slot["tray_type"],
        "sub_brand": slot.get("sub_brand") or "",
        "color": slot.get("color") or "FFFFFF",
        "nozzle_min": lo,
        "nozzle_max": hi,
    }


async def save(db: AsyncSession, printer_id: int, slots: list[dict]) -> dict:
    value = {"taken_at": datetime.now(UTC).isoformat(), "slots": slots}
    row = await db.get(Setting, key(printer_id))
    if row is None:
        db.add(Setting(key=key(printer_id), value=value))
    else:
        row.value = value
    await db.commit()
    return value


async def load(db: AsyncSession, printer_id: int) -> dict | None:
    row = await db.get(Setting, key(printer_id))
    return row.value if row else None
