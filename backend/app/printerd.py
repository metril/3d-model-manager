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
from datetime import UTC, datetime

import redis
from sqlalchemy import select

from app.config import Settings, get_settings
from app.models import File, Model, Printer, PrintJob, Revision
from app.models.enums import PrintJobState
from app.printers.base import PrinterAdapter, PrinterPublicState, command_channel, state_key
from app.printers.connection import connection_from_printer
from app.printers.registry import build_adapter
from app.services.app_config import get_app_config_sync
from app.services.events import publish_print_job_event_sync
from app.tasks import base

log = logging.getLogger("printerd")
_TERMINAL = {PrintJobState.FINISHED, PrintJobState.FAILED, PrintJobState.CANCELED}
# The lib keeps its own state fresh from the MQTT stream; request_full_status()
# is a cheap LOCAL read of the lib's accessors, so poll it every few seconds.
_POLL_INTERVAL_S = 2.5


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

    def handle_report(self, report: dict) -> None:
        self._merged = self.adapter.merge_report(self._merged, report)
        public = self.adapter.public_state(self._merged)
        self.redis.set(state_key(self.printer_id), json.dumps(dataclasses.asdict(public)))
        self._transition_active_job(public)

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
        created = False
        with base.sync_session() as session:
            job = self._active_job(session)
            if job is None:
                active_states = (
                    PrintJobState.PRINTING,
                    PrintJobState.STARTING,
                    PrintJobState.PAUSED,
                )
                if new_state in active_states:
                    created = True
                    matched_file = None
                    if public.subtask_name:
                        matched_file = (
                            session.execute(
                                select(File)
                                .where(
                                    (File.rel_path == public.subtask_name)
                                    | File.storage_path.endswith(
                                        "/" + public.subtask_name, autoescape=True
                                    )
                                )
                                .order_by(File.id.desc())
                            )
                            .scalars()
                            .first()
                        )
                    # No match -> no job: never credit an unrelated library file.

                    if matched_file is not None:
                        job = PrintJob(
                            printer_id=self.printer_id,
                            file_id=matched_file.id,
                            subtask_name=public.subtask_name or matched_file.rel_path,
                            state=new_state.value,
                        )
                        session.add(job)
                        session.flush()
                else:
                    return

            # A job created above already carries ``new_state``; it must still
            # fall through so its fields are populated and the session commits.
            if job is None or (not created and job.state == new_state.value):
                return
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
                file = session.get(File, job.file_id)
                if file and file.revision_id:
                    rev = session.get(Revision, file.revision_id)
                    if rev and rev.model_id:
                        m = session.get(Model, rev.model_id)
                        if m and (m.print_status is None or m.print_status in ("idle", "to_print")):
                            m.print_status = "printing"
            if new_state in _TERMINAL:
                job.finished_at = now
            if new_state == PrintJobState.FINISHED:
                from app.services.camera_snapshot import capture_and_save_finish_snapshot

                capture_and_save_finish_snapshot(self.settings, session, job, self.adapter)

                file = session.get(File, job.file_id)
                if file and file.revision_id:
                    rev = session.get(Revision, file.revision_id)
                    if rev and rev.model_id:
                        m = session.get(Model, rev.model_id)
                        if m:
                            m.quantity_printed += 1
                            if m.quantity_printed >= m.quantity_target:
                                m.print_status = "printed"
            elif new_state == PrintJobState.FAILED:
                file = session.get(File, job.file_id)
                if file and file.revision_id:
                    rev = session.get(Revision, file.revision_id)
                    if rev and rev.model_id:
                        m = session.get(Model, rev.model_id)
                        if m and m.print_status == "printing":
                            m.print_status = "failed"
            job_id = job.id
            session.commit()
        publish_print_job_event_sync(
            self.settings.redis_url,
            print_job_id=job_id,
            printer_id=self.printer_id,
            state=new_state.value,
        )

    def handle_command(self, command: str) -> None:
        if command == "pause":
            self.adapter.pause()
        elif command == "resume":
            self.adapter.resume()
        elif command == "stop":
            self.adapter.stop()
        elif command in ("toggle_light", "light_on", "light_off"):
            if command == "toggle_light":
                curr = self._merged.get("light_on") if self._merged else None
                next_val = not curr if curr is not None else True
            else:
                next_val = command == "light_on"
            self.adapter.set_light(next_val)
            if self._merged is None:
                self._merged = {}
            self._merged["light_on"] = next_val
            public = self.adapter.public_state(self._merged)
            self.redis.set(state_key(self.printer_id), json.dumps(dataclasses.asdict(public)))
        else:
            log.warning("printerd: unknown command %r for printer %s", command, self.printer_id)


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
                        command = json.loads(msg["data"]).get("command")
                    except (ValueError, TypeError):
                        continue
                    if command:
                        try:
                            worker.handle_command(command)
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
        while not self._stop.wait(_POLL_INTERVAL_S):
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


def main() -> None:
    """Always constructs and runs the daemon (Round 10 T3) -- `printer_enabled`
    is no longer checked here at process start; it's read live, every
    `reconcile()` tick, in `enabled_printers()` above. With the flag off the
    daemon simply runs with zero workers instead of idling via
    `signal.pause()`, and picks printers up the moment the flag flips on
    without a restart."""
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    daemon = PrinterDaemon(settings)
    signal.signal(signal.SIGTERM, lambda *_: daemon.stop())
    signal.signal(signal.SIGINT, lambda *_: daemon.stop())
    daemon.run()


if __name__ == "__main__":
    main()
