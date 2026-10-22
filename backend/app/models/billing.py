"""Billing domain (P7 review/billing plan): ``cost_centers`` (a budget
owned by a single user OR a single group, never both/neither) and
``ledger_entries`` (an append-only log of signed amounts against a cost
center -- deposits, adjustments, charges, and the reservation/release pair
a queued item posts while its estimate is pending settlement). See
``app.models.enums.LedgerKind``/``ReviewState`` and the P7 plan's Task 2
ledger service for how these are written.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    Text,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, str_enum
from app.models.enums import LedgerKind


class CostCenter(Base):
    """A budget bucket: owned by exactly one user (a personal allowance) or
    exactly one group (a shared allowance), never both or neither (the
    ``ck_cost_centers_owner_xor_group`` CHECK). ``balance`` is a cache of
    ``SUM(ledger_entries.amount)`` over ``BALANCE_KINDS`` for this center
    (the P7 ledger service keeps it in sync on every append; a nightly
    recompute job reconciles drift). ``budget_total``/``budget_monthly``
    are allowances -- NULL means unlimited for that axis.

    A single-user center is unique per owner (``uq_cost_centers_owner_user_id``,
    a partial unique index over non-NULL ``owner_user_id``) -- a second
    private center for the same owner is rejected rather than silently
    splitting their budget across two rows.
    """

    __tablename__ = "cost_centers"
    __table_args__ = (
        CheckConstraint(
            "(owner_user_id IS NULL) != (group_id IS NULL)",
            name="ck_cost_centers_owner_xor_group",
        ),
        Index(
            "uq_cost_centers_owner_user_id",
            "owner_user_id",
            unique=True,
            postgresql_where=text("owner_user_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # No plain `index=True` here -- the partial unique index below already
    # covers lookups by owner.
    owner_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    group_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=True, index=True
    )
    budget_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    budget_monthly: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class LedgerEntry(Base):
    """One append-only ledger row against a ``CostCenter`` (SPEC P7 plan).
    ``print_queue_id``/``print_id`` point at what the entry settles, when
    applicable -- a deposit/adjustment carries neither. The partial unique
    index ``uq_ledger_queue_kind`` makes a settlement write idempotent: a
    given queue item can post at most one row of a given ``kind`` (e.g. one
    ``charge``), while bare deposits (``print_queue_id`` NULL) are
    unrestricted.
    """

    __tablename__ = "ledger_entries"
    __table_args__ = (
        Index("ix_ledger_entries_cost_center_id_created_at", "cost_center_id", "created_at"),
        Index(
            "uq_ledger_queue_kind",
            "print_queue_id",
            "kind",
            unique=True,
            postgresql_where=text("print_queue_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    cost_center_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cost_centers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[LedgerKind] = mapped_column(str_enum(LedgerKind, "ledger_kind"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    print_queue_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("print_queue.id", ondelete="SET NULL"), nullable=True, index=True
    )
    print_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("prints.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
