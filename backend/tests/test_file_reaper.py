"""Reaper for unverified files whose store job failed long ago."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import File
from app.services import file_reaper
from app.storage.local import LocalStorageBackend
from app.tasks import base

pytestmark = pytest.mark.usefixtures("library_root", "data_dir")


async def _upload(client: httpx.AsyncClient, model: dict, name: str) -> int:
    r = await client.put(
        "/api/uploads",
        params={
            "model_id": model["id"],
            "revision_id": model["current_revision"]["id"],
            "rel_path": name,
        },
        content=name.encode() * 3,
    )
    assert r.status_code == 201, r.text
    return r.json()["file_id"]


async def test_reaps_only_old_failed_unverified_files(
    authenticated_client: httpx.AsyncClient,
    backend: LocalStorageBackend,
    db_session: AsyncSession,
) -> None:
    m = (await authenticated_client.post("/api/models", json={"name": "Reap Me"})).json()
    old_failed = await _upload(authenticated_client, m, "a.stl")
    old_running = await _upload(authenticated_client, m, "b.stl")
    new_failed = await _upload(authenticated_client, m, "c.stl")
    verified = await _upload(authenticated_client, m, "d.stl")
    old = datetime.now(UTC) - timedelta(hours=2)
    recent = datetime.now(UTC) - timedelta(minutes=5)
    for fid, state, at, unverify in [
        (old_failed, "failed", old, True),
        (old_running, "running", old, True),
        (new_failed, "failed", recent, True),
        (verified, "failed", old, False),
    ]:
        if unverify:
            await db_session.execute(
                text("update files set verified_at = null where id = :i"), {"i": fid}
            )
        await db_session.execute(
            text("update jobs set state = :s, created_at = :t where subject_id = :i"),
            {"s": state, "t": at, "i": fid},
        )
    await db_session.commit()
    key = (await db_session.get(File, old_failed)).storage_path
    assert backend.exists(key)

    with base.sync_session() as s:
        assert file_reaper.reap_unverified_files_sync(s, get_settings()) == 1

    db_session.expire_all()
    left = set((await db_session.execute(select(File.id))).scalars())
    assert left == {old_running, new_failed, verified}
    assert not backend.exists(key)
