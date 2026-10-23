"""printerd camera supervisor (P4 "Live camera and timelapse" plan, Task 3).
Runs entirely on its own thread -- never the status-poll or command
threads -- so a wedged camera socket can never block printerd's main loop.
``CameraWorker`` owns one printer's camera connection; ``CameraSupervisor``
starts/stops workers on demand (viewer refcount in Redis), idle-stops them,
and restarts a stalled one.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from app.printers.base import PrinterAdapter
from app.services import camera_state

log = logging.getLogger("printerd.camera")

MIN_FRAME_INTERVAL_S = 0.2  # ~5 fps cap
IDLE_STOP_S = 30
STALL_S = 15
JOIN_TIMEOUT_S = 2.0
TICK_INTERVAL_S = 1.0
BACKOFF_START_S = 2.0
BACKOFF_MAX_S = 60.0


class CameraWorker:
    """Owns one printer's camera connection on its own daemon thread. Never
    blocks its caller: ``stop()`` is bounded by ``JOIN_TIMEOUT_S`` and never
    raises, even against a hung source (``close()`` unblocks it)."""

    def __init__(
        self,
        printer_id: int,
        adapter: PrinterAdapter,
        on_frame: Callable[[bytes], None],
        on_status: Callable[[str], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        initial_backoff: float = BACKOFF_START_S,
    ) -> None:
        self.printer_id = printer_id
        self.adapter = adapter
        self.on_frame = on_frame
        self.on_status = on_status
        self.clock = clock
        self.last_frame_at: float | None = None
        self.started_at: float | None = None
        # Reconnect backoff -- an instance attribute (not a local in
        # ``_run``) so the supervisor can read its current value when it
        # replaces this worker (a stalled-worker watchdog restart) and
        # carry it into the replacement's ``initial_backoff``, rather than
        # resetting to ``BACKOFF_START_S`` on every restart.
        self.backoff = initial_backoff
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._source_lock = threading.Lock()
        self._source = None

    def start(self) -> None:
        self.started_at = self.clock()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=f"cam-{self.printer_id}"
        )
        self._thread.start()

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _signal_stop(self) -> None:
        """Non-blocking: set the stop event and close whatever source is
        currently open (closing is itself fast/bounded for both
        ``CameraA1Client`` and ``FakeCamera``). Used by the supervisor's
        ``remove()`` so it never blocks its caller; ``stop()`` below also
        uses it before joining."""
        self._stop.set()
        with self._source_lock:
            source = self._source
        if source is not None:
            try:
                source.close()
            except Exception:
                log.debug("camera worker %s: close() during signal raised", self.printer_id)

    def stop(self) -> bool:
        """No-op before ``start()``. Never raises, never blocks longer than
        ``JOIN_TIMEOUT_S``. Returns True if the thread actually joined."""
        if self._thread is None:
            return False
        try:
            self._signal_stop()
            self._thread.join(JOIN_TIMEOUT_S)
            return not self._thread.is_alive()
        except Exception:
            log.exception("camera worker %s: stop() raised", self.printer_id)
            return False

    def _run(self) -> None:
        last_sent = 0.0
        while not self._stop.is_set():
            try:
                source = self.adapter.open_camera(self._stop)
            except NotImplementedError:
                self.on_status("unsupported")
                return
            except Exception:
                log.warning("camera worker %s: open_camera failed", self.printer_id)
                self.on_status("error")
                if self._stop.wait(self.backoff):
                    return
                self.backoff = min(self.backoff * 2, BACKOFF_MAX_S)
                continue

            with self._source_lock:
                self._source = source
            self.on_status("connecting")
            ok = True
            try:
                with source:
                    for frame in source.frames():
                        if self._stop.is_set():
                            break
                        now = self.clock()
                        if now - last_sent < getattr(source, "min_interval", MIN_FRAME_INTERVAL_S):
                            continue
                        last_sent = now
                        self.last_frame_at = now
                        self.on_status("live")
                        self.on_frame(frame)
            except Exception:
                ok = False
                log.warning("camera worker %s: frames() failed", self.printer_id)
                self.on_status("error")
            finally:
                with self._source_lock:
                    self._source = None

            if self._stop.is_set():
                return
            if ok:
                self.backoff = BACKOFF_START_S
                wait_s = BACKOFF_START_S
            else:
                wait_s = self.backoff
                self.backoff = min(self.backoff * 2, BACKOFF_MAX_S)
            if self._stop.wait(wait_s):
                return


class CameraSupervisor:
    """Starts a ``CameraWorker`` per printer only while its Redis viewer
    ZSET is non-empty, stops it ``idle_stop_s`` after the last viewer
    leaves, and restarts a worker that's alive but has produced no frame
    for ``stall_s``. Runs its own ``tick()`` loop on a dedicated thread --
    never the status-poll or command threads."""

    def __init__(
        self,
        redis_client,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        idle_stop_s: float = IDLE_STOP_S,
        stall_s: float = STALL_S,
    ) -> None:
        self.redis = redis_client
        self.clock = clock
        self.wall = wall
        self.idle_stop_s = idle_stop_s
        self.stall_s = stall_s
        self._adapters: dict[int, PrinterAdapter] = {}
        self._workers: dict[int, CameraWorker] = {}
        self._last_viewer_at: dict[int, float] = {}
        # Printers whose camera reported "unsupported" -- a worker that
        # hit this exits its thread immediately (``alive`` goes False), so
        # without tracking it separately `_tick_one` would otherwise read
        # that as "needs restarting" and spin up a new worker every tick
        # forever. Reconnect backoff carried across a stall-restart (not
        # reset to ``BACKOFF_START_S``), keyed the same way.
        self._unsupported: set[int] = set()
        self._backoff: dict[int, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def add(self, printer_id: int, adapter: PrinterAdapter) -> None:
        self._adapters[printer_id] = adapter

    def remove(self, printer_id: int) -> None:
        """Non-blocking: signals the worker (if any) to stop; the next
        ``tick()`` actually joins it and drops the reference."""
        self._adapters.pop(printer_id, None)
        self._last_viewer_at.pop(printer_id, None)
        self._unsupported.discard(printer_id)
        self._backoff.pop(printer_id, None)
        worker = self._workers.get(printer_id)
        if worker is not None:
            worker._signal_stop()

    def _start_worker(self, printer_id: int, adapter: PrinterAdapter) -> CameraWorker:
        def on_frame(jpeg: bytes) -> None:
            camera_state.put_frame(self.redis, printer_id, jpeg)

        def on_status(status: str) -> None:
            if status == "unsupported":
                self._unsupported.add(printer_id)
            else:
                self._unsupported.discard(printer_id)
            camera_state.set_status(self.redis, printer_id, status)

        worker = CameraWorker(
            printer_id,
            adapter,
            on_frame,
            on_status,
            clock=self.clock,
            initial_backoff=self._backoff.get(printer_id, BACKOFF_START_S),
        )
        worker.start()
        self._workers[printer_id] = worker
        return worker

    def _tick_one(self, printer_id: int, now_wall: float, now_clock: float) -> None:
        adapter = self._adapters.get(printer_id)
        worker = self._workers.get(printer_id)

        if adapter is None:
            # Removed since the last tick -- reap the signalled worker.
            if worker is not None:
                worker.stop()
                self._workers.pop(printer_id, None)
            self._last_viewer_at.pop(printer_id, None)
            self._unsupported.discard(printer_id)
            self._backoff.pop(printer_id, None)
            return

        count = camera_state.viewer_count(self.redis, printer_id, now=now_wall)
        if count > 0:
            self._last_viewer_at[printer_id] = now_clock
            # A worker whose camera is unsupported exits immediately
            # (``alive`` is False) -- without this check, every tick would
            # read that as "needs restarting" and spin up a worker that
            # just re-reports unsupported and exits again, forever.
            if printer_id not in self._unsupported and (worker is None or not worker.alive):
                worker = self._start_worker(printer_id, adapter)
        else:
            last = self._last_viewer_at.get(printer_id)
            if worker is not None and last is not None and now_clock - last >= self.idle_stop_s:
                worker.stop()
                self._workers.pop(printer_id, None)
                self._last_viewer_at.pop(printer_id, None)
                self._backoff.pop(printer_id, None)
                return

        if worker is not None and worker.alive:
            ref = worker.last_frame_at if worker.last_frame_at is not None else worker.started_at
            if ref is not None and now_clock - ref >= self.stall_s:
                log.warning("camera supervisor: printer %s stalled, restarting", printer_id)
                self._backoff[printer_id] = worker.backoff
                worker.stop()
                self._start_worker(printer_id, adapter)

    def tick(self) -> None:
        now_wall = self.wall()
        now_clock = self.clock()
        for printer_id in set(self._adapters) | set(self._workers):
            try:
                self._tick_one(printer_id, now_wall, now_clock)
            except Exception:
                log.exception("camera supervisor: tick failed for printer %s", printer_id)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="camera-supervisor")
        self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("camera supervisor: tick loop failed")
            if self._stop.wait(TICK_INTERVAL_S):
                return

    def stop(self) -> None:
        """Bounded: at most ``JOIN_TIMEOUT_S`` per worker, plus the
        supervisor thread's own join."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(JOIN_TIMEOUT_S)
        for worker in list(self._workers.values()):
            worker.stop()
        self._workers.clear()
