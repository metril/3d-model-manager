"""Folder move guards: owner-or-admin, pending-store 409, enqueue failure 503."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import update

from app.models import File
from app.tasks.relocate import move_storage_folder

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


async def _model_with_files(c: httpx.AsyncClient, name: str, paths: list[str]) -> dict:
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


def _body(m: dict) -> dict:
    root = f"{m['slug']}/{m['current_revision']['dir_name']}"
    return {"src": f"{root}/a", "dest": f"{root}/b"}


async def test_folder_move_requires_owner_or_admin(member_client, make_user, login_as) -> None:
    m = await _model_with_files(member_client, "OwnedMove", ["a/x.stl", "b/z.stl"])
    other = await login_as(await make_user("member"))
    assert (await other.post("/api/storage/folders/move", json=_body(m))).status_code == 403
    assert (await member_client.post("/api/storage/folders/move", json=_body(m))).status_code == 200


async def test_folder_move_blocked_while_unverified(
    authenticated_client: httpx.AsyncClient, db_session
) -> None:
    m = await _model_with_files(authenticated_client, "Pending", ["a/x.stl", "b/z.stl"])
    await db_session.execute(
        update(File).where(File.rel_path == "a/x.stl").values(verified_at=None)
    )
    await db_session.commit()
    r = await authenticated_client.post("/api/storage/folders/move", json=_body(m))
    assert r.status_code == 409


async def test_folder_move_enqueue_failure_is_503(
    authenticated_client: httpx.AsyncClient, monkeypatch
) -> None:
    def boom(*a, **k):
        raise RuntimeError("broker down")

    monkeypatch.setattr(move_storage_folder, "apply_async", boom)
    m = await _model_with_files(authenticated_client, "Broker", ["a/x.stx", "b/z.stl"])
    r = await authenticated_client.post("/api/storage/folders/move", json=_body(m))
    assert r.status_code == 503
    assert (await authenticated_client.get(f"/api/jobs/{uuid.uuid4()}")).status_code == 404
