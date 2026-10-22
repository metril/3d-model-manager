"""Attribution on create + creator-or-admin deletes (P1 Task 6)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import File as FileRow
from app.models import Import, Note, Print, PrintQueueEntry, Revision
from app.models.library import Model
from tests.auth_fixtures import LoginAs, MakeUser

pytestmark = pytest.mark.usefixtures("library_root")

MSG = "only the creator or an admin can delete this"


async def _mk(client: httpx.AsyncClient, name: str) -> dict:
    r = await client.post("/api/models", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


async def test_create_model_records_creator(member_client, member) -> None:
    body = await _mk(member_client, "Owned")
    assert body["created_by_user_id"] == member.id
    assert body["created_by_username"] == member.username


async def test_other_rows_record_creator(
    member_client, member, db_session: AsyncSession, monkeypatch
) -> None:
    model = await _mk(member_client, "Rows")
    rev_id = model["current_revision"]["id"]

    r = await member_client.put(
        "/api/uploads",
        params={"model_id": model["id"], "revision_id": rev_id, "rel_path": "a.stl"},
        content=b"abc",
    )
    assert r.status_code == 201, r.text
    r = await member_client.post("/api/notes", json={"model_id": model["id"], "body": "n"})
    assert r.status_code == 201, r.text
    r = await member_client.post(f"/api/models/{model['id']}/revisions", json={"name": "v2"})
    assert r.status_code == 201, r.text
    r = await member_client.post(f"/api/models/{model['id']}/prints", json={"result": "success"})
    assert r.status_code == 201, r.text
    r = await member_client.post("/api/queue", json={"model_id": model["id"]})
    assert r.status_code == 201, r.text
    r = await member_client.post(
        "/api/collections",
        json={"site": "printables", "list_id": "L1", "kind": "likes", "title": "T"},
    )
    assert r.status_code == 201, r.text
    monkeypatch.setattr("app.services.imports._enqueue", lambda imp: None)
    r = await member_client.post(
        "/api/imports", json={"url": "https://www.printables.com/model/123-foo"}
    )
    assert r.status_code in (200, 201), r.text

    for cls in (Note, Print, PrintQueueEntry, Import, FileRow):
        owners = (await db_session.execute(select(cls.created_by_user_id))).scalars().all()
        assert owners and all(o == member.id for o in owners), cls
    revs = (
        await db_session.execute(
            select(Revision.number, Revision.created_by_user_id).where(
                Revision.model_id == model["id"]
            )
        )
    ).all()
    assert (2, member.id) in revs
    n = await db_session.scalar(
        text("select count(*) from followed_collections where created_by_user_id = :u"),
        {"u": member.id},
    )
    assert n == 1


async def test_member_cannot_delete_others_model(
    member_client, make_user: MakeUser, login_as: LoginAs
) -> None:
    model = await _mk(member_client, "Mine")
    other = await login_as(await make_user("member"))
    r = await other.delete(f"/api/models/{model['slug']}")
    assert r.status_code == 403
    assert r.json()["detail"] == MSG


async def test_member_can_delete_own_model(member_client) -> None:
    model = await _mk(member_client, "Own")
    assert (await member_client.delete(f"/api/models/{model['slug']}")).status_code == 204


async def test_admin_deletes_any_model(member_client, authenticated_client) -> None:
    model = await _mk(member_client, "AdminKills")
    r = await authenticated_client.delete(f"/api/models/{model['slug']}")
    assert r.status_code == 204


async def test_bulk_delete_with_one_foreign_slug_is_403_and_deletes_nothing(
    member_client, make_user: MakeUser, login_as: LoginAs
) -> None:
    mine = await _mk(member_client, "BulkMine")
    other = await login_as(await make_user("member"))
    theirs = await _mk(other, "BulkTheirs")
    r = await member_client.post(
        "/api/models/bulk-delete", json={"ids": [mine["id"], theirs["id"]]}
    )
    assert r.status_code == 403
    assert r.json()["detail"] == MSG
    assert (await member_client.get(f"/api/models/{mine['slug']}")).status_code == 200
    assert (await member_client.get(f"/api/models/{theirs['slug']}")).status_code == 200


async def test_null_creator_model_only_admin_deletes(
    member_client, authenticated_client, db_session: AsyncSession
) -> None:
    model = await _mk(authenticated_client, "Legacy")
    await db_session.execute(
        text("update models set created_by_user_id = NULL where id = :i"), {"i": model["id"]}
    )
    await db_session.commit()
    r = await member_client.delete(f"/api/models/{model['slug']}")
    assert r.status_code == 403
    assert r.json()["detail"] == MSG
    assert (await authenticated_client.delete(f"/api/models/{model['slug']}")).status_code == 204


async def test_note_delete_creator_or_admin(
    member_client, authenticated_client, make_user: MakeUser, login_as: LoginAs
) -> None:
    model = await _mk(member_client, "NoteM")
    note = (
        await member_client.post("/api/notes", json={"model_id": model["id"], "body": "x"})
    ).json()
    other = await login_as(await make_user("member"))
    assert (await other.delete(f"/api/notes/{note['id']}")).status_code == 403
    assert (await member_client.delete(f"/api/notes/{note['id']}")).status_code == 204
    note2 = (
        await member_client.post("/api/notes", json={"model_id": model["id"], "body": "y"})
    ).json()
    assert (await authenticated_client.delete(f"/api/notes/{note2['id']}")).status_code == 204


async def test_file_delete_creator_or_admin(
    member_client, authenticated_client, make_user: MakeUser, login_as: LoginAs
) -> None:
    model = await _mk(member_client, "FileM")
    up = await member_client.put(
        "/api/uploads",
        params={
            "model_id": model["id"],
            "revision_id": model["current_revision"]["id"],
            "rel_path": "p.stl",
        },
        content=b"hello",
    )
    fid = up.json()["file_id"]
    other = await login_as(await make_user("member"))
    assert (await other.delete(f"/api/files/{fid}")).status_code == 403
    assert (await member_client.delete(f"/api/files/{fid}")).status_code == 204


async def test_print_and_queue_delete_creator_or_admin(
    member_client, make_user: MakeUser, login_as: LoginAs
) -> None:
    model = await _mk(member_client, "PQ")
    pr = (
        await member_client.post(f"/api/models/{model['id']}/prints", json={"result": "success"})
    ).json()
    qe = (await member_client.post("/api/queue", json={"model_id": model["id"]})).json()
    other = await login_as(await make_user("member"))
    assert (await other.delete(f"/api/prints/{pr['id']}")).status_code == 403
    assert (await other.delete(f"/api/queue/{qe['id']}")).status_code == 403
    assert (await member_client.delete(f"/api/prints/{pr['id']}")).status_code == 204
    assert (await member_client.delete(f"/api/queue/{qe['id']}")).status_code == 204


@pytest.mark.usefixtures("printer_enabled")
async def test_get_print_job_404_for_invisible_printer(
    member_client, authenticated_client, db_session: AsyncSession, printer_row
) -> None:
    from app.models import Blob, PrintJob
    from app.models.enums import BlobFormat, BlobKind, PrintJobState

    db_session.add(Blob(hash="c" * 64, size=1, kind=BlobKind.SLICED, format=BlobFormat.GCODE_3MF))
    m = Model(slug="pjx", name="PJX")
    db_session.add(m)
    await db_session.flush()
    rev = Revision(model_id=m.id, number=1, dir_name="rev-001")
    db_session.add(rev)
    await db_session.flush()
    f = FileRow(
        revision_id=rev.id, blob_hash="c" * 64, rel_path="p.gcode.3mf", storage_path="pjx/p"
    )
    db_session.add(f)
    await db_session.flush()
    job = PrintJob(printer_id=printer_row.id, file_id=f.id, state=PrintJobState.FINISHED)
    db_session.add(job)
    await db_session.commit()
    assert (await member_client.get(f"/api/print-jobs/{job.id}")).status_code == 404
    assert (await authenticated_client.get(f"/api/print-jobs/{job.id}")).status_code == 200
