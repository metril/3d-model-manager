"""Layer-based timelapse (W5): one camera frame per layer change instead of a
printer-recorded time-lapse.

printerd (``PrinterWorker._layer_tick``) keeps a camera viewer registered while
a layer-timelapse printer is printing and calls ``capture`` on every status
report; ``capture`` stores a frame only when the layer number advanced, so each
layer is captured once. The frames live in a Redis list keyed by job until the
archive step stitches them (``assemble_for``) into the archive's timelapse.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import PrintArchive
from app.models.enums import BlobFormat, BlobKind
from app.services import camera_state
from app.services.archive import _ensure_blob, archive_path
from app.services.derivatives import publish_bytes

VIEWER_ID = "layer-timelapse"
FRAMES_TTL_S = 24 * 3600
MAX_FRAMES = 3000
FPS = 30
_SECONDS_PER_FRAME = 0.5
_MIN_STITCH_S = 120.0
_MAX_STITCH_S = 780.0  # inside the task's own soft limit


def frames_key(printer_id: int, job_id: int) -> str:
    return f"printer:{printer_id}:layertl:{job_id}:frames"


def last_key(printer_id: int, job_id: int) -> str:
    return f"printer:{printer_id}:layertl:{job_id}:last"


def enabled(options: dict | None) -> bool:
    return bool((options or {}).get("layer_timelapse"))


def hold_camera(r, printer_id: int) -> None:
    """Keep a viewer registered so the supervisor runs a camera worker."""
    camera_state.add_viewer(r, printer_id, VIEWER_ID)


def release_camera(r, printer_id: int) -> None:
    camera_state.remove_viewer(r, printer_id, VIEWER_ID)


def capture(r, printer_id: int, job_id: int, layer: int | None) -> bool:
    """Store the current camera frame iff ``layer`` is a new, higher layer.
    A missing frame leaves the layer uncaptured so the next report retries."""
    if not layer or layer < 1:
        return False
    last = r.get(last_key(printer_id, job_id))
    if last is not None and layer <= int(last):
        return False
    if r.llen(frames_key(printer_id, job_id)) >= MAX_FRAMES:
        return False
    frame = camera_state.get_frame(r, printer_id)
    if not frame:
        return False
    fk, lk = frames_key(printer_id, job_id), last_key(printer_id, job_id)
    pipe = r.pipeline()
    pipe.rpush(fk, frame)
    pipe.set(lk, layer)
    pipe.expire(fk, FRAMES_TTL_S)
    pipe.expire(lk, FRAMES_TTL_S)
    pipe.execute()
    return True


def frame_count(r, printer_id: int, job_id: int) -> int:
    return int(r.llen(frames_key(printer_id, job_id)))


def clear(r, printer_id: int, job_id: int) -> None:
    r.delete(frames_key(printer_id, job_id), last_key(printer_id, job_id))


def stitch(frames: list[bytes], dest: Path, *, fps: int = FPS) -> bool:
    """Encode JPEG ``frames`` to an H.264 mp4. False on ffmpeg failure."""
    if not frames:
        return False
    timeout = min(max(_MIN_STITCH_S, len(frames) * _SECONDS_PER_FRAME), _MAX_STITCH_S)
    with tempfile.TemporaryDirectory(prefix="tdmm-ltl-") as td:
        for i, f in enumerate(frames):
            (Path(td) / f"{i:06d}.jpg").write_bytes(f)
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-y",
                    "-framerate",
                    str(fps),
                    "-i",
                    str(Path(td) / "%06d.jpg"),
                    "-vf",
                    "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(dest),
                ],  # fmt: skip
                check=True,
                capture_output=True,
                timeout=timeout,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
    return True


def assemble_for(session: Session, settings: Settings, r, print_job_id: int) -> bool:
    """Stitch the job's captured frames into the archive timelapse. True when
    stored; False leaves the archive untouched (caller falls back to the
    printer's SD timelapse). Idempotent once ``timelapse_blob`` is set."""
    from app.models import PrintJob

    job = session.get(PrintJob, print_job_id)
    arc = session.scalars(
        select(PrintArchive).where(PrintArchive.print_job_id == print_job_id)
    ).first()
    if job is None or arc is None:
        return False
    if arc.timelapse_blob:
        return True
    frames = r.lrange(frames_key(job.printer_id, job.id), 0, -1)
    if not frames:
        return False
    last = r.get(last_key(job.printer_id, job.id))
    with tempfile.TemporaryDirectory(prefix="tdmm-ltl-out-") as td:
        out = Path(td) / "timelapse.mp4"
        if not stitch(list(frames), out):
            return False
        data = out.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    publish_bytes(data, archive_path(settings, sha, "mp4"))
    _ensure_blob(session, sha, len(data), BlobKind.VIDEO, BlobFormat.MP4)
    arc.timelapse_blob = sha
    arc.timelapse_frames = len(frames)
    arc.timelapse_layers = int(last) if last is not None else len(frames)
    session.commit()
    clear(r, job.printer_id, job.id)
    return True
