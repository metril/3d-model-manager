"""P7 review/billing plan Task 5: the finance API -- config toggle,
cost-center CRUD + visibility scoping, ledger pages, and deposits/
adjustments.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest

from app.models import User
from app.models.enums import LedgerKind
from app.tasks import base

pytestmark = pytest.mark.usefixtures("library_root")


# ---------------------------------------------------------------------------
# config toggle
# ---------------------------------------------------------------------------


async def test_config_toggle_roundtrip(authenticated_client: httpx.AsyncClient) -> None:
    r = await authenticated_client.get("/api/finance/config")
    assert r.status_code == 200
    assert r.json() == {"billing_enabled": False}

    r = await authenticated_client.put("/api/finance/config", json={"billing_enabled": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"billing_enabled": True}

    r = await authenticated_client.get("/api/finance/config")
    assert r.json() == {"billing_enabled": True}


async def test_config_toggle_off_clears_over_budget_holds(
    authenticated_client: httpx.AsyncClient,
    make_cost_center,
    make_queue_item,
) -> None:
    admin_id = (await authenticated_client.get("/api/auth/me")).json()["id"]
    with base.sync_session() as s:
        cc = make_cost_center(s, "held-center", owner_user_id=admin_id, budget_total=10)
        item = make_queue_item(s, cost_center_id=cc.id)
        item.hold_reason = "over_budget"
        s.commit()
        item_id = item.id

    r = await authenticated_client.put("/api/finance/config", json={"billing_enabled": True})
    assert r.status_code == 200

    r = await authenticated_client.put("/api/finance/config", json={"billing_enabled": False})
    assert r.status_code == 200

    with base.sync_session() as s:
        from app.models import PrintQueueEntry

        refreshed = s.get(PrintQueueEntry, item_id)
        assert refreshed.hold_reason is None


# ---------------------------------------------------------------------------
# cost-center visibility scoping
# ---------------------------------------------------------------------------


async def test_member_sees_only_own_and_group_centers(
    authenticated_client: httpx.AsyncClient,
    make_user: Callable[..., Awaitable[User]],
    make_group,
    login_as: Callable[[User], Awaitable[httpx.AsyncClient]],
    make_cost_center,
) -> None:
    member = await make_user(username="member")
    other_user = await make_user(username="otheruser")
    group = await make_group("finance-folks", members=[member])
    member_client = await login_as(member)

    with base.sync_session() as s:
        own = make_cost_center(s, "own-center", owner_user_id=member.id)
        group_cc = make_cost_center(s, "group-center", group_id=group.id)
        other = make_cost_center(s, "other-private-center", owner_user_id=other_user.id)
        s.commit()
        own_id, group_id_, other_id = own.id, group_cc.id, other.id

    r = await member_client.get("/api/finance/cost-centers")
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    # member's lazily-ensured own private center is also present, but must
    # not include the other user's private center.
    assert own_id in ids
    assert group_id_ in ids
    assert other_id not in ids

    r = await member_client.get(f"/api/finance/cost-centers/{other_id}")
    assert r.status_code == 403

    r = await member_client.get(f"/api/finance/cost-centers/{own_id}")
    assert r.status_code == 200

    r = await member_client.get(f"/api/finance/ledger?cost_center={other_id}")
    assert r.status_code == 403

    r = await member_client.get(f"/api/finance/ledger?cost_center={own_id}")
    assert r.status_code == 200


async def test_mine_restricts_manager_to_usable_centers(
    authenticated_client: httpx.AsyncClient,
    make_user: Callable[..., Awaitable[User]],
    make_cost_center,
) -> None:
    someone = await make_user(username="someone")
    with base.sync_session() as s:
        make_cost_center(s, "someones-center", owner_user_id=someone.id)
        s.commit()

    r = await authenticated_client.get("/api/finance/cost-centers?mine=true")
    assert r.status_code == 200
    names = {c["name"] for c in r.json()}
    assert "someones-center" not in names


# ---------------------------------------------------------------------------
# manager CRUD
# ---------------------------------------------------------------------------


async def test_manager_cost_center_crud(authenticated_client: httpx.AsyncClient) -> None:
    admin_id = (await authenticated_client.get("/api/auth/me")).json()["id"]
    r = await authenticated_client.post(
        "/api/finance/cost-centers",
        json={
            "name": "team-alpha",
            "group_id": None,
            "owner_user_id": admin_id,
            "budget_total": 100,
        },
    )
    assert r.status_code == 201, r.text
    cc = r.json()
    assert cc["balance"] == 0.0
    assert cc["budget_total"] == 100.0

    r = await authenticated_client.patch(
        f"/api/finance/cost-centers/{cc['id']}",
        json={"budget_total": 200, "active": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["budget_total"] == 200.0
    assert r.json()["active"] is False

    r = await authenticated_client.delete(f"/api/finance/cost-centers/{cc['id']}")
    assert r.status_code == 204


async def test_create_exactly_one_of_owner_or_group_422(
    authenticated_client: httpx.AsyncClient,
) -> None:
    r = await authenticated_client.post("/api/finance/cost-centers", json={"name": "neither"})
    assert r.status_code == 422

    r = await authenticated_client.post(
        "/api/finance/cost-centers",
        json={"name": "both", "owner_user_id": 1, "group_id": 1},
    )
    assert r.status_code == 422


async def test_delete_referenced_cost_center_409(
    authenticated_client: httpx.AsyncClient,
    make_cost_center,
    make_queue_item,
) -> None:
    admin_id = (await authenticated_client.get("/api/auth/me")).json()["id"]
    with base.sync_session() as s:
        cc = make_cost_center(s, "referenced-center", owner_user_id=admin_id)
        make_queue_item(s, cost_center_id=cc.id)
        s.commit()
        cc_id = cc.id

    r = await authenticated_client.delete(f"/api/finance/cost-centers/{cc_id}")
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# deposits / adjustments
# ---------------------------------------------------------------------------


async def test_deposit_validation(
    authenticated_client: httpx.AsyncClient, make_cost_center
) -> None:
    admin_id = (await authenticated_client.get("/api/auth/me")).json()["id"]
    with base.sync_session() as s:
        cc = make_cost_center(s, "deposit-center", owner_user_id=admin_id)
        s.commit()
        cc_id = cc.id

    r = await authenticated_client.post(
        "/api/finance/deposits", json={"cost_center_id": cc_id, "amount": 0}
    )
    assert r.status_code == 422

    r = await authenticated_client.post(
        "/api/finance/deposits",
        json={"cost_center_id": cc_id, "amount": 0, "kind": "adjustment"},
    )
    assert r.status_code == 422

    r = await authenticated_client.post(
        "/api/finance/deposits", json={"cost_center_id": cc_id, "amount": 10}
    )
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == LedgerKind.DEPOSIT.value
    assert r.json()["amount"] == 10.0


async def test_deposit_unholds_over_budget_item(
    authenticated_client: httpx.AsyncClient,
    make_cost_center,
    make_queue_item,
) -> None:
    admin_id = (await authenticated_client.get("/api/auth/me")).json()["id"]
    with base.sync_session() as s:
        cc = make_cost_center(s, "unhold-center", owner_user_id=admin_id, budget_total=10)
        item = make_queue_item(s, cost_center_id=cc.id)
        item.hold_reason = "over_budget"
        item.reserved_cost = 15
        s.commit()
        cc_id, item_id = cc.id, item.id

    r = await authenticated_client.put("/api/finance/config", json={"billing_enabled": True})
    assert r.status_code == 200

    r = await authenticated_client.post(
        "/api/finance/deposits", json={"cost_center_id": cc_id, "amount": 20}
    )
    assert r.status_code == 201, r.text

    with base.sync_session() as s:
        from app.models import PrintQueueEntry

        refreshed = s.get(PrintQueueEntry, item_id)
        assert refreshed.hold_reason is None
