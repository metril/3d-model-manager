"""Archive media (P4 camera plan Task 7): the run's camera snapshot and
printer-captured timelapse, attached to the ``print_archives`` row
``archive_job`` (P2) already wrote.

``attach_snapshot`` runs inline from ``archive_print`` right after the 3MF
archive step, regardless of whether that step succeeded -- a camera frame of
the bed is useful even when the sliced file couldn't be located. It never
raises: a missing/unreachable camera just leaves ``media_note`` set.

``fetch_timelapse_for`` is the retried half -- printers only finalize the
timelapse file to SD some time after the print ends, so it's fetched by a
separate, delayed task (``app.tasks.archive.fetch_timelapse``). It probes
both known timelapse directories (layouts vary by firmware), tolerating
either one being absent, and picks the newest candidate no older than the
print's own start time (minus a small grace window for clock skew).
"""

from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import PrintArchive, Printer, PrintJob
from app.models.enums import BlobFormat, BlobKind
from app.printers.base import RemoteFile
from app.printers.connection import connection_from_printer
from app.printers.registry import build_adapter
from app.services import camera_state as cs
from app.services.archive import _ensure_blob, archive_path
from app.services.derivatives import publish_bytes

TIMELAPSE_DIRS: tuple[str, ...] = ("/timelapse", "/timelapse/video")
_TIMELAPSE_EXTS = (".mp4", ".avi")
# LIST mtimes are treated as UTC; a print's upload/start and the printer's
# own clock can disagree by a bit, so candidates up to 2 minutes BEFORE the
# job started still count (SD card finalization lag, not a stale leftover).
_START_GRACE_S = 120


def pick_timelapse(
    entries: list[tuple[str, RemoteFile]], started_at: datetime
) -> tuple[str, RemoteFile] | None:
    """The newest ``(dir, file)`` among ``entries`` that looks like a video
    and wasn't finalized before this print started (within the grace
    window). ``None`` if nothing qualifies."""
    cutoff = started_at - timedelta(seconds=_START_GRACE_S)
    candidates = [
        (d, f)
        for d, f in entries
        if Path(f.name).suffix.lower() in _TIMELAPSE_EXTS
        and f.mtime is not None
        and f.mtime >= cutoff
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda df: df[1].mtime)


def attach_snapshot(
    session: Session,
    settings: Settings,
    r,
    job: PrintJob,
    arc: PrintArchive,
    *,
    timeout: float = 12.0,
) -> None:
    """Store the per-job camera frame the supervisor copied at the job's
    terminal transition (``camera_state.keep_job_frame``), falling back to
    waiting briefly for a live frame when that copy is missing (e.g. the
    supervisor hadn't started a worker yet). Idempotent: a no-op once
    ``arc.snapshot_blob`` is already set."""
    if arc.snapshot_blob:
        return
    frame = r.get(cs.job_frame_key(job.printer_id, job.id))
    if frame is None:
        frame = cs.wait_for_frame(r, job.printer_id, timeout=timeout, viewer_id=f"archive:{job.id}")
    if frame is None:
        arc.media_note = "no camera frame"
        return
    sha = hashlib.sha256(frame).hexdigest()
    publish_bytes(frame, archive_path(settings, sha, "jpg"))
    _ensure_blob(session, sha, len(frame), BlobKind.IMAGE, BlobFormat.JPG)
    arc.snapshot_blob = sha


def fetch_timelapse_for(
    session: Session, settings: Settings, print_job_id: int
) -> Literal["stored", "retry", "none"]:
    """One attempt at fetching the printer's timelapse for ``print_job_id``.

    ``"stored"``: already had one, or just fetched and stored it now.
    ``"retry"``: listing worked but no qualifying file exists yet (the
    printer may still be finalizing it) -- worth trying again later.
    ``"none"``: nothing to do (job/archive/printer missing, no start time,
    or the adapter can't list files at all) -- retrying would never help.
    """
    job = session.get(PrintJob, print_job_id)
    if job is None or job.started_at is None:
        return "none"
    arc = session.scalars(select(PrintArchive).where(PrintArchive.print_job_id == job.id)).first()
    if arc is None:
        return "none"
    if arc.timelapse_blob is not None:
        return "stored"
    printer = session.get(Printer, job.printer_id)
    if printer is None:
        return "none"
    conn = connection_from_printer(settings, printer)
    adapter = build_adapter(printer.kind, conn)
    try:
        entries: list[tuple[str, RemoteFile]] = []
        any_listed = False
        for d in TIMELAPSE_DIRS:
            try:
                files = adapter.list_files(d)
            except (OSError, NotImplementedError):
                continue
            any_listed = True
            entries.extend((d, f) for f in files)
        if not any_listed:
            return "none"
        picked = pick_timelapse(entries, job.started_at)
        if picked is None:
            return "retry"
        d, rf = picked
        remote = f"{d.rstrip('/')}/{rf.name}"
        with tempfile.TemporaryDirectory(prefix="tdmm-tl-") as td:
            dest = Path(td) / rf.name
            try:
                adapter.download_file(remote, dest)
            except (OSError, FileNotFoundError):
                return "retry"
            data = dest.read_bytes()
    finally:
        adapter.close()
    sha = hashlib.sha256(data).hexdigest()
    ext = Path(rf.name).suffix.lstrip(".").lower()
    fmt = BlobFormat.MP4 if ext == "mp4" else BlobFormat.AVI
    publish_bytes(data, archive_path(settings, sha, ext))
    _ensure_blob(session, sha, len(data), BlobKind.VIDEO, fmt)
    arc.timelapse_blob = sha
    session.commit()
    return "stored"
