"""Billing test fixtures (P7 Task 2): seed helpers for the sync ledger
service (``app.services.billing``), which runs against the SYNC engine
(``app.tasks.base.sync_session``), not the API's async session. Registered
from ``conftest.pytest_plugins``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models import CostCenter, Group, GroupMember, Model, PrintQueueEntry, User
from app.models.enums import UserRole


@dataclass
class FakeConfig:
    """Duck-typed stand-in for ``app.services.app_config.AppConfig`` --
    ``app.services.billing`` only ever reads ``billing_enabled`` off it.
    """

    billing_enabled: bool = True


@pytest.fixture
def billing_cfg() -> FakeConfig:
    return FakeConfig(billing_enabled=True)


@pytest.fixture
def billing_cfg_off() -> FakeConfig:
    return FakeConfig(billing_enabled=False)


@pytest.fixture
def make_sync_user() -> Callable[..., User]:
    def _make(s: Session, username: str, *, display_name: str | None = None) -> User:
        user = User(username=username, display_name=display_name, role=UserRole.MEMBER)
        s.add(user)
        s.flush()
        return user

    return _make


@pytest.fixture
def make_sync_group() -> Callable[..., Group]:
    def _make(s: Session, name: str, *, member_user_ids: tuple[int, ...] = ()) -> Group:
        group = Group(name=name)
        s.add(group)
        s.flush()
        for user_id in member_user_ids:
            s.add(GroupMember(group_id=group.id, user_id=user_id))
        s.flush()
        return group

    return _make


@pytest.fixture
def make_cost_center() -> Callable[..., CostCenter]:
    def _make(
        s: Session,
        name: str,
        *,
        owner_user_id: int | None = None,
        group_id: int | None = None,
        budget_total: Decimal | float | None = None,
        budget_monthly: Decimal | float | None = None,
        active: bool = True,
    ) -> CostCenter:
        cc = CostCenter(
            name=name,
            owner_user_id=owner_user_id,
            group_id=group_id,
            budget_total=budget_total,
            budget_monthly=budget_monthly,
            active=active,
        )
        s.add(cc)
        s.flush()
        return cc

    return _make


@pytest.fixture
def make_queue_item() -> Callable[..., PrintQueueEntry]:
    def _make(
        s: Session,
        *,
        position: int = 1,
        cost_center_id: int | None = None,
        created_by_user_id: int | None = None,
        model_slug: str | None = None,
    ) -> PrintQueueEntry:
        slug = model_slug or f"billing-model-{position}"
        model = Model(slug=slug, name=slug)
        s.add(model)
        s.flush()
        item = PrintQueueEntry(
            model_id=model.id,
            position=position,
            cost_center_id=cost_center_id,
            created_by_user_id=created_by_user_id,
        )
        s.add(item)
        s.flush()
        return item

    return _make
