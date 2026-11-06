"""Per-object geometry of a 3MF for the plate editor.

Each ``<build><item>`` of a multi-object 3MF becomes its own GLB (Y-up, on a
single bed, colours baked) plus a JSON listing, generated lazily from the
source file and cached under the blob's derivative directory. Bump
``OBJECTS_V`` to regenerate (the version is stored in the listing).
"""

from __future__ import annotations

import fcntl
import json
import logging
from contextlib import contextmanager
from pathlib import Path

from app.config import Settings
from app.pipeline import convert, plates3mf
from app.services import derivatives

OBJECTS_V = 3

logger = logging.getLogger(__name__)


@contextmanager
def _blob_lock(settings: Settings, blob_hash: str):
    """Exclusive per-blob lock (across API workers and Celery processes) so two
    requests never generate the same assets at once."""
    lock = derivatives.objects_json_path(settings, blob_hash).with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _clear_object_glbs(settings: Settings, blob_hash: str) -> None:
    """Drop every per-object GLB (and listing) left by an older ``OBJECTS_V``."""
    listing = derivatives.objects_json_path(settings, blob_hash)
    for path in listing.parent.glob(f"{blob_hash}.object_*.glb"):
        path.unlink(missing_ok=True)
    listing.unlink(missing_ok=True)


def _describe(piece: plates3mf.ObjectPiece) -> dict:
    lo, hi = piece.mesh.bounds
    return {
        "index": piece.index,
        "plate": piece.plate,
        "name": piece.name,
        "x_mm": round(float((lo[0] + hi[0]) / 2), 3),
        "y_mm": round(float((lo[1] + hi[1]) / 2), 3),
        "w_mm": round(float(hi[0] - lo[0]), 3),
        "d_mm": round(float(hi[1] - lo[1]), 3),
        "h_mm": round(float(hi[2] - lo[2]), 3),
    }


def read_cached(settings: Settings, blob_hash: str) -> list[dict] | None:
    path = derivatives.objects_json_path(settings, blob_hash)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("v") != OBJECTS_V:
        return None
    return data.get("objects")


def ensure_objects(settings: Settings, blob_hash: str, src: Path) -> list[dict]:
    """The object listing for ``src`` (``[]`` = single object / not splittable),
    writing the GLBs on first use under a per-blob lock. Blocking."""
    cached = read_cached(settings, blob_hash)
    if cached is not None:
        return cached
    with _blob_lock(settings, blob_hash):
        cached = read_cached(settings, blob_hash)  # another worker may have finished
        if cached is not None:
            return cached
        _clear_object_glbs(settings, blob_hash)
        pieces = plates3mf.object_meshes(src)
        for piece in pieces:
            data = convert.to_y_up(piece.mesh).export(file_type="glb")
            derivatives.publish_bytes(
                data, derivatives.object_glb_path(settings, blob_hash, piece.index)
            )
        objects = [_describe(p) for p in pieces]
        derivatives.publish_bytes(
            json.dumps({"v": OBJECTS_V, "objects": objects}).encode(),
            derivatives.objects_json_path(settings, blob_hash),
        )
        return objects
