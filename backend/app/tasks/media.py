"""ffmpeg transcode of a fetched timelapse (P4 camera plan Task 7). cpu
queue (``celery_app`` routes ``app.tasks.media.*`` there) -- ffmpeg is a
real CPU-bound subprocess, unlike the io-queue fetch that downloaded the
source .avi.

Never fails the archive: a missing ffmpeg binary or a failed subprocess
just leaves the .avi in place with ``media_note`` explaining why.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.models.enums import BlobFormat, BlobKind
from app.models.library import Blob
from app.models.printing import PrintArchive
from app.services.archive import _ensure_blob, archive_path
from app.services.derivatives import publish_bytes
from app.tasks import base
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_FFMPEG_TIMEOUT_S = 840  # inside the task's own 900s soft_time_limit


@celery_app.task(name="app.tasks.media.transcode_timelapse", soft_time_limit=900, time_limit=960)
def transcode_timelapse(archive_id: int) -> str:
    settings = get_settings()
    if shutil.which("ffmpeg") is None:
        with base.sync_session() as session:
            arc = session.get(PrintArchive, archive_id)
            if arc is not None:
                arc.media_note = "ffmpeg unavailable; original .avi kept"
                session.commit()
        return "skipped"

    with base.sync_session() as session:
        arc = session.get(PrintArchive, archive_id)
        if arc is None or arc.timelapse_blob is None:
            return "skipped"
        old_sha = arc.timelapse_blob
        blob = session.get(Blob, old_sha)
        if blob is None or blob.format != BlobFormat.AVI:
            return "skipped"
        src = archive_path(settings, old_sha, "avi")

    try:
        with tempfile.TemporaryDirectory(prefix="tdmm-transcode-") as td:
            dest = Path(td) / "timelapse.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-y",
                    "-i",
                    str(src),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(dest),
                ],
                check=True,
                capture_output=True,
                timeout=_FFMPEG_TIMEOUT_S,
            )
            data = dest.read_bytes()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        with base.sync_session() as session:
            arc = session.get(PrintArchive, archive_id)
            if arc is not None:
                arc.media_note = f"transcode failed: {type(e).__name__}"
                session.commit()
        return "error"

    new_sha = hashlib.sha256(data).hexdigest()
    publish_bytes(data, archive_path(settings, new_sha, "mp4"))
    with base.sync_session() as session:
        arc = session.get(PrintArchive, archive_id)
        if arc is None:
            return "error"
        _ensure_blob(session, new_sha, len(data), BlobKind.VIDEO, BlobFormat.MP4)
        arc.timelapse_blob = new_sha
        session.commit()
        # Only drop the old .avi (file + blob row) once nothing else points
        # at it -- a blob is content-addressed and could, in principle, be
        # shared by another archive row.
        still_used = session.scalars(
            select(PrintArchive).where(PrintArchive.timelapse_blob == old_sha)
        ).first()
        if still_used is None:
            old_blob = session.get(Blob, old_sha)
            if old_blob is not None:
                session.delete(old_blob)
            session.commit()
    if still_used is None:
        archive_path(settings, old_sha, "avi").unlink(missing_ok=True)
    return "transcoded"


def _set_job(job_id: str, **fields: object) -> None:
    import json

    import redis

    r = redis.Redis.from_url(get_settings().redis_url)
    try:
        key = f"tl:job:{job_id}"
        raw = r.get(key)
        data = json.loads(raw) if raw else {}
        data.update(fields)
        r.set(key, json.dumps(data), ex=86400)
    finally:
        r.close()


@celery_app.task(name="app.tasks.media.timelapse_edit", soft_time_limit=900, time_limit=960)
def timelapse_edit(
    archive_id: int,
    job_id: str,
    trim_start: float,
    trim_end: float | None,
    speed: float,
    save_mode: str,
    user_id: int | None = None,
) -> str:
    """T7 timelapse editor: trim (``-ss``/``-to``) + speed (``setpts``), then
    either replace the archive's timelapse or append it to ``photos``."""
    import secrets
    from datetime import UTC, datetime

    from app.services.archive_blobs import release_blobs, unlink_all

    settings = get_settings()
    _set_job(job_id, state="running")
    if shutil.which("ffmpeg") is None:
        _set_job(job_id, state="error", error="ffmpeg unavailable")
        return "error"
    with base.sync_session() as session:
        arc = session.get(PrintArchive, archive_id)
        blob = session.get(Blob, arc.timelapse_blob) if arc and arc.timelapse_blob else None
        if blob is None:
            _set_job(job_id, state="error", error="no timelapse")
            return "error"
        src = archive_path(settings, blob.hash, blob.format.value)
    try:
        with tempfile.TemporaryDirectory(prefix="tdmm-tledit-") as td:
            dest = Path(td) / "edit.mp4"
            cmd = ["ffmpeg", "-nostdin", "-y", "-ss", f"{trim_start:.3f}"]
            if trim_end is not None:
                cmd += ["-to", f"{trim_end:.3f}"]
            cmd += [
                "-i", str(src), "-an",
                "-vf", f"setpts=PTS/{speed:.4f}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(dest),
            ]  # fmt: skip
            subprocess.run(cmd, check=True, capture_output=True, timeout=_FFMPEG_TIMEOUT_S)
            data = dest.read_bytes()
        if not data:
            raise ValueError("empty output")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as e:
        _set_job(job_id, state="error", error=f"ffmpeg failed: {type(e).__name__}")
        return "error"

    new_sha = hashlib.sha256(data).hexdigest()
    publish_bytes(data, archive_path(settings, new_sha, "mp4"))
    paths: list[Path] = []
    filename = None
    with base.sync_session() as session:
        arc = session.get(PrintArchive, archive_id)
        if arc is None:
            _set_job(job_id, state="error", error="archive gone")
            return "error"
        _ensure_blob(session, new_sha, len(data), BlobKind.VIDEO, BlobFormat.MP4)
        if save_mode == "new":
            dup = next((x for x in (arc.photos or []) if x.get("blob") == new_sha), None)
            if dup is not None:  # redelivery: already appended
                _set_job(job_id, state="done", filename=dup["filename"])
                return "done"
            filename = f"edit-{secrets.token_hex(6)}.mp4"
            arc.photos = [
                *(arc.photos or []),
                {
                    "filename": filename,
                    "blob": new_sha,
                    "kind": "timelapse",
                    "uploaded_by": user_id,
                    "at": datetime.now(UTC).isoformat(),
                },
            ]
        else:
            old = arc.timelapse_blob
            arc.timelapse_blob = new_sha
            session.flush()
            if old and old != new_sha:
                _freed, paths = release_blobs(session, settings, {old})
        session.commit()
    unlink_all(paths)
    _set_job(job_id, state="done", filename=filename)
    return "done"
