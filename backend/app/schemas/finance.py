"""Finance API schemas (P7 review/billing plan Task 5): the ``billing_enabled``
config toggle, cost-center CRUD, ledger pages and deposits/adjustments.

Money is always serialized as a plain JSON number at 2dp (``float(q2(x))``
-- see ``app.services.billing.q2``), never a string.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enums import LedgerKind


class FinanceConfigOut(BaseModel):
    billing_enabled: bool


class FinanceConfigIn(BaseModel):
    billing_enabled: bool


class CostCenterOut(BaseModel):
    id: int
    name: str
    owner_user_id: int | None
    group_id: int | None
    budget_total: float | None
    budget_monthly: float | None
    balance: float
    open_reserved: float
    month_spent: float
    available: float | None
    active: bool


class CostCenterCreateIn(BaseModel):
    name: str = Field(min_length=1)
    owner_user_id: int | None = None
    group_id: int | None = None
    budget_total: float | None = Field(default=None, ge=0)
    budget_monthly: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _exactly_one_owner(self) -> CostCenterCreateIn:
        if (self.owner_user_id is None) == (self.group_id is None):
            raise ValueError("exactly one of owner_user_id or group_id is required")
        return self


class CostCenterPatchIn(BaseModel):
    name: str | None = None
    budget_total: float | None = Field(default=None, ge=0)
    budget_monthly: float | None = Field(default=None, ge=0)
    active: bool | None = None


class LedgerEntryOut(BaseModel):
    id: int
    cost_center_id: int
    kind: LedgerKind
    amount: float
    print_queue_id: int | None
    print_id: int | None
    created_by_user_id: int | None
    note: str | None
    created_at: datetime


class DepositIn(BaseModel):
    cost_center_id: int
    amount: float
    kind: LedgerKind = LedgerKind.DEPOSIT
    note: str | None = None

    @model_validator(mode="after")
    def _valid_amount(self) -> DepositIn:
        if self.kind not in (LedgerKind.DEPOSIT, LedgerKind.ADJUSTMENT):
            raise ValueError("kind must be deposit or adjustment")
        if self.kind == LedgerKind.DEPOSIT and self.amount <= 0:
            raise ValueError("a deposit amount must be > 0")
        if self.kind == LedgerKind.ADJUSTMENT and self.amount == 0:
            raise ValueError("an adjustment amount must not be 0")
        return self
