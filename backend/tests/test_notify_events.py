from datetime import UTC, datetime

import pytest
import redis as redis_lib
from sqlalchemy import select

from app import printerd as printerd_module
from app.config import get_settings
from app.models.notify import NotificationChannel, NotificationLog, NotificationTemplate
from app.services.notify import ProviderError
from app.services.notify import events as nev
from app.services.notify.delivery import cron_due, dump_config, in_quiet_hours
from app.services.notify.templates import base_context, render
from app.tasks import base
from app.tasks import notify as ntask
from tests.cassettes import bambu_snapshots as cass
from tests.test_printerd import _seed_active_job, worker  # noqa: F401


@pytest.fixture
def emitted(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        printerd_module,
        "emit_bg",
        lambda ev, pid=None, payload=None: calls.append((ev, pid, payload)),
    )
    return calls


def test_printerd_emits_job_lifecycle(worker, emitted):  # noqa: F811
    w, _a, pid, _c = worker
    _seed_active_job(pid)
    w.handle_report(cass.SNAPSHOT_PRINTING)
    w.handle_report(cass.SNAPSHOT_FINISH)
    names = [e[0] for e in emitted]
    assert names == ["job_started", "job_finished"]
    assert emitted[1][1] == pid and emitted[1][2]["job_id"]


def test_printerd_emits_job_failed_on_error(worker, emitted):  # noqa: F811
    w, _a, pid, _c = worker
    _seed_active_job(pid)
    w.handle_report(cass.SNAPSHOT_ERROR)
    assert [e[0] for e in emitted] == ["job_failed"]


def test_printerd_emits_new_hms_only_and_dev_mode_off(worker, emitted):  # noqa: F811
    w, adapter, pid, _c = worker
    base_pub = adapter.public_state(cass.SNAPSHOT_IDLE)
    base_pub.hms = [{"attr": 0x03000100, "code": 0x10001}]
    w._write_public(base_pub)  # baseline: seeds, no event
    assert emitted == []
    base_pub.hms = base_pub.hms + [{"attr": 0x07000100, "code": 0x20002}]
    w._write_public(base_pub)
    assert [e[0] for e in emitted] == ["hms_error"]
    assert emitted[0][2]["raw_code"] == "0700010000020002"
    w._write_public(base_pub)  # same codes: nothing new
    assert len(emitted) == 1
    w.devmode.state = True
    w._write_public(base_pub)
    w.devmode.state = False
    w._write_public(base_pub)
    assert emitted[-1][0] == "dev_mode_off"


def test_emit_pushes_to_queue_and_swallows_errors(redis_url, monkeypatch):
    r = redis_lib.Redis.from_url(redis_url)
    r.delete(nev.QUEUE_KEY)
    assert nev.emit("job_started", 1, {"a": 1})
    assert r.llen(nev.QUEUE_KEY) == 1
    r.delete(nev.QUEUE_KEY)
    monkeypatch.setattr(
        nev.redis.Redis, "from_url", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    assert nev.emit("job_started") is False


def test_template_sandbox_and_fallback():
    ctx = base_context("job_finished", 1, "P", {"subtask_name": "x"})
    assert render("job_finished", "Hi {{ subtask_name }}", "b", ctx) == ("Hi x", "b")
    # undefined variable -> default template
    assert render("job_finished", "{{ nope }}", "b", ctx)[0].startswith("Print finished")
    # sandbox escape attempt -> default template, not code execution
    evil = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
    assert render("job_finished", evil, "b", ctx)[0].startswith("Print finished")


def test_quiet_hours_and_cron():
    q = {"start": "22:00", "end": "07:00", "tz": "UTC"}
    at = lambda h: datetime(2026, 10, 23, h, 0, tzinfo=UTC)  # noqa: E731
    assert in_quiet_hours(q, at(23)) and in_quiet_hours(q, at(3)) and not in_quiet_hours(q, at(12))
    assert not in_quiet_hours(None, at(3))
    last = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
    assert cron_due("0 9 * * *", last, datetime(2026, 10, 23, 9, 1, tzinfo=UTC))
    assert not cron_due(
        "0 9 * * *",
        datetime(2026, 10, 23, 9, 0, tzinfo=UTC),
        datetime(2026, 10, 23, 12, tzinfo=UTC),
    )


def _channel(**kw) -> int:
    with base.sync_session() as s:
        ch = NotificationChannel(
            kind=kw.pop("kind", "ntfy"),
            name="c",
            config_enc=dump_config(get_settings(), {"topic": "t"}),
            events=kw.pop("events", ["job_finished"]),
            **kw,
        )
        s.add(ch)
        s.commit()
        return ch.id


def _logs() -> list[NotificationLog]:
    with base.sync_session() as s:
        return list(s.scalars(select(NotificationLog).order_by(NotificationLog.id)))


@pytest.fixture
def sent(monkeypatch, redis_url, migrated_db):
    redis_lib.Redis.from_url(redis_url).delete(nev.QUEUE_KEY)
    calls: list = []
    monkeypatch.setattr(ntask, "send_message", lambda ch, msg, **k: calls.append((ch.id, msg)))
    return calls


def test_deliver_fans_out_to_subscribed_channels_with_template(sent):
    a = _channel()
    _channel(events=["job_failed"])
    _channel(enabled=False)
    with base.sync_session() as s:
        s.add(
            NotificationTemplate(
                event="job_finished", title_tpl="Done {{ subtask_name }}", body_tpl="b"
            )
        )
        s.commit()
    nev.emit("job_finished", None, {"subtask_name": "benchy"})
    nev.emit("job_started", None, {})
    assert ntask.deliver_pending() == 1
    assert [(c, m.title) for c, m in sent] == [(a, "Done benchy")]
    assert [row.status for row in _logs()] == ["sent"]


def test_quiet_hours_suppress_and_log(sent):
    _channel(quiet_hours={"start": "00:00", "end": "23:59", "tz": "UTC"})
    nev.emit("job_finished", None, {})
    ntask.deliver_pending()
    assert sent == [] and [r.status for r in _logs()] == ["quiet"]


def test_retries_three_times_then_logs_failed(monkeypatch, sent):
    cid = _channel()
    attempts = []

    def boom(ch, msg, **k):
        attempts.append(1)
        raise ProviderError("down")

    monkeypatch.setattr(ntask, "send_message", boom)

    class Retried(Exception):
        pass

    def fake_retry(exc=None, countdown=None, **k):
        raise Retried(countdown)

    monkeypatch.setattr(ntask.send_one, "retry", fake_retry)
    args = (cid, "job_finished", None, {})
    for n, backoff in enumerate(ntask.BACKOFF_S):  # attempts 1-3 retry, log nothing yet
        ntask.send_one.push_request(retries=n)
        with pytest.raises(Retried) as e:
            ntask.send_one.run(*args)
        ntask.send_one.pop_request()
        assert e.value.args == (backoff,) and _logs() == []
    ntask.send_one.push_request(retries=3)
    assert ntask.send_one.run(*args) == "failed" and len(attempts) == 4
    ntask.send_one.pop_request()
    logs = _logs()
    assert [r.status for r in logs] == ["failed"] and logs[0].error == "down"


def test_digest_queues_then_flushes_on_cron(sent, redis_url):
    cid = _channel(digest_cron="0 9 * * *")
    redis_lib.Redis.from_url(redis_url).delete(ntask.DIGEST_LAST_KEY.format(cid))
    nev.emit("job_finished", None, {"subtask_name": "a"})
    nev.emit("job_finished", None, {"subtask_name": "b"})
    ntask.deliver_pending()
    assert sent == [] and [r.status for r in _logs()] == ["queued", "queued"]
    now = datetime.now(UTC)
    assert ntask.digest(now=now.replace(hour=9, minute=1)) in (0, 1)
    # force due: channel created "now", so use a later day
    from datetime import timedelta

    assert ntask.digest(now=now + timedelta(days=1, hours=0)) >= 0
    statuses = [r.status for r in _logs()]
    assert statuses.count("digested") == 2 and statuses[-1] == "sent"
    assert "Digest: 2" in sent[-1][1].title and "Print finished: a" in sent[-1][1].body
