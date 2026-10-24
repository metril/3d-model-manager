"""Duplicate-files report (Branch 4 Task 1): ``GET /reports/duplicates``
groups ``files`` rows by blob hash, keeping only hashes whose files span
more than one distinct model.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import blake3
import httpx
import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import BlobFormat, BlobKind
from app.models.library import Blob, File, Model, Revision
from app.services import jobs as jobs_service
from app.storage.local import LocalStorageBackend

pytestmark = pytest.mark.usefixtures("library_root")


async def _create_model(client: httpx.AsyncClient, name: str) -> dict:
    response = await client.post("/api/models", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def _model_and_revision(db_session: AsyncSession, payload: dict) -> tuple[Model, Revision]:
    model = await db_session.get(Model, payload["id"])
    revision = await db_session.get(Revision, model.current_revision_id)
    return model, revision


async def _file_on_revision(db_session: AsyncSession, revision_id: int, rel_path: str) -> File:
    return (
        await db_session.execute(
            select(File).where(File.revision_id == revision_id, File.rel_path == rel_path)
        )
    ).scalar_one()


async def _file_row_exists(db_session: AsyncSession, file_id: int) -> bool:
    """Whether a ``File`` row survives, queried via a fresh ``select()``
    rather than ``AsyncSession.get()`` -- ``get()``'s identity-map-first path
    doesn't play well with this suite's ASGI-in-process test client when
    checked right after an HTTP response carrying a JSON body (a
    ``response_model=`` round trip through ``httpx.ASGITransport`` on the
    SAME event loop as this session), so every post-request row check in
    this module goes through a plain query instead.
    """
    rows = (await db_session.execute(select(File.id).where(File.id == file_id))).scalars().all()
    return len(rows) == 1


async def _seed_pending_copy(
    db_session: AsyncSession, model: Model, revision: Revision, rel_path: str, content: bytes
) -> File:
    """Like ``seed_file``, but plants a ``File`` row whose store job hasn't
    settled yet (``verified_at`` NULL, a live ``queued`` job) -- the
    ``try_delete_duplicate_copy`` "store_pending" skip reason's setup
    (mirrors ``test_revisions_api._seed_pending_file``). Checks for an
    existing ``Blob`` row first, unlike that helper, since these tests seed
    it ALONGSIDE a real ``seed_file()`` copy sharing the same content/hash
    (a duplicate group needs >1 file sharing a hash by construction).
    """
    digest = blake3.blake3(content).hexdigest()
    blob = await db_session.get(Blob, digest)
    if blob is None:
        blob = Blob(hash=digest, size=len(content), kind=BlobKind.MESH, format=BlobFormat.STL)
        db_session.add(blob)
        await db_session.flush()
    file = File(
        revision_id=revision.id,
        blob_hash=digest,
        rel_path=rel_path,
        storage_path=f"{model.slug}/{revision.dir_name}/{rel_path}",
        verified_at=None,
    )
    db_session.add(file)
    await db_session.flush()
    await jobs_service.create_job(
        db_session,
        id=uuid.uuid4(),
        type="store_to_backend",
        subject_type="file",
        subject_id=file.id,
    )
    await db_session.commit()
    await db_session.refresh(file)
    return file


async def test_duplicate_group_across_two_models(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model_a = await _create_model(authenticated_client, "Duplicate A")
    model_b = await _create_model(authenticated_client, "Duplicate B")

    a, a_rev = await _model_and_revision(db_session, model_a)
    file_a = await seed_file(a, a_rev, "part.stl", b"shared-bytes")

    b, b_rev = await _model_and_revision(db_session, model_b)
    file_b = await seed_file(b, b_rev, "clone.stl", b"shared-bytes")

    assert file_a.blob_hash == file_b.blob_hash

    response = await authenticated_client.get("/api/reports/duplicates")
    assert response.status_code == 200
    body = response.json()

    assert len(body["groups"]) == 1
    group = body["groups"][0]
    assert group["blob_hash"] == file_a.blob_hash
    assert group["size"] == len(b"shared-bytes")
    assert group["wasted_bytes"] == len(b"shared-bytes")  # 2 files -> 1 wasted copy
    assert body["total_wasted_bytes"] == group["wasted_bytes"]

    file_entries = {(f["model_id"], f["file_name"]) for f in group["files"]}
    assert file_entries == {(model_a["id"], "part.stl"), (model_b["id"], "clone.stl")}
    for entry in group["files"]:
        if entry["model_id"] == model_a["id"]:
            assert entry["model_slug"] == model_a["slug"]
            assert entry["model_name"] == model_a["name"]
            assert entry["file_id"] == file_a.id


async def test_unique_blobs_are_excluded(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model = await _create_model(authenticated_client, "Unique Only")
    m, rev = await _model_and_revision(db_session, model)
    await seed_file(m, rev, "solo.stl", b"one-of-a-kind")

    response = await authenticated_client.get("/api/reports/duplicates")
    body = response.json()

    assert body["groups"] == []
    assert body["total_wasted_bytes"] == 0


async def test_same_model_two_files_sharing_hash_is_not_a_group(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    """A hash spanning two files that both belong to the SAME model doesn't
    count -- the report exists to find duplication ACROSS models.
    """
    model = await _create_model(authenticated_client, "Self Duplicate")
    m, rev = await _model_and_revision(db_session, model)
    await seed_file(m, rev, "a.stl", b"same-content-twice")
    await seed_file(m, rev, "b.stl", b"same-content-twice")

    response = await authenticated_client.get("/api/reports/duplicates")
    body = response.json()

    assert body["groups"] == []
    assert body["total_wasted_bytes"] == 0


async def test_archived_model_stays_in_group_and_is_labeled(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    """Fix-review F4: storage is per-file, so an archived model's bytes are
    still real wasted storage -- it stays in the report, just LABELED via
    ``model_archived``, rather than being silently dropped.

    feat/import-fidelity T3: archiving moved from ``DELETE`` (now a real
    hard delete that removes the row entirely) to ``PATCH {"is_archived":
    true}`` -- updated here to match.
    """
    model_a = await _create_model(authenticated_client, "Archived Dup A")
    model_b = await _create_model(authenticated_client, "Archived Dup B")

    a, a_rev = await _model_and_revision(db_session, model_a)
    file_a = await seed_file(a, a_rev, "part.stl", b"archived-shared-bytes")

    b, b_rev = await _model_and_revision(db_session, model_b)
    await seed_file(b, b_rev, "clone.stl", b"archived-shared-bytes")

    archived = await authenticated_client.patch(
        f"/api/models/{model_a['slug']}", json={"is_archived": True}
    )
    assert archived.status_code == 200, archived.text

    response = await authenticated_client.get("/api/reports/duplicates")
    assert response.status_code == 200
    body = response.json()

    assert len(body["groups"]) == 1
    entries = {f["model_id"]: f for f in body["groups"][0]["files"]}
    assert entries[model_a["id"]]["model_archived"] is True
    assert entries[model_a["id"]]["file_id"] == file_a.id
    assert entries[model_b["id"]]["model_archived"] is False


async def test_groups_sorted_by_wasted_bytes_descending(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    small_a = await _create_model(authenticated_client, "Small A")
    small_b = await _create_model(authenticated_client, "Small B")
    big_a = await _create_model(authenticated_client, "Big A")
    big_b = await _create_model(authenticated_client, "Big B")
    big_c = await _create_model(authenticated_client, "Big C")

    sa_m, sa_rev = await _model_and_revision(db_session, small_a)
    await seed_file(sa_m, sa_rev, "s.stl", b"x" * 100)
    sb_m, sb_rev = await _model_and_revision(db_session, small_b)
    await seed_file(sb_m, sb_rev, "s.stl", b"x" * 100)

    ba_m, ba_rev = await _model_and_revision(db_session, big_a)
    await seed_file(ba_m, ba_rev, "b.stl", b"y" * 10000)
    bb_m, bb_rev = await _model_and_revision(db_session, big_b)
    await seed_file(bb_m, bb_rev, "b.stl", b"y" * 10000)
    bc_m, bc_rev = await _model_and_revision(db_session, big_c)
    await seed_file(bc_m, bc_rev, "b.stl", b"y" * 10000)

    response = await authenticated_client.get("/api/reports/duplicates")
    body = response.json()

    assert len(body["groups"]) == 2
    assert body["groups"][0]["wasted_bytes"] == 10000 * 2
    assert body["groups"][1]["wasted_bytes"] == 100 * 1
    assert body["groups"][0]["wasted_bytes"] > body["groups"][1]["wasted_bytes"]
    assert body["total_wasted_bytes"] == 10000 * 2 + 100 * 1


# ---------------------------------------------------------------------------
# resolve (Round 11 Task 2): POST /reports/duplicates/resolve -- the client
# names a keeper per duplicate group; the server deletes every OTHER copy in
# that group. The keeper is never a delete candidate, so "a copy always
# survives" is structural, not a guard this endpoint has to enforce.
# ---------------------------------------------------------------------------


async def test_resolve_honors_chosen_keeper(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model_a = await _create_model(authenticated_client, "Keeper Choice A")
    model_b = await _create_model(authenticated_client, "Keeper Choice B")
    model_c = await _create_model(authenticated_client, "Keeper Choice C")

    a, a_rev = await _model_and_revision(db_session, model_a)
    file_a = await seed_file(a, a_rev, "part.stl", b"triplicate-bytes")
    b, b_rev = await _model_and_revision(db_session, model_b)
    file_b = await seed_file(b, b_rev, "part.stl", b"triplicate-bytes")
    c, c_rev = await _model_and_revision(db_session, model_c)
    file_c = await seed_file(c, c_rev, "part.stl", b"triplicate-bytes")

    size = len(b"triplicate-bytes")
    blob_hash = file_a.blob_hash

    # Keep the LAST-created (highest model_id) copy -- not the lowest -- to
    # rule out an implementation that silently always keeps the first row.
    response = await authenticated_client.post(
        "/api/reports/duplicates/resolve",
        json={"keep": [{"blob_hash": blob_hash, "file_id": file_c.id}]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"deleted": 2, "reclaimed_bytes": 2 * size, "skipped": []}

    assert not await _file_row_exists(db_session, file_a.id)
    assert not await _file_row_exists(db_session, file_b.id)
    assert await _file_row_exists(db_session, file_c.id)

    assert not backend.exists(file_a.storage_path)
    assert not backend.exists(file_b.storage_path)
    assert backend.exists(file_c.storage_path)


async def test_resolve_scoped_group_leaves_others_untouched(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model_a1 = await _create_model(authenticated_client, "Scoped Group One A")
    model_a2 = await _create_model(authenticated_client, "Scoped Group One B")
    model_b1 = await _create_model(authenticated_client, "Scoped Group Two A")
    model_b2 = await _create_model(authenticated_client, "Scoped Group Two B")

    a1, a1_rev = await _model_and_revision(db_session, model_a1)
    keep_file = await seed_file(a1, a1_rev, "one.stl", b"group-one-bytes")
    a2, a2_rev = await _model_and_revision(db_session, model_a2)
    drop_file = await seed_file(a2, a2_rev, "one.stl", b"group-one-bytes")

    b1, b1_rev = await _model_and_revision(db_session, model_b1)
    other_file_1 = await seed_file(b1, b1_rev, "two.stl", b"group-two-bytes")
    b2, b2_rev = await _model_and_revision(db_session, model_b2)
    other_file_2 = await seed_file(b2, b2_rev, "two.stl", b"group-two-bytes")

    response = await authenticated_client.post(
        "/api/reports/duplicates/resolve",
        json={"keep": [{"blob_hash": keep_file.blob_hash, "file_id": keep_file.id}]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == 1
    assert body["skipped"] == []

    assert await _file_row_exists(db_session, keep_file.id)
    assert not await _file_row_exists(db_session, drop_file.id)
    # The other group was never named -- fully untouched.
    assert await _file_row_exists(db_session, other_file_1.id)
    assert await _file_row_exists(db_session, other_file_2.id)
    assert backend.exists(other_file_1.storage_path)
    assert backend.exists(other_file_2.storage_path)


async def test_resolve_unknown_blob_hash_404_nothing_deleted(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model_p = await _create_model(authenticated_client, "Unknown Hash P")
    model_q = await _create_model(authenticated_client, "Unknown Hash Q")

    p, p_rev = await _model_and_revision(db_session, model_p)
    keep_file = await seed_file(p, p_rev, "real.stl", b"real-duplicate-bytes")
    q, q_rev = await _model_and_revision(db_session, model_q)
    drop_file = await seed_file(q, q_rev, "real.stl", b"real-duplicate-bytes")

    response = await authenticated_client.post(
        "/api/reports/duplicates/resolve",
        json={
            "keep": [
                # A perfectly valid choice ...
                {"blob_hash": keep_file.blob_hash, "file_id": keep_file.id},
                # ... alongside a hash that names no current duplicate group.
                {"blob_hash": "0" * 64, "file_id": 999999},
            ]
        },
    )

    assert response.status_code == 404
    assert "0" * 64 in response.text

    # Validation runs BEFORE any deletion -- the otherwise-valid choice in
    # the same request must not have gone through either.
    assert await _file_row_exists(db_session, keep_file.id)
    assert await _file_row_exists(db_session, drop_file.id)


async def test_resolve_keeper_not_in_group_404_nothing_deleted(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model_a1 = await _create_model(authenticated_client, "Mismatched Keeper A1")
    model_a2 = await _create_model(authenticated_client, "Mismatched Keeper A2")
    model_b1 = await _create_model(authenticated_client, "Mismatched Keeper B1")
    model_b2 = await _create_model(authenticated_client, "Mismatched Keeper B2")

    a1, a1_rev = await _model_and_revision(db_session, model_a1)
    file_a1 = await seed_file(a1, a1_rev, "one.stl", b"mismatch-group-one")
    a2, a2_rev = await _model_and_revision(db_session, model_a2)
    file_a2 = await seed_file(a2, a2_rev, "one.stl", b"mismatch-group-one")

    b1, b1_rev = await _model_and_revision(db_session, model_b1)
    file_b1 = await seed_file(b1, b1_rev, "two.stl", b"mismatch-group-two")
    b2, b2_rev = await _model_and_revision(db_session, model_b2)
    file_b2 = await seed_file(b2, b2_rev, "two.stl", b"mismatch-group-two")

    response = await authenticated_client.post(
        "/api/reports/duplicates/resolve",
        json={
            "keep": [
                # A perfectly valid group-two choice listed FIRST -- an
                # implementation that interleaved validation with deletion
                # would delete group two's extra copy before ever noticing
                # the mismatch below. All-or-nothing means it must not.
                {"blob_hash": file_b1.blob_hash, "file_id": file_b1.id},
                # Valid hash (group one), but a file_id that belongs to group two.
                {"blob_hash": file_a1.blob_hash, "file_id": file_b1.id},
            ]
        },
    )

    assert response.status_code == 404
    assert str(file_b1.id) in response.text
    assert file_a1.blob_hash in response.text

    # Includes file_b2 -- the valid earlier choice's would-be delete target.
    for f in (file_a1, file_a2, file_b1, file_b2):
        assert await _file_row_exists(db_session, f.id)


async def test_resolve_skips_non_current_revision_copy(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    """A dupe copy sitting on a SUPERSEDED revision can't be hard-deleted
    (file ops are current-revision-only, same guard as ``DELETE
    /files/{id}``) -- ``resolve_duplicates`` skips it with a reason instead
    of failing the whole request.
    """
    model_a = await _create_model(authenticated_client, "Superseded Copy A")
    model_b = await _create_model(authenticated_client, "Superseded Copy B")

    a, a_rev1 = await _model_and_revision(db_session, model_a)
    stale_file = await seed_file(a, a_rev1, "part.stl", b"superseded-bytes")

    # New revision snapshot-copies the current revision's files forward, so
    # model_a now has TWO rows sharing this hash: the original on rev1
    # (no longer current) and a fresh copy on the new current revision.
    new_revision = await authenticated_client.post(
        f"/api/models/{model_a['id']}/revisions", json={}
    )
    assert new_revision.status_code == 201, new_revision.text
    current_file = await _file_on_revision(db_session, new_revision.json()["id"], "part.stl")

    b, b_rev = await _model_and_revision(db_session, model_b)
    other_file = await seed_file(b, b_rev, "part.stl", b"superseded-bytes")

    response = await authenticated_client.post(
        "/api/reports/duplicates/resolve",
        json={"keep": [{"blob_hash": stale_file.blob_hash, "file_id": current_file.id}]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["skipped"] == [{"file_id": stale_file.id, "reason": "not_current_revision"}]
    assert body["deleted"] == 1
    assert body["reclaimed_bytes"] == len(b"superseded-bytes")

    assert await _file_row_exists(db_session, stale_file.id)
    assert not await _file_row_exists(db_session, other_file.id)
    assert await _file_row_exists(db_session, current_file.id)


async def test_resolve_skips_pending_store_copy(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model_a = await _create_model(authenticated_client, "Pending Copy A")
    model_b = await _create_model(authenticated_client, "Pending Copy B")

    a, a_rev = await _model_and_revision(db_session, model_a)
    keep_file = await seed_file(a, a_rev, "part.stl", b"pending-store-bytes")
    b, b_rev = await _model_and_revision(db_session, model_b)
    pending_file = await _seed_pending_copy(
        db_session, b, b_rev, "part.stl", b"pending-store-bytes"
    )

    response = await authenticated_client.post(
        "/api/reports/duplicates/resolve",
        json={"keep": [{"blob_hash": keep_file.blob_hash, "file_id": keep_file.id}]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["skipped"] == [{"file_id": pending_file.id, "reason": "store_pending"}]
    assert body["deleted"] == 0
    assert body["reclaimed_bytes"] == 0

    assert await _file_row_exists(db_session, pending_file.id)


async def test_resolve_empty_keep_returns_zeroes(
    authenticated_client: httpx.AsyncClient,
) -> None:
    response = await authenticated_client.post("/api/reports/duplicates/resolve", json={"keep": []})

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 0, "reclaimed_bytes": 0, "skipped": []}


async def test_resolve_bumps_updated_at_on_affected_models(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    """``_hard_delete_file_row``'s ``model.updated_at`` bump (shared with
    ``delete_file``) fires for a resolve-triggered delete too -- checked the
    same way ``test_upload_to_old_model_bumps_it_above_an_untouched_newer_model``
    checks it, via the gallery's default sort, to sidestep raw timestamp
    resolution.
    """
    model_keeper = await _create_model(authenticated_client, "Bump Keeper")
    model_dropped = await _create_model(authenticated_client, "Bump Dropped")
    await _create_model(authenticated_client, "Bump Untouched Newer")

    keeper, keeper_rev = await _model_and_revision(db_session, model_keeper)
    keep_file = await seed_file(keeper, keeper_rev, "part.stl", b"bump-bytes")
    dropped, dropped_rev = await _model_and_revision(db_session, model_dropped)
    await seed_file(dropped, dropped_rev, "part.stl", b"bump-bytes")

    response = await authenticated_client.post(
        "/api/reports/duplicates/resolve",
        json={"keep": [{"blob_hash": keep_file.blob_hash, "file_id": keep_file.id}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == 1

    gallery = await authenticated_client.get("/api/models")
    names = [item["name"] for item in gallery.json()["items"]]
    assert names[0] == "Bump Dropped"


async def test_report_exposes_is_current_revision(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model_a = await _create_model(authenticated_client, "Revision Flag A")
    model_b = await _create_model(authenticated_client, "Revision Flag B")

    a, a_rev1 = await _model_and_revision(db_session, model_a)
    stale_file = await seed_file(a, a_rev1, "part.stl", b"revision-flag-bytes")

    new_revision = await authenticated_client.post(
        f"/api/models/{model_a['id']}/revisions", json={}
    )
    assert new_revision.status_code == 201, new_revision.text
    current_file = await _file_on_revision(db_session, new_revision.json()["id"], "part.stl")

    b, b_rev = await _model_and_revision(db_session, model_b)
    other_file = await seed_file(b, b_rev, "part.stl", b"revision-flag-bytes")

    response = await authenticated_client.get("/api/reports/duplicates")
    assert response.status_code == 200
    body = response.json()

    assert len(body["groups"]) == 1
    entries = {f["file_id"]: f for f in body["groups"][0]["files"]}
    assert entries[stale_file.id]["is_current_revision"] is False
    assert entries[current_file.id]["is_current_revision"] is True
    assert entries[other_file.id]["is_current_revision"] is True


async def test_resolve_skips_group_when_keeper_vanishes_mid_request(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    backend: LocalStorageBackend,
    seed_file: Callable[..., Awaitable[File]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent ``DELETE /files/{id}`` can remove the KEEPER between
    resolve's report snapshot and the delete loop. The per-group keeper
    re-check must then skip the whole group instead of deleting its
    remaining copies -- which would silently destroy the blob's last content
    despite the user's stated intent to keep it. Deletable copies are
    skipped as ``keeper_missing``; old-revision ones keep their own
    ``not_current_revision`` (never deletable, never promised).
    """
    model_a = await _create_model(authenticated_client, "Vanishing Keeper A")
    model_b = await _create_model(authenticated_client, "Vanishing Keeper B")
    model_c = await _create_model(authenticated_client, "Vanishing Keeper C")

    a, a_rev = await _model_and_revision(db_session, model_a)
    keeper = await seed_file(a, a_rev, "keep.stl", b"vanishing-keeper-bytes")
    b, b_rev = await _model_and_revision(db_session, model_b)
    extra = await seed_file(b, b_rev, "extra.stl", b"vanishing-keeper-bytes")

    # A third copy on a SUPERSEDED revision: it was never deletable, so a
    # vanished keeper must not relabel it `keeper_missing` -- the UI counts
    # (and warns about) those two reasons differently.
    c, c_rev1 = await _model_and_revision(db_session, model_c)
    stale = await seed_file(c, c_rev1, "extra.stl", b"vanishing-keeper-bytes")
    superseded = await authenticated_client.post(f"/api/models/{model_c['id']}/revisions", json={})
    assert superseded.status_code == 201, superseded.text
    # The new revision carries the file forward, so model C also contributes
    # a CURRENT copy -- which does get the keeper_missing treatment.
    carried = await _file_on_revision(db_session, superseded.json()["id"], "extra.stl")

    from app.services import reports as reports_service

    real_report = reports_service.duplicate_files_report

    async def snapshot_then_lose_keeper(db: AsyncSession):
        """Return the genuine report, then yank the keeper row -- simulating
        the concurrent delete landing right after the snapshot."""
        report = await real_report(db)
        await db.execute(sa_delete(File).where(File.id == keeper.id))
        await db.commit()
        return report

    monkeypatch.setattr(reports_service, "duplicate_files_report", snapshot_then_lose_keeper)

    response = await authenticated_client.post(
        "/api/reports/duplicates/resolve",
        json={"keep": [{"blob_hash": keeper.blob_hash, "file_id": keeper.id}]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == 0
    assert body["reclaimed_bytes"] == 0
    assert {(entry["file_id"], entry["reason"]) for entry in body["skipped"]} == {
        (extra.id, "keeper_missing"),
        (carried.id, "keeper_missing"),
        # NOT keeper_missing: it was already undeletable on its own.
        (stale.id, "not_current_revision"),
    }

    # Every surviving copy -- the blob's last content -- was left alone.
    for survivor in (extra, carried, stale):
        assert await _file_row_exists(db_session, survivor.id)
    assert backend.exists(extra.storage_path)


async def test_resolve_duplicate_group_choices_422_nothing_deleted(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    """Two keep choices naming the SAME group must be rejected at the schema
    layer (422) -- processed sequentially they would contradict each other,
    with the first pass deleting the second pass's keeper.
    """
    model_a = await _create_model(authenticated_client, "Twice Named A")
    model_b = await _create_model(authenticated_client, "Twice Named B")

    a, a_rev = await _model_and_revision(db_session, model_a)
    file_a = await seed_file(a, a_rev, "part.stl", b"twice-named-bytes")
    b, b_rev = await _model_and_revision(db_session, model_b)
    file_b = await seed_file(b, b_rev, "part.stl", b"twice-named-bytes")

    response = await authenticated_client.post(
        "/api/reports/duplicates/resolve",
        json={
            "keep": [
                {"blob_hash": file_a.blob_hash, "file_id": file_a.id},
                {"blob_hash": file_b.blob_hash, "file_id": file_b.id},
            ]
        },
    )

    assert response.status_code == 422
    assert await _file_row_exists(db_session, file_a.id)
    assert await _file_row_exists(db_session, file_b.id)


async def test_resolve_member_cannot_delete_others_models_copies(
    authenticated_client: httpx.AsyncClient,
    member_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    model_a = await _create_model(authenticated_client, "Owned A")
    model_b = await _create_model(authenticated_client, "Owned B")
    a, a_rev = await _model_and_revision(db_session, model_a)
    file_a = await seed_file(a, a_rev, "part.stl", b"owned-bytes")
    b, b_rev = await _model_and_revision(db_session, model_b)
    file_b = await seed_file(b, b_rev, "part.stl", b"owned-bytes")
    body = {"keep": [{"blob_hash": file_a.blob_hash, "file_id": file_a.id}]}

    r = await member_client.post("/api/reports/duplicates/resolve", json=body)
    assert r.status_code == 403, r.text
    assert await _file_row_exists(db_session, file_b.id)

    r = await authenticated_client.post("/api/reports/duplicates/resolve", json=body)
    assert r.status_code == 200 and r.json()["deleted"] == 1
    assert not await _file_row_exists(db_session, file_b.id)


async def test_get_file_format(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    payload = await _create_model(authenticated_client, "Format lookup")
    model, revision = await _model_and_revision(db_session, payload)
    file = await seed_file(model, revision, "part.stl", b"format-bytes")

    response = await authenticated_client.get(f"/api/files/{file.id}")
    assert response.status_code == 200
    assert response.json() == {"id": file.id, "rel_path": "part.stl", "format": "stl"}

    assert (await authenticated_client.get("/api/files/999999")).status_code == 404
