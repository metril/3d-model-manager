"""Archive blob lifecycle (T7): extension lookup, content-addressed release
and archive deletion. Sync (callers: API via threadpool, Celery purge)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import File, PrintArchive
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
    commits, then calls ``unlink_all``."""
    from app.services.archive import archive_path

    freed = 0
    paths: list[Path] = []
    for h in hashes:
        blob = session.get(Blob, h)
        if blob is None:
            continue
        if session.scalars(select(File.id).where(File.blob_hash == h).limit(1)).first():
            continue
        used = session.scalars(
            select(PrintArchive.id)
            .where(
                or_(
                    PrintArchive.archive_blob == h,
                    PrintArchive.thumbnail_blob == h,
                    PrintArchive.snapshot_blob == h,
                    PrintArchive.timelapse_blob == h,
                    PrintArchive.source_blob == h,
                    PrintArchive.f3d_blob == h,
                    cast(PrintArchive.photos, String).like(f'%"{h}"%'),
                )
            )
            .limit(1)
        ).first()
        if used is not None:
            continue
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
    Returns bytes freed; commits."""
    hashes: set[str] = set()
    for aid in ids:
        arc = session.get(PrintArchive, aid)
        if arc is None:
            continue
        hashes |= blob_hashes(arc)
        session.delete(arc)
    session.flush()
    freed, paths = release_blobs(session, settings, hashes)
    session.commit()
    unlink_all(paths)
    return freed
