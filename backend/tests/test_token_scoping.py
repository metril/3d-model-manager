"""User-scoped API tokens and the capped token plane."""

import httpx
import pytest
from fastapi import Depends
from sqlalchemy import select

from app.api.deps import AuthContext, require_token_perm
from app.main import create_app
from app.models import Import
from app.services import api_tokens

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


_PERM = {p: require_token_perm(p) for p in ("library:write", "settings:write", "admin")}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_member_lists_only_own_tokens(
    db_session, admin_user, make_user, login_as, authenticated_client
):
    member = await make_user("member")
    await api_tokens.mint(db_session, user_id=member.id, label="mine")
    await api_tokens.mint(db_session, user_id=admin_user.id, label="theirs")
    mc = await login_as(member)
    rows = (await mc.get("/api/settings/api-tokens")).json()
    assert [r["label"] for r in rows] == ["mine"]
    assert rows[0]["user_id"] == member.id and rows[0]["username"] == member.username
    assert len((await authenticated_client.get("/api/settings/api-tokens")).json()) == 2


async def test_member_cannot_revoke_others_token(db_session, admin_user, make_user, login_as):
    member = await make_user("member")
    _, row = await api_tokens.mint(db_session, user_id=admin_user.id, label="theirs")
    r = await (await login_as(member)).delete(f"/api/settings/api-tokens/{row.id}")
    assert r.status_code == 404


async def test_viewer_token_403_on_ext_import(client, db_session, make_user):
    viewer = await make_user("viewer")
    token, _ = await api_tokens.mint(db_session, user_id=viewer.id, label="v")
    r = await client.post(
        "/api/ext/imports",
        json={"url": "https://www.thingiverse.com/thing:1"},
        headers=_bearer(token),
    )
    assert r.status_code == 403


async def test_admin_token_cannot_reach_session_api(client, db_session, admin_user):
    token, _ = await api_tokens.mint(db_session, user_id=admin_user.id, label="a")
    r = await client.put("/api/settings/app", json={}, headers=_bearer(token))
    assert r.status_code == 401


async def test_admin_token_perms_capped(migrated_db, redis_url, db_session, admin_user):
    token, _ = await api_tokens.mint(db_session, user_id=admin_user.id, label="a")
    app = create_app()

    @app.get("/api/_cap/lib")
    async def lib(_: AuthContext = Depends(_PERM["library:write"])) -> dict:
        return {}

    @app.get("/api/_cap/settings")
    async def sett(_: AuthContext = Depends(_PERM["settings:write"])) -> dict:
        return {}

    @app.get("/api/_cap/admin")
    async def adm(_: AuthContext = Depends(_PERM["admin"])) -> dict:
        return {}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        assert (await ac.get("/api/_cap/lib", headers=_bearer(token))).status_code == 200
        assert (await ac.get("/api/_cap/settings", headers=_bearer(token))).status_code == 403
        assert (await ac.get("/api/_cap/admin", headers=_bearer(token))).status_code == 403


async def test_ext_import_records_token_owner(client, db_session, make_user, monkeypatch):
    member = await make_user("member")
    token, _ = await api_tokens.mint(db_session, user_id=member.id, label="m")
    r = await client.post(
        "/api/ext/imports",
        json={"url": "https://www.thingiverse.com/thing:4242"},
        headers=_bearer(token),
    )
    assert r.status_code in (200, 201), r.text
    imp = (await db_session.execute(select(Import).where(Import.id == r.json()["id"]))).scalar_one()
    assert imp.created_by_user_id == member.id


async def test_token_of_deactivated_user_401(client, db_session, make_user):
    u = await make_user("member")
    token, _ = await api_tokens.mint(db_session, user_id=u.id, label="m")
    assert (await client.get("/api/ext/ping", headers=_bearer(token))).status_code == 200
    u.is_active = False
    await db_session.commit()
    assert (await client.get("/api/ext/ping", headers=_bearer(token))).status_code == 401
