import pytest
from sqlalchemy import select

from app.models import PrintJob, Spool, SpoolSlot, SpoolUsage
from app.models.enums import ArchiveResult, SpoolUsageSource
from app.services.spool_inventory import (
    assign_slot,
    is_low_stock,
    rate_per_kg,
    record_job_usage,
    remaining_g,
    spool_for_tray,
)

pytestmark = pytest.mark.usefixtures("data_dir")


def _spool(s, *, label=1000.0, used=0.0, low_stock=100.0, cost_per_kg=None, material="PLA"):
    sp = Spool(
        material_type=material,
        label_weight_g=label,
        weight_used_g=used,
        low_stock_g=low_stock,
        cost_per_kg=cost_per_kg,
    )
    s.add(sp)
    s.commit()
    return sp


def test_remaining_and_low_stock(seed):
    not_low = _spool(seed.s, used=850.0)
    low = _spool(seed.s, used=950.0)
    assert remaining_g(not_low) == pytest.approx(150.0)
    assert is_low_stock(not_low) is False
    assert remaining_g(low) == pytest.approx(50.0)
    assert is_low_stock(low) is True


def test_rate_fallback(seed):
    priced = _spool(seed.s, cost_per_kg=25.0)
    unpriced = _spool(seed.s, cost_per_kg=None)
    assert rate_per_kg(priced, 20.0) == 25.0
    assert rate_per_kg(unpriced, 20.0) == 20.0
    assert rate_per_kg(None, 20.0) == 20.0


def test_assign_slot_moves_spool(seed):
    job_id = seed.job()
    printer_id = seed.printer_id
    sp = _spool(seed.s)
    assign_slot(seed.s, printer_id, 0, 0, sp.id)
    assert spool_for_tray(seed.s, printer_id, 0).id == sp.id

    assign_slot(seed.s, printer_id, 1, 2, sp.id)
    rows = seed.s.scalars(select(SpoolSlot).where(SpoolSlot.spool_id == sp.id)).all()
    assert len(rows) == 1
    assert (rows[0].ams_unit, rows[0].slot) == (1, 2)
    assert spool_for_tray(seed.s, printer_id, 0) is None
    assert spool_for_tray(seed.s, printer_id, 1 * 4 + 2).id == sp.id
    assert job_id is not None  # printer created via job seed


def test_record_usage_decrements(seed):
    seed.job()
    printer_id = seed.printer_id
    sp0, sp1 = _spool(seed.s), _spool(seed.s)
    assign_slot(seed.s, printer_id, 0, 0, sp0.id)
    assign_slot(seed.s, printer_id, 0, 1, sp1.id)
    job_id = seed.job()
    job = seed.s.get(PrintJob, job_id)
    est = [{"tray": 0, "grams": 50.0}, {"tray": 1, "grams": 30.0}]
    out = record_job_usage(seed.s, job, est, ArchiveResult.COMPLETED)
    assert {(s.id if s else None, round(g, 2)) for s, g in out} == {
        (sp0.id, 50.0),
        (sp1.id, 30.0),
    }
    rows = seed.s.scalars(select(SpoolUsage).where(SpoolUsage.print_job_id == job_id)).all()
    assert len(rows) == 2
    assert all(r.source == SpoolUsageSource.THREE_MF for r in rows)
    seed.s.refresh(sp0)
    seed.s.refresh(sp1)
    assert sp0.weight_used_g == pytest.approx(50.0)
    assert sp1.weight_used_g == pytest.approx(30.0)
    assert sp0.last_used_at is not None


def test_record_usage_partial_ratio(seed):
    sp = _spool(seed.s)
    seed.job()
    printer_id = seed.printer_id
    assign_slot(seed.s, printer_id, 0, 0, sp.id)
    job_id = seed.job(layer=50, total_layers=100)
    job = seed.s.get(PrintJob, job_id)
    est = [{"tray": 0, "grams": 100.0}]
    out = record_job_usage(seed.s, job, est, ArchiveResult.FAILED)
    assert out == [(sp, pytest.approx(50.0))]
    seed.s.refresh(sp)
    assert sp.weight_used_g == pytest.approx(50.0)


def test_record_usage_idempotent(seed):
    sp = _spool(seed.s)
    seed.job()
    printer_id = seed.printer_id
    assign_slot(seed.s, printer_id, 0, 0, sp.id)
    job_id = seed.job()
    job = seed.s.get(PrintJob, job_id)
    manual = SpoolUsage(
        spool_id=sp.id, print_job_id=job_id, grams=5.0, source=SpoolUsageSource.MANUAL
    )
    seed.s.add(manual)
    seed.s.commit()

    est = [{"tray": 0, "grams": 50.0}]
    record_job_usage(seed.s, job, est, ArchiveResult.COMPLETED)
    record_job_usage(seed.s, job, est, ArchiveResult.COMPLETED)

    rows = seed.s.scalars(select(SpoolUsage).where(SpoolUsage.print_job_id == job_id)).all()
    auto_rows = [r for r in rows if r.source == SpoolUsageSource.THREE_MF]
    manual_rows = [r for r in rows if r.source == SpoolUsageSource.MANUAL]
    assert len(auto_rows) == 1
    assert len(manual_rows) == 1
    seed.s.refresh(sp)
    assert sp.weight_used_g == pytest.approx(50.0)


def test_ams_delta_fallback(seed):
    sp = _spool(seed.s, label=1000.0)
    seed.job()
    printer_id = seed.printer_id
    assign_slot(seed.s, printer_id, 0, 2, sp.id)
    job_id = seed.job()
    job = seed.s.get(PrintJob, job_id)
    job.tray_snapshot = {"start": [None, None, 80.0], "end": [None, None, 70.0]}
    seed.s.commit()

    out = record_job_usage(seed.s, job, [], ArchiveResult.COMPLETED)
    assert out == [(sp, pytest.approx(100.0))]
    rows = seed.s.scalars(select(SpoolUsage).where(SpoolUsage.print_job_id == job_id)).all()
    assert len(rows) == 1 and rows[0].source == SpoolUsageSource.AMS_DELTA
    seed.s.refresh(sp)
    assert sp.weight_used_g == pytest.approx(100.0)


def test_redelivery_replaces_ams_delta_with_3mf(seed):
    sp = _spool(seed.s, label=1000.0)
    seed.job()
    printer_id = seed.printer_id
    assign_slot(seed.s, printer_id, 0, 2, sp.id)
    job_id = seed.job()
    job = seed.s.get(PrintJob, job_id)
    job.tray_snapshot = {"start": [None, None, 80.0], "end": [None, None, 70.0]}
    seed.s.commit()
    record_job_usage(seed.s, job, [], ArchiveResult.COMPLETED)
    seed.s.refresh(sp)
    assert sp.weight_used_g == pytest.approx(100.0)

    est = [{"tray": 8, "grams": 25.0}]  # tray 8 == unit 2 slot 0
    assign_slot(seed.s, printer_id, 2, 0, sp.id)
    record_job_usage(seed.s, job, est, ArchiveResult.COMPLETED)

    rows = seed.s.scalars(select(SpoolUsage).where(SpoolUsage.print_job_id == job_id)).all()
    assert len(rows) == 1 and rows[0].source == SpoolUsageSource.THREE_MF
    seed.s.refresh(sp)
    assert sp.weight_used_g == pytest.approx(25.0)


def test_unassigned_tray_returns_spoolless_grams(seed):
    job_id = seed.job()
    job = seed.s.get(PrintJob, job_id)
    est = [{"tray": 5, "grams": 40.0}]
    out = record_job_usage(seed.s, job, est, ArchiveResult.COMPLETED)
    assert out == [(None, pytest.approx(40.0))]
    rows = seed.s.scalars(select(SpoolUsage).where(SpoolUsage.print_job_id == job_id)).all()
    assert rows == []
