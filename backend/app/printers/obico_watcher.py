"""printerd Obico failure watcher (Q10). One scorer thread per printer, owned
by ``ObicoSupervisor`` (its own tick thread beside the camera supervisor).
Runs only while: global enabled + per-printer enabled + job RUNNING. Registers
as a camera viewer so the camera stays alive; a 1-frame queue and
``interval_s`` bound the ML load. Isolation rule (same as the camera
supervisor): nothing here ever raises into the worker loop, and a pause goes
through the normal command bus (Redis publish, ``request_id`` ``obico:*``) --
never a direct adapter call from this thread."""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select

from app.config import Settings
from app.models import PrintJob
from app.models.enums import PrintJobState
from app.printers.base import command_channel
from app.services import camera_state, obico
from app.services.notify.events import emit
from app.services.obico import ObicoConfig, PrinterDetection
from app.services.obico_ml import MlError, score_frame
from app.tasks import base

log = logging.getLogger("printerd.obico")

EWM_ALPHA = 2.0 / (12 + 1)
ROLLING_SHORT = 310
ROLLING_LONG = 7200
WARMUP_FRAMES = 30
MAX_ERRORS = 3
WARN_HYSTERESIS = 0.15
WARN_COOLDOWN_S = 300.0
TICK_INTERVAL_S = 1.0
CONFIG_TTL_S = 5.0
JOIN_TIMEOUT_S = 2.0
_TERMINAL = (PrintJobState.FINISHED, PrintJobState.FAILED, PrintJobState.CANCELED)


@dataclass
class Smoother:
    """Obico's smoothing: per-frame EWM (span 12), final score = max(ewm,
    rolling_short - rolling_long); 'safe' (0.0) during warmup."""

    warmup: int = WARMUP_FRAMES
    frame_count: int = 0
    ewm_mean: float = 0.0
    short_sum: float = 0.0
    long_sum: float = 0.0
    short_buf: deque = field(default_factory=lambda: deque(maxlen=ROLLING_SHORT))
    long_buf: deque = field(default_factory=lambda: deque(maxlen=ROLLING_LONG))

    def update(self, p: float) -> float:
        self.frame_count += 1
        self.ewm_mean = (
            p if self.frame_count == 1 else EWM_ALPHA * p + (1 - EWM_ALPHA) * self.ewm_mean
        )
        if len(self.short_buf) == self.short_buf.maxlen:
            self.short_sum -= self.short_buf[0]
        self.short_buf.append(p)
        self.short_sum += p
        if len(self.long_buf) == self.long_buf.maxlen:
            self.long_sum -= self.long_buf[0]
        self.long_buf.append(p)
        self.long_sum += p
        if self.frame_count <= self.warmup:
            return 0.0
        return max(
            self.ewm_mean, self.short_sum / len(self.short_buf) - self.long_sum / len(self.long_buf)
        )


def classify(smoothed: float, warn: float, pause: float) -> str:
    if smoothed >= pause:
        return "pause"
    if smoothed >= warn:
        return "warn"
    return "safe"


def _emit(event: str, printer_id: int, payload: dict) -> None:
    try:
        emit(event, printer_id, payload)
    except Exception:
        log.warning("obico: emit failed for printer %s", printer_id)


def enqueue_event(
    redis_client,
    printer_id: int,
    job_id: int | None,
    score: float,
    smoothed: float,
    action: str,
    jpeg: bytes | None,
) -> None:
    """Stash the frame in Redis (here, in the scorer thread) and hand the DB
    write to Celery (``sensors.record`` pattern). Never raises."""
    try:
        key = None
        if jpeg:
            key = f"obico:evt:{printer_id}:{uuid.uuid4().hex}"
            redis_client.set(key, jpeg, ex=obico.EVENT_FRAME_TTL_S)
        from app.tasks.obico import record_event

        record_event.apply_async(
            args=[printer_id, job_id, score, smoothed, action, key], retry=False
        )
    except Exception as exc:
        log.error("obico: enqueue failed for printer %s: %s", printer_id, type(exc).__name__)


class ObicoWatcher:
    def __init__(
        self,
        printer_id: int,
        redis_client,
        cfg: ObicoConfig,
        pcfg: PrinterDetection,
        job_id: int | None,
        *,
        transport: httpx.BaseTransport | None = None,
        warmup: int = WARMUP_FRAMES,
        recorder: Callable[..., object] = enqueue_event,
        is_running: Callable[[], bool] = lambda: True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.printer_id = printer_id
        self.redis = redis_client
        self.cfg = cfg
        self.pcfg = pcfg
        self.job_id = job_id
        self.transport = transport
        self.recorder = recorder
        self.is_running = is_running
        self._warn_at = 0.0
        self.clock = clock
        self.smoother = Smoother(warmup=warmup)
        self.viewer_id = f"obico-{printer_id}"
        self.queue: queue.Queue[bytes] = queue.Queue(maxsize=1)
        self.errors = 0
        self.paused = False
        self.state = "safe"
        self.fired = False
        self.warn_recorded = False
        self._next_feed = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- supervisor-thread side ---------------------------------------
    def update(self, cfg: ObicoConfig, pcfg: PrinterDetection) -> None:
        self.cfg, self.pcfg = cfg, pcfg

    def feed(self, now: float | None = None) -> bool:
        """Heartbeat the viewer registration (keeps the camera alive) and, at
        most once per ``interval_s``, offer the latest frame to the 1-frame
        queue. Returns True when a frame was queued."""
        now = self.clock() if now is None else now
        camera_state.add_viewer(self.redis, self.printer_id, self.viewer_id)
        if self.paused or now < self._next_feed or not self.queue.empty():
            return False
        jpeg = camera_state.get_frame(self.redis, self.printer_id)
        if not jpeg:
            return False
        self._next_feed = now + self.cfg.interval_s
        self.queue.put_nowait(jpeg)
        return True

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=f"obico-{self.printer_id}"
        )
        self._thread.start()

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        self._stop.set()
        with contextlib.suppress(Exception):
            camera_state.remove_viewer(self.redis, self.printer_id, self.viewer_id)
        if self._thread is not None:
            self._thread.join(JOIN_TIMEOUT_S)

    # -- scorer-thread side -------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set() and not self.paused:
            try:
                jpeg = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.process(jpeg)
            except Exception:
                log.exception("obico: process failed for printer %s", self.printer_id)

    def _publish(self, score: float | None, smoothed: float | None) -> None:
        body = {
            "state": self.state,
            "score": score,
            "smoothed": smoothed,
            "paused": self.paused,
            "job_id": self.job_id,
            "ts": time.time(),
        }
        with contextlib.suppress(Exception):
            self.redis.set(
                obico.status_key(self.printer_id),
                json.dumps(body),
                **({} if self.paused else {"ex": obico.STATUS_TTL_S}),
            )

    def process(self, jpeg: bytes) -> None:
        try:
            current = score_frame(self.redis, self.cfg, jpeg, transport=self.transport)
        except MlError as exc:
            if self._stop.is_set():
                return
            self.errors += 1
            log.warning("obico: printer %s ML error (%s) #%d", self.printer_id, exc, self.errors)
            if self.errors >= MAX_ERRORS:
                self.paused = True
                self._publish(None, None)
            return
        if self._stop.is_set():
            return  # a slow ML call returned after stop(): drop it
        self.errors = 0
        smoothed = self.smoother.update(current)
        new_state = classify(smoothed, self.cfg.warn, self.cfg.pause)
        self.state = new_state
        self._publish(current, smoothed)
        if (
            self.warn_recorded
            and smoothed < self.cfg.warn - WARN_HYSTERESIS
            and self.clock() - self._warn_at >= WARN_COOLDOWN_S
        ):
            self.warn_recorded = False  # re-arm: dipped well below warn for 5+ min
        if new_state == "warn" and not self.warn_recorded and not self.fired:
            if self._stop.is_set() or not self.is_running():
                return
            self.warn_recorded = True
            self._warn_at = self.clock()
            self.recorder(self.redis, self.printer_id, self.job_id, current, smoothed, "warn", jpeg)
        elif new_state == "pause" and not self.fired:
            if self._stop.is_set() or not self.is_running():
                return
            self.fired = True
            action = obico.effective_action(self.cfg, self.pcfg)
            self.recorder(self.redis, self.printer_id, self.job_id, current, smoothed, action, jpeg)
            if action == "pause":
                self.redis.publish(
                    command_channel(self.printer_id),
                    json.dumps(
                        {"command": "pause", "args": {}, "request_id": f"obico:{uuid.uuid4().hex}"}
                    ),
                )
            if action in ("notify", "pause"):
                _emit(
                    "failure_suspected",
                    self.printer_id,
                    {"score": round(smoothed, 3), "action": action, "job_id": self.job_id},
                )


class ObicoSupervisor:
    """Starts/stops one ``ObicoWatcher`` per printer from its own tick thread.
    ``add(printer_id, is_running)`` registers a callable reporting whether the
    printer's gcode_state is RUNNING."""

    def __init__(
        self,
        redis_client,
        settings: Settings,
        *,
        clock: Callable[[], float] = time.monotonic,
        transport: httpx.BaseTransport | None = None,
        warmup: int = WARMUP_FRAMES,
    ) -> None:
        self.redis = redis_client
        self.settings = settings
        self.clock = clock
        self.transport = transport
        self.warmup = warmup
        self._probes: dict[int, Callable[[], bool]] = {}
        self._watchers: dict[int, ObicoWatcher] = {}
        self._disabled: set[int] = set()  # auto-disabled until the job ends
        self._cfg: tuple[ObicoConfig, dict[int, PrinterDetection]] | None = None
        self._cfg_at = -1e9
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def add(self, printer_id: int, is_running: Callable[[], bool]) -> None:
        self._probes[printer_id] = is_running

    def remove(self, printer_id: int) -> None:
        self._probes.pop(printer_id, None)

    def _load(self) -> tuple[ObicoConfig, dict[int, PrinterDetection]]:
        now = self.clock()
        if self._cfg is None or now - self._cfg_at >= CONFIG_TTL_S:
            with base.sync_session() as session:
                self._cfg = (
                    obico.get_config_sync(session, self.settings),
                    obico.printer_configs_sync(session),
                )
            self._cfg_at = now
        return self._cfg

    def _active_job_id(self, printer_id: int) -> int | None:
        with base.sync_session() as session:
            return session.execute(
                select(PrintJob.id)
                .where(
                    PrintJob.printer_id == printer_id,
                    PrintJob.state.notin_([s.value for s in _TERMINAL]),
                )
                .order_by(PrintJob.id.desc())
            ).scalar()

    def _drop(self, printer_id: int, *, clear_status: bool) -> None:
        w = self._watchers.pop(printer_id, None)
        if w is not None:
            w.stop()
        if clear_status:
            with contextlib.suppress(Exception):
                self.redis.delete(obico.status_key(printer_id))

    def _tick_one(
        self, printer_id: int, cfg: ObicoConfig, pcs: dict[int, PrinterDetection]
    ) -> None:
        probe = self._probes.get(printer_id)
        pc = pcs.get(printer_id, PrinterDetection())
        running = bool(probe and probe())
        if not running:
            self._disabled.discard(printer_id)
        want = cfg.enabled and bool(cfg.url) and pc.enabled and running
        w = self._watchers.get(printer_id)
        if w is not None and w.paused:
            self._disabled.add(printer_id)
            w.stop()
            self._watchers.pop(printer_id, None)  # status keeps "paused" until the job ends
            w = None
        if not want:
            self._disabled.discard(printer_id)  # toggling detection off/on re-arms it
            if w is not None or not running or printer_id in self._disabled:
                self._drop(printer_id, clear_status=True)
            return
        if printer_id in self._disabled:
            return
        if w is None:
            w = ObicoWatcher(
                printer_id,
                self.redis,
                cfg,
                pc,
                self._active_job_id(printer_id),
                transport=self.transport,
                warmup=self.warmup,
                clock=self.clock,
                is_running=lambda: bool(probe and probe()),
            )
            w.start()
            self._watchers[printer_id] = w
        else:
            w.update(cfg, pc)
        w.feed()

    def tick(self) -> None:
        try:
            cfg, pcs = self._load()
        except Exception as exc:
            log.error("obico: config load failed: %s", type(exc).__name__)
            return
        for printer_id in set(self._probes) | set(self._watchers):
            try:
                if printer_id not in self._probes:
                    self._drop(printer_id, clear_status=True)
                    continue
                self._tick_one(printer_id, cfg, pcs)
            except Exception:
                log.exception("obico: tick failed for printer %s", printer_id)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="obico-supervisor")
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(TICK_INTERVAL_S):
            try:
                self.tick()
            except Exception:
                log.exception("obico: tick loop failed")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(JOIN_TIMEOUT_S)
        for printer_id in list(self._watchers):
            self._drop(printer_id, clear_status=False)
