"""PR-A security hardening: secret re-entry on target change, admin-only
storage, SSE scoping, ownership, OIDC linking, session hashing, credential
revocation, FTPS throttle, SSRF helper."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from app.api import events
from app.models import ApiToken, AuthEphemeralToken, Session
from app.services import url_safety
from app.virtual_printer import ftps

SMB = {
    "backend": "smb",
    "config": {
        "host": "nas-a.local",
        "share": "lib",
        "username": "u",
        "password": "pw-real",
    },
}


# -- 1. secret re-entry --------------------------------------------------------


async def test_ha_url_change_requires_token(authenticated_client: httpx.AsyncClient) -> None:
    c = authenticated_client
    r = await c.put(
        "/api/settings/home-assistant",
        json={"ha_url": "http://ha-a.local:8123", "ha_token": "tok"},
    )
    assert r.status_code == 200
    r = await c.put("/api/settings/home-assistant", json={"ha_url": "http://evil.example:8123"})
    assert r.status_code == 422
    r = await c.put(
        "/api/settings/home-assistant",
        json={"ha_url": "http://evil.example:8123", "ha_token": "***"},
    )
    assert r.status_code == 422
    r = await c.put(
        "/api/settings/home-assistant",
        json={"ha_url": "http://ha-b.local:8123", "ha_token": "new"},
    )
    assert r.status_code == 200


async def test_ha_mqtt_test_url_change_requires_password(
    authenticated_client: httpx.AsyncClient,
) -> None:
    c = authenticated_client
    await c.put(
        "/api/settings/home-assistant",
        json={"ha_mqtt_url": "mqtt://broker-a.local", "ha_mqtt_password": "pw"},
    )
    r = await c.post(
        "/api/settings/home-assistant/test-mqtt", json={"ha_mqtt_url": "mqtt://evil.example"}
    )
    assert r.status_code == 422


async def test_storage_target_change_requires_secret(
    authenticated_client: httpx.AsyncClient,
) -> None:
    c = authenticated_client
    assert (await c.put("/api/settings/storage", json=SMB)).status_code == 200
    moved = {**SMB, "config": {**SMB["config"], "host": "evil.example", "password": "***"}}
    assert (await c.put("/api/settings/storage", json=moved)).status_code == 422
    assert (await c.post("/api/settings/storage/test", json=moved)).status_code == 422
    # same target, sentinel secret -> stored secret reused
    same = {**SMB, "config": {**SMB["config"], "password": "***"}}
    assert (await c.put("/api/settings/storage", json=same)).status_code == 200


# -- 2. storage admin only ------------------------------------------------------


async def test_storage_mutations_admin_only(
    member, make_group, login_as, authenticated_client
) -> None:
    await make_group("ops", permissions=["settings:write", "library:write"], members=[member])
    mc = await login_as(member)
    for method, url, body in (
        ("put", "/api/settings/storage", SMB),
        ("post", "/api/settings/storage/migrate", SMB),
        ("post", "/api/settings/storage/backends", {"name": "x", "config": {"backend": "local"}}),
        ("post", "/api/settings/storage/backends/1/default", None),
        ("post", "/api/settings/storage/backends/1/migrate", None),
    ):
        r = await mc.request(method, url, json=body)
        assert r.status_code == 403, (url, r.status_code)


# -- 4. SSE ---------------------------------------------------------------------


def test_sse_forward_filters_by_visible_printers() -> None:
    def ev(t: str, pid: int) -> str:
        return json.dumps({"type": t, "printer_id": pid})

    visible = {1}
    assert events._forward(ev("print_job.updated", 1), visible, False, True)
    assert not events._forward(ev("print_job.updated", 2), visible, False, True)
    assert not events._forward(ev("printer.status", 2), visible, False, True)
    assert events._forward(ev("printer.status", 1), visible, False, True)
    assert not events._forward(ev("print_job.created", 2), set(), False, True)
    assert events._forward(json.dumps({"type": "model.updated"}), visible, False, True)


# -- 5. ownership ---------------------------------------------------------------


async def test_non_owner_cannot_edit_others_model_or_note(
    member_client, authenticated_client, make_user, login_as
) -> None:
    admin_model = (
        await authenticated_client.post("/api/models", json={"name": "AdminOwned"})
    ).json()
    note = (
        await authenticated_client.post(
            "/api/notes", json={"model_id": admin_model["id"], "body": "x"}
        )
    ).json()
    slug, mid = admin_model["slug"], admin_model["id"]

    assert (
        await member_client.patch(f"/api/notes/{note['id']}", json={"body": "y"})
    ).status_code == 403
    assert (await member_client.patch(f"/api/models/{slug}", json={"name": "z"})).status_code == 403
    assert (
        await member_client.post("/api/models/bulk", json={"ids": [mid], "favorite": True})
    ).status_code == 403
    assert (
        await member_client.post(
            f"/api/models/{slug}/relocate", json={"target_backend_id": 1, "mode": "move"}
        )
    ).status_code == 403
    assert (
        await member_client.post(f"/api/models/{slug}/redownload", json={"mode": "replace"})
    ).status_code == 403
    assert (
        await member_client.put(f"/api/models/{mid}/tags", json={"tag_ids": []})
    ).status_code == 403
    assert (
        await member_client.post(f"/api/models/{mid}/revisions", json={"name": "r"})
    ).status_code == 403

    # the owner (and admin) still can
    mine = (await member_client.post("/api/models", json={"name": "Mine"})).json()
    r = await member_client.patch(f"/api/models/{mine['slug']}", json={"name": "Mine2"})
    assert r.status_code == 200
    assert (
        await authenticated_client.patch(f"/api/notes/{note['id']}", json={"body": "y"})
    ).status_code == 200


# -- 6. OIDC --------------------------------------------------------------------


async def test_oidc_never_links_admin_or_password_user(
    client, db_session, fake_idp, make_provider, make_user
) -> None:
    await make_provider(link_by_email=True, auto_create_users=False)
    pw = await make_user(username="pwuser", email="pw@x.test", password="longenough-pw")
    adm = await make_user("admin", username="adm", email="adm@x.test", password=None)
    for email in ("pw@x.test", "adm@x.test"):
        r = await fake_idp.login(
            client, "authentik", {"sub": f"s-{email}", "email": email, "email_verified": True}
        )
        assert r.headers["location"] == "/login?error=not_provisioned"
    await db_session.refresh(pw)
    await db_session.refresh(adm)
    assert pw.oidc_subject is None and adm.oidc_subject is None


# -- 9. session hashing -----------------------------------------------------------


async def test_session_cookie_is_not_stored(client, admin_user, db_session) -> None:
    r = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "correct horse battery staple"}
    )
    assert r.status_code == 204
    token = client.cookies["tdmm_session"]
    rows = (await db_session.scalars(select(Session))).all()
    assert [s.token_hash for s in rows] == [hashlib.sha256(token.encode()).hexdigest()]
    assert all(str(s.id) != token for s in rows)
    assert (await client.get("/api/auth/me")).status_code == 200


# -- 10. credential changes revoke tokens ---------------------------------------


async def test_password_change_revokes_api_tokens(
    authenticated_client, admin_user, db_session
) -> None:
    db_session.add(ApiToken(token_hash="h1", user_id=admin_user.id, label="k"))
    db_session.add(
        AuthEphemeralToken(
            token_hash="h2",
            token_type="pre_auth",
            user_id=admin_user.id,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    await db_session.commit()
    r = await authenticated_client.post(
        "/api/auth/password",
        json={
            "current_password": "correct horse battery staple",
            "new_password": "another long password",
        },
    )
    assert r.status_code == 204
    assert (await db_session.scalars(select(ApiToken))).all() == []
    assert (await db_session.scalars(select(AuthEphemeralToken))).all() == []
    assert (await authenticated_client.get("/api/auth/me")).status_code == 200  # session kept


async def test_admin_reset_revokes_api_tokens(authenticated_client, member, db_session) -> None:
    db_session.add(ApiToken(token_hash="h3", user_id=member.id, label="k"))
    await db_session.commit()
    r = await authenticated_client.post(
        f"/api/admin/users/{member.id}/password", json={"new_password": "another long password"}
    )
    assert r.status_code == 204
    assert (await db_session.scalars(select(ApiToken))).all() == []


# -- 13. FTPS throttle -------------------------------------------------------------


def test_ftps_ip_lockout() -> None:
    ftps._fail_log.clear()
    ip = "203.0.113.9"
    for _ in range(ftps.LOCKOUT_FAILS - 1):
        ftps._note_ip_failure(ip)
    assert not ftps._ip_locked(ip)
    ftps._note_ip_failure(ip)
    assert ftps._ip_locked(ip)
    assert not ftps._ip_locked("203.0.113.10")
    ftps._fail_log.clear()


# -- 3. SSRF helper -------------------------------------------------------------------


def test_is_safe_url() -> None:
    assert not url_safety.is_safe_url("http://127.0.0.1/x")
    assert not url_safety.is_safe_url("https://169.254.169.254/latest/meta-data")
    assert not url_safety.is_safe_url("https://10.0.0.5/x")
    assert url_safety.is_safe_url("http://10.0.0.5/x", allow_private=True)
    assert not url_safety.is_safe_url("http://169.254.169.254/", allow_private=True)
    assert not url_safety.is_safe_url("http://127.0.0.1/", allow_private=True)
    assert not url_safety.is_safe_url("ftp://1.1.1.1/x")
    assert not url_safety.is_safe_url("http://1.1.1.1/x", https_only=True)
    assert url_safety.is_safe_url("https://1.1.1.1/x", https_only=True)


async def test_obico_test_blocks_loopback_override(authenticated_client: httpx.AsyncClient) -> None:
    r = await authenticated_client.post(
        "/api/settings/obico/test", json={"url": "http://169.254.169.254:80"}
    )
    assert r.status_code == 422
    r = await authenticated_client.post(
        "/api/settings/obico/test", json={"url": "http://127.0.0.1:1"}
    )
    assert r.status_code == 422
