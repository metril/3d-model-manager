"""``/api/archives`` listing, detail, media and the reprint stub (P2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
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


async def test_runs_endpoint_and_list_summary(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    m = Model(slug="m", name="M")
    db_session.add(m)
    await db_session.commit()
    a = await _archive(db_session, await _printer(db_session, "p1"), model=m)
    old = await _archive(db_session, await _printer(db_session, "p2"), model=m, n=5)
    # fold `old`'s job in as a prior run of `a`
    run_old = (
        await db_session.scalars(select(Print).where(Print.print_job_id == old.print_job_id))
    ).one()
    run_old.archive_id = a.id
    run_new = (
        await db_session.scalars(select(Print).where(Print.print_job_id == a.print_job_id))
    ).one()
    run_new.archive_id = a.id
    a.run_count = 2
    await db_session.commit()
    c = authenticated_client
    row = next(x for x in (await c.get("/api/archives")).json() if x["id"] == a.id)
    assert row["run_count"] == 2
    assert row["latest_run"]["outcome"] == "completed" and row["latest_run"]["total_cost"] == 1.5
    runs = (await c.get(f"/api/archives/{a.id}/runs")).json()
    assert len(runs) == 2 and [r["is_latest"] for r in runs].count(True) == 1
    assert next(r for r in runs if r["is_latest"])["print_job_id"] == a.print_job_id
    assert (await c.get("/api/archives/99999/runs")).status_code == 404


async def _fold_run(db: AsyncSession, printer: Printer, arc: PrintArchive, model: Model):
    """An older run folded into ``arc`` (run_count > 1)."""
    job = PrintJob(
        printer_id=printer.id,
        origin=PrintJobOrigin.EXTERNAL,
        subtask_name="older",
        state=PrintJobState.FINISHED,
        started_at=datetime(2025, 12, 31, tzinfo=UTC),
        finished_at=datetime(2025, 12, 31, 1, tzinfo=UTC),
    )
    db.add(job)
    await db.flush()
    db.add(
        Print(
            model_id=model.id,
            print_job_id=job.id,
            archive_id=arc.id,
            printed_at=datetime(2025, 12, 31, tzinfo=UTC),
        )
    )
    await db.commit()
    return job.id


def _sweep(monkeypatch) -> list:
    from app.tasks import archive as task_mod

    calls: list = []
    monkeypatch.setattr(task_mod.archive_print, "apply_async", lambda args: calls.append(args))
    task_mod.archive_sweep.apply()
    return calls


async def _recent(db: AsyncSession, *job_ids: int) -> None:
    for jid in job_ids:
        (await db.get(PrintJob, jid)).finished_at = datetime.now(UTC) - timedelta(minutes=10)
    await db.commit()


@pytest.mark.usefixtures("printer_enabled")
async def test_deleted_archive_runs_stay_deleted(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch
):
    p = await _printer(db_session, "p")
    m = Model(slug="m", name="M", print_count=3)
    db_session.add(m)
    await db_session.commit()
    arc = await _archive(db_session, p, model=m)
    arc_id, arc_job, mid = arc.id, arc.print_job_id, m.id
    old_job = await _fold_run(db_session, p, arc, m)
    other = await _archive(db_session, p, model=m, n=5)  # untouched bystander
    keep_job = other.print_job_id
    await _recent(db_session, arc_job, old_job, keep_job)

    r = await authenticated_client.delete(f"/api/archives/{arc_id}")
    assert r.status_code == 204
    assert _sweep(monkeypatch) == []
    db_session.expire_all()
    assert (await db_session.get(Model, mid)).print_count == 1
    left = (await db_session.execute(select(Print.print_job_id))).scalars().all()
    assert left == [keep_job]
    # jobs are kept (billing/queue reference them) but tombstoned and hidden
    assert (await db_session.get(PrintJob, old_job)).archive_dismissed_at is not None
    assert (await db_session.get(PrintJob, arc_job)).archive_dismissed_at is not None
    assert (await db_session.get(PrintJob, keep_job)).archive_dismissed_at is None
    ids = {j["id"] for j in (await authenticated_client.get("/api/print-jobs")).json()}
    assert ids == {keep_job}
    stats = (await authenticated_client.get("/api/stats/records?days=365")).json()
    assert "older" not in str(stats)


async def _two_run_archive(db: AsyncSession, printer: Printer, model: Model):
    """Archive whose run 1 (older) rolled its media into photos, run 2 is latest."""
    arc = await _archive(db, printer, model=model, blobs=True)
    arc_id, latest_job = arc.id, arc.print_job_id
    old_job = await _fold_run(db, printer, arc, model)
    arc = await db.get(PrintArchive, arc_id)
    arc.run_count = 2
    arc.result = ArchiveResult.FAILED
    arc.snapshot_blob = "c" * 64
    arc.photos = [
        {"filename": "run1-photo.jpg", "blob": THUMB, "kind": "photo", "at": "x"},
        {"filename": "mine.jpg", "blob": FILE, "kind": "photo", "at": "x"},
    ]
    db.add(Blob(hash="c" * 64, size=1, kind=BlobKind.SLICED, format=BlobFormat.PNG))
    await db.commit()
    return arc_id, latest_job, old_job


async def test_delete_latest_run_repoints_and_blocks_reprint_resurrection(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch
):
    p = await _printer(db_session, "p")
    m = Model(slug="m", name="M", print_count=2)
    db_session.add(m)
    await db_session.commit()
    mid = m.id
    arc_id, latest_job, old_job = await _two_run_archive(db_session, p, m)
    await _recent(db_session, latest_job, old_job)
    run = (
        await db_session.execute(select(Print).where(Print.print_job_id == latest_job))
    ).scalar_one()
    r = await authenticated_client.delete(f"/api/prints/{run.id}")
    assert r.status_code == 204
    assert _sweep(monkeypatch) == []
    db_session.expire_all()
    arc = await db_session.get(PrintArchive, arc_id)
    assert arc.run_count == 1 and arc.print_job_id == old_job
    assert arc.result == ArchiveResult.COMPLETED  # from the older run's job
    assert arc.snapshot_blob == THUMB  # run1-photo restored as current media
    assert [ph["filename"] for ph in arc.photos] == ["mine.jpg"]
    assert (await db_session.get(Model, mid)).print_count == 1
    assert (await db_session.get(PrintJob, latest_job)).archive_dismissed_at is not None
    assert (await db_session.get(PrintJob, old_job)).archive_dismissed_at is None
    assert await db_session.get(Blob, "c" * 64) is None  # deleted run's snapshot released


async def test_delete_older_run_drops_its_media_and_renumbers(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
):
    p = await _printer(db_session, "p")
    m = Model(slug="m", name="M", print_count=2)
    db_session.add(m)
    await db_session.commit()
    arc_id, latest_job, old_job = await _two_run_archive(db_session, p, m)
    run = (
        await db_session.execute(select(Print).where(Print.print_job_id == old_job))
    ).scalar_one()
    assert (await authenticated_client.delete(f"/api/prints/{run.id}")).status_code == 204
    db_session.expire_all()
    arc = await db_session.get(PrintArchive, arc_id)
    assert arc.run_count == 1 and arc.print_job_id == latest_job
    assert arc.result == ArchiveResult.FAILED and arc.snapshot_blob == "c" * 64
    assert [ph["filename"] for ph in arc.photos] == ["mine.jpg"]


async def test_delete_only_run_removes_archive(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch
):
    p = await _printer(db_session, "p")
    m = Model(slug="m", name="M", print_count=1)
    db_session.add(m)
    await db_session.commit()
    arc = await _archive(db_session, p, model=m, blobs=True)
    arc_id, job_id, mid = arc.id, arc.print_job_id, m.id
    await _recent(db_session, job_id)
    run = (await db_session.execute(select(Print))).scalar_one()
    assert (await authenticated_client.delete(f"/api/prints/{run.id}")).status_code == 204
    assert _sweep(monkeypatch) == []
    db_session.expire_all()
    assert await db_session.get(PrintArchive, arc_id) is None
    assert (await db_session.get(Model, mid)).print_count == 0
    assert (await db_session.get(PrintJob, job_id)).archive_dismissed_at is not None


async def test_sweep_still_picks_up_lost_enqueue(db_session: AsyncSession, monkeypatch):
    p = await _printer(db_session, "p")
    job = PrintJob(
        printer_id=p.id,
        origin=PrintJobOrigin.EXTERNAL,
        state=PrintJobState.FINISHED,
        finished_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    db_session.add(job)
    await db_session.commit()
    assert _sweep(monkeypatch) == [[job.id, "completed"]]
