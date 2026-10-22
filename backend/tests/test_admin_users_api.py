import httpx

from tests.auth_fixtures import LoginAs, MakeGroup, MakeUser

NEW = {"username": "bob", "password": "bobs-password-1", "email": "bob@x.test"}


async def _create(c: httpx.AsyncClient, **over: object) -> dict:
    r = await c.post("/api/admin/users", json={**NEW, **over})
    assert r.status_code == 201, r.text
    return r.json()


async def test_create_list_user(authenticated_client):
    bob = await _create(authenticated_client)
    assert bob["has_password"] is True and bob["oidc_linked"] is False
    rows = (await authenticated_client.get("/api/admin/users")).json()
    assert any(
        u["username"] == "bob"
        and u["role"] == "member"
        and u["is_active"] is True
        and u["has_password"] is True
        for u in rows
    )


async def test_duplicate_email_case_insensitive_409(authenticated_client):
    await _create(authenticated_client)
    r = await authenticated_client.post(
        "/api/admin/users", json={**NEW, "username": "bob2", "email": "Bob@X.test"}
    )
    assert r.status_code == 409 and r.json()["detail"] == "username or email already in use"
    r = await authenticated_client.post("/api/admin/users", json={**NEW, "email": "z@x.test"})
    assert r.status_code == 409


async def test_deactivate_kills_sessions(
    authenticated_client, make_user: MakeUser, login_as: LoginAs
):
    bob = await make_user("member")
    bc = await login_as(bob)
    assert (await bc.get("/api/auth/me")).status_code == 200
    r = await authenticated_client.patch(f"/api/admin/users/{bob.id}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert (await bc.get("/api/auth/me")).status_code == 401


async def test_reset_password(authenticated_client, make_user: MakeUser, login_as: LoginAs):
    bob = await make_user("member")
    bc = await login_as(bob)
    r = await authenticated_client.post(
        f"/api/admin/users/{bob.id}/password", json={"new_password": "brand-new-pass"}
    )
    assert r.status_code == 204
    assert (await bc.get("/api/auth/me")).status_code == 401
    r = await bc.post(
        "/api/auth/login", json={"username": bob.username, "password": "brand-new-pass"}
    )
    assert r.status_code == 204


async def test_cannot_demote_last_admin(authenticated_client, admin_user):
    r = await authenticated_client.patch(
        f"/api/admin/users/{admin_user.id}", json={"role": "member"}
    )
    assert r.status_code == 409 and r.json()["detail"] == "cannot remove the last admin"
    me = (await authenticated_client.get("/api/auth/me")).json()
    assert me["role"] == "admin"


async def test_cannot_deactivate_last_admin(authenticated_client, admin_user):
    r = await authenticated_client.patch(
        f"/api/admin/users/{admin_user.id}", json={"is_active": False}
    )
    assert r.status_code == 409 and r.json()["detail"] == "cannot remove the last admin"


async def test_can_demote_when_another_admin(authenticated_client, admin_user, make_user: MakeUser):
    other = await make_user("admin")
    r = await authenticated_client.patch(f"/api/admin/users/{other.id}", json={"role": "member"})
    assert r.status_code == 200
    r = await authenticated_client.patch(f"/api/admin/users/{other.id}", json={"role": "admin"})
    assert r.status_code == 200


async def test_group_granted_admin_counts(
    authenticated_client, admin_user, make_user: MakeUser, make_group: MakeGroup
):
    m = await make_user("member")
    await make_group("ops", permissions=["admin"], members=[m])
    r = await authenticated_client.patch(
        f"/api/admin/users/{admin_user.id}", json={"role": "member"}
    )
    assert r.status_code == 200


async def test_delete_inactive_admin_ok(authenticated_client, admin_user, make_user: MakeUser):
    other = await make_user("admin", is_active=False)
    r = await authenticated_client.delete(f"/api/admin/users/{other.id}")
    assert r.status_code == 204


async def test_cannot_delete_self(authenticated_client, admin_user):
    r = await authenticated_client.delete(f"/api/admin/users/{admin_user.id}")
    assert r.status_code == 409 and r.json()["detail"] == "cannot delete yourself"


async def test_delete_user(authenticated_client, make_user: MakeUser):
    bob = await make_user("member")
    assert (await authenticated_client.delete(f"/api/admin/users/{bob.id}")).status_code == 204
    assert (await authenticated_client.delete(f"/api/admin/users/{bob.id}")).status_code == 404


async def test_member_403_on_admin_users(member_client):
    assert (await member_client.get("/api/admin/users")).status_code == 403
