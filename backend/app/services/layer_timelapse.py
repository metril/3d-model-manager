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
import shutil
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.models.enums import BlobFormat, BlobKind
from app.services import camera_state
from app.services.archive import _ensure_blob, archive_path
from app.services.archive_lookup import archive_for_job
from app.services.derivatives import publish_bytes

VIEWER_ID = "layer-timelapse"
FRAMES_TTL_S = 24 * 3600
MAX_FRAMES = 5000
FPS = 30
_SECONDS_PER_FRAME = 0.5
_MIN_STITCH_S = 120.0
_MAX_STITCH_S = 780.0  # inside the task's own soft limit


def frames_dir(settings: Settings, printer_id: int, job_id: int) -> Path:
    return settings.data_dir / "layer_timelapse" / str(printer_id) / str(job_id)


def last_key(printer_id: int, job_id: int) -> str:
    return f"printer:{printer_id}:layertl:{job_id}:last"


def enabled(options: dict | None) -> bool:
    return bool((options or {}).get("layer_timelapse"))


def hold_camera(r, printer_id: int) -> None:
    """Keep a viewer registered so the supervisor runs a camera worker."""
    camera_state.add_viewer(r, printer_id, VIEWER_ID)


def release_camera(r, printer_id: int) -> None:
    camera_state.remove_viewer(r, printer_id, VIEWER_ID)


def frame_count(settings: Settings, printer_id: int, job_id: int) -> int:
    d = frames_dir(settings, printer_id, job_id)
    return len(list(d.glob("*.jpg"))) if d.is_dir() else 0


def capture(r, settings: Settings, printer_id: int, job_id: int, layer: int | None) -> bool:
    """Write the current camera frame to disk iff ``layer`` is a new, higher
    layer (Redis holds only the last-layer counter). A missing frame leaves the
    layer uncaptured so the next report retries."""
    if not layer or layer < 1:
        return False
    last = r.get(last_key(printer_id, job_id))
    if last is not None and layer <= int(last):
        return False
    frame = camera_state.get_frame(r, printer_id)
    if not frame:
        return False
    d = frames_dir(settings, printer_id, job_id)
    d.mkdir(parents=True, exist_ok=True)
    n = frame_count(settings, printer_id, job_id)
    if n >= MAX_FRAMES:
        return False
    tmp = d / f".{n + 1:06d}.part"
    tmp.write_bytes(frame)
    tmp.rename(d / f"{n + 1:06d}.jpg")
    r.set(last_key(printer_id, job_id), layer, ex=FRAMES_TTL_S)
    return True


def clear(r, settings: Settings, printer_id: int, job_id: int) -> None:
    r.delete(last_key(printer_id, job_id))
    shutil.rmtree(frames_dir(settings, printer_id, job_id), ignore_errors=True)


def stitch(src: Path, n_frames: int, dest: Path, *, fps: int = FPS) -> bool:
    """Encode the ``%06d.jpg`` frames in ``src`` to an H.264 mp4 (ffmpeg reads
    them from disk). False on ffmpeg failure."""
    if n_frames < 1:
        return False
    timeout = min(max(_MIN_STITCH_S, n_frames * _SECONDS_PER_FRAME), _MAX_STITCH_S)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-framerate",
                str(fps),
                "-i",
                str(src / "%06d.jpg"),
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
    arc = archive_for_job(session, print_job_id)
    if job is None or arc is None:
        return False
    if arc.print_job_id != job.id or arc.timelapse_blob:
        return True  # folded into a later run (media is the latest's), or done
    n = frame_count(settings, job.printer_id, job.id)
    if n == 0:
        return False
    last = r.get(last_key(job.printer_id, job.id))
    with tempfile.TemporaryDirectory(prefix="tdmm-ltl-out-") as td:
        out = Path(td) / "timelapse.mp4"
        if not stitch(frames_dir(settings, job.printer_id, job.id), n, out):
            return False
        data = out.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    publish_bytes(data, archive_path(settings, sha, "mp4"))
    _ensure_blob(session, sha, len(data), BlobKind.VIDEO, BlobFormat.MP4)
    arc.timelapse_blob = sha
    arc.timelapse_frames = n
    arc.timelapse_layers = int(last) if last is not None else n
    session.commit()
    return True
