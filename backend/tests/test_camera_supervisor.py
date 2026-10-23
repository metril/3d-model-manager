"""printerd camera supervisor + watchdog tests (P4 Task 3)."""

from __future__ import annotations

import threading
import time

import pytest
import redis as redis_lib

from app.printers.base import PrinterConnection
from app.printers.camera_supervisor import JOIN_TIMEOUT_S, CameraSupervisor, CameraWorker
from app.printers.fake import FakeCamera, FakePrinterAdapter
from app.services import camera_state as cs

JPEG = b"\xff\xd8\xff\xe0" + bytes(10) + b"\xff\xd9"


def _adapter(camera: FakeCamera | None = None) -> FakePrinterAdapter:
    a = FakePrinterAdapter(PrinterConnection(host="h", serial="S", access_code="x"))
    a.camera = camera
    return a


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_worker_stop_before_start_returns():
    w = CameraWorker(1, _adapter(), lambda f: None, lambda s: None)
    t0 = time.monotonic()
    w.stop()
    assert time.monotonic() - t0 < 0.5


def test_worker_stop_with_hung_source_bounded():
    w = CameraWorker(1, _adapter(FakeCamera([], hang=True)), lambda f: None, lambda s: None)
    w.start()
    time.sleep(0.1)
    t0 = time.monotonic()
    w.stop()
    assert time.monotonic() - t0 <= JOIN_TIMEOUT_S + 0.5


def test_worker_initial_backoff_seed():
    w = CameraWorker(1, _adapter(), lambda f: None, lambda s: None, initial_backoff=16.0)
    assert w.backoff == 16.0


def test_frames_rate_capped():
    received: list[bytes] = []
    w = CameraWorker(
        1, _adapter(FakeCamera([JPEG] * 50, delay=0.0)), received.append, lambda s: None
    )
    w.start()
    time.sleep(1.0)
    w.stop()
    assert len(received) <= 6


def test_no_worker_without_viewers(redis_url):
    """Final review F1: ``redis_url`` is session-scoped, and OTHER tests in
    this module (and ``test_printerd.py``'s hung-camera daemon test) seed a
    viewer for printer id 1 -- clear that printer's viewer ZSET before AND
    after so a leftover registration from one of those can never bleed
    into this test and make it spuriously start a worker."""
    r = redis_lib.Redis.from_url(redis_url)
    r.delete(cs.viewers_key(1))
    try:
        sup = CameraSupervisor(r)
        sup.add(1, _adapter(FakeCamera([JPEG])))
        sup.tick()
        assert 1 not in sup._workers
    finally:
        r.delete(cs.viewers_key(1))


def test_viewer_starts_worker_and_frame_reaches_redis(redis_url):
    r = redis_lib.Redis.from_url(redis_url)
    sup = CameraSupervisor(r)
    pid = 11
    sup.add(pid, _adapter(FakeCamera([JPEG] * 10, delay=0.05)))
    cs.add_viewer(r, pid, "v1")
    sup.tick()
    deadline = time.monotonic() + 5.0
    frame = None
    while time.monotonic() < deadline:
        frame = cs.get_frame(r, pid)
        if frame is not None:
            break
        time.sleep(0.1)
    assert frame == JPEG
    assert cs.get_status(r, pid) == "live"
    sup.stop()


def test_two_viewers_one_worker(redis_url):
    r = redis_lib.Redis.from_url(redis_url)
    sup = CameraSupervisor(r)
    pid = 12
    sup.add(pid, _adapter(FakeCamera([JPEG] * 5, delay=0.05)))
    cs.add_viewer(r, pid, "v1")
    cs.add_viewer(r, pid, "v2")
    sup.tick()
    worker1 = sup._workers[pid]
    sup.tick()
    assert sup._workers[pid] is worker1
    sup.stop()


def test_idle_shutdown_after_last_viewer(redis_url):
    r = redis_lib.Redis.from_url(redis_url)
    clock = _FakeClock()
    sup = CameraSupervisor(r, clock=clock, stall_s=100_000)
    pid = 13
    sup.add(pid, _adapter(FakeCamera([], hang=True)))
    cs.add_viewer(r, pid, "v1")
    sup.tick()
    assert pid in sup._workers
    cs.remove_viewer(r, pid, "v1")
    clock.advance(29)
    sup.tick()
    assert pid in sup._workers
    clock.advance(2)  # total +31
    sup.tick()
    assert pid not in sup._workers


def test_watchdog_restarts_stalled_worker(redis_url):
    r = redis_lib.Redis.from_url(redis_url)
    clock = _FakeClock()
    sup = CameraSupervisor(r, clock=clock)
    pid = 14
    sup.add(pid, _adapter(FakeCamera([], hang=True)))
    cs.add_viewer(r, pid, "v1", now=time.time())
    sup.tick()
    worker1 = sup._workers[pid]
    clock.advance(16)
    sup.tick()
    worker2 = sup._workers.get(pid)
    assert worker2 is not None and worker2 is not worker1


def test_watchdog_restart_carries_backoff(redis_url):
    """Final review item 6: a stall-triggered restart seeds the
    replacement worker's backoff from the old worker's current value
    instead of resetting to ``BACKOFF_START_S``."""
    r = redis_lib.Redis.from_url(redis_url)
    clock = _FakeClock()
    sup = CameraSupervisor(r, clock=clock)
    pid = 16
    sup.add(pid, _adapter(FakeCamera([], hang=True)))
    cs.add_viewer(r, pid, "v1", now=time.time())
    sup.tick()
    worker1 = sup._workers[pid]
    worker1.backoff = 16.0  # simulate several prior failed reconnects
    clock.advance(16)
    sup.tick()
    worker2 = sup._workers[pid]
    assert worker2 is not worker1
    assert worker2.backoff == 16.0


def test_unsupported_sets_status_and_no_retry(redis_url):
    r = redis_lib.Redis.from_url(redis_url)
    clock = _FakeClock()
    sup = CameraSupervisor(r, clock=clock)
    pid = 15
    sup.add(pid, _adapter(None))  # camera=None -> open_camera raises NotImplementedError
    cs.add_viewer(r, pid, "v1", now=time.time())
    sup.tick()
    deadline = time.monotonic() + 3.0
    status = None
    while time.monotonic() < deadline:
        status = cs.get_status(r, pid)
        if status == "unsupported":
            break
        time.sleep(0.1)
    assert status == "unsupported"

    worker_after_unsupported = sup._workers[pid]
    assert worker_after_unsupported.alive is False

    # Several more ticks (viewer still present, as if the page stayed
    # open) must NOT spin up a fresh worker -- a camera that's unsupported
    # stays unsupported; retrying it is pure churn.
    for _ in range(5):
        clock.advance(1.0)
        sup.tick()
    assert sup._workers[pid] is worker_after_unsupported
    assert cs.get_status(r, pid) == "unsupported"


# -- Q4 source selection -----------------------------------------------------


def _conn(model, options=None):
    return PrinterConnection(
        host="h", serial="SER", access_code="code", model=model, options=options or {}
    )


@pytest.mark.parametrize(
    ("model", "options", "cls"),
    [
        ("A1 Mini", {}, "CameraA1Client"),
        ("X1C", {}, "FfmpegMjpegSource"),
        (
            "X1C",
            {"camera": {"kind": "snapshot", "url": "http://cam/s.jpg"}},
            "ExternalCameraSource",
        ),
        ("A1", {"camera": {"kind": "mjpeg", "url": "http://cam/s.mjpg"}}, "ExternalCameraSource"),
    ],
)
def test_open_camera_source_selection(model, options, cls) -> None:
    from app.printers.bambu import BambuLanAdapter

    src = BambuLanAdapter(_conn(model, options)).open_camera(threading.Event())
    assert type(src).__name__ == cls


def test_open_camera_none_unsupported() -> None:
    from app.printers.bambu import BambuLanAdapter

    with pytest.raises(NotImplementedError):
        BambuLanAdapter(_conn("Unknown Model")).open_camera(threading.Event())


def test_camera_options_roundtrip(tmp_path, monkeypatch) -> None:
    from app.config import get_settings
    from app.printers.camera_select import decrypt_options, normalize_options, redact_options

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    s = get_settings()
    norm = normalize_options(
        s, {"camera": {"kind": "mjpeg", "url": "http://u:p@cam/x"}, "camera_rotation": 90}, None
    )
    assert "u:p" not in str(norm) and norm["camera"]["url_enc"]
    assert redact_options(norm)["camera"] == {"kind": "mjpeg", "has_url": True}
    assert decrypt_options(s, norm)["camera"]["url"] == "http://u:p@cam/x"
    # editing without re-sending the url keeps the stored ciphertext
    kept = normalize_options(s, {"camera": {"kind": "mjpeg"}}, norm)
    assert kept["camera"]["url_enc"] == norm["camera"]["url_enc"]
    with pytest.raises(ValueError):
        normalize_options(s, {"camera_rotation": 45}, None)
    with pytest.raises(ValueError):
        normalize_options(s, {"camera": {"kind": "rtsp", "url": "http://x"}}, None)
    get_settings.cache_clear()
