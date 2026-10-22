"""Archive media tests (P4 camera plan Task 7): snapshot attach, timelapse
list/pick/fetch, ffmpeg transcode."""

from __future__ import annotations

import hashlib
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
import redis as redis_lib

from app.config import get_settings
from app.models import PrintArchive, PrintJob
from app.models.enums import ArchiveFetchState, ArchiveResult
from app.printers.bambu import parse_list_line
from app.printers.base import RemoteFile
from app.services import camera_state as cs
from app.services.archive_media import attach_snapshot, fetch_timelapse_for, pick_timelapse

pytestmark = pytest.mark.usefixtures("printer_enabled", "data_dir")

JPEG = b"\xff\xd8\xff\xe0" + bytes(10) + b"\xff\xd9"


def _archive_row(seed, job_id: int) -> PrintArchive:
    arc = PrintArchive(
        print_job_id=job_id, result=ArchiveResult.COMPLETED, fetch_state=ArchiveFetchState.DONE
    )
    seed.s.add(arc)
    seed.s.commit()
    return arc


# -- parse_list_line -----------------------------------------------------


def test_parse_list_line_variants():
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

    # No year -> current year.
    rf = parse_list_line("-rwxrwxrwx 1 root root 1234 Oct 22 14:03 a.avi", now)
    assert rf == RemoteFile("a.avi", 1234, datetime(2026, 10, 22, 14, 3, tzinfo=UTC))

    # No year, but naively landing in the future -> previous year.
    rf = parse_list_line("-rwxrwxrwx 1 root root 1234 Dec 25 10:00 b.avi", now)
    assert rf.mtime == datetime(2025, 12, 25, 10, 0, tzinfo=UTC)

    # Explicit year column (old file, no time-of-day).
    rf = parse_list_line("-rwxrwxrwx 1 root root 1234 Dec 31  2025 c.avi", now)
    assert rf == RemoteFile("c.avi", 1234, datetime(2025, 12, 31, tzinfo=UTC))

    # Unparseable month -> fallback to a timestamp embedded in the filename.
    rf = parse_list_line(
        "-rwxrwxrwx 1 root root 1234 ??? ?? ????? ipcam_2026-10-22_14-03-05.mp4", now
    )
    assert rf.mtime == datetime(2026, 10, 22, 14, 3, 5, tzinfo=UTC)

    # Too few columns, and the "." / ".." entries -> None.
    assert parse_list_line("drwxr-xr-x 2 root root 10 Jan 1 2024 .", now) is None
    assert parse_list_line("short line", now) is None


# -- pick_timelapse --------------------------------------------------------


def test_pick_newest_after_start():
    t0 = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    e = [
        ("/timelapse", RemoteFile("old.avi", 1, t0 - timedelta(hours=2))),
        ("/timelapse/video", RemoteFile("a.avi", 1, t0 + timedelta(minutes=30))),
        ("/timelapse/video", RemoteFile("b.avi", 1, t0 + timedelta(minutes=40))),
        ("/timelapse", RemoteFile("c.txt", 1, t0 + timedelta(hours=1))),
    ]
    assert pick_timelapse(e, t0)[1].name == "b.avi"
    assert pick_timelapse(e[:1], t0) is None


def test_pick_timelapse_rejects_unsafe_names():
    t0 = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    for bad_name in ("/abs.mp4", "../x.avi", ".hidden.mp4"):
        e = [("/timelapse", RemoteFile(bad_name, 1, t0 + timedelta(minutes=1)))]
        assert pick_timelapse(e, t0) is None


# -- fetch_timelapse_for ----------------------------------------------------


def test_fetch_tries_both_dirs_when_one_errors(fake_adapter, seed):
    job_id = seed.job(subtask="X", duration_s=600)
    job = seed.s.get(PrintJob, job_id)
    arc = _archive_row(seed, job_id)
    fake_adapter.files["/timelapse/video/newest.avi"] = b"video-bytes"
    fake_adapter.file_mtimes["/timelapse/video/newest.avi"] = job.started_at + timedelta(minutes=5)

    real_list_files = fake_adapter.list_files

    def flaky(path):
        if path == "/timelapse":
            raise OSError("listing /timelapse failed")
        return real_list_files(path)

    fake_adapter.list_files = flaky

    outcome = fetch_timelapse_for(seed.s, get_settings(), job_id)
    assert outcome == "stored"
    seed.s.refresh(arc)
    assert arc.timelapse_blob == hashlib.sha256(b"video-bytes").hexdigest()


def test_fetch_none_retries_then_notes(fake_adapter, seed):
    job_id = seed.job(subtask="Y", duration_s=600)
    arc = _archive_row(seed, job_id)
    # No timelapse files at all: both dirs list fine but empty -> "retry".
    outcome = fetch_timelapse_for(seed.s, get_settings(), job_id)
    assert outcome == "retry"

    from celery.exceptions import Retry

    from app.tasks.archive import fetch_timelapse

    with pytest.raises(Retry):
        fetch_timelapse.apply(args=[job_id], retries=0)
    with pytest.raises(Retry):
        fetch_timelapse.apply(args=[job_id], retries=2)
    # Exhausted (retries == max_retries) -> noted instead of retried again.
    result = fetch_timelapse.apply(args=[job_id], retries=3).get()
    assert result == "none"
    seed.s.expire_all()
    seed.s.refresh(arc)
    assert arc.media_note == "no timelapse found"


# -- transcode_timelapse ----------------------------------------------------


def _stored_avi_archive(seed, fake_adapter) -> tuple[int, PrintArchive]:
    job_id = seed.job(subtask="Z", duration_s=600)
    job = seed.s.get(PrintJob, job_id)
    arc = _archive_row(seed, job_id)
    fake_adapter.files["/timelapse/run.avi"] = b"avi-bytes"
    fake_adapter.file_mtimes["/timelapse/run.avi"] = job.started_at + timedelta(minutes=1)
    outcome = fetch_timelapse_for(seed.s, get_settings(), job_id)
    assert outcome == "stored"
    seed.s.refresh(arc)
    assert arc.timelapse_blob is not None
    return arc.id, arc


def test_transcode_without_ffmpeg_keeps_avi_and_notes(fake_adapter, seed, monkeypatch):
    from app.tasks import media as media_mod

    archive_id, arc = _stored_avi_archive(seed, fake_adapter)
    old_sha = arc.timelapse_blob
    monkeypatch.setattr(media_mod.shutil, "which", lambda name: None)

    result = media_mod.transcode_timelapse.apply(args=[archive_id]).get()
    assert result == "skipped"
    seed.s.expire_all()
    arc2 = seed.s.get(PrintArchive, archive_id)
    assert arc2.timelapse_blob == old_sha
    assert arc2.media_note == "ffmpeg unavailable; original .avi kept"
    assert arc2.fetch_state == ArchiveFetchState.DONE


def test_transcode_swaps_to_mp4(fake_adapter, seed, monkeypatch):
    from app.tasks import media as media_mod

    archive_id, arc = _stored_avi_archive(seed, fake_adapter)
    old_sha = arc.timelapse_blob

    monkeypatch.setattr(media_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    seen_cmds = []

    def fake_run(cmd, **kwargs):
        seen_cmds.append(cmd)
        dest = cmd[-1]
        with open(dest, "wb") as fh:
            fh.write(b"mp4-bytes")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(media_mod.subprocess, "run", fake_run)

    result = media_mod.transcode_timelapse.apply(args=[archive_id]).get()
    assert result == "transcoded"
    assert seen_cmds[0] == [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i",
        seen_cmds[0][4],
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        seen_cmds[0][-1],
    ]
    seed.s.expire_all()
    arc2 = seed.s.get(PrintArchive, archive_id)
    assert arc2.timelapse_blob == hashlib.sha256(b"mp4-bytes").hexdigest()
    assert arc2.timelapse_blob != old_sha
    from app.models.library import Blob

    assert seed.s.get(Blob, old_sha) is None


# -- attach_snapshot ---------------------------------------------------------


def test_attach_snapshot_prefers_job_frame(seed, redis_url):
    job_id = seed.job(subtask="S1")
    job = seed.s.get(PrintJob, job_id)
    arc = _archive_row(seed, job_id)
    r = redis_lib.Redis.from_url(redis_url)
    r.set(cs.job_frame_key(job.printer_id, job.id), JPEG)

    attach_snapshot(seed.s, get_settings(), r, job, arc)
    assert arc.snapshot_blob == hashlib.sha256(JPEG).hexdigest()
    assert arc.media_note is None


def test_attach_snapshot_waits_for_live_frame(seed, redis_url):
    job_id = seed.job(subtask="S2")
    job = seed.s.get(PrintJob, job_id)
    arc = _archive_row(seed, job_id)
    r = redis_lib.Redis.from_url(redis_url)
    r2 = redis_lib.Redis.from_url(redis_url)

    def putter() -> None:
        time.sleep(0.3)
        cs.put_frame(r2, job.printer_id, JPEG)

    t = threading.Thread(target=putter)
    t.start()
    try:
        attach_snapshot(seed.s, get_settings(), r, job, arc, timeout=3.0)
    finally:
        t.join()
    assert arc.snapshot_blob == hashlib.sha256(JPEG).hexdigest()


def test_attach_snapshot_no_frame_notes(seed, redis_url):
    job_id = seed.job(subtask="S3")
    job = seed.s.get(PrintJob, job_id)
    arc = _archive_row(seed, job_id)
    r = redis_lib.Redis.from_url(redis_url)
    # Printer ids are reused across tests (fresh DB per test); a frame left
    # live (FRAME_TTL_S) by test_attach_snapshot_waits_for_live_frame could
    # otherwise leak into this one.
    r.delete(cs.frame_key(job.printer_id), cs.job_frame_key(job.printer_id, job.id))

    attach_snapshot(seed.s, get_settings(), r, job, arc, timeout=0.2)
    assert arc.snapshot_blob is None
    assert arc.media_note == "no camera frame"


# -- task wiring --------------------------------------------------------


def test_archive_print_enqueues_timelapse_fetch(
    fake_adapter, sliced_3mf_bytes, seed, monkeypatch, redis_url
):
    from app.tasks import archive as task_mod

    job_id = seed.job(subtask="X_plate_1")
    # Job ids restart at 1 each test but Redis is session-shared -- clear
    # any stale media-schedule lock a previous test left for the same id.
    redis_lib.Redis.from_url(redis_url).delete(task_mod.media_schedule_lock_key(job_id))
    fake_adapter.files["/cache/X_plate_1.3mf"] = sliced_3mf_bytes
    monkeypatch.setattr(task_mod, "attach_snapshot", lambda *a, **k: None)
    calls: list[dict] = []
    monkeypatch.setattr(task_mod.fetch_timelapse, "apply_async", lambda **kw: calls.append(kw))

    task_mod.archive_print.apply(args=[job_id, "completed"])
    assert calls == [{"args": [job_id], "countdown": 90}]


def test_media_routes_to_cpu_queue():
    from app.tasks.celery_app import celery_app

    route = celery_app.amqp.router.route({}, "app.tasks.media.transcode_timelapse")
    assert route["queue"].name == "cpu"
