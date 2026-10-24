"""printerd: long-lived Bambu MQTT supervisor (SPEC "Architecture"). Holds one
bambulabs_api client session per enabled printer (the lib owns the MQTT
stream + incremental merge); POLLS the adapter's normalized snapshot on a
short interval into Redis printer:{id}:state; transitions the active
print_jobs row + publishes a coarse print_job.updated SSE event; subscribes
a Redis command channel for pause/resume/stop. NOT a Celery task -- a plain
long-lived process (compose service, always-on -- Round 10 T3 retired the
`printer` compose profile) reusing app.tasks.base.sync_session().

Round 10 T3: the daemon itself is no longer gated on the `printer_enabled`
flag at process start -- it always runs. The flag (DB-backed `AppConfig`,
`PUT /settings/app`) is instead read LIVE, once per `reconcile()` tick, in
`enabled_printers()` below: off means every running worker tears down within
one `_POLL_INTERVAL_S` cycle (the same presence-diff teardown a disabled
`Printer` row already drives); back on means every enabled printer is picked
back up on the very next tick. No process restart, no compose profile,
required either way."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import signal
import threading
import time
from datetime import UTC, datetime

import redis
from sqlalchemy import select

from app.config import Settings, get_settings
from app.logging_config import LevelPoller, configure_logging
from app.models import Printer, PrintJob
from app.models.enums import ArchiveResult, PrintJobOrigin, PrintJobState
from app.printers import commands as cmds
from app.printers.base import (
    CMD_RESULT_TTL_S,
    PrinterAdapter,
    PrinterPublicState,
    cmd_result_key,
    command_channel,
    state_key,
)
from app.printers.camera_supervisor import CameraSupervisor
from app.printers.connection import connection_from_printer
from app.printers.devmode import DevModeTracker, is_verify_failed_reply
from app.printers.ha_bridge import HaBridge
from app.printers.obico_watcher import ObicoSupervisor
from app.printers.registry import build_adapter
from app.services import camera_state, ha_state, hms, spool_inventory
from app.services.app_config import get_app_config_sync
from app.services.events import publish_print_job_event_sync
from app.services.notify.events import emit_bg
from app.services.server_config import seed_all_sync_safe
from app.tasks import base

log = logging.getLogger("printerd")
_TERMINAL = {PrintJobState.FINISHED, PrintJobState.FAILED, PrintJobState.CANCELED}
# The lib keeps its own state fresh from the MQTT stream; request_full_status()
# is a cheap LOCAL read of the lib's accessors, so poll it every few seconds.
_POLL_INTERVAL_S = 2.5
# Q8b: sensor-history sampling cadence (no extra printer polling).
SENSOR_INTERVAL_S = 60.0
# Bambu print_error code the printer reports when a print is cancelled
# (0x0300400C). Pending live verification (P2 Task 12).
CANCEL_ERROR_CODES: frozenset[int] = frozenset({50348044})
# How long a command waits for the printer's MQTT reply (adapters that emit
# replies only). Must stay well under the API's 3 s wait.
REPLY_WAIT_S = 1.5


def terminal_result(
    state: PrintJobState, print_error: int | None, stop_requested: bool
) -> ArchiveResult:
    """Classify a terminal transition for the archive."""
    if state == PrintJobState.FINISHED:
        return ArchiveResult.COMPLETED
    if stop_requested:
        return ArchiveResult.STOPPED
    if print_error in CANCEL_ERROR_CODES:
        return ArchiveResult.CANCELED
    return ArchiveResult.FAILED


def hms_payload(code: str) -> dict:
    """Notification payload for a 16-hex HMS code, decoded via the HMS catalog."""
    pretty = "_".join(code[i : i + 4] for i in range(0, 16, 4))
    out: dict = {"code": pretty, "raw_code": code}
    entry = hms.decode_hms(int(code[:8], 16), int(code[8:], 16))
    if entry.get("description"):
        out["message"] = entry["description"]
    out["severity"] = entry["severity"]
    out["module"] = entry["module"]
    return out


def _enqueue_archive(print_job_id: int, result: ArchiveResult) -> None:
    from app.tasks.archive import archive_print

    archive_print.apply_async(args=[print_job_id, result.value])


def _sensor_snapshot(public: PrinterPublicState) -> dict:
    return {
        "ts": datetime.now(UTC).isoformat(),
        "nozzle": public.nozzle_temper,
        "nozzle_target": public.nozzle_target,
        "bed": public.bed_temper,
        "bed_target": public.bed_target,
        "chamber": public.chamber_temper,
        "fan_part": public.fan_part_pct,
        "ams": [
            {"id": u.get("id"), "humidity": u.get("humidity"), "temp": u.get("temp")}
            for u in public.ams_units
            if isinstance(u, dict)
        ],
    }


def _enqueue_sensor_sample(printer_id: int, snapshot: dict) -> None:
    """Runs on a throwaway daemon thread -- a slow/unreachable broker must
    never stall the poll loop or an MQTT callback."""
    try:
        from app.tasks.sensors import record

        record.apply_async(args=[printer_id, snapshot], retry=False)
    except Exception as exc:
        log.error("printerd: sensor enqueue failed: %s", type(exc).__name__)


def _tray_pcts(trays: list[dict]) -> list[int | None]:
    """A ``remain_pct``-by-global-tray positional snapshot --
    ``spool_inventory.record_job_usage``'s ``ams_delta`` fallback indexes
    straight into this list by global tray id (``spool_for_tray`` takes the
    same int), so it must stay positional rather than keyed."""
    if not trays:
        return []
    size = max((t.get("slot") or 0) for t in trays) + 1
    out: list[int | None] = [None] * size
    for t in trays:
        slot = t.get("slot")
        if slot is not None and 0 <= slot < size:
            out[slot] = t.get("remain_pct")
    return out


def _signature(printer: Printer) -> tuple[str, str, str]:
    """The subset of a ``Printer`` row that a running worker's MQTT session
    is actually built from (Round 8 T2) -- ``connection_from_printer``'s
    exact inputs, minus ``kind``/``model``/``options`` (unused by the one
    adapter today) and minus anything cosmetic (``name``). Changing any of
    these three columns invalidates the worker's live session; reconcile()
    diffs this against the signature captured when the worker was started to
    decide whether a restart is warranted -- a rename or model-label edit
    must NOT bounce an otherwise-healthy MQTT connection."""
    return (printer.host, printer.serial, printer.access_code_enc)


class PrinterWorker:
    def __init__(
        self,
        settings: Settings,
        printer_id: int,
        adapter: PrinterAdapter,
        redis_client: redis.Redis,
    ) -> None:
        self.settings = settings
        self.printer_id = printer_id
        self.adapter = adapter
        self.redis = redis_client
        self._merged: dict | None = None
        self._stop_requested = False
        self._tray_sig: tuple | None = None
        self.devmode = DevModeTracker()
        self._public: PrinterPublicState | None = None
        self._reply_evt = threading.Event()
        self._last_reply: dict | None = None
        adapter.set_reply_handler(self.handle_reply)

        self._hms_seen: set[str] | None = None
        self._dev_mode_prev: bool | None = None

        self._sensor_next = 0.0
        self._sensor_last_report: str | None = None
        self._sensor_thread: threading.Thread | None = None

    def sensor_tick(self, now: float | None = None) -> bool:
        """Q8b: at most once per ``SENSOR_INTERVAL_S``, hand the in-memory
        snapshot to a Celery task (no DB here). Skips when the printer never
        reported or ``last_report_at`` hasn't advanced (offline/stale).
        Returns True when a sample was dispatched."""
        now = time.monotonic() if now is None else now
        if now < self._sensor_next:
            return False
        if self._sensor_thread is not None and self._sensor_thread.is_alive():
            return False  # previous enqueue still stuck (hung broker): don't pile up
        self._sensor_next = now + SENSOR_INTERVAL_S
        public = self._public
        if public is None or not public.last_report_at:
            return False
        if public.last_report_at == self._sensor_last_report:
            return False
        self._sensor_last_report = public.last_report_at
        t = threading.Thread(
            target=_enqueue_sensor_sample,
            args=(self.printer_id, _sensor_snapshot(public)),
            daemon=True,
            name=f"sensor-{self.printer_id}",
        )
        t.start()
        self._sensor_thread = t
        return True

    def is_running(self) -> bool:
        return (self._merged or {}).get("gcode_state") == "RUNNING"

    def _write_public(self, public: PrinterPublicState) -> None:
        public.dev_mode = self.devmode.state
        public.dev_mode_reason = self.devmode.reason
        self._notify_changes(public)
        self._public = public
        self.redis.set(state_key(self.printer_id), json.dumps(dataclasses.asdict(public)))

    def _notify_changes(self, public: PrinterPublicState) -> None:
        """Q6: dev mode flipped off, and HMS codes not in the previous report
        (the first report only seeds the baseline). Never raises."""
        try:
            if self._dev_mode_prev is True and public.dev_mode is False:
                emit_bg("dev_mode_off", self.printer_id, {"reason": public.dev_mode_reason})
            if public.dev_mode is not None:
                self._dev_mode_prev = public.dev_mode
            codes = {
                f"{int(e['attr']):08X}{int(e['code']):08X}"
                for e in public.hms or []
                if isinstance(e, dict) and "attr" in e and "code" in e
            }
            seen = self._hms_seen
            self._hms_seen = codes
            if seen is None:
                return
            for code in sorted(codes - seen):
                emit_bg("hms_error", self.printer_id, hms_payload(code))
        except Exception as exc:
            log.warning("printerd: notify diff failed: %s", type(exc).__name__)

    def handle_reply(self, reply: dict) -> None:
        """A printer command reply (adapter's MQTT thread): feeds lazy
        Developer-Mode detection and wakes a command waiting on it."""
        self.devmode.on_reply(reply, time.monotonic())
        self._last_reply = reply
        self._reply_evt.set()
        if self._public is not None:
            with contextlib.suppress(Exception):
                self._write_public(self._public)

    def handle_report(self, report: dict) -> None:
        self._merged = self.adapter.merge_report(self._merged, report)
        probe = self.devmode.on_report(self._merged, time.monotonic())
        if probe is not None:
            try:
                self.adapter.send_devmode_probe(probe.slot, probe.seq)
            except Exception as exc:  # NotImplementedError / publish failure: stay unknown
                log.warning("printerd: dev-mode probe not sent: %s", type(exc).__name__)
        public = self.adapter.public_state(self._merged)
        self._write_public(public)
        self._auto_assign_on_tray_change(public)
        self._transition_active_job(public)

    def _auto_assign_on_tray_change(self, public: PrinterPublicState) -> None:
        """RFID auto-assign (P5 spool tray plan): only hits the DB when the
        tray signature (global slot, RFID uuid, material) actually changed
        since the last report -- a printer polled every few seconds would
        otherwise open a session on every tick for nothing."""
        sig = tuple((t.get("slot"), t.get("tray_uuid"), t.get("tray_type")) for t in public.trays)
        if sig == self._tray_sig:
            return
        self._tray_sig = sig
        # spool_inventory.auto_assign_from_trays takes the adapter's tray
        # dicts as-is -- its "slot" key is already the GLOBAL tray id.
        try:
            with base.sync_session() as session:
                spool_inventory.auto_assign_from_trays(session, self.printer_id, public.trays)
        except Exception:
            log.exception("printerd: RFID auto-assign failed for printer %s", self.printer_id)

    def _active_job(self, session) -> PrintJob | None:
        return (
            session.execute(
                select(PrintJob)
                .where(
                    PrintJob.printer_id == self.printer_id,
                    PrintJob.state.notin_([s.value for s in _TERMINAL]),
                )
                .order_by(PrintJob.id.desc())
            )
            .scalars()
            .first()
        )

    def _transition_active_job(self, public: PrinterPublicState) -> None:
        new_state = self.adapter.job_state(public)
        if new_state is None:
            return
        with base.sync_session() as session:
            job = self._active_job(session)
            if job is None:
                if new_state != PrintJobState.PRINTING:
                    return
                self._stop_requested = False
                energy_start_kwh = None
                with contextlib.suppress(Exception):
                    energy_start_kwh = ha_state.energy_acc(self.redis, self.printer_id)
                job = PrintJob(
                    printer_id=self.printer_id,
                    file_id=None,
                    origin=PrintJobOrigin.EXTERNAL,
                    created_by_user_id=None,
                    state=PrintJobState.PRINTING,
                    started_at=datetime.now(UTC),
                    subtask_name=public.subtask_name,
                    layer=public.layer_num,
                    total_layers=public.total_layer_num,
                    progress_pct=public.mc_percent,
                    remaining_min=public.mc_remaining_time,
                    raw_status=self._merged,
                    tray_snapshot={"start": _tray_pcts(public.trays)},
                    energy_start_kwh=energy_start_kwh,
                )
                session.add(job)
                session.commit()
                publish_print_job_event_sync(
                    self.settings.redis_url,
                    print_job_id=job.id,
                    printer_id=self.printer_id,
                    state=PrintJobState.PRINTING.value,
                )
                emit_bg(
                    "job_started",
                    self.printer_id,
                    {"job_id": job.id, "subtask_name": public.subtask_name},
                )
                return
            if job.state == new_state.value:
                return
            if new_state in (PrintJobState.FINISHED, PrintJobState.FAILED):
                # T3: the plate needs clearing -- set before the CANCELED remap below.
                printer_row = session.get(Printer, self.printer_id)
                if printer_row is not None:
                    printer_row.awaiting_plate_clear = True
            prev_state = job.state
            result: ArchiveResult | None = None
            if new_state in _TERMINAL:
                result = terminal_result(new_state, public.print_error, self._stop_requested)
                if result in (ArchiveResult.CANCELED, ArchiveResult.STOPPED):
                    new_state = PrintJobState.CANCELED
                self._stop_requested = False
            if new_state == PrintJobState.PRINTING:
                self._stop_requested = False
            job.state = new_state.value
            job.progress_pct = public.mc_percent
            job.remaining_min = public.mc_remaining_time
            job.layer = public.layer_num
            job.total_layers = public.total_layer_num
            job.printer_error = str(public.print_error) if public.print_error else None
            job.raw_status = self._merged
            now = datetime.now(UTC)
            if new_state == PrintJobState.PRINTING and job.started_at is None:
                job.started_at = now
                with contextlib.suppress(Exception):
                    job.energy_start_kwh = ha_state.energy_acc(self.redis, self.printer_id)
            if new_state == PrintJobState.PRINTING and job.tray_snapshot is None:
                job.tray_snapshot = {"start": _tray_pcts(public.trays)}
            if new_state in _TERMINAL:
                job.finished_at = now
                with contextlib.suppress(Exception):
                    job.energy_end_kwh = ha_state.energy_acc(self.redis, self.printer_id)
                job.tray_snapshot = {
                    **(job.tray_snapshot or {}),
                    "end": _tray_pcts(public.trays),
                }
            job_id = job.id
            subtask = job.subtask_name or public.subtask_name
            session.commit()
        publish_print_job_event_sync(
            self.settings.redis_url,
            print_job_id=job_id,
            printer_id=self.printer_id,
            state=new_state.value,
        )
        info = {"job_id": job_id, "subtask_name": subtask}
        if new_state == PrintJobState.PRINTING and prev_state != PrintJobState.PAUSED.value:
            emit_bg("job_started", self.printer_id, info)
        elif new_state == PrintJobState.PAUSED:
            emit_bg("job_paused", self.printer_id, info)
        if result is not None:
            with contextlib.suppress(Exception):
                camera_state.keep_job_frame(self.redis, self.printer_id, job_id)
            if result == ArchiveResult.COMPLETED:
                emit_bg("job_finished", self.printer_id, info)
            elif result in (ArchiveResult.FAILED, ArchiveResult.CANCELED):
                emit_bg(
                    "job_failed",
                    self.printer_id,
                    {**info, "error": str(public.print_error) if public.print_error else None},
                )
            try:
                _enqueue_archive(job_id, result)
            except Exception as exc:
                log.error("printerd: archive enqueue failed: %s", type(exc).__name__)

    def handle_command(
        self, command: str, args: dict | None = None, request_id: str | None = None
    ) -> dict:
        """Run one command from the dispatch table and (when the caller sent
        a ``request_id``) publish the result to ``cmd_result_key``."""
        result = self._execute(command, args or {})
        if request_id:
            with contextlib.suppress(Exception):
                self.redis.set(
                    cmd_result_key(self.printer_id, request_id),
                    json.dumps(result),
                    ex=CMD_RESULT_TTL_S,
                )
        return result

    def _execute(self, command: str, args: dict) -> dict:
        spec = cmds.DISPATCH.get(command)
        if spec is None:
            log.warning("printerd: unknown command %r for printer %s", command, self.printer_id)
            return {"ok": False, "code": "unsupported", "message": f"unknown command {command}"}
        try:
            clean = spec.validate(args)
            merged = self._merged or {}
            printing = merged.get("gcode_state") in cmds.PRINTING_STATES
            if spec.refuse_printing and printing:
                raise cmds.CommandError("printing", f"{command} refused while printing")
            if spec.needs_printing and not printing:
                raise cmds.CommandError("not_printing", f"{command} requires an active print")
            if spec.cold_check:
                nozzle = merged.get("nozzle_temper")
                if not isinstance(nozzle, int | float) or nozzle < cmds.COLD_EXTRUDE_C:
                    raise cmds.CommandError("cold_extrude", "nozzle below 170 C")
            if command == "stop":
                self._stop_requested = True
            self._reply_evt.clear()
            self._last_reply = None
            spec.run(self.adapter, clean)
            if self.adapter.emits_replies:
                self._reply_evt.wait(REPLY_WAIT_S)
            if is_verify_failed_reply(self._last_reply):
                return {
                    "ok": False,
                    "code": "dev_mode_off",
                    "message": str((self._last_reply or {}).get("reason", "")),
                }
            return {"ok": True}
        except cmds.CommandError as e:
            return {"ok": False, "code": e.code, "message": e.message}
        except NotImplementedError:
            return {"ok": False, "code": "unsupported", "message": f"{command} not supported"}
        except ValueError as e:
            return {"ok": False, "code": "invalid", "message": str(e)}
        except Exception as e:  # noqa: BLE001 -- type only: text could echo the access code
            log.error("printerd: command %r failed: %s", command, type(e).__name__)
            return {"ok": False, "code": "error", "message": type(e).__name__}


class PrinterDaemon:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.redis = redis.Redis.from_url(settings.redis_url)
        self._workers: dict[int, PrinterWorker] = {}
        self._pubsubs: dict[int, redis.client.PubSub] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._cmd_stops: dict[int, threading.Event] = {}
        # The connection signature (host/serial/access_code_enc) each running
        # worker was started with (Round 8 T2) -- reconcile() diffs this
        # against the printer row's current signature every tick to catch a
        # connection-settings edit that a presence-only diff would otherwise
        # miss (the printer stays enabled the whole time, so it never leaves
        # ``_workers``).
        self._signatures: dict[int, tuple[str, str, str]] = {}
        self._stop = threading.Event()
        self.camera = CameraSupervisor(self.redis)
        self.obico = ObicoSupervisor(self.redis, settings)
        self.ha = HaBridge(self.redis, self.settings, active_ids=lambda: set(self._workers))

    def enabled_printers(self) -> list[Printer]:
        """Every `enabled` Printer row -- but only when the `printer_enabled`
        flag is on (Round 10 T3: read live off the DB-backed `AppConfig`,
        same session, on every call); flag off returns `[]` unconditionally,
        so `reconcile()`'s ordinary presence diff tears every running worker
        down exactly as it would for a disabled `Printer` row, and no
        adapter is ever built -- `bambulabs_api`/`paho` stay unimported."""
        with base.sync_session() as session:
            config = get_app_config_sync(session, self.settings)
            if not config.printer_enabled:
                return []
            return list(session.execute(select(Printer).where(Printer.enabled.is_(True))).scalars())

    def start_printer(self, printer: Printer) -> PrinterWorker:
        conn = connection_from_printer(self.settings, printer)
        adapter = build_adapter(printer.kind, conn)
        worker = PrinterWorker(self.settings, printer.id, adapter, self.redis)
        adapter.set_report_handler(worker.handle_report)
        adapter.connect()
        adapter.request_full_status()
        self._workers[printer.id] = worker
        self._signatures[printer.id] = _signature(printer)
        self._subscribe_commands(printer.id, worker)
        self.camera.add(printer.id, adapter)
        self.obico.add(printer.id, worker.is_running)
        return worker

    def _safe_start(self, printer: Printer) -> None:
        """``start_printer``, swallowing a failure so one bad printer can't
        stall ``reconcile()`` for the rest. Exception TEXT could echo the
        plaintext access code (e.g. an MQTT/FTPS auth-failure string) -- log
        only the type, never the full exception body."""
        try:
            self.start_printer(printer)
        except Exception as exc:
            log.error("printerd: failed to start printer %s: %s", printer.id, type(exc).__name__)

    def _subscribe_commands(self, printer_id: int, worker: PrinterWorker) -> None:
        pubsub = self.redis.pubsub()
        pubsub.subscribe(command_channel(printer_id))
        stop = threading.Event()

        def _loop() -> None:
            try:
                for msg in pubsub.listen():
                    if self._stop.is_set() or stop.is_set():
                        break
                    if msg["type"] != "message":
                        continue
                    try:
                        body = json.loads(msg["data"])
                        command = body.get("command")
                        args = body.get("args")
                        request_id = body.get("request_id")
                    except (ValueError, TypeError, AttributeError):
                        continue
                    if command:
                        try:
                            worker.handle_command(
                                command, args if isinstance(args, dict) else None, request_id
                            )
                        except Exception:
                            log.exception("printerd: command %r failed", command)
            except Exception:
                # pubsub.close() from stop_printer/stop() unblocks listen() by
                # dropping the connection -> exit the thread instead of leaking it.
                pass
            finally:
                with contextlib.suppress(Exception):
                    pubsub.close()

        t = threading.Thread(target=_loop, daemon=True, name=f"cmd-{printer_id}")
        t.start()
        self._pubsubs[printer_id] = pubsub
        self._threads[printer_id] = t
        self._cmd_stops[printer_id] = stop

    def stop_printer(self, printer_id: int) -> None:
        self._signatures.pop(printer_id, None)
        self.camera.remove(printer_id)
        self.obico.remove(printer_id)
        stop = self._cmd_stops.pop(printer_id, None)
        if stop is not None:
            stop.set()
        pubsub = self._pubsubs.pop(printer_id, None)
        if pubsub is not None:
            with contextlib.suppress(Exception):
                pubsub.unsubscribe()
                pubsub.close()  # unblocks the listen() loop
        worker = self._workers.pop(printer_id, None)
        if worker is not None:
            with contextlib.suppress(Exception):
                worker.adapter.close()
        thread = self._threads.pop(printer_id, None)
        if thread is not None:
            thread.join(timeout=2.0)

    def reconcile(self) -> None:
        enabled = {p.id: p for p in self.enabled_printers()}
        for printer_id in list(self._workers):
            if printer_id not in enabled:
                self.stop_printer(printer_id)
        for printer_id, printer in enabled.items():
            if printer_id not in self._workers:
                self._safe_start(printer)
            elif self._signatures.get(printer_id) != _signature(printer):
                # host/serial/access_code changed under a still-enabled
                # printer (Round 8 T2) -- the running worker's MQTT session
                # was built from the OLD values, so it must be torn down and
                # rebuilt rather than left connected to the wrong printer (or
                # authenticating with a stale code). A name/model-only edit
                # leaves the signature unchanged and never reaches here.
                self.stop_printer(printer_id)
                self._safe_start(printer)

    def run(self) -> None:
        self.reconcile()  # initial start (replaces the old one-shot start loop)
        self.camera.start()
        self.obico.start()
        self.ha.start()
        level_poller = LevelPoller(ttl_s=30)
        while not self._stop.wait(_POLL_INTERVAL_S):
            level_poller.maybe_apply_sync(base.sync_session)
            try:
                self.reconcile()
            except Exception as exc:
                # I1: reconcile() now also does a Setting read (enabled_printers())
                # on top of its existing Printer select -- a transient DB blip
                # must not propagate out of run() (that would exit main() and
                # let `restart: unless-stopped` tear down every live printer's
                # MQTT session over a momentary hiccup). Same access-code leak
                # concern as the poll-failure log below -- type only, never the
                # full exception body. Existing workers are left running as-is;
                # the next tick re-reconciles.
                log.error("printerd: reconcile failed: %s", type(exc).__name__)
            for worker in list(self._workers.values()):
                with contextlib.suppress(Exception):
                    worker.sensor_tick()
                try:
                    # emit a fresh lib snapshot -> Redis + transitions
                    worker.adapter.request_full_status()
                except Exception as exc:
                    # Same access-code leak concern as the start-failure log
                    # above -- type only, never the full exception body.
                    log.error("printerd: status poll failed: %s", type(exc).__name__)

    def stop(self) -> None:
        self._stop.set()
        for printer_id in list(self._workers):
            self.stop_printer(printer_id)
        self.camera.stop()
        self.obico.stop()
        self.ha.stop()


def main() -> None:
    """Always constructs and runs the daemon (Round 10 T3) -- `printer_enabled`
    is no longer checked here at process start; it's read live, every
    `reconcile()` tick, in `enabled_printers()` above. With the flag off the
    daemon simply runs with zero workers instead of idling via
    `signal.pause()`, and picks printers up the moment the flag flips on
    without a restart."""
    configure_logging()
    seed_all_sync_safe()
    settings = get_settings()
    daemon = PrinterDaemon(settings)
    signal.signal(signal.SIGTERM, lambda *_: daemon.stop())
    signal.signal(signal.SIGINT, lambda *_: daemon.stop())
    daemon.run()


if __name__ == "__main__":
    main()
