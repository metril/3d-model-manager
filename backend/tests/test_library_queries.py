"""Library query efficiency/correctness: batched store-pending check,
NULL-safe ``list_models`` filters, paginated storage tree root."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import blake3
import httpx
import pytest
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Blob, File, Job, Model, Revision
from app.models.enums import BlobFormat, BlobKind
from app.services import jobs as jobs_service
from app.services import library

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


async def _create_model(client: httpx.AsyncClient, name: str) -> dict:
    response = await client.post("/api/models", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def _seed_files(db: AsyncSession, model: dict, names: list[str]) -> list[File]:
    revision = await db.get(Revision, model["current_revision"]["id"])
    digest = blake3.blake3(b"x").hexdigest()
    if await db.get(Blob, digest) is None:
        db.add(Blob(hash=digest, size=1, kind=BlobKind.OTHER, format=BlobFormat.OTHER))
        await db.flush()
    files = [
        File(
            revision_id=revision.id,
            blob_hash=digest,
            rel_path=n,
            storage_path=f"{model['slug']}/{revision.dir_name}/{n}",
            verified_at=None,
        )
        for n in names
    ]
    db.add_all(files)
    await db.commit()
    return files


async def test_files_store_pending_is_one_query_and_uses_latest_job(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    model = await _create_model(authenticated_client, "Pending Batch")
    files = await _seed_files(db_session, model, [f"f{i}.bin" for i in range(5)])
    fid = [f.id for f in files]
    now = datetime.now(UTC)

    def job(file_id: int, state: str, age: int) -> Job:
        return Job(
            id=uuid.uuid4(),
            type="store_to_backend",
            subject_type="file",
            subject_id=file_id,
            state=state,
            created_at=now - timedelta(minutes=age),
        )

    # f0 queued -> pending; f1 queued then failed (latest wins) -> not pending;
    # f2 running -> pending; f3/f4 no job -> not pending.
    db_session.add_all(
        [
            job(fid[0], jobs_service.STATE_QUEUED, 1),
            job(fid[1], jobs_service.STATE_QUEUED, 5),
            job(fid[1], "failed", 1),
            job(fid[2], jobs_service.STATE_RUNNING, 1),
        ]
    )
    await db_session.commit()

    statements: list[str] = []
    sync_engine = db_session.bind.sync_engine

    def _count(conn, cursor, statement, *_a) -> None:  # noqa: ANN001
        statements.append(statement)

    event.listen(sync_engine, "before_cursor_execute", _count)
    try:
        pending = await library._files_store_pending(db_session, files)
    finally:
        event.remove(sync_engine, "before_cursor_execute", _count)

    assert pending == {fid[0], fid[2]}
    assert len([s for s in statements if "jobs" in s]) == 1


async def test_list_models_includes_null_current_revision_with_has_sliced_false(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    model = await _create_model(authenticated_client, "No Current Revision")
    await db_session.execute(
        update(Model).where(Model.id == model["id"]).values(current_revision_id=None)
    )
    await db_session.commit()

    response = await authenticated_client.get("/api/models?has_sliced=false")
    assert response.status_code == 200, response.text
    assert model["id"] in {m["id"] for m in response.json()["items"]}

    sliced = await authenticated_client.get("/api/models?has_sliced=true")
    assert model["id"] not in {m["id"] for m in sliced.json()["items"]}


async def test_storage_tree_root_dirs_aggregated_and_files_paginated(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    model = await _create_model(authenticated_client, "Tree Pages")
    await _seed_files(db_session, model, ["a.bin", "b.bin", "c.bin", "sub/d.bin"])
    dir_name = model["current_revision"]["dir_name"]

    root = (await authenticated_client.get("/api/storage/tree")).json()
    entry = next(d for d in root["dirs"] if d["name"] == model["slug"])
    assert entry["file_count"] == 4
    assert entry["model_count"] == 1
    assert root["files"] == []

    base = f"/api/storage/tree?path={model['slug']}/{dir_name}"
    page1 = (await authenticated_client.get(f"{base}&limit=2")).json()
    page2 = (await authenticated_client.get(f"{base}&limit=2&offset=2")).json()
    assert [f["name"] for f in page1["files"]] == ["a.bin", "b.bin"]
    assert [f["name"] for f in page2["files"]] == ["c.bin"]
    assert page1["total_files"] == page2["total_files"] == 3
    assert [d["name"] for d in page1["dirs"]] == ["sub"]
