"""Spool inventory: remaining weight, AMS slot assignment (incl. RFID
auto-assign), and idempotent per-job filament usage recording (P5 spool
tray plan)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import PrintJob, Spool, SpoolSlot, SpoolUsage
from app.models.enums import ArchiveResult, SpoolUsageSource
from app.services.costing import charged_grams

EXTERNAL_UNIT = 255
EXTERNAL_SLOT = 254
UNMAPPED = -1


def global_tray(unit: int, slot: int) -> int:
    if unit == EXTERNAL_UNIT:
        return EXTERNAL_SLOT
    return unit * 4 + slot


def split_tray(g: int) -> tuple[int, int]:
    if g == EXTERNAL_SLOT:
        return (EXTERNAL_UNIT, 0)
    return divmod(g, 4)


def remaining_g(s: Spool) -> float:
    return max(s.label_weight_g - s.weight_used_g, 0.0)


def is_low_stock(s: Spool) -> bool:
    return remaining_g(s) <= s.low_stock_g


def rate_per_kg(s: Spool | None, global_per_kg: float) -> float:
    if s is not None and s.cost_per_kg is not None:
        return s.cost_per_kg
    return global_per_kg


def _slot_row(session: Session, printer_id: int, unit: int, slot: int) -> SpoolSlot | None:
    return session.scalars(
        select(SpoolSlot).where(
            SpoolSlot.printer_id == printer_id,
            SpoolSlot.ams_unit == unit,
            SpoolSlot.slot == slot,
        )
    ).first()


def assign_slot(
    session: Session, printer_id: int, unit: int, slot: int, spool_id: int | None
) -> None:
    """Point a printer's AMS slot at ``spool_id`` (or clear it on ``None``).
    A spool occupies at most one slot: assigning it elsewhere frees its old
    slot first."""
    existing = _slot_row(session, printer_id, unit, slot)
    if spool_id is None:
        if existing is not None:
            session.delete(existing)
            session.commit()
        return
    old = session.scalars(select(SpoolSlot).where(SpoolSlot.spool_id == spool_id)).first()
    if old is not None and old is not existing:
        session.delete(old)
        session.flush()
    if existing is not None:
        existing.spool_id = spool_id
    else:
        session.add(SpoolSlot(printer_id=printer_id, ams_unit=unit, slot=slot, spool_id=spool_id))
    session.commit()


def spool_for_tray(session: Session, printer_id: int, tray: int | None) -> Spool | None:
    if tray is None or tray == UNMAPPED:
        return None
    unit, slot = split_tray(tray)
    row = _slot_row(session, printer_id, unit, slot)
    if row is None:
        return None
    return session.get(Spool, row.spool_id)


def auto_assign_from_trays(session: Session, printer_id: int, trays: list[dict]) -> bool:
    """RFID auto-assign: a tray reporting a ``tray_uuid`` is matched to the
    spool registered with that UUID (never creates one); an empty tray
    (no UUID) clears its slot. Returns True if anything changed."""
    changed = False
    for t in trays:
        g = t.get("tray")
        if g is None:
            continue
        unit, slot = split_tray(g)
        uuid = t.get("tray_uuid")
        current = _slot_row(session, printer_id, unit, slot)
        if not uuid:
            if current is not None:
                session.delete(current)
                session.flush()
                changed = True
            continue
        spool = session.scalars(select(Spool).where(Spool.tray_uuid == uuid)).first()
        if spool is None:
            continue
        if current is not None and current.spool_id == spool.id:
            continue
        assign_slot(session, printer_id, unit, slot, spool.id)
        changed = True
    if changed:
        session.commit()
    return changed


def _clear_auto_usage(session: Session, job: PrintJob) -> None:
    """Reverse and delete this job's ``3mf``/``ams_delta`` rows so a
    redelivered archive can be rewritten idempotently. ``manual`` rows are
    never touched."""
    rows = session.scalars(
        select(SpoolUsage).where(
            SpoolUsage.print_job_id == job.id,
            SpoolUsage.source.in_([SpoolUsageSource.THREE_MF, SpoolUsageSource.AMS_DELTA]),
        )
    ).all()
    for row in rows:
        spool = session.get(Spool, row.spool_id)
        if spool is not None:
            spool.weight_used_g = max(spool.weight_used_g - row.grams, 0.0)
        session.delete(row)
    if rows:
        session.flush()


def _charge(grams: float | None, result: ArchiveResult, job: PrintJob) -> float | None:
    return charged_grams(
        grams,
        result,
        layer=job.layer,
        total_layers=job.total_layers,
        progress_pct=job.progress_pct,
    )


def record_job_usage(
    session: Session, job: PrintJob, est_filament: list[dict], result: ArchiveResult
) -> list[tuple[Spool | None, float]]:
    """Debit spools for a finished job's filament use. Idempotent: a
    redelivery first reverses+deletes this job's prior ``3mf``/``ams_delta``
    rows before rewriting (``manual`` rows are untouched). Prefers per-row
    tray info (source ``3mf``); when ``est_filament`` rows carry no tray at
    all, falls back to the start/end AMS tray-weight snapshot on the job
    (source ``ams_delta``). Never commits -- the caller does. Returns
    ``(spool_or_None, grams)`` for every charged row, including spool-less
    ones, so the caller can price the run."""
    _clear_auto_usage(session, job)
    now = datetime.now(UTC)
    out: list[tuple[Spool | None, float]] = []

    has_trays = any(r.get("tray") is not None for r in est_filament)
    if has_trays:
        for r in est_filament:
            grams = _charge(r.get("grams"), result, job)
            if grams is None or grams <= 0:
                continue
            spool = spool_for_tray(session, job.printer_id, r.get("tray"))
            out.append((spool, grams))
            if spool is not None:
                session.add(
                    SpoolUsage(
                        spool_id=spool.id,
                        print_job_id=job.id,
                        grams=grams,
                        source=SpoolUsageSource.THREE_MF,
                    )
                )
                spool.weight_used_g += grams
                spool.last_used_at = now
        session.flush()
        return out

    snapshot = job.tray_snapshot or {}
    start = snapshot.get("start") or []
    end = snapshot.get("end") or []
    for tray_idx, (s_pct, e_pct) in enumerate(zip(start, end, strict=False)):
        if s_pct is None or e_pct is None:
            continue
        delta_pct = s_pct - e_pct
        if delta_pct <= 0:
            continue
        spool = spool_for_tray(session, job.printer_id, tray_idx)
        if spool is None:
            continue
        # The snapshot delta is an observed physical weight change, already
        # reflecting whatever printed before a cancel/fail -- no partial-run
        # ratio to re-apply (unlike the sliced-estimate `3mf` path above).
        grams = spool.label_weight_g * delta_pct / 100
        if grams <= 0:
            continue
        out.append((spool, grams))
        session.add(
            SpoolUsage(
                spool_id=spool.id,
                print_job_id=job.id,
                grams=grams,
                source=SpoolUsageSource.AMS_DELTA,
            )
        )
        spool.weight_used_g += grams
        spool.last_used_at = now
    session.flush()
    return out


async def slot_map(db: AsyncSession, printer_ids: Iterable[int]) -> dict[tuple[int, int], Spool]:
    """``(printer_id, global tray) -> Spool`` for every occupied slot across
    the given printers."""
    ids = list(printer_ids)
    if not ids:
        return {}
    rows = (
        (await db.execute(select(SpoolSlot).where(SpoolSlot.printer_id.in_(ids)))).scalars().all()
    )
    result: dict[tuple[int, int], Spool] = {}
    for row in rows:
        spool = await db.get(Spool, row.spool_id)
        if spool is not None:
            result[(row.printer_id, global_tray(row.ams_unit, row.slot))] = spool
    return result
