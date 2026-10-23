"""Camera Redis plumbing tests (P4 Task 2)."""

from __future__ import annotations

import threading
import time

import redis as redis_lib

from app.services import camera_state as cs

JPEG = b"\xff\xd8\xff\xe0" + bytes(10) + b"\xff\xd9"


def _client(redis_url: str) -> redis_lib.Redis:
    """The Redis container is session-shared and these tests use small fixed
    printer ids that other camera tests (API snapshot viewers, stamped with
    real wall time) also write -- start each test from clean camera keys."""
    r = redis_lib.Redis.from_url(redis_url)
    for key in r.scan_iter("printer:*:camera:*"):
        r.delete(key)
    return r


def test_viewer_expires_after_ttl(redis_url):
    r = _client(redis_url)
    pid = 1
    t = 1_000_000.0
    cs.add_viewer(r, pid, "v1", now=t)
    assert cs.viewer_count(r, pid, now=t) == 1
    assert cs.viewer_count(r, pid, now=t + 11) == 0


def test_heartbeat_extends(redis_url):
    r = _client(redis_url)
    pid = 2
    t = 1_000_000.0
    cs.add_viewer(r, pid, "v1", now=t)
    cs.add_viewer(r, pid, "v1", now=t + 8)  # heartbeat before first TTL expiry
    assert cs.viewer_count(r, pid, now=t + 11) == 1
    assert cs.viewer_count(r, pid, now=t + 19) == 0


def test_remove_viewer(redis_url):
    r = _client(redis_url)
    pid = 3
    t = 1_000_000.0
    cs.add_viewer(r, pid, "v1", now=t)
    cs.remove_viewer(r, pid, "v1")
    assert cs.viewer_count(r, pid, now=t) == 0


def test_put_frame_sets_ttl_and_publishes(redis_url):
    r = _client(redis_url)
    pid = 4
    pubsub = r.pubsub()
    pubsub.subscribe(cs.frame_channel(pid))
    while pubsub.get_message(timeout=0.1):
        pass
    cs.put_frame(r, pid, JPEG)
    deadline = time.monotonic() + 5.0
    msg = None
    while time.monotonic() < deadline:
        m = pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
        if m and m["type"] == "message":
            msg = m
            break
    assert msg is not None and msg["data"] == JPEG
    assert r.get(cs.frame_key(pid)) == JPEG
    assert r.ttl(cs.frame_key(pid)) <= 5


def test_keep_job_frame_copies(redis_url):
    r = _client(redis_url)
    pid = 5
    job_id = 99
    cs.put_frame(r, pid, JPEG)
    assert cs.keep_job_frame(r, pid, job_id) is True
    assert r.get(cs.job_frame_key(pid, job_id)) == JPEG
    assert r.ttl(cs.job_frame_key(pid, job_id)) <= 3600


def test_keep_job_frame_no_frame_returns_false(redis_url):
    r = _client(redis_url)
    pid = 6
    assert cs.keep_job_frame(r, pid, 1) is False


def test_set_and_get_status(redis_url):
    r = _client(redis_url)
    pid = 7
    assert cs.get_status(r, pid) is None
    cs.set_status(r, pid, "live")
    assert cs.get_status(r, pid) == "live"


def test_wait_for_frame_registers_viewer_then_cleans_up(redis_url):
    r = _client(redis_url)
    pid = 8

    def _publish_later():
        time.sleep(0.3)
        cs.put_frame(r, pid, JPEG)

    t = threading.Thread(target=_publish_later, daemon=True)
    t.start()
    result = cs.wait_for_frame(r, pid, timeout=5.0, viewer_id="vwait")
    t.join()
    assert result == JPEG
    assert cs.viewer_count(r, pid) == 0


def test_wait_for_frame_times_out_and_cleans_up(redis_url):
    r = _client(redis_url)
    pid = 9
    result = cs.wait_for_frame(r, pid, timeout=1.0, viewer_id="vtimeout")
    assert result is None
    assert cs.viewer_count(r, pid) == 0
