"""P7 review/billing plan Task 4: the archive settlement hook
(`billing.settle_job`, wired into `archive.archive_job`) -- release then
charge the actual run cost, partial/unstarted failures, redelivery, and
billing-off-mid-flight.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import CostCenter, LedgerEntry, Model, Print, PrintQueueEntry, User
from app.models.enums import ArchiveResult, LedgerKind, QueueState, UserRole
from app.services import billing
from app.services.archive import archive_job

pytestmark = pytest.mark.usefixtures("printer_enabled", "data_dir")


def _seed_billed_item(s, *, job_id: int, reserved: Decimal) -> tuple[int, int]:
    """A queue entry already dispatched to ``job_id``, with an open
    reservation of ``reserved`` against a fresh private cost center.
    Returns ``(queue_entry_id, cost_center_id)``.
    """
    user = User(username=f"billed-owner-{job_id}", role=UserRole.MEMBER)
    s.add(user)
    s.flush()
    cc = billing.ensure_private_center(s, user.id)
    s.commit()

    model = Model(slug=f"archive-billing-{job_id}", name=f"archive-billing-{job_id}")
    s.add(model)
    s.flush()
    entry = PrintQueueEntry(
        model_id=model.id,
        position=1,
        state=QueueState.DISPATCHED,
        print_job_id=job_id,
        cost_center_id=cc.id,
        created_by_user_id=user.id,
        reserved_cost=reserved,
    )
    s.add(entry)
    s.flush()
    billing.append(s, cc.id, LedgerKind.RESERVATION, -reserved, queue_id=entry.id, user_id=user.id)
    s.commit()
    return entry.id, cc.id


def _ledger_rows(s, queue_id: int) -> list[LedgerEntry]:
    return list(
        s.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.print_queue_id == queue_id)
            .order_by(LedgerEntry.id)
        ).all()
    )


def test_completed_release_then_charge(fake_adapter, sliced_3mf_bytes, seed):
    job_id = seed.job(subtask="Billed_plate_1")
    fake_adapter.files["/cache/Billed_plate_1.3mf"] = sliced_3mf_bytes
    entry_id, cc_id = _seed_billed_item(seed.s, job_id=job_id, reserved=Decimal("999.00"))

    arc = archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    assert arc.fetch_state == "done"

    rows = _ledger_rows(seed.s, entry_id)
    kinds = [r.kind for r in rows]
    assert kinds == [LedgerKind.RESERVATION, LedgerKind.RELEASE, LedgerKind.CHARGE]
    assert rows[0].amount == Decimal("-999.00")
    assert rows[1].amount == Decimal("999.00")  # negates the reservation exactly

    run = seed.s.scalar(select(Print).where(Print.print_job_id == job_id))
    assert run.total_cost is not None
    assert rows[2].amount == -billing.q2(run.total_cost)
    assert rows[2].print_id == run.id

    entry = seed.s.get(PrintQueueEntry, entry_id)
    assert entry.reserved_cost is None  # released

    before = Decimal("0.00")  # a fresh private center starts at zero balance
    cc = seed.s.get(CostCenter, cc_id)
    assert cc.balance == before - billing.q2(run.total_cost)


def test_failed_partial_charge(fake_adapter, sliced_3mf_bytes, seed):
    job_id = seed.job(subtask="Partial_plate_1", layer=30, total_layers=100)
    fake_adapter.files["/cache/Partial_plate_1.3mf"] = sliced_3mf_bytes
    entry_id, _cc_id = _seed_billed_item(seed.s, job_id=job_id, reserved=Decimal("50.00"))

    archive_job(seed.s, get_settings(), job_id, ArchiveResult.FAILED)

    run = seed.s.scalar(select(Print).where(Print.print_job_id == job_id))
    assert run.result == "fail"
    assert run.filament_g == pytest.approx(61.64 * 0.30, rel=0.01)
    assert run.total_cost is not None

    rows = _ledger_rows(seed.s, entry_id)
    kinds = [r.kind for r in rows]
    assert kinds == [LedgerKind.RESERVATION, LedgerKind.RELEASE, LedgerKind.CHARGE]
    assert rows[2].amount == -billing.q2(run.total_cost)


def test_unstarted_failure_keeps_reservation(fake_adapter, seed):
    job_id = seed.job(subtask="NeverStarted", duration_s=None)
    entry_id, _cc_id = _seed_billed_item(seed.s, job_id=job_id, reserved=Decimal("12.00"))

    archive_job(seed.s, get_settings(), job_id, ArchiveResult.FAILED)

    rows = _ledger_rows(seed.s, entry_id)
    assert [r.kind for r in rows] == [LedgerKind.RESERVATION]

    entry = seed.s.get(PrintQueueEntry, entry_id)
    assert entry.reserved_cost == Decimal("12.00")


def test_archive_redelivery_no_double_charge(fake_adapter, sliced_3mf_bytes, seed):
    job_id = seed.job(subtask="Redelivered_plate_1")
    fake_adapter.files["/cache/Redelivered_plate_1.3mf"] = sliced_3mf_bytes
    entry_id, _cc_id = _seed_billed_item(seed.s, job_id=job_id, reserved=Decimal("5.00"))

    archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)

    rows = _ledger_rows(seed.s, entry_id)
    kinds = [r.kind for r in rows]
    assert kinds.count(LedgerKind.RELEASE) == 1
    assert kinds.count(LedgerKind.CHARGE) == 1


def test_billing_off_mid_flight_still_settles(fake_adapter, sliced_3mf_bytes, seed):
    """The item carries a cost center/reservation from when billing was on
    -- settlement still runs even though the app config's `billing_enabled`
    is off right now (the default in this test env)."""
    from app.services.app_config import get_app_config_sync

    cfg = get_app_config_sync(seed.s, get_settings())
    assert cfg.billing_enabled is False

    job_id = seed.job(subtask="MidFlight_plate_1")
    fake_adapter.files["/cache/MidFlight_plate_1.3mf"] = sliced_3mf_bytes
    entry_id, _cc_id = _seed_billed_item(seed.s, job_id=job_id, reserved=Decimal("3.00"))

    archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)

    rows = _ledger_rows(seed.s, entry_id)
    assert [r.kind for r in rows] == [LedgerKind.RESERVATION, LedgerKind.RELEASE, LedgerKind.CHARGE]


def test_no_queue_item_no_charge(fake_adapter, sliced_3mf_bytes, seed):
    job_id = seed.job(subtask="NoQueueItem_plate_1")
    fake_adapter.files["/cache/NoQueueItem_plate_1.3mf"] = sliced_3mf_bytes

    arc = archive_job(seed.s, get_settings(), job_id, ArchiveResult.COMPLETED)
    assert arc.fetch_state == "done"

    count = seed.s.scalar(select(LedgerEntry).limit(1))
    assert count is None
