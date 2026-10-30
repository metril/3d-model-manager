import json
import logging
import threading
import time

import pytest
import redis as redis_lib

from app import printerd as printerd_module
from app.config import get_settings
from app.crypto import encrypt_secret
from app.models import Blob, File, Model, Printer, PrintJob, Revision, Setting, Spool
from app.models.enums import ArchiveResult, BlobFormat, BlobKind, PrinterKind, PrintJobState
from app.printerd import PrinterDaemon, PrinterWorker
from app.printers.base import PrinterConnection, state_key
from app.printers.fake import FakeCamera, FakePrinterAdapter
from app.services import camera_state as cs
from app.services import spool_inventory
from app.tasks import base
from tests.cassettes import bambu_snapshots as cass

JPEG = b"\xff\xd8\xff\xe0" + bytes(10) + b"\xff\xd9"


def _seed_active_job(printer_id: int) -> int:
    with base.sync_session() as s:
        blob = Blob(hash="a" * 64, size=1, kind=BlobKind.SLICED, format=BlobFormat.GCODE_3MF)
        model = Model(slug="m1", name="M1")
        s.add_all([blob, model])
        s.flush()
        rev = Revision(model_id=model.id, number=1, dir_name="rev-001")
        s.add(rev)
        s.flush()
        f = File(
            revision_id=rev.id,
            blob_hash=blob.hash,
            rel_path="p.gcode.3mf",
            storage_path="m1/rev-001/p.gcode.3mf",
        )
        s.add(f)
        s.flush()
        job = PrintJob(
            printer_id=printer_id, file_id=f.id, state=PrintJobState.STARTING, subtask_name="w"
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        return job.id


@pytest.fixture
def worker(redis_url, printer_enabled, migrated_db):
    settings = get_settings()
    with base.sync_session() as s:
        printer = Printer(
            name="p",
            kind=PrinterKind.BAMBU_LAN,
            host="h",
            serial="S",
            access_code_enc="x",
            enabled=True,
        )
        s.add(printer)
        s.commit()
        s.refresh(printer)
        pid = printer.id
    adapter = FakePrinterAdapter(PrinterConnection(host="h", serial="S", access_code="x"))
    client = redis_lib.Redis.from_url(settings.redis_url)
    return PrinterWorker(settings, pid, adapter, client), adapter, pid, client


def _next_event(pubsub, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
        if msg and msg["type"] == "message":
            return json.loads(msg["data"])
    raise AssertionError("no event published")


def test_handle_report_writes_redis_state(worker):
    w, _adapter, pid, client = worker
    w.handle_report(cass.SNAPSHOT_IDLE)
    state = json.loads(client.get(state_key(pid)))
    assert state["gcode_state"] == "IDLE" and state["mc_percent"] == 0


def test_report_transitions_active_job_and_publishes(worker):
    w, _adapter, pid, client = worker
    job_id = _seed_active_job(pid)
    pubsub = client.pubsub()
    pubsub.subscribe("tdmm:events")
    while pubsub.get_message(timeout=0.1):
        pass
    w.handle_report(cass.SNAPSHOT_IDLE)  # IDLE -> job_state None -> no transition
    w.handle_report(cass.SNAPSHOT_PRINTING)  # RUNNING -> PRINTING
    with base.sync_session() as s:
        job = s.get(PrintJob, job_id)
        assert job.state == PrintJobState.PRINTING.value
        assert job.progress_pct == 55 and job.layer == 66 and job.total_layers == 120
        assert job.started_at is not None
    evt = _next_event(pubsub)
    assert evt == {
        "type": "print_job.updated",
        "print_job_id": job_id,
        "printer_id": pid,
        "state": "printing",
    }


def test_finish_sets_finished_at(worker):
    w, _adapter, pid, _client = worker
    job_id = _seed_active_job(pid)
    w.handle_report(cass.SNAPSHOT_PRINTING)
    w.handle_report(cass.SNAPSHOT_FINISH)
    with base.sync_session() as s:
        job = s.get(PrintJob, job_id)
        assert job.state == PrintJobState.FINISHED.value and job.finished_at is not None


def test_error_report_fails_job(worker):
    w, _adapter, pid, _client = worker
    job_id = _seed_active_job(pid)
    w.handle_report(cass.SNAPSHOT_ERROR)
    with base.sync_session() as s:
        job = s.get(PrintJob, job_id)
        assert job.state == PrintJobState.FAILED.value and job.printer_error == "83935248"


def test_command_dispatch(worker):
    w, adapter, _pid, _client = worker
    w.handle_command("pause")
    w.handle_command("resume")
    w.handle_command("stop")
    assert adapter.paused == 1 and adapter.resumed == 1 and adapter.stopped == 1


def test_hms_command_dispatch(worker):
    w, adapter, _pid, _client = worker
    w.handle_command("hms_action", {"print_error": "0500400E", "action": "STOP_PRINTING"})
    assert adapter.hms_actions == [("0500400E", "STOP_PRINTING", None)]


def test_obico_pause_flows_through_command_bus(worker, redis_url, monkeypatch):
    """Q10: the watcher's pause is a plain bus envelope (request_id obico:*)
    that the daemon's subscriber thread dispatches to the adapter."""
    from app.printers.obico_watcher import ObicoWatcher
    from app.services.obico import ObicoConfig, PrinterDetection

    w, adapter, pid, client = worker
    daemon = PrinterDaemon(get_settings())
    daemon._subscribe_commands(pid, w)
    time.sleep(0.3)
    watcher = ObicoWatcher(
        pid,
        client,
        ObicoConfig(url="http://ml", enabled=True),
        PrinterDetection(enabled=True, action="pause"),
        None,
        warmup=0,
        recorder=lambda *a: 1,
    )
    monkeypatch.setattr("app.printers.obico_watcher.score_frame", lambda *a, **k: 1.0)
    try:
        for _ in range(30):
            watcher.process(JPEG)
        deadline = time.monotonic() + 3
        while adapter.paused == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert adapter.paused == 1
    finally:
        daemon.stop_printer(pid)


def test_daemon_has_obico_supervisor_and_worker_running_probe(worker):
    w, _adapter, pid, _client = worker
    daemon = PrinterDaemon(get_settings())
    assert daemon.obico is not None
    assert w.is_running() is False
    w._merged = {"gcode_state": "RUNNING"}
    assert w.is_running() is True


def _tray(slot, ams_unit, tray, tray_uuid=None, tray_type=None, remain_pct=None):
    return {
        "slot": slot,
        "ams_unit": ams_unit,
        "tray": tray,
        "color": None,
        "material": tray_type,
        "tray_type": tray_type,
        "tray_uuid": tray_uuid,
        "remain_pct": remain_pct,
    }


def _spool(s, *, tray_uuid=None, material_type="PLA") -> int:
    spool = Spool(material_type=material_type, tray_uuid=tray_uuid)
    s.add(spool)
    s.commit()
    return spool.id


def test_rfid_auto_assign_moves_spool(worker):
    w, _adapter, pid, _client = worker
    with base.sync_session() as s:
        rfid_id = _spool(s, tray_uuid="U")
        manual_id = _spool(s, tray_uuid=None)
        spool_inventory.assign_slot(s, pid, 0, 2, rfid_id)  # old location for U
        spool_inventory.assign_slot(s, pid, 0, 3, manual_id)  # untouched

    w.handle_report({"trays": [_tray(0, 0, 0, tray_uuid="U"), _tray(2, 0, 2, tray_uuid=None)]})

    with base.sync_session() as s:
        assert spool_inventory.spool_for_tray(s, pid, 0).id == rfid_id  # moved here
        assert spool_inventory.spool_for_tray(s, pid, 2) is None  # cleared
        assert spool_inventory.spool_for_tray(s, pid, 3).id == manual_id  # untouched


def test_manual_assignment_survives_loaded_non_rfid_tray(worker):
    """Final-review fix wave item 1: a loaded tray (tray_type/material set)
    with no tray_uuid is a manually-assigned non-RFID spool -- it must NOT
    be cleared just because the report carries no UUID for it."""
    w, _adapter, pid, _client = worker
    with base.sync_session() as s:
        manual_id = _spool(s, tray_uuid=None)
        spool_inventory.assign_slot(s, pid, 0, 3, manual_id)

    w.handle_report({"trays": [_tray(3, 0, 3, tray_uuid=None, tray_type="PLA")]})

    with base.sync_session() as s:
        assert spool_inventory.spool_for_tray(s, pid, 3).id == manual_id


def test_mismatched_rfid_spool_clears_stale_assignment(worker):
    """A slot currently assigned to an RFID spool must clear when the
    report's tray_uuid changes to something no registered spool owns yet
    (swap/removal) rather than leaving the stale assignment in place."""
    w, _adapter, pid, _client = worker
    with base.sync_session() as s:
        rfid_id = _spool(s, tray_uuid="OLD")
        spool_inventory.assign_slot(s, pid, 0, 1, rfid_id)

    w.handle_report({"trays": [_tray(1, 0, 1, tray_uuid="UNKNOWN", tray_type="PLA")]})

    with base.sync_session() as s:
        assert spool_inventory.spool_for_tray(s, pid, 1) is None


def test_auto_assign_only_on_signature_change(worker, monkeypatch):
    w, _adapter, pid, _client = worker
    calls = []
    orig = spool_inventory.auto_assign_from_trays

    def _counting(session, printer_id, trays):
        calls.append(1)
        return orig(session, printer_id, trays)

    monkeypatch.setattr(spool_inventory, "auto_assign_from_trays", _counting)
    report = {"trays": [_tray(0, 0, 0, tray_uuid="U")]}
    w.handle_report(report)
    w.handle_report(dict(report))  # identical signature -> no second DB call
    assert len(calls) == 1


def test_tray_snapshot_start_end(worker):
    w, _adapter, pid, _client = worker
    job_id = _seed_active_job(pid)
    start_report = {**cass.SNAPSHOT_PRINTING, "trays": [_tray(0, 0, 0, remain_pct=80)]}
    w.handle_report(start_report)
    with base.sync_session() as s:
        job = s.get(PrintJob, job_id)
        assert job.tray_snapshot == {"start": [80]}

    end_report = {**cass.SNAPSHOT_FINISH, "trays": [_tray(0, 0, 0, remain_pct=55)]}
    w.handle_report(end_report)
    with base.sync_session() as s:
        job = s.get(PrintJob, job_id)
        assert job.tray_snapshot == {"start": [80], "end": [55]}


def test_run_logs_start_failure_type_only(caplog, monkeypatch, redis_url):
    """M4 review Fix A part 2: a start_printer failure's exception TEXT could
    echo the printer's plaintext access code (e.g. an MQTT/FTPS auth-failure
    string) -- printerd's failure log must carry only the exception TYPE,
    never the full exception body."""
    settings = get_settings()
    daemon = PrinterDaemon(settings)

    class _StubPrinterRow:
        id = 999

    monkeypatch.setattr(daemon, "enabled_printers", lambda: [_StubPrinterRow()])

    def _boom(printer):
        raise RuntimeError("mqtt auth failed for access code 12345678")

    monkeypatch.setattr(daemon, "start_printer", _boom)
    daemon._stop.set()  # skip the (real, multi-second-interval) poll loop below

    with caplog.at_level(logging.ERROR, logger="printerd"):
        daemon.run()

    assert "failed to start printer" in caplog.text
    assert "12345678" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_run_logs_poll_failure_type_only(caplog, monkeypatch, redis_url):
    """Same leak-surface concern as above, for the status-poll log line."""
    settings = get_settings()
    daemon = PrinterDaemon(settings)

    class _StubPrinterRow:
        id = 1
        host = "h"
        serial = "S"
        access_code_enc = "x"

    # Task 5: run() now reconciles every tick, diffing enabled_printers()
    # against _workers -- stub printer 1 as still enabled so reconcile()
    # doesn't tear the injected stub worker down before the poll below runs.
    monkeypatch.setattr(daemon, "enabled_printers", lambda: [_StubPrinterRow()])
    monkeypatch.setattr(printerd_module, "_POLL_INTERVAL_S", 0.0)

    class _BoomAdapter:
        def request_full_status(self):
            daemon._stop.set()  # stop the loop after this one poll iteration
            raise RuntimeError("ftps auth failed for access code 87654321")

    class _StubWorker:
        adapter = _BoomAdapter()

    daemon._workers[1] = _StubWorker()
    # Round 8 T2: reconcile() now ALSO diffs each still-enabled printer's
    # connection signature against the one its running worker was started
    # with, to catch a host/serial/access_code edit -- pre-seed a matching
    # signature so this poll-failure test (unrelated to that diff) doesn't
    # trip a restart of the injected stub worker before the poll below runs.
    daemon._signatures[1] = ("h", "S", "x")

    with caplog.at_level(logging.ERROR, logger="printerd"):
        daemon.run()

    assert "status poll failed" in caplog.text
    assert "87654321" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_run_survives_transient_reconcile_failure(caplog, monkeypatch, redis_url):
    """I1 regression: `reconcile()` (which now also does a `Setting` read in
    `enabled_printers()`, on top of its existing `Printer` select) used to be
    unguarded in `run()`'s loop body -- a transient DB error there would
    propagate out of `run()`, exit `main()`, and let `restart: unless-stopped`
    tear every live printer's MQTT session down over a momentary hiccup.
    A single failing tick must be logged (type only, mirroring the
    access-code-safety poll-failure guard) and the loop must keep ticking."""
    settings = get_settings()
    daemon = PrinterDaemon(settings)
    monkeypatch.setattr(printerd_module, "_POLL_INTERVAL_S", 0.0)

    calls = {"n": 0}

    def _flaky_reconcile():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("db blip for access code 13572468")
        if calls["n"] == 3:
            daemon._stop.set()  # stop the loop after the next (successful) tick

    monkeypatch.setattr(daemon, "reconcile", _flaky_reconcile)

    with caplog.at_level(logging.ERROR, logger="printerd"):
        daemon.run()

    assert calls["n"] == 3  # initial start (1) + failing tick (2) + recovered tick (3)
    assert "reconcile failed" in caplog.text
    assert "13572468" not in caplog.text
    assert "RuntimeError" in caplog.text


# ---------------------------------------------------------------------------
# Task 5 (M6 hardening B2): reconciliation loop + clean per-printer thread
# teardown. printerd used to query enabled_printers() exactly once at
# startup, so a printer enabled after start was invisible until a process
# restart, and a disabled printer kept its MQTT session + command thread
# alive forever. These drive the real run() loop (in a background thread)
# against real Postgres + Redis so the fix is proven end-to-end, not just
# via a directly-called reconcile().
# ---------------------------------------------------------------------------


def _seed_printer(settings, *, name: str, enabled: bool) -> int:
    with base.sync_session() as s:
        printer = Printer(
            name=name,
            kind=PrinterKind.BAMBU_LAN,
            host="h",
            serial=name,
            access_code_enc=encrypt_secret(settings, "12345678"),
            enabled=enabled,
        )
        s.add(printer)
        s.commit()
        s.refresh(printer)
        return printer.id


def _wait_until(predicate, *, timeout: float = 20.0, interval: float = 0.02) -> bool:
    # Returns the instant the predicate holds, so a generous budget costs
    # nothing when healthy; it only absorbs CPU starvation under xdist
    # (reconcile does DB + Redis + adapter work per tick on a loaded runner).
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _find_thread(name: str, *, timeout: float = 20.0, interval: float = 0.02) -> threading.Thread:
    """Poll ``threading.enumerate()`` for a thread by name.

    ``_workers[id]`` is set a couple of statements before the `cmd-{id}`
    thread is actually spawned (see ``start_printer``/``_subscribe_commands``),
    so a bare `id in daemon._workers` check can momentarily race ahead of the
    thread's existence -- retry instead of a single lookup.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for t in threading.enumerate():
            if t.name == name:
                return t
        time.sleep(interval)
    raise AssertionError(f"no thread named {name!r} appeared within {timeout}s")


def test_run_reconciles_newly_enabled_printer(
    redis_url, printer_enabled, migrated_db, fake_adapter, monkeypatch
):
    """A printer enabled/created AFTER printerd's run() loop has started must
    be picked up within a poll tick or two -- no process restart required."""
    settings = get_settings()
    monkeypatch.setattr(printerd_module, "_POLL_INTERVAL_S", 0.05)
    daemon = PrinterDaemon(settings)
    thread = threading.Thread(target=daemon.run, daemon=True)
    thread.start()
    try:
        # Give the loop a couple of ticks with nothing enabled yet.
        time.sleep(0.15)
        assert daemon._workers == {}

        pid = _seed_printer(settings, name="late", enabled=True)

        assert _wait_until(lambda: pid in daemon._workers), (
            f"printer {pid} was never picked up by reconcile(); workers={daemon._workers!r}"
        )
    finally:
        daemon.stop()
        thread.join(timeout=2.0)


def test_reconcile_tears_down_disabled_printer_thread(
    redis_url, printer_enabled, migrated_db, fake_adapter, monkeypatch
):
    """Disabling a printer must tear down BOTH its worker AND its `cmd-{id}`
    command thread (which blocks on a Redis pubsub `listen()`) -- a naive
    `self._workers.pop(id)` leaves the thread (and its Redis connection)
    running forever."""
    settings = get_settings()
    monkeypatch.setattr(printerd_module, "_POLL_INTERVAL_S", 0.05)
    id1 = _seed_printer(settings, name="keep", enabled=True)
    id2 = _seed_printer(settings, name="drop", enabled=True)

    daemon = PrinterDaemon(settings)
    thread = threading.Thread(target=daemon.run, daemon=True)
    thread.start()
    try:
        assert _wait_until(lambda: {id1, id2} <= daemon._workers.keys())
        cmd_thread = _find_thread(f"cmd-{id2}")
        assert _wait_until(cmd_thread.is_alive)

        with base.sync_session() as s:
            printer = s.get(Printer, id2)
            printer.enabled = False
            s.commit()

        assert _wait_until(lambda: id2 not in daemon._workers), (
            f"disabled printer {id2} was never torn down; workers={daemon._workers!r}"
        )
        assert id1 in daemon._workers  # the still-enabled printer is untouched

        cmd_thread.join(timeout=20.0)
        assert not cmd_thread.is_alive()
        assert not any(t.name == f"cmd-{id2}" for t in threading.enumerate())

        # Step 4 sanity: a few more enable/disable cycles must never leak a
        # lingering cmd-{id2} thread.
        for _ in range(2):
            with base.sync_session() as s:
                s.get(Printer, id2).enabled = True
                s.commit()
            assert _wait_until(lambda: id2 in daemon._workers)
            cycle_thread = _find_thread(f"cmd-{id2}")

            with base.sync_session() as s:
                s.get(Printer, id2).enabled = False
                s.commit()
            assert _wait_until(lambda: id2 not in daemon._workers)
            cycle_thread.join(timeout=2.0)
            assert not cycle_thread.is_alive()
        assert not any(t.name == f"cmd-{id2}" for t in threading.enumerate())
    finally:
        daemon.stop()
        thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Round 8 T2: reconcile() also restarts a still-enabled printer's worker when
# its CONNECTION signature (host/serial/access_code_enc) changes -- the
# presence-only diff above never notices this (the printer never leaves
# `_workers`), so a running worker would otherwise keep talking to the OLD
# host/serial or authenticating with a stale access code forever. Driven
# directly via `reconcile()` (not the background `run()` loop) for
# determinism; the fake-adapter fixture ignores `conn` entirely, so a
# restart is asserted via `PrinterWorker` object identity -- `start_printer`
# always builds a fresh one, so `is not` proves the old worker was torn down
# and replaced, not merely left in place.
# ---------------------------------------------------------------------------


def test_reconcile_restarts_worker_when_host_changes(
    redis_url, printer_enabled, migrated_db, fake_adapter
):
    settings = get_settings()
    pid = _seed_printer(settings, name="p", enabled=True)
    daemon = PrinterDaemon(settings)
    try:
        daemon.reconcile()
        worker_before = daemon._workers[pid]

        with base.sync_session() as s:
            s.get(Printer, pid).host = "new-host"
            s.commit()
        daemon.reconcile()

        assert daemon._workers[pid] is not worker_before
    finally:
        # M5: reconcile() (unlike run()) spawns real `cmd-{id}` threads but
        # is driven directly here with no background run() loop/stop() of
        # its own -- stop the daemon so its cmd thread doesn't leak into
        # later tests (it previously did, and one later test had to work
        # around it -- see test_run_tears_down_and_restarts_all_workers_on_db_flag_flip).
        daemon.stop()


def test_reconcile_restarts_worker_when_serial_changes(
    redis_url, printer_enabled, migrated_db, fake_adapter
):
    settings = get_settings()
    pid = _seed_printer(settings, name="p", enabled=True)
    daemon = PrinterDaemon(settings)
    try:
        daemon.reconcile()
        worker_before = daemon._workers[pid]

        with base.sync_session() as s:
            s.get(Printer, pid).serial = "NEWSERIAL01"
            s.commit()
        daemon.reconcile()

        assert daemon._workers[pid] is not worker_before
    finally:
        daemon.stop()


def test_reconcile_restarts_worker_when_access_code_changes(
    redis_url, printer_enabled, migrated_db, fake_adapter
):
    settings = get_settings()
    pid = _seed_printer(settings, name="p", enabled=True)
    daemon = PrinterDaemon(settings)
    try:
        daemon.reconcile()
        worker_before = daemon._workers[pid]

        with base.sync_session() as s:
            s.get(Printer, pid).access_code_enc = encrypt_secret(settings, "87654321")
            s.commit()
        daemon.reconcile()

        assert daemon._workers[pid] is not worker_before
    finally:
        daemon.stop()


def test_reconcile_does_not_restart_on_name_only_change(
    redis_url, printer_enabled, migrated_db, fake_adapter
):
    settings = get_settings()
    pid = _seed_printer(settings, name="p", enabled=True)
    daemon = PrinterDaemon(settings)
    try:
        daemon.reconcile()
        worker_before = daemon._workers[pid]

        with base.sync_session() as s:
            s.get(Printer, pid).name = "renamed"
            s.commit()
        daemon.reconcile()

        assert daemon._workers[pid] is worker_before
    finally:
        daemon.stop()


# ---------------------------------------------------------------------------
# Round 10 T3: the `printer_enabled` flag is now read LIVE off the DB-backed
# `AppConfig` (`app.services.app_config`), once per `enabled_printers()`
# call, instead of gating `main()` at process start. The `printer_enabled`
# fixture above sets the ENV var true (so these prove the DB row, once
# present, overrides that env fallback); with no row at all the existing
# tests above (which all use that fixture) still pass, proving the env
# fallback path is untouched.
# ---------------------------------------------------------------------------


def _set_flag_in_db(printer_enabled: bool) -> None:
    """Insert/update the `"app"` Setting row's `printer_enabled` key --
    mirrors what `PUT /settings/app` persists, without going through the API
    (these tests drive `PrinterDaemon` directly)."""
    with base.sync_session() as s:
        row = s.get(Setting, "app")
        if row is None:
            s.add(Setting(key="app", value={"printer_enabled": printer_enabled}))
        else:
            row.value = {**row.value, "printer_enabled": printer_enabled}
        s.commit()


def test_enabled_printers_empty_when_db_row_flag_off(
    redis_url, printer_enabled, migrated_db, fake_adapter
):
    """No row yet -> env fallback (the `printer_enabled` fixture) still
    reports the printer; a row explicitly turning the flag off overrides
    that env value with `[]`, no matter what `Printer.enabled` says."""
    settings = get_settings()
    _seed_printer(settings, name="p", enabled=True)
    daemon = PrinterDaemon(settings)
    assert len(daemon.enabled_printers()) == 1

    _set_flag_in_db(False)
    assert daemon.enabled_printers() == []

    _set_flag_in_db(True)
    assert len(daemon.enabled_printers()) == 1


def test_run_tears_down_and_restarts_all_workers_on_db_flag_flip(
    redis_url, printer_enabled, migrated_db, fake_adapter, monkeypatch
):
    """End-to-end via the real background `run()` loop (mirrors Task 5's
    `test_run_reconciles_newly_enabled_printer`/
    `test_reconcile_tears_down_disabled_printer_thread`): flipping the DB
    row off must tear the running worker (and its `cmd-{id}` thread) down
    within a few `_POLL_INTERVAL_S` ticks -- with NO printerd restart -- and
    flipping it back on must pick the printer back up, again with no
    restart.

    The cmd thread is taken from `daemon._threads[pid]` (this daemon's OWN
    registry), NOT looked up process-wide by name via `_find_thread`: this is
    belt-and-suspenders against a stale still-alive `cmd-1` thread from
    another test in this module -- the Round 8 T2 reconcile tests now
    `daemon.stop()` in a `finally` (M5 fix wave), but truncation restarts
    printer ids at 1 for every test, so a process-wide name lookup would
    still be one accidental missing `stop()` away from returning the wrong
    thread and failing the liveness assertions below spuriously."""
    settings = get_settings()
    monkeypatch.setattr(printerd_module, "_POLL_INTERVAL_S", 0.05)
    pid = _seed_printer(settings, name="p", enabled=True)

    daemon = PrinterDaemon(settings)
    thread = threading.Thread(target=daemon.run, daemon=True)
    thread.start()
    try:
        # `_threads[pid]` is registered a couple of statements after
        # `_workers[pid]` (see `_subscribe_commands`), so wait on it
        # directly rather than on `_workers`.
        assert _wait_until(lambda: pid in daemon._threads), (
            f"printer {pid} was never started; workers={daemon._workers!r}"
        )
        cmd_thread = daemon._threads[pid]
        assert cmd_thread.is_alive()

        _set_flag_in_db(False)

        assert _wait_until(lambda: daemon._workers == {}), (
            f"workers not torn down after the DB flag flipped off: {daemon._workers!r}"
        )
        cmd_thread.join(timeout=20.0)
        assert not cmd_thread.is_alive()

        _set_flag_in_db(True)

        assert _wait_until(lambda: pid in daemon._workers), (
            "printer was never picked back up after the DB flag flipped back on"
        )
    finally:
        daemon.stop()
        thread.join(timeout=2.0)


def _jobs(pid):
    with base.sync_session() as s:
        return list(s.query(PrintJob).filter(PrintJob.printer_id == pid).order_by(PrintJob.id))


def _state(job_id):
    with base.sync_session() as s:
        return s.get(PrintJob, job_id).state


def test_terminal_transition_enqueues_archive(worker, monkeypatch):
    calls = []
    monkeypatch.setattr(printerd_module, "_enqueue_archive", lambda j, r: calls.append((j, r)))
    w, _adapter, pid, _ = worker
    job_id = _seed_active_job(pid)
    w.handle_report({"gcode_state": "RUNNING"})
    w.handle_report({"gcode_state": "FINISH"})
    assert calls == [(job_id, ArchiveResult.COMPLETED)]
    w.handle_report({"gcode_state": "FINISH"})
    assert len(calls) == 1


def test_cancel_code_failed_and_app_stop(worker, monkeypatch):
    calls = []
    monkeypatch.setattr(printerd_module, "_enqueue_archive", lambda j, r: calls.append((j, r)))
    w, _adapter, pid, _ = worker
    j1 = _seed_active_job(pid)
    w.handle_report({"gcode_state": "RUNNING"})
    w.handle_report({"gcode_state": "FAILED", "print_error": 50348044})
    assert calls[-1] == (j1, ArchiveResult.CANCELED) and _state(j1) == "canceled"
    with base.sync_session() as s:
        j2 = PrintJob(printer_id=pid, file_id=None, state=PrintJobState.PRINTING)
        s.add(j2)
        s.commit()
        j2 = j2.id
    w.handle_command("stop")
    w.handle_report({"gcode_state": "FAILED"})
    assert calls[-1] == (j2, ArchiveResult.STOPPED) and _state(j2) == "canceled"
    with base.sync_session() as s:
        j3 = PrintJob(printer_id=pid, file_id=None, state=PrintJobState.PRINTING)
        s.add(j3)
        s.commit()
        j3 = j3.id
    w.handle_report({"gcode_state": "FAILED", "print_error": 1234})
    assert calls[-1] == (j3, ArchiveResult.FAILED) and _state(j3) == "failed"


def test_enqueue_failure_is_swallowed(worker, monkeypatch):
    def boom(j, r):
        raise RuntimeError("broker down")

    monkeypatch.setattr(printerd_module, "_enqueue_archive", boom)
    w, _adapter, pid, _ = worker
    job_id = _seed_active_job(pid)
    w.handle_report({"gcode_state": "FINISH"})
    assert _state(job_id) == "finished"


def test_external_job_detection(worker):
    w, _adapter, pid, _ = worker
    w.handle_report({"gcode_state": "RUNNING", "subtask_name": "Benchy", "total_layer_num": 100})
    w.handle_report({"gcode_state": "RUNNING", "subtask_name": "Benchy", "layer_num": 2})
    jobs = _jobs(pid)
    assert len(jobs) == 1
    j = jobs[0]
    assert (j.origin, j.file_id, j.state, j.subtask_name) == (
        "external",
        None,
        "printing",
        "Benchy",
    )
    assert j.created_by_user_id is None and j.started_at is not None


def test_no_external_job_while_app_job_active(worker):
    w, _adapter, pid, _ = worker
    _seed_active_job(pid)
    w.handle_report({"gcode_state": "RUNNING"})
    jobs = _jobs(pid)
    assert len(jobs) == 1 and jobs[0].state == "printing"


def test_idle_creates_no_external_job(worker):
    w, _adapter, pid, _ = worker
    w.handle_report({"gcode_state": "IDLE"})
    w.handle_report({"gcode_state": "FINISH"})
    assert _jobs(pid) == []


def test_running_with_print_error_creates_no_external_job(worker):
    w, _adapter, pid, _ = worker
    for _ in range(3):
        w.handle_report({"gcode_state": "RUNNING", "print_error": 1234})
    assert _jobs(pid) == []


def test_stale_stop_flag_cleared_when_next_job_starts(worker, monkeypatch):
    calls = []
    monkeypatch.setattr(printerd_module, "_enqueue_archive", lambda j, r: calls.append((j, r)))
    w, _adapter, pid, _ = worker
    w.handle_command("stop")  # no active job; flag would go stale
    job_id = _seed_active_job(pid)
    w.handle_report({"gcode_state": "RUNNING"})
    w.handle_report({"gcode_state": "FAILED", "print_error": 1234})
    assert calls[-1] == (job_id, ArchiveResult.FAILED)


def test_terminal_transition_keeps_job_frame(worker, monkeypatch):
    monkeypatch.setattr(printerd_module, "_enqueue_archive", lambda j, r: None)
    w, _adapter, pid, client = worker
    job_id = _seed_active_job(pid)
    cs.put_frame(client, pid, JPEG)
    w.handle_report({"gcode_state": "RUNNING"})
    w.handle_report({"gcode_state": "FINISH"})
    assert client.get(cs.job_frame_key(pid, job_id)) == JPEG


def test_daemon_never_blocks_on_hung_camera(redis_url, printer_enabled, migrated_db, monkeypatch):
    """A hung camera socket (FakeCamera(hang=True), ignoring the worker's own
    stop event) must never block printerd's main status-poll loop nor
    ``stop_printer()`` -- only ``close()`` (via the non-blocking
    ``camera.remove()`` signal) unblocks it."""
    settings = get_settings()
    pid = _seed_printer(settings, name="camtest", enabled=True)
    adapter = FakePrinterAdapter(PrinterConnection(host="h", serial="S", access_code="x"))
    adapter.camera = FakeCamera([], hang=True)
    monkeypatch.setattr(printerd_module, "build_adapter", lambda kind, conn: adapter)
    monkeypatch.setattr(printerd_module, "_POLL_INTERVAL_S", 0.05)

    daemon = PrinterDaemon(settings)
    thread = threading.Thread(target=daemon.run, daemon=True)
    thread.start()
    try:
        assert _wait_until(lambda: pid in daemon._workers)
        client = redis_lib.Redis.from_url(settings.redis_url)
        cs.add_viewer(client, pid, "v1")
        assert _wait_until(lambda: pid in daemon.camera._workers, timeout=3.0)

        count_before = adapter.full_status_requests
        assert _wait_until(lambda: adapter.full_status_requests > count_before + 2, timeout=3.0), (
            "status polls stalled -- the hung camera worker blocked the main loop"
        )

        t0 = time.monotonic()
        daemon.stop_printer(pid)
        assert time.monotonic() - t0 < 3.0
    finally:
        daemon.stop()
        thread.join(timeout=3.0)


def test_run_starts_and_stop_joins_ha_bridge(redis_url, printer_enabled, migrated_db, monkeypatch):
    """``run()`` starts the HA bridge thread (P8 plan Task 3) right after the
    camera supervisor; ``stop()`` joins it back down -- same lifecycle as
    ``daemon.camera``. Read ``daemon.ha._thread`` directly (rather than
    hunting ``threading.enumerate()`` by the shared "ha-bridge" thread name)
    -- several OTHER tests in this module call ``daemon.run()`` with
    ``_stop`` pre-set just to exercise its early reconcile/log path, which
    still starts (and never stops) a same-named camera/HA thread of their
    own, so a name-based lookup could grab one of those instead of this
    daemon's."""
    settings = get_settings()
    monkeypatch.setattr(printerd_module, "_POLL_INTERVAL_S", 0.05)
    daemon = PrinterDaemon(settings)
    thread = threading.Thread(target=daemon.run, daemon=True)
    thread.start()
    try:
        assert _wait_until(lambda: daemon.ha._thread is not None, timeout=3.0)
        ha_thread = daemon.ha._thread
        assert ha_thread.is_alive()
    finally:
        daemon.stop()
        thread.join(timeout=3.0)
    assert _wait_until(lambda: not ha_thread.is_alive(), timeout=3.0)


def _sensor_worker(worker, monkeypatch):
    w, _adapter, pid, _client = worker
    calls: list[tuple[int, dict]] = []
    monkeypatch.setattr(
        printerd_module, "_enqueue_sensor_sample", lambda p, snap: calls.append((p, snap))
    )
    return w, pid, calls


def _tick(w, now):
    sent = w.sensor_tick(now)
    if w._sensor_thread is not None:
        w._sensor_thread.join(2)
    return sent


def test_sensor_tick_samples_once_per_interval_and_skips_stale(worker, monkeypatch):
    w, pid, calls = _sensor_worker(worker, monkeypatch)
    assert not _tick(w, 0.0)  # no report yet
    w.handle_report(
        {
            "gcode_state": "IDLE",
            "nozzle_temper": 201.0,
            "last_report_at": "2026-10-23T10:00:00Z",
            "ams_units": [{"id": 0, "humidity": 4, "temp": 25.0, "sw_ver": "x"}],
        }
    )
    assert _tick(w, 100.0)
    assert calls[0][0] == pid
    assert calls[0][1]["nozzle"] == 201.0
    assert calls[0][1]["ams"] == [{"id": 0, "humidity": 4, "temp": 25.0}]
    assert not _tick(w, 120.0)  # inside the 60 s interval
    assert not _tick(w, 200.0)  # unchanged last_report_at => offline/stale
    w.handle_report({"last_report_at": "2026-10-23T10:03:00Z"})
    assert _tick(w, 300.0)
    assert len(calls) == 2


def test_sensor_tick_never_blocks_on_slow_enqueue(worker, monkeypatch):
    w, _adapter, _pid, _client = worker
    gate = threading.Event()
    monkeypatch.setattr(printerd_module, "_enqueue_sensor_sample", lambda p, s: gate.wait(5))
    w.handle_report({"gcode_state": "IDLE", "last_report_at": "2026-10-23T10:00:00Z"})
    t0 = time.monotonic()
    assert w.sensor_tick(0.0)
    assert time.monotonic() - t0 < 0.5
    gate.set()
    w._sensor_thread.join(2)


def test_sensor_tick_skips_while_previous_enqueue_alive(worker, monkeypatch):
    w, _adapter, _pid, _client = worker
    gate = threading.Event()
    calls = []
    monkeypatch.setattr(
        printerd_module, "_enqueue_sensor_sample", lambda p, s: (calls.append(1), gate.wait(5))
    )
    w.handle_report({"last_report_at": "2026-10-23T10:00:00Z"})
    assert w.sensor_tick(0.0)
    w.handle_report({"last_report_at": "2026-10-23T10:01:00Z"})
    assert not w.sensor_tick(100.0)  # first enqueue still hung
    gate.set()
    w._sensor_thread.join(2)
    assert w.sensor_tick(200.0)
    w._sensor_thread.join(2)
    assert len(calls) == 2


# -- Q2 command bus v2 -------------------------------------------------------


def _result(client, pid, rid):
    from app.printers.base import cmd_result_key

    raw = client.get(cmd_result_key(pid, rid))
    return json.loads(raw) if raw else None


def test_dispatch_runs_adapter_and_publishes_result_with_ttl(worker):
    from app.printers.base import cmd_result_key

    w, adapter, pid, client = worker
    w.handle_report({"gcode_state": "IDLE"})
    res = w.handle_command("set_temperature", {"target": "bed", "value": 60}, "rid1")
    assert res == {"ok": True}
    assert adapter.calls == [("set_temperature", "bed", 60)]
    assert _result(client, pid, "rid1") == {"ok": True}
    assert 0 < client.ttl(cmd_result_key(pid, "rid1")) <= 30


def test_dispatch_table_maps_every_command(worker):
    w, adapter, _pid, _c = worker
    w.handle_report({"gcode_state": "IDLE", "nozzle_temper": 200})
    for cmd, args in [
        ("set_fan", {"fan": "part", "pct": 50}),
        ("set_light", {"on": True}),
        ("set_speed", {"level": 3}),
        ("home", {}),
        ("jog", {"axis": "x", "mm": 7}),
        ("extrude", {"mm": 5}),
        ("gcode", {"gcode": " G28 "}),
        ("set_airduct", {"mode": "heating"}),
        ("calibrate", {}),
        ("clear_hms", {}),
        ("load_filament", {"tray": 1}),
        ("unload_filament", {}),
    ]:
        assert w.handle_command(cmd, args)["ok"] is True, cmd
    assert ("jog", "X", 10.0) in adapter.calls  # step clamped to {1,10,50}
    assert ("send_gcode", "G28") in adapter.calls
    assert ("calibrate", True, True, True) in adapter.calls


def test_bad_args_unknown_command_and_unsupported(worker):
    w, adapter, _pid, _c = worker
    assert w.handle_command("set_speed", {"level": 9})["code"] == "invalid"
    assert w.handle_command("set_temperature", {"target": "bed", "value": 999})["code"] == "invalid"
    assert w.handle_command("gcode", {"gcode": "x" * 1025})["code"] == "invalid"
    assert w.handle_command("launch_missiles")["code"] == "unsupported"
    adapter.raise_on["set_light"] = NotImplementedError()
    assert w.handle_command("set_light", {"on": True})["code"] == "unsupported"
    adapter.raise_on["home"] = OSError("boom")
    assert w.handle_command("home")["code"] == "error"
    assert w.handle_command("skip_objects", {"object_ids": []})["code"] == "invalid"


@pytest.mark.parametrize("state", ["RUNNING", "PAUSE"])
def test_motion_refused_while_printing(worker, state):
    w, adapter, pid, client = worker
    w.handle_report({"gcode_state": state, "nozzle_temper": 220})
    for cmd, args in [
        ("home", {}),
        ("jog", {"axis": "X", "mm": 1}),
        ("extrude", {"mm": 5}),
        ("gcode", {"gcode": "G28"}),
    ]:
        assert w.handle_command(cmd, args, f"r-{cmd}")["code"] == "printing", cmd
        assert _result(client, pid, f"r-{cmd}")["code"] == "printing"
    assert adapter.calls == []
    assert w.handle_command("set_speed", {"level": 3}) == {"ok": True}  # Sport while printing
    assert w.handle_command("pause")["ok"] is True


def test_cold_extrude_refused_and_skip_needs_active_print(worker):
    w, adapter, _pid, _c = worker
    w.handle_report({"gcode_state": "IDLE", "nozzle_temper": 169.0})
    assert w.handle_command("extrude", {"mm": 5})["code"] == "cold_extrude"
    assert w.handle_command("skip_objects", {"object_ids": [3]})["code"] == "not_printing"
    w.handle_report({"gcode_state": "RUNNING"})
    assert w.handle_command("skip_objects", {"object_ids": [3, 3, 1]}) == {"ok": True}
    assert adapter.calls == [("skip_objects", [1, 3])]


def test_verify_failed_reply_becomes_dev_mode_off_and_state_flips(worker):
    w, adapter, pid, client = worker
    w.handle_report({"gcode_state": "IDLE"})
    adapter.reply = {
        "command": "gcode_line",
        "result": "failed",
        "reason": "mqtt message verify failed",
    }
    res = w.handle_command("set_light", {"on": True}, "rid-off")
    assert res["code"] == "dev_mode_off"
    assert _result(client, pid, "rid-off")["code"] == "dev_mode_off"
    state = json.loads(client.get(state_key(pid)))
    assert state["dev_mode"] is False and state["dev_mode_reason"] == "command_verify_failed"
    adapter.reply = {"command": "gcode_line", "result": "success"}
    assert w.handle_command("set_light", {"on": False}) == {"ok": True}
    assert json.loads(client.get(state_key(pid)))["dev_mode"] is True


def test_report_publishes_dev_mode_and_sends_guarded_probe(worker, monkeypatch):
    w, adapter, pid, client = worker
    clock = [100.0]
    monkeypatch.setattr(printerd_module.time, "monotonic", lambda: clock[0])
    slot = {
        "tray_info_idx": "GFA00",
        "tray_type": "PLA",
        "tray_color": "FF0000FF",
        "nozzle_temp_min": 190,
        "nozzle_temp_max": 230,
    }
    w.handle_report({"gcode_state": "RUNNING", "ext_tray_raw": slot})
    clock[0] += 10
    w.handle_report({"gcode_state": "RUNNING", "ext_tray_raw": slot})  # printing: no probe
    assert adapter.calls == []
    w.handle_report({"gcode_state": "IDLE", "ext_tray_raw": slot})
    assert adapter.calls == [("devmode_probe", slot, "9000017")]
    assert json.loads(client.get(state_key(pid)))["dev_mode"] is None  # unknown until answered
    w.handle_reply({"sequence_id": "9000017", "result": "success"})
    assert json.loads(client.get(state_key(pid)))["dev_mode"] is True


def test_fun_bit_in_report_sets_dev_mode_without_probe(worker):
    w, adapter, pid, client = worker
    w.handle_report({"gcode_state": "IDLE", "fun": "0x20000000"})
    state = json.loads(client.get(state_key(pid)))
    assert state["dev_mode"] is False and state["dev_mode_reason"] == "fun_bit"
    assert adapter.calls == []


def test_raw_tap_only_while_viewer_flag_set(worker):
    from app.services import mqtt_debug

    w, _adapter, pid, client = worker
    client.delete(mqtt_debug.list_key(pid), mqtt_debug.viewer_key(pid))
    w.handle_raw("device/S/report", b'{"print": {}}')
    assert client.llen(mqtt_debug.list_key(pid)) == 0  # no viewer -> no tap
    mqtt_debug.mark_viewer_sync(client, pid)
    w._tap_until = 0.0  # drop the 1s cache
    w.handle_raw("device/S/report", b'{"print": {}}')
    assert client.llen(mqtt_debug.list_key(pid)) == 1
    client.delete(mqtt_debug.list_key(pid), mqtt_debug.viewer_key(pid))


def test_write_public_marks_stale_report_disconnected(worker):
    from datetime import UTC, datetime, timedelta

    w, _adapter, pid, client = worker
    old = (datetime.now(UTC) - timedelta(seconds=printerd_module.STALE_AFTER_S + 5)).isoformat()
    w.handle_report({**cass.SNAPSHOT_IDLE, "last_report_at": old})
    assert json.loads(client.get(state_key(pid)))["connected"] is False
    fresh = datetime.now(UTC).isoformat()
    w.handle_report({**cass.SNAPSHOT_IDLE, "last_report_at": fresh})
    state = json.loads(client.get(state_key(pid)))
    assert state["connected"] is True
    assert 0 < client.ttl(state_key(pid)) <= printerd_module.STATE_TTL_S


def test_write_public_publishes_no_data_snapshot_as_disconnected(worker):
    w, _adapter, pid, client = worker
    w.handle_report({"connected": False, "gcode_state": None})
    state = json.loads(client.get(state_key(pid)))
    assert state["connected"] is False and state["gcode_state"] is None
    assert state["nozzle_temper"] is None


def test_persist_nozzle_writes_printer_row_on_change(worker):
    from types import SimpleNamespace

    w, _adapter, pid, _client = worker
    w._persist_nozzle(SimpleNamespace(nozzle_mm=0.2))
    with base.sync_session() as s:
        assert s.get(Printer, pid).nozzle_mm == 0.2
    w._persist_nozzle(SimpleNamespace(nozzle_mm=None))  # unknown never clears the last seen
    w._persist_nozzle(SimpleNamespace(nozzle_mm=0.0))
    with base.sync_session() as s:
        assert s.get(Printer, pid).nozzle_mm == 0.2


def test_next_pause_in_status_and_one_shot_heads_up(worker, monkeypatch):
    from app.printers import pause_eta

    w, _adapter, pid, client = worker
    sent = []
    monkeypatch.setattr(printerd_module, "emit_bg", lambda *a, **k: sent.append(a))
    # PRINTING snapshot: layer 66, 21 min left -> a pause at 18 min left is 3 min away.
    pause_eta.store_points(client, pid, 99, [{"layer": 70, "remaining_min": 18}])
    try:
        for _ in range(3):
            w.handle_report(cass.SNAPSHOT_PRINTING)
        state = json.loads(client.get(state_key(pid)))
        assert state["next_pause"] == {"layer": 70, "eta_s": 180}
        pauses = [a for a in sent if a[0] == "pause_upcoming"]
        assert len(pauses) == 1
        assert pauses[0][2]["minutes"] == 3 and pauses[0][2]["layer"] == 70
    finally:
        client.delete(pause_eta.warned_key(pid, 99), pause_eta.points_key(pid))
