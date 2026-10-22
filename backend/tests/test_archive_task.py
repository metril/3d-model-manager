import pytest
from celery.exceptions import Retry
from sqlalchemy import select

from app.models import PrintArchive
from app.tasks.archive import archive_print

pytestmark = pytest.mark.usefixtures("printer_enabled", "data_dir")


def test_archive_task_retries_then_succeeds(fake_adapter, sliced_3mf_bytes, seed):
    job_id = seed.job(subtask="X_plate_1")
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


def test_archive_task_has_time_limits():
    assert archive_print.soft_time_limit and archive_print.time_limit
    assert archive_print.time_limit > archive_print.soft_time_limit


def test_archive_task_soft_timeout_records_error(seed, monkeypatch):
    from celery.exceptions import SoftTimeLimitExceeded

    from app.tasks import archive as task_mod

    job_id = seed.job(subtask="X_plate_1")

    def boom(*a, **k):
        raise SoftTimeLimitExceeded

    monkeypatch.setattr(task_mod, "archive_job", boom)
    archive_print.apply(args=[job_id, "completed"])
    seed.s.expire_all()
    arc = seed.s.scalar(select(PrintArchive).where(PrintArchive.print_job_id == job_id))
    assert arc.fetch_state == "error"


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
