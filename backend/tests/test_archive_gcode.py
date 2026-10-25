"""T8b-be: G-code extraction endpoints."""

from __future__ import annotations

import gzip
import io
import zipfile
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.crypto import encrypt_secret
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
from app.printers.fake import FakePrinterAdapter
from app.services.archive import archive_path

pytestmark = pytest.mark.usefixtures("library_root")

SHA = "f" * 64
G1 = b"G1 X1 Y1\n" * 50
G2 = b"G1 X2 Y2\n"


def _3mf(plates: dict[int, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Metadata/slice_info.config", "<x/>")
        for n, data in plates.items():
            zf.writestr(f"Metadata/plate_{n}.gcode", data)
    return buf.getvalue()


async def _printer(db: AsyncSession) -> Printer:
    p = Printer(
        name="gp",
        kind=PrinterKind.BAMBU_LAN,
        host="10.0.0.9",
        serial="gp",
        access_code_enc=encrypt_secret(get_settings(), "12345678"),
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _archive(
    db: AsyncSession, printer: Printer, data: bytes | None, plate: int | None = None
) -> PrintArchive:
    job = PrintJob(
        printer_id=printer.id,
        origin=PrintJobOrigin.EXTERNAL,
        subtask_name="gjob",
        state=PrintJobState.FINISHED,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )
    db.add(job)
    await db.flush()
    if data is not None:
        if await db.get(Blob, SHA) is None:
            db.add(
                Blob(hash=SHA, size=len(data), kind=BlobKind.SLICED, format=BlobFormat.GCODE_3MF)
            )
        path = archive_path(get_settings(), SHA, "gcode.3mf")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    a = PrintArchive(
        print_job_id=job.id,
        result=ArchiveResult.COMPLETED,
        fetch_state=ArchiveFetchState.DONE,
        archive_blob=SHA if data is not None else None,
        plate=plate,
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return a


async def test_plates_and_extraction(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    a = await _archive(db_session, await _printer(db_session), _3mf({1: G1, 2: G2}))
    c = authenticated_client
    assert (await c.get(f"/api/archives/{a.id}/gcode/plates")).json() == [
        {"plate": 1, "bytes": len(G1)},
        {"plate": 2, "bytes": len(G2)},
    ]
    r = await c.get(f"/api/archives/{a.id}/gcode", headers={"accept-encoding": "identity"})
    assert r.status_code == 200 and r.content == G1  # default plate 1
    assert r.headers["content-type"].startswith("text/plain")
    assert (await c.get(f"/api/archives/{a.id}/gcode?plate=2")).content == G2
    assert (await c.get(f"/api/archives/{a.id}/gcode?plate=3")).status_code == 404


async def test_default_plate_from_archive_and_gzip(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    a = await _archive(db_session, await _printer(db_session), _3mf({1: G1, 2: G2}), plate=2)
    r = await authenticated_client.get(
        f"/api/archives/{a.id}/gcode", headers={"accept-encoding": "gzip"}
    )
    assert r.headers.get("content-encoding") == "gzip"
    assert r.content == G2  # httpx transparently decodes


def test_gzip_roundtrip_chunks(tmp_path):
    from app.api.archive_gcode import _chunks

    f = tmp_path / "a.3mf"
    f.write_bytes(_3mf({1: G1 * 100}))
    raw = b"".join(_chunks(f, "Metadata/plate_1.gcode", True))
    assert gzip.decompress(raw) == G1 * 100


async def test_missing_member_and_blob_404(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    p = await _printer(db_session)
    a = await _archive(db_session, p, _3mf({}))
    assert (await authenticated_client.get(f"/api/archives/{a.id}/gcode")).status_code == 404
    assert (await authenticated_client.get(f"/api/archives/{a.id}/gcode/plates")).json() == []
    b = await _archive(db_session, p, None)
    assert (await authenticated_client.get(f"/api/archives/{b.id}/gcode")).status_code == 404
    assert (await authenticated_client.get("/api/archives/99999/gcode")).status_code == 404


async def test_archive_gcode_visibility(member_client: httpx.AsyncClient, db_session: AsyncSession):
    a = await _archive(db_session, await _printer(db_session), _3mf({1: G1}))
    for s in ("/gcode", "/gcode/plates"):
        assert (await member_client.get(f"/api/archives/{a.id}{s}")).status_code == 404


@pytest.mark.usefixtures("printer_enabled")
async def test_printer_side(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake_adapter: FakePrinterAdapter,
):
    p = await _printer(db_session)
    fake_adapter.files["/sdcard/a.gcode.3mf"] = _3mf({1: G1, 2: G2})
    fake_adapter.files["/sdcard/raw.gcode"] = G2
    fake_adapter.files["/sdcard/x.txt"] = b"x"
    c, url = authenticated_client, f"/api/printers/{p.id}/files/gcode"
    r = await c.get(url, params={"path": "/sdcard/a.gcode.3mf"})
    assert r.status_code == 200 and r.content == G1  # lowest plate
    assert (await c.get(url, params={"path": "/sdcard/a.gcode.3mf", "plate": 2})).content == G2
    assert (await c.get(url, params={"path": "/sdcard/a.gcode.3mf", "plate": 9})).status_code == 404
    assert (await c.get(url, params={"path": "/sdcard/raw.gcode"})).content == G2
    assert (await c.get(url, params={"path": "/sdcard/missing.3mf"})).status_code == 404
    for bad in ("sdcard/a.3mf", "/sdcard/../etc/a.3mf", "/sdcard/x.txt", "/"):
        assert (await c.get(url, params={"path": bad})).status_code == 422, bad
    assert "/sdcard/x.txt" not in fake_adapter.downloads


@pytest.mark.usefixtures("printer_enabled")
async def test_printer_side_visibility(
    member_client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake_adapter: FakePrinterAdapter,
):
    p = await _printer(db_session)
    fake_adapter.files["/a.3mf"] = _3mf({1: G1})
    r = await member_client.get(f"/api/printers/{p.id}/files/gcode", params={"path": "/a.3mf"})
    assert r.status_code in (403, 404)
    assert fake_adapter.downloads == []


def test_accepts_gzip_qvalues():
    from app.api.archive_gcode import _accepts_gzip

    assert _accepts_gzip("gzip, deflate")
    assert _accepts_gzip("br;q=1, gzip;q=0.5")
    assert not _accepts_gzip("gzip;q=0")
    assert not _accepts_gzip("identity")


def test_chunks_on_close_runs_when_abandoned(tmp_path):
    from app.api.archive_gcode import _chunks

    f = tmp_path / "a.3mf"
    f.write_bytes(_3mf({1: G1 * 10000}))
    called = []
    gen = _chunks(f, "Metadata/plate_1.gcode", False, lambda: called.append(1))
    next(gen)
    gen.close()  # client disconnect
    assert called == [1]
