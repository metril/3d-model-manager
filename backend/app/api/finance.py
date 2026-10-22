"""Finance API (P7 review/billing plan Task 5): the ``billing_enabled``
config toggle, cost-center CRUD, ledger pages and deposits/adjustments.

Visibility rule (not a server-side RBAC split, just scoping): a
``billing:manage`` holder sees/manages every cost center; everyone else
only ever sees their own private center (lazily created) plus any center
owned by a group they belong to. GET endpoints are reachable by any
session; every mutating endpoint requires ``billing:manage``.

All DB work against ``cost_centers``/``ledger_entries`` goes through the
sync ``app.services.billing`` module via ``AsyncSession.run_sync`` --
mirrors ``app.services.queue``'s own billing call sites.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require_perm, require_session
from app.config import Settings, get_settings
from app.db import get_db
from app.models import CostCenter, GroupMember, LedgerEntry, PrintQueueEntry
from app.schemas.finance import (
    CostCenterCreateIn,
    CostCenterOut,
    CostCenterPatchIn,
    DepositIn,
    FinanceConfigIn,
    FinanceConfigOut,
    LedgerEntryOut,
)
from app.services import billing
from app.services.app_config import get_app_config, set_app_config

router = APIRouter(prefix="/finance", tags=["finance"])


def _is_manager(ctx: AuthContext) -> bool:
    return ctx.is_admin or "billing:manage" in ctx.perms


def _cc_out(s: Session, cc: CostCenter, *, now: datetime) -> CostCenterOut:
    f = billing.figures(s, cc, now=now)
    return CostCenterOut(
        id=cc.id,
        name=cc.name,
        owner_user_id=cc.owner_user_id,
        group_id=cc.group_id,
        budget_total=float(cc.budget_total) if cc.budget_total is not None else None,
        budget_monthly=float(cc.budget_monthly) if cc.budget_monthly is not None else None,
        balance=float(f.balance),
        open_reserved=float(f.open_reserved),
        month_spent=float(f.month_spent),
        available=float(f.available) if f.available is not None else None,
        active=cc.active,
    )


def _is_member_of(s: Session, user_id: int, group_id: int) -> bool:
    return (
        s.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id, GroupMember.user_id == user_id
            )
        ).first()
        is not None
    )


def _is_visible(s: Session, ctx: AuthContext, cc: CostCenter) -> bool:
    if _is_manager(ctx):
        return True
    if cc.owner_user_id == ctx.user.id:
        return True
    return cc.group_id is not None and _is_member_of(s, ctx.user.id, cc.group_id)


def _get_cc_or_404(s: Session, cost_center_id: int) -> CostCenter:
    cc = s.get(CostCenter, cost_center_id)
    if cc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"cost center {cost_center_id} not found")
    return cc


# --- config -------------------------------------------------------------


@router.get("/config", response_model=FinanceConfigOut)
async def get_config(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> FinanceConfigOut:
    cfg = await get_app_config(db, settings)
    return FinanceConfigOut(billing_enabled=cfg.billing_enabled)


@router.put(
    "/config",
    response_model=FinanceConfigOut,
    dependencies=[Depends(require_perm("billing:manage"))],
)
async def put_config(
    payload: FinanceConfigIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FinanceConfigOut:
    cfg = await get_app_config(db, settings)
    new_cfg = cfg.model_copy(update={"billing_enabled": payload.billing_enabled})
    await set_app_config(db, settings, new_cfg)
    if not payload.billing_enabled:

        def _clear_holds(s: Session) -> None:
            s.execute(
                update(PrintQueueEntry)
                .where(PrintQueueEntry.hold_reason == billing.HOLD_OVER_BUDGET)
                .values(hold_reason=None)
            )

        await db.run_sync(_clear_holds)
        await db.commit()
    return FinanceConfigOut(billing_enabled=payload.billing_enabled)


# --- cost centers --------------------------------------------------------


@router.get("/cost-centers", response_model=list[CostCenterOut])
async def list_cost_centers(
    mine: bool = False,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[CostCenterOut]:
    manager = _is_manager(ctx) and not mine

    def _list(s: Session) -> list[CostCenterOut]:
        now = datetime.now(UTC)
        if manager:
            rows = list(s.execute(select(CostCenter).order_by(CostCenter.id)).scalars().all())
        else:
            billing.ensure_private_center(s, ctx.user.id)
            rows = sorted(billing.usable_centers(s, ctx.user.id), key=lambda c: c.id)
        return [_cc_out(s, cc, now=now) for cc in rows]

    out = await db.run_sync(_list)
    if not manager:
        await db.commit()  # ensure_private_center may have inserted
    return out


@router.get("/cost-centers/{cost_center_id}", response_model=CostCenterOut)
async def get_cost_center(
    cost_center_id: int,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> CostCenterOut:
    def _get(s: Session) -> CostCenterOut:
        cc = _get_cc_or_404(s, cost_center_id)
        if not _is_visible(s, ctx, cc):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "cost center is not visible to you")
        return _cc_out(s, cc, now=datetime.now(UTC))

    return await db.run_sync(_get)


@router.post(
    "/cost-centers",
    status_code=status.HTTP_201_CREATED,
    response_model=CostCenterOut,
    dependencies=[Depends(require_perm("billing:manage"))],
)
async def create_cost_center(
    payload: CostCenterCreateIn,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> CostCenterOut:
    def _create(s: Session) -> CostCenterOut:
        cc = CostCenter(
            name=payload.name,
            owner_user_id=payload.owner_user_id,
            group_id=payload.group_id,
            budget_total=Decimal(str(payload.budget_total))
            if payload.budget_total is not None
            else None,
            budget_monthly=Decimal(str(payload.budget_monthly))
            if payload.budget_monthly is not None
            else None,
            created_by_user_id=ctx.user.id,
        )
        s.add(cc)
        s.flush()
        return _cc_out(s, cc, now=datetime.now(UTC))

    try:
        out = await db.run_sync(_create)
        await db.commit()
    except IntegrityError:
        # M3: `uq_cost_centers_owner_user_id` -- a second private center
        # for the same owner. 409 (a real conflict with an existing
        # resource) rather than a raw 500.
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "a private cost center for this owner already exists"
        ) from None
    return out


@router.patch(
    "/cost-centers/{cost_center_id}",
    response_model=CostCenterOut,
    dependencies=[Depends(require_perm("billing:manage"))],
)
async def patch_cost_center(
    cost_center_id: int,
    payload: CostCenterPatchIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CostCenterOut:
    cfg = await get_app_config(db, settings)
    fields = payload.model_dump(exclude_unset=True)
    budget_changed = "budget_total" in fields or "budget_monthly" in fields

    def _patch(s: Session) -> CostCenterOut:
        cc = _get_cc_or_404(s, cost_center_id)
        if "name" in fields:
            cc.name = fields["name"]
        if "budget_total" in fields:
            v = fields["budget_total"]
            cc.budget_total = Decimal(str(v)) if v is not None else None
        if "budget_monthly" in fields:
            v = fields["budget_monthly"]
            cc.budget_monthly = Decimal(str(v)) if v is not None else None
        if "active" in fields:
            cc.active = fields["active"]
        s.flush()
        if budget_changed:
            billing.retry_over_budget(s, cfg, cc.id)
        return _cc_out(s, cc, now=datetime.now(UTC))

    out = await db.run_sync(_patch)
    await db.commit()
    return out


@router.delete(
    "/cost-centers/{cost_center_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm("billing:manage"))],
)
async def delete_cost_center(
    cost_center_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    def _delete(s: Session) -> None:
        cc = _get_cc_or_404(s, cost_center_id)
        referenced = (
            s.execute(
                select(LedgerEntry.id).where(LedgerEntry.cost_center_id == cc.id).limit(1)
            ).first()
            is not None
            or s.execute(
                select(PrintQueueEntry.id).where(PrintQueueEntry.cost_center_id == cc.id).limit(1)
            ).first()
            is not None
        )
        if referenced:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "cost center is referenced by ledger entries or queue items",
            )
        s.delete(cc)

    await db.run_sync(_delete)
    await db.commit()


# --- ledger ---------------------------------------------------------------


@router.get("/ledger", response_model=list[LedgerEntryOut])
async def list_ledger(
    cost_center: int,
    limit: int = Query(50, le=500),  # M3: cap the page size -- never an unbounded SELECT.
    before_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_session),
) -> list[LedgerEntryOut]:
    def _list(s: Session) -> list[LedgerEntryOut]:
        cc = _get_cc_or_404(s, cost_center)
        if not _is_visible(s, ctx, cc):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "cost center is not visible to you")
        stmt = (
            select(LedgerEntry)
            .where(LedgerEntry.cost_center_id == cc.id)
            .order_by(LedgerEntry.id.desc())
            .limit(limit)
        )
        if before_id is not None:
            stmt = stmt.where(LedgerEntry.id < before_id)
        rows = s.execute(stmt).scalars().all()
        return [
            LedgerEntryOut(
                id=r.id,
                cost_center_id=r.cost_center_id,
                kind=r.kind,
                amount=float(r.amount),
                print_queue_id=r.print_queue_id,
                print_id=r.print_id,
                created_by_user_id=r.created_by_user_id,
                note=r.note,
                created_at=r.created_at,
            )
            for r in rows
        ]

    return await db.run_sync(_list)


# --- deposits / adjustments ------------------------------------------------


@router.post(
    "/deposits",
    status_code=status.HTTP_201_CREATED,
    response_model=LedgerEntryOut,
    dependencies=[Depends(require_perm("billing:manage"))],
)
async def create_deposit(
    payload: DepositIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_session),
) -> LedgerEntryOut:
    cfg = await get_app_config(db, settings)

    def _deposit(s: Session) -> LedgerEntryOut:
        cc = _get_cc_or_404(s, payload.cost_center_id)
        entry = billing.append(
            s,
            cc.id,
            payload.kind,
            Decimal(str(payload.amount)),
            user_id=ctx.user.id,
            note=payload.note,
        )
        s.flush()
        billing.retry_over_budget(s, cfg, cc.id)
        return LedgerEntryOut(
            id=entry.id,
            cost_center_id=entry.cost_center_id,
            kind=entry.kind,
            amount=float(entry.amount),
            print_queue_id=entry.print_queue_id,
            print_id=entry.print_id,
            created_by_user_id=entry.created_by_user_id,
            note=entry.note,
            created_at=entry.created_at,
        )

    out = await db.run_sync(_deposit)
    await db.commit()
    return out
