"""Ledger service (P7 review/billing plan, Task 2): append-only writes
against ``ledger_entries``/``cost_centers``, the balance/reserved/month
figures a gate reads, and reserve/release/charge/transfer under a row lock.

This module is deliberately SYNC and NEVER COMMITS -- every function here
takes a plain ``Session`` and leaves the transaction open for the caller to
commit (mirroring ``app.services.print_plan.create_job_sync``'s sync-world
convention). ``reserve``'s ``SELECT ... FOR UPDATE`` on the cost center
therefore stays held until the CALLER's commit/rollback, which is what makes
two concurrent reservations against the same budget serialize correctly
(``test_concurrent_reserve_row_lock``). Gates must read the ledger under
that same lock -- ``cost_centers.balance`` is only a display cache, kept in
sync by ``append`` for ``BALANCE_KINDS`` writes and reconciled by
``recompute``.

``cfg`` (passed to ``reserve``/``retry_over_budget``) is duck-typed: only
``cfg.billing_enabled`` is read, so callers can pass the real
``app.services.app_config.AppConfig`` or any object with that attribute
without this module importing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import and_, case, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import CostCenter, GroupMember, LedgerEntry, Print, PrintQueueEntry, User
from app.models.enums import LedgerKind
from app.models.printing import PrintJob

HOLD_OVER_BUDGET = "over_budget"
BALANCE_KINDS = (LedgerKind.DEPOSIT, LedgerKind.ADJUSTMENT, LedgerKind.CHARGE)
RESERVE_KINDS = (LedgerKind.RESERVATION, LedgerKind.RELEASE)

_CENTS = Decimal("0.01")


def q2(x: float | Decimal) -> Decimal:
    """Quantize to 2dp (the ledger boundary) -- half-up, via ``str(x)`` for
    a ``float`` input so binary-float noise doesn't creep into the ledger.
    """
    d = x if isinstance(x, Decimal) else Decimal(str(x))
    return d.quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Figures:
    balance: Decimal
    open_reserved: Decimal
    month_spent: Decimal
    available: Decimal | None


def figures(s: Session, cc: CostCenter, *, now: datetime) -> Figures:
    month_start = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    amt = LedgerEntry.amount
    bal, res, month = s.execute(
        select(
            func.coalesce(func.sum(case((LedgerEntry.kind.in_(BALANCE_KINDS), amt))), 0),
            func.coalesce(func.sum(case((LedgerEntry.kind.in_(RESERVE_KINDS), amt))), 0),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                LedgerEntry.kind == LedgerKind.CHARGE,
                                LedgerEntry.created_at >= month_start,
                            ),
                            amt,
                        )
                    )
                ),
                0,
            ),
        ).where(LedgerEntry.cost_center_id == cc.id)
    ).one()
    open_reserved = -res
    available = None if cc.budget_total is None else cc.budget_total + bal - open_reserved
    return Figures(bal, open_reserved, -month, available)


def fits(cc: CostCenter, f: Figures, est: Decimal) -> bool:
    if f.available is not None and est > f.available:
        return False
    if (  # noqa: SIM103 -- mirrors the task brief's literal early-return form
        cc.budget_monthly is not None and f.month_spent + f.open_reserved + est > cc.budget_monthly
    ):
        return False
    return True


def append(
    s: Session,
    cc_id: int,
    kind: LedgerKind,
    amount: Decimal,
    *,
    queue_id: int | None = None,
    print_id: int | None = None,
    user_id: int | None = None,
    note: str | None = None,
) -> LedgerEntry | None:
    """Append one ledger row, idempotently for a (queue, kind) pair: a
    second call for the same ``queue_id``/``kind`` hits ``uq_ledger_queue_kind``
    (a partial unique index over non-NULL ``print_queue_id``) and is
    silently dropped (``ON CONFLICT DO NOTHING``), returning ``None`` so the
    caller can tell the write didn't happen -- a bare deposit/adjustment
    (``queue_id=None``) is never restricted by that index and always
    inserts. A successful ``BALANCE_KINDS`` write also bumps the cost
    center's cached ``balance`` in the same statement-level atomic update.
    """
    amount = q2(amount)
    stmt = (
        pg_insert(LedgerEntry)
        .values(
            cost_center_id=cc_id,
            kind=kind,
            amount=amount,
            print_queue_id=queue_id,
            print_id=print_id,
            created_by_user_id=user_id,
            note=note,
        )
        .on_conflict_do_nothing(
            index_elements=["print_queue_id", "kind"],
            index_where=text("print_queue_id IS NOT NULL"),
        )
        .returning(LedgerEntry)
    )
    entry = s.execute(stmt).scalar_one_or_none()
    if entry is None:
        return None
    if kind in BALANCE_KINDS:
        s.execute(
            CostCenter.__table__.update()
            .where(CostCenter.id == cc_id)
            .values(balance=CostCenter.balance + amount)
        )
    return entry


def lock_center(s: Session, cc_id: int) -> CostCenter:
    """Row-lock the cost center for the rest of the caller's transaction."""
    return s.execute(
        select(CostCenter).where(CostCenter.id == cc_id).with_for_update()
    ).scalar_one()


def ensure_private_center(s: Session, user_id: int) -> CostCenter:
    """Lazily create (idempotently, racing two callers) the user's private
    cost center, named after their ``display_name`` (falling back to
    ``username``). ``uq_cost_centers_owner_user_id`` (a partial unique index
    over non-NULL ``owner_user_id``) makes the insert itself race-safe; a
    concurrent second caller's ``ON CONFLICT DO NOTHING`` blocks on the
    first's row until it commits, then sees it via the follow-up ``SELECT``.
    """
    user = s.get(User, user_id)
    name = (user.display_name or user.username) if user is not None else str(user_id)
    s.execute(
        pg_insert(CostCenter)
        .values(name=name, owner_user_id=user_id, created_by_user_id=user_id)
        .on_conflict_do_nothing(
            index_elements=["owner_user_id"],
            index_where=text("owner_user_id IS NOT NULL"),
        )
    )
    return s.execute(select(CostCenter).where(CostCenter.owner_user_id == user_id)).scalar_one()


def usable_centers(s: Session, user_id: int) -> list[CostCenter]:
    """The user's own private center (if any) plus every ACTIVE cost
    center belonging to a group they're a member of.
    """
    group_ids = select(GroupMember.group_id).where(GroupMember.user_id == user_id)
    stmt = select(CostCenter).where(
        (CostCenter.owner_user_id == user_id)
        | ((CostCenter.group_id.in_(group_ids)) & CostCenter.active.is_(True))
    )
    return list(s.execute(stmt).scalars().all())


def _reservation(s: Session, queue_id: int) -> LedgerEntry | None:
    return s.execute(
        select(LedgerEntry).where(
            LedgerEntry.print_queue_id == queue_id,
            LedgerEntry.kind == LedgerKind.RESERVATION,
        )
    ).scalar_one_or_none()


def reserve(
    s: Session,
    cfg: Any,
    item: PrintQueueEntry,
    est: Decimal | None,
    *,
    user_id: int | None,
    now: datetime | None = None,
) -> bool:
    if not cfg.billing_enabled or item.cost_center_id is None:
        return True
    cc = lock_center(s, item.cost_center_id)  # held until the CALLER commits
    if _reservation(s, item.id) is not None:  # retry/redelivery: already reserved
        return True
    est = q2(est or 0)
    if not fits(cc, figures(s, cc, now=now or datetime.now(UTC)), est):
        item.hold_reason = HOLD_OVER_BUDGET
        # Stash the attempted estimate even on a hold so retry_over_budget
        # can re-check it later without the caller re-supplying it.
        item.reserved_cost = est
        return False
    append(s, cc.id, LedgerKind.RESERVATION, -est, queue_id=item.id, user_id=user_id)
    item.reserved_cost, item.hold_reason = est, None
    return True


def release(s: Session, item: PrintQueueEntry, *, user_id: int | None = None) -> LedgerEntry | None:
    """Undo an open reservation (settlement -- runs even with billing off,
    since it only ever unwinds a reservation that billing-on already
    created). No-op if there's no reservation row to release.
    """
    reservation = _reservation(s, item.id)
    if reservation is None:
        return None
    entry = append(
        s,
        reservation.cost_center_id,
        LedgerKind.RELEASE,
        -reservation.amount,
        queue_id=item.id,
        user_id=user_id,
    )
    if entry is not None:
        item.reserved_cost = None
    return entry


def charge(s: Session, item: PrintQueueEntry, run: Print) -> LedgerEntry | None:
    """Post the final charge for a finished print (settlement -- runs even
    with billing off). No-op if the item never carried a cost center.
    """
    if item.cost_center_id is None:
        return None
    amount = -q2(run.total_cost or 0)
    return append(
        s,
        item.cost_center_id,
        LedgerKind.CHARGE,
        amount,
        queue_id=item.id,
        print_id=run.id,
        user_id=item.created_by_user_id,
    )


def transfer(s: Session, src: PrintQueueEntry, clone: PrintQueueEntry, amount: Decimal) -> None:
    """Clone an open reservation from ``src`` onto ``clone`` (a copies-split
    fanout) at a caller-supplied ``amount`` -- no budget re-check, since the
    total being moved was already reserved against the same center.
    """
    if src.cost_center_id is None:
        return
    amount = q2(amount)
    append(s, src.cost_center_id, LedgerKind.RESERVATION, -amount, queue_id=clone.id)
    clone.cost_center_id = src.cost_center_id
    clone.reserved_cost = amount
    clone.hold_reason = None


def retry_over_budget(s: Session, cfg: Any, cc_id: int, *, now: datetime | None = None) -> int:
    """Re-attempt every ``over_budget``-held item against ``cc_id``, FIFO by
    queue position (so an earlier item gets first claim on newly-freed
    budget). Returns the number of items that came off hold. ``now``
    (defaulting to the real current time) is forwarded to ``reserve``'s own
    ``figures()`` call -- the nightly recompute task (P7 Task 7) injects it
    so a monthly-cap hold can be tested as unholding "on the 1st" without
    waiting for the real calendar to turn over.
    """
    if not cfg.billing_enabled:
        return 0
    items = (
        s.execute(
            select(PrintQueueEntry)
            .where(
                PrintQueueEntry.cost_center_id == cc_id,
                PrintQueueEntry.hold_reason == HOLD_OVER_BUDGET,
            )
            .order_by(PrintQueueEntry.position)
        )
        .scalars()
        .all()
    )
    unheld = 0
    for item in items:
        est = item.reserved_cost or Decimal("0")
        if reserve(s, cfg, item, est, user_id=item.created_by_user_id, now=now):
            unheld += 1
    return unheld


def settle_job(s: Session, job: PrintJob, run: Print) -> None:
    """Settlement hook (P7 Task 4): release the queue item's open
    reservation, then post the final charge for its actual run cost.
    Called by ``app.services.archive.archive_job`` once ``run.total_cost``
    is final.

    No-op when there's no queue item for this job at all, when the job
    never started (``job.started_at IS NULL`` -- P6's own before-start
    retry path, which keeps its reservation for the next attempt rather
    than releasing it here), or when the item was never billed in the
    first place (no open reservation AND no cost center). Both
    ``release``/``charge`` are themselves idempotent (``append``'s
    ``(queue_id, kind)`` unique index) -- a second ``archive_job`` call for
    the same job (redelivery) never double-releases or double-charges.
    """
    item = s.scalars(select(PrintQueueEntry).where(PrintQueueEntry.print_job_id == job.id)).first()
    if item is None:
        return
    if job.started_at is None:
        return
    if _reservation(s, item.id) is None and item.cost_center_id is None:
        return
    release(s, item)
    charge(s, item, run)


def recompute(s: Session, cc_id: int | None = None) -> dict[int, tuple[Decimal, Decimal]]:
    """Reconcile ``cost_centers.balance`` against ``SUM(ledger_entries.amount)``
    over ``BALANCE_KINDS``, for one center or every center. Returns
    ``{id: (old, new)}`` for whatever it touched.
    """
    stmt = select(CostCenter)
    if cc_id is not None:
        stmt = stmt.where(CostCenter.id == cc_id)
    centers = s.execute(stmt).scalars().all()
    result: dict[int, tuple[Decimal, Decimal]] = {}
    for cc in centers:
        old = cc.balance
        total = s.execute(
            select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
                LedgerEntry.cost_center_id == cc.id,
                LedgerEntry.kind.in_(BALANCE_KINDS),
            )
        ).scalar_one()
        new = q2(total)
        cc.balance = new
        result[cc.id] = (old, new)
    return result
