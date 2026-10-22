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
