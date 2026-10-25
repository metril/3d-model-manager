from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.crypto import encrypt_secret
from app.models import PrintJob, User
from app.models.enums import PrinterKind, PrintJobState
from app.models.notify import UserEmailPreference
from app.models.printing import Printer
from app.services.server_config import ServerConfig, SmtpConfig, set_server_config
from app.tasks import base
from app.tasks import notify as ntask

PREFS = {
    "notify_print_start": False,
    "notify_print_complete": True,
    "notify_print_failed": False,
    "notify_print_stopped": True,
}


@pytest.fixture
async def smtp_on(db_session) -> None:
    await set_server_config(
        db_session,
        ServerConfig(
            slicer_enabled=False, smtp=SmtpConfig(host="smtp.test", from_addr="tdmm@test.local")
        ),
    )


# ---- API ------------------------------------------------------------------


async def test_get_defaults_without_row(make_user, login_as):
    c = await login_as(await make_user("viewer", email="v@example.com"))
    r = await c.get("/api/user-notifications/preferences")
    assert r.status_code == 200
    assert r.json() == {
        "notify_print_start": False,
        "notify_print_complete": False,
        "notify_print_failed": False,
        "notify_print_stopped": False,
        "smtp_configured": False,
        "has_email": True,
    }


async def test_put_upserts_and_flags(make_user, login_as, smtp_on):
    c = await login_as(await make_user("viewer"))  # allowlisted: a viewer may write
    r = await c.put("/api/user-notifications/preferences", json=PREFS)
    assert r.status_code == 200, r.text
    assert r.json() | PREFS == r.json() and r.json()["smtp_configured"] is True
    assert r.json()["has_email"] is False
    r = await c.put("/api/user-notifications/preferences", json={"notify_print_start": True})
    body = (await c.get("/api/user-notifications/preferences")).json()
    assert body["notify_print_start"] is True and body["notify_print_complete"] is False


async def test_anonymous_is_rejected(client):
    assert (await client.get("/api/user-notifications/preferences")).status_code == 401


async def test_preferences_are_per_user(make_user, login_as):
    a = await login_as(await make_user("member"))
    b = await login_as(await make_user("member"))
    await a.put("/api/user-notifications/preferences", json=PREFS)
    assert (await b.get("/api/user-notifications/preferences")).json()[
        "notify_print_complete"
    ] is False


# ---- fan-out ----------------------------------------------------------------


@pytest.fixture
def mails(monkeypatch, migrated_db):
    out: list[tuple[str, str, str]] = []
    monkeypatch.setattr(ntask.mailer, "send", lambda to, s, t, h=None: out.append((to, s, t)))
    monkeypatch.setattr(
        ntask,
        "get_server_config_sync",
        lambda s: SimpleNamespace(smtp=SimpleNamespace(configured=True)),
    )
    return out


def _seed(*, email="o@example.com", pref: dict | None = None, starter=True) -> int:
    with base.sync_session() as s:
        owner = User(username="owner", email=email)
        other = User(username="other", email="x@example.com")
        printer = Printer(
            name="P1",
            kind=PrinterKind.BAMBU_LAN,
            host="h",
            serial="S",
            access_code_enc=encrypt_secret(get_settings(), "12345678"),
            enabled=True,
        )
        s.add_all([owner, other, printer])
        s.flush()
        if pref is not None:
            s.add(UserEmailPreference(user_id=owner.id, **pref))
            s.add(UserEmailPreference(user_id=other.id, **{k: True for k in PREFS}))
        job = PrintJob(
            printer_id=printer.id,
            state=PrintJobState.PRINTING,
            created_by_user_id=owner.id if starter else None,
        )
        s.add(job)
        s.commit()
        return job.id


def _item(event, job_id):
    return {
        "event": event,
        "printer_id": 1,
        "payload": {"job_id": job_id, "subtask_name": "benchy"},
    }


def test_fan_out_sends_once_to_owner_only(mails):
    job = _seed(pref=PREFS)
    assert ntask.email_job_owner(_item("job_finished", job)) is True
    assert len(mails) == 1 and mails[0][0] == "o@example.com"
    assert "benchy" in mails[0][1]
    assert ntask.email_job_owner(_item("job_paused", job)) is True  # stopped pref
    assert len(mails) == 2


def test_no_send_without_matching_pref_event_or_prerequisites(mails, monkeypatch):
    job = _seed(pref=PREFS)
    assert not ntask.email_job_owner(_item("job_started", job))  # pref off
    assert not ntask.email_job_owner(_item("job_failed", job))  # pref off
    assert not ntask.email_job_owner(_item("hms_error", job))  # not a job event
    assert not ntask.email_job_owner({"event": "job_finished", "payload": {}})  # no job
    assert mails == []
    monkeypatch.setattr(
        ntask,
        "get_server_config_sync",
        lambda s: SimpleNamespace(smtp=SimpleNamespace(configured=False)),
    )
    assert not ntask.email_job_owner(_item("job_finished", job))  # no SMTP
    assert mails == []


def test_no_send_without_email_row_or_starter(mails):
    job = _seed(email=None, pref=PREFS)
    assert not ntask.email_job_owner(_item("job_finished", job))
    assert mails == []


def test_no_send_without_pref_row(mails):
    job = _seed(pref=None)
    assert not ntask.email_job_owner(_item("job_finished", job))


def test_no_send_for_external_job(mails):
    job = _seed(pref=PREFS, starter=False)
    assert not ntask.email_job_owner(_item("job_finished", job))


def test_mail_failure_never_raises(mails, monkeypatch):
    job = _seed(pref=PREFS)

    def boom(*a, **k):
        raise RuntimeError("broker down")

    monkeypatch.setattr(ntask.mailer, "send", boom)
    assert ntask.email_job_owner(_item("job_finished", job)) is False


def test_cancelled_job_uses_stopped_pref(mails):
    job = _seed(pref={**PREFS, "notify_print_failed": True, "notify_print_stopped": False})
    item = _item("job_failed", job)
    assert ntask.email_job_owner(item) is True  # plain failure -> failed pref
    item["payload"]["result"] = "canceled"
    assert not ntask.email_job_owner(item)  # cancelled -> stopped pref (off)
    assert len(mails) == 1


def test_cancelled_job_sends_when_stopped_pref_on(mails):
    job = _seed(pref={**PREFS, "notify_print_failed": False, "notify_print_stopped": True})
    item = _item("job_failed", job)
    assert not ntask.email_job_owner(item)
    item["payload"]["result"] = "canceled"
    assert ntask.email_job_owner(item) is True


def test_deliver_pending_emails_owner_once_per_event(mails, redis_url):
    import redis as redis_lib

    from app.services.notify import events as nev

    r = redis_lib.Redis.from_url(redis_url)
    r.delete(nev.QUEUE_KEY, ntask.PROCESSING_KEY)
    job = _seed(pref=PREFS)
    nev.emit("job_finished", 1, {"job_id": job, "subtask_name": "a"})
    nev.emit("job_started", 1, {"job_id": job})  # pref off
    nev.emit("job_finished", 1, {"job_id": job, "subtask_name": "b"})
    ntask.deliver_pending()
    assert [m[0] for m in mails] == ["o@example.com", "o@example.com"]
    assert r.llen(ntask.PROCESSING_KEY) == 0


def test_deliver_pending_sends_nothing_without_prereqs(mails, redis_url, monkeypatch):
    import redis as redis_lib

    from app.services.notify import events as nev

    r = redis_lib.Redis.from_url(redis_url)
    r.delete(nev.QUEUE_KEY, ntask.PROCESSING_KEY)
    job = _seed(email=None, pref=PREFS)
    nev.emit("job_finished", 1, {"job_id": job})
    ntask.deliver_pending()
    assert mails == []
