from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GroupMember, Printer
from app.models.enums import MembershipSource
from tests.auth_fixtures import LoginAs, MakeGroup, MakeUser


async def _mk(c, **over):
    r = await c.post("/api/admin/groups", json={"name": "makers", **over})
    assert r.status_code == 201, r.text
    return r.json()


async def test_create_group_with_perms(authenticated_client):
    g = await _mk(authenticated_client, permissions=["queue:review", "library:write"])
    assert g["permissions"] == ["library:write", "queue:review"]
    rows = (await authenticated_client.get("/api/admin/groups")).json()
    assert [x["name"] for x in rows] == ["makers"]


async def test_unknown_perm_422(authenticated_client):
    r = await authenticated_client.post(
        "/api/admin/groups", json={"name": "x", "permissions": ["printers:fly"]}
    )
    assert r.status_code == 422 and "printers:fly" in r.text


async def test_duplicate_name_409(authenticated_client):
    await _mk(authenticated_client)
    r = await authenticated_client.post("/api/admin/groups", json={"name": "makers"})
    assert r.status_code == 409


async def test_patch_and_delete(authenticated_client):
    g = await _mk(authenticated_client)
    r = await authenticated_client.patch(
        f"/api/admin/groups/{g['id']}", json={"description": "d", "permissions": ["library:write"]}
    )
    assert r.status_code == 200 and r.json()["description"] == "d"
    assert r.json()["permissions"] == ["library:write"]
    assert (await authenticated_client.delete(f"/api/admin/groups/{g['id']}")).status_code == 204
    assert (await authenticated_client.delete(f"/api/admin/groups/{g['id']}")).status_code == 404


async def test_members_put_keeps_oidc_rows(
    authenticated_client, make_user: MakeUser, make_group: MakeGroup, db_session: AsyncSession
):
    u1, u2 = await make_user("member"), await make_user("member")
    g = await make_group("g")
    db_session.add(GroupMember(group_id=g.id, user_id=u2.id, source=MembershipSource.OIDC))
    await db_session.commit()
    r = await authenticated_client.put(
        f"/api/admin/groups/{g.id}/members", json={"user_ids": [u1.id]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["members"] == [
        {"user_id": u1.id, "username": u1.username, "source": "local"},
        {"user_id": u2.id, "username": u2.username, "source": "oidc"},
    ]
    r = await authenticated_client.put(
        f"/api/admin/groups/{g.id}/members", json={"user_ids": [999999]}
    )
    assert r.status_code == 422


async def test_printer_access_put_replaces(
    authenticated_client, make_group: MakeGroup, printer_row: Printer
):
    g = await make_group("g")
    url = f"/api/admin/groups/{g.id}/printer-access"
    row = {
        "printer_id": printer_row.id,
        "can_print": True,
        "can_control": False,
        "requires_review": True,
    }
    off = {**row, "can_print": False, "requires_review": False}
    r = await authenticated_client.put(url, json=[row])
    assert r.status_code == 200 and r.json()["printer_access"] == [row]
    r = await authenticated_client.put(url, json=[off])
    assert r.json()["printer_access"] == []
    await authenticated_client.put(url, json=[row])
    assert (await authenticated_client.put(url, json=[])).json()["printer_access"] == []
    r = await authenticated_client.put(url, json=[{**row, "printer_id": 999999}])
    assert r.status_code == 422


async def test_group_grant_takes_effect_next_request(
    authenticated_client, make_user: MakeUser, make_group: MakeGroup, login_as: LoginAs
):
    model = (await authenticated_client.post("/api/models", json={"name": "M"})).json()
    viewer = await make_user("viewer")
    vc = await login_as(viewer)
    body = {"model_id": model["id"], "body": "hi"}
    assert (await vc.post("/api/notes", json=body)).status_code == 403
    g = await make_group("w", permissions=["library:write"])
    r = await authenticated_client.put(
        f"/api/admin/groups/{g.id}/members", json={"user_ids": [viewer.id]}
    )
    assert r.status_code == 200
    assert (await vc.post("/api/notes", json=body)).status_code == 201


async def test_last_admin_guard_on_group_delete_and_member_removal(
    admin_user, db_session: AsyncSession, make_user: MakeUser, make_group: MakeGroup, login_as
):
    admin_user.is_active = False  # only the group grants admin now
    gm = await make_user("viewer")
    g = await make_group("admins", permissions=["admin"], members=[gm])
    await db_session.commit()
    c = await login_as(gm)
    r = await c.put(f"/api/admin/groups/{g.id}/members", json={"user_ids": []})
    assert r.status_code == 409
    r = await c.patch(f"/api/admin/groups/{g.id}", json={"permissions": []})
    assert r.status_code == 409
    r = await c.delete(f"/api/admin/groups/{g.id}")
    assert r.status_code == 409
    assert (await c.get("/api/admin/groups")).status_code == 200
