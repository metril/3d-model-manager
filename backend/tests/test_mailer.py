"""T6: server SMTP settings, the mailer (real in-process SMTP server) and the
email notify provider's ``use_server_smtp`` mode."""

from __future__ import annotations

import socket
from email import message_from_bytes, policy

import pytest
from aiosmtpd.controller import Controller
from sqlalchemy import select

from app.models import AuditLog, Setting
from app.services import mailer, server_config
from app.services.notify.base import Message
from app.services.notify.email import Email
from app.services.server_config import ServerConfig, SmtpConfig, set_server_config


class Inbox:
    def __init__(self) -> None:
        self.messages: list = []

    async def handle_DATA(self, server, session, envelope):  # noqa: N802 - aiosmtpd API
        self.messages.append(
            (
                envelope.mail_from,
                envelope.rcpt_tos,
                message_from_bytes(envelope.content, policy=policy.default),
            )
        )
        return "250 OK"


@pytest.fixture
def smtp_server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    inbox = Inbox()
    ctl = Controller(inbox, hostname="127.0.0.1", port=port)
    ctl.start()
    inbox.port = port
    yield inbox
    ctl.stop()


def cfg_for(inbox: Inbox, **kw) -> SmtpConfig:
    return SmtpConfig(
        host="127.0.0.1",
        port=inbox.port,
        security="none",
        from_addr="tdmm@test.local",
        from_name="TDMM",
        **kw,
    )


def test_deliver_plain_and_html(smtp_server):
    mailer.deliver(cfg_for(smtp_server), "u@example.com", "Hi", "plain body", "<b>html</b>")
    sender, rcpts, msg = smtp_server.messages[0]
    assert sender == "tdmm@test.local" and rcpts == ["u@example.com"]
    assert msg["Subject"] == "Hi" and msg["From"] == "TDMM <tdmm@test.local>"
    assert msg.get_body(("plain",)).get_content().strip() == "plain body"
    assert "<b>html</b>" in msg.get_body(("html",)).get_content()


def test_deliver_unconfigured_and_unreachable():
    with pytest.raises(mailer.MailError):
        mailer.deliver(SmtpConfig(), "u@example.com", "s", "t")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(mailer.MailError, match="SMTP failed"):
        mailer.deliver(
            SmtpConfig(host="127.0.0.1", port=port, security="none", from_addr="a@b.c"),
            "u@example.com",
            "s",
            "t",
        )


def test_task_delivers_via_server_settings(smtp_server, monkeypatch):
    from app.tasks import mail as mail_task

    monkeypatch.setattr(mail_task, "current_sync", lambda: ServerConfig(smtp=cfg_for(smtp_server)))
    assert mailer.send("u@example.com", "Queued", "body") is None  # eager celery runs it inline
    assert smtp_server.messages[0][2]["Subject"] == "Queued"


def test_task_skips_when_unconfigured(monkeypatch):
    from app.tasks import mail as mail_task

    monkeypatch.setattr(mail_task, "current_sync", lambda: ServerConfig())
    assert mail_task.send_mail("u@example.com", "s", "t") is False


def test_task_retries_with_backoff(monkeypatch):
    from celery.exceptions import Retry

    from app.tasks import mail as mail_task

    monkeypatch.setattr(
        mail_task,
        "current_sync",
        lambda: ServerConfig(smtp=SmtpConfig(host="h", from_addr="a@b.c")),
    )

    def boom(*a, **k):
        raise mailer.MailError("SMTP failed: OSError")

    monkeypatch.setattr(mail_task.mailer, "deliver", boom)
    with pytest.raises(Retry):
        mail_task.send_mail.apply(args=("u@example.com", "s", "t"), throw=True)
    assert mail_task.MAX_RETRIES == 3 and mail_task.BACKOFF_S == (10, 60, 300)


async def test_smtp_test_route(login_as, make_user, smtp_server, db_session):
    admin = await login_as(await make_user("admin"))
    r = await admin.post("/api/settings/server/smtp/test", json={"to": "x@example.com"})
    assert r.json() == {"ok": False, "error": "not_configured"}
    await set_server_config(
        db_session, ServerConfig(slicer_enabled=False, smtp=cfg_for(smtp_server))
    )
    r = await admin.post("/api/settings/server/smtp/test", json={"to": "x@example.com"})
    assert r.json() == {"ok": True, "error": None}
    assert smtp_server.messages[0][1] == ["x@example.com"]
    bad = await admin.post("/api/settings/server/smtp/test", json={"to": "not-an-email"})
    assert bad.status_code == 422


async def test_smtp_test_route_admin_only(login_as, make_user):
    member = await login_as(await make_user("member"))
    r = await member.post("/api/settings/server/smtp/test", json={"to": "x@example.com"})
    assert r.status_code == 403


async def test_smtp_password_masked_encrypted_and_kept(login_as, make_user, db_session):
    admin = await login_as(await make_user("admin"))
    cfg = (await admin.get("/api/settings/server")).json()["config"]
    assert cfg["smtp"]["port"] == 587 and cfg["smtp"]["security"] == "starttls"
    cfg["smtp"].update(host="smtp.test", username="u", password="hunter2", from_addr="a@b.c")
    r = await admin.put("/api/settings/server", json=cfg)
    assert r.status_code == 200, r.text
    assert r.json()["config"]["smtp"]["password"] == "***"
    row = await db_session.get(Setting, "server")
    stored = row.value["smtp"]["password"]
    assert stored not in ("hunter2", "***") and "hunter2" not in str(row.value)
    assert (
        await server_config.get_server_config(db_session)
    ).smtp.password.get_secret_value() == "hunter2"

    # "***" round-trips as "keep"; null clears
    cfg = (await admin.get("/api/settings/server")).json()["config"]
    cfg["smtp"]["host"] = "smtp2.test"
    assert (await admin.put("/api/settings/server", json=cfg)).status_code == 200
    db_session.expire_all()
    assert (
        await server_config.get_server_config(db_session)
    ).smtp.password.get_secret_value() == "hunter2"
    cfg["smtp"]["password"] = None
    await admin.put("/api/settings/server", json=cfg)
    db_session.expire_all()
    assert (await server_config.get_server_config(db_session)).smtp.password is None

    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "server_settings.update")
            )
        )
        .scalars()
        .all()
    )
    assert "hunter2" not in str([a.payload for a in audits])
    assert any("smtp.host" in a.payload["changed"] for a in audits)


async def test_smtp_validation(login_as, make_user):
    admin = await login_as(await make_user("admin"))
    cfg = (await admin.get("/api/settings/server")).json()["config"]
    cfg["smtp"]["security"] = "bogus"
    assert (await admin.put("/api/settings/server", json=cfg)).status_code == 422
    cfg["smtp"].update(security="tls", port=0)
    assert (await admin.put("/api/settings/server", json=cfg)).status_code == 422


def test_env_seed_smtp(monkeypatch):
    env = {
        "TDMM_SMTP_HOST": "mail.example.com",
        "TDMM_SMTP_PORT": "465",
        "TDMM_SMTP_SECURITY": "tls",
        "TDMM_SMTP_USERNAME": "user",
        "TDMM_SMTP_PASSWORD": "pw",
        "TDMM_SMTP_FROM": "noreply@example.com",
    }
    data, seeded = server_config._build_seed(env, None)
    assert data["smtp"]["host"] == "mail.example.com" and data["smtp"]["port"] == 465
    assert data["smtp"]["security"] == "tls" and data["smtp"]["password"] == "pw"
    assert data["smtp"]["from_addr"] == "noreply@example.com"
    assert {"smtp.host", "smtp.port", "smtp.password", "smtp.from_addr"} <= set(seeded)
    assert server_config.ENV_SEED["smtp.from_addr"] == "TDMM_SMTP_FROM"
    # invalid value degrades to the default for that field only
    data, seeded = server_config._build_seed(
        {"TDMM_SMTP_PORT": "99999", "TDMM_SMTP_HOST": "h"}, None
    )
    assert data["smtp"]["port"] == 587 and data["smtp"]["host"] == "h"


async def test_seeded_password_is_encrypted_at_rest(db_session, monkeypatch):
    await db_session.delete(await db_session.get(Setting, "server"))
    await db_session.commit()
    monkeypatch.setenv("TDMM_SMTP_HOST", "mail.example.com")
    monkeypatch.setenv("TDMM_SMTP_PASSWORD", "seedpw")
    assert await server_config.seed_server_config(db_session)
    row = await db_session.get(Setting, "server")
    assert "seedpw" not in str(row.value)
    cfg = await server_config.get_server_config(db_session)
    assert cfg.smtp.password.get_secret_value() == "seedpw"
    assert "smtp.password" in row.value["_seeded_from_env"]


def test_email_provider_server_smtp_mode(smtp_server, monkeypatch):
    monkeypatch.setattr(
        "app.services.server_config.current_sync",
        lambda: ServerConfig(smtp=cfg_for(smtp_server)),
    )
    assert any(f["name"] == "use_server_smtp" for f in Email.fields)
    Email().send({"use_server_smtp": "true", "to_addr": "t@example.com"}, Message("Title", "Body"))
    _, rcpts, msg = smtp_server.messages[0]
    assert rcpts == ["t@example.com"] and msg["Subject"] == "Title"
    assert msg["From"] == "TDMM <tdmm@test.local>"


def test_email_provider_server_smtp_unconfigured(monkeypatch):
    from app.services.notify.base import ProviderError

    monkeypatch.setattr("app.services.server_config.current_sync", lambda: ServerConfig())
    with pytest.raises(ProviderError, match="not configured"):
        Email().send({"use_server_smtp": "true", "to_addr": "t@example.com"}, Message("T", "B"))
