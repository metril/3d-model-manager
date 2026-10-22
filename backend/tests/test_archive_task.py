import pytest
import redis as redis_lib
from celery.exceptions import Retry
from sqlalchemy import select

from app.models import PrintArchive
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


def test_archive_sweep_is_scheduled():
    from app.tasks.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["archive-sweep"]
    assert entry["task"] == "app.tasks.archive.archive_sweep" and entry["schedule"] == 300
    assert "dispatch-scheduled" in celery_app.conf.beat_schedule
