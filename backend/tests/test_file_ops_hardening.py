"""File-move hardening: path normalization, clashes, run-time re-validation."""

from __future__ import annotations

import uuid

import pytest
from celery.exceptions import Retry
from sqlalchemy import text

from app.config import get_settings
from app.models import File
from app.services import jobs as jobs_service
from app.storage.base import WriteResult
from app.storage.local import LocalStorageBackend
from app.tasks import base, pipeline, relocate
from tests.test_file_ops import _ids, _model, _rels

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


@pytest.mark.parametrize("bad", ["./c.stl", "sub//c.stl", "c.stl/", "sub/./c.stl", "./a.stl"])
async def test_non_normalized_paths_rejected(authenticated_client, db_session, bad) -> None:
    m = await _model(authenticated_client, "NormPath", ["a.stl"])
    ids = await _ids(db_session, m["id"])
    r = await authenticated_client.patch(f"/api/files/{ids['a.stl']}", json={"rel_path": bad})
    assert r.status_code == 400, r.text
    assert await _rels(db_session, m["id"]) == {"a.stl"}


async def test_case_insensitive_clash_but_case_rename_ok(authenticated_client, db_session) -> None:
    m = await _model(authenticated_client, "CaseClash", ["a.stl", "b.stl"])
    ids = await _ids(db_session, m["id"])
    r = await authenticated_client.patch(f"/api/files/{ids['a.stl']}", json={"rel_path": "B.STL"})
    assert r.status_code == 409, r.text
    r = await authenticated_client.patch(f"/api/files/{ids['a.stl']}", json={"rel_path": "A.stl"})
    assert r.status_code == 202, r.text
    db_session.expire_all()
    assert await _rels(db_session, m["id"]) == {"A.stl", "b.stl"}


async def test_file_folder_clashes(authenticated_client, db_session) -> None:
    m = await _model(authenticated_client, "FFClash", ["a.stl", "b.stl", "x/y.stl"])
    ids = await _ids(db_session, m["id"])
    patch = authenticated_client.patch
    assert (await patch(f"/api/files/{ids['a.stl']}", json={"rel_path": "x"})).status_code == 409
    r = await patch(f"/api/files/{ids['a.stl']}", json={"rel_path": "b.stl/z.stl"})
    assert r.status_code == 409
    r = await authenticated_client.post(
        f"/api/models/{m['id']}/files/move",
        json={
            "moves": [
                {"file_id": ids["a.stl"], "rel_path": "n"},
                {"file_id": ids["b.stl"], "rel_path": "n/z.stl"},
            ]
        },
    )
    assert r.status_code == 409, r.text
    assert await _rels(db_session, m["id"]) == {"a.stl", "b.stl", "x/y.stl"}


async def test_snapshot_source_rejected(authenticated_client, db_session) -> None:
    m = await _model(authenticated_client, "SnapSrc", ["a.stl"])
    await db_session.execute(
        text("update files set rel_path='_snapshots/cover.png' where rel_path='a.stl'")
    )
    await db_session.commit()
    ids = await _ids(db_session, m["id"])
    r = await authenticated_client.patch(
        f"/api/files/{ids['_snapshots/cover.png']}", json={"rel_path": "cover.png"}
    )
    assert r.status_code == 400, r.text


async def test_authz_before_validation(member_client, make_user, login_as, db_session) -> None:
    m = await _model(member_client, "AuthzFirst", ["a.stl"])
    ids = await _ids(db_session, m["id"])
    other = await login_as(await make_user("member"))
    r = await other.patch(f"/api/files/{ids['a.stl']}", json={"rel_path": "../bad"})
    assert r.status_code == 403
    assert "modify" in r.json()["detail"]


def _run(job_id: str, model_id: int, moves: list[list], **kw):
    return relocate.move_model_files.apply(args=[job_id, model_id, moves], **kw)


async def _fresh_job(db_session, model_id: int) -> str:
    job = await jobs_service.create_job(
        db_session,
        id=uuid.uuid4(),
        type="move_model_files",
        subject_type="model",
        subject_id=model_id,
    )
    return str(job.id)


async def test_task_revalidates_and_partial_batch_resumes(authenticated_client, db_session) -> None:
    m = await _model(authenticated_client, "TaskRecheck", ["a.stl", "b.stl", "c.stl"])
    ids = await _ids(db_session, m["id"])
    jid = await _fresh_job(db_session, m["id"])
    # c.stl exists, so the second move is rejected at run time; the first still lands
    with pytest.raises(RuntimeError, match="conflicts"):
        _run(jid, m["id"], [[ids["a.stl"], "n/a.stl"], [ids["b.stl"], "c.stl"]])
    db_session.expire_all()
    assert await _rels(db_session, m["id"]) == {"n/a.stl", "b.stl", "c.stl"}
    state = (
        await db_session.execute(text("select state from jobs where id=:j"), {"j": jid})
    ).scalar()
    assert state == "failed"
    # resume: the already-moved file is skipped, nothing lost or duplicated
    _run(jid, m["id"], [[ids["a.stl"], "n/a.stl"]])
    db_session.expire_all()
    assert await _rels(db_session, m["id"]) == {"n/a.stl", "b.stl", "c.stl"}


async def test_task_rejects_non_current_revision(authenticated_client, db_session) -> None:
    m = await _model(authenticated_client, "TaskOldRev", ["a.stl"])
    ids = await _ids(db_session, m["id"])
    r = await authenticated_client.post(f"/api/models/{m['id']}/revisions", json={})
    assert r.status_code in (200, 201)
    jid = await _fresh_job(db_session, m["id"])
    with pytest.raises(RuntimeError, match="current revision"):
        _run(jid, m["id"], [[ids["a.stl"], "q.stl"]])
    db_session.expire_all()
    assert "q.stl" not in await _rels(db_session, m["id"])


async def test_hash_mismatch_cleans_only_own_writes(
    authenticated_client, db_session, backend, monkeypatch
) -> None:
    m = await _model(authenticated_client, "HashMis", ["a.stl"])
    ids = await _ids(db_session, m["id"])
    orig = LocalStorageBackend.write

    def bad_write(self, key, chunks):
        res = orig(self, key, chunks)
        return WriteResult(hash="0" * 64, size=res.size)

    monkeypatch.setattr(LocalStorageBackend, "write", bad_write)
    with base.sync_session() as s:
        f = s.get(File, ids["a.stl"])
        old = f.storage_path
        new = old.rsplit("/", 1)[0] + "/moved.stl"
        with pytest.raises(RuntimeError, match="hash mismatch"):
            relocate._move_file_to(s, get_settings(), f, new)
    with pytest.raises(Exception):  # noqa: B017 -- the stray copy must be gone
        b"".join(backend.read(new))
    assert b"".join(backend.read(old)) == b"content-HashMis-0"


async def test_transient_errors_retry_then_fail(
    authenticated_client, db_session, monkeypatch
) -> None:
    m = await _model(authenticated_client, "Transient", ["a.stl"])
    ids = await _ids(db_session, m["id"])
    calls: list[int] = []

    def boom(*a, **k):
        calls.append(1)
        raise OSError("disk hiccup")

    monkeypatch.setattr(relocate, "_move_file_to", boom)
    jid = await _fresh_job(db_session, m["id"])
    # within the retry budget: a Retry is raised and the job is NOT failed
    with pytest.raises(Retry):
        _run(jid, m["id"], [[ids["a.stl"], "t/a.stl"]])
    state = lambda: db_session.execute(text("select state from jobs where id=:j"), {"j": jid})  # noqa: E731
    assert (await state()).scalar() == "running"
    # budget exhausted: the job fails
    with pytest.raises(OSError):
        _run(jid, m["id"], [[ids["a.stl"], "t/a.stl"]], retries=3)
    assert (await state()).scalar() == "failed"
    assert len(calls) == 2
    db_session.expire_all()
    assert await _rels(db_session, m["id"]) == {"a.stl"}


async def test_assembly_enqueued_for_this_revision_only(
    authenticated_client, db_session, monkeypatch
) -> None:
    m = await _model(authenticated_client, "AsmScope", ["a.stl"])
    ids = await _ids(db_session, m["id"])
    seen: list[int] = []
    monkeypatch.setattr(
        pipeline, "maybe_enqueue_assembly_for_revision_sync", lambda s, rid: seen.append(rid)
    )
    monkeypatch.setattr(
        pipeline, "maybe_enqueue_assembly_sync", lambda *a, **k: pytest.fail("blob-wide enqueue")
    )
    r = await authenticated_client.patch(f"/api/files/{ids['a.stl']}", json={"rel_path": "s/a.stl"})
    assert r.status_code == 202
    assert seen == [m["current_revision"]["id"]]
