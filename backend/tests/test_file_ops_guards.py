"""File-move guards: foreign object at target, delete-old re-check, import lock."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.importers.download import StagedFile
from app.models import File, Model, Revision
from app.models.enums import BlobFormat, BlobKind
from app.services import library
from app.tasks import base, ingest, relocate
from tests.test_file_ops import _ids, _model, _rels
from tests.test_file_ops_hardening import _fresh_job, _run

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


async def test_foreign_object_at_target_aborts_move(
    authenticated_client, db_session, backend
) -> None:
    m = await _model(authenticated_client, "Foreign", ["a.stl"])
    ids = await _ids(db_session, m["id"])
    with base.sync_session() as s:
        old = s.get(File, ids["a.stl"]).storage_path
    new = old.rsplit("/", 1)[0] + "/n/a.stl"
    backend.write(new, [b"someone else's bytes"])
    jid = await _fresh_job(db_session, m["id"])
    with pytest.raises(RuntimeError, match="already holds an object"):
        _run(jid, m["id"], [[ids["a.stl"], "n/a.stl"]])
    db_session.expire_all()
    assert await _rels(db_session, m["id"]) == {"a.stl"}
    assert b"".join(backend.read(new)) == b"someone else's bytes"  # not clobbered
    assert b"".join(backend.read(old)) == b"content-Foreign-0"


async def test_old_object_kept_when_referenced_again(
    authenticated_client, db_session, backend, monkeypatch
) -> None:
    m = await _model(authenticated_client, "Rereferenced", ["a.stl", "b.stl"])
    ids = await _ids(db_session, m["id"])
    with base.sync_session() as s:
        old = s.get(File, ids["a.stl"]).storage_path
        other_id = ids["b.stl"]
    real = relocate.lock_model_row_sync
    calls: list[int] = []

    def lock_then_rereference(session, model_id):
        real(session, model_id)
        calls.append(1)
        if len(calls) == 1:  # the post-commit lock, right before deleting `old`
            session.execute(
                text("update files set storage_path=:p where id=:i"), {"p": old, "i": other_id}
            )

    monkeypatch.setattr(relocate, "lock_model_row_sync", lock_then_rereference)
    with base.sync_session() as s:
        f = s.get(File, ids["a.stl"])
        relocate._move_file_to(s, get_settings(), f, old.rsplit("/", 1)[0] + "/moved.stl")
    assert b"".join(backend.read(old)) == b"content-Rereferenced-0"  # delete skipped


async def test_store_imported_file_takes_model_lock(
    authenticated_client, db_session, monkeypatch, tmp_path
) -> None:
    m = await _model(authenticated_client, "ImportLock", [])
    seen: list[int] = []
    real = library.lock_model_row_sync
    monkeypatch.setattr(
        library, "lock_model_row_sync", lambda s, mid: (seen.append(mid), real(s, mid))
    )
    monkeypatch.setattr(ingest.store_to_backend, "apply_async", lambda *a, **k: None)
    spool = tmp_path / "spool.bin"
    spool.write_bytes(b"x")
    staged = StagedFile(
        token=uuid.uuid4(),
        spool_path=Path(spool),
        blob_hash="a" * 64,
        size=1,
        rel_path="imp.stl",
        kind=BlobKind.MESH,
        format_=BlobFormat.STL,
    )
    with base.sync_session() as s:
        model = s.get(Model, m["id"])
        revision = s.get(Revision, model.current_revision_id)
        library.store_imported_file_sync(s, model=model, revision=revision, staged=staged)
    assert seen == [m["id"]]


async def test_folder_move_takes_model_lock_before_file(
    authenticated_client, db_session, monkeypatch
) -> None:
    m = await _model(authenticated_client, "FolderLock", ["a/x.stl", "b/z.stl"])
    order: list[str] = []
    real = relocate.lock_model_row_sync
    monkeypatch.setattr(
        relocate, "lock_model_row_sync", lambda s, mid: (order.append("model"), real(s, mid))
    )
    orig = relocate._move_file_to
    monkeypatch.setattr(
        relocate, "_move_file_to", lambda *a, **k: (order.append("move"), orig(*a, **k))
    )
    root = f"{m['slug']}/{m['current_revision']['dir_name']}"
    r = await authenticated_client.post(
        "/api/storage/folders/move", json={"src": f"{root}/a", "dest": f"{root}/b"}
    )
    assert r.status_code == 200, r.text
    assert order[:2] == ["model", "move"]
