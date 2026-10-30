"""Queue-card filament details: the sliced file's per-slot filaments joined
to the spools mapped onto them, a colour-mismatch flag, and an up-front cost
estimate (all computed on read -- nothing is stored)."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Spool, SpoolSlot
from app.models.library import File, PrintQueueEntry
from app.models.printing import Printer
from app.models.processing import BlobMeta
from app.schemas.queue import QueueFilamentOut
from app.services.ams_match import DE_OK, delta_e_ok
from app.services.app_config import get_app_config
from app.services.costing import CostRates, filament_cost, price_run
from app.services.spool_inventory import UNMAPPED, global_tray


@dataclass
class EntryDetails:
    filaments: list[QueueFilamentOut] = field(default_factory=list)
    colour_mismatch: bool = False
    estimated_cost: float | None = None


def pick_plate(plates: list[dict], plate_no: int | None) -> dict | None:
    if plate_no is None and plates:
        return plates[0]
    return next((p for p in plates if p.get("index") == plate_no), None)


def spool_label(s: Spool) -> str:
    return " ".join(x for x in (s.brand, s.color_name or s.material_type) if x)


def colour_differs(want: str | None, have: str | None) -> bool:
    """True when two hex colours differ beyond the AMS-match tolerance
    (OKLab distance, ``ams_match.DE_OK``); unknown colours never differ."""
    if not want or not have:
        return False
    try:
        return delta_e_ok(want, have) > DE_OK
    except ValueError:
        return False


def tray_for(mapping: list[int], i: int, fid: int) -> int | None:
    tray = None
    if 0 <= fid - 1 < len(mapping):
        tray = mapping[fid - 1]
    elif i < len(mapping):
        tray = mapping[i]
    return None if tray is None or tray == UNMAPPED else tray


def build_details(
    plate: dict | None,
    mapping: list[int],
    spool_for_tray: dict[int, Spool],
    *,
    copies: int,
    rates: CostRates,
    wear_per_hour: float | None,
) -> EntryDetails:
    """Pure core: ``spool_for_tray`` is ``{global_tray: Spool}`` for the
    entry's target printer (empty when untargeted)."""
    if plate is None:
        return EntryDetails()
    rows: list[QueueFilamentOut] = []
    usages: list[tuple[Spool | None, float]] = []
    for i, f in enumerate(plate.get("filaments") or []):
        fid = f["filament_id"] if f.get("filament_id") is not None else i + 1
        tray = tray_for(mapping, i, fid)
        spool = spool_for_tray.get(tray) if tray is not None else None
        want = f.get("color")
        mismatch = spool is not None and colour_differs(want, spool.color_hex)
        rows.append(
            QueueFilamentOut(
                slot=fid,
                type=f.get("type"),
                color=want,
                grams=f.get("used_g"),
                tray=tray,
                spool_id=spool.id if spool else None,
                spool_name=spool_label(spool) if spool else None,
                spool_color=spool.color_hex if spool else None,
                colour_mismatch=mismatch,
            )
        )
        if f.get("used_g") is not None:
            usages.append((spool, f["used_g"]))
    est = estimate_cost(plate, usages, rates, wear_per_hour)
    return EntryDetails(
        filaments=rows,
        colour_mismatch=any(r.colour_mismatch for r in rows),
        estimated_cost=None if est is None else est * copies,
    )


def estimate_cost(
    plate: dict,
    usages: list[tuple[Spool | None, float]],
    rates: CostRates,
    wear_per_hour: float | None,
) -> float | None:
    """Per-copy cost: per-spool filament pricing (global rate for a spool-less
    slot, or the plate's own ``weight_g`` when no per-slot grams exist), plus
    machine and wear rates x the predicted time."""
    fc = filament_cost(usages, rates.filament_per_kg)
    if fc is None and plate.get("weight_g") is not None:
        fc = plate["weight_g"] / 1000 * rates.filament_per_kg
    cost = price_run(None, plate.get("prediction_s"), rates, wear_per_hour=wear_per_hour)
    parts = [c for c in (fc, cost.machine_cost, cost.wear_cost) if c is not None]
    return sum(parts) if parts else None


async def details_for_entries(
    db: AsyncSession, settings: Settings, entries: list[PrintQueueEntry]
) -> dict[int, EntryDetails]:
    """``entry.id -> EntryDetails`` in four batched queries."""
    sliced = {e.sliced_file_id for e in entries if e.sliced_file_id is not None}
    if not sliced:
        return {}
    raws = {
        fid: (raw or {})
        for fid, raw in (
            await db.execute(
                select(File.id, BlobMeta.raw)
                .join(BlobMeta, BlobMeta.blob_hash == File.blob_hash)
                .where(File.id.in_(sliced))
            )
        ).all()
    }
    printer_ids = {e.printer_id or e.assigned_printer_id for e in entries} - {None}
    spools: dict[int, dict[int, Spool]] = {}
    wear: dict[int, float | None] = {}
    if printer_ids:
        for slot, spool in (
            await db.execute(
                select(SpoolSlot, Spool)
                .join(Spool, Spool.id == SpoolSlot.spool_id)
                .where(SpoolSlot.printer_id.in_(printer_ids))
            )
        ).all():
            spools.setdefault(slot.printer_id, {})[global_tray(slot.ams_unit, slot.slot)] = spool
        wear = dict(
            (
                await db.execute(
                    select(Printer.id, Printer.wear_cost_per_hour).where(
                        Printer.id.in_(printer_ids)
                    )
                )
            ).all()
        )
    rates = CostRates.from_config(await get_app_config(db, settings))
    out: dict[int, EntryDetails] = {}
    for e in entries:
        raw = raws.get(e.sliced_file_id) if e.sliced_file_id is not None else None
        if raw is None:
            continue
        pid = e.printer_id or e.assigned_printer_id
        mapping = e.ams_mapping if isinstance(e.ams_mapping, list) else []
        out[e.id] = build_details(
            pick_plate(raw.get("plates") or [], e.plate),
            mapping,
            spools.get(pid, {}) if pid is not None else {},
            copies=e.copies,
            rates=rates,
            wear_per_hour=wear.get(pid) if pid is not None else None,
        )
    return out
