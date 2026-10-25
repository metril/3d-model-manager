"""V4 projects: CRUD, BOM, progress/cost, timeline, templates, export, queue."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Model, Printer, PrintJob
from app.models.enums import (
    ArchiveFetchState,
    ArchiveResult,
    BlobFormat,
    BlobKind,
    PrinterKind,
    PrintJobOrigin,
    PrintJobState,
)
from app.models.library import File, Print, PrintQueueEntry, Revision
from app.models.printing import PrintArchive

pytestmark = pytest.mark.usefixtures("library_root")


async def _model(client: httpx.AsyncClient, name: str) -> dict:
    r = await client.post("/api/models", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


async def _project(client: httpx.AsyncClient, **kw) -> dict:
    r = await client.post("/api/projects", json={"name": "Robot", **kw})
    assert r.status_code == 201, r.text
    return r.json()


async def _archive(
    db: AsyncSession,
    model_id: int,
    *,
    result: ArchiveResult = ArchiveResult.COMPLETED,
    cost: float = 2.0,
    n: int = 0,
    plate: int | None = None,
) -> PrintArchive:
    printer = Printer(
        name=f"p{n}",
        kind=PrinterKind.BAMBU_LAN,
        host="10.0.0.9",
        serial=f"s{n}",
        access_code_enc="x",
    )
    db.add(printer)
    await db.flush()
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
    a = PrintArchive(
        print_job_id=job.id,
        model_id=model_id,
        result=result,
        duration_s=600,
        plate=plate,
        fetch_state=ArchiveFetchState.DONE,
        created_at=datetime.now(UTC) + timedelta(minutes=1 + n),
    )
    db.add(a)
    await db.flush()
    db.add(Print(model_id=model_id, print_job_id=job.id, total_cost=cost, filament_g=10.0))
    await db.commit()
    await db.refresh(a)
    return a


async def test_crud_and_audit(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    p = await _project(authenticated_client, tags=["a", " a ", "b"], due_date="2020-01-01")
    assert p["tags"] == ["a", "b"]
    assert p["overdue"] is True
    r = await authenticated_client.patch(
        f"/api/projects/{p['id']}", json={"status": "completed", "notes": "n"}
    )
    assert r.status_code == 200
    assert r.json()["completed_at"] is not None
    assert r.json()["overdue"] is False
    listed = await authenticated_client.get("/api/projects", params={"status": "completed"})
    assert [x["id"] for x in listed.json()] == [p["id"]]
    assert (await authenticated_client.get("/api/projects", params={"q": "zzz"})).json() == []
    assert (await authenticated_client.get("/api/projects/9999")).status_code == 404
    d = await authenticated_client.delete(f"/api/projects/{p['id']}")
    assert d.status_code == 204
    actions = set((await db_session.execute(select(AuditLog.action))).scalars())
    assert {"project_create", "project_status", "project_delete"} <= actions


async def test_bom_progress_cost_timeline(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _model(authenticated_client, "Gear")
    p = await _project(authenticated_client)
    pid = p["id"]
    r = await authenticated_client.post(
        f"/api/projects/{pid}/items", json={"kind": "part", "model_id": m["id"], "quantity": 4}
    )
    assert r.status_code == 201, r.text
    part = r.json()
    assert part["model_name"] == "Gear" and part["outstanding"] == 4
    r = await authenticated_client.post(
        f"/api/projects/{pid}/items",
        json={"kind": "purchased", "name": "Screws", "quantity": 10, "unit_cost": 0.5},
    )
    buy = r.json()
    assert buy["line_cost"] == 5.0
    bad = await authenticated_client.post(
        f"/api/projects/{pid}/items", json={"kind": "purchased", "quantity": 1}
    )
    assert bad.status_code == 422
    nomodel = await authenticated_client.post(
        f"/api/projects/{pid}/items", json={"kind": "part", "model_id": 99999}
    )
    assert nomodel.status_code == 404

    await _archive(db_session, m["id"], n=1)
    await _archive(db_session, m["id"], n=2)
    await _archive(db_session, m["id"], result=ArchiveResult.FAILED, n=3)
    patched = await authenticated_client.patch(
        f"/api/projects/{pid}/items/{buy['id']}", json={"acquired": 5}
    )
    assert patched.json()["acquired"] == 5

    out = (await authenticated_client.get(f"/api/projects/{pid}")).json()
    item = next(i for i in out["items"] if i["kind"] == "part")
    assert item["done"] == 2 and item["outstanding"] == 2
    assert out["progress"]["parts_done"] == 2
    assert out["progress"]["percent"] == round(100 * (2 + 5) / 14)
    assert out["cost"]["print_cost"] == 4.0
    assert out["cost"]["purchased_cost"] == 5.0
    assert out["cost"]["total_cost"] == 9.0
    assert out["cost"]["filament_g"] == 20.0
    assert out["cost"]["estimated_remaining_cost"] == 4.0

    tl = (await authenticated_client.get(f"/api/projects/{pid}/timeline")).json()
    assert sum(1 for e in tl if e["kind"] == "print") == 3  # incl. failed
    assert any(e["kind"] == "bom_add" for e in tl)

    arcs = (await authenticated_client.get(f"/api/projects/{pid}/archives")).json()
    assert len(arcs) == 3

    d = await authenticated_client.delete(f"/api/projects/{pid}/items/{part['id']}")
    assert d.status_code == 204


async def test_explicit_link_and_plate_match(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _model(authenticated_client, "Lid")
    p = await _project(authenticated_client)
    pid = p["id"]
    await authenticated_client.post(
        f"/api/projects/{pid}/items",
        json={"kind": "part", "model_id": m["id"], "quantity": 2, "plate": 2},
    )
    a1 = await _archive(db_session, m["id"], n=1, plate=1)
    a2 = await _archive(db_session, m["id"], n=2, plate=2)
    out = (await authenticated_client.get(f"/api/projects/{pid}")).json()
    assert out["progress"]["parts_done"] == 1  # only the plate-2 run
    # Explicitly linking an archive from before the project / other plate: still plate-gated.
    r = await authenticated_client.post(
        f"/api/projects/{pid}/archives", json={"archive_ids": [a1.id, a2.id]}
    )
    assert r.json()["linked"] == 2
    r = await authenticated_client.post(
        f"/api/projects/{pid}/archives/remove", json={"archive_ids": [a1.id]}
    )
    assert r.json()["unlinked"] == 1


async def test_add_models_bulk_and_templates(
    authenticated_client: httpx.AsyncClient,
) -> None:
    m1 = await _model(authenticated_client, "A")
    m2 = await _model(authenticated_client, "B")
    p = await _project(authenticated_client)
    pid = p["id"]
    r = await authenticated_client.post(
        f"/api/projects/{pid}/models", json={"model_ids": [m1["id"], m2["id"], m1["id"]]}
    )
    assert len(r.json()["items"]) == 2
    r = await authenticated_client.post(
        f"/api/projects/{pid}/models", json={"model_ids": [m1["id"]]}
    )
    assert len(r.json()["items"]) == 2  # no dup whole-model line
    assert (
        await authenticated_client.post(f"/api/projects/{pid}/models", json={"model_ids": [999999]})
    ).status_code == 404

    t = await authenticated_client.post(
        f"/api/projects/{pid}/create-template", json={"name": "Tpl"}
    )
    assert t.status_code == 201 and t.json()["is_template"] is True
    assert [
        x["name"] for x in (await authenticated_client.get("/api/projects/templates")).json()
    ] == ["Tpl"]
    assert [x["id"] for x in (await authenticated_client.get("/api/projects")).json()] == [pid]
    n = await authenticated_client.post(
        f"/api/projects/from-template/{t.json()['id']}", json={"name": "Copy"}
    )
    assert n.status_code == 201 and len(n.json()["items"]) == 2 and not n.json()["is_template"]
    # a normal project is not a template
    bad = await authenticated_client.post(f"/api/projects/from-template/{pid}", json={"name": "x"})
    assert bad.status_code == 422


async def test_attachments_and_export(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    md = await _model(authenticated_client, "Exp")
    model = await db_session.get(Model, md["id"])
    rev = await db_session.get(Revision, model.current_revision_id)
    await seed_file(model, rev, "part.stl", b"stl-bytes")
    p = await _project(authenticated_client)
    pid = p["id"]
    await authenticated_client.post(
        f"/api/projects/{pid}/items", json={"kind": "part", "model_id": md["id"], "quantity": 1}
    )
    await authenticated_client.post(
        f"/api/projects/{pid}/items",
        json={"kind": "purchased", "name": "Magnet", "quantity": 2, "unit_cost": 1.25},
    )

    up = await authenticated_client.post(
        f"/api/projects/{pid}/attachments",
        params={"filename": "../evil/notes.txt"},
        content=b"hello",
        headers={"content-type": "text/plain"},
    )
    assert up.status_code == 201, up.text
    assert up.json()["filename"] == "notes.txt"
    aid = up.json()["id"]
    dl = await authenticated_client.get(f"/api/projects/{pid}/attachments/{aid}")
    assert dl.status_code == 200 and dl.content == b"hello"
    assert (
        await authenticated_client.post(
            f"/api/projects/{pid}/attachments", params={"filename": "e"}, content=b""
        )
    ).status_code == 400

    csv_r = await authenticated_client.get(f"/api/projects/{pid}/export", params={"format": "csv"})
    assert csv_r.status_code == 200 and "Magnet" in csv_r.text and "Exp" in csv_r.text
    js = await authenticated_client.get(f"/api/projects/{pid}/export", params={"format": "json"})
    assert js.json()["name"] == "Robot"
    z = await authenticated_client.get(f"/api/projects/{pid}/export", params={"format": "zip"})
    assert z.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert any(n.endswith("bom.csv") for n in names)
    assert any(n.endswith("part.stl") for n in names)
    assert (
        await authenticated_client.get(f"/api/projects/{pid}/export", params={"format": "x"})
    ).status_code == 422

    d = await authenticated_client.delete(f"/api/projects/{pid}/attachments/{aid}")
    assert d.status_code == 204
    assert (
        await authenticated_client.get(f"/api/projects/{pid}/attachments/{aid}")
    ).status_code == 404


async def test_queue_remaining(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    md = await _model(authenticated_client, "Sliced")
    model = await db_session.get(Model, md["id"])
    rev = await db_session.get(Revision, model.current_revision_id)
    await seed_file(
        model,
        rev,
        "p.gcode.3mf",
        b"sliced",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    plain = await _model(authenticated_client, "NoSlice")
    p = await _project(authenticated_client)
    pid = p["id"]
    for mid, q in ((md["id"], 3), (plain["id"], 1)):
        await authenticated_client.post(
            f"/api/projects/{pid}/items", json={"kind": "part", "model_id": mid, "quantity": q}
        )
    await _archive(db_session, md["id"], n=1)  # 1 of 3 done

    r = await authenticated_client.post(f"/api/projects/{pid}/queue-remaining", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert sum(e["copies"] for e in body["enqueued"]) == 2
    assert [s["model"] for s in body["skipped"]] == ["NoSlice"]
    entries = (await db_session.execute(select(PrintQueueEntry))).scalars().all()
    assert sum(e.copies for e in entries) == 2

    # Idempotent: queued copies count against the outstanding total.
    r2 = await authenticated_client.post(f"/api/projects/{pid}/queue-remaining", json={})
    assert r2.json()["enqueued"] == []
    out = (await authenticated_client.get(f"/api/projects/{pid}")).json()
    assert out["status"] == "active"
    assert out["progress"]["parts_queued"] == 2


async def test_write_requires_library_write(
    member_client: httpx.AsyncClient, authenticated_client: httpx.AsyncClient
) -> None:
    p = await _project(authenticated_client)
    assert (await member_client.get(f"/api/projects/{p['id']}")).status_code == 200
    r = await member_client.post("/api/projects", json={"name": "mine"})
    assert r.status_code == 201  # members hold library:write
