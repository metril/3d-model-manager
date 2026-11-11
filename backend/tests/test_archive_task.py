from datetime import UTC, datetime, timedelta

import pytest
import redis as redis_lib
from celery.exceptions import Retry
from sqlalchemy import select

from app.models import PrintArchive, PrintJob
from app.tasks import archive as task_mod
from app.tasks.archive import archive_print, media_schedule_lock_key

pytestmark = pytest.mark.usefixtures("printer_enabled", "data_dir")


def test_archive_task_retries_then_succeeds(
    fake_adapter, sliced_3mf_bytes, seed, monkeypatch, redis_url
):
    # attach_snapshot would otherwise block on a real camera-frame wait
    # (default timeout 12s) with no camera wired up in this test.
    monkeypatch.setattr(task_mod, "attach_snapshot", lambda *a, **k: None)
    calls: list[dict] = []
    monkeypatch.setattr(task_mod.fetch_timelapse, "apply_async", lambda **kw: calls.append(kw))

    job_id = seed.job(subtask="X_plate_1")
    # Job ids restart at 1 each test (truncate + restart identity) but
    # Redis is session-shared -- clear any stale lock a previous test left
    # for the same id.
    redis_lib.Redis.from_url(redis_url).delete(media_schedule_lock_key(job_id))
    fake_adapter.files["/cache/X_plate_1.3mf"] = sliced_3mf_bytes
    fake_adapter.download_errors = 2
    # Eager mode raises Retry instead of re-running; redeliver as a broker would.
    for _ in range(2):
        with pytest.raises(Retry):
            archive_print.apply(args=[job_id, "completed"])
    archive_print.apply(args=[job_id, "completed"])
    seed.s.expire_all()
    arc = seed.s.scalar(select(PrintArchive).where(PrintArchive.print_job_id == job_id))
    assert arc.fetch_state == "done" and len(fake_adapter.downloads) == 3
    # Retry-then-success: the snapshot/timelapse follow-up is scheduled
    # exactly once, on the first attempt's failure -- not again when a
    # later retry succeeds.
    assert len(calls) == 1


def test_archive_task_has_time_limits():
    assert archive_print.soft_time_limit and archive_print.time_limit
    assert archive_print.time_limit > archive_print.soft_time_limit


def test_fetch_timelapse_has_time_limits():
    assert task_mod.fetch_timelapse.soft_time_limit == 240
    assert task_mod.fetch_timelapse.time_limit == 300


def test_archive_task_soft_timeout_records_error(seed, monkeypatch, redis_url):
    from celery.exceptions import SoftTimeLimitExceeded

    # attach_snapshot would otherwise block on a real camera-frame wait
    # with no camera wired up in this test.
    monkeypatch.setattr(task_mod, "attach_snapshot", lambda *a, **k: None)
    calls: list[dict] = []
    monkeypatch.setattr(task_mod.fetch_timelapse, "apply_async", lambda **kw: calls.append(kw))

    job_id = seed.job(subtask="X_plate_1")
    redis_lib.Redis.from_url(redis_url).delete(media_schedule_lock_key(job_id))

    def boom(*a, **k):
        raise SoftTimeLimitExceeded

    monkeypatch.setattr(task_mod, "archive_job", boom)
    archive_print.apply(args=[job_id, "completed"])
    seed.s.expire_all()
    arc = seed.s.scalar(select(PrintArchive).where(PrintArchive.print_job_id == job_id))
    assert arc.fetch_state == "error"
    # A soft-timeout is still a terminal outcome -- the snapshot/timelapse
    # follow-up (item 11) is scheduled even though the archive itself errored.
    assert len(calls) == 1


def test_archive_sweep_enqueues_unarchived_terminal_jobs(seed, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.models import PrintJob
    from app.tasks import archive as task_mod

    old = seed.job(subtask="A")
    seed.job(subtask="B")
    done = seed.job(subtask="C")
    seed.s.add(PrintArchive(print_job_id=done, result="completed", fetch_state="done"))
    for jid in (old, old + 1, done):
        seed.s.get(PrintJob, jid).finished_at = datetime.now(UTC) - timedelta(minutes=10)
    seed.s.get(PrintJob, old + 1).finished_at = datetime.now(UTC)  # too fresh
    seed.s.commit()
    calls = []
    monkeypatch.setattr(task_mod.archive_print, "apply_async", lambda args: calls.append(args))
    task_mod.archive_sweep.apply()
    assert calls == [[old, "completed"]]


def test_archive_sweep_skips_start_timeout_but_keeps_real_rejections(seed, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.models import PrintJob
    from app.models.enums import PrintJobState
    from app.services.printer_state import START_TIMEOUT_ERROR

    _, _, file_id = seed.model_file("same") if hasattr(seed, "model_file") else (None, None, None)
    good = seed.job(subtask="A", file_id=file_id)
    timed_out = seed.job(subtask="A", file_id=file_id, duration_s=None)
    rejected = seed.job(subtask="A", file_id=file_id, duration_s=None)
    seed.s.add(PrintArchive(print_job_id=good, result="completed", fetch_state="done"))
    old = datetime.now(UTC) - timedelta(minutes=10)
    for jid, err in ((timed_out, START_TIMEOUT_ERROR), (rejected, "AMS jam")):
        j = seed.s.get(PrintJob, jid)
        j.state, j.printer_error, j.finished_at = PrintJobState.FAILED, err, old
    seed.s.get(PrintJob, good).finished_at = old
    seed.s.commit()
    calls = []
    monkeypatch.setattr(task_mod.archive_print, "apply_async", lambda args: calls.append(args))
    task_mod.archive_sweep.apply()
    assert calls == [[rejected, "failed"]]
    seed.s.expire_all()
    arcs = seed.s.scalars(select(PrintArchive)).all()
    assert [(a.print_job_id, a.result) for a in arcs] == [(good, "completed")]


def test_archive_sweep_is_scheduled():
    from app.tasks.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["archive-sweep"]
    assert entry["task"] == "app.tasks.archive.archive_sweep" and entry["schedule"] == 300
    assert "dispatch-scheduled" in celery_app.conf.beat_schedule


def _sweep_ready(seed, *job_ids, age=timedelta(minutes=10)):
    for jid in job_ids:
        seed.s.get(PrintJob, jid).finished_at = datetime.now(UTC) - age
    seed.s.commit()


def test_archive_sweep_still_picks_up_old_lost_enqueue(seed, monkeypatch):
    """No age bound: a worker outage longer than a day must not lose real prints."""
    old = seed.job(subtask="A")
    _sweep_ready(seed, old, age=timedelta(days=5))
    calls = []
    monkeypatch.setattr(task_mod.archive_print, "apply_async", lambda args: calls.append(args))
    task_mod.archive_sweep.apply()
    assert calls == [[old, "completed"]]


def test_archive_sweep_skips_retention_deleted_jobs(seed, monkeypatch):
    from app.config import get_settings
    from app.services.archive_blobs import delete_archives

    job = seed.job(subtask="A")
    seed.s.add(PrintArchive(print_job_id=job, result="completed", fetch_state="done"))
    _sweep_ready(seed, job)
    arc_id = seed.s.scalar(select(PrintArchive.id))
    delete_archives(seed.s, get_settings(), [arc_id])
    calls = []
    monkeypatch.setattr(task_mod.archive_print, "apply_async", lambda args: calls.append(args))
    task_mod.archive_sweep.apply()
    assert calls == []
    seed.s.expire_all()
    assert seed.s.get(PrintJob, job) is not None  # retention keeps the job


def test_archive_print_on_dismissed_job_is_a_noop(seed, monkeypatch):
    job = seed.job(subtask="A")
    seed.s.get(PrintJob, job).archive_dismissed_at = datetime.now(UTC)
    seed.s.commit()
    monkeypatch.setattr(task_mod, "_snapshot_and_schedule_timelapse", lambda *a: None)
    archive_print.apply(args=[job, "completed"])
    seed.s.expire_all()
    assert seed.s.scalar(select(PrintArchive)) is None
    # and the error-tombstone path can't re-create it either
    from app.models.enums import ArchiveResult
    from app.services.archive import mark_archive_error

    mark_archive_error(seed.s, job, ArchiveResult.COMPLETED, "x")
    assert seed.s.scalar(select(PrintArchive)) is None
