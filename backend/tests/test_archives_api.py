"""``/api/archives`` listing, detail, media and the reprint stub (P2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Model, Printer, PrintJob
from app.models.enums import (
    ArchiveFetchState,
    ArchiveResult,
    BlobFormat,
    BlobKind,
    PrinterKind,
    PrintJobOrigin,
    PrintJobState,
)
from app.models.library import Blob, Print
from app.models.printing import PrintArchive
from app.services.archive import archive_path

pytestmark = pytest.mark.usefixtures("library_root")

THUMB = "a" * 64
FILE = "b" * 64


async def _printer(db: AsyncSession, name: str) -> Printer:
    p = Printer(
        name=name, kind=PrinterKind.BAMBU_LAN, host="10.0.0.9", serial=name, access_code_enc="x"
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _archive(
    db: AsyncSession,
    printer: Printer,
    *,
    result: ArchiveResult = ArchiveResult.COMPLETED,
    model: Model | None = None,
    blobs: bool = False,
    n: int = 0,
) -> PrintArchive:
    job = PrintJob(
        printer_id=printer.id,
        origin=PrintJobOrigin.EXTERNAL,
        subtask_name=f"job{n}",
        state=PrintJobState.FINISHED,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )
    db.add(job)
    await db.flush()
    if blobs:
        for h, fmt in ((THUMB, BlobFormat.PNG), (FILE, BlobFormat.GCODE_3MF)):
            if await db.get(Blob, h) is None:
                db.add(Blob(hash=h, size=1, kind=BlobKind.SLICED, format=fmt))
        await db.flush()
    a = PrintArchive(
        print_job_id=job.id,
        model_id=model.id if model else None,
        result=result,
        duration_s=3600,
        fetch_state=ArchiveFetchState.DONE,
        thumbnail_blob=THUMB if blobs else None,
        archive_blob=FILE if blobs else None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=n),
    )
    db.add(a)
    await db.flush()
    if model is not None:
        db.add(
            Print(model_id=model.id, print_job_id=job.id, total_cost=1.5, printed_at=a.created_at)
        )
    await db.commit()
    await db.refresh(a)
    return a


async def test_list_filters(authenticated_client: httpx.AsyncClient, db_session: AsyncSession):
    p1, p2 = await _printer(db_session, "p1"), await _printer(db_session, "p2")
    m = Model(slug="m", name="M")
    db_session.add(m)
    await db_session.commit()
    await _archive(db_session, p1, model=m, n=1)
    await _archive(db_session, p1, result=ArchiveResult.FAILED, n=2)
    await _archive(db_session, p2, n=3)
    c = authenticated_client
    body = (await c.get("/api/archives")).json()
    assert [a["subtask_name"] for a in body] == ["job3", "job2", "job1"]
    assert body[0]["created_at"] >= body[-1]["created_at"]
    assert len((await c.get("/api/archives?result=failed")).json()) == 1
    assert {a["printer_id"] for a in (await c.get(f"/api/archives?printer={p1.id}")).json()} == {
        p1.id
    }
    by_model = (await c.get(f"/api/archives?model={m.id}")).json()
    assert by_model[0]["run"]["total_cost"] is not None
    assert by_model[0]["model_slug"] == "m"
    assert len((await c.get("/api/archives?limit=1&offset=1")).json()) == 1


async def test_archive_visibility(
    member_client: httpx.AsyncClient, db_session: AsyncSession, printer_row: Printer
):
    a = await _archive(db_session, printer_row, blobs=True)
    assert (await member_client.get("/api/archives")).json() == []
    for suffix in ("", "/thumbnail", "/file"):
        assert (await member_client.get(f"/api/archives/{a.id}{suffix}")).status_code == 404
    assert (await member_client.post(f"/api/archives/{a.id}/reprint")).status_code == 404


async def test_media_served(authenticated_client: httpx.AsyncClient, db_session: AsyncSession):
    p = await _printer(db_session, "p1")
    a = await _archive(db_session, p, blobs=True)
    s = get_settings()
    for sha, ext, data in ((THUMB, "png", b"png"), (FILE, "gcode.3mf", b"3mf")):
        path = archive_path(s, sha, ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    r = await authenticated_client.get(f"/api/archives/{a.id}/thumbnail")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    r = await authenticated_client.get(f"/api/archives/{a.id}/file")
    assert r.content == b"3mf" and "job0.gcode.3mf" in r.headers["content-disposition"]
    detail = (await authenticated_client.get(f"/api/archives/{a.id}")).json()
    assert detail["has_thumbnail"] and detail["has_file"]


async def test_missing_media_404(authenticated_client: httpx.AsyncClient, db_session: AsyncSession):
    a = await _archive(db_session, await _printer(db_session, "p1"))
    assert (await authenticated_client.get(f"/api/archives/{a.id}/thumbnail")).status_code == 404
    assert (await authenticated_client.get("/api/archives/99999")).status_code == 404


async def test_reprint_409_without_library_file(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    """P5 Task 7: the legacy 501 stub is gone -- reprint now builds a real
    plan, 409ing only when neither the job's own file nor a library file
    sharing the archived 3MF's blob hash exists anymore."""
    a = await _archive(db_session, await _printer(db_session, "p1"))
    r = await authenticated_client.post(f"/api/archives/{a.id}/reprint")
    assert r.status_code == 409
