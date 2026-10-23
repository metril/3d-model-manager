import json
import time

import httpx
import pytest
import redis as redis_lib

from app.config import get_settings
from app.models import Setting
from app.printers.base import command_channel
from app.printers.obico_watcher import ObicoSupervisor, ObicoWatcher, Smoother, classify
from app.services import camera_state as cs
from app.services import obico
from app.services.obico import ObicoConfig, PrinterDetection
from app.tasks import base

JPEG = b"\xff\xd8\xff\xe0" + bytes(10) + b"\xff\xd9"
PID = 9101


def _transport(scores: list[float] | None = None, status: int = 200, hits: list | None = None):
    seq = iter(scores or [])

    def handler(req: httpx.Request) -> httpx.Response:
        if hits is not None:
            hits.append(req)
        if status != 200:
            return httpx.Response(status)
        p = next(seq, 0.0)
        dets = [["failure", p, [0, 0, 1, 1]]] if p else []
        return httpx.Response(200, json={"detections": dets})

    return httpx.MockTransport(handler)


@pytest.fixture
def rds(redis_url):
    r = redis_lib.Redis.from_url(redis_url)
    r.delete(cs.viewers_key(PID), cs.frame_key(PID), obico.status_key(PID))
    yield r
    r.close()


def _cfg(**kw) -> ObicoConfig:
    base_kw = dict(url="http://ml:3333", callback_url="http://api:8000", enabled=True)
    return ObicoConfig(**{**base_kw, **kw})


def _watcher(rds, transport, action="pause", events=None, **kw):
    def rec(redis_client, pid, job, score, smoothed, act, jpeg):
        if events is not None:
            events.append(act)
        return 1

    return ObicoWatcher(
        PID,
        rds,
        _cfg(),
        PrinterDetection(enabled=True, action=action),
        7,
        transport=transport,
        warmup=0,
        recorder=rec,
        **kw,
    )


def test_single_spike_never_pauses(rds):
    events: list[str] = []
    w = _watcher(rds, _transport([0, 0, 0, 1.0] + [0] * 20), events=events)
    pub = rds.pubsub()
    pub.subscribe(command_channel(PID))
    for _ in range(24):
        w.process(JPEG)
    assert "pause" not in events and not w.fired
    assert pub.get_message(timeout=0.2) is not None  # subscribe ack only
    assert pub.get_message(timeout=0.2) is None


def test_sustained_high_publishes_pause_once(rds):
    events: list[str] = []
    w = _watcher(rds, _transport([1.0] * 40), events=events)
    pub = rds.pubsub(ignore_subscribe_messages=True)
    pub.subscribe(command_channel(PID))
    pub.get_message(timeout=1)  # consume the subscribe ack
    for _ in range(30):
        w.process(JPEG)
    msgs = []
    while (m := pub.get_message(timeout=0.3)) is not None:
        msgs.append(json.loads(m["data"]))
    assert len(msgs) == 1
    assert msgs[0]["command"] == "pause" and msgs[0]["request_id"].startswith("obico:")
    assert events == ["warn", "pause"] or events == ["pause"]
    assert events.count("pause") == 1
    status = json.loads(rds.get(obico.status_key(PID)))
    assert status["state"] == "pause" and status["paused"] is False


def test_notify_action_does_not_pause(rds, monkeypatch):
    emitted = []
    monkeypatch.setattr(
        "app.printers.obico_watcher._emit", lambda ev, pid, payload: emitted.append(ev)
    )
    w = _watcher(rds, _transport([1.0] * 40), action="notify")
    pub = rds.pubsub(ignore_subscribe_messages=True)
    pub.subscribe(command_channel(PID))
    pub.get_message(timeout=1)  # consume the subscribe ack
    for _ in range(30):
        w.process(JPEG)
    assert pub.get_message(timeout=0.3) is None
    assert emitted == ["failure_suspected"]


def test_three_errors_set_paused_flag(rds):
    w = _watcher(rds, _transport(status=500))
    for _ in range(2):
        w.process(JPEG)
    assert not w.paused
    w.process(JPEG)
    assert w.paused
    assert json.loads(rds.get(obico.status_key(PID)))["paused"] is True
    cs.put_frame(rds, PID, JPEG)
    assert w.feed(now=1e6) is False  # paused: no more frames queued


def test_error_counter_resets_on_success(rds):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(500 if calls["n"] in (1, 2, 4, 5) else 200, json={"detections": []})

    w = _watcher(rds, httpx.MockTransport(handler))
    for _ in range(5):
        w.process(JPEG)
    assert not w.paused


def test_feed_registers_viewer_and_rate_limits(rds):
    w = _watcher(rds, _transport())
    cs.put_frame(rds, PID, JPEG)
    assert w.feed(now=100.0) is True
    assert cs.viewer_count(rds, PID) == 1
    w.queue.get_nowait()
    assert w.feed(now=105.0) is False  # < interval_s (10)
    assert w.feed(now=110.5) is True
    assert w.feed(now=125.0) is False  # queue still full (1-frame queue)


def test_smoother_warmup_and_classify():
    s = Smoother(warmup=3)
    assert [s.update(1.0) for _ in range(3)] == [0.0, 0.0, 0.0]
    assert s.update(1.0) > 0.78
    assert classify(0.1, 0.38, 0.78) == "safe"
    assert classify(0.5, 0.38, 0.78) == "warn"
    assert classify(0.9, 0.38, 0.78) == "pause"


def _seed_config(enabled=True, printer_enabled=True):
    with base.sync_session() as s:
        s.merge(Setting(key="obico", value=_cfg(enabled=enabled).model_dump()))
        s.merge(
            Setting(
                key=obico.printer_key(PID),
                value=PrinterDetection(enabled=printer_enabled, action="pause").model_dump(),
            )
        )
        s.commit()


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_supervisor_no_frames_unless_running(rds, migrated_db):
    _seed_config()
    hits: list = []
    cs.put_frame(rds, PID, JPEG)
    state = {"running": False}
    sup = ObicoSupervisor(rds, get_settings(), transport=_transport(hits=hits), warmup=0)
    sup.add(PID, lambda: state["running"])
    sup.tick()
    assert PID not in sup._watchers and cs.viewer_count(rds, PID) == 0 and not hits

    state["running"] = True
    sup.tick()
    assert PID in sup._watchers and cs.viewer_count(rds, PID) == 1
    assert _wait(lambda: len(hits) == 1)

    state["running"] = False
    sup.tick()
    assert PID not in sup._watchers
    assert rds.get(obico.status_key(PID)) is None
    sup.stop()


def test_supervisor_respects_disabled_flags(rds, migrated_db):
    cs.put_frame(rds, PID, JPEG)
    for g, p in ((False, True), (True, False)):
        _seed_config(enabled=g, printer_enabled=p)
        sup = ObicoSupervisor(rds, get_settings(), transport=_transport(), warmup=0)
        sup.add(PID, lambda: True)
        sup.tick()
        assert PID not in sup._watchers
    sup.stop()


def test_supervisor_autodisable_holds_until_job_ends(rds, migrated_db):
    _seed_config()
    state = {"running": True}
    sup = ObicoSupervisor(rds, get_settings(), transport=_transport(status=500), warmup=0)
    sup.add(PID, lambda: state["running"])
    sup.tick()
    w = sup._watchers[PID]
    for _ in range(3):
        w.process(JPEG)
    sup.tick()
    assert PID not in sup._watchers and PID in sup._disabled
    assert json.loads(rds.get(obico.status_key(PID)))["paused"] is True
    sup.tick()
    assert PID not in sup._watchers  # not restarted mid-job
    state["running"] = False
    sup.tick()
    assert PID not in sup._disabled and rds.get(obico.status_key(PID)) is None
    sup.stop()
