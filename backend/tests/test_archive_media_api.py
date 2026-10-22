"""P4 camera plan Task 6: ``video`` blob kind, ``mp4``/``avi`` formats,
``print_archives.media_note``, and the ``/archives/{id}/snapshot`` +
``/archives/{id}/timelapse`` endpoints (Range requests)."""

from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.config import get_settings
from app.models import Printer, PrintJob
from app.models.enums import (
    ArchiveFetchState,
    ArchiveResult,
    BlobFormat,
    BlobKind,
    PrinterKind,
    PrintJobOrigin,
    PrintJobState,
)
from app.models.library import Blob
from app.models.printing import PrintArchive
from app.services.archive import archive_path
from tests.migration_helpers import _run_alembic

pytestmark = pytest.mark.usefixtures("library_root")

_DOWN_REVISION = "a1c5e7f90b34"

SNAP = "c" * 64
MP4 = "d" * 64
AVI = "e" * 64


async def _printer(db: AsyncSession, name: str) -> Printer:
    p = Printer(
        name=name, kind=PrinterKind.BAMBU_LAN, host="10.0.0.9", serial=name, access_code_enc="x"
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _archive_with_media(
    db: AsyncSession, printer: Printer, *, timelapse_format: BlobFormat | None = BlobFormat.MP4
) -> PrintArchive:
    job = PrintJob(
        printer_id=printer.id,
        origin=PrintJobOrigin.EXTERNAL,
        subtask_name="job0",
        state=PrintJobState.FINISHED,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )
    db.add(job)
    await db.flush()

    db.add(Blob(hash=SNAP, size=1, kind=BlobKind.IMAGE, format=BlobFormat.JPG))
    timelapse_hash = None
    if timelapse_format is not None:
        timelapse_hash = MP4 if timelapse_format == BlobFormat.MP4 else AVI
        db.add(Blob(hash=timelapse_hash, size=1, kind=BlobKind.VIDEO, format=timelapse_format))
    await db.flush()

    a = PrintArchive(
        print_job_id=job.id,
        result=ArchiveResult.COMPLETED,
        fetch_state=ArchiveFetchState.DONE,
        snapshot_blob=SNAP,
        timelapse_blob=timelapse_hash,
        media_note="camera reconnected mid-print",
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return a


async def test_archive_media_migration_round_trip(scratch_db: tuple[str, str]) -> None:
    sa_url, dsn = scratch_db
    await _run_alembic(sa_url, command.upgrade, "head")

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO blobs (hash, size, kind, format) VALUES ($1, 10, 'video', 'mp4')",
            "a" * 64,
        )
        await conn.execute(
            "UPDATE print_archives SET media_note = 'x' WHERE false"
        )  # column exists
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.downgrade, _DOWN_REVISION)

    conn = await asyncpg.connect(dsn)
    try:
        kind, format_ = await conn.fetchrow(
            "SELECT kind, format FROM blobs WHERE hash = $1", "a" * 64
        )
        assert (kind, format_) == ("other", "other")
        has_media_note = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'print_archives' AND column_name = 'media_note')"
        )
        assert has_media_note is False
    finally:
        await conn.close()

    await _run_alembic(sa_url, command.upgrade, "head")

    conn = await asyncpg.connect(dsn)
    try:
        has_media_note = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'print_archives' AND column_name = 'media_note')"
        )
        assert has_media_note is True
    finally:
        await conn.close()


async def test_archive_out_exposes_media_fields(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    p = await _printer(db_session, "p1")
    a = await _archive_with_media(db_session, p)
    detail = (await authenticated_client.get(f"/api/archives/{a.id}")).json()
    assert detail["has_snapshot"] is True
    assert detail["has_timelapse"] is True
    assert detail["media_note"] == "camera reconnected mid-print"


async def test_snapshot_served(authenticated_client: httpx.AsyncClient, db_session: AsyncSession):
    p = await _printer(db_session, "p1")
    a = await _archive_with_media(db_session, p)
    s = get_settings()
    path = archive_path(s, SNAP, "jpg")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"jpeg-bytes")

    r = await authenticated_client.get(f"/api/archives/{a.id}/snapshot")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == b"jpeg-bytes"


async def test_snapshot_missing_404(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    p = await _printer(db_session, "p1")
    a = await _archive_with_media(db_session, p, timelapse_format=None)
    a.snapshot_blob = None
    await db_session.commit()
    r = await authenticated_client.get(f"/api/archives/{a.id}/snapshot")
    assert r.status_code == 404


async def test_timelapse_mp4_served_with_range(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    p = await _printer(db_session, "p1")
    a = await _archive_with_media(db_session, p, timelapse_format=BlobFormat.MP4)
    s = get_settings()
    path = archive_path(s, MP4, "mp4")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0123456789")

    r = await authenticated_client.get(f"/api/archives/{a.id}/timelapse")
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"

    r = await authenticated_client.get(
        f"/api/archives/{a.id}/timelapse", headers={"Range": "bytes=0-9"}
    )
    assert r.status_code == 206
    assert r.content == b"0123456789"
    assert r.headers["content-range"] == "bytes 0-9/10"


async def test_timelapse_avi_media_type(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    p = await _printer(db_session, "p1")
    a = await _archive_with_media(db_session, p, timelapse_format=BlobFormat.AVI)
    s = get_settings()
    path = archive_path(s, AVI, "avi")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"avi-bytes")

    r = await authenticated_client.get(f"/api/archives/{a.id}/timelapse")
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/x-msvideo"


async def test_timelapse_missing_404(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    p = await _printer(db_session, "p1")
    a = await _archive_with_media(db_session, p, timelapse_format=None)
    r = await authenticated_client.get(f"/api/archives/{a.id}/timelapse")
    assert r.status_code == 404


async def test_hidden_printer_media_404(
    member_client: httpx.AsyncClient, db_session: AsyncSession, printer_row: Printer
):
    a = await _archive_with_media(db_session, printer_row)
    for suffix in ("/snapshot", "/timelapse"):
        r = await member_client.get(f"/api/archives/{a.id}{suffix}")
        assert r.status_code == 404
