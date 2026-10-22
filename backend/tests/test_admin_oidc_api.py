from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.crypto import decrypt_secret
from app.models import OidcProvider
from tests.auth_fixtures import MakeGroup

BODY = {
    "name": "Authentik",
    "slug": "authentik",
    "discovery_url": "https://auth.example/application/o/tdmm/.well-known/openid-configuration",
    "client_id": "cid",
    "client_secret": "s3cret",
}
URL = "/api/admin/oidc-providers"


async def test_create_provider_never_returns_secret(authenticated_client, db_session: AsyncSession):
    r = await authenticated_client.post(URL, json=BODY)
    assert r.status_code == 201
    j = r.json()
    assert "client_secret" not in j and "client_secret_enc" not in j
    assert j["has_client_secret"] is True
    assert j["scopes"] == "openid profile email groups" and j["groups_claim"] == "groups"
    assert j["callback_url"].endswith("/api/auth/oidc/authentik/callback")
    row = await db_session.get(OidcProvider, j["id"])
    assert row.client_secret_enc != "s3cret"
    assert decrypt_secret(get_settings(), row.client_secret_enc) == "s3cret"
    assert "s3cret" not in (await authenticated_client.get(URL)).text


async def test_patch_without_secret_keeps_it(authenticated_client, db_session: AsyncSession):
    pid = (await authenticated_client.post(URL, json=BODY)).json()["id"]
    before = (await db_session.get(OidcProvider, pid)).client_secret_enc
    r = await authenticated_client.patch(f"{URL}/{pid}", json={"name": "New", "enabled": False})
    assert r.status_code == 200 and r.json()["name"] == "New" and r.json()["enabled"] is False
    await db_session.rollback()
    row = await db_session.get(OidcProvider, pid, populate_existing=True)
    assert row.client_secret_enc == before
    r = await authenticated_client.patch(f"{URL}/{pid}", json={"client_secret": "other"})
    assert r.status_code == 200
    row = await db_session.get(OidcProvider, pid, populate_existing=True)
    assert decrypt_secret(get_settings(), row.client_secret_enc) == "other"


async def test_http_discovery_url_422(authenticated_client):
    r = await authenticated_client.post(URL, json={**BODY, "discovery_url": "http://x/y"})
    assert r.status_code == 422


async def test_bad_slug_422(authenticated_client):
    r = await authenticated_client.post(URL, json={**BODY, "slug": "Auth Entik"})
    assert r.status_code == 422


async def test_secret_required_on_create(authenticated_client):
    body = {k: v for k, v in BODY.items() if k != "client_secret"}
    assert (await authenticated_client.post(URL, json=body)).status_code == 422


async def test_duplicate_slug_409(authenticated_client):
    await authenticated_client.post(URL, json=BODY)
    assert (await authenticated_client.post(URL, json=BODY)).status_code == 409


async def test_group_mapping_unknown_group_422(authenticated_client, make_group: MakeGroup):
    g = await make_group("mapped")
    ok = await authenticated_client.post(URL, json={**BODY, "group_mapping": {"idp-a": g.id}})
    assert ok.status_code == 201 and ok.json()["group_mapping"] == {"idp-a": g.id}
    r = await authenticated_client.post(
        URL, json={**BODY, "slug": "two", "group_mapping": {"idp-a": g.id + 999}}
    )
    assert r.status_code == 422
    pid = ok.json()["id"]
    r = await authenticated_client.patch(f"{URL}/{pid}", json={"group_mapping": {"x": g.id + 999}})
    assert r.status_code == 422


async def test_delete_and_404(authenticated_client):
    pid = (await authenticated_client.post(URL, json=BODY)).json()["id"]
    assert (await authenticated_client.delete(f"{URL}/{pid}")).status_code == 204
    assert (await authenticated_client.delete(f"{URL}/{pid}")).status_code == 404
    assert (await authenticated_client.patch(f"{URL}/{pid}", json={})).status_code == 404


async def test_non_admin_forbidden(member_client):
    assert (await member_client.get(URL)).status_code == 403
