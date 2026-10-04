import json
import logging
import threading
import time

import pytest
import redis as redis_lib

from app import printerd as printerd_module
from app.config import get_settings
from app.crypto import encrypt_secret
from app.models import Blob, File, Model, Printer, PrintJob, Revision, Setting
from app.models.enums import BlobFormat, BlobKind, PrinterKind, PrintJobState
from app.printerd import PrinterDaemon, PrinterWorker
from app.printers.base import PrinterConnection, state_key
from app.printers.fake import FakePrinterAdapter
from app.tasks import base
from tests.cassettes import bambu_snapshots as cass


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


def _wait_until(predicate, *, timeout: float = 2.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _find_thread(name: str, *, timeout: float = 2.0, interval: float = 0.02) -> threading.Thread:
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
        assert cmd_thread.is_alive()

        with base.sync_session() as s:
            printer = s.get(Printer, id2)
            printer.enabled = False
            s.commit()

        assert _wait_until(lambda: id2 not in daemon._workers), (
            f"disabled printer {id2} was never torn down; workers={daemon._workers!r}"
        )
        assert id1 in daemon._workers  # the still-enabled printer is untouched

        cmd_thread.join(timeout=2.0)
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
        cmd_thread.join(timeout=2.0)
        assert not cmd_thread.is_alive()

        _set_flag_in_db(True)

        assert _wait_until(lambda: pid in daemon._workers), (
            "printer was never picked back up after the DB flag flipped back on"
        )
    finally:
        daemon.stop()
        thread.join(timeout=2.0)


def _terminate_seeded_job(job_id: int) -> None:
    with base.sync_session() as s:
        s.get(PrintJob, job_id).state = PrintJobState.CANCELED.value
        s.commit()


def _job_count() -> int:
    with base.sync_session() as s:
        return len(s.query(PrintJob).all())


def test_unknown_subtask_creates_no_job(worker):
    w, _adapter, pid, _client = worker
    _terminate_seeded_job(_seed_active_job(pid))
    w.handle_report(cass.SNAPSHOT_PRINTING)  # subtask "widget" matches no file
    assert _job_count() == 1


def test_matched_subtask_creates_and_persists_job(worker):
    w, _adapter, pid, _client = worker
    _terminate_seeded_job(_seed_active_job(pid))
    w.handle_report({**cass.SNAPSHOT_PRINTING, "subtask_name": "p.gcode.3mf"})
    assert _job_count() == 2
    with base.sync_session() as s:
        job = s.query(PrintJob).order_by(PrintJob.id.desc()).first()
        assert job.state == PrintJobState.PRINTING.value
        assert job.started_at is not None
        file = s.get(File, job.file_id)
        assert file.rel_path == "p.gcode.3mf"
        model = s.get(Model, s.get(Revision, file.revision_id).model_id)
        assert model.print_status == "printing"


def test_subtask_match_requires_path_boundary(worker):
    w, _adapter, pid, _client = worker
    _terminate_seeded_job(_seed_active_job(pid))
    # "gcode.3mf" is a raw suffix of ".../p.gcode.3mf" but not a path segment.
    w.handle_report({**cass.SNAPSHOT_PRINTING, "subtask_name": "gcode.3mf"})
    assert _job_count() == 1


def test_subtask_percent_is_not_a_wildcard(worker):
    w, _adapter, pid, _client = worker
    _terminate_seeded_job(_seed_active_job(pid))
    w.handle_report({**cass.SNAPSHOT_PRINTING, "subtask_name": "%"})
    assert _job_count() == 1


def test_finish_snapshot_failure_does_not_lose_finished_state(worker, monkeypatch):
    import httpx

    from app.services import camera_snapshot, library

    w, adapter, pid, _client = worker
    job_id = _seed_active_job(pid)
    monkeypatch.setattr(
        adapter, "get_camera_urls", lambda: {"snapshot_url": "http://cam/snap.jpg"}, raising=False
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"jpegbytes"))
    real_client = httpx.Client
    monkeypatch.setattr(
        camera_snapshot.httpx, "Client", lambda **kw: real_client(transport=transport, **kw)
    )

    def _boom(session, *, model, revision, staged):
        session.add(File(revision_id=10**9, blob_hash="z" * 64, rel_path="x", storage_path="x"))
        session.flush()  # FK violation -> session needs rollback

    monkeypatch.setattr(library, "store_imported_file_sync", _boom)
    w.handle_report(cass.SNAPSHOT_PRINTING)
    w.handle_report(cass.SNAPSHOT_FINISH)
    with base.sync_session() as s:
        job = s.get(PrintJob, job_id)
        assert job.state == PrintJobState.FINISHED.value
        file = s.get(File, job.file_id)
        rev = s.get(Revision, file.revision_id)
        assert s.get(Model, rev.model_id).quantity_printed == 1


def test_finish_snapshot_duplicate_blob_keeps_finished_state(worker, monkeypatch):
    """Real store_imported_file_sync hitting a duplicate-blob IntegrityError
    must not roll back the caller's pending finished-job state."""
    import httpx
    from blake3 import blake3
    from sqlalchemy.orm import Session

    from app.services import camera_snapshot

    w, adapter, pid, _client = worker
    job_id = _seed_active_job(pid)
    data = b"jpegbytes"
    with base.sync_session() as s:
        s.add(
            Blob(
                hash=blake3(data).hexdigest(),
                size=len(data),
                kind=BlobKind.IMAGE,
                format=BlobFormat.JPG,
            )
        )
        s.commit()
    monkeypatch.setattr(
        adapter, "get_camera_urls", lambda: {"snapshot_url": "http://cam/snap.jpg"}, raising=False
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=data))
    real_client = httpx.Client
    monkeypatch.setattr(
        camera_snapshot.httpx, "Client", lambda **kw: real_client(transport=transport, **kw)
    )
    real_get = Session.get
    hidden = {"n": 0}

    def _get(self, entity, ident, *a, **kw):
        if entity is Blob and ident == blake3(data).hexdigest() and hidden["n"] == 0:
            hidden["n"] += 1  # simulate a concurrent insert: first lookup misses
            return None
        return real_get(self, entity, ident, *a, **kw)

    monkeypatch.setattr(Session, "get", _get)
    w.handle_report(cass.SNAPSHOT_PRINTING)
    w.handle_report(cass.SNAPSHOT_FINISH)
    assert hidden["n"] == 1
    with base.sync_session() as s:
        job = s.get(PrintJob, job_id)
        assert job.state == PrintJobState.FINISHED.value
        file = s.get(File, job.file_id)
        rev = s.get(Revision, file.revision_id)
        assert s.get(Model, rev.model_id).quantity_printed == 1
        assert s.query(File).filter(File.rel_path.like("print-result-%")).count() == 1
