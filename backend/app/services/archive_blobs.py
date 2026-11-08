"""Archive blob lifecycle (T7): extension lookup, content-addressed release
and archive deletion. Sync (callers: API via threadpool, Celery purge)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import File, Print, PrintArchive, PrintJob
from app.models.enums import BlobFormat
from app.models.library import Blob


def blob_ext(fmt: BlobFormat) -> str:
    return "gcode.3mf" if fmt == BlobFormat.GCODE_3MF else fmt.value


def blob_hashes(arc: PrintArchive) -> set[str]:
    out = {
        h
        for h in (
            arc.archive_blob,
            arc.thumbnail_blob,
            arc.snapshot_blob,
            arc.timelapse_blob,
            arc.source_blob,
            arc.f3d_blob,
        )
        if h
    }
    out.update(p["blob"] for p in (arc.photos or []) if p.get("blob"))
    return out


def release_blobs(session: Session, settings: Settings, hashes: set[str]) -> tuple[int, list[Path]]:
    """Drop each blob row no longer referenced by a library file or any
    archive column / photo. Returns (bytes freed, files to unlink); the caller
    commits, then calls ``unlink_all``. Reference checks are batched: one
    query per column (not per hash), photos via indexed JSONB containment."""
    from app.services.archive import archive_path

    if not hashes:
        return 0, []
    wanted = list(hashes)
    blobs = {b.hash: b for b in session.scalars(select(Blob).where(Blob.hash.in_(wanted)))}
    if not blobs:
        return 0, []
    candidates = list(blobs)
    used: set[str] = set(
        session.scalars(select(File.blob_hash).where(File.blob_hash.in_(candidates)))
    )
    for col in (
        PrintArchive.archive_blob,
        PrintArchive.thumbnail_blob,
        PrintArchive.snapshot_blob,
        PrintArchive.timelapse_blob,
        PrintArchive.source_blob,
        PrintArchive.f3d_blob,
    ):
        left = [h for h in candidates if h not in used]
        if not left:
            break
        used.update(session.scalars(select(col).where(col.in_(left))))
    for h in (h for h in candidates if h not in used):
        # ``@>`` hits the GIN index on photos; photos entries are {"blob": sha, ...}.
        if session.scalars(
            select(PrintArchive.id).where(PrintArchive.photos.contains([{"blob": h}])).limit(1)
        ).first():
            used.add(h)

    freed = 0
    paths: list[Path] = []
    for h in candidates:
        if h in used:
            continue
        blob = blobs[h]
        paths.append(archive_path(settings, h, blob_ext(blob.format)))
        freed += blob.size
        session.delete(blob)
    session.flush()
    return freed, paths


def unlink_all(paths: list[Path]) -> None:
    for p in paths:
        p.unlink(missing_ok=True)


def delete_archives(session: Session, settings: Settings, ids: list[int]) -> int:
    """Delete archive rows (never ``print_jobs``) and release their blobs.
    Returns bytes freed; commits. The runs' jobs are tombstoned so
    ``archive_sweep`` doesn't re-archive them."""
    hashes: set[str] = set()
    for arc in session.scalars(select(PrintArchive).where(PrintArchive.id.in_(ids))):
        hashes |= blob_hashes(arc)
        job_ids = {arc.print_job_id} | set(
            session.scalars(select(Print.print_job_id).where(Print.archive_id == arc.id))
        )
        session.execute(
            update(PrintJob)
            .where(PrintJob.id.in_({j for j in job_ids if j is not None}))
            .where(PrintJob.archive_dismissed_at.is_(None))
            .values(archive_dismissed_at=func.now())
        )
        session.delete(arc)
    session.flush()
    freed, paths = release_blobs(session, settings, hashes)
    session.commit()
    unlink_all(paths)
    return freed
