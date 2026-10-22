"""P7 review/billing plan Task 7: the nightly balance recompute beat task
(``app.tasks.billing.recompute_balances``) -- drift reconciliation and the
monthly-cap rollover unhold.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.models import LedgerEntry, Setting
from app.models.enums import LedgerKind
from app.services import billing
from app.tasks import base
from app.tasks.billing import recompute_balances


def _enable_billing(s) -> None:
    row = s.get(Setting, "app")
    if row is None:
        s.add(Setting(key="app", value={"billing_enabled": True}))
    else:
        row.value = {**(row.value or {}), "billing_enabled": True}
    s.commit()


def test_recompute_matches_ledger(make_cost_center) -> None:
    with base.sync_session() as s:
        from app.models import User
        from app.models.enums import UserRole

        owner = User(username="recompute-owner", role=UserRole.MEMBER)
        s.add(owner)
        s.flush()
        cc = make_cost_center(s, "recompute-cc", owner_user_id=owner.id)
        billing.append(s, cc.id, LedgerKind.DEPOSIT, Decimal("20"))
        billing.append(s, cc.id, LedgerKind.CHARGE, Decimal("-5"))
        s.commit()
        cc_id = cc.id

        # Corrupt the cached balance directly, bypassing `append`.
        cc.balance = Decimal("999")
        s.commit()

    result = recompute_balances()

    assert cc_id in result["drifted"]
    assert result["drifted"][cc_id]["old"] == 999.0
    assert result["drifted"][cc_id]["new"] == 15.0

    with base.sync_session() as s:
        from sqlalchemy import func, select

        from app.models import CostCenter

        cc = s.get(CostCenter, cc_id)
        ledger_sum = s.execute(
            select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
                LedgerEntry.cost_center_id == cc_id,
                LedgerEntry.kind.in_(billing.BALANCE_KINDS),
            )
        ).scalar_one()
        assert cc.balance == ledger_sum == Decimal("15.00")


def test_rollover_unholds(make_cost_center, make_queue_item) -> None:
    last_month = datetime(2026, 9, 15, tzinfo=UTC)
    new_month = datetime(2026, 10, 1, 0, 5, tzinfo=UTC)

    with base.sync_session() as s:
        from app.models import User
        from app.models.enums import UserRole

        _enable_billing(s)

        owner = User(username="rollover-owner", role=UserRole.MEMBER)
        s.add(owner)
        s.flush()
        cc = make_cost_center(s, "rollover-cc", owner_user_id=owner.id, budget_monthly=10)
        # A charge dated LAST month -- it pushed last month's month_spent
        # over the monthly cap (why the item was held), but is excluded
        # from the NEW month's `figures()` window.
        s.add(
            LedgerEntry(
                cost_center_id=cc.id,
                kind=LedgerKind.CHARGE,
                amount=Decimal("-8"),
                created_at=last_month,
            )
        )
        item = make_queue_item(s, cost_center_id=cc.id, created_by_user_id=owner.id, position=1)
        item.hold_reason = billing.HOLD_OVER_BUDGET
        item.reserved_cost = Decimal("5")
        s.commit()
        item_id = item.id

    result = recompute_balances(now=new_month)
    assert result["unheld"] == 1

    with base.sync_session() as s:
        from app.models import PrintQueueEntry

        refreshed = s.get(PrintQueueEntry, item_id)
        assert refreshed.hold_reason is None
        assert refreshed.reserved_cost == Decimal("5.00")


def test_beat_entry_routes_to_io() -> None:
    from app.tasks.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["billing-recompute"]
    assert entry["task"] == "app.tasks.billing.recompute_balances"

    route = celery_app.amqp.router.route({}, "app.tasks.billing.recompute_balances")
    assert route["queue"].name == "io"
