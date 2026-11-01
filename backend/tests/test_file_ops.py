"""File rename/move: ``PATCH /files/{id}`` and ``POST /models/{id}/files/move``."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select, text

from app.models import File
from app.tasks import relocate

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


async def _model(c: httpx.AsyncClient, name: str, paths: list[str]) -> dict:
    m = (await c.post("/api/models", json={"name": name})).json()
    for i, rel in enumerate(paths):
        r = await c.put(
            "/api/uploads",
            params={
                "model_id": m["id"],
                "revision_id": m["current_revision"]["id"],
                "rel_path": rel,
            },
            content=f"content-{name}-{i}".encode(),
        )
        assert r.status_code == 201, r.text
    return m


async def _ids(db_session, model_id: int) -> dict[str, int]:
    rows = await db_session.execute(
        text(
            "select f.rel_path, f.id from files f join revisions r on r.id=f.revision_id "
            "where r.model_id=:m"
        ),
        {"m": model_id},
    )
    return {rel: fid for rel, fid in rows.all()}


async def _rels(db_session, model_id: int) -> set[str]:
    return set(await _ids(db_session, model_id))


async def _tag(c: httpx.AsyncClient, m: dict, rel: str, name: str) -> None:
    t = (await c.post("/api/tags", json={"name": name})).json()
    r = await c.put(
        f"/api/models/{m['id']}/files/tags", json={"rel_path": rel, "tag_ids": [t["id"]]}
    )
    assert r.status_code == 200, r.text


async def _file_tag_paths(db_session, model_id: int) -> set[str]:
    rows = await db_session.execute(
        text("select rel_path from model_file_tags where model_id=:m"), {"m": model_id}
    )
    return {r[0] for r in rows}


async def test_rename_one_file(authenticated_client, db_session, backend) -> None:
    m = await _model(authenticated_client, "RenameOne", ["a.stl", "b.stl"])
    ids = await _ids(db_session, m["id"])
    r = await authenticated_client.patch(
        f"/api/files/{ids['a.stl']}", json={"rel_path": "sub/c.stl"}
    )
    assert r.status_code == 202, r.text
    assert r.json()["state"] == "done"
    db_session.expire_all()
    assert await _rels(db_session, m["id"]) == {"sub/c.stl", "b.stl"}
    f = (await db_session.execute(select(File).where(File.rel_path == "sub/c.stl"))).scalar_one()
    assert f.storage_path.endswith("/sub/c.stl")
    assert b"".join(backend.read(f.storage_path)) == b"content-RenameOne-0"


async def test_batch_folder_move_and_tags(authenticated_client, db_session) -> None:
    m = await _model(authenticated_client, "FolderMv", ["a/x.stl", "a/y.stl", "z.stl"])
    await _tag(authenticated_client, m, "a/x.stl", "keepme")
    ids = await _ids(db_session, m["id"])
    r = await authenticated_client.post(
        f"/api/models/{m['id']}/files/move",
        json={
            "moves": [
                {"file_id": ids["a/x.stl"], "rel_path": "b/x.stl"},
                {"file_id": ids["a/y.stl"], "rel_path": "b/y.stl"},
            ]
        },
    )
    assert r.status_code == 202, r.text
    db_session.expire_all()
    assert await _rels(db_session, m["id"]) == {"b/x.stl", "b/y.stl", "z.stl"}
    assert await _file_tag_paths(db_session, m["id"]) == {"b/x.stl"}


async def test_collision_with_existing_and_within_batch(authenticated_client, db_session) -> None:
    m = await _model(authenticated_client, "Collide", ["a.stl", "b.stl", "c.stl"])
    ids = await _ids(db_session, m["id"])
    r = await authenticated_client.patch(f"/api/files/{ids['a.stl']}", json={"rel_path": "b.stl"})
    assert r.status_code == 409
    r = await authenticated_client.post(
        f"/api/models/{m['id']}/files/move",
        json={
            "moves": [
                {"file_id": ids["a.stl"], "rel_path": "n/x.stl"},
                {"file_id": ids["b.stl"], "rel_path": "n/x.stl"},
            ]
        },
    )
    assert r.status_code == 409
    assert await _rels(db_session, m["id"]) == {"a.stl", "b.stl", "c.stl"}


@pytest.mark.parametrize("bad", ["_snapshots/x.stl", "../x.stl", "/abs.stl"])
async def test_unsafe_paths_rejected(authenticated_client, db_session, bad) -> None:
    m = await _model(authenticated_client, "BadPath", ["a.stl"])
    ids = await _ids(db_session, m["id"])
    r = await authenticated_client.patch(f"/api/files/{ids['a.stl']}", json={"rel_path": bad})
    assert r.status_code == 400, r.text


async def test_non_current_revision_409(authenticated_client, db_session) -> None:
    m = await _model(authenticated_client, "OldRev", ["a.stl"])
    ids = await _ids(db_session, m["id"])
    r = await authenticated_client.post(f"/api/models/{m['id']}/revisions", json={})
    assert r.status_code in (200, 201), r.text
    r = await authenticated_client.patch(f"/api/files/{ids['a.stl']}", json={"rel_path": "q.stl"})
    assert r.status_code == 409, r.text


async def test_pending_store_409(authenticated_client, db_session) -> None:
    m = await _model(authenticated_client, "PendMv", ["a.stl"])
    ids = await _ids(db_session, m["id"])
    await db_session.execute(
        text(
            "insert into jobs (id, type, state, subject_type, subject_id, created_at) "
            "values (gen_random_uuid(), 'store_to_backend', 'queued', 'file', :f, "
            "now() + interval '1 minute')"
        ),
        {"f": ids["a.stl"]},
    )
    await db_session.execute(
        text("update files set verified_at=NULL where id=:f"), {"f": ids["a.stl"]}
    )
    await db_session.commit()
    r = await authenticated_client.patch(f"/api/files/{ids['a.stl']}", json={"rel_path": "q.stl"})
    assert r.status_code == 409


async def test_non_owner_403(member_client, make_user, login_as, db_session) -> None:
    m = await _model(member_client, "OwnedFiles", ["a.stl"])
    ids = await _ids(db_session, m["id"])
    other = await login_as(await make_user("member"))
    r = await other.patch(f"/api/files/{ids['a.stl']}", json={"rel_path": "q.stl"})
    assert r.status_code == 403
    r = await member_client.patch(f"/api/files/{ids['a.stl']}", json={"rel_path": "q.stl"})
    assert r.status_code == 202


async def test_move_storage_folder_keeps_tags(authenticated_client, db_session) -> None:
    m = await _model(authenticated_client, "FolderTags", ["a/x.stl", "b/z.stl"])
    await _tag(authenticated_client, m, "a/x.stl", "folder-tag")
    root = f"{m['slug']}/{m['current_revision']['dir_name']}"
    r = await authenticated_client.post(
        "/api/storage/folders/move", json={"src": f"{root}/a", "dest": f"{root}/b"}
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert await _rels(db_session, m["id"]) == {"b/a/x.stl", "b/z.stl"}
    assert await _file_tag_paths(db_session, m["id"]) == {"b/a/x.stl"}


async def test_retry_is_idempotent(authenticated_client, db_session) -> None:
    m = await _model(authenticated_client, "Retry", ["a.stl"])
    ids = await _ids(db_session, m["id"])
    r = await authenticated_client.patch(f"/api/files/{ids['a.stl']}", json={"rel_path": "r/a.stl"})
    assert r.status_code == 202
    # re-running the same task (a retry) is a no-op and does not fail
    relocate.move_model_files.apply(args=[r.json()["id"], m["id"], [[ids["a.stl"], "r/a.stl"]]])
    db_session.expire_all()
    assert await _rels(db_session, m["id"]) == {"r/a.stl"}
