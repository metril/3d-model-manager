"""``PATCH /api/archives/{id}`` model link / unlink (archive + run + print_count)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Model, Printer, PrintJob, Revision
from app.models.enums import ArchiveFetchState, ArchiveResult, PrintJobOrigin, PrintJobState
from app.models.library import Print
from app.models.printing import PrintArchive

pytestmark = pytest.mark.usefixtures("library_root")


async def _orphan(db: AsyncSession, printer: Printer) -> PrintArchive:
    job = PrintJob(
        printer_id=printer.id,
        origin=PrintJobOrigin.EXTERNAL,
        subtask_name="x",
        state=PrintJobState.FINISHED,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(job)
    await db.flush()
    a = PrintArchive(
        print_job_id=job.id, result=ArchiveResult.COMPLETED, fetch_state=ArchiveFetchState.DONE
    )
    db.add(a)
    db.add(Print(print_job_id=job.id, printed_at=datetime(2026, 1, 1, tzinfo=UTC)))
    await db.commit()
    await db.refresh(a)
    return a


async def _model(db: AsyncSession, slug: str, *, rev: bool = True) -> tuple[Model, int | None]:
    m = Model(slug=slug, name=slug)
    db.add(m)
    await db.flush()
    rid = None
    if rev:
        r = Revision(model_id=m.id, number=1, dir_name="r1")
        db.add(r)
        await db.flush()
        rid = r.id
    await db.commit()
    return m, rid


async def _run(db: AsyncSession, a: PrintArchive) -> Print:
    job_id = a.print_job_id
    db.expire_all()
    return (await db.scalars(select(Print).where(Print.print_job_id == job_id))).one()


async def test_link_updates_archive_run_and_count(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, printer_row: Printer
):
    a = await _orphan(db_session, printer_row)
    m, rid = await _model(db_session, "m1")
    mid = m.id
    r = await authenticated_client.patch(
        f"/api/archives/{a.id}", json={"model_id": mid, "revision_id": rid}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_id"] == mid and body["revision_id"] == rid
    await db_session.refresh(a)
    assert (a.model_id, a.revision_id) == (mid, rid)
    assert (await _run(db_session, a)).model_id == mid
    await db_session.refresh(m)
    assert m.print_count == 1


async def test_relink_and_unlink(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, printer_row: Printer
):
    a = await _orphan(db_session, printer_row)
    m1, _ = await _model(db_session, "m1", rev=False)
    m2, _ = await _model(db_session, "m2", rev=False)
    c = authenticated_client
    assert (await c.patch(f"/api/archives/{a.id}", json={"model_id": m1.id})).status_code == 200
    assert (await c.patch(f"/api/archives/{a.id}", json={"model_id": m2.id})).status_code == 200
    await db_session.refresh(m1)
    await db_session.refresh(m2)
    assert (m1.print_count, m2.print_count) == (0, 1)
    r = await c.patch(f"/api/archives/{a.id}", json={"model_id": None})
    assert r.status_code == 200 and r.json()["model_id"] is None
    await db_session.refresh(m2)
    assert m2.print_count == 0
    assert (await _run(db_session, a)).model_id is None


async def test_mismatched_revision_422(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, printer_row: Printer
):
    a = await _orphan(db_session, printer_row)
    m1, _ = await _model(db_session, "m1")
    _m2, rid2 = await _model(db_session, "m2")
    r = await authenticated_client.patch(
        f"/api/archives/{a.id}", json={"model_id": m1.id, "revision_id": rid2}
    )
    assert r.status_code == 422
    await db_session.refresh(a)
    assert a.model_id is None
    r = await authenticated_client.patch(f"/api/archives/{a.id}", json={"revision_id": rid2})
    assert r.status_code == 422


async def test_trashed_or_missing_model_404(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, printer_row: Printer
):
    a = await _orphan(db_session, printer_row)
    m, _ = await _model(db_session, "m1", rev=False)
    m.deleted_at = datetime(2026, 1, 2, tzinfo=UTC)
    await db_session.commit()
    c = authenticated_client
    assert (await c.patch(f"/api/archives/{a.id}", json={"model_id": m.id})).status_code == 404
    assert (await c.patch(f"/api/archives/{a.id}", json={"model_id": 99999})).status_code == 404


async def test_invisible_archive_denied(
    member_client: httpx.AsyncClient, db_session: AsyncSession, printer_row: Printer
):
    a = await _orphan(db_session, printer_row)
    m, _ = await _model(db_session, "m1", rev=False)
    r = await member_client.patch(f"/api/archives/{a.id}", json={"model_id": m.id})
    assert r.status_code == 404
    await db_session.refresh(a)
    assert a.model_id is None


async def test_link_moves_every_run_of_the_archive(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, printer_row: Printer
):
    a = await _orphan(db_session, printer_row)
    job2 = PrintJob(
        printer_id=printer_row.id,
        origin=PrintJobOrigin.EXTERNAL,
        subtask_name="x",
        state=PrintJobState.FINISHED,
        started_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    db_session.add(job2)
    await db_session.flush()
    first = (await db_session.scalars(select(Print))).one()
    first.archive_id = a.id
    db_session.add(Print(print_job_id=job2.id, archive_id=a.id, printed_at=first.printed_at))
    a.run_count = 2
    await db_session.commit()
    m1, _ = await _model(db_session, "m1", rev=False)
    m2, _ = await _model(db_session, "m2", rev=False)
    m1id, m2id, aid = m1.id, m2.id, a.id
    c = authenticated_client
    assert (await c.patch(f"/api/archives/{aid}", json={"model_id": m1id})).status_code == 200
    db_session.expire_all()
    rows = (await db_session.scalars(select(Print))).all()
    assert [r.model_id for r in rows] == [m1id, m1id]
    assert (await db_session.get(Model, m1id)).print_count == 2
    assert (await c.patch(f"/api/archives/{aid}", json={"model_id": m2id})).status_code == 200
    db_session.expire_all()
    assert (await db_session.get(Model, m1id)).print_count == 0
    assert (await db_session.get(Model, m2id)).print_count == 2


async def test_omitted_fields_keep_current_values(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, printer_row: Printer
):
    a = await _orphan(db_session, printer_row)
    m, rid = await _model(db_session, "m1")
    m2, rid2 = await _model(db_session, "m2")
    mid, m2id, aid = m.id, m2.id, a.id
    c = authenticated_client
    base = {"model_id": mid, "revision_id": rid}
    assert (await c.patch(f"/api/archives/{aid}", json=base)).status_code == 200
    # revision-only change validates against the current model
    r = await c.patch(f"/api/archives/{aid}", json={"revision_id": rid2})
    assert r.status_code == 422
    # same model again keeps the revision
    r = await c.patch(f"/api/archives/{aid}", json={"model_id": mid})
    assert r.json()["model_id"] == mid and r.json()["revision_id"] == rid
    # revision null alone does not unlink
    r = await c.patch(f"/api/archives/{aid}", json={"revision_id": None})
    assert r.json()["model_id"] == mid and r.json()["revision_id"] is None
    # changing the model clears the revision
    r = await c.patch(f"/api/archives/{aid}", json={"model_id": m2id})
    assert r.json()["model_id"] == m2id and r.json()["revision_id"] is None
