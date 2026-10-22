from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from app.models import GroupMember
from app.models.enums import MembershipSource

ISS = "https://idp.test/application/o/tdmm/"


def _loc(r):
    return r.headers["location"]


async def test_happy_path_links_existing_subject(client, fake_idp, make_provider, make_user):
    await make_provider()
    u = await make_user(oidc_issuer=ISS, oidc_subject="sub-1", password=None)
    r = await fake_idp.login(client, "authentik", {"sub": "sub-1"})
    assert r.status_code == 302 and _loc(r) == "/"
    assert "set-cookie" in r.headers
    me = await client.get("/api/auth/me")
    assert me.json()["username"] == u.username


async def test_start_stores_one_shot_state_with_ttl(client, redis_client, fake_idp, make_provider):
    await make_provider()
    r = await fake_idp.start(client, "authentik")
    assert r.status_code == 302
    assert "code_challenge_method=S256" in _loc(r)
    state = parse_qs(urlparse(_loc(r)).query)["state"][0]
    assert 590 < await redis_client.ttl(f"oidc:state:{state}") <= 600


async def test_bad_nonce_rejected(client, fake_idp, make_provider):
    await make_provider()
    fake_idp.nonce_override = "wrong"
    r = await fake_idp.login(client, "authentik", {})
    assert _loc(r) == "/login?error=oidc_failed"


async def test_reused_state_rejected(client, fake_idp, make_provider, make_user):
    await make_provider()
    await make_user(oidc_issuer=ISS, oidc_subject="sub-1", password=None)
    r = await fake_idp.login(client, "authentik")
    assert _loc(r) == "/"
    r2 = await fake_idp.callback(client, "authentik")
    assert _loc(r2) == "/login?error=invalid_state"


async def test_expired_state_rejected(client, redis_client, fake_idp, make_provider):
    await make_provider()
    await fake_idp.start(client, "authentik")
    await redis_client.delete(f"oidc:state:{fake_idp.state}")
    r = await fake_idp.callback(client, "authentik")
    assert _loc(r) == "/login?error=invalid_state"


async def test_unverified_email_not_linked(client, db_session, fake_idp, make_provider, make_user):
    await make_provider(link_by_email=True, auto_create_users=False)
    alice = await make_user(username="alice", email="alice@x.test", password=None)
    r = await fake_idp.login(
        client, "authentik", {"email": "alice@x.test", "email_verified": False}
    )
    assert _loc(r) == "/login?error=not_provisioned"
    await db_session.refresh(alice)
    assert alice.oidc_subject is None


async def test_verified_email_links_case_insensitive(
    client, db_session, fake_idp, make_provider, make_user
):
    await make_provider(link_by_email=True)
    alice = await make_user(username="alice", email="alice@x.test", password=None)
    r = await fake_idp.login(
        client, "authentik", {"sub": "sub-1", "email": "Alice@X.test", "email_verified": True}
    )
    assert _loc(r) == "/"
    assert (await client.get("/api/auth/me")).json()["username"] == "alice"
    await db_session.refresh(alice)
    assert alice.oidc_subject == "sub-1"


async def test_jit_off_not_provisioned(client, fake_idp, make_provider):
    await make_provider()
    r = await fake_idp.login(client, "authentik", {"preferred_username": "newbie"})
    assert _loc(r) == "/login?error=not_provisioned"


async def test_jit_on_creates_member(client, fake_idp, make_provider):
    await make_provider(auto_create_users=True)
    r = await fake_idp.login(client, "authentik", {"preferred_username": "newbie"})
    assert _loc(r) == "/"
    me = (await client.get("/api/auth/me")).json()
    assert me["role"] == "member" and me["username"] == "newbie"
    assert me["has_password"] is False


async def test_jit_username_collision_suffix(client, fake_idp, make_provider, make_user):
    await make_provider(auto_create_users=True)
    await make_user(username="newbie")
    await fake_idp.login(client, "authentik", {"preferred_username": "newbie"})
    assert (await client.get("/api/auth/me")).json()["username"] == "newbie-2"


async def test_inactive_user_rejected(client, fake_idp, make_provider, make_user):
    await make_provider()
    await make_user(oidc_issuer=ISS, oidc_subject="sub-1", password=None, is_active=False)
    r = await fake_idp.login(client, "authentik")
    assert _loc(r) == "/login?error=inactive"


async def test_group_mapping_and_admin_promotion_then_demotion(
    client, db_session, fake_idp, make_provider, make_user, make_group
):
    other_admin = await make_user("admin")  # keeps the last-admin guard out of the way
    u = await make_user(oidc_issuer=ISS, oidc_subject="sub-1", password=None)
    g = await make_group("makers")
    local = await make_group("local-grp", members=[u])
    await make_provider(group_mapping={"makers": g.id}, admin_group="tdmm-admins")
    assert other_admin.id
    await fake_idp.login(client, "authentik", {"groups": ["makers", "tdmm-admins"]})
    me = (await client.get("/api/auth/me")).json()
    assert me["role"] == "admin"
    assert {"id": g.id, "name": "makers"} in me["groups"]

    await fake_idp.login(client, "authentik", {"groups": []})
    me = (await client.get("/api/auth/me")).json()
    assert me["role"] == "member"
    assert me["groups"] == [{"id": local.id, "name": "local-grp"}]
    rows = (await db_session.scalars(select(GroupMember).where(GroupMember.user_id == u.id))).all()
    assert [(r.group_id, r.source) for r in rows] == [(local.id, MembershipSource.LOCAL)]


async def test_last_admin_never_demoted(client, db_session, fake_idp, make_provider, make_user):
    u = await make_user("admin", oidc_issuer=ISS, oidc_subject="sub-1", password=None)
    await make_provider(admin_group="tdmm-admins")
    await fake_idp.login(client, "authentik", {"groups": []})
    await db_session.refresh(u)
    assert u.role == "admin"
    await make_user("admin")
    await fake_idp.login(client, "authentik", {"groups": []})
    await db_session.refresh(u)
    assert u.role == "member"


async def test_mapping_to_deleted_group_skipped(client, fake_idp, make_provider, make_user):
    await make_user(oidc_issuer=ISS, oidc_subject="sub-1", password=None)
    await make_provider(group_mapping={"makers": 999999})
    r = await fake_idp.login(client, "authentik", {"groups": ["makers"]})
    assert _loc(r) == "/"


async def test_return_to_honoured(client, fake_idp, make_provider, make_user):
    await make_provider()
    await make_user(oidc_issuer=ISS, oidc_subject="sub-1", password=None)
    r = await fake_idp.login(client, "authentik", return_to="/printer")
    assert _loc(r) == "/printer"


async def test_unknown_or_disabled_slug_and_idp_error(client, fake_idp, make_provider):
    await make_provider(enabled=False)
    r = await client.get("/api/auth/oidc/authentik/start")
    assert _loc(r) == "/login?error=oidc_failed"
    r = await client.get("/api/auth/oidc/nope/callback", params={"code": "c", "state": "s"})
    assert _loc(r) == "/login?error=oidc_failed"
    await make_provider(slug="on", name="On")
    r = await client.get("/api/auth/oidc/on/callback", params={"error": "access_denied"})
    assert _loc(r) == "/login?error=oidc_failed"


async def test_providers_list_public_enabled_only(client, make_provider):
    await make_provider()
    await make_provider(slug="off", name="Off", enabled=False)
    r = await client.get("/api/auth/oidc/providers")
    assert r.json() == [{"slug": "authentik", "name": "Authentik"}]
