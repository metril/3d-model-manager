"""T6: TOTP / backup codes / email OTP, the two-step login, require_2fa gating,
OIDC second factor and the admin reset."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta

import httpx
import pyotp
import pytest
from sqlalchemy import select, update

from app.config import get_settings
from app.crypto import encrypt_secret
from app.main import create_app
from app.models import AuditLog, AuthEphemeralToken, User, UserOtpCode, UserTotp
from app.services import two_factor as tf
from app.services.server_config import ServerConfig, SmtpConfig, set_server_config
from tests.auth_fixtures import DEFAULT_PASSWORD

ISS = "https://idp.test/application/o/tdmm/"
STEP = 30


class Clock:
    def __init__(self) -> None:
        self.t = (int(time.time()) // STEP) * STEP + 5

    @property
    def step(self) -> int:
        return int(self.t // STEP)

    def code(self, secret: str, offset: int = 0) -> str:
        return pyotp.TOTP(secret).generate_otp(self.step + offset)


@pytest.fixture
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(tf, "_now_ts", lambda: c.t)
    return c


@pytest.fixture(autouse=True)
async def _fresh_redis(redis_client):
    """Per-user failure counters live in Redis and user ids restart each test."""


@pytest.fixture
def outbox(monkeypatch) -> list[tuple[str, str, str]]:
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "app.services.mailer.send",
        lambda to, subject, text, html=None: sent.append((to, subject, text)),
    )
    return sent


def last_code(outbox) -> str:
    return re.search(r"\b(\d{6})\b", outbox[-1][2]).group(1)


@pytest.fixture
async def smtp_on(db_session) -> None:
    await set_server_config(
        db_session,
        ServerConfig(
            slicer_enabled=False, smtp=SmtpConfig(host="smtp.test", from_addr="tdmm@test.local")
        ),
    )


async def enroll(c, clock: Clock) -> tuple[str, list[str]]:
    r = await c.post("/api/auth/2fa/totp/setup")
    assert r.status_code == 200, r.text
    body = r.json()
    r = await c.post(
        "/api/auth/2fa/totp/enable",
        json={"setup_token": body["setup_token"], "code": clock.code(body["secret"])},
    )
    assert r.status_code == 200, r.text
    return body["secret"], r.json()["backup_codes"]


async def pre_auth(anon, user: User) -> dict:
    r = await anon.post(
        "/api/auth/login", json={"username": user.username, "password": DEFAULT_PASSWORD}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def verify(anon, token: str, method: str, code: str):
    return await anon.post(
        "/api/auth/2fa/verify", json={"pre_auth_token": token, "method": method, "code": code}
    )


@pytest.fixture
async def enrolled(make_user, login_as, clock):
    user = await make_user("member", email="m@example.com")
    c = await login_as(user)
    secret, codes = await enroll(c, clock)
    return user, secret, codes


# --- TOTP ------------------------------------------------------------------


def test_totp_window_is_plus_minus_one(clock):
    s = pyotp.random_base32()
    for off in (-1, 0, 1):
        assert tf.match_counter(s, clock.code(s, off)) == clock.step + off
    for off in (-2, 2):
        assert tf.match_counter(s, clock.code(s, off)) is None
    assert tf.match_counter(s, "abcdef") is None
    assert tf.match_counter(s, "12345") is None


async def test_enable_returns_ten_backup_codes_and_hides_secret(login_as, make_user, clock):
    c = await login_as(await make_user("member"))
    r = await c.post("/api/auth/2fa/totp/setup")
    assert r.json()["otpauth_url"].startswith("otpauth://totp/")
    secret = r.json()["secret"]
    bad = await c.post(
        "/api/auth/2fa/totp/enable",
        json={"setup_token": r.json()["setup_token"], "code": "000000"},
    )
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "invalid_code"
    ok = await c.post(
        "/api/auth/2fa/totp/enable",
        json={"setup_token": r.json()["setup_token"], "code": clock.code(secret)},
    )
    codes = ok.json()["backup_codes"]
    assert len(codes) == 10 and all(len(x) == 8 for x in codes)
    st = (await c.get("/api/auth/2fa/status")).json()
    assert st["totp_enabled"] and st["backup_codes_remaining"] == 10
    assert st["methods"] == ["totp", "backup"]
    assert "secret" not in st
    again = await c.post("/api/auth/2fa/totp/setup")
    assert again.status_code == 409


async def test_setup_token_is_bound_to_user(login_as, make_user, clock):
    a = await login_as(await make_user("member"))
    b = await login_as(await make_user("member"))
    body = (await a.post("/api/auth/2fa/totp/setup")).json()
    r = await b.post(
        "/api/auth/2fa/totp/enable",
        json={"setup_token": body["setup_token"], "code": clock.code(body["secret"])},
    )
    assert r.status_code == 410


async def test_stored_secret_and_codes_are_not_plaintext(enrolled, db_session):
    user, secret, codes = enrolled
    row = (await db_session.execute(select(UserTotp))).scalar_one()
    assert secret not in row.secret_enc
    assert all(h.startswith("$argon2") for h in row.backup_codes_json)
    assert not set(codes) & set(row.backup_codes_json)
    toks = (await db_session.execute(select(AuthEphemeralToken))).scalars().all()
    assert toks == []  # setup token consumed


async def test_login_shape_without_and_with_2fa(enrolled, make_user, client, clock):
    user, secret, _ = enrolled
    plain = await make_user("member")
    r = await client.post(
        "/api/auth/login", json={"username": plain.username, "password": DEFAULT_PASSWORD}
    )
    assert r.status_code == 204
    await client.post("/api/auth/logout")

    body = await pre_auth(client, user)
    assert body["requires_2fa"] is True and body["methods"] == ["totp", "backup"]
    assert len(body["pre_auth_token"]) >= 40
    assert (await client.get("/api/auth/me")).status_code == 401  # no session yet


async def test_verify_starts_session_and_rejects_replay(enrolled, client, clock):
    user, secret, _ = enrolled
    clock.t += STEP  # enrolment consumed the current step
    tok = (await pre_auth(client, user))["pre_auth_token"]
    r = await verify(client, tok, "totp", clock.code(secret))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["username"] == user.username and body["two_factor_enabled"] is True
    assert (await client.get("/api/auth/me")).status_code == 200

    # same code again (fresh pre-auth) is a replay; so is any older step
    await client.post("/api/auth/logout")
    tok2 = (await pre_auth(client, user))["pre_auth_token"]
    assert (await verify(client, tok2, "totp", clock.code(secret))).status_code == 401
    assert (await verify(client, tok2, "totp", clock.code(secret, -1))).status_code == 401
    # a later step is fine
    assert (await verify(client, tok2, "totp", clock.code(secret, 1))).status_code == 200


async def test_enrol_step_cannot_be_replayed_at_login(enrolled, client, clock):
    user, secret, _ = enrolled
    tok = (await pre_auth(client, user))["pre_auth_token"]
    r = await verify(client, tok, "totp", clock.code(secret))  # code used to enable
    assert r.status_code == 401


async def test_backup_code_is_single_use(enrolled, client, db_session):
    user, _, codes = enrolled
    tok = (await pre_auth(client, user))["pre_auth_token"]
    assert (await verify(client, tok, "backup", codes[0].lower())).status_code == 200
    await client.post("/api/auth/logout")
    tok2 = (await pre_auth(client, user))["pre_auth_token"]
    assert (await verify(client, tok2, "backup", codes[0])).status_code == 401
    assert (await verify(client, tok2, "backup", codes[1])).status_code == 200
    row = (await db_session.execute(select(UserTotp))).scalar_one()
    assert len(row.backup_codes_json) == 8


async def test_pre_auth_attempt_cap_then_410(enrolled, client, clock):
    user, secret, _ = enrolled
    clock.t += STEP
    tok = (await pre_auth(client, user))["pre_auth_token"]
    for _ in range(4):
        r = await verify(client, tok, "totp", "000000")
        assert r.status_code == 401
    assert (await verify(client, tok, "totp", "000000")).status_code == 410
    good = await verify(client, tok, "totp", clock.code(secret))
    assert good.status_code == 410 and good.json()["detail"]["code"] == "pre_auth_expired"


async def test_pre_auth_expires_and_is_single_use(enrolled, client, clock, db_session):
    user, secret, _ = enrolled
    clock.t += STEP
    tok = (await pre_auth(client, user))["pre_auth_token"]
    await db_session.execute(
        update(AuthEphemeralToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()
    assert (await verify(client, tok, "totp", clock.code(secret))).status_code == 410

    tok2 = (await pre_auth(client, user))["pre_auth_token"]
    assert (await verify(client, tok2, "totp", clock.code(secret))).status_code == 200
    assert (await verify(client, tok2, "totp", clock.code(secret, 1))).status_code == 410


async def test_pre_auth_ttl_is_five_minutes(enrolled, client, db_session):
    await pre_auth(client, enrolled[0])
    tok = (await db_session.execute(select(AuthEphemeralToken))).scalar_one()
    left = (tok.expires_at - datetime.now(UTC)).total_seconds()
    assert 290 < left <= 300 and len(tok.token_hash) == 64


async def test_unknown_token_and_unavailable_method(enrolled, client):
    r = await verify(client, "nope", "totp", "123456")
    assert r.status_code == 410
    tok = (await pre_auth(client, enrolled[0]))["pre_auth_token"]
    r = await verify(client, tok, "email", "123456")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "method_unavailable"


async def test_totp_disable_and_regenerate(enrolled, clock):
    user, secret, codes = enrolled
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        clock.t += STEP
        tok = (await pre_auth(c, user))["pre_auth_token"]
        assert (await verify(c, tok, "backup", codes[0])).status_code == 200

        bad = await c.post("/api/auth/2fa/backup/regenerate", json={"code": "000000"})
        assert bad.status_code == 400
        clock.t += STEP
        new = await c.post("/api/auth/2fa/backup/regenerate", json={"code": clock.code(secret)})
        assert new.status_code == 200 and len(new.json()["backup_codes"]) == 10
        assert set(new.json()["backup_codes"]).isdisjoint(codes)

        wrong = await c.post("/api/auth/2fa/totp/disable", json={"code_or_password": "nope"})
        assert wrong.status_code == 400
        ok = await c.post("/api/auth/2fa/totp/disable", json={"code_or_password": DEFAULT_PASSWORD})
        assert ok.status_code == 204
        st = (await c.get("/api/auth/2fa/status")).json()
        assert not st["totp_enabled"] and st["backup_codes_remaining"] == 0


# --- email OTP ---------------------------------------------------------------


async def test_email_enable_needs_smtp_and_address(login_as, make_user, outbox):
    no_addr = await login_as(await make_user("member"))
    assert (await no_addr.post("/api/auth/2fa/email/enable")).json()["detail"]["code"] == "no_email"
    c = await login_as(await make_user("member", email="a@example.com"))
    r = await c.post("/api/auth/2fa/email/enable")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "smtp_not_configured"
    assert outbox == []


async def test_email_otp_flow_and_login(login_as, make_user, outbox, smtp_on, client):
    user = await make_user("member", email="a@example.com")
    c = await login_as(user)
    assert (await c.get("/api/auth/2fa/status")).json()["email_available"] is True
    assert (await c.post("/api/auth/2fa/email/enable")).status_code == 200
    assert outbox[-1][0] == "a@example.com"
    assert (await c.post("/api/auth/2fa/email/confirm", json={"code": "000000"})).status_code == 400
    assert (
        await c.post("/api/auth/2fa/email/confirm", json={"code": last_code(outbox)})
    ).status_code == 204

    body = await pre_auth(client, user)
    assert body["methods"] == ["email"]
    tok = body["pre_auth_token"]
    assert (
        await client.post("/api/auth/2fa/email/send", json={"pre_auth_token": tok})
    ).status_code == 200
    code = last_code(outbox)
    assert (await verify(client, tok, "email", "999999")).status_code == 401
    assert (await verify(client, tok, "email", code)).status_code == 200
    # a used code never works again
    await client.post("/api/auth/logout")
    tok2 = (await pre_auth(client, user))["pre_auth_token"]
    assert (await verify(client, tok2, "email", code)).status_code == 401


async def test_email_send_rate_limit(login_as, make_user, outbox, smtp_on):
    c = await login_as(await make_user("member", email="a@example.com"))
    for _ in range(3):
        assert (await c.post("/api/auth/2fa/email/enable")).status_code == 200
    r = await c.post("/api/auth/2fa/email/enable")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "rate_limited"
    assert len(outbox) == 3


async def test_email_code_attempt_cap_and_hashing(login_as, make_user, outbox, smtp_on, db_session):
    c = await login_as(await make_user("member", email="a@example.com"))
    await c.post("/api/auth/2fa/email/enable")
    code = last_code(outbox)
    row = (await db_session.execute(select(UserOtpCode))).scalar_one()
    assert code not in row.code_hash and row.code_hash.startswith("$argon2")
    assert (row.expires_at - datetime.now(UTC)).total_seconds() > 590
    for _ in range(5):
        assert (
            await c.post("/api/auth/2fa/email/confirm", json={"code": "000000"})
        ).status_code == 400
    # the correct code is dead after 5 failures
    assert (await c.post("/api/auth/2fa/email/confirm", json={"code": code})).status_code == 400


async def test_email_disable(login_as, make_user, outbox, smtp_on):
    c = await login_as(await make_user("member", email="a@example.com"))
    await c.post("/api/auth/2fa/email/enable")
    await c.post("/api/auth/2fa/email/confirm", json={"code": last_code(outbox)})
    assert (
        await c.post("/api/auth/2fa/email/disable", json={"code_or_password": "x"})
    ).status_code == 400
    ok = await c.post("/api/auth/2fa/email/disable", json={"code_or_password": DEFAULT_PASSWORD})
    assert ok.status_code == 204
    assert (await c.get("/api/auth/2fa/status")).json()["email_otp_enabled"] is False


# --- require_2fa ---------------------------------------------------------------


async def test_require_2fa_gating(login_as, make_user, clock):
    admin = await login_as(await make_user("admin"))
    member = await login_as(await make_user("member"))
    assert (await member.get("/api/auth/me")).json()["must_enroll_2fa"] is False
    assert (
        await admin.put("/api/settings/security", json={"require_2fa": True})
    ).status_code == 200

    # admins are gated too -- everything but /auth/* and GET /settings/security
    r = await admin.get("/api/admin/users")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "2fa_enrollment_required"
    assert (
        await admin.put("/api/settings/security", json={"require_2fa": False})
    ).status_code == 403
    assert (await admin.get("/api/settings/security")).json() == {"require_2fa": True}

    assert (await member.get("/api/auth/me")).json()["must_enroll_2fa"] is True
    for path in ("/api/models", "/api/settings/app", "/api/printers"):
        r = await member.get(path)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "2fa_enrollment_required"
    assert (await member.get("/api/auth/2fa/status")).status_code == 200
    assert (await member.get("/api/settings/security")).status_code == 200

    await enroll(member, clock)
    me = (await member.get("/api/auth/me")).json()
    assert me["must_enroll_2fa"] is False and me["two_factor_enabled"] is True
    assert (await member.get("/api/models")).status_code != 403
    # admin enrols and regains access
    await enroll(admin, clock)
    assert (await admin.get("/api/admin/users")).status_code == 200


async def test_security_settings_perms_and_audit(login_as, make_user, db_session):
    member = await login_as(await make_user("member"))
    assert (
        await member.put("/api/settings/security", json={"require_2fa": True})
    ).status_code == 403
    admin = await login_as(await make_user("admin"))
    assert (await admin.get("/api/settings/security")).json() == {"require_2fa": False}
    await admin.put("/api/settings/security", json={"require_2fa": True})
    row = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "security_settings.update")
        )
    ).scalar_one()
    assert row.payload["changed"] == {"require_2fa": [False, True]}


# --- OIDC ---------------------------------------------------------------------


async def _oidc_user_with_totp(make_user, db_session, clock):
    user = await make_user(oidc_issuer=ISS, oidc_subject="sub-1", password=None)
    secret = pyotp.random_base32()
    db_session.add(
        UserTotp(
            user_id=user.id,
            secret_enc=encrypt_secret(get_settings(), secret),
            is_enabled=True,
            backup_codes_json=[],
            last_totp_counter=0,
        )
    )
    await db_session.commit()
    return user, secret


async def test_oidc_redirects_to_2fa_step(
    client, fake_idp, make_provider, make_user, db_session, clock
):
    await make_provider()
    user, secret = await _oidc_user_with_totp(make_user, db_session, clock)
    r = await fake_idp.login(client, "authentik", {"sub": "sub-1"}, return_to="/library")
    loc = r.headers["location"]
    assert loc.startswith("/login#step=2fa&token=") and "methods=totp" in loc
    assert (await client.get("/api/auth/me")).status_code == 401
    token = re.search(r"token=([^&]+)", loc).group(1)
    ok = await verify(client, token, "totp", clock.code(secret))
    assert ok.status_code == 200 and ok.json()["return_to"] == "/library"
    assert (await client.get("/api/auth/me")).status_code == 200


async def test_oidc_trusting_provider_skips_2fa(
    client, fake_idp, make_provider, make_user, db_session, clock
):
    await make_provider(trusts_idp_mfa=True)
    await _oidc_user_with_totp(make_user, db_session, clock)
    r = await fake_idp.login(client, "authentik", {"sub": "sub-1"})
    assert r.headers["location"] == "/"
    assert (await client.get("/api/auth/me")).status_code == 200


async def test_oidc_without_2fa_unchanged(client, fake_idp, make_provider, make_user):
    await make_provider()
    await make_user(oidc_issuer=ISS, oidc_subject="sub-1", password=None)
    r = await fake_idp.login(client, "authentik", {"sub": "sub-1"})
    assert r.headers["location"] == "/"


async def test_admin_oidc_exposes_trusts_idp_mfa(login_as, make_user):
    admin = await login_as(await make_user("admin"))
    r = await admin.post(
        "/api/admin/oidc-providers",
        json={
            "name": "A",
            "slug": "a",
            "discovery_url": "https://idp.test/.well-known/openid-configuration",
            "client_id": "c",
            "client_secret": "s",
            "trusts_idp_mfa": True,
        },
    )
    assert r.status_code == 201 and r.json()["trusts_idp_mfa"] is True
    pid = r.json()["id"]
    r = await admin.patch(f"/api/admin/oidc-providers/{pid}", json={"trusts_idp_mfa": False})
    assert r.json()["trusts_idp_mfa"] is False


# --- admin reset -----------------------------------------------------------------


async def test_admin_reset(enrolled, login_as, make_user, client, db_session):
    user, _, _ = enrolled
    admin_user = await make_user("admin")
    admin = await login_as(admin_user)
    member = await login_as(await make_user("member"))
    assert (await member.post(f"/api/admin/users/{user.id}/2fa/reset")).status_code == 403
    assert (await admin.post(f"/api/admin/users/{admin_user.id}/2fa/reset")).status_code == 403
    assert (await admin.post("/api/admin/users/99999/2fa/reset")).status_code == 404
    assert (await admin.post(f"/api/admin/users/{user.id}/2fa/reset")).status_code == 204
    assert (await db_session.execute(select(UserTotp))).first() is None
    audit = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "user.2fa_reset"))
    ).scalar_one()
    assert audit.user_id == admin_user.id and audit.payload == {"target_user_id": user.id}
    r = await client.post(
        "/api/auth/login", json={"username": user.username, "password": DEFAULT_PASSWORD}
    )
    assert r.status_code == 204


async def test_sweeper_deletes_expired(enrolled, db_session):
    from app.tasks import auth as auth_task

    user = enrolled[0]
    past = datetime.now(UTC) - timedelta(minutes=1)
    future = datetime.now(UTC) + timedelta(minutes=5)
    for exp in (past, future):
        db_session.add(
            AuthEphemeralToken(
                token_hash=f"h{exp.timestamp()}",
                token_type="pre_auth",
                user_id=user.id,
                expires_at=exp,
            )
        )
        db_session.add(UserOtpCode(user_id=user.id, code_hash="x", expires_at=exp))
    await db_session.commit()
    assert auth_task.sweep_ephemeral() == 2
    db_session.expire_all()
    assert len((await db_session.execute(select(AuthEphemeralToken))).scalars().all()) == 1
    assert len((await db_session.execute(select(UserOtpCode))).scalars().all()) == 1


def test_beat_schedule_has_sweeper():
    from app.tasks.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["auth-sweep-ephemeral"]
    assert entry["task"] == "app.tasks.auth.sweep_ephemeral" and entry["schedule"] == 3600


async def test_failures_across_tokens_lock_the_user(enrolled, client, clock, login_as):
    user, secret, _ = enrolled
    clock.t += STEP
    for _ in range(2):
        tok = (await pre_auth(client, user))["pre_auth_token"]
        for _ in range(5):
            assert (await verify(client, tok, "totp", "000000")).status_code in (401, 410)
    # locked: a correct code on a fresh token is refused, and no new token is issued
    r = await client.post(
        "/api/auth/login", json={"username": user.username, "password": DEFAULT_PASSWORD}
    )
    assert r.status_code == 429 and r.json()["detail"]["code"] == "2fa_locked"


async def test_verify_refused_while_locked(enrolled, client, clock):
    user, secret, _ = enrolled
    clock.t += STEP
    tok = (await pre_auth(client, user))["pre_auth_token"]
    from app.services.two_factor import note_failure

    for _ in range(10):
        await note_failure(user.id)
    r = await verify(client, tok, "totp", clock.code(secret))
    assert r.status_code == 429 and r.json()["detail"]["code"] == "2fa_locked"


async def test_reauth_routes_share_the_lock(enrolled, clock):
    user, secret, codes = enrolled
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        clock.t += STEP
        tok = (await pre_auth(c, user))["pre_auth_token"]
        assert (await verify(c, tok, "backup", codes[0])).status_code == 200
        for _ in range(5):
            r = await c.post("/api/auth/2fa/totp/disable", json={"code_or_password": "wrong-pw"})
            assert r.status_code == 400
        for _ in range(5):
            r = await c.post("/api/auth/2fa/backup/regenerate", json={"code": "000000"})
            assert r.status_code == 400
        r = await c.post("/api/auth/2fa/totp/disable", json={"code_or_password": DEFAULT_PASSWORD})
        assert r.status_code == 429
        clock.t += STEP
        r = await c.post("/api/auth/2fa/backup/regenerate", json={"code": clock.code(secret)})
        assert r.status_code == 429
