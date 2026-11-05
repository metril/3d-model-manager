"""Backfills run once on worker start (Redis-locked), not via beat."""

import pytest

from app.tasks import startup_backfills as sb
from app.tasks.celery_app import celery_app


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **k: calls.append((name, k)))
    return calls


class FakeRedis:
    def __init__(self):
        self.keys = set()

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True


def test_enqueues_each_once_then_locked(sent):
    client = FakeRedis()
    first = sb.enqueue_startup_backfills(client)
    second = sb.enqueue_startup_backfills(client)
    assert first == [n for n, _ in sb.BACKFILLS]
    assert second == []
    assert [c[0] for c in sent] == first
    assert all(c[1] == {"queue": "cpu"} for c in sent)


def test_redis_failure_does_not_raise(sent):
    class Boom:
        def set(self, *a, **k):
            raise ConnectionError("down")

    assert sb.enqueue_startup_backfills(Boom()) == []
    assert sent == []


def test_beat_no_longer_has_backfills():
    tasks = {e["task"] for e in celery_app.conf.beat_schedule.values()}
    assert not {n for n, _ in sb.BACKFILLS} & tasks
    assert "plates-backfill" not in celery_app.conf.beat_schedule
    assert "glb-orientation-backfill" not in celery_app.conf.beat_schedule


def test_worker_ready_handler_connected(monkeypatch):
    from celery.signals import worker_ready

    called = []
    monkeypatch.setattr(sb, "enqueue_startup_backfills", lambda: called.append(1))
    worker_ready.send(sender=None)
    assert called == [1]
