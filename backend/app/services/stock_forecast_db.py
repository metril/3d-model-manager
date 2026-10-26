"""Database side of the stock forecast: loads spools, usage history, queued
demand and settings, runs :mod:`stock_forecast`, and fires reorder / stock
break notifications on transitions. Sync (API goes through ``run_sync``)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import File, Model, PrintQueueEntry, Setting, Spool, SpoolUsage
from app.models.enums import QueueState
from app.models.inventory import StockSkuSettings
from app.models.processing import BlobMeta
from app.services import stock_forecast as sf

log = logging.getLogger(__name__)

SETTINGS_KEY = "stock_forecast"
ALERT_STATE_KEY = "stock_forecast_alerts"
HISTORY_DAYS = 180
_OPEN_STATES = (QueueState.WAITING, QueueState.SLICING, QueueState.READY, QueueState.HELD)


class ForecastConfig(BaseModel):
    enabled: bool = True
    lead_time_days: int = Field(0, ge=0, le=365)
    safety_margin_value: float = Field(sf.DEFAULT_SAFETY_MARGIN_VALUE, ge=0, le=100000)
    safety_margin_unit: str = Field(sf.DEFAULT_SAFETY_MARGIN_UNIT, pattern="^(days|g)$")
    include_queue: bool = True
    notify: bool = True


def get_config(session: Session) -> ForecastConfig:
    row = session.get(Setting, SETTINGS_KEY)
    return ForecastConfig(**(row.value if row else {}))


def set_config(session: Session, patch: dict) -> ForecastConfig:
    row = session.get(Setting, SETTINGS_KEY)
    value = {**(row.value if row else {}), **patch}
    cfg = ForecastConfig(**value)
    if row is None:
        session.add(Setting(key=SETTINGS_KEY, value=cfg.model_dump()))
    else:
        row.value = cfg.model_dump()
    session.commit()
    return cfg


def color_key(s: Spool) -> str:
    return (s.color_name or s.color_hex or "").strip()


def _stock_spool(s: Spool) -> sf.StockSpool:
    return sf.StockSpool(
        id=s.id,
        material=s.material_type,
        brand=s.brand,
        color=color_key(s),
        label_weight=s.label_weight_g,
        weight_used=s.weight_used_g,
        created_at=s.created_at,
    )


def _plate_grams(meta: BlobMeta, plate_no: int | None) -> dict[str, float] | None:
    """Per-material grams for ONE plate of a sliced file, or ``None`` when
    ``meta`` has no per-plate data (caller then uses whole-file figures). A
    plate-specific entry whose plate isn't in the metadata yields ``{}``."""
    plates = (meta.raw or {}).get("plates") or []
    if not plates:
        return None
    wanted = plate_no if plate_no is not None else plates[0].get("index")
    plate = next((p for p in plates if p.get("index") == wanted), None)
    if plate is None:
        return {}
    out: dict[str, float] = {}
    fils = [f for f in plate.get("filaments") or [] if f.get("used_g")]
    for f in fils:
        t = (f.get("type") or "").lower()
        if t:
            out[t] = out.get(t, 0.0) + float(f["used_g"])
    if out:
        return out
    grams = plate.get("weight_g")
    types = [t.lower() for t in (meta.filament_types or []) if t]
    if grams and types:
        return {t: float(grams) / len(types) for t in types}
    return {}


def queued_demand(session: Session) -> dict[str, float]:
    """Grams of filament still to print by queued work, per lower-cased
    material type. Trashed models are ignored; entries without slice metadata
    (nothing known about their filament) contribute nothing. Plate-specific
    entries count only their plate's grams. File/metadata lookups are batched."""
    q = (
        select(
            PrintQueueEntry.copies,
            PrintQueueEntry.plate,
            PrintQueueEntry.sliced_file_id,
            PrintQueueEntry.file_id,
        )
        .join(Model, Model.id == PrintQueueEntry.model_id)
        .where(PrintQueueEntry.state.in_(_OPEN_STATES), Model.deleted_at.is_(None))
    )
    entries = list(session.execute(q))
    fids = {fid for e in entries for fid in (e[2], e[3]) if fid is not None}
    blob_of: dict[int, str] = {}
    metas: dict[str, BlobMeta] = {}
    if fids:
        blob_of = {
            fid: bh
            for fid, bh in session.execute(select(File.id, File.blob_hash).where(File.id.in_(fids)))
        }
        metas = {
            m.blob_hash: m
            for m in session.scalars(
                select(BlobMeta).where(BlobMeta.blob_hash.in_(set(blob_of.values())))
            )
        }
    out: dict[str, float] = {}
    for copies, plate_no, sliced_id, file_id in entries:
        for fid in (sliced_id, file_id):
            if fid is None:
                continue
            meta = metas.get(blob_of.get(fid, ""))
            if meta is None:
                continue
            per_plate = _plate_grams(meta, plate_no)
            if per_plate is not None:
                for t, g in per_plate.items():
                    out[t] = out.get(t, 0.0) + g * max(copies, 1)
                break
            if not meta.filament_g:
                continue
            types = [t.lower() for t in (meta.filament_types or []) if t]
            if types:
                share = meta.filament_g * max(copies, 1) / len(types)
                for t in types:
                    out[t] = out.get(t, 0.0) + share
            break
    return out


def _sku_settings(session: Session) -> dict[sf.SkuKey, sf.SkuSettings]:
    return {
        (r.material_type, r.brand, r.color_key): sf.SkuSettings(
            lead_time_days=r.lead_time_days,
            safety_margin_value=r.safety_margin_value,
            safety_margin_unit=r.safety_margin_unit,
            alerts_snoozed=r.alerts_snoozed,
        )
        for r in session.scalars(select(StockSkuSettings))
    }


def compute(session: Session, now: datetime | None = None):
    """``(config, ForecastMap, material_demand)`` over the live (unarchived) spools."""
    now = now or datetime.now(UTC)
    cfg = get_config(session)
    spools = list(session.scalars(select(Spool).where(Spool.archived_at.is_(None))))
    since = now - timedelta(days=HISTORY_DAYS)
    history: dict[int, list[sf.UsageRecord]] = {}
    if spools:
        rows = session.execute(
            select(SpoolUsage.spool_id, SpoolUsage.created_at, SpoolUsage.grams).where(
                SpoolUsage.created_at >= since, SpoolUsage.grams > 0
            )
        )
        for sid, at, grams in rows:
            history.setdefault(sid, []).append(sf.UsageRecord(at, grams))
    demand = queued_demand(session) if cfg.include_queue else {}
    fm = sf.forecast_all(
        [_stock_spool(s) for s in spools],
        history,
        _sku_settings(session),
        cfg.lead_time_days,
        now,
        demand,
    )
    return cfg, fm, demand


def _row(fc: sf.SkuForecast, first: sf.StockSpool, count: int, cfg: ForecastConfig) -> dict:
    mat, brand, color = fc.key
    status = "break" if fc.stock_break_alert else "reorder" if fc.reorder_alert else "ok"
    return {
        "material": mat,
        "brand": brand or None,
        "color": color or None,
        "spool_count": count,
        "remaining_g": round(fc.remaining_g, 1),
        "demand_g": round(fc.demand_g, 1),
        "daily_rate_g": None if fc.daily_rate_g is None else round(fc.daily_rate_g, 2),
        "days_remaining": fc.days_remaining,
        "days_until_reorder": fc.days_until_reorder_point,
        "reorder_point_g": round(fc.reorder_point_g, 1),
        "lead_time_days": fc.effective_lead_time_days,
        "status": status,
        "snoozed": fc.snoozed,
    }


def build_report(session: Session, now: datetime | None = None) -> dict:
    cfg, fm, demand = compute(session, now)
    counts: dict[sf.SkuKey, int] = {}
    stock: dict[str, float] = {}
    for sp in session.scalars(select(Spool).where(Spool.archived_at.is_(None))):
        k = sf.sku_key(sp.material_type, sp.brand, color_key(sp))
        counts[k] = counts.get(k, 0) + 1
        m = sp.material_type.lower()
        stock[m] = stock.get(m, 0.0) + max(0.0, sp.label_weight_g - sp.weight_used_g)
    rows = [_row(fc, first, counts.get(k, 1), cfg) for k, (fc, first) in fm.items()]
    order = {"break": 0, "reorder": 1, "ok": 2}
    rows.sort(
        key=lambda r: (
            order[r["status"]],
            r["days_remaining"] if r["days_remaining"] is not None else 10**9,
            r["material"],
        )
    )
    shortfalls = [
        {"material": m, "demand_g": round(d, 1), "stock_g": round(stock.get(m, 0.0), 1)}
        for m, d in sorted(demand.items())
        if d > stock.get(m, 0.0)
    ]
    return {
        "config": cfg.model_dump(),
        "skus": rows,
        "queue_demand": [{"material": m, "grams": round(g, 1)} for m, g in sorted(demand.items())],
        "queue_shortfalls": shortfalls,
        "alert_count": sum(1 for r in rows if r["status"] != "ok" and not r["snoozed"]),
        "generated_at": (now or datetime.now(UTC)).isoformat(),
    }


def _sku_id(key: sf.SkuKey) -> str:
    return "|".join(key)


def check_alerts(session: Session, now: datetime | None = None) -> list[dict]:
    """Emit ``stock_break`` / ``stock_reorder`` for SKUs that newly entered
    that state (and aren't snoozed); state is remembered so a still-low SKU
    doesn't re-notify every tick. Returns the emitted payloads."""
    cfg, fm, _demand = compute(session, now)
    row = session.get(Setting, ALERT_STATE_KEY)
    prev: dict[str, str] = dict(row.value) if row else {}
    cur: dict[str, str] = {}
    sent: list[dict] = []
    for key, (fc, _first) in fm.items():
        state = "break" if fc.stock_break_alert else "reorder" if fc.reorder_alert else ""
        if not state:
            continue
        sid = _sku_id(key)
        cur[sid] = state
        if fc.snoozed or not cfg.enabled or not cfg.notify:
            continue
        if prev.get(sid) == state:
            continue
        mat, brand, color = key
        sent.append(
            {
                "event": f"stock_{state}",
                "payload": {
                    "sku": " ".join(x for x in (brand, mat, color) if x),
                    "remaining_g": round(fc.remaining_g),
                    "days_remaining": fc.days_remaining,
                    "reorder_point_g": round(fc.reorder_point_g),
                    "lead_time_days": fc.effective_lead_time_days,
                },
            }
        )
    if row is None:
        session.add(Setting(key=ALERT_STATE_KEY, value=cur))
    else:
        row.value = cur
    session.commit()
    if sent:
        from app.services.notify.events import emit

        for s in sent:
            emit(s["event"], None, s["payload"])
    return sent
