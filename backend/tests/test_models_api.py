"""Model CRUD + gallery listing (Task 5 brief; feat/import-fidelity T3):
slug generation/collision, sidecar content, PATCH archive/is_archived
semantics, DELETE's real hard-delete, POST .../redownload's API surface, and
the gallery's search/filter/sort/cursor-pagination behavior.
"""

import json
import uuid
from collections.abc import Awaitable, Callable

import blake3
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AssemblyThumb, Blob, BlobMeta, Derivative, File, Import, Job, Model, Revision
from app.models.enums import (
    BlobFormat,
    BlobKind,
    DerivativeKind,
    DerivativeStatus,
    ImportSite,
    ImportState,
)
from app.services import import_dedup
from app.services import jobs as jobs_service
from app.services import storage_backends as sb
from app.storage.config import LocalConfig
from app.storage.local import LocalStorageBackend

pytestmark = pytest.mark.usefixtures("library_root")


async def _create_model(
    client: httpx.AsyncClient, name: str, description: str | None = None
) -> dict:
    payload = {"name": name}
    if description is not None:
        payload["description"] = description
    response = await client.post("/api/models", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# creation: slug, revision 1, storage side effects
# ---------------------------------------------------------------------------


async def test_create_model_sets_up_slug_revision_and_storage(
    authenticated_client: httpx.AsyncClient, backend: LocalStorageBackend
) -> None:
    body = await _create_model(authenticated_client, "Cube Holder", "Holds cubes")

    assert body["slug"] == "cube-holder"
    assert body["name"] == "Cube Holder"
    assert body["description"] == "Holds cubes"
    assert body["is_archived"] is False
    assert body["tags"] == []
    assert body["notes"] == []

    revision = body["current_revision"]
    assert revision["number"] == 1
    assert revision["name"] == "initial"
    assert revision["dir_name"] == "rev-001_initial"
    assert revision["files"] == []

    assert backend.exists("cube-holder/.3dmm.json")
    # mkdirs actually created the revision directory on disk.
    assert (backend.root / "cube-holder" / "rev-001_initial").is_dir()


async def test_create_model_writes_sidecar_with_expected_content(
    authenticated_client: httpx.AsyncClient, backend: LocalStorageBackend
) -> None:
    body = await _create_model(authenticated_client, "Sidecar Test")

    sidecar = json.loads(b"".join(backend.read("sidecar-test/.3dmm.json")))

    assert sidecar == {"model_id": body["id"], "slug": "sidecar-test", "name": "Sidecar Test"}


async def test_create_model_slug_collision_gets_numeric_suffix(
    authenticated_client: httpx.AsyncClient,
) -> None:
    first = await _create_model(authenticated_client, "Cube Holder")
    second = await _create_model(authenticated_client, "Cube Holder")
    third = await _create_model(authenticated_client, "Cube Holder")

    assert first["slug"] == "cube-holder"
    assert second["slug"] == "cube-holder-2"
    assert third["slug"] == "cube-holder-3"


@pytest.mark.parametrize("bad_name", ["", "   "])
async def test_create_model_empty_name_is_422(
    authenticated_client: httpx.AsyncClient, bad_name: str
) -> None:
    response = await authenticated_client.post("/api/models", json={"name": bad_name})

    assert response.status_code == 422


async def test_models_require_authentication(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/models")

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# get / patch / archive
# ---------------------------------------------------------------------------


async def test_get_model_by_slug_returns_404_for_unknown_slug(
    authenticated_client: httpx.AsyncClient,
) -> None:
    response = await authenticated_client.get("/api/models/does-not-exist")

    assert response.status_code == 404


async def test_patch_model_updates_description_without_touching_slug(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Patchable Thing", "before")

    response = await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"description": "after"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["description"] == "after"
    assert body["slug"] == created["slug"]
    assert body["name"] == "Patchable Thing"


async def test_patch_model_name_does_not_change_slug_or_dirs(
    authenticated_client: httpx.AsyncClient, backend: LocalStorageBackend
) -> None:
    created = await _create_model(authenticated_client, "Original Name")

    response = await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"name": "Renamed Thing"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed Thing"
    assert body["slug"] == "original-name"
    assert backend.exists("original-name/.3dmm.json")


async def test_patch_model_name_rewrites_sidecar_content(
    authenticated_client: httpx.AsyncClient, backend: LocalStorageBackend
) -> None:
    """M3 carried backlog item: the sidecar used to go stale after a rename
    (only the model's own row changed); ``patch_model`` now rewrites it.
    """
    created = await _create_model(authenticated_client, "Stale Sidecar Name")

    response = await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"name": "Fresh Sidecar Name"}
    )

    assert response.status_code == 200
    sidecar_bytes = b"".join(backend.read("stale-sidecar-name/.3dmm.json"))
    sidecar = json.loads(sidecar_bytes)
    assert sidecar == {
        "model_id": created["id"],
        "slug": "stale-sidecar-name",
        "name": "Fresh Sidecar Name",
    }


async def test_patch_model_is_archived_round_trip_and_gallery_filter(
    authenticated_client: httpx.AsyncClient,
) -> None:
    """feat/import-fidelity T3: soft-delete moved from ``DELETE`` (now a
    real hard delete, see below) to ``PATCH {"is_archived": ...}`` -- fully
    reversible, unlike the old DELETE-based archive.
    """
    created = await _create_model(authenticated_client, "Archive Me")

    archive = await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"is_archived": True}
    )
    assert archive.status_code == 200, archive.text
    assert archive.json()["is_archived"] is True

    default_gallery = await authenticated_client.get("/api/models")
    slugs = [item["slug"] for item in default_gallery.json()["items"]]
    assert created["slug"] not in slugs

    with_archived = await authenticated_client.get("/api/models?archived=true")
    slugs_with_archived = [item["slug"] for item in with_archived.json()["items"]]
    assert created["slug"] in slugs_with_archived

    unarchive = await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"is_archived": False}
    )
    assert unarchive.status_code == 200, unarchive.text
    assert unarchive.json()["is_archived"] is False

    default_gallery_again = await authenticated_client.get("/api/models")
    slugs_again = [item["slug"] for item in default_gallery_again.json()["items"]]
    assert created["slug"] in slugs_again


# ---------------------------------------------------------------------------
# hard delete (feat/import-fidelity T3): DELETE /models/{slug} now REALLY
# deletes -- physical bytes (every backend, including replicas), the
# sidecar, and the row itself, cascading DB-side. Uses `_upload` (defined
# below, in the "detail: backends summary" section) to plant REAL files
# through the normal store_to_backend pipeline, so `file_locations` rows
# exist exactly as they would for any genuinely-imported/uploaded model.
# ---------------------------------------------------------------------------


async def test_delete_model_hard_deletes_files_sidecar_and_row(
    authenticated_client: httpx.AsyncClient, backend: LocalStorageBackend
) -> None:
    created = await _create_model(authenticated_client, "Delete Me")
    revision_id = created["current_revision"]["id"]
    dir_name = created["current_revision"]["dir_name"]
    upload = await authenticated_client.put(
        "/api/uploads",
        params={"model_id": created["id"], "revision_id": revision_id, "rel_path": "part.stl"},
        content=b"hello-world",
    )
    assert upload.status_code == 201, upload.text
    storage_path = f"{created['slug']}/{dir_name}/part.stl"
    sidecar_path = f"{created['slug']}/.3dmm.json"
    assert backend.exists(storage_path)
    assert backend.exists(sidecar_path)

    response = await authenticated_client.delete(f"/api/models/{created['slug']}")
    assert response.status_code == 204

    assert not backend.exists(storage_path)
    assert not backend.exists(sidecar_path)
    detail = await authenticated_client.get(f"/api/models/{created['slug']}")
    assert detail.status_code == 404


async def test_delete_model_deletes_every_replica_location(
    authenticated_client: httpx.AsyncClient,
    backend: LocalStorageBackend,
    db_session: AsyncSession,
    tmp_path,
) -> None:
    settings = get_settings()
    target = await sb.create_backend(
        db_session, settings, "Replica Target", LocalConfig(root=str(tmp_path / "target"))
    )
    created = await _create_model(authenticated_client, "Delete Me Replicated")
    revision_id = created["current_revision"]["id"]
    dir_name = created["current_revision"]["dir_name"]
    upload = await authenticated_client.put(
        "/api/uploads",
        params={"model_id": created["id"], "revision_id": revision_id, "rel_path": "part.stl"},
        content=b"hello-world",
    )
    assert upload.status_code == 201, upload.text

    relocate = await authenticated_client.post(
        f"/api/models/{created['slug']}/relocate",
        json={"target_backend_id": target.id, "mode": "replicate"},
    )
    assert relocate.status_code == 200, relocate.text

    target_backend = LocalStorageBackend(tmp_path / "target")
    storage_path = f"{created['slug']}/{dir_name}/part.stl"
    assert backend.exists(storage_path)
    assert target_backend.exists(storage_path)

    response = await authenticated_client.delete(f"/api/models/{created['slug']}")
    assert response.status_code == 204

    assert not backend.exists(storage_path)
    assert not target_backend.exists(storage_path)


async def test_delete_model_409_with_pending_store_job(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """A ``File`` row seeded directly (not through ``PUT /api/uploads``, so
    NO real ``store_to_backend``/pipeline chain ever runs for it) with
    ``verified_at=None`` and a single ``queued`` job -- the exact "still
    processing" state ``_file_store_pending`` guards against. Seeding
    directly (rather than racing a real upload's own eager-Celery job
    against a manually-inserted one) keeps this deterministic: with only
    ONE job ever created for the file, ``ORDER BY created_at DESC LIMIT 1``
    can't pick anything else.
    """
    created = await _create_model(authenticated_client, "Delete Pending")
    revision = await db_session.get(Revision, created["current_revision"]["id"])
    model = await db_session.get(Model, created["id"])

    digest = blake3.blake3(b"hello-world").hexdigest()
    blob = Blob(hash=digest, size=11, kind=BlobKind.MESH, format=BlobFormat.STL)
    db_session.add(blob)
    await db_session.flush()
    file = File(
        revision_id=revision.id,
        blob_hash=digest,
        rel_path="part.stl",
        storage_path=f"{model.slug}/{revision.dir_name}/part.stl",
        verified_at=None,
    )
    db_session.add(file)
    await db_session.flush()
    db_session.add(
        Job(
            id=uuid.uuid4(),
            type="store_to_backend",
            subject_type="file",
            subject_id=file.id,
            state=jobs_service.STATE_QUEUED,
        )
    )
    await db_session.commit()

    response = await authenticated_client.delete(f"/api/models/{created['slug']}")
    assert response.status_code == 409

    # nothing was deleted
    detail = await authenticated_client.get(f"/api/models/{created['slug']}")
    assert detail.status_code == 200


async def test_delete_model_unknown_slug_is_404(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.delete("/api/models/does-not-exist")

    assert response.status_code == 404


async def test_delete_model_frees_source_for_reimport(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """``imports.model_id``'s ``ON DELETE SET NULL`` frees ``(site,
    external_id)`` for a future re-import once the model it pointed at is
    hard-deleted (``app.services.import_dedup``'s module docstring).
    """
    created = await _create_model(authenticated_client, "Delete Frees Import")
    imp = Import(
        url="https://fake.test/thing/99",
        site=ImportSite.THINGIVERSE,
        external_id="99",
        state=ImportState.DONE,
        model_id=created["id"],
    )
    db_session.add(imp)
    await db_session.commit()
    await db_session.refresh(imp)

    response = await authenticated_client.delete(f"/api/models/{created['slug']}")
    assert response.status_code == 204

    live = await import_dedup.find_live_import(db_session, ImportSite.THINGIVERSE, "99")
    assert live is None
    await db_session.refresh(imp)
    assert imp.model_id is None


# ---------------------------------------------------------------------------
# redownload (feat/import-fidelity T3): POST /models/{slug}/redownload
# enqueues app.tasks.importing.redownload_model. The redownload MECHANICS
# (mode=revision/replace, image refresh, failure ordering) are covered end
# to end in tests/test_redownload.py -- this only exercises the API
# surface's own guardrails, which never need a real importer.
# ---------------------------------------------------------------------------


async def test_redownload_no_source_is_409(authenticated_client: httpx.AsyncClient) -> None:
    created = await _create_model(authenticated_client, "No Source Redownload")

    response = await authenticated_client.post(
        f"/api/models/{created['slug']}/redownload", json={"mode": "revision"}
    )

    assert response.status_code == 409


async def test_redownload_bad_mode_is_422(authenticated_client: httpx.AsyncClient) -> None:
    created = await _create_model(authenticated_client, "Bad Mode Redownload")

    response = await authenticated_client.post(
        f"/api/models/{created['slug']}/redownload", json={"mode": "duplicate"}
    )

    assert response.status_code == 422


async def test_redownload_unknown_model_is_404(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.post(
        "/api/models/does-not-exist/redownload", json={"mode": "revision"}
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# gallery: search / tag / format filters
# ---------------------------------------------------------------------------


async def test_gallery_q_filters_by_name_or_description(
    authenticated_client: httpx.AsyncClient,
) -> None:
    await _create_model(authenticated_client, "Widget", "a small gadget")
    await _create_model(authenticated_client, "Gadget", "a small widget")
    await _create_model(authenticated_client, "Unrelated Thing", "nothing to see here")

    response = await authenticated_client.get("/api/models?q=widget")

    names = {item["name"] for item in response.json()["items"]}
    assert names == {"Widget", "Gadget"}


async def test_gallery_tag_filter_matches_exact_tag_name(
    authenticated_client: httpx.AsyncClient,
) -> None:
    tagged = await _create_model(authenticated_client, "Tagged Model")
    await _create_model(authenticated_client, "Untagged Model")

    tag_response = await authenticated_client.post(
        f"/api/models/{tagged['id']}/tags", json={"name": "keychain"}
    )
    assert tag_response.status_code == 201

    response = await authenticated_client.get("/api/models?tag=keychain")

    slugs = [item["slug"] for item in response.json()["items"]]
    assert slugs == [tagged["slug"]]


async def test_gallery_format_filter_matches_current_revision_files(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    with_stl = await _create_model(authenticated_client, "Has STL")
    await _create_model(authenticated_client, "Has Nothing")

    model = await db_session.get(Model, with_stl["id"])
    revision = await db_session.get(Revision, model.current_revision_id)
    await seed_file(model, revision, "part.stl", b"solid stl-data", blob_format=BlobFormat.STL)

    stl_response = await authenticated_client.get("/api/models?format=stl")
    obj_response = await authenticated_client.get("/api/models?format=obj")

    assert [item["slug"] for item in stl_response.json()["items"]] == [with_stl["slug"]]
    assert obj_response.json()["items"] == []

    detail = stl_response.json()["items"][0]
    assert detail["file_count"] == 1
    assert detail["formats"] == ["stl"]


# ---------------------------------------------------------------------------
# gallery: sort + cursor pagination
# ---------------------------------------------------------------------------


async def test_gallery_sort_by_name_ascending(authenticated_client: httpx.AsyncClient) -> None:
    for name in ["Charlie", "Alpha", "Bravo"]:
        await _create_model(authenticated_client, name)

    response = await authenticated_client.get("/api/models?sort=name&limit=10")

    names = [item["name"] for item in response.json()["items"]]
    assert names == ["Alpha", "Bravo", "Charlie"]


async def test_gallery_default_sort_is_updated_at_descending(
    authenticated_client: httpx.AsyncClient,
) -> None:
    first = await _create_model(authenticated_client, "First Created")
    await _create_model(authenticated_client, "Second Created")

    # Bump `first`'s updated_at so it should now sort ahead under the
    # default `-updated_at` sort despite being created earlier.
    patch_response = await authenticated_client.patch(
        f"/api/models/{first['slug']}", json={"description": "bumped"}
    )
    assert patch_response.status_code == 200

    response = await authenticated_client.get("/api/models")

    names = [item["name"] for item in response.json()["items"]]
    assert names[0] == "First Created"


async def test_gallery_cursor_pagination_round_trip_covers_all_items_without_overlap(
    authenticated_client: httpx.AsyncClient,
) -> None:
    expected_names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
    for name in expected_names:
        await _create_model(authenticated_client, name)

    seen: list[str] = []
    cursor = None
    for _ in range(len(expected_names) + 1):  # +1 safety margin against infinite loop
        url = "/api/models?sort=name&limit=2"
        if cursor:
            url += f"&cursor={cursor}"
        response = await authenticated_client.get(url)
        assert response.status_code == 200
        page = response.json()
        seen.extend(item["name"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == expected_names
    assert len(seen) == len(set(seen))


async def test_gallery_invalid_cursor_is_400(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.get("/api/models?cursor=not-valid-base64!!!")

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# patch: cover_blob_hash validation
# ---------------------------------------------------------------------------


async def test_patch_model_with_unknown_cover_blob_hash_is_422(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Test Model")

    # 64-character hex string (valid blake3 hash length) but non-existent blob
    bogus_hash = "a" * 64

    response = await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"cover_blob_hash": bogus_hash}
    )

    assert response.status_code == 422
    detail = response.json()
    assert detail["detail"] == "unknown cover_blob_hash"


async def test_patch_model_with_valid_cover_blob_hash_persists(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    created = await _create_model(authenticated_client, "Coverable Model")

    # Get model and revision from DB
    model = await db_session.get(Model, created["id"])
    revision = await db_session.get(Revision, model.current_revision_id)

    # Seed a file (creates blob + file)
    file = await seed_file(model, revision, "cover.stl", b"solid cover-data")

    # Now patch with this blob hash as cover
    response = await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"cover_blob_hash": file.blob_hash}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["cover_blob_hash"] == file.blob_hash

    # Verify it persisted in DB
    await db_session.refresh(model)
    assert model.cover_blob_hash == file.blob_hash


async def test_patch_model_clearing_cover_blob_hash_with_null(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    created = await _create_model(authenticated_client, "Cover Clearable Model")

    # Get model and revision from DB
    model = await db_session.get(Model, created["id"])
    revision = await db_session.get(Revision, model.current_revision_id)

    # Seed a file and set it as cover
    file = await seed_file(model, revision, "cover.stl", b"solid cover-data")
    model.cover_blob_hash = file.blob_hash
    await db_session.commit()

    # Clear the cover by setting to null
    response = await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"cover_blob_hash": None}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["cover_blob_hash"] is None

    # Verify it persisted in DB
    await db_session.refresh(model)
    assert model.cover_blob_hash is None


# ---------------------------------------------------------------------------
# gallery: cover priority chain (Task 7)
# ---------------------------------------------------------------------------


async def _gallery_item(client: httpx.AsyncClient, slug: str) -> dict:
    response = await client.get("/api/models")
    return next(item for item in response.json()["items"] if item["slug"] == slug)


async def test_gallery_cover_prefers_cover_blob_hash_when_its_thumb_is_ok(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    created = await _create_model(authenticated_client, "Cover From Hash")
    model = await db_session.get(Model, created["id"])
    revision = await db_session.get(Revision, model.current_revision_id)
    file = await seed_file(model, revision, "part.stl", b"solid cover-bytes")
    db_session.add(
        Derivative(
            blob_hash=file.blob_hash, kind=DerivativeKind.THUMB_256, status=DerivativeStatus.OK
        )
    )
    model.cover_blob_hash = file.blob_hash
    await db_session.commit()

    item = await _gallery_item(authenticated_client, created["slug"])

    assert item["cover"] == f"/api/blobs/{file.blob_hash}/thumb?size=256"


async def test_gallery_cover_falls_back_to_assembly_thumb_when_no_cover_blob_hash(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    created = await _create_model(authenticated_client, "Cover From Assembly")
    model = await db_session.get(Model, created["id"])
    revision = await db_session.get(Revision, model.current_revision_id)
    await seed_file(model, revision, "part.stl", b"solid no-thumb-bytes")
    db_session.add(AssemblyThumb(revision_id=revision.id, status=DerivativeStatus.OK))
    await db_session.commit()

    item = await _gallery_item(authenticated_client, created["slug"])

    assert item["cover"] == f"/api/revisions/{revision.id}/assembly-thumb"


async def test_gallery_cover_falls_back_to_first_ok_thumb_file_by_rel_path(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    created = await _create_model(authenticated_client, "Cover From First File")
    model = await db_session.get(Model, created["id"])
    revision = await db_session.get(Revision, model.current_revision_id)
    # "a.stl" sorts first by rel_path but has no ready thumb -- the fallback
    # must skip it and use "b.stl", proving it's not just "any file".
    await seed_file(model, revision, "a.stl", b"file-a-bytes")
    file_b = await seed_file(model, revision, "b.stl", b"file-b-bytes")
    db_session.add(
        Derivative(
            blob_hash=file_b.blob_hash, kind=DerivativeKind.THUMB_256, status=DerivativeStatus.OK
        )
    )
    await db_session.commit()

    item = await _gallery_item(authenticated_client, created["slug"])

    assert item["cover"] == f"/api/blobs/{file_b.blob_hash}/thumb?size=256"


async def test_gallery_cover_is_none_when_nothing_is_ready(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    created = await _create_model(authenticated_client, "Cover Not Ready")
    model = await db_session.get(Model, created["id"])
    revision = await db_session.get(Revision, model.current_revision_id)
    await seed_file(model, revision, "part.stl", b"no-derivatives-at-all")

    item = await _gallery_item(authenticated_client, created["slug"])

    assert item["cover"] is None


# ---------------------------------------------------------------------------
# gallery: render_url (feat/import-fidelity T2) -- the assembly-thumb render
# URL specifically, independent of whatever `cover` is showing.
# ---------------------------------------------------------------------------


async def test_gallery_render_url_present_when_assembly_thumb_is_ok(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    created = await _create_model(authenticated_client, "Render Ready")
    model = await db_session.get(Model, created["id"])
    revision = await db_session.get(Revision, model.current_revision_id)
    await seed_file(model, revision, "part.stl", b"solid render-bytes")
    db_session.add(AssemblyThumb(revision_id=revision.id, status=DerivativeStatus.OK))
    await db_session.commit()

    item = await _gallery_item(authenticated_client, created["slug"])

    assert item["render_url"] == f"/api/revisions/{revision.id}/assembly-thumb"


async def test_gallery_render_url_is_none_when_assembly_thumb_not_ok(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    created = await _create_model(authenticated_client, "Render Not Ready")
    model = await db_session.get(Model, created["id"])
    revision = await db_session.get(Revision, model.current_revision_id)
    await seed_file(model, revision, "part.stl", b"no-assembly-thumb-yet")

    item = await _gallery_item(authenticated_client, created["slug"])

    assert item["render_url"] is None


async def test_gallery_render_url_independent_of_cover_blob_hash_chain(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    """`cover_blob_hash` outranks the assembly thumb in the `cover` chain --
    but `render_url` must still surface the assembly-thumb URL on its own,
    proving the two fields are genuinely independent (T2 brief: "expose it
    as its own field WITHOUT changing the existing cover chain semantics")."""
    created = await _create_model(authenticated_client, "Cover And Render Both Ready")
    model = await db_session.get(Model, created["id"])
    revision = await db_session.get(Revision, model.current_revision_id)
    file = await seed_file(model, revision, "cover.png", b"solid cover-bytes")
    db_session.add(
        Derivative(
            blob_hash=file.blob_hash, kind=DerivativeKind.THUMB_256, status=DerivativeStatus.OK
        )
    )
    db_session.add(AssemblyThumb(revision_id=revision.id, status=DerivativeStatus.OK))
    model.cover_blob_hash = file.blob_hash
    await db_session.commit()

    item = await _gallery_item(authenticated_client, created["slug"])

    assert item["cover"] == f"/api/blobs/{file.blob_hash}/thumb?size=256"
    assert item["render_url"] == f"/api/revisions/{revision.id}/assembly-thumb"


# ---------------------------------------------------------------------------
# gallery: has_sliced filter + print_time_s aggregation (Task 7)
# ---------------------------------------------------------------------------


async def test_gallery_has_sliced_filter(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    sliced = await _create_model(authenticated_client, "Sliced Model")
    plain = await _create_model(authenticated_client, "Plain Model")

    sliced_model = await db_session.get(Model, sliced["id"])
    sliced_revision = await db_session.get(Revision, sliced_model.current_revision_id)
    sliced_file = await seed_file(
        sliced_model,
        sliced_revision,
        "print.gcode.3mf",
        b"sliced-bytes",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    db_session.add(BlobMeta(blob_hash=sliced_file.blob_hash, print_time_s=3600))

    plain_model = await db_session.get(Model, plain["id"])
    plain_revision = await db_session.get(Revision, plain_model.current_revision_id)
    await seed_file(plain_model, plain_revision, "part.stl", b"plain-bytes")

    await db_session.commit()

    sliced_only = await authenticated_client.get("/api/models?has_sliced=true")
    plain_only = await authenticated_client.get("/api/models?has_sliced=false")

    assert [i["slug"] for i in sliced_only.json()["items"]] == [sliced["slug"]]
    assert {i["slug"] for i in plain_only.json()["items"]} == {plain["slug"]}


async def test_gallery_print_time_s_is_min_over_sliced_files_in_current_revision(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    created = await _create_model(authenticated_client, "Multi Plate Model")
    model = await db_session.get(Model, created["id"])
    revision = await db_session.get(Revision, model.current_revision_id)

    file_a = await seed_file(
        model,
        revision,
        "a.gcode.3mf",
        b"a-bytes",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    file_b = await seed_file(
        model,
        revision,
        "b.gcode.3mf",
        b"b-bytes",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    db_session.add(BlobMeta(blob_hash=file_a.blob_hash, print_time_s=7200))
    db_session.add(BlobMeta(blob_hash=file_b.blob_hash, print_time_s=3600))
    await db_session.commit()

    item = await _gallery_item(authenticated_client, created["slug"])

    assert item["print_time_s"] == 3600
    assert item["has_sliced"] is True


# ---------------------------------------------------------------------------
# gallery: q ILIKE-escaping backlog fold (Task 7)
# ---------------------------------------------------------------------------


async def test_gallery_q_escapes_percent_so_it_matches_literally(
    authenticated_client: httpx.AsyncClient,
) -> None:
    await _create_model(authenticated_client, "Sale 100% Off")
    await _create_model(authenticated_client, "Best 100 Widgets")

    response = await authenticated_client.get("/api/models", params={"q": "100%"})

    names = {item["name"] for item in response.json()["items"]}
    assert names == {"Sale 100% Off"}


# ---------------------------------------------------------------------------
# gallery: source_site (Task 6 -- gallery badge attribution)
# ---------------------------------------------------------------------------


async def test_gallery_item_carries_source_site_for_imported_and_manual_models(
    authenticated_client: httpx.AsyncClient, backend: LocalStorageBackend
) -> None:
    from app.services import library
    from app.tasks.base import sync_session

    with sync_session() as s:
        library.create_imported_model_sync(
            s,
            backend,
            name="Imported Vase",
            description=None,
            source_url="https://www.thingiverse.com/thing:763622",
            source_site="thingiverse",
            source_author="alice",
            source_license="CC-BY-4.0",
            imported_at=None,
            tags=[],
        )

    await _create_model(authenticated_client, "Manual Widget")

    response = await authenticated_client.get("/api/models")
    items = {item["name"]: item for item in response.json()["items"]}

    assert items["Imported Vase"]["source_site"] == "thingiverse"
    assert items["Manual Widget"]["source_site"] is None


# ---------------------------------------------------------------------------
# gallery/detail: review_state (Task 11 -- "needs review" gallery badge)
# ---------------------------------------------------------------------------


async def test_gallery_and_detail_surface_review_state_for_adopted_model(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    adopted = await _create_model(authenticated_client, "Adopted Model")
    model = await db_session.get(Model, adopted["id"])
    model.review_state = "adopted"
    await db_session.commit()

    item = await _gallery_item(authenticated_client, adopted["slug"])
    assert item["review_state"] == "adopted"

    detail = await authenticated_client.get(f"/api/models/{adopted['slug']}")
    assert detail.json()["review_state"] == "adopted"


async def test_gallery_collection_filter_and_schema_fields(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Branch 3 Task 1: ``source_collection_id``/``source_collection_title``
    surface on both the gallery list item and the detail response, and
    ``GET /models?collection=<id>`` filters by them (simple equality, since
    the field is denormalized straight onto ``models``)."""
    from app.models.collections import FollowedCollection
    from app.models.enums import CollectionSyncMode, ImportSite

    collection = FollowedCollection(
        site=ImportSite.THINGIVERSE,
        list_id="likes",
        kind="likes",
        title="My Likes",
        mode=CollectionSyncMode.AUTO,
    )
    db_session.add(collection)
    await db_session.commit()
    await db_session.refresh(collection)

    from_collection = await _create_model(authenticated_client, "From Collection")
    other = await _create_model(authenticated_client, "Not From Collection")

    model = await db_session.get(Model, from_collection["id"])
    model.source_collection_id = collection.id
    model.source_collection_title = collection.title
    await db_session.commit()

    filtered = await authenticated_client.get(f"/api/models?collection={collection.id}")
    assert [i["slug"] for i in filtered.json()["items"]] == [from_collection["slug"]]
    item = filtered.json()["items"][0]
    assert item["source_collection_id"] == collection.id
    assert item["source_collection_title"] == "My Likes"

    unfiltered = await authenticated_client.get("/api/models")
    slugs = {i["slug"] for i in unfiltered.json()["items"]}
    assert slugs == {from_collection["slug"], other["slug"]}

    detail = await authenticated_client.get(f"/api/models/{from_collection['slug']}")
    assert detail.json()["source_collection_id"] == collection.id
    assert detail.json()["source_collection_title"] == "My Likes"

    detail_other = await authenticated_client.get(f"/api/models/{other['slug']}")
    assert detail_other.json()["source_collection_id"] is None
    assert detail_other.json()["source_collection_title"] is None


async def test_gallery_and_detail_review_state_is_null_for_normal_model(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Normal Model")

    item = await _gallery_item(authenticated_client, created["slug"])
    assert item["review_state"] is None

    detail = await authenticated_client.get(f"/api/models/{created['slug']}")
    assert detail.json()["review_state"] is None


# ---------------------------------------------------------------------------
# favorites (Branch 4 Task 1): ModelSummary/ModelDetail.favorite, PATCH
# toggle, and the gallery's `favorite=` filter facet.
# ---------------------------------------------------------------------------


async def test_patch_model_favorite_toggle(authenticated_client: httpx.AsyncClient) -> None:
    created = await _create_model(authenticated_client, "Favorite Me")
    assert created["favorite"] is False

    starred = await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"favorite": True}
    )
    assert starred.status_code == 200
    assert starred.json()["favorite"] is True

    unstarred = await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"favorite": False}
    )
    assert unstarred.status_code == 200
    assert unstarred.json()["favorite"] is False


async def test_gallery_favorite_filter_and_schema_field(
    authenticated_client: httpx.AsyncClient,
) -> None:
    fav = await _create_model(authenticated_client, "Favorite Model")
    plain = await _create_model(authenticated_client, "Plain Model")

    patch = await authenticated_client.patch(f"/api/models/{fav['slug']}", json={"favorite": True})
    assert patch.status_code == 200

    fav_item = await _gallery_item(authenticated_client, fav["slug"])
    plain_item = await _gallery_item(authenticated_client, plain["slug"])
    assert fav_item["favorite"] is True
    assert plain_item["favorite"] is False

    only_favorites = await authenticated_client.get("/api/models?favorite=true")
    assert [i["slug"] for i in only_favorites.json()["items"]] == [fav["slug"]]

    # `favorite=false` must NOT hide favorites -- it applies no filter at all.
    unfiltered_by_false = await authenticated_client.get("/api/models?favorite=false")
    slugs = {i["slug"] for i in unfiltered_by_false.json()["items"]}
    assert slugs == {fav["slug"], plain["slug"]}


# ---------------------------------------------------------------------------
# bulk ops (Branch 4 Task 1): POST /models/bulk
# ---------------------------------------------------------------------------


async def test_bulk_add_remove_tags_and_favorite_across_models(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model_a = await _create_model(authenticated_client, "Bulk A")
    model_b = await _create_model(authenticated_client, "Bulk B")
    for model in (model_a, model_b):
        tagged = await authenticated_client.post(
            f"/api/models/{model['id']}/tags", json={"name": "old"}
        )
        assert tagged.status_code == 201

    response = await authenticated_client.post(
        "/api/models/bulk",
        json={
            "ids": [model_a["id"], model_b["id"]],
            "add_tags": ["new"],
            "remove_tags": ["old"],
            "favorite": True,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"updated": 2}

    for model in (model_a, model_b):
        detail = await authenticated_client.get(f"/api/models/{model['slug']}")
        body = detail.json()
        assert body["tags"] == ["new"]
        assert body["favorite"] is True


async def test_bulk_unknown_id_is_404_with_nothing_applied(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model = await _create_model(authenticated_client, "Bulk Untouched")

    response = await authenticated_client.post(
        "/api/models/bulk",
        json={"ids": [model["id"], 999999], "add_tags": ["should-not-land"], "favorite": True},
    )

    assert response.status_code == 404

    detail = await authenticated_client.get(f"/api/models/{model['slug']}")
    body = detail.json()
    assert body["tags"] == []
    assert body["favorite"] is False


async def test_bulk_remove_tag_partial_membership_is_not_404_and_is_atomic(
    authenticated_client: httpx.AsyncClient,
) -> None:
    """Fix-review F1: the UI's remove-tag popover offers the UNION of tags
    across the selection, so "only one of the two selected models actually
    has this tag" is the NORMAL case -- must be a 200 no-op for the model
    that never had it, not a 404 that leaves the batch half-committed.
    """
    model_a = await _create_model(authenticated_client, "Partial Tag A")
    model_b = await _create_model(authenticated_client, "Partial Tag B")
    tagged = await authenticated_client.post(
        f"/api/models/{model_a['id']}/tags", json={"name": "shared"}
    )
    assert tagged.status_code == 201

    detail_b_before = await authenticated_client.get(f"/api/models/{model_b['slug']}")
    updated_at_b_before = detail_b_before.json()["updated_at"]

    response = await authenticated_client.post(
        "/api/models/bulk",
        json={"ids": [model_a["id"], model_b["id"]], "remove_tags": ["shared"]},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"updated": 2}

    detail_a = await authenticated_client.get(f"/api/models/{model_a['slug']}")
    assert detail_a.json()["tags"] == []

    # model_b never had the tag -- untouched, including its updated_at.
    detail_b = await authenticated_client.get(f"/api/models/{model_b['slug']}")
    assert detail_b.json()["tags"] == []
    assert detail_b.json()["updated_at"] == updated_at_b_before


async def test_bulk_remove_tag_that_exists_on_no_model_in_the_batch_is_a_noop(
    authenticated_client: httpx.AsyncClient,
) -> None:
    """A tag name that doesn't exist on ANY of the selected models (or at all)
    is likewise a silent no-op, not a 404 -- same reasoning as the partial
    case above, just the all-missing edge of it.
    """
    model = await _create_model(authenticated_client, "Bulk Remove Noop")

    response = await authenticated_client.post(
        "/api/models/bulk",
        json={"ids": [model["id"]], "remove_tags": ["never-applied"]},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"updated": 1}


# ---------------------------------------------------------------------------
# bulk delete (Round 11 Task 1): POST /models/bulk-delete -- hard-delete
# every model in `ids` in one call, reusing `hard_delete_model` per model.
# ---------------------------------------------------------------------------


async def test_bulk_delete_two_models_deletes_files_sidecars_and_rows(
    authenticated_client: httpx.AsyncClient, backend: LocalStorageBackend
) -> None:
    model_a = await _create_model(authenticated_client, "Bulk Delete A")
    model_b = await _create_model(authenticated_client, "Bulk Delete B")

    paths = []
    for model in (model_a, model_b):
        revision_id = model["current_revision"]["id"]
        dir_name = model["current_revision"]["dir_name"]
        upload = await authenticated_client.put(
            "/api/uploads",
            params={
                "model_id": model["id"],
                "revision_id": revision_id,
                "rel_path": "part.stl",
                "allow_duplicate": "true",
            },
            content=b"hello-world",
        )
        assert upload.status_code == 201, upload.text
        storage_path = f"{model['slug']}/{dir_name}/part.stl"
        sidecar_path = f"{model['slug']}/.3dmm.json"
        assert backend.exists(storage_path)
        assert backend.exists(sidecar_path)
        paths.append((storage_path, sidecar_path))

    response = await authenticated_client.post(
        "/api/models/bulk-delete", json={"ids": [model_a["id"], model_b["id"]]}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 2}

    for storage_path, sidecar_path in paths:
        assert not backend.exists(storage_path)
        assert not backend.exists(sidecar_path)
    for model in (model_a, model_b):
        detail = await authenticated_client.get(f"/api/models/{model['slug']}")
        assert detail.status_code == 404


async def test_bulk_delete_unknown_id_is_404_with_nothing_deleted(
    authenticated_client: httpx.AsyncClient, backend: LocalStorageBackend
) -> None:
    model = await _create_model(authenticated_client, "Bulk Delete Untouched")
    revision_id = model["current_revision"]["id"]
    dir_name = model["current_revision"]["dir_name"]
    upload = await authenticated_client.put(
        "/api/uploads",
        params={"model_id": model["id"], "revision_id": revision_id, "rel_path": "part.stl"},
        content=b"hello-world",
    )
    assert upload.status_code == 201, upload.text
    storage_path = f"{model['slug']}/{dir_name}/part.stl"
    sidecar_path = f"{model['slug']}/.3dmm.json"

    response = await authenticated_client.post(
        "/api/models/bulk-delete", json={"ids": [model["id"], 999999]}
    )

    assert response.status_code == 404
    assert "999999" in response.text

    detail = await authenticated_client.get(f"/api/models/{model['slug']}")
    assert detail.status_code == 200
    assert backend.exists(storage_path)
    assert backend.exists(sidecar_path)


async def test_bulk_delete_409_when_any_model_has_pending_store_job_nothing_deleted(
    authenticated_client: httpx.AsyncClient, backend: LocalStorageBackend, db_session: AsyncSession
) -> None:
    """Seeds a pending ``store_to_backend`` job the same way as
    ``test_delete_model_409_with_pending_store_job`` above -- a ``File`` row
    inserted directly, with ``verified_at=None`` and a single ``queued``
    job. Paired with a fully "clean" second model (uploaded through the real
    pipeline) to prove the whole batch 409s and rolls back nothing, not just
    the pending one.
    """
    pending = await _create_model(authenticated_client, "Bulk Delete Pending")
    revision = await db_session.get(Revision, pending["current_revision"]["id"])
    pending_model = await db_session.get(Model, pending["id"])

    digest = blake3.blake3(b"hello-world").hexdigest()
    blob = Blob(hash=digest, size=11, kind=BlobKind.MESH, format=BlobFormat.STL)
    db_session.add(blob)
    await db_session.flush()
    file = File(
        revision_id=revision.id,
        blob_hash=digest,
        rel_path="part.stl",
        storage_path=f"{pending_model.slug}/{revision.dir_name}/part.stl",
        verified_at=None,
    )
    db_session.add(file)
    await db_session.flush()
    db_session.add(
        Job(
            id=uuid.uuid4(),
            type="store_to_backend",
            subject_type="file",
            subject_id=file.id,
            state=jobs_service.STATE_QUEUED,
        )
    )
    await db_session.commit()

    clean = await _create_model(authenticated_client, "Bulk Delete Clean")
    clean_revision_id = clean["current_revision"]["id"]
    clean_dir_name = clean["current_revision"]["dir_name"]
    upload = await authenticated_client.put(
        "/api/uploads",
        params={
            "model_id": clean["id"],
            "revision_id": clean_revision_id,
            "rel_path": "part.stl",
            "allow_duplicate": "true",
        },
        content=b"hello-world",
    )
    assert upload.status_code == 201, upload.text
    clean_storage_path = f"{clean['slug']}/{clean_dir_name}/part.stl"
    clean_sidecar_path = f"{clean['slug']}/.3dmm.json"
    assert backend.exists(clean_storage_path)
    assert backend.exists(clean_sidecar_path)

    # The CLEAN model is listed first on purpose: a naive implementation
    # with no batch-level pre-check would delete it before tripping over the
    # pending model's own per-model guard inside hard_delete_model -- with
    # the pending model first, that broken shape would pass this test too.
    response = await authenticated_client.post(
        "/api/models/bulk-delete", json={"ids": [clean["id"], pending["id"]]}
    )

    assert response.status_code == 409
    assert str(pending["id"]) in response.json()["detail"]

    detail_pending = await authenticated_client.get(f"/api/models/{pending['slug']}")
    assert detail_pending.status_code == 200
    detail_clean = await authenticated_client.get(f"/api/models/{clean['slug']}")
    assert detail_clean.status_code == 200
    assert backend.exists(clean_storage_path)
    assert backend.exists(clean_sidecar_path)


async def test_bulk_delete_dedupes_ids_and_counts_unique(
    authenticated_client: httpx.AsyncClient,
) -> None:
    model = await _create_model(authenticated_client, "Bulk Delete Dedupe")

    response = await authenticated_client.post(
        "/api/models/bulk-delete", json={"ids": [model["id"], model["id"]]}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 1}

    detail = await authenticated_client.get(f"/api/models/{model['slug']}")
    assert detail.status_code == 404


async def test_bulk_delete_empty_ids_returns_zero(
    authenticated_client: httpx.AsyncClient,
) -> None:
    response = await authenticated_client.post("/api/models/bulk-delete", json={"ids": []})

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 0}


# ---------------------------------------------------------------------------
# relocate (Workstream C task C3): POST /models/{slug}/relocate enqueues
# app.tasks.relocate.relocate_model_storage. The relocate MECHANICS
# (move/replicate copy+verify, hash-mismatch handling) are covered end to
# end in tests/test_relocate.py -- this only exercises the API surface: job
# dispatch and the mode/target-backend validation guardrails.
# ---------------------------------------------------------------------------


async def test_relocate_dispatches_tracked_job(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, tmp_path
) -> None:
    settings = get_settings()
    target = await sb.create_backend(
        db_session, settings, "Target", LocalConfig(root=str(tmp_path / "target"))
    )
    created = await _create_model(authenticated_client, "Relocate Me")

    response = await authenticated_client.post(
        f"/api/models/{created['slug']}/relocate",
        json={"target_backend_id": target.id, "mode": "move"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["type"] == "relocate_model_storage"
    assert body["id"]
    assert body["state"] in {"queued", "running", "done", "failed"}


async def test_relocate_bad_mode_is_422(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, tmp_path
) -> None:
    settings = get_settings()
    target = await sb.create_backend(
        db_session, settings, "Target", LocalConfig(root=str(tmp_path / "target"))
    )
    created = await _create_model(authenticated_client, "Relocate Bad Mode")

    response = await authenticated_client.post(
        f"/api/models/{created['slug']}/relocate",
        json={"target_backend_id": target.id, "mode": "duplicate"},
    )

    assert response.status_code == 422


async def test_relocate_unknown_target_backend_is_404(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Relocate Bad Target")

    response = await authenticated_client.post(
        f"/api/models/{created['slug']}/relocate",
        json={"target_backend_id": 999999, "mode": "move"},
    )

    assert response.status_code == 404


async def test_relocate_unknown_model_is_404(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, tmp_path
) -> None:
    settings = get_settings()
    target = await sb.create_backend(
        db_session, settings, "Target", LocalConfig(root=str(tmp_path / "target"))
    )

    response = await authenticated_client.post(
        "/api/models/does-not-exist/relocate",
        json={"target_backend_id": target.id, "mode": "move"},
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# detail: backends summary (Workstream C task C4) -- ModelDetail.backends is
# the DISTINCT set of storage backends holding the model's current-revision
# files, computed from `files.backend_id` (the PRIMARY location) only --
# NOT every backend a file has been replicated onto (`file_locations`; see
# `app.tasks.relocate` -- `mode="replicate"` never touches `backend_id`).
# ---------------------------------------------------------------------------


async def _upload(
    client: httpx.AsyncClient, *, model_id: int, revision_id: int, rel_path: str, content: bytes
) -> httpx.Response:
    return await client.put(
        "/api/uploads",
        params={"model_id": model_id, "revision_id": revision_id, "rel_path": rel_path},
        content=content,
    )


async def test_model_detail_reports_default_backend_after_upload(
    authenticated_client: httpx.AsyncClient, backend: LocalStorageBackend
) -> None:
    created = await _create_model(authenticated_client, "Backend Summary Model")
    revision_id = created["current_revision"]["id"]

    upload = await _upload(
        authenticated_client,
        model_id=created["id"],
        revision_id=revision_id,
        rel_path="part.stl",
        content=b"hello-world",
    )
    assert upload.status_code == 201, upload.text

    detail = await authenticated_client.get(f"/api/models/{created['slug']}")
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["backends"]) == 1
    assert body["backends"][0]["name"] == "Default"


async def test_model_detail_reports_target_backend_after_move_relocate(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, tmp_path
) -> None:
    settings = get_settings()
    target = await sb.create_backend(
        db_session, settings, "Target", LocalConfig(root=str(tmp_path / "target"))
    )
    created = await _create_model(authenticated_client, "Relocate Backend Summary")
    revision_id = created["current_revision"]["id"]
    upload = await _upload(
        authenticated_client,
        model_id=created["id"],
        revision_id=revision_id,
        rel_path="part.stl",
        content=b"hello-world",
    )
    assert upload.status_code == 201, upload.text

    relocate = await authenticated_client.post(
        f"/api/models/{created['slug']}/relocate",
        json={"target_backend_id": target.id, "mode": "move"},
    )
    assert relocate.status_code == 200, relocate.text

    detail = await authenticated_client.get(f"/api/models/{created['slug']}")
    assert detail.json()["backends"] == [{"id": target.id, "name": "Target"}]


async def test_model_detail_backends_unchanged_by_replicate_relocate(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession, tmp_path
) -> None:
    """`mode="replicate"` copies bytes onto the target backend but never
    flips `files.backend_id` -- the PRIMARY-backend-only summary must still
    report only the ORIGINAL (default) backend afterward.
    """
    settings = get_settings()
    target = await sb.create_backend(
        db_session, settings, "Replica Target", LocalConfig(root=str(tmp_path / "target"))
    )
    created = await _create_model(authenticated_client, "Replicate Backend Summary")
    revision_id = created["current_revision"]["id"]
    upload = await _upload(
        authenticated_client,
        model_id=created["id"],
        revision_id=revision_id,
        rel_path="part.stl",
        content=b"hello-world",
    )
    assert upload.status_code == 201, upload.text

    relocate = await authenticated_client.post(
        f"/api/models/{created['slug']}/relocate",
        json={"target_backend_id": target.id, "mode": "replicate"},
    )
    assert relocate.status_code == 200, relocate.text

    detail = await authenticated_client.get(f"/api/models/{created['slug']}")
    body = detail.json()
    assert len(body["backends"]) == 1
    assert body["backends"][0]["name"] == "Default"


async def test_model_detail_backends_empty_without_current_revision(
    authenticated_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    created = await _create_model(authenticated_client, "No Revision Backend Summary")
    model = await db_session.get(Model, created["id"])
    model.current_revision_id = None
    await db_session.commit()

    detail = await authenticated_client.get(f"/api/models/{created['slug']}")
    assert detail.json()["backends"] == []


# ---------------------------------------------------------------------------
# gallery: dims_mm / best_slicer_file / printable_file (Phase 6)
# ---------------------------------------------------------------------------


async def test_gallery_best_slicer_file_prefers_3mf_over_stl_and_carries_dims(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    created = await _create_model(authenticated_client, "Mixed Format Model")
    model = await db_session.get(Model, created["id"])
    revision = await db_session.get(Revision, model.current_revision_id)

    stl_file = await seed_file(model, revision, "part.stl", b"stl-bytes")
    db_session.add(BlobMeta(blob_hash=stl_file.blob_hash, dims_mm=[10.0, 10.0, 10.0]))
    threemf_file = await seed_file(
        model, revision, "part.3mf", b"3mf-bytes", blob_format=BlobFormat.THREEMF
    )
    db_session.add(BlobMeta(blob_hash=threemf_file.blob_hash, dims_mm=[20.0, 20.0, 20.0]))
    await db_session.commit()

    item = await _gallery_item(authenticated_client, created["slug"])

    assert item["best_slicer_file"]["id"] == threemf_file.id
    assert item["best_slicer_file"]["format"] == "3mf"
    assert item["dims_mm"] == [20.0, 20.0, 20.0]


async def test_gallery_best_slicer_file_skips_unverified_files(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    created = await _create_model(authenticated_client, "Unverified 3mf Model")
    model = await db_session.get(Model, created["id"])
    revision = await db_session.get(Revision, model.current_revision_id)

    stl_file = await seed_file(model, revision, "part.stl", b"stl-bytes")
    db_session.add(BlobMeta(blob_hash=stl_file.blob_hash, dims_mm=[5.0, 5.0, 5.0]))
    threemf_file = await seed_file(
        model, revision, "part.3mf", b"3mf-bytes", blob_format=BlobFormat.THREEMF
    )
    db_session.add(BlobMeta(blob_hash=threemf_file.blob_hash, dims_mm=[20.0, 20.0, 20.0]))
    await db_session.commit()

    threemf_row = await db_session.get(File, threemf_file.id)
    threemf_row.verified_at = None
    await db_session.commit()

    item = await _gallery_item(authenticated_client, created["slug"])

    # The unverified 3mf is skipped -- the verified stl wins by default, and
    # `dims_mm` falls back to its own (smaller) mesh dims, not the 3mf's.
    assert item["best_slicer_file"]["id"] == stl_file.id
    assert item["dims_mm"] == [5.0, 5.0, 5.0]


async def test_gallery_printable_file_is_newest_sliced_gcode_3mf(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    created = await _create_model(authenticated_client, "Printable Model")
    model = await db_session.get(Model, created["id"])
    revision = await db_session.get(Revision, model.current_revision_id)

    await seed_file(
        model,
        revision,
        "old.gcode.3mf",
        b"old-bytes",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    newest = await seed_file(
        model,
        revision,
        "new.gcode.3mf",
        b"new-bytes",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    await db_session.commit()

    item = await _gallery_item(authenticated_client, created["slug"])

    assert item["printable_file"]["id"] == newest.id


async def test_gallery_printable_file_carries_plate_meta(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    """Review fix: `printable_file` used to come back with `meta: null`,
    so the Send-to-printer dialog's plate picker had nothing to read and
    silently defaulted to plate 1. `printable_file.meta.plates` must be
    populated straight off the gallery list.
    """
    created = await _create_model(authenticated_client, "Sliced Plate Meta Model")
    model = await db_session.get(Model, created["id"])
    revision = await db_session.get(Revision, model.current_revision_id)

    sliced = await seed_file(
        model,
        revision,
        "print.gcode.3mf",
        b"sliced-bytes",
        blob_format=BlobFormat.GCODE_3MF,
        blob_kind=BlobKind.SLICED,
    )
    db_session.add(
        BlobMeta(
            blob_hash=sliced.blob_hash,
            plate_count=2,
            raw={
                "plates": [
                    {"index": 1, "prediction_s": 100, "weight_g": 5.0, "filaments": []},
                    {"index": 2, "prediction_s": 200, "weight_g": 8.0, "filaments": []},
                ]
            },
        )
    )
    await db_session.commit()

    item = await _gallery_item(authenticated_client, created["slug"])

    assert item["printable_file"]["id"] == sliced.id
    assert item["printable_file"]["meta"] is not None
    plates = item["printable_file"]["meta"]["plates"]
    assert [p["index"] for p in plates] == [1, 2]


async def test_gallery_dims_mm_falls_back_to_mesh_when_slicer_file_has_no_meta(
    authenticated_client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_file: Callable[..., Awaitable[File]],
) -> None:
    """Review fix: `dims_mm` used to short-circuit to `None` whenever a
    `best_slicer_file` existed at all, even if THAT file's own `BlobMeta`
    row was missing -- never falling through to a sibling mesh's dims. A
    `.3mf` with no extracted meta alongside an `.stl` that does have one
    must still surface the mesh's dims.
    """
    created = await _create_model(authenticated_client, "3mf No Meta Model")
    model = await db_session.get(Model, created["id"])
    revision = await db_session.get(Revision, model.current_revision_id)

    await seed_file(model, revision, "part.3mf", b"3mf-bytes", blob_format=BlobFormat.THREEMF)
    stl_file = await seed_file(model, revision, "part.stl", b"stl-bytes")
    db_session.add(BlobMeta(blob_hash=stl_file.blob_hash, dims_mm=[7.0, 8.0, 9.0]))
    await db_session.commit()

    item = await _gallery_item(authenticated_client, created["slug"])

    assert item["best_slicer_file"]["format"] == "3mf"
    assert item["dims_mm"] == [7.0, 8.0, 9.0]


async def test_gallery_dims_and_file_picks_are_null_without_files(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Empty Model")

    item = await _gallery_item(authenticated_client, created["slug"])

    assert item["dims_mm"] is None
    assert item["best_slicer_file"] is None
    assert item["printable_file"] is None


# ---------------------------------------------------------------------------
# R13c: custom metadata + print_tips
# ---------------------------------------------------------------------------


async def test_model_metadata_and_print_tips_default_to_none(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Metadata Defaults")

    assert created["metadata"] is None
    assert created["print_tips"] is None


async def test_patch_model_metadata_roundtrip(authenticated_client: httpx.AsyncClient) -> None:
    created = await _create_model(authenticated_client, "Metadata Roundtrip")

    response = await authenticated_client.patch(
        f"/api/models/{created['slug']}",
        json={"metadata": {"Nozzle": "0.4mm", "Layer height": "0.2mm"}},
    )

    assert response.status_code == 200, response.text
    assert response.json()["metadata"] == {"Nozzle": "0.4mm", "Layer height": "0.2mm"}

    detail = await authenticated_client.get(f"/api/models/{created['slug']}")
    assert detail.json()["metadata"] == {"Nozzle": "0.4mm", "Layer height": "0.2mm"}


async def test_patch_model_metadata_null_clears_it(authenticated_client: httpx.AsyncClient) -> None:
    created = await _create_model(authenticated_client, "Metadata Clear")
    await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"metadata": {"a": "b"}}
    )

    response = await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"metadata": None}
    )

    assert response.status_code == 200, response.text
    assert response.json()["metadata"] is None


async def test_patch_model_metadata_too_many_entries_is_422(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Metadata Too Many")
    too_many = {f"key{i}": "value" for i in range(51)}

    response = await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"metadata": too_many}
    )

    assert response.status_code == 422


async def test_patch_model_metadata_key_too_long_is_422(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Metadata Key Too Long")

    response = await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"metadata": {"x" * 65: "value"}}
    )

    assert response.status_code == 422


async def test_patch_model_metadata_value_too_long_is_422(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Metadata Value Too Long")

    response = await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"metadata": {"key": "x" * 2001}}
    )

    assert response.status_code == 422


async def test_patch_model_metadata_empty_key_is_422(
    authenticated_client: httpx.AsyncClient,
) -> None:
    created = await _create_model(authenticated_client, "Metadata Empty Key")

    response = await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"metadata": {"": "value"}}
    )

    assert response.status_code == 422


async def test_patch_model_print_tips_roundtrip(authenticated_client: httpx.AsyncClient) -> None:
    created = await _create_model(authenticated_client, "Print Tips Roundtrip")

    response = await authenticated_client.patch(
        f"/api/models/{created['slug']}", json={"print_tips": "Use a brim, print slow."}
    )

    assert response.status_code == 200, response.text
    assert response.json()["print_tips"] == "Use a brim, print slow."

    detail = await authenticated_client.get(f"/api/models/{created['slug']}")
    assert detail.json()["print_tips"] == "Use a brim, print slow."


async def test_merge_models_moves_files_and_deletes_source(
    authenticated_client: httpx.AsyncClient, backend: LocalStorageBackend
) -> None:
    target = await _create_model(authenticated_client, "Target Model")
    source = await _create_model(authenticated_client, "Source Model")

    # Upload file to source
    await _upload(
        authenticated_client,
        model_id=source["id"],
        revision_id=source["current_revision"]["id"],
        rel_path="plate_1.gcode.3mf",
        content=b"dummy-gcode-content",
    )

    response = await authenticated_client.post(
        f"/api/models/{target['slug']}/merge",
        json={"source_slug": source["slug"]},
    )
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["slug"] == target["slug"]

    # Verify source model is gone
    get_source = await authenticated_client.get(f"/api/models/{source['slug']}")
    assert get_source.status_code == 404

    # Verify target model now has the file
    rev_files = await authenticated_client.get(f"/api/revisions/{target['current_revision']['id']}")
    assert rev_files.status_code == 200
    files = rev_files.json()["files"]
    assert any(f["rel_path"] == "plate_1.gcode.3mf" for f in files)


async def test_merge_copy_failure_keeps_source_bytes(
    authenticated_client: httpx.AsyncClient,
    backend: LocalStorageBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = await _create_model(authenticated_client, "Target Copyfail")
    source = await _create_model(authenticated_client, "Source Copyfail")
    up = await _upload(
        authenticated_client,
        model_id=source["id"],
        revision_id=source["current_revision"]["id"],
        rel_path="part.stl",
        content=b"source-bytes",
    )
    assert up.status_code in (200, 201), up.text

    def _boom(self: LocalStorageBackend, src: str, dst: str) -> None:
        raise OSError("copy failed")

    monkeypatch.setattr(LocalStorageBackend, "copy", _boom)
    resp = await authenticated_client.post(
        f"/api/models/{target['slug']}/merge", json={"source_slug": source["slug"]}
    )
    assert resp.status_code == 500
    # Source must still exist, with its file intact.
    assert (await authenticated_client.get(f"/api/models/{source['slug']}")).status_code == 200
