"""printerd camera supervisor + watchdog tests (P4 Task 3)."""

from __future__ import annotations

import time

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
    r = redis_lib.Redis.from_url(redis_url)
    sup = CameraSupervisor(r)
    sup.add(1, _adapter(FakeCamera([JPEG])))
    sup.tick()
    assert 1 not in sup._workers


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


def test_unsupported_sets_status_and_no_retry(redis_url):
    r = redis_lib.Redis.from_url(redis_url)
    sup = CameraSupervisor(r)
    pid = 15
    sup.add(pid, _adapter(None))  # camera=None -> open_camera raises NotImplementedError
    cs.add_viewer(r, pid, "v1")
    sup.tick()
    deadline = time.monotonic() + 3.0
    status = None
    while time.monotonic() < deadline:
        status = cs.get_status(r, pid)
        if status == "unsupported":
            break
        time.sleep(0.1)
    assert status == "unsupported"
