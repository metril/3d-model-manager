"""Archive source attachments: the editable project behind a print.

Two slots per archive -- ``source`` (slicer project / CAD export: 3MF, STEP,
...) and ``f3d`` (Fusion or any other native CAD file). Bytes live in the
archive blob store (content-addressed sha256), independent of the library, so
a later model purge never breaks an archive. When the archive's file came from
a library model the source is linked automatically (:func:`link_from_model`),
skipping trashed models. Sync: callers are the archive worker and, via a
thread, the API.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import File, Model, PrintArchive
from app.models.enums import BlobFormat, BlobKind
from app.models.library import Blob, Revision
from app.services.archive import _ensure_blob, archive_path
from app.services.archive_blobs import blob_ext, release_blobs, unlink_all
from app.services.derivatives import publish_bytes, publish_file
from app.services.storage_backends import resolve_backend_for_file_sync

log = logging.getLogger(__name__)

Slot = Literal["source", "f3d"]
SLOTS: tuple[Slot, ...] = ("source", "f3d")
MAX_SOURCE_BYTES = 256 * 1024 * 1024

_EXT_FORMAT = {
    "3mf": BlobFormat.THREEMF,
    "step": BlobFormat.STEP,
    "stp": BlobFormat.STEP,
    "stl": BlobFormat.STL,
    "obj": BlobFormat.OBJ,
    "iges": BlobFormat.IGES,
    "igs": BlobFormat.IGES,
}
_F3D_EXTS = {".f3d", ".f3z"}


def clean_name(name: str | None) -> str:
    base = PurePosixPath((name or "").replace("\\", "/")).name.strip()
    return (base or "source")[:200]


def _fmt_for(name: str) -> BlobFormat:
    ext = PurePosixPath(name).suffix.lower().lstrip(".")
    return _EXT_FORMAT.get(ext, BlobFormat.OTHER)


def get(arc: PrintArchive, slot: Slot) -> tuple[str | None, str | None]:
    """``(blob hash, display name)`` for a slot."""
    if slot == "source":
        return arc.source_blob, arc.source_name
    return arc.f3d_blob, arc.f3d_name


def _set(arc: PrintArchive, slot: Slot, blob: str | None, name: str | None) -> str | None:
    old, _ = get(arc, slot)
    if slot == "source":
        arc.source_blob, arc.source_name = blob, name
    else:
        arc.f3d_blob, arc.f3d_name = blob, name
    return old


def _attach(
    session: Session,
    settings: Settings,
    arc: PrintArchive,
    slot: Slot,
    sha: str,
    size: int,
    name: str,
    linked: bool,
    publish,
) -> list[Path]:
    """Attach blob ``sha`` to a slot. ``publish(dest)`` writes the bytes unless
    the content-addressed file is already stored (then nothing is copied)."""
    _ensure_blob(session, sha, size, BlobKind.CAD, _fmt_for(name))
    blob = session.get(Blob, sha)
    assert blob is not None
    dest = archive_path(settings, sha, blob_ext(blob.format))
    if not dest.exists():
        if publish is None:
            raise FileNotFoundError(dest)
        publish(dest)
    old = _set(arc, slot, sha, name)
    if slot == "source":
        arc.source_linked = linked
    session.flush()
    if old and old != sha:
        _freed, paths = release_blobs(session, settings, {old})
        return paths
    return []


def store(
    session: Session,
    settings: Settings,
    arc: PrintArchive,
    slot: Slot,
    data: bytes,
    name: str,
    *,
    linked: bool = False,
) -> list[Path]:
    """Attach (or replace) a slot. Returns paths of replaced blobs to unlink
    after the caller commits."""
    return _attach(
        session,
        settings,
        arc,
        slot,
        hashlib.sha256(data).hexdigest(),
        len(data),
        clean_name(name),
        linked,
        lambda dest: publish_bytes(data, dest),
    )


def stage_dir(settings: Settings) -> Path:
    """Where staged uploads live: inside the archive store so the final
    ``os.replace`` never crosses a filesystem."""
    d = settings.data_dir / "archives" / ".staging"
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_path(
    session: Session,
    settings: Settings,
    arc: PrintArchive,
    slot: Slot,
    staged: Path,
    sha: str,
    size: int,
    name: str,
    *,
    linked: bool = False,
) -> list[Path]:
    """Like :func:`store` for a file already staged on disk (hashed while it
    was written). The staged file is moved into place, or deleted when the
    content is already stored."""
    try:
        return _attach(
            session,
            settings,
            arc,
            slot,
            sha,
            size,
            clean_name(name),
            linked,
            lambda dest: publish_file(staged, dest),
        )
    finally:
        staged.unlink(missing_ok=True)


def clear(session: Session, settings: Settings, arc: PrintArchive, slot: Slot) -> list[Path]:
    old = _set(arc, slot, None, None)
    if slot == "source":
        arc.source_linked = False
    session.flush()
    if old:
        _freed, paths = release_blobs(session, settings, {old})
        return paths
    return []


def _stage_library_file(
    session: Session, settings: Settings, file: File
) -> tuple[Path | None, str, int] | None:
    """``(staged_path | None, sha, size)`` for a library file, or ``None`` to
    skip. ``staged_path`` is ``None`` when the content is already in the
    archive store (nothing to copy); otherwise the file is streamed to a temp
    file in chunks (hashed on the way), never held in memory."""
    blob = session.get(Blob, file.blob_hash)
    if blob is None or blob.size > MAX_SOURCE_BYTES:
        return None
    if archive_path(settings, blob.hash, blob_ext(blob.format)).exists():
        return None, blob.hash, blob.size
    backend = resolve_backend_for_file_sync(session, settings, file)
    h = hashlib.sha256()
    size = 0
    fd, tmp_name = tempfile.mkstemp(dir=stage_dir(settings), prefix="link-")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            for chunk in backend.read(file.storage_path):
                size += len(chunk)
                if size > MAX_SOURCE_BYTES:
                    raise ValueError("source exceeds limit")
                h.update(chunk)
                fh.write(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, h.hexdigest(), size


def link_from_model(session: Session, settings: Settings, arc: PrintArchive) -> list[Slot]:
    """Fill empty slots from the archive's library model (current revision when
    the archive names none). Trashed models are ignored. Never raises; returns
    the slots filled. The caller commits."""
    filled: list[Slot] = []
    try:
        if arc.model_id is None:
            return filled
        model = session.get(Model, arc.model_id)
        if model is None or model.deleted_at is not None:
            return filled
        rev_id = arc.revision_id or model.current_revision_id
        if rev_id is None or session.get(Revision, rev_id) is None:
            return filled
        files = list(
            session.scalars(select(File).where(File.revision_id == rev_id).order_by(File.id))
        )
        wanted: dict[Slot, File | None] = {"source": None, "f3d": None}
        if arc.source_blob is None:
            cand = [
                f
                for f in files
                if f.rel_path.lower().endswith(".3mf")
                and not f.rel_path.lower().endswith(".gcode.3mf")
            ]
            if not cand:
                cand = [f for f in files if f.rel_path.lower().endswith((".step", ".stp"))]
            wanted["source"] = next((f for f in cand if f.blob_hash != arc.archive_blob), None)
        if arc.f3d_blob is None:
            wanted["f3d"] = next(
                (f for f in files if PurePosixPath(f.rel_path).suffix.lower() in _F3D_EXTS), None
            )
        for slot in SLOTS:
            f = wanted[slot]
            if f is None:
                continue
            staged = _stage_library_file(session, settings, f)
            if staged is None:
                continue
            tmp, sha, size = staged
            if tmp is None:
                paths = _attach(
                    session, settings, arc, slot, sha, size, clean_name(f.rel_path), True, None
                )
            else:
                paths = store_path(
                    session, settings, arc, slot, tmp, sha, size, f.rel_path, linked=True
                )
            unlink_all(paths)  # replaced blobs (slot was empty, so normally none)
            filled.append(slot)
    except Exception:
        log.exception("source link failed for archive %s", arc.id)
    return filled
