"""P7 Task 2: the ledger service (`app.services.billing`) -- append
idempotency, the balance/reserved/month figures, reserve/release/charge
under a row lock, lazy private centers, and over-budget retry.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import CostCenter, LedgerEntry, Print, PrintQueueEntry
from app.models.enums import LedgerKind
from app.services import billing
from app.tasks import base

# ---------------------------------------------------------------------------
# fits() -- pure arithmetic, no DB
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "budget_total,budget_monthly,balance,open_reserved,month_spent,est,expected",
    [
        # unlimited: no cap on either axis -- always fits.
        (None, None, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("1000000"), True),
        # budget_total with a deposit/charge baked into `balance`: available
        # = budget_total + balance - open_reserved.
        (Decimal("10"), None, Decimal("5"), Decimal("0"), Decimal("0"), Decimal("15"), True),
        (Decimal("10"), None, Decimal("5"), Decimal("0"), Decimal("0"), Decimal("16"), False),
        # monthly cap: month_spent + open_reserved + est must not exceed it.
        (None, Decimal("20"), Decimal("0"), Decimal("0"), Decimal("15"), Decimal("5"), True),
        (None, Decimal("20"), Decimal("0"), Decimal("0"), Decimal("15"), Decimal("6"), False),
        # both caps: either one failing fails the whole check.
        (
            Decimal("100"),
            Decimal("20"),
            Decimal("0"),
            Decimal("0"),
            Decimal("15"),
            Decimal("5"),
            True,
        ),
        (
            Decimal("100"),
            Decimal("20"),
            Decimal("0"),
            Decimal("0"),
            Decimal("15"),
            Decimal("6"),
            False,
        ),
        (
            Decimal("5"),
            Decimal("100"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("6"),
            False,
        ),
    ],
)
def test_fits_matrix(
    budget_total, budget_monthly, balance, open_reserved, month_spent, est, expected
):
    cc = CostCenter(name="x", budget_total=budget_total, budget_monthly=budget_monthly)
    available = None if budget_total is None else budget_total + balance - open_reserved
    f = billing.Figures(
        balance=balance, open_reserved=open_reserved, month_spent=month_spent, available=available
    )
    assert billing.fits(cc, f, est) is expected


# ---------------------------------------------------------------------------
# append() idempotency + balance cache
# ---------------------------------------------------------------------------


def test_append_idempotent(make_user, make_cost_center, make_queue_item):
    with base.sync_session() as s:
        user = make_user(s, "alice")
        cc = make_cost_center(s, "alice's budget", owner_user_id=user.id)
        item = make_queue_item(s, position=1, cost_center_id=cc.id)
        s.commit()

        first = billing.append(
            s, cc.id, LedgerKind.RESERVATION, Decimal("-5"), queue_id=item.id, user_id=user.id
        )
        s.commit()
        assert first is not None

        second = billing.append(
            s, cc.id, LedgerKind.RESERVATION, Decimal("-5"), queue_id=item.id, user_id=user.id
        )
        s.commit()
        assert second is None

        rows = (
            s.execute(
                select(LedgerEntry).where(
                    LedgerEntry.print_queue_id == item.id,
                    LedgerEntry.kind == LedgerKind.RESERVATION,
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


def test_release_negates_reservation(make_user, make_cost_center, make_queue_item):
    with base.sync_session() as s:
        user = make_user(s, "bob")
        cc = make_cost_center(s, "bob's budget", owner_user_id=user.id)
        item = make_queue_item(s, position=1, cost_center_id=cc.id)
        s.commit()

        billing.append(s, cc.id, LedgerKind.RESERVATION, Decimal("-6"), queue_id=item.id)
        s.commit()

        released = billing.release(s, item, user_id=user.id)
        s.commit()
        assert released is not None
        assert released.amount == Decimal("6.00")
        assert released.kind == LedgerKind.RELEASE
        assert item.reserved_cost is None

        f = billing.figures(s, s.get(CostCenter, cc.id), now=datetime.now(UTC))
        assert f.open_reserved == Decimal("0.00")

        # No open reservation left -- a second release is a no-op.
        assert billing.release(s, item) is None


def test_charge_updates_balance_cache(make_user, make_cost_center, make_queue_item):
    with base.sync_session() as s:
        user = make_user(s, "carol")
        cc = make_cost_center(s, "carol's budget", owner_user_id=user.id)
        item = make_queue_item(s, position=1, cost_center_id=cc.id, created_by_user_id=user.id)
        run = Print(total_cost=12.5)
        s.add(run)
        s.commit()

        entry = billing.charge(s, item, run)
        s.commit()
        assert entry is not None
        assert entry.amount == Decimal("-12.50")

        s.refresh(cc)
        assert cc.balance == Decimal("-12.50")

        # Idempotent re-charge of the same queue item is a no-op.
        assert billing.charge(s, item, run) is None


def test_monthly_rollover(make_user, make_cost_center, make_queue_item):
    with base.sync_session() as s:
        user = make_user(s, "dana")
        cc = make_cost_center(
            s, "dana's budget", owner_user_id=user.id, budget_monthly=Decimal("100")
        )
        last_month_item = make_queue_item(s, position=1, cost_center_id=cc.id)
        this_month_item = make_queue_item(s, position=2, cost_center_id=cc.id)
        s.commit()

        now = datetime(2026, 10, 15, tzinfo=UTC)
        last_month = datetime(2026, 9, 15, tzinfo=UTC)

        s.add(
            LedgerEntry(
                cost_center_id=cc.id,
                kind=LedgerKind.CHARGE,
                amount=Decimal("-10.00"),
                print_queue_id=last_month_item.id,
                created_at=last_month,
            )
        )
        s.add(
            LedgerEntry(
                cost_center_id=cc.id,
                kind=LedgerKind.CHARGE,
                amount=Decimal("-7.00"),
                print_queue_id=this_month_item.id,
                created_at=now,
            )
        )
        s.commit()

        f = billing.figures(s, cc, now=now)
        assert f.month_spent == Decimal("7.00")


# ---------------------------------------------------------------------------
# reserve() over-budget hold + retry
# ---------------------------------------------------------------------------


def test_over_budget_sets_hold(billing_cfg, make_user, make_cost_center, make_queue_item):
    with base.sync_session() as s:
        user = make_user(s, "erin")
        cc = make_cost_center(s, "erin's budget", owner_user_id=user.id, budget_total=Decimal("5"))
        item = make_queue_item(s, position=1, cost_center_id=cc.id)
        s.commit()

        ok = billing.reserve(s, billing_cfg, item, Decimal("10"), user_id=user.id)
        s.commit()

        assert ok is False
        assert item.hold_reason == billing.HOLD_OVER_BUDGET
        rows = (
            s.execute(select(LedgerEntry).where(LedgerEntry.print_queue_id == item.id))
            .scalars()
            .all()
        )
        assert rows == []


def test_retry_over_budget_after_deposit(billing_cfg, make_user, make_cost_center, make_queue_item):
    with base.sync_session() as s:
        user = make_user(s, "frank")
        cc = make_cost_center(s, "frank's budget", owner_user_id=user.id, budget_total=Decimal("5"))
        item = make_queue_item(s, position=1, cost_center_id=cc.id)
        s.commit()

        assert billing.reserve(s, billing_cfg, item, Decimal("10"), user_id=user.id) is False
        s.commit()
        assert item.hold_reason == billing.HOLD_OVER_BUDGET

        billing.append(s, cc.id, LedgerKind.DEPOSIT, Decimal("10"), user_id=user.id)
        s.commit()

        unheld = billing.retry_over_budget(s, billing_cfg, cc.id)
        s.commit()

        assert unheld == 1
        s.refresh(item)
        assert item.hold_reason is None
        reservation = s.execute(
            select(LedgerEntry).where(
                LedgerEntry.print_queue_id == item.id, LedgerEntry.kind == LedgerKind.RESERVATION
            )
        ).scalar_one()
        assert reservation.amount == Decimal("-10.00")


# ---------------------------------------------------------------------------
# ensure_private_center() race
# ---------------------------------------------------------------------------


def test_ensure_private_center_race(make_user):
    with base.sync_session() as setup:
        user = make_user(setup, "grace", display_name="Grace")
        setup.commit()
        user_id = user.id

    barrier = threading.Barrier(2)
    results: list[int] = [None, None]  # type: ignore[list-item]

    def _run(idx: int) -> None:
        with base.sync_session() as s:
            barrier.wait()
            cc = billing.ensure_private_center(s, user_id)
            s.commit()
            results[idx] = cc.id

    t1 = threading.Thread(target=_run, args=(0,))
    t2 = threading.Thread(target=_run, args=(1,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results[0] == results[1]

    with base.sync_session() as s:
        rows = (
            s.execute(select(CostCenter).where(CostCenter.owner_user_id == user_id)).scalars().all()
        )
        assert len(rows) == 1
        assert rows[0].name == "Grace"


# ---------------------------------------------------------------------------
# concurrent reserve() under the row lock
# ---------------------------------------------------------------------------


def test_concurrent_reserve_row_lock(make_user, make_cost_center, make_queue_item):
    with base.sync_session() as setup:
        user = make_user(setup, "henry")
        cc = make_cost_center(
            setup, "henry's budget", owner_user_id=user.id, budget_total=Decimal("10")
        )
        item_a = make_queue_item(setup, position=1, cost_center_id=cc.id)
        item_b = make_queue_item(setup, position=2, cost_center_id=cc.id)
        setup.commit()
        cc_id, user_id, item_a_id, item_b_id = cc.id, user.id, item_a.id, item_b.id

    barrier = threading.Barrier(2)
    outcomes: list[bool] = [None, None]  # type: ignore[list-item]

    class _Cfg:
        billing_enabled = True

    def _run(idx: int, queue_id: int) -> None:
        with base.sync_session() as s:
            item = s.get(PrintQueueEntry, queue_id)
            barrier.wait()
            outcomes[idx] = billing.reserve(s, _Cfg(), item, Decimal("6.00"), user_id=user_id)
            s.commit()

    t1 = threading.Thread(target=_run, args=(0, item_a_id))
    t2 = threading.Thread(target=_run, args=(1, item_b_id))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(outcomes) == [False, True]

    with base.sync_session() as s:
        reservations = (
            s.execute(select(LedgerEntry).where(LedgerEntry.kind == LedgerKind.RESERVATION))
            .scalars()
            .all()
        )
        assert len(reservations) == 1

        items = (
            s.execute(select(PrintQueueEntry).where(PrintQueueEntry.cost_center_id == cc_id))
            .scalars()
            .all()
        )
        held_items = [i for i in items if i.hold_reason == billing.HOLD_OVER_BUDGET]
        assert len(held_items) == 1
