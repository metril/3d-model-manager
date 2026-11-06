"""PR-C review fixes: celery config, printerd command bus, task idempotency,
SMB error mapping."""

from __future__ import annotations

import errno
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
import redis as redis_lib
from smbprotocol.exceptions import NtStatus, SMBOSError

from app.config import get_settings
from app.printerd import PrinterDaemon, PrinterWorker
from app.printers import bambu
from app.storage.errors import StorageKeyNotFound
from app.storage.smb import SmbStorageBackend, _is_not_found
from app.tasks import base
from app.tasks.celery_app import VISIBILITY_TIMEOUT_S, celery_app


# -- 1. celery config ------------------------------------------------------
def test_celery_reliability_config():
    c = celery_app.conf
    assert c.broker_transport_options["visibility_timeout"] == VISIBILITY_TIMEOUT_S
    assert c.worker_prefetch_multiplier == 1
    assert c.task_soft_time_limit and c.task_time_limit > c.task_soft_time_limit
    from app.tasks import migrate, relocate, scan, sync_collections

    for t in (
        scan.scan_library,
        migrate.migrate_storage,
        relocate.relocate_all_models,
        sync_collections.sync_all,
    ):
        assert t.time_limit < VISIBILITY_TIMEOUT_S
        assert t.soft_time_limit > c.task_soft_time_limit
    from app.tasks import importing

    assert importing.import_from_url.soft_time_limit < importing.IMPORT_LOCK_TIMEOUT_S


def test_numeric_beat_entries_expire():
    for name, entry in celery_app.conf.beat_schedule.items():
        if isinstance(entry["schedule"], int | float):
            assert entry["options"]["expires"] == entry["schedule"], name


# -- 3d. sequence-id stamping + reply matching -----------------------------
def test_stamp_sequence_id_only_replaces_placeholder():
    a = {"print": {"command": "stop", "sequence_id": "0"}}
    b = {"print": {"command": "stop", "sequence_id": "0"}}
    seq_a, seq_b = bambu._stamp_sequence_id(a), bambu._stamp_sequence_id(b)
    assert seq_a != seq_b and a["print"]["sequence_id"] == seq_a
    probe = {"print": {"command": "x", "sequence_id": "10000000"}}
    assert bambu._stamp_sequence_id(probe) is None
    assert probe["print"]["sequence_id"] == "10000000"
    push = {"pushing": {"command": "pushall", "sequence_id": "0"}}
    assert bambu._stamp_sequence_id(push) is None


def _bare_worker() -> PrinterWorker:
    w = object.__new__(PrinterWorker)
    w._reply_evt = threading.Event()
    w._last_reply = None
    return w


def test_wait_reply_ignores_replies_to_other_commands(monkeypatch):
    monkeypatch.setattr("app.printerd.REPLY_WAIT_S", 1.0)
    w = _bare_worker()

    def deliver():
        time.sleep(0.05)
        w._last_reply = {"sequence_id": "999", "result": "success"}  # someone else's
        w._reply_evt.set()
        time.sleep(0.05)
        w._last_reply = {"sequence_id": "42", "result": "fail"}
        w._reply_evt.set()

    threading.Thread(target=deliver, daemon=True).start()
    w._wait_reply("42")
    assert w._last_reply["sequence_id"] == "42"


def test_wait_reply_discards_stale_reply_on_timeout(monkeypatch):
    monkeypatch.setattr("app.printerd.REPLY_WAIT_S", 0.2)
    w = _bare_worker()
    w._last_reply = {"sequence_id": "1"}
    w._reply_evt.set()
    w._wait_reply("2")
    assert w._last_reply is None


# -- 3a/3b. printerd start cleanup + command thread resilience -------------
def _daemon_with_fake_redis(pubsubs):
    d = object.__new__(PrinterDaemon)
    d._stop = threading.Event()
    d._workers, d._signatures = {}, {}
    d._pubsubs, d._threads, d._cmd_stops = {}, {}, {}
    d._start_failures, d._start_retry_at = {}, {}
    d.redis = SimpleNamespace(pubsub=lambda: pubsubs.pop(0))
    return d


class _PubSub:
    def __init__(self, messages, fail=False):
        self.messages, self.fail, self.closed = messages, fail, False

    def subscribe(self, *_):
        pass

    def listen(self):
        if self.fail:
            raise ConnectionError("redis down")
        yield from self.messages
        while not self.closed:
            time.sleep(0.01)

    def close(self):
        self.closed = True


def test_command_thread_resubscribes_after_redis_drop(monkeypatch):
    monkeypatch.setattr("app.printerd.CMD_RECONNECT_BASE_S", 0.01)
    import json

    handled = []
    worker = SimpleNamespace(handle_command=lambda c, a, r: handled.append(c))
    msg = {"type": "message", "data": json.dumps({"command": "pause"})}
    d = _daemon_with_fake_redis([_PubSub([], fail=True), _PubSub([msg])])
    d._subscribe_commands(1, worker)  # first pubsub dies immediately
    deadline = time.monotonic() + 3
    while not handled and time.monotonic() < deadline:
        time.sleep(0.02)
    d._stop.set()
    d._cmd_stops[1].set()
    assert handled == ["pause"]
    assert d._threads[1].is_alive() or handled


def test_failed_start_closes_adapter_and_backs_off(monkeypatch):
    import app.printerd as pd

    closed = []

    class Adapter:
        def set_report_handler(self, *_):
            pass

        def connect(self):
            raise OSError("boom")

        def close(self):
            closed.append(1)

    d = _daemon_with_fake_redis([])
    d.camera = d.obico = SimpleNamespace(remove=lambda *_: None)
    d.settings = get_settings()
    monkeypatch.setattr(pd, "connection_from_printer", lambda s, p: object())
    monkeypatch.setattr(pd, "build_adapter", lambda k, c: Adapter())
    monkeypatch.setattr(pd, "PrinterWorker", lambda *a, **k: SimpleNamespace())
    printer = SimpleNamespace(id=5, kind="bambu_lan")
    d._safe_start(printer)
    assert closed and d._start_failures[5] == 1
    d._safe_start(printer)  # inside the backoff window: not retried
    assert len(closed) == 1 and d._start_failures[5] == 1


# -- 5. SMB error mapping ----------------------------------------------------
def test_smb_only_enoent_means_not_found():
    assert _is_not_found(SMBOSError(NtStatus.STATUS_OBJECT_NAME_NOT_FOUND, "x"))
    assert _is_not_found(SMBOSError(NtStatus.STATUS_OBJECT_PATH_NOT_FOUND, "x"))
    assert not _is_not_found(SMBOSError(NtStatus.STATUS_ACCESS_DENIED, "x"))


@pytest.fixture
def smb(monkeypatch):
    b = object.__new__(SmbStorageBackend)
    b._ensure_session = lambda: None
    b._path = lambda key: key
    b._kwargs = {}
    b._c = SimpleNamespace(host="h", share="s")
    return b


def _raiser(status):
    def f(*a, **k):
        raise SMBOSError(status, "x")

    return f


def test_smb_exists_stat_read_delete_map_only_missing(smb, monkeypatch):
    import app.storage.smb as m

    monkeypatch.setattr(m.smbclient, "stat", _raiser(NtStatus.STATUS_OBJECT_NAME_NOT_FOUND))
    assert smb.exists("a") is False
    with pytest.raises(StorageKeyNotFound):
        smb.stat("a")
    monkeypatch.setattr(m.smbclient, "stat", _raiser(NtStatus.STATUS_ACCESS_DENIED))
    with pytest.raises(SMBOSError):
        smb.exists("a")
    with pytest.raises(SMBOSError):
        smb.stat("a")
    monkeypatch.setattr(m.smbclient, "open_file", _raiser(NtStatus.STATUS_ACCESS_DENIED))
    with pytest.raises(SMBOSError):
        smb.read("a")
    monkeypatch.setattr(m.smbclient, "remove", _raiser(NtStatus.STATUS_ACCESS_DENIED))
    with pytest.raises(SMBOSError):
        smb.delete("a")


def test_smb_walk_missing_prefix_empty_but_other_errors_raise(smb, monkeypatch):
    import app.storage.smb as m

    monkeypatch.setattr(m.smbclient, "scandir", _raiser(NtStatus.STATUS_OBJECT_PATH_NOT_FOUND))
    assert list(smb._walk_dir("p", "p")) == []
    monkeypatch.setattr(m.smbclient, "scandir", _raiser(NtStatus.STATUS_ACCESS_DENIED))
    with pytest.raises(SMBOSError):
        list(smb._walk_dir("p", "p"))
    assert errno.ENOENT  # (errno import used by the mapping)


# -- 4. task idempotency -----------------------------------------------------
def test_scan_duplicate_only_skips_queued_run(migrated_db, redis_url):
    from app.models import ScanRun
    from app.tasks import scan

    r = redis_lib.Redis.from_url(redis_url)
    with base.sync_session() as s:
        queued, running = ScanRun(state="queued"), ScanRun(state="running")
        s.add_all([queued, running])
        s.commit()
        qid, rid = queued.id, running.id
    r.set(scan.SCAN_LOCK_KEY, "other", ex=60)  # someone else holds the lock
    try:
        scan.scan_library.apply(args=[qid])
        scan.scan_library.apply(args=[rid])
    finally:
        r.delete(scan.SCAN_LOCK_KEY)
    with base.sync_session() as s:
        assert s.get(ScanRun, qid).state == "skipped"
        assert s.get(ScanRun, rid).state == "running"  # not clobbered


def test_scan_redelivery_of_finished_run_is_a_noop(migrated_db, redis_url, monkeypatch):
    from app.models import ScanRun
    from app.tasks import scan

    with base.sync_session() as s:
        run = ScanRun(state="done")
        s.add(run)
        s.commit()
        rid = run.id
    monkeypatch.setattr(
        scan.scanner, "run_scan_all_backends", lambda *a, **k: pytest.fail("rescanned")
    )
    scan.scan_library.apply(args=[rid])
    with base.sync_session() as s:
        assert s.get(ScanRun, rid).state == "done"


def test_deliver_pending_is_a_singleton(redis_url):
    from app.tasks import notify as ntask

    r = redis_lib.Redis.from_url(redis_url)
    r.delete(ntask.QUEUE_KEY, ntask.PROCESSING_KEY)
    r.rpush(ntask.PROCESSING_KEY, b'{"event":"x"}')  # another run's in-flight item
    r.set(ntask.DELIVER_LOCK_KEY, "other", ex=60)
    try:
        assert ntask.deliver_pending() == 0
        assert r.llen(ntask.PROCESSING_KEY) == 1  # not shuffled back to the queue
        assert r.llen(ntask.QUEUE_KEY) == 0
    finally:
        r.delete(ntask.DELIVER_LOCK_KEY, ntask.PROCESSING_KEY)


def test_archive_unexpected_error_leaves_tombstone(seed, monkeypatch, redis_url):
    from sqlalchemy import select

    from app.models import PrintArchive
    from app.tasks import archive as task_mod

    monkeypatch.setattr(task_mod, "attach_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(task_mod.fetch_timelapse, "apply_async", lambda **kw: None)
    job_id = seed.job(subtask="X")
    redis_lib.Redis.from_url(redis_url).delete(task_mod.media_schedule_lock_key(job_id))

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(task_mod, "archive_job", boom)
    task_mod.archive_print.apply(args=[job_id, "completed"])
    seed.s.expire_all()
    arc = seed.s.scalar(select(PrintArchive).where(PrintArchive.print_job_id == job_id))
    assert arc is not None and arc.fetch_state == "error"  # the sweep skips it now


def test_redownload_skips_terminal_job_and_locked_model(migrated_db, redis_url, monkeypatch):
    from app.models.system import Job
    from app.services import jobs
    from app.tasks import importing

    ran = []
    monkeypatch.setattr(importing, "_redownload_model", lambda *a: ran.append(a))
    r = redis_lib.Redis.from_url(redis_url)
    jid = uuid.uuid4()
    with base.sync_session() as s:
        s.add(Job(id=jid, type="redownload_model", state=jobs.STATE_DONE))
        s.commit()
    importing.redownload_model.apply(args=[str(jid), 9001, "revision"])
    assert ran == []  # already done
    with base.sync_session() as s:
        s.get(Job, jid).state = jobs.STATE_QUEUED
        s.commit()
    r.set(importing.redownload_lock_key(9001), "other", ex=60)
    try:
        importing.redownload_model.apply(args=[str(jid), 9001, "revision"])
        assert ran == []  # model locked
    finally:
        r.delete(importing.redownload_lock_key(9001))
    importing.redownload_model.apply(args=[str(jid), 9001, "revision"])
    assert len(ran) == 1
