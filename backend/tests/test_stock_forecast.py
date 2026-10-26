"""Stock forecast: pure maths, API report, config/SKU overrides, alerts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Setting, Spool, SpoolUsage
from app.models.enums import SpoolUsageSource
from app.services import stock_forecast as sf
from app.services import stock_forecast_db as fdb
from app.tasks.base import sync_session

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


def _spool(i=1, used=0.0, age=60, color="Black", label=1000.0) -> sf.StockSpool:
    return sf.StockSpool(
        id=i,
        material="PLA",
        brand="Acme",
        color=color,
        label_weight=label,
        weight_used=used,
        created_at=NOW - timedelta(days=age),
    )


def test_history_rate_needs_two_days():
    one = [sf.UsageRecord(NOW - timedelta(days=1), 50)]
    assert sf.history_rate(one, NOW) is None
    two = [
        sf.UsageRecord(NOW - timedelta(days=11), 50),
        sf.UsageRecord(NOW - timedelta(days=1), 100),
    ]
    rate, _sigma = sf.history_rate(two, NOW)
    assert rate == pytest.approx(10.0)  # 100 g over the 10-day gap


def test_delta_rate_fallback_and_empty():
    assert sf.delta_rate([_spool(used=0)], NOW) is None
    assert sf.delta_rate([_spool(used=300, age=30)], NOW) == pytest.approx(10.0)
    assert sf.delta_rate([_spool(used=10, age=0)], NOW) is None


def test_stock_break_vs_reorder_exclusive():
    sp = [_spool(used=600, age=60)]  # 10 g/day, 400 g left -> 40 days
    ok = sf.forecast_sku(sp, {}, sf.SkuSettings(safety_margin_value=1), 0, NOW)
    assert ok.days_remaining == 40 and not ok.stock_break_alert
    brk = sf.forecast_sku(sp, {}, sf.SkuSettings(), 45, NOW)
    assert brk.stock_break_alert and not brk.reorder_alert
    reo = sf.forecast_sku(sp, {}, sf.SkuSettings(), 30, NOW)
    assert reo.reorder_alert and not reo.stock_break_alert


def test_queue_demand_reduces_runway():
    sp = [_spool(used=600, age=60)]
    base = sf.forecast_sku(sp, {}, sf.SkuSettings(), 0, NOW)
    loaded = sf.forecast_sku(sp, {}, sf.SkuSettings(), 0, NOW, demand_g=200)
    assert loaded.days_remaining == base.days_remaining - 20
    assert loaded.demand_g == 200


def test_forecast_all_splits_demand_by_stock():
    a, b = _spool(1, used=0, color="Black"), _spool(2, used=500, color="Red")
    fm = sf.forecast_all([a, b], {}, {}, 0, NOW, {"pla": 300})
    d = {k[2]: fc.demand_g for k, (fc, _f) in fm.items()}
    assert d["Black"] == pytest.approx(200) and d["Red"] == pytest.approx(100)


async def _seed_spool(db, used=600.0, age=60, **kw):
    s = Spool(
        material_type="PLA",
        brand="Acme",
        color_name="Black",
        label_weight_g=1000.0,
        weight_used_g=used,
        created_at=datetime.now(UTC) - timedelta(days=age),
        **kw,
    )
    db.add(s)
    await db.commit()
    return s


async def test_report_config_sku_and_alerts(authenticated_client, viewer_client, db_session):
    s = await _seed_spool(db_session)
    sid = s.id
    c = authenticated_client
    rep = (await c.get("/api/spools/forecast")).json()
    assert len(rep["skus"]) == 1
    row = rep["skus"][0]
    assert row["material"] == "PLA" and row["remaining_g"] == 400.0
    assert row["daily_rate_g"] == pytest.approx(10.0, rel=0.05)
    assert row["status"] == "ok" and rep["alert_count"] == 0

    assert (await viewer_client.put("/api/spools/forecast/config", json={})).status_code == 403
    r = await c.put("/api/spools/forecast/config", json={"lead_time_days": 45})
    assert r.status_code == 200 and r.json()["lead_time_days"] == 45
    rep = (await c.get("/api/spools/forecast")).json()
    assert rep["skus"][0]["status"] == "break" and rep["alert_count"] == 1

    # recorded usage history drives the rate when there are two distinct days
    now = datetime.now(UTC)
    for days_ago, g in ((20, 100), (10, 100)):
        db_session.add(
            SpoolUsage(
                spool_id=sid,
                grams=g,
                source=SpoolUsageSource.MANUAL,
                created_at=now - timedelta(days=days_ago),
            )
        )
    await db_session.commit()
    rep = (await c.get("/api/spools/forecast")).json()
    assert rep["skus"][0]["daily_rate_g"] == pytest.approx(10.0, rel=0.1)

    # per-SKU snooze silences the alert count and notification
    r = await c.put(
        "/api/spools/forecast/sku",
        json={"material": "PLA", "brand": "Acme", "color": "Black", "alerts_snoozed": True},
    )
    assert r.status_code == 204
    rep = (await c.get("/api/spools/forecast")).json()
    assert rep["skus"][0]["snoozed"] and rep["alert_count"] == 0


async def test_check_alerts_notifies_once_per_transition(db_session, monkeypatch):
    await _seed_spool(db_session)
    sent: list = []
    monkeypatch.setattr(
        "app.services.notify.events.emit", lambda ev, pid=None, payload=None: sent.append(ev)
    )
    with sync_session() as s:
        fdb.set_config(s, {"lead_time_days": 45})
        assert [x["event"] for x in fdb.check_alerts(s)] == ["stock_break"]
        assert fdb.check_alerts(s) == []  # unchanged state: no repeat
        fdb.set_config(s, {"lead_time_days": 0})
        assert fdb.check_alerts(s) == []
        assert s.get(Setting, fdb.ALERT_STATE_KEY).value == {}
        fdb.set_config(s, {"lead_time_days": 45})
        assert len(fdb.check_alerts(s)) == 1  # re-armed after recovery
    assert sent == ["stock_break", "stock_break"]
    _ = select  # keep import used by linters in older tests


async def test_queued_demand_counts_open_entries_not_trashed(db_session):
    from app.models import File, Model, PrintQueueEntry
    from app.models.enums import BlobFormat, BlobKind
    from app.models.library import Blob, Revision
    from app.models.processing import BlobMeta

    def mk(s, name, trashed):
        m = Model(slug=name, name=name, deleted_at=datetime.now(UTC) if trashed else None)
        s.add(m)
        s.flush()
        rev = Revision(model_id=m.id, number=1, dir_name="r1")
        s.add(rev)
        s.flush()
        h = name[0].ljust(64, "1")
        s.add(Blob(hash=h, size=1, kind=BlobKind.SLICED, format=BlobFormat.GCODE_3MF))
        s.flush()
        s.add(BlobMeta(blob_hash=h, filament_g=50.0, filament_types=["PLA"]))
        f = File(revision_id=rev.id, blob_hash=h, rel_path=f"{name}.gcode.3mf", storage_path="x")
        s.add(f)
        s.flush()
        s.add(PrintQueueEntry(model_id=m.id, position=1, file_id=f.id, copies=3))
        s.commit()

    with sync_session() as s:
        mk(s, "live", False)
        mk(s, "gone", True)
        assert fdb.queued_demand(s) == {"pla": 150.0}
