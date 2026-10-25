"""Library trash: soft delete, restore, purge, retention auto-purge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AuditLog
from app.models.library import Model
from app.services.stats import reset_stats_cache
from app.storage.local import LocalStorageBackend
from app.tasks.trash import purge_expired
from tests.auth_fixtures import LoginAs, MakeUser

pytestmark = pytest.mark.usefixtures("library_root")


async def _mk(client: httpx.AsyncClient, name: str, upload: bool = False) -> dict:
    r = await client.post("/api/models", json={"name": name})
    assert r.status_code == 201, r.text
    m = r.json()
    if upload:
        u = await client.put(
            "/api/uploads",
            params={
                "model_id": m["id"],
                "revision_id": m["current_revision"]["id"],
                "rel_path": "part.stl",
                "allow_duplicate": "true",
            },
            content=b"hello-world",
        )
        assert u.status_code == 201, u.text
    return m


async def test_trash_hides_from_library_and_stats(authenticated_client) -> None:
    keep = await _mk(authenticated_client, "Keep Me")
    gone = await _mk(authenticated_client, "Trash Me")
    reset_stats_cache()
    before = (await authenticated_client.get("/api/stats")).json()["models"]["total"]
    assert (await authenticated_client.delete(f"/api/models/{gone['slug']}")).status_code == 204

    gallery = (await authenticated_client.get("/api/models")).json()["items"]
    assert [i["id"] for i in gallery] == [keep["id"]]
    searched = (await authenticated_client.get("/api/models", params={"q": "Trash"})).json()
    assert searched["items"] == []
    assert (await authenticated_client.get(f"/api/models/{gone['slug']}")).status_code == 404
    reset_stats_cache()
    assert (await authenticated_client.get("/api/stats")).json()["models"]["total"] == before - 1

    listing = (await authenticated_client.get("/api/trash")).json()
    assert [i["id"] for i in listing["items"]] == [gone["id"]]
    assert listing["retention_days"] == 30
    assert listing["items"][0]["purge_at"] is not None


async def test_restore_brings_model_back_and_audits(
    authenticated_client, db_session: AsyncSession
) -> None:
    m = await _mk(authenticated_client, "Round Trip")
    assert (
        await authenticated_client.post("/api/models/bulk-delete", json={"ids": [m["id"]]})
    ).json() == {"deleted": 1}
    r = await authenticated_client.post("/api/trash/restore", json={"ids": [m["id"]]})
    assert r.json() == {"count": 1}
    assert (await authenticated_client.get(f"/api/models/{m['slug']}")).status_code == 200
    assert (await authenticated_client.get("/api/trash")).json()["items"] == []
    # restoring something that is not trashed 404s
    assert (
        await authenticated_client.post("/api/trash/restore", json={"ids": [m["id"]]})
    ).status_code == 404
    actions = (await db_session.execute(select(AuditLog.action))).scalars().all()
    assert "model.trash" in actions
    assert "model.restore" in actions


async def test_purge_selected_and_all_remove_blobs(
    authenticated_client, backend: LocalStorageBackend, db_session: AsyncSession
) -> None:
    a = await _mk(authenticated_client, "Purge A", upload=True)
    b = await _mk(authenticated_client, "Purge B", upload=True)
    c = await _mk(authenticated_client, "Purge C", upload=True)
    path = lambda m: f"{m['slug']}/{m['current_revision']['dir_name']}/part.stl"  # noqa: E731
    await authenticated_client.post(
        "/api/models/bulk-delete", json={"ids": [a["id"], b["id"], c["id"]]}
    )
    assert backend.exists(path(a))  # soft delete keeps the blob

    # a live (non-trashed) model cannot be purged
    live = await _mk(authenticated_client, "Live")
    assert (
        await authenticated_client.post("/api/trash/purge", json={"ids": [live["id"]]})
    ).status_code == 404

    r = await authenticated_client.post("/api/trash/purge", json={"ids": [a["id"]]})
    assert r.json() == {"count": 1}
    assert not backend.exists(path(a))
    assert backend.exists(path(b))

    r = await authenticated_client.post("/api/trash/purge-all")
    assert r.json() == {"count": 2}
    assert not backend.exists(path(b)) and not backend.exists(path(c))
    assert (await authenticated_client.get("/api/trash")).json()["items"] == []
    assert (await authenticated_client.get(f"/api/models/{live['slug']}")).status_code == 200
    actions = (await db_session.execute(select(AuditLog.action))).scalars().all()
    assert actions.count("model.purge") == 2


async def test_non_admin_only_touches_own_trash(
    member_client, authenticated_client, make_user: MakeUser, login_as: LoginAs
) -> None:
    mine = await _mk(member_client, "Mine")
    theirs = await _mk(authenticated_client, "Admins")
    await authenticated_client.post(
        "/api/models/bulk-delete", json={"ids": [mine["id"], theirs["id"]]}
    )
    r = await member_client.post("/api/trash/purge", json={"ids": [theirs["id"]]})
    assert r.status_code == 403
    assert (await member_client.post("/api/trash/purge-all")).json() == {"count": 1}
    left = (await authenticated_client.get("/api/trash")).json()["items"]
    assert [i["id"] for i in left] == [theirs["id"]]


async def test_auto_purge_honours_retention(
    authenticated_client, backend: LocalStorageBackend, db_session: AsyncSession
) -> None:
    old = await _mk(authenticated_client, "Old", upload=True)
    fresh = await _mk(authenticated_client, "Fresh", upload=True)
    await authenticated_client.post(
        "/api/models/bulk-delete", json={"ids": [old["id"], fresh["id"]]}
    )
    await db_session.execute(
        text("update models set deleted_at = :t where id = :i"),
        {"t": datetime.now(UTC) - timedelta(days=45), "i": old["id"]},
    )
    await db_session.commit()
    get_settings.cache_clear()

    assert purge_expired() == 1
    assert not backend.exists(f"{old['slug']}/{old['current_revision']['dir_name']}/part.stl")
    assert backend.exists(f"{fresh['slug']}/{fresh['current_revision']['dir_name']}/part.stl")
    ids = (await db_session.execute(select(Model.id))).scalars().all()
    assert old["id"] not in ids and fresh["id"] in ids

    # retention 0 = keep forever
    cur = (await authenticated_client.get("/api/settings/app")).json()
    cur["trash_retention_days"] = 0
    assert (await authenticated_client.put("/api/settings/app", json=cur)).status_code == 200
    await db_session.execute(
        text("update models set deleted_at = :t where id = :i"),
        {"t": datetime.now(UTC) - timedelta(days=400), "i": fresh["id"]},
    )
    await db_session.commit()
    assert purge_expired() == 0
